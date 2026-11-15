"""炸鸡代点 · 商家入驻

- 商家提交入驻申请：店名、地址、区域、菜单价格、联系方式、营业时间、身份信息（姓名、证件号、证件照片）
- 证件号和证件照片在应用层用 AES-256-GCM 加密后存库；密钥只来自环境变量 LVHAN_PII_KEY，
  未配置时拒绝收集身份信息。只有管理员后台能解密查看；其余页面只显示脱敏信息
- 保证金 ¥500 走会员页同一套付款截图流程（项目 chk_deposit）；管理员确认保证金已收且资质审核通过后才上架
- 上架后商家可切换「营业 / 休息」；休息中的店不能下单
- 商家可随时注销入驻：删除申请和身份信息，店铺下架（历史订单保留店名）
数据库只做加法。不记录证件号、证件照片、联系方式到日志。
"""
from __future__ import annotations

import base64, hashlib, json, os, re

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from community import (
    MAX_IMG_BYTES, POST_IMG_FMTS, _open_image, _json, _page, _ts, clean_text, csrf, db, ip_of, limit,
    need_admin, need_user, q,
)

router = APIRouter()

DEPOSIT_YUAN = 500
AREAS = ("明洞", "弘大", "江南", "东大门", "梨泰院", "圣水", "新村", "其他")
ID_TYPES = ("护照", "外国人登录证", "韩国身份证", "中国身份证", "其他")
APP_STATUS_CN = {"pending": "审核中", "approved": "已上架", "rejected": "未通过", "suspended": "已暂停"}
DEPOSIT_CN = {"unpaid": "未交", "paid": "已交", "refunded": "已退"}
ID_PHOTO_MAX_SIDE = 1600

MIGRATE_CHICKEN_MERCHANT = """
ALTER TABLE c_chicken_shops ADD COLUMN IF NOT EXISTS owner_id BIGINT REFERENCES c_users(id) ON DELETE SET NULL;
ALTER TABLE c_chicken_shops ADD COLUMN IF NOT EXISTS is_open BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE c_chicken_shops ADD COLUMN IF NOT EXISTS hours TEXT;
CREATE TABLE IF NOT EXISTS c_chicken_merchants (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL UNIQUE REFERENCES c_users(id) ON DELETE CASCADE,
  shop_name TEXT NOT NULL,
  address TEXT NOT NULL,
  area TEXT NOT NULL,
  menu JSONB NOT NULL DEFAULT '[]'::jsonb,
  contact TEXT NOT NULL,
  hours TEXT NOT NULL,
  real_name TEXT NOT NULL,
  id_type TEXT NOT NULL,
  id_no_enc BYTEA NOT NULL,
  id_last4 TEXT NOT NULL,
  id_photo_enc BYTEA NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  reject_reason TEXT,
  deposit_status TEXT NOT NULL DEFAULT 'unpaid',
  deposit_proof_id BIGINT,
  shop_id BIGINT REFERENCES c_chicken_shops(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS c_chicken_merchants_status_idx ON c_chicken_merchants(status, id DESC);
"""


# ---------------- 加密 ----------------
def _key():
    raw = os.environ.get("LVHAN_PII_KEY", "")
    if len(raw) < 32:
        return None
    return hashlib.sha256(("lvhan-pii-v1:" + raw).encode()).digest()


def pii_ready() -> bool:
    return _key() is not None


def encrypt(data: bytes) -> bytes:
    k = _key()
    if not k:
        raise HTTPException(503, "商家入驻暂未开放（安全配置未完成），请稍后再试")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return b"v1" + nonce + AESGCM(k).encrypt(nonce, data, b"lvhan-pii")


def decrypt(blob) -> bytes:
    k = _key()
    if not k:
        raise HTTPException(503, "未配置解密密钥")
    blob = bytes(blob)
    if blob[:2] != b"v1":
        raise HTTPException(500, "数据格式不正确")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        return AESGCM(k).decrypt(blob[2:14], blob[14:], b"lvhan-pii")
    except Exception:
        raise HTTPException(500, "解密失败，请检查密钥配置")


