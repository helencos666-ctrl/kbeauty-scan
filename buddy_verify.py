"""旅游搭子实名核验（不采集完整证件号）。

法律约束：韩国 PIPA 禁止一般网站收集住民登录号（주민등록번호）；护照号属于固有识别信息。
因此本站只收集证件号码后四位，不保存完整住民登录号或护照号。证件照片仅供人工审核，
通过或拒绝后立即删除。
"""
import hashlib, hmac, os, re, secrets
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request, Response, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse

from community import (
    csrf, db, ip_of, limit, need_admin, need_user, current_user, q, _ts, clean_text,
    process_image, MAX_IMG_BYTES, BASE,
)

router = APIRouter()

ID_TYPES = {
    "alien_reg": "韩国外国人登录证",
    "kr_id": "韩国身份证",
    "passport": "护照",
    "cn_id": "中国身份证",
}
NATIONALITIES = ["中国", "韩国", "其他"]
REPORT_REASONS = ["色情骚扰", "诈骗或索要钱财", "虚假资料", "广告营销", "其他"]
AUTO_HIDE_N = 3
LAST4_RE = re.compile(r"^[A-Za-z0-9]{4}$")
RRN_RE = re.compile(r"\d{6}\s*-?\s*\d{7}")
LONG_ID_RE = re.compile(r"[A-Za-z0-9]{8,}")
BUDDY_DISCLAIMER = "仅限结伴旅游，不是交友/约会平台，禁止色情、诈骗、金钱往来，注意人身安全。"
CONSENT_TEXT = (
    "我已阅读并同意：本次核验仅用于确认旅游搭子身份、减少假冒和诈骗，不会用于其他营销。"
    "本站不收集完整住民登录号或完整护照号，只保存证件号码后四位。"
    "证件照片仅在审核期间保存，通过或拒绝后立即删除；姓名、国籍、证件类型和后四位在账号存续期间保留，注销时删除。"
)
KST = timezone(timedelta(hours=9))

MIGRATE_BUDDY_VERIFY = """
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS id_verified BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS buddy_banned_until TIMESTAMPTZ;
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS buddy_ban_reason TEXT;
ALTER TABLE c_dating_profiles ADD COLUMN IF NOT EXISTS auto_hidden BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE c_dating_profiles ADD COLUMN IF NOT EXISTS report_count INT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS c_id_verifications (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  real_name TEXT NOT NULL,
  nationality TEXT NOT NULL,
  id_type TEXT NOT NULL,
  last4 TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  reject_reason TEXT,
  consent_at TIMESTAMPTZ NOT NULL,
  image_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at TIMESTAMPTZ,
  reviewed_by BIGINT
);
CREATE INDEX IF NOT EXISTS c_id_verifications_user_idx ON c_id_verifications(user_id, id DESC);
CREATE INDEX IF NOT EXISTS c_id_verifications_status_idx ON c_id_verifications(status, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS c_id_verifications_pending_uidx
  ON c_id_verifications(user_id) WHERE status = 'pending';
"""


