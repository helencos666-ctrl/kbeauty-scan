"""手机号绑定 + 短信验证码（PHONE_OTP_REQUIRED，默认关闭）。

关闭时：本模块的接口全部 404，注册/登录/「我的」返回内容与以前完全一样，不会拦任何人。
打开后：
  * 新注册必须填手机号 + 短信验证码（支持 +86 / +82）；
  * 老账号登录后软提示绑定；若设置了 PHONE_OTP_ENFORCE_FROM（日期），到期后未绑定的账号只在「写操作」上被拦，
    仍能登录、浏览、绑定手机号、改密码、注销账号，不会被锁在门外；
  * 一个手机号只能对应一个账号（数据库 UNIQUE + 事务内加锁）。

验证码：6 位、5 分钟有效、哈希保存、最多错 5 次、60 秒重发冷却、每号每天/每 IP 每小时每天上限、发送前必须过图形验证码。
手机号统一存为 E.164（+8613812345678 / +821012345678），对外只显示打码。

环境变量（都可选，只在 Render 环境里设置）：
  PHONE_OTP_REQUIRED=1            总开关
  PHONE_OTP_ENFORCE_FROM=YYYY-MM-DD   老账号从这天起必须绑定（首尔时间 00:00）。不设=永远只是软提示
  PHONE_OTP_PEPPER=随机长字符串      验证码哈希的服务端盐（不设也能用，每条验证码另有随机盐）
  PHONE_OTP_CAPTCHA=simple|turnstile|off   发送前人机校验（默认 simple 图形验证码）
  TURNSTILE_SITE_KEY / TURNSTILE_SECRET_KEY  选 turnstile 时需要
  PHONE_OTP_PHONE_DAILY=5  PHONE_OTP_IP_HOURLY=8  PHONE_OTP_IP_DAILY=20  PHONE_OTP_GLOBAL_DAILY=1000
  WECHAT_MP_APPID / WECHAT_MP_SECRET  小程序 getPhoneNumber 绑定（只在 Render 环境变量里设置）
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import io
import os
import random
import re
import secrets
import threading
from typing import Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, Response

import sms_providers
from sms_providers import SmsError, SmsMessage

router = APIRouter()

OTP_LEN = 6
OTP_TTL_SEC = 300
OTP_MAX_TRIES = 5
RESEND_COOLDOWN_SEC = 60
COUNTRIES = {"86": "中国大陆 +86", "82": "韩国 +82"}
KST = datetime.timezone(datetime.timedelta(hours=9))

MIGRATE_PHONE_OTP = """
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS phone_verified_at TIMESTAMPTZ;
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS phone_verify_method TEXT;
CREATE TABLE IF NOT EXISTS c_phone_otp (
  id BIGSERIAL PRIMARY KEY,
  phone TEXT NOT NULL,
  purpose TEXT NOT NULL,
  user_id BIGINT,
  salt TEXT NOT NULL,
  code_hash TEXT NOT NULL,
  ip_hash TEXT,
  provider TEXT,
  tries INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  consumed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS c_phone_otp_phone_idx ON c_phone_otp(phone, created_at DESC);
CREATE INDEX IF NOT EXISTS c_phone_otp_ip_idx ON c_phone_otp(ip_hash, created_at DESC);
CREATE INDEX IF NOT EXISTS c_phone_otp_created_idx ON c_phone_otp(created_at);
CREATE TABLE IF NOT EXISTS c_phone_captcha (
  id TEXT PRIMARY KEY,
  answer_hash TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


# ---------------- 配置 ----------------
def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(_env(name, str(default))))
    except ValueError:
        return default


def enabled() -> bool:
    return _env("PHONE_OTP_REQUIRED").lower() in ("1", "true", "yes", "on")


def enforce_from() -> Optional[datetime.datetime]:
    s = _env("PHONE_OTP_ENFORCE_FROM")
    if not s:
        return None
    try:
        d = datetime.date.fromisoformat(s[:10])
    except ValueError:
        return None
    return datetime.datetime(d.year, d.month, d.day, tzinfo=KST)


def enforce_active(now: Optional[datetime.datetime] = None) -> bool:
    """对老账号是否已经「必须绑定」。未设置日期=永远只是软提示。"""
    if not enabled():
        return False
    ef = enforce_from()
    if not ef:
        return False
    return (now or datetime.datetime.now(datetime.timezone.utc)) >= ef


def public_config() -> dict:
    ef = enforce_from()
    return {"enabled": enabled(), "countries": [{"code": k, "name": v} for k, v in COUNTRIES.items()],
            "enforce_from": ef.date().isoformat() if ef else None, "enforce_active": enforce_active(),
            "code_length": OTP_LEN, "cooldown": RESEND_COOLDOWN_SEC, "ttl": OTP_TTL_SEC,
            "captcha": captcha_client_config(), "wechat": bool(_env("WECHAT_MP_APPID") and _env("WECHAT_MP_SECRET"))}


def _C():
    import community as C   # 延迟导入，避免循环依赖
    return C


def need_enabled():
    if not enabled():
        raise HTTPException(404, "页面不存在")


# ---------------- 手机号规范化 / 打码 ----------------
def normalize_phone(country: str, national: str):
    """返回 (country, national_digits, e164)。支持传入国家码 + 本地号，或直接传 +国家码开头的完整号码。"""
    country = re.sub(r"\D", "", str(country or ""))
    raw = str(national or "").strip()
    digits = re.sub(r"[\s\-().]", "", raw)
    if digits.startswith("+") or digits.startswith("00"):
        full = re.sub(r"\D", "", digits[1:] if digits.startswith("+") else digits[2:])
        for cc in COUNTRIES:
            if full.startswith(cc):
                country, digits = cc, full[len(cc):]
                break
        else:
            raise HTTPException(400, "暂只支持中国 +86 和韩国 +82 的手机号")
    if country not in COUNTRIES:
        raise HTTPException(400, "请选择国家/地区（中国 +86 或韩国 +82）")
    if not digits.isdigit():
        raise HTTPException(400, "请输入手机号")
    if country == "86":
        if digits.startswith("86") and len(digits) == 13:
            digits = digits[2:]
        if not re.fullmatch(r"1[3-9]\d{9}", digits):
            raise HTTPException(400, "中国手机号应为 11 位（1 开头）")
    else:
        if digits.startswith("82") and len(digits) in (11, 12):
            digits = digits[2:]
        if digits.startswith("0"):
            digits = digits[1:]
        if not re.fullmatch(r"1[016789]\d{7,8}", digits):
            raise HTTPException(400, "韩国手机号应为 010 开头的 10–11 位号码")
    return country, digits, "+" + country + digits


def mask_phone(e164: Optional[str]) -> str:
    if not e164:
        return ""
    if e164.startswith("+86") and len(e164) == 14:
        return "+86 " + e164[3:6] + "****" + e164[-4:]
    if e164.startswith("+82"):
        return "+82 " + e164[3:5] + "-****-" + e164[-4:]
    return e164[:4] + "****" + e164[-3:]


# ---------------- 验证码哈希 ----------------
def _hash_code(salt: str, phone: str, code: str) -> str:
    pepper = _env("PHONE_OTP_PEPPER")
    msg = ("%s|%s|%s" % (salt, phone, code)).encode()
    if pepper:
        return hmac.new(pepper.encode(), msg, hashlib.sha256).hexdigest()
    return hashlib.sha256(msg).hexdigest()


def _ip_hash(ip: str) -> str:
    return hashlib.sha256(("lvhan-otp-ip|" + (ip or "")).encode()).hexdigest()[:32]


def _lock(conn, key: str):
    C = _C()
    C.q(conn, "SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))


# ---------------- 人机校验（图形验证码 / Turnstile / 自定义滑块钩子） ----------------
_CUSTOM_CAPTCHA: Dict[str, dict] = {}


def register_captcha_provider(name: str, verify: Callable[[Request, dict], bool], client_config: Optional[dict] = None):
    """钩子：接入阿里云验证码 2.0 / 腾讯云天御等滑块时，注册 verify(req, body)->bool，并把 PHONE_OTP_CAPTCHA 设为该名字。"""
    _CUSTOM_CAPTCHA[name] = {"verify": verify, "config": client_config or {}}


def captcha_mode() -> str:
    return _env("PHONE_OTP_CAPTCHA", "simple").lower()


def captcha_client_config() -> dict:
    m = captcha_mode()
    if m == "turnstile":
        return {"type": "turnstile", "site_key": _env("TURNSTILE_SITE_KEY")}
    if m in _CUSTOM_CAPTCHA:
        return dict(_CUSTOM_CAPTCHA[m]["config"], type=m)
    if m == "off":
        return {"type": "off"}
    return {"type": "simple"}


def make_captcha_image(text: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    W, H = 132, 46
    im = Image.new("RGB", (W, H), (244, 248, 247))
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.load_default(size=30)
    except TypeError:
        font = ImageFont.load_default()
    rnd = random.SystemRandom()
    for _ in range(5):
        d.line([(rnd.randint(0, W), rnd.randint(0, H)), (rnd.randint(0, W), rnd.randint(0, H))],
               fill=(rnd.randint(120, 200), rnd.randint(120, 200), rnd.randint(120, 200)), width=1)
    for i, ch in enumerate(text):
        tile = Image.new("RGBA", (34, 44), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((6, 4), ch, font=font, fill=(rnd.randint(10, 90), rnd.randint(60, 120), rnd.randint(60, 110), 255))
        tile = tile.rotate(rnd.randint(-22, 22), expand=False, resample=Image.BICUBIC)
        im.paste(tile, (8 + i * 28 + rnd.randint(-2, 2), rnd.randint(-1, 3)), tile)
    for _ in range(60):
        d.point((rnd.randint(0, W - 1), rnd.randint(0, H - 1)), fill=(rnd.randint(80, 200),) * 3)
    im = im.filter(ImageFilter.SMOOTH)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


@router.get("/api/community/phone/config")
def api_phone_config():
    return public_config()


@router.get("/api/community/phone/captcha")
def api_phone_captcha(req: Request):
    need_enabled()
    C = _C()
    if captcha_mode() != "simple":
        raise HTTPException(404, "页面不存在")
    C.limit("pcap:" + C.ip_of(req), 30, 3600, "刷新验证码太频繁，请稍后再试")
    text = "".join(random.SystemRandom().choice("23456789") for _ in range(4))
    cid = secrets.token_urlsafe(12)
    with C.db() as conn:
        C.q(conn, "DELETE FROM c_phone_captcha WHERE expires_at < now()")
        C.q(conn, "INSERT INTO c_phone_captcha(id,answer_hash,expires_at) VALUES (%s,%s,now()+interval '5 minutes')",
            (cid, hashlib.sha256(("cap|" + cid + "|" + text).encode()).hexdigest()))
    png = make_captcha_image(text)
    return {"id": cid, "image": "data:image/png;base64," + base64.b64encode(png).decode()}


def verify_captcha(conn, req: Request, d: dict):
    """发送验证码前必须调用。图形验证码一次性：答错也作废，防止暴力猜。"""
    m = captcha_mode()
    if m == "off":
        return
    if m == "simple":
        cid = str(d.get("captcha_id") or "")[:40]
        ans = str(d.get("captcha_answer") or "").strip()[:8]
        row = _C().q(conn, "DELETE FROM c_phone_captcha WHERE id=%s AND expires_at>now() RETURNING answer_hash", (cid,), one=True)
        if not row or not ans or not hmac.compare_digest(row["answer_hash"], hashlib.sha256(("cap|" + cid + "|" + ans).encode()).hexdigest()):
            conn.commit()
            raise HTTPException(400, "图形验证码不对，请看清后重试")
        return
    if m == "turnstile":
        import httpx
        tok = str(d.get("captcha_token") or "")[:2048]
        secret = _env("TURNSTILE_SECRET_KEY")
        if not tok or not secret:
            raise HTTPException(400, "请先完成人机验证")
        try:
            r = httpx.post("https://challenges.cloudflare.com/turnstile/v0/siteverify",
                           data={"secret": secret, "response": tok, "remoteip": _C().ip_of(req)}, timeout=6)
            ok = bool(r.json().get("success"))
        except Exception:
            raise HTTPException(503, "人机验证服务暂时不可用，请稍后再试")
        if not ok:
            raise HTTPException(400, "人机验证未通过，请重试")
        return
    if m in _CUSTOM_CAPTCHA:
        if not _CUSTOM_CAPTCHA[m]["verify"](req, d):
            raise HTTPException(400, "人机验证未通过，请重试")
        return
    raise HTTPException(503, "人机验证未配置，暂时无法发送验证码")


# ---------------- 发送 ----------------
def _user_from_bearer_or_cookie(conn, req: Request):
    """小程序请求不带 Cookie：先试 Cookie，再试 Authorization: Bearer <会话令牌>（与网站同一张 c_sessions 表）。"""
    C = _C()
    u = C.current_user(conn, req)
    if u:
        return u
    auth = req.headers.get("authorization") or ""
    if auth.lower().startswith("bearer ") and len(auth) < 300:
        tok = auth[7:].strip()
        return C.q(conn, """SELECT u.id,u.nickname,u.tier,u.is_banned,u.phone,u.phone_verified_at
                            FROM c_sessions s JOIN c_users u ON u.id=s.user_id
                            WHERE s.token_hash=%s AND NOT s.is_admin AND s.expires_at>now() AND NOT u.is_banned""",
                   (C._th(tok),), one=True)
    return None


def _need_user_any(conn, req: Request):
    u = _user_from_bearer_or_cookie(conn, req)
    if not u:
        raise HTTPException(401, "请先登录")
    return u


def _holder(conn, e164: str):
    return _C().q(conn, "SELECT id, phone_verified_at FROM c_users WHERE phone=%s", (e164,), one=True)


def _check_purpose(conn, req: Request, d: dict, purpose: str, e164: str):
    """返回 user（bind 时）或 None（register 时），并检查「一号一账号」。"""
    C = _C()
    holder = _holder(conn, e164)
    if purpose == "register":
        if holder:
            raise HTTPException(409, "这个手机号已注册，请直接登录")
        return None
    if purpose == "bind":
        u = _need_user_any(conn, req)       # 网站用 Cookie，小程序用 Authorization: Bearer
        cur = C.q(conn, "SELECT phone, phone_verified_at FROM c_users WHERE id=%s", (u["id"],), one=True)
        if cur["phone_verified_at"]:
            raise HTTPException(400, "你已经绑定了手机号 " + mask_phone(cur["phone"]) + "，如需更换请联系客服")
        if holder and holder["id"] != u["id"]:
            raise HTTPException(409, "这个手机号已被另一个账号使用。如果是你本人的号码，请用它登录原账号；不是你注册的请联系客服")
        return u
    raise HTTPException(400, "请求无效")


@router.post("/api/community/phone/send")
async def api_phone_send(req: Request):
    need_enabled()
    C = _C()
    C.csrf(req)
    d = await C._json(req)
    purpose = str(d.get("purpose") or "")
    country, national, e164 = normalize_phone(d.get("country"), d.get("phone"))
    ip = C.ip_of(req)
    C.limit("psend-min:" + ip, 6, 60, "操作太快了，请稍后再试")
    cap = int(_int_env("PHONE_OTP_PHONE_DAILY", 5))
    with C.db() as conn:
        _lock(conn, "otp:" + e164)
        verify_captcha(conn, req, d)
        conn.commit()
        _lock(conn, "otp:" + e164)
        user = _check_purpose(conn, req, d, purpose, e164)
        # ---- 冷却 / 额度（全部以数据库为准，多实例/重启都不会清零）----
        last = C.q(conn, "SELECT extract(epoch FROM (now()-max(created_at))) AS ago FROM c_phone_otp WHERE phone=%s", (e164,), one=True)
        if last and last["ago"] is not None and last["ago"] < RESEND_COOLDOWN_SEC:
            wait = int(RESEND_COOLDOWN_SEC - last["ago"]) + 1
            raise HTTPException(429, "发送太频繁，请 %d 秒后再试" % wait, headers={"Retry-After": str(wait)})
        n = C.q(conn, "SELECT count(*) AS n FROM c_phone_otp WHERE phone=%s AND created_at>now()-interval '24 hours'", (e164,), one=True)["n"]
        if n >= cap:
            raise HTTPException(429, "这个手机号今天获取验证码次数已达上限，请明天再试")
        ih = _ip_hash(ip)
        n = C.q(conn, "SELECT count(*) AS n FROM c_phone_otp WHERE ip_hash=%s AND created_at>now()-interval '1 hour'", (ih,), one=True)["n"]
        if n >= _int_env("PHONE_OTP_IP_HOURLY", 8):
            raise HTTPException(429, "你的网络今天获取验证码太频繁，请稍后再试")
        n = C.q(conn, "SELECT count(*) AS n FROM c_phone_otp WHERE ip_hash=%s AND created_at>now()-interval '24 hours'", (ih,), one=True)["n"]
        if n >= _int_env("PHONE_OTP_IP_DAILY", 20):
            raise HTTPException(429, "你的网络今天获取验证码次数已达上限，请明天再试")
        n = C.q(conn, "SELECT count(*) AS n FROM c_phone_otp WHERE created_at>now()-interval '24 hours'", (), one=True)["n"]
        if n >= _int_env("PHONE_OTP_GLOBAL_DAILY", 1000):
            raise HTTPException(503, "今天的短信额度已用完，请明天再试")
        # ---- 生成并保存（只存哈希）----
        code = "".join(str(secrets.randbelow(10)) for _ in range(OTP_LEN))
        salt = secrets.token_hex(8)
        C.q(conn, "UPDATE c_phone_otp SET consumed_at=now() WHERE phone=%s AND consumed_at IS NULL", (e164,))   # 旧码作废
        provider = sms_providers.get_provider(country)
        row = C.q(conn, """INSERT INTO c_phone_otp(phone,purpose,user_id,salt,code_hash,ip_hash,provider,expires_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s, now()+make_interval(secs => %s)) RETURNING id""",
                  (e164, purpose, user["id"] if user else None, salt, _hash_code(salt, e164, code), ih, provider.name, OTP_TTL_SEC), one=True)
        conn.commit()
        try:
            provider.send_otp(SmsMessage(to_e164=e164, country=country, national=national, code=code, ttl_minutes=OTP_TTL_SEC // 60))
        except SmsError as e:
            print("sms send failed provider=%s phone=%s err=%s" % (provider.name, sms_providers.mask_for_log(e164), str(e)[:120]), flush=True)
            C.q(conn, "DELETE FROM c_phone_otp WHERE id=%s", (row["id"],))     # 发送失败不占用用户额度
            conn.commit()
            raise HTTPException(503, "短信暂时发不出去，请稍后再试")
        except Exception as e:
            print("sms send crashed provider=%s type=%s" % (provider.name, type(e).__name__), flush=True)
            C.q(conn, "DELETE FROM c_phone_otp WHERE id=%s", (row["id"],))
            conn.commit()
            raise HTTPException(503, "短信暂时发不出去，请稍后再试")
    return {"ok": True, "masked": mask_phone(e164), "cooldown": RESEND_COOLDOWN_SEC, "expires_in": OTP_TTL_SEC}


# ---------------- 校验 ----------------
def consume_otp(conn, e164: str, purpose: str, code: str, user_id: Optional[int] = None):
    """校验并消耗验证码。成功返回；失败抛 HTTPException（错误次数会先提交，不会被回滚吃掉）。"""
    C = _C()
    code = re.sub(r"\D", "", str(code or ""))
    if len(code) != OTP_LEN:
        raise HTTPException(400, "请输入 %d 位数字验证码" % OTP_LEN)
    _lock(conn, "otp:" + e164)
    row = C.q(conn, """SELECT id,salt,code_hash,tries,user_id FROM c_phone_otp
                       WHERE phone=%s AND purpose=%s AND consumed_at IS NULL AND expires_at>now()
                       ORDER BY id DESC LIMIT 1""", (e164, purpose), one=True)
    if not row:
        raise HTTPException(400, "验证码已过期或还没发送，请重新获取")
    if purpose == "bind" and row["user_id"] != user_id:
        raise HTTPException(400, "验证码已过期或还没发送，请重新获取")
    if row["tries"] >= OTP_MAX_TRIES:
        C.q(conn, "UPDATE c_phone_otp SET consumed_at=now() WHERE id=%s", (row["id"],))
        conn.commit()
        raise HTTPException(429, "验证码错误次数太多，请重新获取")
    if not hmac.compare_digest(row["code_hash"], _hash_code(row["salt"], e164, code)):
        tries = row["tries"] + 1
        C.q(conn, "UPDATE c_phone_otp SET tries=%s, consumed_at=CASE WHEN %s>=%s THEN now() ELSE NULL END WHERE id=%s",
            (tries, tries, OTP_MAX_TRIES, row["id"]))
        conn.commit()
        left = OTP_MAX_TRIES - tries
        raise HTTPException(400, "验证码不对，还可以再试 %d 次" % left if left > 0 else "验证码错误次数太多，请重新获取")
    C.q(conn, "UPDATE c_phone_otp SET consumed_at=now() WHERE id=%s", (row["id"],))


def _unique_violation(e: Exception) -> bool:
    return type(e).__name__ == "UniqueViolation" or getattr(e, "sqlstate", "") == "23505"


def bind_verified_phone(conn, user_id: int, e164: str, method: str):
    """把已验证的手机号写进账号。唯一约束兜底「一号一账号」。"""
    C = _C()
    try:
        C.q(conn, "UPDATE c_users SET phone=%s, phone_verified_at=now(), phone_verify_method=%s WHERE id=%s",
            (e164, method, user_id))
    except Exception as e:
        if _unique_violation(e):
            conn.rollback()
            raise HTTPException(409, "这个手机号已被另一个账号使用")
        raise


@router.post("/api/community/phone/bind")
async def api_phone_bind(req: Request):
    need_enabled()
    C = _C()
    C.csrf(req)
    d = await C._json(req)
    country, national, e164 = normalize_phone(d.get("country"), d.get("phone"))
    with C.db() as conn:
        u = _need_user_any(conn, req)
        C.limit("pbind:" + str(u["id"]), 20, 3600, "尝试太多，请稍后再试")
        cur = C.q(conn, "SELECT phone, phone_verified_at FROM c_users WHERE id=%s", (u["id"],), one=True)
        if cur["phone_verified_at"]:
            raise HTTPException(400, "你已经绑定了手机号")
        holder = _holder(conn, e164)
        if holder and holder["id"] != u["id"]:
            raise HTTPException(409, "这个手机号已被另一个账号使用。如果是你本人的号码，请用它登录原账号；不是你注册的请联系客服")
        consume_otp(conn, e164, "bind", d.get("code"), u["id"])
        bind_verified_phone(conn, u["id"], e164, "sms")
    return {"ok": True, "phone": mask_phone(e164)}


# ---------------- 状态（给「我的」/登录用） ----------------
def user_phone_status(u: dict) -> dict:
    """u 来自 community.current_user（含 phone, phone_verified_at）。"""
    verified = bool(u.get("phone_verified_at"))
    ef = enforce_from()
    days_left = None
    if ef and not verified:
        days_left = max(0, (ef - datetime.datetime.now(datetime.timezone.utc)).days)
    return {"verified": verified, "masked": mask_phone(u.get("phone")) if verified else "",
            "required_now": (not verified) and enforce_active(),
            "enforce_from": ef.date().isoformat() if ef else None, "days_left": days_left}


_WRITE_ALLOW = ("/api/community/phone/", "/api/mp/phone/", "/api/community/logout", "/api/community/password",
                "/api/community/account/delete", "/api/community/me")


def check_enforcement(u: dict, req) -> None:
    """need_user 里调用：到期后，未验证手机号的账号不能做「写操作」。读、登录、绑定、改密码、注销不受影响。"""
    if not enabled() or u is None or u.get("phone_verified_at"):
        return
    if not enforce_active():
        return
    try:
        method = req.method.upper()
        path = req.url.path
    except Exception:
        return
    if method in ("GET", "HEAD", "OPTIONS"):
        return
    if any(path.startswith(p) for p in _WRITE_ALLOW):
        return
    raise HTTPException(403, "请先绑定手机号再操作（我的 → 绑定手机号，30 秒搞定）")


def register_fields(d: dict):
    """开关打开时的注册：返回 (kind, acct, e164, country, national)。account 可省略（用手机号当账号）或填邮箱。"""
    country, national, e164 = normalize_phone(d.get("country"), d.get("phone"))
    C = _C()
    raw = str(d.get("account") or "").strip()
    if raw and "@" in raw:
        kind, acct = C.parse_account(raw)
    else:
        kind, acct = "phone", e164
        if raw:
            k2, a2 = C.parse_account(raw)
            if k2 != "phone" or a2 != e164:
                raise HTTPException(400, "账号手机号和验证的手机号不一致")
    return kind, acct, e164


# ---------------- 微信小程序 getPhoneNumber（备选通道） ----------------
_wx_token_cache = {"tok": None, "exp": 0.0}
_wx_lock = threading.Lock()


def _wx_access_token() -> str:
    import time
    import httpx
    appid, secret = _env("WECHAT_MP_APPID"), _env("WECHAT_MP_SECRET")
    if not appid or not secret:
        raise HTTPException(503, "微信快捷绑定暂未开通")
    with _wx_lock:
        if _wx_token_cache["tok"] and _wx_token_cache["exp"] > time.time() + 60:
            return _wx_token_cache["tok"]
        r = httpx.get("https://api.weixin.qq.com/cgi-bin/token",
                      params={"grant_type": "client_credential", "appid": appid, "secret": secret}, timeout=8).json()
        if not r.get("access_token"):
            print("wx token error errcode=%s" % r.get("errcode"), flush=True)
            raise HTTPException(503, "微信快捷绑定暂时不可用，请改用短信验证码")
        _wx_token_cache.update(tok=r["access_token"], exp=time.time() + int(r.get("expires_in", 7000)))
        return r["access_token"]


def wx_get_phone(code: str) -> dict:
    """用 getPhoneNumber 返回的 code 换手机号。可在测试里替换。返回 {"country":"86","phone":"138...."}。"""
    import httpx
    tok = _wx_access_token()
    r = httpx.post("https://api.weixin.qq.com/wxa/business/getuserphonenumber", params={"access_token": tok},
                   json={"code": code}, timeout=8).json()
    if r.get("errcode") not in (0, None):
        print("wx getuserphonenumber errcode=%s" % r.get("errcode"), flush=True)
        raise HTTPException(400, "微信没有返回手机号，请重试或改用短信验证码")
    info = r.get("phone_info") or {}
    if (info.get("watermark") or {}).get("appid") != _env("WECHAT_MP_APPID"):
        raise HTTPException(400, "微信返回的数据不属于本小程序")
    return {"country": str(info.get("countryCode") or "86"), "phone": str(info.get("purePhoneNumber") or "")}


@router.post("/api/mp/phone/wx-bind")
async def api_mp_wx_bind(req: Request):
    """小程序「微信一键绑定手机号」：前端把 getPhoneNumber 事件里的 code 传来，后端换号并绑定。
    需要：已认证的非个人主体小程序 + WECHAT_MP_APPID/WECHAT_MP_SECRET；每次成功调用微信收 0.03 元。"""
    need_enabled()
    C = _C()
    C.csrf(req)
    d = await C._json(req)
    code = str(d.get("code") or "")[:200]
    if not code:
        raise HTTPException(400, "缺少授权码")
    with C.db() as conn:
        u = _user_from_bearer_or_cookie(conn, req)
        if not u:
            raise HTTPException(401, "请先登录")
        C.limit("pwx:" + str(u["id"]), 10, 3600, "尝试太多，请稍后再试")
        C.limit("pwx-ip:" + C.ip_of(req), 30, 3600, "尝试太多，请稍后再试")
        cur = C.q(conn, "SELECT phone_verified_at FROM c_users WHERE id=%s", (u["id"],), one=True)
        if cur["phone_verified_at"]:
            raise HTTPException(400, "你已经绑定了手机号")
        got = wx_get_phone(code)
        country, national, e164 = normalize_phone(got["country"], got["phone"])
        holder = _holder(conn, e164)
        if holder and holder["id"] != u["id"]:
            raise HTTPException(409, "这个手机号已被另一个账号使用。如果是你本人的号码，请用它登录原账号；不是你注册的请联系客服")
        bind_verified_phone(conn, u["id"], e164, "wechat")
    return {"ok": True, "phone": mask_phone(e164)}