# ---------------- 工具 ----------------
def mask_name(s):
    s = (s or "").strip()
    return (s[0] + "*" * max(1, len(s) - 1)) if s else ""


def parse_menu(raw):
    """接受 JSON 数组或「名称,价格」每行一个。"""
    items = []
    if isinstance(raw, str):
        t = raw.strip()
        if t.startswith("["):
            try:
                raw = json.loads(t)
            except Exception:
                raise HTTPException(400, "菜单格式不正确")
        else:
            raw = []
            for line in re.split(r"[\n;；]+", t):
                line = line.strip()
                if not line:
                    continue
                m = re.match(r"^(.{1,40}?)[,，\s]+(\d{1,7})\s*(韩元|원|krw)?$", line, re.I)
                if not m:
                    raise HTTPException(400, f"菜单这一行看不懂：{line[:20]}（格式：名称,价格韩元）")
                raw.append({"name": m.group(1).strip(), "price_krw": int(m.group(2))})
    for i, it in enumerate(raw or []):
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()[:40]
        try:
            price = int(it.get("price_krw"))
        except (TypeError, ValueError):
            continue
        if name and 100 <= price <= 1000000:
            items.append({"id": "c%d" % (i + 1), "name": name, "price_krw": price})
    if not items:
        raise HTTPException(400, "请至少填写一个菜品和价格（韩元）")
    if len(items) > 40:
        raise HTTPException(400, "菜品最多 40 个")
    return items


def process_id_photo(raw: bytes) -> bytes:
    if len(raw) > MAX_IMG_BYTES:
        raise HTTPException(413, "证件照片太大，请换一张（最大 6MB）")
    if len(raw) < 50:
        raise HTTPException(400, "证件照片无法识别，请换 JPG/PNG")
    from PIL import ImageOps
    import io
    im = _open_image(raw, POST_IMG_FMTS, "证件照片无法识别，请换 JPG/PNG")
    im = ImageOps.exif_transpose(im).convert("RGB")      # 丢弃 EXIF（含定位）
    im.thumbnail((ID_PHOTO_MAX_SIDE, ID_PHOTO_MAX_SIDE))
    b = io.BytesIO(); im.save(b, "JPEG", quality=80, optimize=True)
    return b.getvalue()


def _app_out(r, *, admin=False):
    o = {"id": r["id"], "shop_name": r["shop_name"], "address": r["address"], "area": r["area"],
         "menu": r["menu"] if isinstance(r["menu"], list) else json.loads(r["menu"] or "[]"),
         "contact": r["contact"], "hours": r["hours"],
         "real_name_masked": mask_name(r["real_name"]), "id_type": r["id_type"],
         "id_masked": "*" * 6 + (r["id_last4"] or ""),
         "status": r["status"], "status_name": APP_STATUS_CN.get(r["status"], r["status"]),
         "reject_reason": r.get("reject_reason") or "",
         "deposit_status": r["deposit_status"], "deposit_name": DEPOSIT_CN.get(r["deposit_status"], r["deposit_status"]),
         "shop_id": r.get("shop_id"), "time": _ts(r["created_at"]), "updated": _ts(r.get("updated_at"))}
    if admin:
        o.update({"user_id": r["user_id"], "member_no": r.get("member_no"), "nickname": r.get("nickname") or "",
                  "real_name": r["real_name"], "deposit_proof_id": r.get("deposit_proof_id"),
                  "is_open": r.get("is_open"), "shop_active": r.get("shop_active")})
    return o


def my_app(conn, uid):
    return q(conn, """SELECT m.*, s.is_open, s.active AS shop_active FROM c_chicken_merchants m
                      LEFT JOIN c_chicken_shops s ON s.id=m.shop_id WHERE m.user_id=%s""", (uid,), one=True)


def mark_deposit_paid(conn, uid, proof_id):
    """付款审核通过保证金截图时调用。"""
    r = q(conn, "UPDATE c_chicken_merchants SET deposit_status='paid', deposit_proof_id=%s, updated_at=now() WHERE user_id=%s RETURNING id",
          (proof_id, uid), one=True)
    if not r:
        raise HTTPException(400, "该用户还没有炸鸡商家入驻申请")
    return r["id"]


