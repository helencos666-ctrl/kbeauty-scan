"""服务商家入驻

分类与「商家」页一致：地陪向导、医美、美食代点外卖、接送机、住宿、翻译、
追星活动、租车、通讯电话卡、物流快递、代购、其它。
旧申请里的 booking / transfer / assist 仍可读取，分别对应美食代点外卖、接送机、翻译。

医美仅限合法医疗机构自行入驻，不接受中介或陪诊；不接受代办签证、打包一日游、加价代抢票。

- 不需要登录即可申请；登录后会把申请记到账号上，方便审核后直接发布服务
- 按 IP 限流 + 隐藏字段（honeypot）+ 最短填写时间防刷
- 联系人、联系方式（电话/邮箱/KakaoTalk）、营业执照号/资质用 AES-256-GCM 加密后存库（复用 chicken_merchant 的
  LVHAN_PII_KEY 机制）；公开页面只展示店名、类别、区域、服务内容与价格、营业时间、照片（最多 6 张）
- 不提供微信字段；公开文字里不允许出现站外联系方式（contact_guard）
- 管理员在 /admin「入驻申请」里审核：待审核 / 通过 / 拒绝（可下架）
数据库只做加法；不把联系方式、执照号写进日志。
"""
from __future__ import annotations

import hashlib, io, json, os, re, time

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse

from community import (MAX_IMG_BYTES, POST_IMG_FMTS, _open_image, _json, _page, _ts, csrf, current_user, db, ip_of, limit,
                       need_admin, q)
from chicken_merchant import encrypt, decrypt, pii_ready
from feature_flags import UNAVAILABLE_MSG, is_hidden_cat, public_flags, unavailable_payload

router = APIRouter()

# 页面分类（顺序与 /shops 一致）
CANON_CATS = ("guide", "clinic", "food", "airport", "hotel", "interp", "kpop", "car", "sim", "logistics", "daigou", "other")
CATS = {
    "guide": ("地陪向导", "현지 가이드", "市区陪同、购物陪同、行程讲解等（不含打包付费一日游产品）"),
    "clinic": ("医美", "의료·뷰티", "仅限合法医疗机构自行入驻，不接受中介或陪诊代订"),
    "food": ("美食代点外卖", "맛집 대신 주문", "餐厅代点、外卖协助等"),
    "airport": ("接送机", "공항 픽업·샌딩", "机场与酒店之间的单程接送，须有合法营运资质"),
    "hotel": ("住宿", "숙소", "酒店、民宿、韩屋等住宿信息"),
    "interp": ("翻译", "통역", "陪同翻译、短时口译、沟通协助"),
    "kpop": ("追星活动", "팬 활동", "演唱会、见面会、应援、快闪店相关协助（不加价代抢票）"),
    "car": ("租车", "렌터카", "租车服务，须有合法营运资质"),
    "sim": ("通讯电话卡", "유심·통신", "电话卡、上网卡等通讯服务"),
    "logistics": ("物流快递", "물류·택배", "国际快递、集运、行李寄送等"),
    "daigou": ("代购", "구매 대행", "美妆、服饰、食品等代购（价格和商品须如实描述）"),
    "other": ("其它", "기타", "其他适合在本站展示的服务，审核时会再次确认"),
    # 旧申请类别，保留读取，不再作为新的申请选项
    "booking": ("美食代点外卖", "맛집 대신 주문", "餐厅、美发、写真馆等门店的预约代订（旧类别，对应美食代点外卖）"),
    "transfer": ("接送机", "공항 픽업·샌딩", "机场与酒店之间的单程接送，须有合法营运资质"),
    "assist": ("翻译", "통역", "临时沟通、短时陪同翻译、小额代买代取（旧类别，对应翻译）"),
}
CAT_ALIASES = {"booking": "food", "transfer": "airport", "assist": "interp"}
LICENSE_CATS = {"airport", "transfer", "clinic", "car"}
ADDRESS_CATS = {"hotel", "food", "booking"}
STATUS_CN = {"pending": "待审核", "approved": "已通过", "rejected": "已拒绝"}
AREAS = ("明洞", "弘大", "江南", "东大门", "梨泰院", "圣水", "新村", "钟路", "汝矣岛", "蚕室", "仁川机场", "金浦机场", "首尔其他", "首尔以外")
EXCLUDED = ["医美中介", "医院中介", "医疗中介", "皮肤科中介", "陪诊", "代办签证", "签证代办", "办签证", "一日游", "包车游",
            "抢票", "代抢", "알선", "비자대행", "비자 대행", "일일투어", "티켓팅 대행"]
