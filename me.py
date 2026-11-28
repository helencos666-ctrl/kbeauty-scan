"""「我的」页、开屏广告、购物优惠合集页、邀请有奖（一级邀请，站内抵用券）

- 账号沿用现有注册/登录（手机号或邮箱 + 密码，手机号非必填）。
- 等级只按真实累计获得的旅行豆计算，不编造。
- 邀请有奖：好友经邀请链接注册并完成首次使用（查一次地铁路线 / 收藏一条优惠）后：
    好友得 10 元代订抵用券（满 50 可用）；邀请人第 1/3/5 位有效好友时分别得 15/40/80 元券。
  只计一级；单人奖励上限 150 元；总额度 3000 元；试运行 90 天；券 60 天有效、不可折现；
  同设备互邀不计入；1 小时内 ≥3 人或同 IP 批量注册 → 冻结人工复核；分享动作本身没有奖励。
- 设备/IP 只存加盐哈希，不存原始值。
"""
from __future__ import annotations

import datetime, hashlib, io, os, re, secrets

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from PIL import Image, ImageOps

from community import (POST_IMG_FMTS, _open_image, _json, _page, _ts, clean_text, csrf, current_user, db, ip_of, limit,
                       need_admin, need_user, q)

router = APIRouter()
KST = datetime.timezone(datetime.timedelta(hours=9))
UTC = datetime.timezone.utc

