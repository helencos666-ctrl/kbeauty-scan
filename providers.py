"""服务商家入驻（中/韩双语申请）

只接受三类：
  booking   非医疗门店代订（餐厅、美发、写真馆等）
  transfer  接送机（仅单程交通，须有营运资质）
  assist    中文应急协助 / 短时口译 / 小额跑腿
明确不接受：医美/皮肤科/医院中介、代办签证、打包一日游、加价代抢票。

- 不需要登录即可申请；按 IP 限流 + 隐藏字段（honeypot）+ 最短填写时间防刷
- 联系人、联系方式（电话/邮箱/KakaoTalk）、营业执照号/资质用 AES-256-GCM 加密后存库（复用 chicken_merchant 的
  LVHAN_PII_KEY 机制）；公开页面只展示店名、类别、区域、服务内容与价格、营业时间、照片
- 不提供微信字段；公开文字里不允许出现站外联系方式（contact_guard）
- 管理员在 /admin「入驻申请」里审核：待审核 / 通过 / 拒绝（可下架）
数据库只做加法；不把联系方式、执照号写进日志。
"""
from __future__ import annotations

import hashlib, io, json, os, re, time

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse

from community import (MAX_IMG_BYTES, POST_IMG_FMTS, _open_image, _json, _page, _ts, csrf, db, ip_of, limit,
                       need_admin, q)
from chicken_merchant import encrypt, decrypt, pii_ready

router = APIRouter()

CATS = {
    "booking": ("非医疗门店代订", "비의료 매장 예약 대행", "餐厅、美发、写真馆等门店的预约代订"),
    "transfer": ("接送机", "공항 픽업·샌딩", "机场与酒店之间的单程接送，须有合法营运资质"),
    "assist": ("中文应急协助·短时口译·小额跑腿", "중국어 긴급 지원·단시간 통역·심부름", "临时沟通、短时陪同翻译、小额代买代取"),
}
STATUS_CN = {"pending": "待审核", "approved": "已通过", "rejected": "已拒绝"}
AREAS = ("明洞", "弘大", "江南", "东大门", "梨泰院", "圣水", "新村", "钟路", "汝矣岛", "蚕室", "仁川机场", "金浦机场", "首尔其他", "首尔以外")
EXCLUDED = ["医美", "整形", "皮肤科", "医院", "诊所", "陪诊", "代办签证", "签证代办", "办签证", "一日游", "包车游", "抢票", "代抢",
            "성형", "피부과", "병원", "의원", "비자대행", "비자 대행", "일일투어", "티켓팅 대행"]
PHOTO_MAX_SIDE = 1400
MIN_FILL_SECONDS = 4

MIGRATE_PROVIDERS = """
CREATE TABLE IF NOT EXISTS c_providers (
  id BIGSERIAL PRIMARY KEY,
  category TEXT NOT NULL,
  name TEXT NOT NULL,
  name_ko TEXT,
  area TEXT NOT NULL,
  address TEXT,
  services TEXT NOT NULL,
  price TEXT NOT NULL,
  hours TEXT NOT NULL,
  contact_enc BYTEA NOT NULL,
  license_enc BYTEA,
  photo BYTEA,
  status TEXT NOT NULL DEFAULT 'pending',
  reject_reason TEXT,
  ip_hash TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS c_providers_status_idx ON c_providers(status, category, id DESC);
"""


def _clean(s, maxlen, field, required=True, minlen=1):
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(s or "")).strip()
    s = re.sub(r"\n{3,}", "\n\n", s)
    if not s:
        if required:
            raise HTTPException(400, f"请填写{field}")
        return ""
    if len(s) < minlen:
        raise HTTPException(400, f"{field}至少 {minlen} 个字")
    if len(s) > maxlen:
        raise HTTPException(400, f"{field}最多 {maxlen} 个字")
    return s


def _check_excluded(*texts):
    t = re.sub(r"\s+", "", " ".join(texts)).lower()
    for w in EXCLUDED:
        if re.sub(r"\s+", "", w).lower() in t:
            raise HTTPException(400, f"本平台暂不接受这类服务（检测到「{w}」）：医美/皮肤科/医院中介、代办签证、打包一日游、加价代抢票。")


def _check_public_text(*texts):
    from contact_guard import find_contacts
    for t in texts:
        hits = find_contacts(t)
        if hits:
            raise HTTPException(400, "公开展示的内容里不能留联系方式（" + "、".join(hits) + "）。联系方式请填在下面的「联系方式」栏，只给管理员审核用。")


def _photo(raw: bytes) -> bytes:
    if len(raw) > MAX_IMG_BYTES:
        raise HTTPException(413, "照片太大（最大 6MB）")
    from PIL import ImageOps
    im = _open_image(raw, POST_IMG_FMTS, "照片无法识别，请换 JPG/PNG")
    im = ImageOps.exif_transpose(im).convert("RGB")          # 去掉 EXIF（含定位）
    im.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE))
    b = io.BytesIO(); im.save(b, "JPEG", quality=82, optimize=True)
    data = b.getvalue()
    try:
        from contact_guard import image_has_qr
        if image_has_qr(data):
            raise HTTPException(400, "照片里不能有二维码")
    except HTTPException:
        raise
    except Exception:
        pass
    return data


