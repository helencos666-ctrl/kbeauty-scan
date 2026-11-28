"""推广员系统扩展：用户申请、实付金额 50% 佣金、冻结期、单层结算。

不另建一套推广体系，只扩展已有 c_promoters / c_commissions / ?ref= / lh_ref Cookie。
只给直接推荐人计佣（推荐人的推荐人不再计），禁止自我推荐。金额以分为单位存储。
"""
from __future__ import annotations

import csv
import io
import re
import secrets
from datetime import datetime, timedelta, timezone

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response

from community import (
    BASE, csrf, current_user, db, ip_of, limit, need_admin, need_user, q, _json, _page, _ts,
    active_codes, clean_text, merchant_activate, norm_code, parse_account, set_tier,
)

router = APIRouter()

KST = timezone(timedelta(hours=9))
RATE_BPS_DEFAULT = 5000  # 50%
HOLD_DAYS_DEFAULT = 7
STATUSES = ("pending", "payable", "paid", "void")
KINDS = {
    "t1": ("会员", 1),
    "t2": ("高级会员", 2),
    "m_month": ("商家会员月付", 3),
    "m_year": ("商家会员年付", 4),
}
PROMO_TERMS = (
    "只给直接推荐的注册用户结算佣金，推荐人的下级再发展的用户不计佣（单层，不是多层分销）。"
    "禁止骚扰、群发、虚假承诺和任何医疗效果保证；佣金以管理员核实到账的实付金额的 50% 计算，"
    "需过冻结期且未被退款后才可申请发放。本站只支持人工打款。"
)
# 给推广员和管理员看的简短说明（三句话）
POLICY_LINES = (
    "朋友通过你的链接或二维码注册，30天内付费，你拿实付金额的50%。",
    "7天后结算，退款不算。",
    "只算你直接推荐的人。",
)
POLICY_TEXT = "".join(POLICY_LINES)
_LOGO = Path(__file__).resolve().parent / "static" / "icons" / "icon-192.png"
_FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
_FONT_BOLD = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
PROMO_METHODS = ("户外直播", "短视频", "社群", "其他")
DOUYIN_RE = re.compile("^[A-Za-z0-9_.\\-\u4e00-\u9fff]{2,40}$")

MIGRATE_PROMO = """
ALTER TABLE c_promoters ADD COLUMN IF NOT EXISTS user_id BIGINT REFERENCES c_users(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX IF NOT EXISTS c_promoters_user_uidx ON c_promoters(user_id) WHERE user_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS c_promo_applications (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  message TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  reject_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at TIMESTAMPTZ,
  UNIQUE (user_id)
);

ALTER TABLE c_commissions ADD COLUMN IF NOT EXISTS paid_amount_cents INT;
ALTER TABLE c_commissions ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE c_commissions ADD COLUMN IF NOT EXISTS payable_at TIMESTAMPTZ;
ALTER TABLE c_commissions ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ;
ALTER TABLE c_commissions ADD COLUMN IF NOT EXISTS paid_note TEXT;
ALTER TABLE c_commissions ADD COLUMN IF NOT EXISTS void_reason TEXT;
ALTER TABLE c_commissions ADD COLUMN IF NOT EXISTS kind TEXT;

UPDATE c_commissions SET status='paid', paid_at=COALESCE(paid_at, settled_at)
  WHERE settled_at IS NOT NULL AND status='pending';
UPDATE c_commissions SET status='payable', payable_at=COALESCE(payable_at, created_at)
  WHERE settled_at IS NULL AND status='pending' AND paid_amount_cents IS NULL;

ALTER TABLE c_promo_applications ADD COLUMN IF NOT EXISTS douyin_id TEXT;
ALTER TABLE c_promo_applications ADD COLUMN IF NOT EXISTS promo_method TEXT;
ALTER TABLE c_promoters ADD COLUMN IF NOT EXISTS douyin_id TEXT;
ALTER TABLE c_promoters ADD COLUMN IF NOT EXISTS promo_method TEXT;
"""


def _setting(conn, key, default):
    r = q(conn, "SELECT value FROM c_settings WHERE key=%s", (key,), one=True)
    return (r["value"] if r else None) or default