MIGRATE_ME = """
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS bio TEXT;
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS invite_code TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS c_users_invite_code_uidx ON c_users(invite_code) WHERE invite_code IS NOT NULL;
CREATE TABLE IF NOT EXISTS c_splash (
  id INT PRIMARY KEY DEFAULT 1,
  image BYTEA,
  link TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  freq TEXT NOT NULL DEFAULT 'daily',
  starts_on DATE,
  ends_on DATE,
  show_label BOOLEAN NOT NULL DEFAULT TRUE,
  version INT NOT NULL DEFAULT 0,
  shows INT NOT NULL DEFAULT 0,
  clicks INT NOT NULL DEFAULT 0,
  skips INT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS c_ref_config (
  id INT PRIMARY KEY DEFAULT 1,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  starts_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ends_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '90 days'),
  total_cap_cents INT NOT NULL DEFAULT 300000,
  person_cap_cents INT NOT NULL DEFAULT 15000
);
INSERT INTO c_ref_config(id) VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS c_ref_invites (
  id BIGSERIAL PRIMARY KEY,
  inviter_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  invitee_id BIGINT NOT NULL UNIQUE REFERENCES c_users(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending',
  reason TEXT NOT NULL DEFAULT '',
  flags TEXT NOT NULL DEFAULT '',
  dev_hash TEXT,
  ip_hash TEXT,
  first_use_kind TEXT,
  first_use_at TIMESTAMPTZ,
  validated_at TIMESTAMPTZ,
  reviewed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_ref_invites_inviter_idx ON c_ref_invites(inviter_id, created_at DESC);
CREATE TABLE IF NOT EXISTS c_ref_devices (
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  dev_hash TEXT NOT NULL,
  ip_hash TEXT,
  last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, dev_hash)
);
CREATE INDEX IF NOT EXISTS c_ref_devices_dev_idx ON c_ref_devices(dev_hash);
CREATE UNIQUE INDEX IF NOT EXISTS c_coupons_ref_uidx ON c_coupons(user_id, kind) WHERE kind LIKE 'ref_%';
CREATE TABLE IF NOT EXISTS c_ref_backfill (
  id BIGSERIAL PRIMARY KEY,
  job_key TEXT NOT NULL DEFAULT '',
  target TEXT NOT NULL DEFAULT '',
  inviter_code TEXT NOT NULL,
  inviter_id BIGINT,
  invitee_id BIGINT,
  outcome TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  actor TEXT NOT NULL DEFAULT 'system',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_ref_backfill_job_idx ON c_ref_backfill(job_key, target);
CREATE TABLE IF NOT EXISTS c_feedback (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT REFERENCES c_users(id) ON DELETE SET NULL,
  body TEXT NOT NULL,
  page TEXT NOT NULL DEFAULT '',
  ip_hash TEXT,
  status TEXT NOT NULL DEFAULT 'new',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# ---------------- 工具 ----------------
def _salt():
    return (os.environ.get("LVHAN_PII_KEY", "")[:16] or "lvhan")

def _h(kind, v):
    return hashlib.sha256((_salt() + "|" + kind + "|" + v).encode()).hexdigest()[:24]

def _ip_h(req):
    return _h("ip", ip_of(req))

DEV_RE = re.compile(r"^[a-f0-9]{16,64}$")

def _dev_h(req):
    d = (req.headers.get("x-lh-dev") or "").strip().lower()
    return _h("dev", d) if DEV_RE.match(d) else None

def _touch_device(conn, uid, req):
    dh = _dev_h(req)
    if not dh:
        return None
    q(conn, """INSERT INTO c_ref_devices(user_id,dev_hash,ip_hash) VALUES (%s,%s,%s)
               ON CONFLICT (user_id,dev_hash) DO UPDATE SET last_seen=now(), ip_hash=EXCLUDED.ip_hash""",
      (uid, dh, _ip_h(req)))
    return dh

LEVELS = [(0, 1), (100, 2), (300, 3), (800, 4), (2000, 5), (5000, 6)]

def level_of(earned: int):
    lv, nxt = 1, None
    for i, (th, l) in enumerate(LEVELS):
        if earned >= th:
            lv = l
            nxt = LEVELS[i + 1][0] if i + 1 < len(LEVELS) else None
    return {"lv": lv, "earned": earned, "next": nxt}

# ---------------- 页面 ----------------
@router.get("/me")
def p_me():
    return _page("me.html", "/me", extra={"title": "我的 · 旅韩助手", "description": "我的旅行豆、卡券、帖子与常用工具。"})

@router.get("/deals")
def p_deals():
    return _page("deals.html", "/deals", extra={"title": "购物优惠合集｜免税店、药妆、退税与韩国购物季 · 旅韩助手",
                                                 "description": "汇总官方公开的韩国购物优惠：Korea Grand Sale、免税店会员活动、退税要点与大促提醒，每条注明来源。"})

@router.get("/festival")
def p_festival():
    return _page("festival.html", "/festival", extra={"title": "韩国购物节｜Korea Grand Sale 冬季购物季 · 旅韩助手",
                                                       "description": "Korea Grand Sale 冬季购物季（2026-12-23 至 2027-02-13）：官方活动、免税店、药妆与退税入口，每条注明来源。"})

@router.get("/invite")
def p_invite():
    return _page("invite.html", "/invite", extra={"title": "邀请有奖 · 旅韩助手",
                                                   "description": "邀请同行一起用旅韩助手，好友完成首次使用后双方都可获得站内抵用券。"})

@router.get("/opening")
def p_opening():
    return _page("opening.html", "/opening", extra={"title": "开业活动 · 旅韩助手",
                                                     "description": "旅韩助手开业活动：邀请有奖、电话卡、到店优惠、电影节门票和商家入驻免费。规则以本页和店家现场说明为准。"})

# ---------------- 我的 ----------------
@router.get("/api/me/summary")
def api_me_summary(req: Request):
    with db() as conn:
        u = current_user(conn, req)
        if not u:
            return {"user": None}
        _touch_device(conn, u["id"], req)
        from points import user_points_summary
        pts = user_points_summary(conn, u["id"])
        r = q(conn, """SELECT bio,
                         (SELECT COALESCE(sum(points),0) FROM c_points_ledger WHERE user_id=%s AND points>0) AS earned,
                         (SELECT count(*) FROM c_posts WHERE user_id=%s AND NOT deleted) AS posts
                       FROM c_users WHERE id=%s""", (u["id"], u["id"], u["id"]), one=True)
        from retention import notif_unread
        nun = notif_unread(conn, u["id"])
        unread = q(conn, "SELECT count(*) AS n FROM c_messages WHERE recipient_id=%s AND read_at IS NULL AND NOT deleted",
                   (u["id"],), one=True)["n"]
        from merch import consult_unread
        cun = consult_unread(conn, u["id"])
    return {"user": {"id": u["id"], "member_no": u.get("member_no") or u["id"], "nickname": u["nickname"],
                     "bio": r["bio"] or "", "has_avatar": bool(u.get("has_avatar")), "joined": _ts(u["created_at"]),
                     "level": level_of(int(r["earned"])), "points": pts["balance"], "points_name": pts["name"],
                     "coupons": pts["coupon_available"], "posts": int(r["posts"]), "notif_unread": nun, "msg_unread": int(unread),
                     "consult_unread": cun}}

BAD_NICK = re.compile(r"管理员|官方|admin|旅韩小助手", re.I)

@router.post("/api/me/profile")
async def api_me_profile(req: Request):
    csrf(req)
    d = await _json(req)
    with db() as conn:
        u = need_user(conn, req)
        limit("prof:" + str(u["id"]), 20, 3600, "修改太频繁，请稍后再试")
        nick = clean_text(d.get("nickname"), 16, "昵称", 2)
        if BAD_NICK.search(nick):
            raise HTTPException(400, "昵称不能包含“管理员/官方/旅韩小助手”")
        bio = (str(d.get("bio") or "")).replace("\n", " ").strip()
        if len(bio) > 40:
            raise HTTPException(400, "简介最多 40 个字")
        from contact_guard import guard_text, guard_medical
        guard_text(conn, u["id"], "profile", nick, bio)
        guard_medical(bio)
        q(conn, "UPDATE c_users SET nickname=%s, bio=%s WHERE id=%s", (nick, bio or None, u["id"]))
    return {"ok": True, "nickname": nick, "bio": bio}

@router.post("/api/me/feedback")
async def api_feedback(req: Request):
    csrf(req)
    limit("fb:" + ip_of(req), 5, 3600, "反馈太频繁，请稍后再试")
    d = await _json(req)
    body = clean_text(d.get("body"), 500, "反馈内容", 4)
    page = str(d.get("page") or "")[:80]
    with db() as conn:
        u = current_user(conn, req)
        q(conn, "INSERT INTO c_feedback(user_id,body,page,ip_hash) VALUES (%s,%s,%s,%s)",
          (u["id"] if u else None, body, page, _ip_h(req)))
    return {"ok": True}

# ---------------- 开屏广告 ----------------
FREQS = {"daily": "每人每天 1 次", "session": "每次打开 1 次", "always": "每次进入首页"}

def _splash_live(r):
    if not r or not r["enabled"] or not r["has_img"]:
        return False
    today = datetime.datetime.now(KST).date()
    if r["starts_on"] and today < r["starts_on"]:
        return False
    if r["ends_on"] and today > r["ends_on"]:
        return False
    return True

@router.get("/api/splash")
def api_splash():
    with db() as conn:
        r = q(conn, "SELECT enabled,(image IS NOT NULL) AS has_img,link,title,freq,starts_on,ends_on,show_label,version FROM c_splash WHERE id=1", one=True)
    if not _splash_live(r):
        return {"on": False}
    return {"on": True, "v": r["version"], "link": r["link"], "title": r["title"], "freq": r["freq"], "label": r["show_label"],
            "img": "/api/splash/img?v=%d" % r["version"]}

@router.get("/api/splash/img")
def api_splash_img(v: int = 0):
    with db() as conn:
        r = q(conn, "SELECT image FROM c_splash WHERE id=1", one=True)
    if not r or r["image"] is None:
        raise HTTPException(404, "没有图片")
    return Response(bytes(r["image"]), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})

@router.post("/api/splash/ev")
async def api_splash_ev(req: Request):
    d = await _json(req)
    col = {"show": "shows", "click": "clicks", "skip": "skips"}.get(str(d.get("e")))
    if not col:
        raise HTTPException(400, "参数错误")
    try:
        limit("spl:" + col + ":" + ip_of(req), 30, 3600)
    except HTTPException:
        return {"ok": True}
    with db() as conn:
        q(conn, f"UPDATE c_splash SET {col}={col}+1 WHERE id=1")
    return {"ok": True}

def _splash_img(raw: bytes) -> bytes:
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(413, "图片不能超过 8MB")
    im = _open_image(raw, POST_IMG_FMTS, "图片无法识别，请换 JPG/PNG/WebP")
    im = ImageOps.exif_transpose(im)
    if im.mode != "RGB":
        bg = Image.new("RGB", im.size, (255, 255, 255))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA"); bg.paste(im, mask=im.split()[-1])
        else:
            bg.paste(im.convert("RGB"))
        im = bg
    im.thumbnail((1080, 1920))
    b = io.BytesIO(); im.save(b, "JPEG", quality=84, optimize=True, progressive=True)
    return b.getvalue()

def _date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, "日期格式应为 YYYY-MM-DD")

@router.get("/api/admin/splash")
def api_admin_splash(req: Request):
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, """SELECT enabled,(image IS NOT NULL) AS has_img,octet_length(image) AS size,link,title,freq,starts_on,ends_on,
                              show_label,version,shows,clicks,skips,updated_at FROM c_splash WHERE id=1""", one=True)
    if not r:
        return {"splash": None, "freqs": FREQS}
    o = dict(r)
    o["starts_on"] = r["starts_on"].isoformat() if r["starts_on"] else ""
    o["ends_on"] = r["ends_on"].isoformat() if r["ends_on"] else ""
    o["updated_at"] = _ts(r["updated_at"])
    o["img"] = "/api/splash/img?v=%d" % r["version"] if r["has_img"] else ""
    o["live"] = _splash_live(r)
    return {"splash": o, "freqs": FREQS}

@router.post("/api/admin/splash")
async def api_admin_splash_save(req: Request, link: str = Form(""), title: str = Form(""), freq: str = Form("daily"),
                                starts_on: str = Form(""), ends_on: str = Form(""), enabled: str = Form(""),
                                show_label: str = Form("1"), image: UploadFile | None = File(None)):
    csrf(req)
    with db() as conn:
        need_admin(conn, req)
    link = link.strip()
    if link and (not link.startswith("/") or link.startswith("//") or len(link) > 200 or re.search(r"[\s<>\"']", link)):
        raise HTTPException(400, "跳转只能是站内页面，例如 /deals")
    if freq not in FREQS:
        raise HTTPException(400, "展示频率不对")
    s, e = _date(starts_on), _date(ends_on)
    if s and e and e < s:
        raise HTTPException(400, "结束日期不能早于开始日期")
    data = None
    if image is not None and image.filename:
        data = _splash_img(await image.read(8 * 1024 * 1024 + 1))
    with db() as conn:
        need_admin(conn, req)
        q(conn, "INSERT INTO c_splash(id) VALUES (1) ON CONFLICT DO NOTHING")
        if data is not None:
            q(conn, "UPDATE c_splash SET image=%s, version=version+1, shows=0, clicks=0, skips=0 WHERE id=1", (data,))
        r = q(conn, "SELECT image IS NOT NULL AS has FROM c_splash WHERE id=1", one=True)
        on = enabled in ("1", "true", "on")
        if on and not r["has"]:
            raise HTTPException(400, "请先上传广告图片再启用")
        q(conn, """UPDATE c_splash SET link=%s,title=%s,freq=%s,starts_on=%s,ends_on=%s,enabled=%s,show_label=%s,updated_at=now(),
                   version=CASE WHEN freq<>%s OR link<>%s THEN version+1 ELSE version END WHERE id=1""",
          (link, title.strip()[:40], freq, s, e, on, show_label in ("1", "true", "on"), freq, link))
    return {"ok": True}

# ---------------- 邀请有奖 ----------------
REF_TIERS = [(1, "ref_inviter_15", 1500, "邀请奖励抵用券 ¥15（代订满50 / 接送机满200可用）"),
             (3, "ref_inviter_40", 4000, "邀请奖励抵用券 ¥40（满150可用）"),
             (5, "ref_inviter_80", 8000, "邀请奖励·免费代订券（抵扣上限 ¥80）")]
INVITEE_KIND, INVITEE_CENTS, INVITEE_TITLE = "ref_invitee_10", 1000, "新客代订抵用券 ¥10（满50可用）"
SCOPE = "用于旅韩助手站内门店代订/接送机服务费抵扣（服务陆续上线）；每单限 1 张，不可折现、不可转让、不找零，过期作废。"
COUPON_DAYS = 60
CODE_ABC = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

def _cfg(conn):
    return q(conn, "SELECT * FROM c_ref_config WHERE id=1", one=True)

def _active(c):
    now = datetime.datetime.now(UTC)
    return bool(c and c["enabled"] and c["starts_at"] <= now <= c["ends_at"])

def _issued_total(conn):
    return int(q(conn, "SELECT COALESCE(sum(amount_cents),0) AS s FROM c_coupons WHERE kind LIKE 'ref_%%' AND status<>'revoked'", one=True)["s"])

def _user_ref_total(conn, uid):
    return int(q(conn, "SELECT COALESCE(sum(amount_cents),0) AS s FROM c_coupons WHERE user_id=%s AND kind LIKE 'ref_inviter_%%' AND status<>'revoked'",
                 (uid,), one=True)["s"])

def _issue(conn, c, uid, kind, cents, title, staff):
    if _issued_total(conn) + cents > c["total_cap_cents"]:
        return False
    exp = datetime.datetime.now(UTC) + datetime.timedelta(days=COUPON_DAYS)
    r = q(conn, """INSERT INTO c_coupons(user_id,kind,title,amount_cents,status,scope_note,staff_note,expires_at)
                   VALUES (%s,%s,%s,%s,'available',%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
          (uid, kind, title, cents, SCOPE, staff, exp), one=True)
    return bool(r)