PHOTO_MAX_SIDE = 1400
PHOTO_MAX = 6
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

MIGRATE_PROVIDER_PHOTOS = """
ALTER TABLE c_providers ADD COLUMN IF NOT EXISTS user_id BIGINT REFERENCES c_users(id);
CREATE TABLE IF NOT EXISTS c_provider_photos (
  id BIGSERIAL PRIMARY KEY,
  provider_id BIGINT NOT NULL REFERENCES c_providers(id) ON DELETE CASCADE,
  pos INT NOT NULL DEFAULT 0,
  data BYTEA NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_provider_photos_pid_idx ON c_provider_photos(provider_id, pos, id);
"""


def canon(cat: str) -> str:
    c = (cat or "").strip()
    return CAT_ALIASES.get(c, c)


def cat_name(cat: str) -> str:
    c = canon(cat)
    return CATS.get(c, CATS.get(cat, ("未知分类", "", "")))[0]


def _cat_keys_for_filter(cat: str):
    """查询某一分类时，同时带上对应的旧类别。"""
    key = canon(cat)
    aliases = [a for a, v in CAT_ALIASES.items() if v == key]
    return [key] + aliases


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
            raise HTTPException(400, f"本平台暂不接受这类服务（检测到「{w}」）：医美/医院中介、陪诊、代办签证、打包一日游、加价代抢票。医美仅限合法医疗机构自行入驻。")


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


def _photo_urls(pid, photo_ids, has_legacy, admin=False):
    urls = []
    for phid in photo_ids or []:
        if admin:
            urls.append(f"/api/admin/providers/{pid}/photos/{phid}")
        else:
            urls.append(f"/api/providers/{pid}/photos/{phid}")
    if not urls and has_legacy:
        urls.append(f"/api/admin/providers/{pid}/photo" if admin else f"/api/providers/{pid}/photo")
    return urls[:PHOTO_MAX]


def _pub(r, photo_ids=None):
    has_legacy = bool(r.get("has_photo"))
    pids = photo_ids if photo_ids is not None else (r.get("photo_ids") or [])
    photos = _photo_urls(r["id"], pids, has_legacy, admin=False)
    return {"id": r["id"], "category": canon(r["category"]), "category_key": r["category"],
            "category_name": cat_name(r["category"]), "category_ko": CATS.get(canon(r["category"]), ("", "", ""))[1],
            "name": r["name"], "name_ko": r.get("name_ko") or "", "area": r["area"], "address": r.get("address") or "",
            "services": r["services"], "price": r["price"], "hours": r.get("hours") or "",
            "photo": photos[0] if photos else "", "photos": photos}


def _load_photo_map(conn, pids):
    if not pids:
        return {}
    rows = q(conn, """SELECT id, provider_id FROM c_provider_photos
                      WHERE provider_id = ANY(%s) ORDER BY pos, id""", (list(pids),))
    out = {}
    for r in rows:
        out.setdefault(r["provider_id"], []).append(r["id"])
    return out


# ---------------- 页面 ----------------
@router.get("/partner")
def p_partner():
    return _page("partner.html", "/partner", extra={"title": "商家/服务商入驻 · 旅韩助手",
                                                     "description": "地陪向导、医美、美食代点、接送机、住宿、翻译等商家入驻申请，审核通过后在对应分类展示，访客通过站内咨询联系。"})


@router.get("/services")
def p_services(cat: str = ""):
    # Old bookmark; the list now lives on the unified 找商家 page.
    qstr = "tab=services"
    if cat:
        qstr += "&cat=" + canon(cat)
    return RedirectResponse("/shops?" + qstr, status_code=301)


@router.get("/shops/p/{pid:int}")
def p_provider(pid: int):
    extra = {"index": False, "title": "商家详情 · 旅韩助手", "description": "旅韩助手商家详情：服务介绍、价格和照片。"}
    try:
        with db() as conn:
            r = q(conn, "SELECT id, name, area, category, services FROM c_providers WHERE id=%s AND status='approved'", (pid,), one=True)
        if r and is_hidden_cat(r.get("category")):
            return _page("unavailable.html", "/shops/p/%d" % pid,
                         extra={"title": UNAVAILABLE_MSG + " · 旅韩助手",
                                "description": UNAVAILABLE_MSG, "index": False})
        if r:
            extra = {"title": "%s · %s · 旅韩助手" % (r["name"], cat_name(r["category"])),
                     "description": "%s（%s）%s" % (r["name"], r["area"], (r.get("services") or "")[:80]),
                     "index": True}
    except HTTPException:
        pass
    return _page("provider.html", "/shops/p/%d" % pid, extra=extra)