def hold_days(conn) -> int:
    try:
        n = int(_setting(conn, "promo_hold_days", str(HOLD_DAYS_DEFAULT)))
    except (TypeError, ValueError):
        n = HOLD_DAYS_DEFAULT
    return max(0, min(n, 90))


def rate_bps(conn) -> int:
    try:
        n = int(_setting(conn, "promo_rate_bps", str(RATE_BPS_DEFAULT)))
    except (TypeError, ValueError):
        n = RATE_BPS_DEFAULT
    return max(1, min(n, 10000))


def set_promo_settings(conn, days=None, bps=None):
    if days is not None:
        n = int(days)
        if n < 0 or n > 90:
            raise HTTPException(400, "冻结天数应在 0–90 之间")
        q(conn, """INSERT INTO c_settings(key,value,updated_at) VALUES ('promo_hold_days',%s,now())
                   ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""", (str(n),))
    if bps is not None:
        n = int(bps)
        if n < 1 or n > 10000:
            raise HTTPException(400, "佣金比例无效")
        q(conn, """INSERT INTO c_settings(key,value,updated_at) VALUES ('promo_rate_bps',%s,now())
                   ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""", (str(n),))


def yuan(cents) -> float:
    return round((cents or 0) / 100.0, 2)


def parse_amount_cents(v):
    try:
        c = round(float(v) * 100)
    except (TypeError, ValueError):
        raise HTTPException(400, "请填写实付金额")
    if c < 1 or c > 10_000_000:
        raise HTTPException(400, "实付金额无效")
    return int(c)


def mask_name(nick: str) -> str:
    s = (nick or "用").strip() or "用"
    return s[:1] + "*"


def clean_douyin(v) -> str:
    s = str(v or "").strip()
    if not s:
        raise HTTPException(400, "请填写抖音号")
    if len(s) < 2:
        raise HTTPException(400, "抖音号至少 2 个字")
    if len(s) > 40:
        raise HTTPException(400, "抖音号最多 40 个字")
    if not DOUYIN_RE.fullmatch(s):
        raise HTTPException(400, "抖音号只能包含字母、数字、下划线、点、短横线和中文")
    return s


def clean_promo_method(v) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    if s not in PROMO_METHODS:
        raise HTTPException(400, "推广方式不正确")
    return s


def refresh_status(conn, row) -> str:
    """把到期的 pending 记为 payable；返回当前有效状态。"""
    st = row.get("status") or "pending"
    if st == "pending" and row.get("payable_at") and row["payable_at"] <= datetime.now(timezone.utc):
        q(conn, "UPDATE c_commissions SET status='payable' WHERE id=%s AND status='pending'", (row["id"],))
        st = "payable"
        row["status"] = st
    return st


def _new_code(conn) -> str:
    for _ in range(20):
        code = "P" + secrets.token_hex(3).upper()  # P + 6 hex = 7 chars
        if not q(conn, "SELECT code FROM c_promoters WHERE code=%s", (code,), one=True):
            return code
    raise HTTPException(500, "无法生成推广码，请稍后重试")


def _promoter_of_user(conn, uid):
    return q(conn, "SELECT * FROM c_promoters WHERE user_id=%s", (uid,), one=True)


def promo_public_url(code: str) -> str:
    from seo import site_origin
    return site_origin() + "/?ref=" + code