def _reward(conn, inv):
    """有效邀请后发券（总额度、单人上限都满足才发）。"""
    c = _cfg(conn)
    q(conn, "SELECT pg_advisory_xact_lock(424242)")
    _issue(conn, c, inv["invitee_id"], INVITEE_KIND, INVITEE_CENTS, INVITEE_TITLE, "邀请活动·新客券（邀请记录 #%d）" % inv["id"])
    n = q(conn, "SELECT count(*) AS n FROM c_ref_invites WHERE inviter_id=%s AND status='valid'", (inv["inviter_id"],), one=True)["n"]
    for need, kind, cents, title in REF_TIERS:
        if n >= need and _user_ref_total(conn, inv["inviter_id"]) + cents <= c["person_cap_cents"]:
            _issue(conn, c, inv["inviter_id"], kind, cents, title, "邀请活动·第 %d 位有效好友" % need)

def _code(conn, uid):
    r = q(conn, "SELECT invite_code FROM c_users WHERE id=%s", (uid,), one=True)
    if r and r["invite_code"]:
        return r["invite_code"]
    for _ in range(20):
        code = "".join(secrets.choice(CODE_ABC) for _ in range(6))
        try:
            q(conn, "UPDATE c_users SET invite_code=%s WHERE id=%s AND invite_code IS NULL", (code, uid))
            r = q(conn, "SELECT invite_code FROM c_users WHERE id=%s", (uid,), one=True)
            if r["invite_code"]:
                return r["invite_code"]
        except Exception:
            conn.rollback()
    raise HTTPException(500, "生成邀请码失败，请重试")