# ---------------- 商家端 ----------------
@router.get("/chicken/join")
def p_join():
    return _page("chicken-join.html", "/chicken/join")


@router.get("/api/chicken/merchant/meta")
def api_meta():
    return {"areas": AREAS, "id_types": ID_TYPES, "deposit": DEPOSIT_YUAN, "open": pii_ready()}


@router.get("/api/chicken/merchant/me")
def api_me(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        r = my_app(conn, u["id"])
        pending_dep = q(conn, "SELECT id FROM c_pay_proofs WHERE user_id=%s AND kind='chk_deposit' AND status='pending' LIMIT 1", (u["id"],), one=True)
    if not r:
        return {"app": None, "open": pii_ready(), "deposit": DEPOSIT_YUAN}
    o = _app_out(r)
    o["is_open"] = bool(r.get("is_open")) if r.get("shop_id") else None
    o["deposit_pending"] = bool(pending_dep)
    return {"app": o, "open": pii_ready(), "deposit": DEPOSIT_YUAN}


@router.post("/api/chicken/merchant/apply")
async def api_apply(req: Request, shop_name: str = Form(...), address: str = Form(...), area: str = Form(...),
                    menu: str = Form(...), contact: str = Form(...), hours: str = Form(...),
                    real_name: str = Form(...), id_type: str = Form(...), id_no: str = Form(...),
                    id_photo: UploadFile = File(...), agree: str = Form("")):
    csrf(req)
    if not pii_ready():
        raise HTTPException(503, "商家入驻暂未开放（安全配置未完成），请稍后再试")
    if agree not in ("1", "true", "on"):
        raise HTTPException(400, "请先同意入驻须知与身份信息加密保存")
    shop_name = clean_text(shop_name, 40, "店名", 2)
    from shop_extras import clean_address
    address = clean_address(address, required=True)
    if area not in AREAS:
        raise HTTPException(400, "请选择区域")
    items = parse_menu(menu)
    contact = clean_text(contact, 60, "联系方式", 4)
    hours = clean_text(hours, 60, "营业时间", 2)
    real_name = clean_text(real_name, 30, "姓名", 2)
    if id_type not in ID_TYPES:
        raise HTTPException(400, "请选择证件类型")
    idn = re.sub(r"[\s\-]", "", id_no or "").upper()
    if not re.fullmatch(r"[A-Z0-9]{5,30}", idn):
        raise HTTPException(400, "证件号格式不正确")
    with db() as conn:
        u = need_user(conn, req)
        limit("chkm-try:" + str(u["id"]), 10, 3600, "操作太频繁，请一小时后再试")
        from contact_guard import guard_text
        guard_text(conn, u["id"], "炸鸡商家资料", shop_name, address, hours, " ".join(str(i.get("name", "")) for i in items))
        old = q(conn, "SELECT id,status FROM c_chicken_merchants WHERE user_id=%s FOR UPDATE", (u["id"],), one=True)
        if old and old["status"] in ("approved", "suspended"):
            raise HTTPException(409, "你的店铺已入驻，修改资料请联系管理员")
        photo = process_id_photo(await id_photo.read(MAX_IMG_BYTES + 1))
        limit("chkm:" + str(u["id"]), 3, 3600, "提交太频繁，请一小时后再试")
        limit("chkm-ip:" + ip_of(req), 10, 86400, "今天提交次数太多，请明天再试")
        id_enc, ph_enc = encrypt(idn.encode()), encrypt(photo)
        args = (shop_name, address, area, json.dumps(items, ensure_ascii=False), contact, hours, real_name,
                id_type, id_enc, idn[-4:], ph_enc)
        if old:
            r = q(conn, """UPDATE c_chicken_merchants SET shop_name=%s,address=%s,area=%s,menu=%s::jsonb,contact=%s,hours=%s,
                             real_name=%s,id_type=%s,id_no_enc=%s,id_last4=%s,id_photo_enc=%s,status='pending',reject_reason=NULL,
                             updated_at=now() WHERE id=%s RETURNING *""", args + (old["id"],), one=True)
        else:
            r = q(conn, """INSERT INTO c_chicken_merchants(user_id,shop_name,address,area,menu,contact,hours,real_name,id_type,
                             id_no_enc,id_last4,id_photo_enc) VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                  (u["id"],) + args, one=True)
    return {"ok": True, "app": _app_out(r),
            "message": f"已提交，等待审核。请同时在下方上传 ¥{DEPOSIT_YUAN} 保证金付款截图，审核通过且保证金到账后上架。"}


@router.post("/api/chicken/merchant/open")
async def api_open(req: Request):
    csrf(req)
    d = await _json(req)
    is_open = bool(d.get("open"))
    with db() as conn:
        u = need_user(conn, req)
        r = q(conn, "SELECT * FROM c_chicken_merchants WHERE user_id=%s", (u["id"],), one=True)
        if not r or r["status"] != "approved" or not r.get("shop_id"):
            raise HTTPException(400, "店铺上架后才能切换营业状态")
        limit("chkopen:" + str(u["id"]), 30, 3600, "切换太频繁，请稍后再试")
        q(conn, "UPDATE c_chicken_shops SET is_open=%s WHERE id=%s AND owner_id=%s", (is_open, r["shop_id"], u["id"]))
    return {"ok": True, "is_open": is_open}


@router.post("/api/chicken/merchant/delete")
async def api_delete(req: Request):
    csrf(req)
    d = await _json(req)
    if (d.get("confirm") or "").strip() != "注销":
        raise HTTPException(400, "请输入“注销”两个字确认")
    with db() as conn:
        u = need_user(conn, req)
        r = q(conn, "SELECT * FROM c_chicken_merchants WHERE user_id=%s FOR UPDATE", (u["id"],), one=True)
        if not r:
            raise HTTPException(404, "没有入驻记录")
        if r.get("shop_id"):
            q(conn, "UPDATE c_chicken_shops SET active=FALSE, is_open=FALSE, owner_id=NULL WHERE id=%s", (r["shop_id"],))
        q(conn, "DELETE FROM c_chicken_merchants WHERE id=%s", (r["id"],))
        print("chicken merchant deleted", r["id"], flush=True)
    return {"ok": True, "message": "已注销入驻，身份信息已删除，店铺已下架。保证金退还请联系管理员。"}


# ---------------- 管理员 ----------------
@router.get("/api/admin/chicken/merchants")
def api_admin_list(req: Request, status: str = "pending"):
    if status not in ("pending", "approved", "rejected", "suspended", "all"):
        raise HTTPException(400, "状态不正确")
    with db() as conn:
        need_admin(conn, req)
        sql = """SELECT m.*, u.member_no, u.nickname, s.is_open, s.active AS shop_active FROM c_chicken_merchants m
                 JOIN c_users u ON u.id=m.user_id LEFT JOIN c_chicken_shops s ON s.id=m.shop_id"""
        args = ()
        if status != "all":
            sql += " WHERE m.status=%s"; args = (status,)
        rows = q(conn, sql + " ORDER BY m.id DESC LIMIT 200", args)
        pend = q(conn, "SELECT count(*) AS n FROM c_chicken_merchants WHERE status='pending'", one=True)["n"]
    return {"items": [_app_out(r, admin=True) for r in rows], "pending": pend, "pii_ready": pii_ready()}


@router.get("/api/admin/chicken/merchants/{mid}/id")
def api_admin_id(req: Request, mid: int):
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, "SELECT id_no_enc FROM c_chicken_merchants WHERE id=%s", (mid,), one=True)
    if not r:
        raise HTTPException(404, "找不到申请")
    return Response(json.dumps({"id_no": decrypt(r["id_no_enc"]).decode()}), media_type="application/json",
                    headers={"Cache-Control": "no-store"})


@router.get("/api/admin/chicken/merchants/{mid}/id-photo")
def api_admin_photo(req: Request, mid: int):
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, "SELECT id_photo_enc FROM c_chicken_merchants WHERE id=%s", (mid,), one=True)
    if not r:
        raise HTTPException(404, "找不到申请")
    return Response(decrypt(r["id_photo_enc"]), media_type="image/jpeg",
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/api/admin/chicken/merchants/{mid}")
async def api_admin_action(req: Request, mid: int):
    csrf(req)
    d = await _json(req)
    act = d.get("action")
    if act not in ("approve", "reject", "suspend", "resume", "deposit_paid", "deposit_unpaid", "deposit_refunded"):
        raise HTTPException(400, "未知操作")
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, "SELECT * FROM c_chicken_merchants WHERE id=%s FOR UPDATE", (mid,), one=True)
        if not r:
            raise HTTPException(404, "找不到申请")
        if act.startswith("deposit_"):
            ds = act.split("_", 1)[1]
            q(conn, "UPDATE c_chicken_merchants SET deposit_status=%s, updated_at=now() WHERE id=%s", (ds, mid))
            if ds != "paid" and r.get("shop_id"):          # 保证金未交/已退 → 下架
                q(conn, "UPDATE c_chicken_shops SET active=FALSE WHERE id=%s", (r["shop_id"],))
                if r["status"] == "approved":
                    q(conn, "UPDATE c_chicken_merchants SET status='suspended' WHERE id=%s", (mid,))
            return {"ok": True, "deposit_status": ds}
        if act == "reject":
            why = clean_text(d.get("reason") or "资料不完整，请修改后重新提交", 100, "理由", 2)
            q(conn, "UPDATE c_chicken_merchants SET status='rejected', reject_reason=%s, reviewed_at=now(), updated_at=now() WHERE id=%s", (why, mid))
            if r.get("shop_id"):
                q(conn, "UPDATE c_chicken_shops SET active=FALSE WHERE id=%s", (r["shop_id"],))
            return {"ok": True, "status": "rejected"}
        if act == "suspend":
            q(conn, "UPDATE c_chicken_merchants SET status='suspended', updated_at=now() WHERE id=%s", (mid,))
            if r.get("shop_id"):
                q(conn, "UPDATE c_chicken_shops SET active=FALSE WHERE id=%s", (r["shop_id"],))
            return {"ok": True, "status": "suspended"}
        # approve / resume：必须保证金已交
        if r["deposit_status"] != "paid":
            raise HTTPException(400, "保证金未确认已收，不能上架")
        menu = r["menu"] if isinstance(r["menu"], list) else json.loads(r["menu"] or "[]")
        if r.get("shop_id"):
            q(conn, """UPDATE c_chicken_shops SET name=%s,area=%s,menu=%s::jsonb,address=%s,hours=%s,owner_id=%s,
                       is_example=FALSE,active=TRUE WHERE id=%s""",
              (r["shop_name"], r["area"], json.dumps(menu, ensure_ascii=False), r["address"], r["hours"], r["user_id"], r["shop_id"]))
            sid = r["shop_id"]
        else:
            sid = q(conn, """INSERT INTO c_chicken_shops(name,area,is_example,menu,notes,active,sort_order,address,owner_id,is_open,hours)
                             VALUES (%s,%s,FALSE,%s::jsonb,%s,TRUE,10,%s,%s,TRUE,%s) RETURNING id""",
                    (r["shop_name"], r["area"], json.dumps(menu, ensure_ascii=False), "入驻商家 · 营业时间 " + r["hours"],
                     r["address"], r["user_id"], r["hours"]), one=True)["id"]
        q(conn, "UPDATE c_chicken_merchants SET status='approved', shop_id=%s, reject_reason=NULL, reviewed_at=now(), updated_at=now() WHERE id=%s", (sid, mid))
    return {"ok": True, "status": "approved", "shop_id": sid}


def hide_example_shops_once(conn):
    """商家入驻上线后，三家示例店只隐藏一次（保留数据，管理员可在后台重新上架）。"""
    done = q(conn, "SELECT 1 AS x FROM c_settings WHERE key='chicken_examples_hidden'", one=True)
    if done:
        return
    q(conn, "UPDATE c_chicken_shops SET active=FALSE WHERE is_example")
    q(conn, "INSERT INTO c_settings(key,value) VALUES ('chicken_examples_hidden','1') ON CONFLICT (key) DO NOTHING")