def _ip_hash(req):
    salt = os.environ.get("LVHAN_PII_KEY", "")[:16] or "lvhan"
    return hashlib.sha256((salt + "|" + ip_of(req)).encode()).hexdigest()[:24]


def _pub(r):
    return {"id": r["id"], "category": r["category"], "category_name": CATS[r["category"]][0], "category_ko": CATS[r["category"]][1],
            "name": r["name"], "name_ko": r.get("name_ko") or "", "area": r["area"], "address": r.get("address") or "",
            "services": r["services"], "price": r["price"], "hours": r["hours"],
            "photo": f"/api/providers/{r['id']}/photo" if r.get("has_photo") else ""}


# ---------------- 页面 ----------------
@router.get("/partner")
def p_partner():
    return _page("partner.html", "/partner", extra={"title": "商家/服务商入驻 · 旅韩助手",
                                                     "description": "餐厅美发写真馆代订、接送机、中文应急协助与短时口译服务商入驻申请（中韩双语），审核通过后展示。"})


@router.get("/services")
def p_services(cat: str = ""):
    # Old bookmark; the list now lives on the unified 找商家 page.
    q = "tab=services"
    if cat:
        q += "&cat=" + cat
    return RedirectResponse("/shops?" + q, status_code=301)


# ---------------- 公开接口 ----------------
@router.get("/api/providers/meta")
def api_meta():
    return {"cats": [{"key": k, "name": v[0], "name_ko": v[1], "desc": v[2]} for k, v in CATS.items()],
            "areas": AREAS, "open": pii_ready(), "t": int(time.time())}


@router.get("/api/providers")
def api_list(cat: str = ""):
    if cat and cat not in CATS:
        raise HTTPException(400, "类别不存在")
    with db() as conn:
        rows = q(conn, """SELECT id, category, name, name_ko, area, address, services, price, hours, (photo IS NOT NULL) AS has_photo
                          FROM c_providers WHERE status='approved' AND (%s='' OR category=%s)
                          ORDER BY reviewed_at DESC NULLS LAST, id DESC LIMIT 200""", (cat, cat))
    return {"items": [_pub(r) for r in rows]}