def _mask(nick):
    nick = nick or ""
    return (nick[:1] + "**") if nick else "**"

def _bind(conn, req, u, code):
    """把新用户绑定到邀请人。返回状态说明（不抛错，避免影响正常使用）。"""
    code = (code or "").strip().upper()
    if not re.fullmatch(r"[%s]{6}" % CODE_ABC, code):
        return "bad_code"
    c = _cfg(conn)
    if not _active(c):
        return "inactive"
    if q(conn, "SELECT 1 AS x FROM c_ref_invites WHERE invitee_id=%s", (u["id"],), one=True):
        return "already"
    inviter = q(conn, "SELECT id,is_banned FROM c_users WHERE invite_code=%s", (code,), one=True)
    if not inviter or inviter["is_banned"]:
        return "bad_code"
    if inviter["id"] == u["id"]:
        return "self"
    if u["created_at"] < datetime.datetime.now(UTC) - datetime.timedelta(hours=48) or u["created_at"] < c["starts_at"]:
        return "not_new"
    dh, ih = _touch_device(conn, u["id"], req), _ip_h(req)
    status, reason, flags = "pending", "", []
    if dh and q(conn, "SELECT 1 AS x FROM c_ref_devices WHERE user_id=%s AND dev_hash=%s", (inviter["id"], dh), one=True):
        status, reason = "excluded", "与邀请人同一设备，不计入"
    elif dh and q(conn, """SELECT 1 AS x FROM c_ref_invites WHERE dev_hash=%s AND invitee_id<>%s""", (dh, u["id"]), one=True):
        status, reason = "excluded", "该设备已被其他邀请使用，不计入"
    else:
        if q(conn, "SELECT 1 AS x FROM c_ref_devices WHERE user_id=%s AND ip_hash=%s", (inviter["id"], ih), one=True):
            flags.append("与邀请人同网络")
        recent = q(conn, "SELECT count(*) AS n FROM c_ref_invites WHERE inviter_id=%s AND created_at>now()-interval '1 hour'",
                   (inviter["id"],), one=True)["n"]
        same_ip = q(conn, "SELECT count(*) AS n FROM c_ref_invites WHERE ip_hash=%s AND created_at>now()-interval '24 hours'",
                    (ih,), one=True)["n"]
        if recent >= 2:
            status, reason = "frozen", "1 小时内 ≥3 人，冻结待复核"
        elif same_ip >= 2:
            status, reason = "frozen", "同一网络 24 小时内批量注册，冻结待复核"
    q(conn, """INSERT INTO c_ref_invites(inviter_id,invitee_id,status,reason,flags,dev_hash,ip_hash)
               VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (invitee_id) DO NOTHING""",
      (inviter["id"], u["id"], status, reason, "、".join(flags), dh, ih))
    return status