# ---------------- 公开接口 ----------------
@router.get("/api/providers/meta")
def api_meta():
    cats = []
    for k in CANON_CATS:
        if is_hidden_cat(k):
            continue
        v = CATS[k]
        cats.append({"key": k, "name": v[0], "name_ko": v[1], "desc": v[2],
                     "license_required": k in LICENSE_CATS, "address_required": k in ADDRESS_CATS})
    return {"cats": cats, "areas": AREAS, "open": pii_ready(), "t": int(time.time()),
            "photo_max": PHOTO_MAX,
            "license_cats": sorted(c for c in LICENSE_CATS if c in CANON_CATS and not is_hidden_cat(c)),
            "address_cats": sorted(c for c in ADDRESS_CATS if c in CANON_CATS and not is_hidden_cat(c)),
            "flags": public_flags()}


@router.get("/api/providers")
def api_list(cat: str = ""):
    keys = None
    if cat:
        if is_hidden_cat(canon(cat) or cat):
            return unavailable_payload(items=[])
        if canon(cat) not in CANON_CATS and cat not in CATS:
            raise HTTPException(400, "类别不存在")
        keys = _cat_keys_for_filter(cat)
    with db() as conn:
        if keys:
            rows = q(conn, """SELECT id, category, name, name_ko, area, address, services, price, hours, (photo IS NOT NULL) AS has_photo
                              FROM c_providers WHERE status='approved' AND category = ANY(%s)
                              ORDER BY reviewed_at DESC NULLS LAST, id DESC LIMIT 200""", (keys,))
        else:
            rows = q(conn, """SELECT id, category, name, name_ko, area, address, services, price, hours, (photo IS NOT NULL) AS has_photo
                              FROM c_providers WHERE status='approved'
                              ORDER BY reviewed_at DESC NULLS LAST, id DESC LIMIT 200""")
        if not cat:
            rows = [r for r in rows if not is_hidden_cat(r.get("category"))]
        pmap = _load_photo_map(conn, [r["id"] for r in rows])
    return {"items": [_pub(r, pmap.get(r["id"]) or []) for r in rows]}


@router.get("/api/providers/{pid:int}")
def api_detail(pid: int):
    with db() as conn:
        r = q(conn, """SELECT id, category, name, name_ko, area, address, services, price, hours, (photo IS NOT NULL) AS has_photo
                       FROM c_providers WHERE id=%s AND status='approved'""", (pid,), one=True)
        if not r:
            raise HTTPException(404, "商家不存在或未通过审核")
        if is_hidden_cat(r.get("category")):
            return unavailable_payload(item=None)
        pmap = _load_photo_map(conn, [pid])
    return {"item": _pub(r, pmap.get(pid) or [])}


@router.get("/api/providers/mine")
def api_mine(req: Request):
    with db() as conn:
        u = current_user(conn, req)
        if not u:
            raise HTTPException(401, "请先登录")
        rows = q(conn, """SELECT id, category, name, name_ko, area, address, services, price, hours, status, reject_reason, created_at, reviewed_at,
                                 (photo IS NOT NULL) AS has_photo
                          FROM c_providers WHERE user_id=%s ORDER BY id DESC LIMIT 50""", (u["id"],))
        pmap = _load_photo_map(conn, [r["id"] for r in rows])
    items = []
    for r in rows:
        o = _pub(r, pmap.get(r["id"]) or [])
        o.update({"status": r["status"], "status_name": STATUS_CN.get(r["status"], r["status"]),
                  "reject_reason": r.get("reject_reason") or "", "time": _ts(r["created_at"]),
                  "reviewed": _ts(r.get("reviewed_at")),
                  "href": ("/shops/p/%d" % r["id"]) if r["status"] == "approved" else ""})
        items.append(o)
    return {"items": items}


