"""限时把普通会员自动设为高级会员（c_users.tier=2）的可逆推广。

环境变量：
  PROMO_AUTO_PREMIUM   默认开启。设为 0/false/off/no 后，不再给新注册或补录升级；
                       已经升级的账号仍享剩余有效期，到期后回到原本档次。
  PROMO_PREMIUM_DAYS   每次升级的有效天数，默认 30，范围 1–365。

到期后只回到「本来就有的档次」（付费或管理员设置的会员/高级会员），不会降到更低。
不改邀请规则、同设备去重、注册限流和手机验证开关。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request

JOB_KEY = "promo-premium-backfill-30d"
BACKFILL_REG_DAYS = 30
DETAIL_GRANT = "推广期自动升级"
NOTICE_TMPL = "新人限时福利：近期注册会员限时享高级会员，到期日 %s，之后按正常会员制度"

MIGRATE_PREMIUM_PROMO = """
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS promo_premium_until TIMESTAMPTZ;
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS promo_revert_tier SMALLINT;
CREATE TABLE IF NOT EXISTS c_premium_promo_audit (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT REFERENCES c_users(id) ON DELETE SET NULL,
  action TEXT NOT NULL,
  from_tier SMALLINT,
  to_tier SMALLINT,
  revert_tier SMALLINT,
  until_at TIMESTAMPTZ,
  detail TEXT,
  actor TEXT,
  job_key TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_premium_promo_audit_user_idx ON c_premium_promo_audit(user_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS c_premium_promo_audit_job_grant_uidx
  ON c_premium_promo_audit(job_key, user_id) WHERE job_key IS NOT NULL AND action='grant';
"""

router = APIRouter()


def _env_flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or not str(v).strip():
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def promo_auto_premium() -> bool:
    return _env_flag("PROMO_AUTO_PREMIUM", True)


def promo_premium_days() -> int:
    raw = (os.environ.get("PROMO_PREMIUM_DAYS") or "").strip()
    try:
        n = int(raw) if raw else 30
    except ValueError:
        n = 30
    return max(1, min(365, n))


def _now():
    return datetime.now(timezone.utc)


def _kst():
    from community import KST
    return KST


def until_date_text(dt) -> str | None:
    if not dt:
        return None
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_kst()).date().isoformat()


def notice_text(until) -> str:
    d = until if isinstance(until, str) else until_date_text(until)
    return NOTICE_TMPL % (d or "")


def expected_until_if_register_now():
    return _now() + timedelta(days=promo_premium_days())


def public_state() -> dict:
    days = promo_premium_days()
    on = promo_auto_premium()
    until = expected_until_if_register_now()
    date = until_date_text(until)
    return {
        "enabled": on,
        "days": days,
        "until_date": date if on else None,
        "notice": notice_text(date) if on else None,
    }


def _audit(conn, *, user_id, action, from_tier=None, to_tier=None, revert_tier=None,
           until_at=None, detail="", actor="system", job_key=None):
    from community import q
    q(conn, """INSERT INTO c_premium_promo_audit
               (user_id,action,from_tier,to_tier,revert_tier,until_at,detail,actor,job_key)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
      (user_id, action, from_tier, to_tier, revert_tier, until_at, detail, actor, job_key))


def expire_due(conn, uid=None) -> list:
    """把已到期的推广档改回本来的会员档。有效商家会员先不动，商家到期后再回退。"""
    from community import q
    args = []
    extra = ""
    if uid is not None:
        extra = " AND id=%s"
        args.append(uid)
    rows = q(conn, f"""
        UPDATE c_users SET
          tier = GREATEST(COALESCE(promo_revert_tier, 0), 0),
          is_member = (GREATEST(COALESCE(promo_revert_tier, 0), 0) >= 1),
          member_since = CASE WHEN GREATEST(COALESCE(promo_revert_tier, 0), 0) >= 1
                              THEN member_since ELSE NULL END,
          tier2_since = CASE WHEN GREATEST(COALESCE(promo_revert_tier, 0), 0) >= 2
                             THEN tier2_since ELSE NULL END,
          promo_premium_until = NULL
        WHERE promo_premium_until IS NOT NULL
          AND promo_premium_until <= now()
          AND (merchant_until IS NULL OR merchant_until <= now())
          AND NOT COALESCE(is_bot, FALSE)
          {extra}
        RETURNING id, tier, promo_revert_tier
    """, tuple(args))
    out = rows or []
    for r in out:
        _audit(conn, user_id=r["id"], action="expire", from_tier=2, to_tier=r["tier"],
               revert_tier=r["promo_revert_tier"], detail="推广期到期自动回退", actor="system")
    return out


def grant_promo_premium(conn, uid, *, actor="system", job_key=None, days=None) -> dict:
    """给符合条件的普通用户限时高级会员。已是高级/商家/官方/机器人的不改，也不降级。"""
    from community import q
    expire_due(conn, uid)
    if days is None:
        days = promo_premium_days()
    until = _now() + timedelta(days=days)
    row = q(conn, """
        UPDATE c_users SET
          promo_revert_tier = COALESCE(tier, 0),
          promo_premium_until = %s,
          tier = 2,
          is_member = TRUE,
          member_since = COALESCE(member_since, now()),
          tier2_since = COALESCE(tier2_since, now())
        WHERE id=%s
          AND NOT COALESCE(is_bot, FALSE)
          AND NOT COALESCE(is_official, FALSE)
          AND NOT is_banned
          AND (merchant_until IS NULL OR merchant_until <= now())
          AND COALESCE(tier, 0) < 2
          AND (promo_premium_until IS NULL OR promo_premium_until <= now())
        RETURNING id, promo_revert_tier, promo_premium_until, tier
    """, (until, uid), one=True)
    if not row:
        u = q(conn, """SELECT id,tier,is_bot,is_banned,
                              COALESCE(is_official,FALSE) AS is_official,
                              merchant_until, promo_premium_until, promo_revert_tier
                       FROM c_users WHERE id=%s""", (uid,), one=True)
        reason = "not_found"
        if u:
            if u["is_bot"]:
                reason = "bot"
            elif u["is_official"]:
                reason = "official"
            elif u["is_banned"]:
                reason = "banned"
            elif u["merchant_until"] and u["merchant_until"] > _now():
                reason = "merchant"
            elif (u["tier"] or 0) >= 2:
                reason = "already_premium"
            elif u["promo_premium_until"] and u["promo_premium_until"] > _now():
                reason = "already_promo"
            else:
                reason = "ineligible"
        try:
            _audit(conn, user_id=uid, action="skip", from_tier=(u or {}).get("tier"),
                   detail=reason, actor=actor, job_key=job_key)
        except Exception:
            pass
        return {"ok": False, "reason": reason}
    _audit(conn, user_id=uid, action="grant", from_tier=row["promo_revert_tier"],
           to_tier=2, revert_tier=row["promo_revert_tier"], until_at=row["promo_premium_until"],
           detail=DETAIL_GRANT, actor=actor, job_key=job_key)
    return {"ok": True, "until": row["promo_premium_until"],
            "until_date": until_date_text(row["promo_premium_until"]),
            "revert_tier": row["promo_revert_tier"]}


def grant_on_register(conn, uid) -> dict | None:
    if not promo_auto_premium():
        return None
    return grant_promo_premium(conn, uid, actor="register", job_key="register")


def note_earned_tier(conn, uid, t: int):
    """付费或管理员调档：抬高到期回退档；若已付到高级会员及以上，结束推广倒计时（他们已正式拥有）。"""
    from community import q
    t = int(t)
    u = q(conn, """SELECT promo_premium_until, promo_revert_tier, tier FROM c_users WHERE id=%s""",
          (uid,), one=True)
    if not u or not u.get("promo_premium_until"):
        return
    new_revert = max(int(u["promo_revert_tier"] or 0), t)
    if t >= 2:
        q(conn, """UPDATE c_users SET promo_revert_tier=%s, promo_premium_until=NULL WHERE id=%s""",
          (new_revert, uid))
        _audit(conn, user_id=uid, action="earned", from_tier=2, to_tier=t,
               revert_tier=new_revert, detail="正式开通，结束推广倒计时", actor="set_tier")
    else:
        q(conn, """UPDATE c_users SET promo_revert_tier=%s WHERE id=%s""", (new_revert, uid))


def _as_dt(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    return None


def promo_fields(u) -> dict:
    """给「我的 / 会员」接口用的展示字段。到期日以 YYYY-MM-DD（韩国时间）返回。"""
    until = _as_dt(u.get("promo_until_dt")) or _as_dt(u.get("promo_premium_until"))
    active = bool(until and until > _now())
    date = until_date_text(until) if active else None
    return {
        "promo_premium": active,
        "promo_premium_until": date,
        "promo_notice": notice_text(date) if active else None,
    }


def apply_user_promo(conn, u):
    """在 current_user 里调用：到期则回退，并附上展示字段（不覆盖原始时间）。"""
    if not u:
        return u
    until = _as_dt(u.get("promo_premium_until"))
    if until and until <= _now():
        changed = expire_due(conn, u["id"])
        if changed:
            r = changed[0]
            u["tier"] = r["tier"]
            u["promo_premium_until"] = None
            u["promo_revert_tier"] = r["promo_revert_tier"]
            until = None
    u["promo_until_dt"] = until
    fields = promo_fields(u)
    u["promo_premium"] = fields["promo_premium"]
    u["promo_notice"] = fields["promo_notice"]
    u["promo_until_date"] = fields["promo_premium_until"]
    return u


def run_backfill(conn) -> dict:
    """一次性：把近 30 天注册的普通账号升级为限时高级会员。幂等，可重复执行。"""
    from community import q
    expire_due(conn)
    granted = skipped = 0
    if not promo_auto_premium():
        return {"granted": 0, "skipped": 0, "disabled": True}
    rows = q(conn, """
        SELECT id FROM c_users
        WHERE created_at >= now() - make_interval(days => %s)
          AND NOT COALESCE(is_bot, FALSE)
          AND NOT COALESCE(is_official, FALSE)
          AND NOT is_banned
          AND (merchant_until IS NULL OR merchant_until <= now())
          AND COALESCE(tier, 0) < 2
          AND (promo_premium_until IS NULL OR promo_premium_until <= now())
        ORDER BY id
    """, (BACKFILL_REG_DAYS,))
    for r in rows or []:
        already = q(conn, """SELECT 1 AS x FROM c_premium_promo_audit
                             WHERE job_key=%s AND user_id=%s AND action='grant'""",
                    (JOB_KEY, r["id"]), one=True)
        if already:
            skipped += 1
            continue
        out = grant_promo_premium(conn, r["id"], actor="system", job_key=JOB_KEY)
        if out.get("ok"):
            granted += 1
        else:
            skipped += 1
    return {"granted": granted, "skipped": skipped, "disabled": False}


def run_startup():
    from community import db, _db_url
    if not _db_url():
        return
    with db() as conn:
        expired = expire_due(conn)
        result = run_backfill(conn)
    print("premium promo startup: expired=%d granted=%d skipped=%d disabled=%s" % (
        len(expired), result.get("granted", 0), result.get("skipped", 0),
        result.get("disabled", False)), flush=True)
    return result


@router.get("/api/premium-promo")
def api_public():
    return public_state()