@router.get("/api/ref/me")
def api_ref_me(req: Request):
    with db() as conn:
        c = _cfg(conn)
        prog = {"active": _active(c), "ends": _ts(c["ends_at"])[:10], "starts": _ts(c["starts_at"])[:10],
                "budget_left": max(0, c["total_cap_cents"] - _issued_total(conn)) > 0}
        u = current_user(conn, req)
        if not u:
            return {"user": None, "program": prog}
        _touch_device(conn, u["id"], req)
        code = _code(conn, u["id"])
        rows = q(conn, """SELECT i.status,i.created_at,i.first_use_at,u.nickname FROM c_ref_invites i JOIN c_users u ON u.id=i.invitee_id
                          WHERE i.inviter_id=%s ORDER BY i.id DESC LIMIT 50""", (u["id"],))
        valid = sum(1 for r in rows if r["status"] == "valid")
        cps = q(conn, """SELECT kind,title,amount_cents,status,expires_at FROM c_coupons WHERE user_id=%s AND kind LIKE 'ref_%%'
                         ORDER BY id""", (u["id"],))
        mine = q(conn, "SELECT status FROM c_ref_invites WHERE invitee_id=%s", (u["id"],), one=True)
    label = {"pending": "待首次使用", "valid": "有效", "frozen": "复核中", "excluded": "不计入", "invalid": "无效"}
    return {"user": {"id": u["id"]}, "program": prog, "code": code, "link": "/invite?inv=" + code, "valid": valid,
            "invites": [{"name": _mask(r["nickname"]), "joined": _ts(r["created_at"])[:10], "status": r["status"],
                         "label": label.get(r["status"], r["status"])} for r in rows],
            "coupons": [{"kind": r["kind"], "title": r["title"], "amount": r["amount_cents"] // 100, "status": r["status"],
                         "expires": _ts(r["expires_at"])[:10]} for r in cps],
            "invited_status": mine["status"] if mine else None}

@router.post("/api/ref/bind")
async def api_ref_bind(req: Request):
    csrf(req)
    d = await _json(req)
    with db() as conn:
        if d.get("soft"):
            u = current_user(conn, req)
            if not u:                       # 每个页面的自动补绑：没登录就静默跳过，不报错
                return {"ok": True, "status": "guest"}
        else:
            u = need_user(conn, req)
        limit("refb:" + str(u["id"]), 30 if d.get("soft") else 10, 3600)
        st = _bind(conn, req, u, d.get("code"))
    return {"ok": True, "status": st}