@router.get("/api/providers/{pid}/photo")
def api_photo(pid: int):
    with db() as conn:
        r = q(conn, "SELECT photo FROM c_providers WHERE id=%s AND status='approved' AND photo IS NOT NULL", (pid,), one=True)
    if not r:
        raise HTTPException(404, "没有照片")
    return Response(bytes(r["photo"]), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@router.post("/api/providers/apply")
async def api_apply(req: Request, category: str = Form(...), name: str = Form(...), name_ko: str = Form(""),
                    area: str = Form(...), address: str = Form(""), contact_name: str = Form(...),
                    phone: str = Form(""), email: str = Form(""), kakao: str = Form(""), license: str = Form(""),
                    services: str = Form(...), price: str = Form(...), hours: str = Form(...), agree: str = Form(""),
                    website: str = Form(""), t: str = Form("0"), photo: UploadFile | None = File(None)):
    csrf(req)
    # 隐藏字段被填 / 提交过快：按成功返回但不入库，不提示机器人
    try:
        started = int(t)
    except ValueError:
        started = 0
    if website.strip() or not started or time.time() - started < MIN_FILL_SECONDS:
        return {"ok": True, "message": "已收到申请，我们会尽快审核。"}
    ip = ip_of(req)
    limit("prov_try:" + ip, 20, 3600, "提交太频繁，请一小时后再试")   # 含填写有误的尝试
    if not pii_ready():
        raise HTTPException(503, "入驻申请暂未开放（安全配置未完成），请稍后再试")
    if agree not in ("1", "true", "on"):
        raise HTTPException(400, "请先阅读并同意入驻须知")
    if category not in CATS:
        raise HTTPException(400, "请选择服务类别")
    if area not in AREAS:
        raise HTTPException(400, "请选择区域")
    name = _clean(name, 40, "店名/服务名称", minlen=2)
    name_ko = _clean(name_ko, 60, "韩文店名", required=False)
    address = _clean(address, 120, "地址", required=(category == "booking"))
    contact_name = _clean(contact_name, 30, "联系人")
    phone = _clean(phone, 30, "电话", required=False)
    email = _clean(email, 120, "邮箱", required=False)
    kakao = _clean(kakao, 40, "KakaoTalk ID", required=False)
    license = _clean(license, 80, "营业执照号/资质", required=(category == "transfer"))
    services = _clean(services, 600, "服务内容", minlen=6)
    price = _clean(price, 300, "价格")
    hours = _clean(hours, 80, "营业时间")
    if not (phone or email or kakao):
        raise HTTPException(400, "电话、邮箱、KakaoTalk ID 至少填一项")
    if phone and not re.fullmatch(r"[+\d][\d\s\-()]{6,24}", phone):
        raise HTTPException(400, "电话格式不对")
    if email and not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,100}\.[A-Za-z]{2,24}", email):
        raise HTTPException(400, "邮箱格式不对")
    if re.search(r"微信|wechat|weixin|vx|wx", " ".join([phone, email, kakao]), re.I):
        raise HTTPException(400, "本平台不收集微信，请填电话、邮箱或 KakaoTalk ID")
    _check_excluded(name, name_ko, services, price)
    _check_public_text(name, name_ko, address, services, price, hours)
    pdata = None
    if photo is not None and photo.filename:
        raw = await photo.read(MAX_IMG_BYTES + 1)
        if raw:
            pdata = _photo(raw)
    # 成功入库的申请：每 IP 每小时 3 次、每天 6 次，全站每天 200 次
    limit("prov_ip_h:" + ip, 3, 3600, "提交太频繁，请一小时后再试")
    limit("prov_ip_d:" + ip, 6, 86400, "今天提交次数太多，请明天再试")
    limit("prov_all_d", 200, 86400, "今天的申请太多了，请明天再试")
    contact_blob = encrypt(json.dumps({"name": contact_name, "phone": phone, "email": email, "kakao": kakao}, ensure_ascii=False).encode())
    lic_blob = encrypt(license.encode()) if license else None
    with db() as conn:
        dup = q(conn, "SELECT id FROM c_providers WHERE ip_hash=%s AND name=%s AND status='pending'", (_ip_hash(req), name), one=True)
        if dup:
            raise HTTPException(400, "这个名称的申请已在审核中，请耐心等待")
        r = q(conn, """INSERT INTO c_providers(category,name,name_ko,area,address,services,price,hours,contact_enc,license_enc,photo,ip_hash)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
              (category, name, name_ko, area, address, services, price, hours, contact_blob, lic_blob, pdata, _ip_hash(req)), one=True)
    print(f"provider apply id={r['id']} cat={category}", flush=True)
    return {"ok": True, "id": r["id"], "message": "已收到申请（编号 %d），审核通常需要 1–3 天。审核结果会通过你留下的联系方式告知。" % r["id"]}


# ---------------- 管理员 ----------------
def _admin_out(r, full=False):
    o = _pub({**r, "has_photo": r.get("has_photo")})
    o.update({"status": r["status"], "status_name": STATUS_CN.get(r["status"], r["status"]), "reject_reason": r.get("reject_reason") or "",
              "time": _ts(r["created_at"]), "reviewed": _ts(r.get("reviewed_at"))})
    if full:
        try:
            o["contact"] = json.loads(decrypt(r["contact_enc"]).decode())
        except HTTPException:
            o["contact"] = {"error": "无法解密"}
        o["license"] = decrypt(r["license_enc"]).decode() if r.get("license_enc") else ""
        if r.get("has_photo"):
            o["photo"] = f"/api/admin/providers/{r['id']}/photo"
    return o


@router.get("/api/admin/providers")
def adm_list(req: Request, status: str = "pending"):
    if status not in STATUS_CN and status != "all":
        status = "pending"
    with db() as conn:
        need_admin(conn, req)
        rows = q(conn, """SELECT id, category, name, name_ko, area, address, services, price, hours, status, reject_reason, created_at, reviewed_at,
                                 contact_enc, license_enc, (photo IS NOT NULL) AS has_photo
                          FROM c_providers WHERE (%s='all' OR status=%s) ORDER BY id DESC LIMIT 200""", (status, status))
        counts = {r["status"]: r["n"] for r in q(conn, "SELECT status, COUNT(*) AS n FROM c_providers GROUP BY status")}
    return {"items": [_admin_out(r, full=True) for r in rows], "counts": counts}


@router.get("/api/admin/providers/{pid}/photo")
def adm_photo(req: Request, pid: int):
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, "SELECT photo FROM c_providers WHERE id=%s AND photo IS NOT NULL", (pid,), one=True)
    if not r:
        raise HTTPException(404, "没有照片")
    return Response(bytes(r["photo"]), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/api/admin/providers/{pid}")
async def adm_act(req: Request, pid: int):
    csrf(req)
    body = await _json(req)
    act = body.get("action")
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, "SELECT id FROM c_providers WHERE id=%s", (pid,), one=True)
        if not r:
            raise HTTPException(404, "申请不存在")
        if act == "approve":
            q(conn, "UPDATE c_providers SET status='approved', reject_reason=NULL, reviewed_at=now() WHERE id=%s", (pid,))
        elif act == "reject":
            reason = _clean(body.get("reason"), 200, "原因", required=False)
            q(conn, "UPDATE c_providers SET status='rejected', reject_reason=%s, reviewed_at=now() WHERE id=%s", (reason or None, pid))
        elif act == "delete":
            q(conn, "DELETE FROM c_providers WHERE id=%s", (pid,))
        else:
            raise HTTPException(400, "未知操作")
    return {"ok": True}