def id_dir():
    d = os.environ.get("ID_VERIFY_DIR") or os.path.join(BASE, "data", "id-verify")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _key_bytes():
    env = (os.environ.get("ID_VERIFY_KEY") or "").strip()
    kpath = os.path.join(id_dir(), ".key")
    if env:
        raw = env.encode()
    elif os.path.exists(kpath):
        raw = open(kpath, "rb").read()
    else:
        raw = os.urandom(32)
        fd = os.open(kpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
    return hashlib.sha256(raw).digest()


def encrypt_bytes(data: bytes) -> bytes:
    nonce = os.urandom(16)
    key = _key_bytes()
    out = bytearray()
    for i in range(0, len(data), 32):
        block = hmac.new(key, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
        chunk = data[i:i + 32]
        out.extend(bytes(a ^ b for a, b in zip(chunk, block[:len(chunk)])))
    return nonce + bytes(out)


def decrypt_bytes(blob: bytes) -> bytes:
    if not blob or len(blob) < 16:
        raise ValueError("bad blob")
    nonce, data = blob[:16], blob[16:]
    key = _key_bytes()
    out = bytearray()
    for i in range(0, len(data), 32):
        block = hmac.new(key, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
        chunk = data[i:i + 32]
        out.extend(bytes(a ^ b for a, b in zip(chunk, block[:len(chunk)])))
    return bytes(out)


def delete_image(name):
    if not name:
        return
    path = os.path.join(id_dir(), os.path.basename(name))
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def is_id_verified(u) -> bool:
    return bool(u and u.get("id_verified"))


def buddy_ban_active(u) -> bool:
    until = u.get("buddy_banned_until") if u else None
    if not until:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > datetime.now(timezone.utc)


def require_buddy_ok(u, action="使用旅游搭子"):
    if not u:
        raise HTTPException(401, "请先登录")
    if buddy_ban_active(u):
        raise HTTPException(403, "你已被限制使用旅游搭子功能")
    if not is_id_verified(u):
        raise HTTPException(403, "请先完成旅游搭子实名核验后再" + action)


def clean_last4(raw, extra=""):
    s = str(raw or "").strip().upper().replace(" ", "")
    blob = s + " " + str(extra or "")
    if RRN_RE.search(blob) or LONG_ID_RE.search(s):
        raise HTTPException(400, "请只填写证件号码后四位，不要填写完整证件号")
    if not LAST4_RE.match(s):
        raise HTTPException(400, "请只填写证件号码后四位（4 位字母或数字）")
    return s


def _v_out(r, *, admin=False):
    o = {
        "id": r["id"],
        "status": r["status"],
        "real_name": r["real_name"],
        "nationality": r["nationality"],
        "id_type": r["id_type"],
        "id_type_name": ID_TYPES.get(r["id_type"], r["id_type"]),
        "last4": r["last4"],
        "reject_reason": r.get("reject_reason"),
        "time": _ts(r.get("created_at")),
        "reviewed_at": _ts(r.get("reviewed_at")),
        "has_image": bool(r.get("image_name")),
    }
    if admin:
        o["user_id"] = r["user_id"]
        o["nickname"] = r.get("nickname") or ""
        o["member_no"] = r.get("member_no") or r["user_id"]
    return o


@router.get("/api/id-verify/meta")
def api_meta():
    return {"id_types": ID_TYPES, "nationalities": NATIONALITIES, "consent_text": CONSENT_TEXT,
            "disclaimer": BUDDY_DISCLAIMER, "auto_hide_n": AUTO_HIDE_N, "report_reasons": REPORT_REASONS}


@router.get("/api/id-verify/me")
def api_me(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        latest = q(conn, "SELECT * FROM c_id_verifications WHERE user_id=%s ORDER BY id DESC LIMIT 1",
                   (u["id"],), one=True)
        banned = buddy_ban_active(u)
    return {"verified": bool(u.get("id_verified")), "banned": banned,
            "ban_until": _ts(u.get("buddy_banned_until")) if banned else None,
            "ban_reason": (u.get("buddy_ban_reason") if banned else None),
            "application": _v_out(latest) if latest else None,
            "consent_text": CONSENT_TEXT, "disclaimer": BUDDY_DISCLAIMER,
            "id_types": ID_TYPES, "nationalities": NATIONALITIES}


@router.post("/api/id-verify")
async def api_submit(req: Request, real_name: str = Form(...), nationality: str = Form(...),
                     id_type: str = Form(...), last4: str = Form(...), consent: str = Form(""),
                     image: UploadFile = File(...)):
    csrf(req)
    name = clean_text(real_name, 40, "真实姓名", 2)
    nat = str(nationality or "").strip()
    if nat not in NATIONALITIES:
        raise HTTPException(400, "请选择国籍")
    typ = str(id_type or "").strip()
    if typ not in ID_TYPES:
        raise HTTPException(400, "请选择证件类型")
    # 只接受后四位；完整号码（含住民登录号形态）一律拒绝
    last = clean_last4(last4, name)
    if str(consent or "") not in ("1", "true", "on", "yes"):
        raise HTTPException(400, "请勾选同意后再提交")
    raw = await image.read(MAX_IMG_BYTES + 1)
    # 先登录校验再处理图片
    with db() as conn:
        u = need_user(conn, req)
        if buddy_ban_active(u):
            raise HTTPException(403, "你已被限制使用旅游搭子功能")
        if u.get("id_verified"):
            raise HTTPException(400, "你已经通过实名核验")
        if q(conn, "SELECT id FROM c_id_verifications WHERE user_id=%s AND status='pending'", (u["id"],), one=True):
            raise HTTPException(400, "你已有一份待审核的申请，请等待结果")
        limit("idv:" + str(u["id"]), 5, 86400, "今天提交次数已用完，请明天再试")
        limit("idvip:" + ip_of(req), 15, 86400, "今天提交次数已用完，请明天再试")
    big, _th, _w, _h = process_image(raw)
    fname = secrets.token_hex(16) + ".bin"
    path = os.path.join(id_dir(), fname)
    with open(path, "wb") as f:
        f.write(encrypt_bytes(bytes(big)))
    os.chmod(path, 0o600)
    try:
        with db() as conn:
            u = need_user(conn, req)
            row = q(conn, """INSERT INTO c_id_verifications(user_id,real_name,nationality,id_type,last4,status,consent_at,image_name)
                             VALUES (%s,%s,%s,%s,%s,'pending',now(),%s)
                             RETURNING *""", (u["id"], name, nat, typ, last, fname), one=True)
    except Exception:
        delete_image(fname)
        raise
    return {"ok": True, "item": _v_out(row), "message": "已提交，请等待管理员审核。证件照片仅在审核期间保存。"}


@router.get("/api/admin/id-verify")
def api_admin_list(req: Request, status: str = "pending"):
    status = (status or "pending").strip()
    if status not in ("pending", "approved", "rejected", "all"):
        raise HTTPException(400, "状态不正确")
    with db() as conn:
        need_admin(conn, req)
        sql = """SELECT v.*, u.nickname, u.member_no FROM c_id_verifications v
                 JOIN c_users u ON u.id=v.user_id"""
        args = []
        if status != "all":
            sql += " WHERE v.status=%s"; args.append(status)
        sql += " ORDER BY v.id DESC LIMIT 200"
        rows = q(conn, sql, tuple(args))
    return {"items": [_v_out(r, admin=True) for r in rows], "id_types": ID_TYPES}


@router.get("/api/admin/id-verify/{vid}/image")
def api_admin_image(req: Request, vid: int):
    with db() as conn:
        need_admin(conn, req)
        row = q(conn, "SELECT * FROM c_id_verifications WHERE id=%s", (vid,), one=True)
    if not row or not row.get("image_name"):
        raise HTTPException(404, "照片已删除或不存在")
    path = os.path.join(id_dir(), os.path.basename(row["image_name"]))
    if not os.path.isfile(path):
        raise HTTPException(404, "照片已删除或不存在")
    try:
        data = decrypt_bytes(open(path, "rb").read())
    except Exception:
        raise HTTPException(404, "照片无法读取")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.post("/api/admin/id-verify/{vid}")
async def api_admin_review(req: Request, vid: int):
    csrf(req)
    from community import _json
    d = await _json(req)
    act = d.get("action")
    if act not in ("approve", "reject"):
        raise HTTPException(400, "未知操作")
    reason = None
    if act == "reject":
        reason = clean_text(d.get("reason") or "未通过核验", 100, "理由")
    with db() as conn:
        need_admin(conn, req)
        row = q(conn, "SELECT * FROM c_id_verifications WHERE id=%s FOR UPDATE", (vid,), one=True)
        if not row:
            raise HTTPException(404, "找不到这份申请")
        if row["status"] != "pending":
            raise HTTPException(400, "这份申请已经审核过了")
        image_name = row.get("image_name")
        q(conn, """UPDATE c_id_verifications SET status=%s, reject_reason=%s, reviewed_at=now(), image_name=NULL
                   WHERE id=%s""", ("approved" if act == "approve" else "rejected", reason, vid))
        if act == "approve":
            q(conn, "UPDATE c_users SET id_verified=TRUE WHERE id=%s", (row["user_id"],))
        delete_image(image_name)
    return {"ok": True}


@router.post("/api/friends/buddy-report")
async def api_buddy_report(req: Request):
    csrf(req)
    from community import _json
    d = await _json(req)
    reason = str(d.get("reason") or "").strip()
    if reason not in REPORT_REASONS:
        raise HTTPException(400, "请选择举报原因")
    detail = clean_text(d.get("text") or d.get("detail") or reason, 200, "说明", 2)
    body = reason + "：" + detail
    with db() as conn:
        u = need_user(conn, req)
        try:
            uid = int(d.get("uid") or d.get("target_id") or d.get("id"))
        except (TypeError, ValueError):
            raise HTTPException(400, "举报对象无效")
        if u["id"] == uid:
            raise HTTPException(400, "不能举报自己")
        limit("budr:" + str(u["id"]), 8, 3600, "举报太频繁，请稍后再试")
        limit("budrip:" + ip_of(req), 20, 3600, "举报太频繁，请稍后再试")
        prof = q(conn, "SELECT user_id, purpose, report_count FROM c_dating_profiles WHERE user_id=%s", (uid,), one=True)
        if not prof:
            raise HTTPException(404, "资料不存在")
        q(conn, "INSERT INTO c_reports(target_type,target_id,user_id,reason) VALUES ('profile',%s,%s,%s)",
          (uid, u["id"], body))
        pending = q(conn, """SELECT count(*) AS n FROM c_reports
                             WHERE target_type='profile' AND target_id=%s AND NOT resolved""",
                    (uid,), one=True)["n"]
        hidden = False
        q(conn, "UPDATE c_dating_profiles SET report_count=%s WHERE user_id=%s", (pending, uid))
        if pending >= AUTO_HIDE_N:
            q(conn, "UPDATE c_dating_profiles SET hidden_by_admin=TRUE, auto_hidden=TRUE WHERE user_id=%s", (uid,))
            hidden = True
    return {"ok": True, "hidden": hidden, "pending": pending,
            "message": "已收到举报" + ("，该资料已暂时隐藏并交管理员复核。" if hidden else "，管理员会尽快查看。")}


@router.post("/api/admin/buddy-ban/{uid}")
async def api_admin_buddy_ban(req: Request, uid: int):
    csrf(req)
    from community import _json
    d = await _json(req)
    act = d.get("action")
    if act not in ("ban", "unban"):
        raise HTTPException(400, "未知操作")
    with db() as conn:
        need_admin(conn, req)
        if not q(conn, "SELECT id FROM c_users WHERE id=%s", (uid,), one=True):
            raise HTTPException(404, "找不到用户")
        if act == "unban":
            q(conn, "UPDATE c_users SET buddy_banned_until=NULL, buddy_ban_reason=NULL WHERE id=%s", (uid,))
            return {"ok": True, "banned": False}
        reason = clean_text(d.get("reason") or "违反旅游搭子约定", 100, "理由")
        days = d.get("days")
        if d.get("permanent") or str(days).lower() in ("permanent", "perm", "-1"):
            until = datetime.now(timezone.utc) + timedelta(days=36500)
        else:
            try:
                n = int(days or 7)
            except (TypeError, ValueError):
                n = 7
            n = max(1, min(n, 3650))
            until = datetime.now(timezone.utc) + timedelta(days=n)
        q(conn, "UPDATE c_users SET buddy_banned_until=%s, buddy_ban_reason=%s WHERE id=%s", (until, reason, uid))
        # 被封后隐藏其搭子资料
        q(conn, "UPDATE c_dating_profiles SET hidden_by_admin=TRUE WHERE user_id=%s AND purpose='buddy'", (uid,))
    return {"ok": True, "banned": True, "until": _ts(until)}