@router.post("/api/ref/use")
async def api_ref_use(req: Request):
    """首次使用（查地铁路线 / 收藏一条优惠）。可顺带绑定邀请码。"""
    csrf(req)
    d = await _json(req)
    kind = d.get("kind") if d.get("kind") in ("subway", "deal") else None
    if not kind:
        raise HTTPException(400, "参数错误")
    with db() as conn:
        u = current_user(conn, req)
        if not u:
            return {"ok": True, "status": "guest"}
        limit("refu:" + str(u["id"]), 30, 3600)
        if d.get("code"):
            _bind(conn, req, u, d.get("code"))
        inv = q(conn, "SELECT * FROM c_ref_invites WHERE invitee_id=%s FOR UPDATE", (u["id"],), one=True)
        if not inv:
            return {"ok": True, "status": "none"}
        if inv["first_use_at"] is None:
            q(conn, "UPDATE c_ref_invites SET first_use_at=now(), first_use_kind=%s WHERE id=%s", (kind, inv["id"]))
            if inv["status"] == "pending" and _active(_cfg(conn)):
                q(conn, "UPDATE c_ref_invites SET status='valid', validated_at=now() WHERE id=%s", (inv["id"],))
                _reward(conn, inv)
                return {"ok": True, "status": "valid", "reward": INVITEE_TITLE}
        return {"ok": True, "status": inv["status"]}

# ---------------- 补录漏绑的邀请（有审计记录） ----------------
BACKFILL_WINDOW = (datetime.timedelta(seconds=6), datetime.timedelta(seconds=2))   # 日志时间前 6 秒 ~ 后 2 秒内创建的账号