@router.get("/api/providers/{pid:int}/photo")
def api_photo(pid: int):
    with db() as conn:
        meta = q(conn, "SELECT category FROM c_providers WHERE id=%s AND status='approved'", (pid,), one=True)
        if meta and is_hidden_cat(meta.get("category")):
            raise HTTPException(404, UNAVAILABLE_MSG)
        r = q(conn, "SELECT photo FROM c_providers WHERE id=%s AND status='approved' AND photo IS NOT NULL", (pid,), one=True)
        if not r:
            ph = q(conn, """SELECT p.data FROM c_provider_photos p
                            JOIN c_providers v ON v.id=p.provider_id
                            WHERE p.provider_id=%s AND v.status='approved' ORDER BY p.pos, p.id LIMIT 1""", (pid,), one=True)
            if not ph:
                raise HTTPException(404, "没有照片")
            data = ph["data"]
        else:
            data = r["photo"]
    return Response(bytes(data), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/providers/{pid:int}/photos/{phid:int}")
def api_photo_one(pid: int, phid: int):
    with db() as conn:
        r = q(conn, """SELECT p.data, v.category FROM c_provider_photos p
                       JOIN c_providers v ON v.id=p.provider_id
                       WHERE p.id=%s AND p.provider_id=%s AND v.status='approved'""", (phid, pid), one=True)
    if not r:
        raise HTTPException(404, "没有照片")
    if is_hidden_cat(r.get("category")):
        raise HTTPException(404, UNAVAILABLE_MSG)
    return Response(bytes(r["data"]), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


async def _read_photos(photo, photos):
    files = []
    if photos:
        files.extend([f for f in photos if f is not None and getattr(f, "filename", None)])
    if photo is not None and getattr(photo, "filename", None):
        files.insert(0, photo)
    # de-dup by identity, cap at 6
    out = []
    seen = set()
    for f in files:
        mark = id(f)
        if mark in seen:
            continue
        seen.add(mark)
        out.append(f)
        if len(out) >= PHOTO_MAX:
            break
    blobs = []
    for f in out:
        raw = await f.read(MAX_IMG_BYTES + 1)
        if raw:
            blobs.append(_photo(raw))
    if len(files) > PHOTO_MAX:
        raise HTTPException(400, "店铺展示照片最多 %d 张" % PHOTO_MAX)
    return blobs


@router.post("/api/providers/apply")
async def api_apply(req: Request, category: str = Form(...), name: str = Form(...), name_ko: str = Form(""),
                    area: str = Form(...), address: str = Form(""), contact_name: str = Form(...),
                    phone: str = Form(""), email: str = Form(""), kakao: str = Form(""), license: str = Form(""),
                    services: str = Form(...), price: str = Form(...), hours: str = Form(""), agree: str = Form(""),
                    website: str = Form(""), t: str = Form("0"), photo: UploadFile | None = File(None),
                    photos: list[UploadFile] | None = File(None)):
    csrf(req)
    # 隐藏字段被填 / 提交过快：按成功返回但不入库，不提示机器人
    try:
        started = int(t)
    except ValueError:
        started = 0
    if website.strip() or not started or time.time() - started < MIN_FILL_SECONDS:
        return {"ok": True, "message": "已收到申请，我们会尽快审核。", "next": "/community/new?listing=1&from=apply"}
    ip = ip_of(req)
    limit("prov_try:" + ip, 20, 3600, "提交太频繁，请一小时后再试")   # 含填写有误的尝试
    if not pii_ready():
        raise HTTPException(503, "入驻申请暂未开放（安全配置未完成），请稍后再试")
    if agree not in ("1", "true", "on"):
        raise HTTPException(400, "请先阅读并同意入驻须知")
    category = (category or "").strip()
    if category in CAT_ALIASES:
        category = canon(category)
    if is_hidden_cat(category):
        raise HTTPException(400, UNAVAILABLE_MSG)
    if category not in CANON_CATS:
        raise HTTPException(400, "请选择服务类别")
    if area not in AREAS:
        raise HTTPException(400, "请选择区域")
    name = _clean(name, 40, "店名/服务名称", minlen=2)
    name_ko = _clean(name_ko, 60, "韩文店名", required=False)
    address = _clean(address, 120, "地址", required=(category in ADDRESS_CATS))
    contact_name = _clean(contact_name, 30, "联系人")
    phone = _clean(phone, 30, "电话", required=False)
    email = _clean(email, 120, "邮箱", required=False)
    kakao = _clean(kakao, 40, "KakaoTalk ID", required=False)
    license = _clean(license, 80, "营业执照号/资质", required=(category in LICENSE_CATS))
    services = _clean(services, 600, "服务内容", minlen=6)
    price = _clean(price, 300, "价格")
    hours = _clean(hours, 80, "营业时间", required=False)
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
    blobs = await _read_photos(photo, photos or [])
    # 成功入库的申请：每 IP 每小时 3 次、每天 6 次，全站每天 200 次
    limit("prov_ip_h:" + ip, 3, 3600, "提交太频繁，请一小时后再试")
    limit("prov_ip_d:" + ip, 6, 86400, "今天提交次数太多，请明天再试")
    limit("prov_all_d", 200, 86400, "今天的申请太多了，请明天再试")
    contact_blob = encrypt(json.dumps({"name": contact_name, "phone": phone, "email": email, "kakao": kakao}, ensure_ascii=False).encode())
    lic_blob = encrypt(license.encode()) if license else None
    cover = blobs[0] if blobs else None
    with db() as conn:
        u = current_user(conn, req)
        uid = u["id"] if u else None
        dup = q(conn, "SELECT id FROM c_providers WHERE ip_hash=%s AND name=%s AND status='pending'", (_ip_hash(req), name), one=True)
        if dup:
            raise HTTPException(400, "这个名称的申请已在审核中，请耐心等待")
        r = q(conn, """INSERT INTO c_providers(category,name,name_ko,area,address,services,price,hours,contact_enc,license_enc,photo,ip_hash,user_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
              (category, name, name_ko, area, address, services, price, hours or "", contact_blob, lic_blob, cover, _ip_hash(req), uid), one=True)
        for i, blob in enumerate(blobs):
            q(conn, "INSERT INTO c_provider_photos(provider_id,pos,data) VALUES (%s,%s,%s)", (r["id"], i, blob))
    print(f"provider apply id={r['id']} cat={category}", flush=True)
    return {"ok": True, "id": r["id"], "next": "/community/new?listing=1&from=apply",
            "message": "已收到申请（编号 %d），审核通常需要 1–3 天。接下来可以发布你的服务信息。" % r["id"]}


# ---------------- 管理员 ----------------
def _admin_out(r, photo_ids=None, full=False):
    o = _pub({**r, "has_photo": r.get("has_photo")}, photo_ids)
    if full:
        o["photos"] = _photo_urls(r["id"], photo_ids, r.get("has_photo"), admin=True)
        o["photo"] = o["photos"][0] if o["photos"] else ""
    o.update({"status": r["status"], "status_name": STATUS_CN.get(r["status"], r["status"]), "reject_reason": r.get("reject_reason") or "",
              "time": _ts(r["created_at"]), "reviewed": _ts(r.get("reviewed_at")), "user_id": r.get("user_id")})
    if full:
        try:
            o["contact"] = json.loads(decrypt(r["contact_enc"]).decode())
        except HTTPException:
            o["contact"] = {"error": "无法解密"}
        o["license"] = decrypt(r["license_enc"]).decode() if r.get("license_enc") else ""
    return o


@router.get("/api/admin/providers")
def adm_list(req: Request, status: str = "pending"):
    if status not in STATUS_CN and status != "all":
        status = "pending"
    with db() as conn:
        need_admin(conn, req)
        rows = q(conn, """SELECT id, category, name, name_ko, area, address, services, price, hours, status, reject_reason, created_at, reviewed_at,
                                 contact_enc, license_enc, user_id, (photo IS NOT NULL) AS has_photo
                          FROM c_providers WHERE (%s='all' OR status=%s) ORDER BY id DESC LIMIT 200""", (status, status))
        counts = {r["status"]: r["n"] for r in q(conn, "SELECT status, COUNT(*) AS n FROM c_providers GROUP BY status")}
        pmap = _load_photo_map(conn, [r["id"] for r in rows])
    return {"items": [_admin_out(r, pmap.get(r["id"]) or [], full=True) for r in rows], "counts": counts}


@router.get("/api/admin/providers/{pid:int}/photo")
def adm_photo(req: Request, pid: int):
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, "SELECT photo FROM c_providers WHERE id=%s AND photo IS NOT NULL", (pid,), one=True)
        if not r:
            ph = q(conn, "SELECT data FROM c_provider_photos WHERE provider_id=%s ORDER BY pos, id LIMIT 1", (pid,), one=True)
            if not ph:
                raise HTTPException(404, "没有照片")
            data = ph["data"]
        else:
            data = r["photo"]
    return Response(bytes(data), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/api/admin/providers/{pid:int}/photos/{phid:int}")
def adm_photo_one(req: Request, pid: int, phid: int):
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, "SELECT data FROM c_provider_photos WHERE id=%s AND provider_id=%s", (phid, pid), one=True)
    if not r:
        raise HTTPException(404, "没有照片")
    return Response(bytes(r["data"]), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/api/admin/providers/{pid:int}")
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