def _cjk_font(size, bold=False):
    from PIL import ImageFont
    path = _FONT_BOLD if bold and _FONT_BOLD.is_file() else _FONT
    if path.is_file():
        for idx in (2, 0, 1, 3, 4, 5):
            try:
                return ImageFont.truetype(str(path), size=size, index=idx)
            except Exception:
                continue
        try:
            return ImageFont.truetype(str(path), size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _qr_matrix(url: str):
    import zxingcpp
    bc = zxingcpp.create_barcode(url, zxingcpp.BarcodeFormat.QRCode, ec_level="H")
    raw = zxingcpp.write_barcode_to_image(bc, scale=14, add_quiet_zones=True)
    from PIL import Image
    h, w = raw.shape[0], raw.shape[1]
    return Image.frombytes("L", (w, h), bytes(memoryview(raw)))


def _paste_logo(qr_im):
    from PIL import Image
    qr = qr_im.convert("RGB")
    if not _LOGO.is_file():
        return qr
    try:
        logo = Image.open(_LOGO).convert("RGBA")
    except Exception:
        return qr
    side = max(32, int(min(qr.size) * 0.18))
    pad = max(6, side // 10)
    box = side + pad * 2
    bg = Image.new("RGBA", (box, box), (255, 255, 255, 255))
    logo = logo.resize((side, side), Image.Resampling.LANCZOS)
    # 圆角白底，避免硬切标志
    mask = Image.new("L", (box, box), 0)
    from PIL import ImageDraw
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, box - 1, box - 1), radius=max(8, box // 8), fill=255)
    bg.paste(logo, (pad, pad), logo)
    out = Image.new("RGBA", qr.size, (255, 255, 255, 255))
    out.paste(qr, (0, 0))
    x = (qr.size[0] - box) // 2
    y = (qr.size[1] - box) // 2
    out.paste(bg, (x, y), mask)
    return out.convert("RGB")


def render_promo_qr_png(code: str, name: str = "") -> bytes:
    """带旅韩助手标志、推广员名称和推广码的二维码 PNG。"""
    from PIL import Image, ImageDraw
    import io as _io
    url = promo_public_url(code)
    qr = _paste_logo(_qr_matrix(url))
    title_font = _cjk_font(42, bold=True)
    name_font = _cjk_font(30, bold=True)
    code_font = _cjk_font(26)
    name = (name or "推广员").strip()[:20]
    label = "推广码 " + code
    side = 720
    qr_size = 480
    qr = qr.resize((qr_size, qr_size), Image.Resampling.NEAREST)
    # 量文字高度
    tmp = Image.new("RGB", (10, 10), "white")
    d0 = ImageDraw.Draw(tmp)
    def _h(text, font):
        b = d0.textbbox((0, 0), text, font=font)
        return b[3] - b[1], b[2] - b[0]
    th, tw = _h("旅韩助手", title_font)
    nh, nw = _h(name, name_font)
    ch, cw = _h(label, code_font)
    top, gap, bottom = 36, 16, 36
    height = top + th + gap + qr_size + gap + nh + 8 + ch + bottom
    canvas = Image.new("RGB", (side, height), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    # 顶栏淡青条
    draw.rectangle((0, 0, side, top + th + gap - 4), fill=(230, 243, 241))
    tx = (side - tw) // 2
    draw.text((tx, top - 4), "旅韩助手", font=title_font, fill=(20, 110, 104))
    qx = (side - qr_size) // 2
    qy = top + th + gap
    canvas.paste(qr, (qx, qy))
    ny = qy + qr_size + gap
    draw.text(((side - nw) // 2, ny), name, font=name_font, fill=(26, 35, 48))
    draw.text(((side - cw) // 2, ny + nh + 8), label, font=code_font, fill=(20, 110, 104))
    buf = _io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def find_user_for_bind(conn, raw: str):
    """用手机号、邮箱或会员编号 / 用户 ID 找到要绑定的账号。"""
    s = str(raw or "").strip()
    if not s:
        raise HTTPException(400, "请填写手机号或会员编号")
    digits = re.sub(r"[\s\-()+]", "", s)
    looks_phone = bool(
        re.fullmatch(r"1[3-9]\d{9}", digits)
        or re.fullmatch(r"01[016789]\d{7,8}", digits)
        or re.fullmatch(r"86?1[3-9]\d{9}", digits)
        or re.fullmatch(r"8201[016789]\d{7,8}", digits)
    )
    if not looks_phone and re.fullmatch(r"\d{1,12}", s):
        n = int(s)
        u = q(conn, "SELECT * FROM c_users WHERE member_no=%s", (n,), one=True)
        if not u:
            u = q(conn, "SELECT * FROM c_users WHERE id=%s", (n,), one=True)
        if not u:
            raise HTTPException(404, "找不到这个用户")
        return u
    try:
        kind, acct = parse_account(s)
    except HTTPException:
        raise HTTPException(400, "请填写手机号或会员编号")
    if kind == "phone":
        u = q(conn, "SELECT * FROM c_users WHERE phone=%s", (acct,), one=True)
    else:
        u = q(conn, "SELECT * FROM c_users WHERE email=%s", (acct,), one=True)
    if not u:
        raise HTTPException(404, "找不到这个用户")
    return u
def bind_promoter(conn, code: str, raw=None, unbind=False):
    p = q(conn, "SELECT * FROM c_promoters WHERE code=%s", (code,), one=True)
    if not p:
        raise HTTPException(404, "推广员不存在")
    if unbind:
        q(conn, "UPDATE c_promoters SET user_id=NULL WHERE code=%s", (code,))
        return {"ok": True, "code": code, "user_id": None}
    u = find_user_for_bind(conn, raw)
    if p.get("user_id") and int(p["user_id"]) == int(u["id"]):
        return {"ok": True, "code": code, "user_id": u["id"],
                "member_no": u.get("member_no") or u["id"], "nickname": u["nickname"]}
    other = q(conn, "SELECT code FROM c_promoters WHERE user_id=%s AND code<>%s", (u["id"], code), one=True)
    if other:
        raise HTTPException(409, "该用户已经绑定了推广码 " + other["code"])
    try:
        q(conn, "UPDATE c_promoters SET user_id=%s WHERE code=%s", (u["id"], code))
    except Exception as e:
        name = type(e).__name__
        if "Unique" in name or "unique" in str(e).lower() or "duplicate" in str(e).lower():
            raise HTTPException(409, "该用户已经绑定了其他推广码")
        raise
    return {"ok": True, "code": code, "user_id": u["id"],
            "member_no": u.get("member_no") or u["id"], "nickname": u["nickname"]}


# ---------------- 用户：申请 / 看板 ----------------
@router.get("/promo")
def p_promo():
    return _page("promo.html", "/promo", extra={"index": False})


@router.get("/promo/rules")
def p_promo_rules():
    return _page("promo-rules.html", "/promo/rules")


@router.get("/api/promo/meta")
def api_meta():
    with db() as conn:
        days = hold_days(conn)
        bps = rate_bps(conn)
    return {"hold_days": days, "rate_percent": bps / 100.0, "terms": PROMO_TERMS,
            "policy_lines": list(POLICY_LINES), "policy": POLICY_TEXT,
            "single_level": True, "disclaimer": PROMO_TERMS,
            "promo_methods": list(PROMO_METHODS), "douyin_required": True}


@router.get("/api/promo/me")
def api_me(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        p = _promoter_of_user(conn, u["id"])
        app = q(conn, "SELECT * FROM c_promo_applications WHERE user_id=%s", (u["id"],), one=True)
        days, bps = hold_days(conn), rate_bps(conn)
        out = {"is_promoter": bool(p and p["active"]), "terms": PROMO_TERMS,
               "policy_lines": list(POLICY_LINES), "policy": POLICY_TEXT,
               "hold_days": days, "rate_percent": bps / 100.0, "application": None, "dashboard": None}
        if app:
            out["application"] = {"status": app["status"], "time": _ts(app["created_at"]),
                                  "reject_reason": app.get("reject_reason"),
                                  "douyin_id": app.get("douyin_id") or "",
                                  "promo_method": app.get("promo_method") or ""}
        if not p or not p["active"]:
            return out
        _refresh_code(conn, p["code"])
        stats = _code_stats(conn, p["code"])
        refs = q(conn, """SELECT u.id, u.nickname, u.created_at, u.tier,
                                 (SELECT count(*) FROM c_commissions c WHERE c.user_id=u.id AND c.code=%s AND c.status<>'void') AS paid_n
                          FROM c_users u WHERE u.promoter_code=%s ORDER BY u.id DESC LIMIT 200""",
                 (p["code"], p["code"]))
        unsettled_cents = stats["pending_cents"] + stats["payable_cents"]
        out["dashboard"] = {
            "code": p["code"],
            "name": p.get("name") or "",
            "link": "/?ref=" + p["code"],
            "url": promo_public_url(p["code"]),
            "qr": "/api/promo/qr.png",
            "active": bool(p["active"]),
            "visits": stats["visits"],
            "regs": stats["regs"],
            "paid_count": stats["paid_count"],
            "paid_conversions": stats["paid_conversions"],
            "pending": yuan(stats["pending_cents"]),
            "payable": yuan(stats["payable_cents"]),
            "paid": yuan(stats["paid_cents"]),
            "settled": yuan(stats["paid_cents"]),
            "unsettled": yuan(unsettled_cents),
            "pending_cents": stats["pending_cents"],
            "payable_cents": stats["payable_cents"],
            "paid_cents": stats["paid_cents"],
            "unsettled_cents": unsettled_cents,
            "referrals": [{"name": mask_name(r["nickname"]), "joined": _ts(r["created_at"]),
                           "converted": r["paid_n"] > 0} for r in refs],
        }
    return out


@router.post("/api/promo/apply")
async def api_apply(req: Request):
    csrf(req)
    d = await _json(req)
    with db() as conn:
        u = need_user(conn, req)
        if str(d.get("agree") or "") not in ("1", "true", "on", "yes", "True"):
            raise HTTPException(400, "请先阅读并同意推广约定")
        douyin = clean_douyin(d.get("douyin_id") or d.get("douyin"))
        method = clean_promo_method(d.get("promo_method") or d.get("method"))
        msg = clean_text(d.get("message") or "", 200, "说明", 0)
        if _promoter_of_user(conn, u["id"]):
            raise HTTPException(400, "你已经是推广员")
        app = q(conn, "SELECT * FROM c_promo_applications WHERE user_id=%s", (u["id"],), one=True)
        if app and app["status"] == "pending":
            raise HTTPException(400, "你已提交申请，请等待审核")
        if app and app["status"] == "approved":
            raise HTTPException(400, "你已经是推广员")
        limit("promoapp:" + str(u["id"]), 5, 86400, "今天申请次数已用完")
        limit("promoappip:" + ip_of(req), 20, 86400, "今天申请次数已用完")
        if app:
            q(conn, """UPDATE c_promo_applications
                       SET message=%s, douyin_id=%s, promo_method=%s, status='pending',
                           reject_reason=NULL, created_at=now(), reviewed_at=NULL
                       WHERE user_id=%s""", (msg or None, douyin, method or None, u["id"]))
        else:
            q(conn, """INSERT INTO c_promo_applications(user_id,message,douyin_id,promo_method,status)
                       VALUES (%s,%s,%s,%s,'pending')""", (u["id"], msg or None, douyin, method or None))
    return {"ok": True, "message": "已提交申请，请等待管理员审核。"}


def _refresh_code(conn, code):
    rows = q(conn, "SELECT * FROM c_commissions WHERE code=%s AND status='pending'", (code,))
    for r in rows:
        refresh_status(conn, r)


def _code_stats(conn, code):
    _refresh_code(conn, code)
    visits = q(conn, "SELECT count(*) AS n FROM c_ref_visits WHERE code=%s", (code,), one=True)["n"]
    regs = q(conn, "SELECT count(*) AS n FROM c_users WHERE promoter_code=%s", (code,), one=True)["n"]
    paid_conv = q(conn, """SELECT count(DISTINCT user_id) AS n FROM c_commissions
                           WHERE code=%s AND status IN ('pending','payable','paid')""", (code,), one=True)["n"]
    paid_count = q(conn, """SELECT count(*) AS n FROM c_commissions
                            WHERE code=%s AND status IN ('pending','payable','paid')""", (code,), one=True)["n"]
    sums = {s: 0 for s in STATUSES}
    for r in q(conn, "SELECT status, COALESCE(sum(cents),0) AS s FROM c_commissions WHERE code=%s GROUP BY status", (code,)):
        sums[r["status"]] = r["s"]
    return {"visits": visits, "regs": regs, "paid_conversions": paid_conv, "paid_count": paid_count,
            "pending_cents": sums["pending"], "payable_cents": sums["payable"], "paid_cents": sums["paid"]}


# ---------------- 付款确认（生成 50% 佣金） ----------------
def confirm_payment(conn, uid, kind, amount, note="", apply_benefit=True):
    """管理员确认一笔已到账会员款：默认同时开通相应权益，并按实付金额的 50% 给直接推荐人记一笔佣金。"""
    if kind not in KINDS:
        raise HTTPException(400, "请选择会员类型")
    paid = parse_amount_cents(amount)
    label, tier_n = KINDS[kind]
    u = q(conn, "SELECT id, promoter_code, nickname FROM c_users WHERE id=%s", (uid,), one=True)
    if not u:
        raise HTTPException(404, "用户不存在")
    if apply_benefit:
        if kind in ("t1", "t2"):
            set_tier(conn, uid, 1 if kind == "t1" else 2)
        else:
            merchant_activate(conn, uid, "year" if kind == "m_year" else "month")
    result = {"ok": True, "kind": kind, "paid": yuan(paid), "commission": None}
    code = u.get("promoter_code")
    if not code:
        return result
    p = q(conn, "SELECT * FROM c_promoters WHERE code=%s AND active", (code,), one=True)
    if not p:
        return result
    # 禁止自我推荐；只给直接推荐人计佣（不追溯推荐人的推荐人）
    if p.get("user_id") and int(p["user_id"]) == int(uid):
        return result
    bps = rate_bps(conn)
    cents = (paid * bps) // 10000
    if cents < 1:
        return result
    days = hold_days(conn)
    now = datetime.now(timezone.utc)
    payable_at = now + timedelta(days=days)
    status = "payable" if days <= 0 else "pending"
    row = q(conn, """INSERT INTO c_commissions(code,user_id,tier,cents,paid_amount_cents,status,payable_at,kind)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (code, uid, tier_n, cents, paid, status, payable_at, kind), one=True)
    result["commission"] = {"id": row["id"], "cents": cents, "amount": yuan(cents),
                            "status": status, "payable_at": _ts(payable_at)}
    return result


def _png_response(data: bytes, filename: str):
    return Response(data, media_type="image/png", headers={
        "Cache-Control": "no-store",
        "Content-Disposition": 'inline; filename="%s"' % filename,
    })


@router.get("/api/promo/qr.png")
def api_my_qr(req: Request):
    """登录推广员只能下载自己的二维码，不接受别人的推广码参数。"""
    with db() as conn:
        u = need_user(conn, req)
        p = _promoter_of_user(conn, u["id"])
        if not p or not p["active"]:
            raise HTTPException(404, "你还不是推广员")
        data = render_promo_qr_png(p["code"], p.get("name") or u.get("nickname") or "")
    return _png_response(data, "lvhan-promo-%s.png" % p["code"])


@router.get("/api/admin/promoters/{code}/qr.png")
def api_admin_qr(req: Request, code: str):
    code = norm_code(code)
    if not code:
        raise HTTPException(400, "推广码不正确")
    with db() as conn:
        need_admin(conn, req)
        p = q(conn, "SELECT * FROM c_promoters WHERE code=%s", (code,), one=True)
        if not p:
            raise HTTPException(404, "推广员不存在")
        data = render_promo_qr_png(p["code"], p.get("name") or "")
    return _png_response(data, "lvhan-promo-%s.png" % p["code"])


# ---------------- 管理员 ----------------
@router.get("/api/admin/promo/applications")
def api_admin_apps(req: Request, status: str = "pending"):
    status = (status or "pending").strip()
    if status not in ("pending", "approved", "rejected", "all"):
        raise HTTPException(400, "状态不正确")
    with db() as conn:
        need_admin(conn, req)
        sql = """SELECT a.*, u.nickname, u.member_no, u.promoter_code AS referrer
                 FROM c_promo_applications a JOIN c_users u ON u.id=a.user_id"""
        args = []
        if status != "all":
            sql += " WHERE a.status=%s"; args.append(status)
        sql += " ORDER BY a.id DESC LIMIT 200"
        rows = q(conn, sql, tuple(args))
    return {"items": [_app_out(r) for r in rows]}


@router.post("/api/admin/promo/applications/{aid}")
async def api_admin_app_review(req: Request, aid: int):
    csrf(req)
    d = await _json(req)
    act = d.get("action")
    if act not in ("approve", "reject"):
        raise HTTPException(400, "未知操作")
    with db() as conn:
        need_admin(conn, req)
        row = q(conn, "SELECT * FROM c_promo_applications WHERE id=%s FOR UPDATE", (aid,), one=True)
        if not row:
            raise HTTPException(404, "找不到这份申请")
        if row["status"] != "pending":
            raise HTTPException(400, "这份申请已经处理过了")
        if act == "reject":
            reason = clean_text(d.get("reason") or "未通过", 100, "理由")
            q(conn, "UPDATE c_promo_applications SET status='rejected', reject_reason=%s, reviewed_at=now() WHERE id=%s",
              (reason, aid))
            return {"ok": True, "status": "rejected"}
        if _promoter_of_user(conn, row["user_id"]):
            raise HTTPException(400, "该用户已经是推广员")
        u = q(conn, "SELECT id, nickname, member_no, promoter_code FROM c_users WHERE id=%s", (row["user_id"],), one=True)
        code = norm_code(d.get("code")) or _new_code(conn)
        if q(conn, "SELECT code FROM c_promoters WHERE code=%s", (code,), one=True):
            raise HTTPException(409, "推广码已存在")
        # 不能把自己注册时的推荐码写成自己的码
        q(conn, """INSERT INTO c_promoters(code,name,user_id,active,douyin_id,promo_method)
                   VALUES (%s,%s,%s,TRUE,%s,%s)""",
          (code, (u["nickname"] or "推广员")[:30], u["id"],
           row.get("douyin_id") or None, row.get("promo_method") or None))
        q(conn, "UPDATE c_promo_applications SET status='approved', reviewed_at=now() WHERE id=%s", (aid,))
        active_codes(conn)
    return {"ok": True, "status": "approved", "code": code}


def _app_out(r):
    return {"id": r["id"], "user_id": r["user_id"], "member_no": r.get("member_no") or r["user_id"],
            "nickname": r["nickname"], "status": r["status"], "message": r.get("message") or "",
            "douyin_id": r.get("douyin_id") or "", "promo_method": r.get("promo_method") or "",
            "reject_reason": r.get("reject_reason"), "time": _ts(r["created_at"]),
            "referrer": r.get("referrer")}


@router.get("/api/admin/promo/applications.csv")
def api_admin_apps_csv(req: Request, status: str = "all"):
    status = (status or "all").strip()
    if status not in ("pending", "approved", "rejected", "all"):
        raise HTTPException(400, "状态不正确")
    with db() as conn:
        need_admin(conn, req)
        sql = """SELECT a.*, u.nickname, u.member_no, u.promoter_code AS referrer
                 FROM c_promo_applications a JOIN c_users u ON u.id=a.user_id"""
        args = []
        if status != "all":
            sql += " WHERE a.status=%s"; args.append(status)
        sql += " ORDER BY a.id DESC LIMIT 2000"
        rows = q(conn, sql, tuple(args))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "user_id", "member_no", "nickname", "douyin_id", "promo_method",
                "message", "status", "reject_reason", "time", "referrer"])
    for r in rows:
        o = _app_out(r)
        w.writerow([o["id"], o["user_id"], o["member_no"], o["nickname"], o["douyin_id"],
                    o["promo_method"], o["message"], o["status"], o.get("reject_reason") or "",
                    o["time"] or "", o.get("referrer") or ""])
    data = "\ufeff" + buf.getvalue()
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=promo-applications.csv"})


@router.get("/api/admin/promo/settings")
def api_admin_settings(req: Request):
    with db() as conn:
        need_admin(conn, req)
        return {"hold_days": hold_days(conn), "rate_percent": rate_bps(conn) / 100.0, "rate_bps": rate_bps(conn)}


@router.post("/api/admin/promo/settings")
async def api_admin_set_settings(req: Request):
    csrf(req)
    d = await _json(req)
    with db() as conn:
        need_admin(conn, req)
        days = d.get("hold_days")
        bps = d.get("rate_bps")
        if d.get("rate_percent") not in (None, "") and bps in (None, ""):
            try:
                bps = int(round(float(d.get("rate_percent")) * 100))
            except (TypeError, ValueError):
                raise HTTPException(400, "佣金比例无效")
        set_promo_settings(conn, days=days, bps=bps)
        return {"ok": True, "hold_days": hold_days(conn), "rate_percent": rate_bps(conn) / 100.0}


def _list_commissions(conn, code="", status=""):
    _refresh_all = q(conn, "SELECT * FROM c_commissions WHERE status='pending' AND payable_at<=now()")
    for r in _refresh_all:
        refresh_status(conn, r)
    cond, args = [], []
    if code:
        cond.append("c.code=%s"); args.append(norm_code(code) or code)
    if status and status != "all":
        cond.append("c.status=%s"); args.append(status)
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    rows = q(conn, f"""SELECT c.*, u.nickname, u.member_no FROM c_commissions c
                       LEFT JOIN c_users u ON u.id=c.user_id
                       {where} ORDER BY c.id DESC LIMIT 500""", tuple(args))
    return rows


def _comm_out(r):
    return {"id": r["id"], "code": r["code"], "user_id": r["user_id"],
            "member_no": r.get("member_no") or r["user_id"],
            "nickname_masked": mask_name(r.get("nickname") or ""),
            "tier": r["tier"], "kind": r.get("kind") or "",
            "paid_amount": yuan(r.get("paid_amount_cents")),
            "amount": yuan(r["cents"]), "cents": r["cents"],
            "status": r["status"], "time": _ts(r["created_at"]),
            "payable_at": _ts(r.get("payable_at")), "paid_at": _ts(r.get("paid_at")),
            "paid_note": r.get("paid_note") or "", "void_reason": r.get("void_reason") or ""}


@router.get("/api/admin/commissions")
def api_admin_commissions(req: Request, code: str = "", status: str = "all"):
    with db() as conn:
        need_admin(conn, req)
        rows = _list_commissions(conn, code, status)
    return {"items": [_comm_out(r) for r in rows]}


@router.get("/api/admin/commissions.csv")
def api_admin_commissions_csv(req: Request, code: str = "", status: str = "all"):
    with db() as conn:
        need_admin(conn, req)
        rows = _list_commissions(conn, code, status)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "code", "member_no", "user", "kind", "paid_yuan", "commission_yuan", "status", "created", "payable_at", "paid_at", "note"])
    for r in rows:
        o = _comm_out(r)
        w.writerow([o["id"], o["code"], o["member_no"], o["nickname_masked"], o["kind"],
                    o["paid_amount"], o["amount"], o["status"], o["time"], o["payable_at"], o["paid_at"], o["paid_note"]])
    data = "\ufeff" + buf.getvalue()
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=commissions.csv"})


@router.post("/api/admin/commissions/{cid}")
async def api_admin_comm_action(req: Request, cid: int):
    csrf(req)
    d = await _json(req)
    act = d.get("action")
    if act not in ("pay", "void"):
        raise HTTPException(400, "未知操作")
    with db() as conn:
        need_admin(conn, req)
        row = q(conn, "SELECT * FROM c_commissions WHERE id=%s FOR UPDATE", (cid,), one=True)
        if not row:
            raise HTTPException(404, "找不到这笔佣金")
        st = refresh_status(conn, row)
        if act == "void":
            if st == "paid":
                raise HTTPException(400, "已发放的佣金不能作废，请另行处理")
            reason = clean_text(d.get("reason") or "退款作废", 100, "理由")
            q(conn, "UPDATE c_commissions SET status='void', void_reason=%s, settled_at=now() WHERE id=%s", (reason, cid))
            return {"ok": True, "status": "void"}
        if st != "payable":
            raise HTTPException(400, "只有已过冻结期、状态为待发放的佣金才能标记已打款")
        note = clean_text(d.get("note") or "已人工打款", 100, "备注", 0)
        q(conn, "UPDATE c_commissions SET status='paid', paid_at=now(), paid_note=%s, settled_at=now() WHERE id=%s",
          (note or "已人工打款", cid))
    return {"ok": True, "status": "paid"}