def _audit(conn, job_key, target, code, inviter_id, invitee_id, outcome, detail, actor):
    q(conn, """INSERT INTO c_ref_backfill(job_key,target,inviter_code,inviter_id,invitee_id,outcome,detail,actor)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (job_key, target, code, inviter_id, invitee_id, outcome, detail[:300], actor))
    print("ref backfill", job_key or "manual", target, "inviter=%s" % code, "invitee=%s" % invitee_id, outcome, flush=True)

def backfill_invite(conn, code, invitee_id, evidence, actor="admin", job_key="", target=""):
    """补录一条被漏绑的邀请。规则与 _bind 完全一致（邀请有奖未开启 / 邀请码无效 / 自邀 / 已绑 / 账号超过 48 小时 /
    同一设备 / 设备已被其他邀请使用 都不补），通过后写入 pending 并留审计。返回 (outcome, detail)。"""
    code = (code or "").strip().upper()
    def done(outcome, detail, inviter_id=None):
        _audit(conn, job_key, target, code, inviter_id, invitee_id, outcome, detail, actor)
        return outcome, detail
    c = _cfg(conn)
    if not _active(c):
        return done("skipped", "邀请有奖未开启")
    inviter = q(conn, "SELECT id,is_banned FROM c_users WHERE invite_code=%s", (code,), one=True)
    if not inviter or inviter["is_banned"]:
        return done("skipped", "邀请码无效或邀请人已封禁")
    u = q(conn, "SELECT id,created_at,is_banned,is_bot FROM c_users WHERE id=%s", (invitee_id,), one=True) if invitee_id else None
    if not u:
        return done("skipped", "找不到被邀请账号", inviter["id"])
    if u["is_banned"] or u.get("is_bot"):
        return done("skipped", "被邀请账号已封禁或是系统账号", inviter["id"])
    if u["id"] == inviter["id"]:
        return done("skipped", "自己邀请自己", inviter["id"])
    if q(conn, "SELECT 1 AS x FROM c_ref_invites WHERE invitee_id=%s", (u["id"],), one=True):
        return done("skipped", "该账号已有邀请记录", inviter["id"])
    if u["created_at"] < datetime.datetime.now(UTC) - datetime.timedelta(hours=48) or u["created_at"] < c["starts_at"]:
        return done("skipped", "账号创建已超过 48 小时", inviter["id"])
    devs = [r["dev_hash"] for r in q(conn, "SELECT dev_hash FROM c_ref_devices WHERE user_id=%s", (u["id"],))]
    if devs:
        if q(conn, "SELECT 1 AS x FROM c_ref_devices WHERE user_id=%s AND dev_hash = ANY(%s)", (inviter["id"], devs), one=True):
            return done("skipped", "与邀请人同一设备", inviter["id"])
        if q(conn, "SELECT 1 AS x FROM c_ref_invites WHERE dev_hash = ANY(%s) AND invitee_id<>%s", (devs, u["id"]), one=True):
            return done("skipped", "该设备已被其他邀请使用", inviter["id"])
    q(conn, """INSERT INTO c_ref_invites(inviter_id,invitee_id,status,reason,flags,dev_hash,ip_hash)
               VALUES (%s,%s,'pending',%s,'管理员补录',%s,NULL) ON CONFLICT (invitee_id) DO NOTHING""",
      (inviter["id"], u["id"], ("补录：" + evidence)[:120], devs[0] if devs else None))
    return done("added", evidence, inviter["id"])

# 一次性补录：2026-11-28 邀请码在注册后丢失（登录页没带 ref.js）的访客。
# 证据来自请求日志：同一客户端先打开带该邀请码的活动页、随后注册，注册后没有绑定请求。
# 日志只有时间没有账号，所以按「注册时间前后几秒内创建、且唯一」的账号补；对不上或不唯一就不补。
# 不补：同一 IPv6 /64 内多个账号（疑似同设备）、与已绑账号同 IP 的再次注册。
BACKFILL_JOBS = [
    ("gma-20261128", "t1", "3K6GZG", "2026-11-28T11:41:45.540+00:00"),
    ("gma-20261128", "t2", "3K6GZG", "2026-11-28T11:45:11.242+00:00"),
    ("gma-20261128", "t3", "TZCGEU", "2026-11-28T11:37:43.835+00:00"),
]
BACKFILL_EVIDENCE = "请求日志：先打开带邀请码的活动页再注册，注册后无绑定请求（邀请码丢失）"

def run_backfill_jobs():
    done = 0
    with db() as conn:
        for job, target, code, at in BACKFILL_JOBS:
            if q(conn, "SELECT 1 AS x FROM c_ref_backfill WHERE job_key=%s AND target=%s", (job, target), one=True):
                continue
            t = datetime.datetime.fromisoformat(at)
            rows = q(conn, "SELECT id FROM c_users WHERE created_at BETWEEN %s AND %s", (t - BACKFILL_WINDOW[0], t + BACKFILL_WINDOW[1]))
            if len(rows) != 1:
                _audit(conn, job, target, code, None, None, "skipped", "按注册时间找到 %d 个账号，不唯一/找不到，未补" % len(rows), "system")
                continue
            backfill_invite(conn, code, rows[0]["id"], BACKFILL_EVIDENCE, "system", job, target)
            done += 1
    return done

@router.post("/api/admin/referral/backfill")
async def api_admin_referral_backfill(req: Request):
    """管理员补录：填邀请码 + 被邀请人（会员编号 / 邮箱 / 手机）+ 依据。规则同自动绑定，留审计。"""
    csrf(req)
    d = await _json(req)
    with db() as conn:
        need_admin(conn, req)
        evidence = clean_text(d.get("evidence"), 100, "补录依据", 4)
        from promo import find_user_for_bind
        u = find_user_for_bind(conn, d.get("invitee"))
        out, detail = backfill_invite(conn, d.get("code"), u["id"], evidence, "admin")
    return {"ok": True, "outcome": out, "detail": detail}

@router.get("/api/admin/referral/backfill")
def api_admin_referral_backfill_list(req: Request):
    with db() as conn:
        need_admin(conn, req)
        rows = q(conn, """SELECT b.*, a.nickname AS inviter, a.member_no AS inviter_no, i.nickname AS invitee, i.member_no AS invitee_no
                          FROM c_ref_backfill b LEFT JOIN c_users a ON a.id=b.inviter_id LEFT JOIN c_users i ON i.id=b.invitee_id
                          ORDER BY b.id DESC LIMIT 200""")
    return {"items": [{"id": r["id"], "job": r["job_key"], "code": r["inviter_code"], "outcome": r["outcome"], "detail": r["detail"],
                       "actor": r["actor"], "time": _ts(r["created_at"]),
                       "inviter": ("#%s %s" % (r["inviter_no"] or r["inviter_id"], r["inviter"])) if r["inviter_id"] else "",
                       "invitee": ("#%s %s" % (r["invitee_no"] or r["invitee_id"], r["invitee"])) if r["invitee_id"] else ""} for r in rows]}

# ---------------- 后台：邀请奖励 / 意见反馈 ----------------
@router.get("/api/admin/referral")
def api_admin_referral(req: Request):
    with db() as conn:
        need_admin(conn, req)
        c = _cfg(conn)
        st = q(conn, """SELECT count(*) AS total, count(*) FILTER (WHERE status='valid') AS valid,
                               count(*) FILTER (WHERE status='frozen') AS frozen, count(*) FILTER (WHERE status='excluded') AS excluded
                        FROM c_ref_invites""", one=True)
        cp = q(conn, """SELECT count(*) AS issued, count(*) FILTER (WHERE status='used') AS used,
                               COALESCE(sum(amount_cents) FILTER (WHERE status<>'revoked'),0) AS cents
                        FROM c_coupons WHERE kind LIKE 'ref_%%'""", one=True)
        review = q(conn, """SELECT i.id,i.status,i.reason,i.flags,i.created_at,i.first_use_at,i.inviter_id,i.invitee_id,
                                   a.nickname AS inviter, b.nickname AS invitee, a.member_no AS inviter_no, b.member_no AS invitee_no
                            FROM c_ref_invites i JOIN c_users a ON a.id=i.inviter_id JOIN c_users b ON b.id=i.invitee_id
                            WHERE i.status IN ('frozen','excluded') OR i.flags<>'' ORDER BY (i.status='frozen') DESC, i.id DESC LIMIT 100""")
        top = q(conn, """SELECT i.inviter_id, u.nickname, u.member_no, count(*) FILTER (WHERE i.status='valid') AS valid, count(*) AS total,
                                (SELECT COALESCE(sum(amount_cents),0) FROM c_coupons c WHERE c.user_id=i.inviter_id AND c.kind LIKE 'ref_inviter_%%' AND c.status<>'revoked') AS cents
                         FROM c_ref_invites i JOIN c_users u ON u.id=i.inviter_id GROUP BY i.inviter_id,u.nickname,u.member_no
                         ORDER BY valid DESC, total DESC LIMIT 50""")
    return {"config": {"enabled": c["enabled"], "starts": _ts(c["starts_at"])[:10], "ends": _ts(c["ends_at"])[:10],
                       "total_cap": c["total_cap_cents"] // 100, "person_cap": c["person_cap_cents"] // 100, "active": _active(c)},
            "stats": {k: int(v) for k, v in st.items()}, "coupons": {"issued": int(cp["issued"]), "used": int(cp["used"]), "amount": int(cp["cents"]) // 100},
            "review": [{"id": r["id"], "status": r["status"], "reason": r["reason"], "flags": r["flags"], "time": _ts(r["created_at"]),
                        "used": bool(r["first_use_at"]), "inviter": "#%s %s" % (r["inviter_no"] or r["inviter_id"], r["inviter"]),
                        "invitee": "#%s %s" % (r["invitee_no"] or r["invitee_id"], r["invitee"])} for r in review],
            "top": [{"who": "#%s %s" % (r["member_no"] or r["inviter_id"], r["nickname"]), "valid": int(r["valid"]), "total": int(r["total"]),
                     "amount": int(r["cents"]) // 100} for r in top]}

@router.post("/api/admin/referral/config")
async def api_admin_referral_cfg(req: Request):
    csrf(req)
    d = await _json(req)
    with db() as conn:
        need_admin(conn, req)
        if "enabled" in d:
            q(conn, "UPDATE c_ref_config SET enabled=%s WHERE id=1", (bool(d["enabled"]),))
        if d.get("ends"):
            e = _date(d["ends"])
            q(conn, "UPDATE c_ref_config SET ends_at=%s WHERE id=1",
              (datetime.datetime.combine(e, datetime.time(23, 59, 59), KST),))
    return {"ok": True}

@router.post("/api/admin/referral/invite/{iid}")
async def api_admin_referral_invite(iid: int, req: Request):
    csrf(req)
    d = await _json(req)
    act = d.get("action")
    if act not in ("valid", "invalid"):
        raise HTTPException(400, "参数错误")
    with db() as conn:
        need_admin(conn, req)
        inv = q(conn, "SELECT * FROM c_ref_invites WHERE id=%s FOR UPDATE", (iid,), one=True)
        if not inv:
            raise HTTPException(404, "记录不存在")
        if act == "valid":
            if inv["first_use_at"] is None:
                q(conn, "UPDATE c_ref_invites SET status='pending', reason='人工放行，待首次使用', reviewed_at=now() WHERE id=%s", (iid,))
            else:
                q(conn, "UPDATE c_ref_invites SET status='valid', validated_at=COALESCE(validated_at,now()), reason='人工判为有效', reviewed_at=now() WHERE id=%s", (iid,))
                _reward(conn, inv)
        else:
            q(conn, "UPDATE c_ref_invites SET status='invalid', reason='人工判为无效', reviewed_at=now() WHERE id=%s", (iid,))
            # 追回：好友券（未使用）+ 邀请人超出档位的未使用券
            q(conn, "UPDATE c_coupons SET status='revoked' WHERE user_id=%s AND kind=%s AND status='available'", (inv["invitee_id"], INVITEE_KIND))
            n = q(conn, "SELECT count(*) AS n FROM c_ref_invites WHERE inviter_id=%s AND status='valid'", (inv["inviter_id"],), one=True)["n"]
            for need, kind, _c, _t in REF_TIERS:
                if n < need:
                    q(conn, "UPDATE c_coupons SET status='revoked' WHERE user_id=%s AND kind=%s AND status='available'", (inv["inviter_id"], kind))
    return {"ok": True}

@router.get("/api/admin/feedback")
def api_admin_feedback(req: Request):
    with db() as conn:
        need_admin(conn, req)
        rows = q(conn, """SELECT f.id,f.body,f.page,f.status,f.created_at,u.nickname,u.member_no FROM c_feedback f
                          LEFT JOIN c_users u ON u.id=f.user_id ORDER BY f.id DESC LIMIT 100""")
    return {"items": [{"id": r["id"], "body": r["body"], "page": r["page"], "status": r["status"], "time": _ts(r["created_at"]),
                       "who": ("#%s %s" % (r["member_no"], r["nickname"])) if r["nickname"] else "游客"} for r in rows]}

@router.post("/api/admin/feedback/{fid}")
async def api_admin_feedback_done(fid: int, req: Request):
    csrf(req)
    with db() as conn:
        need_admin(conn, req)
        q(conn, "UPDATE c_feedback SET status='done' WHERE id=%s", (fid,))
    return {"ok": True}
