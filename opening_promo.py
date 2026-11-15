"""开业活动：可开关与起止日期、签到双倍、前 100 名注册 +50 旅行豆（原子名额）、地铁攻略卡。"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Request, Response

from community import KST, csrf, db, need_admin, need_user, q, _json, _ts

router = APIRouter()

CAP = 100
BONUS_POINTS = 50
CHECKIN_BASE = 10
CHECKIN_MULT = 2
SUBWAY_KIND = "subway_guide_card"
SUBWAY_TITLE = "地铁攻略卡"
SUBWAY_HREF = "/?tab=sub"
BONUS_KIND = "opening_bonus"

MIGRATE_OPENING_PROMO = """
CREATE TABLE IF NOT EXISTS c_opening_settings (
  id SMALLINT PRIMARY KEY CHECK (id = 1),
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  starts_at TIMESTAMPTZ,
  ends_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO c_opening_settings(id, enabled) VALUES (1, FALSE) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS c_opening_counter (
  id SMALLINT PRIMARY KEY CHECK (id = 1),
  n INT NOT NULL DEFAULT 0
);
INSERT INTO c_opening_counter(id, n) VALUES (1, 0) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS c_opening_bonus (
  rank INT PRIMARY KEY CHECK (rank >= 1 AND rank <= 100),
  user_id BIGINT NOT NULL UNIQUE REFERENCES c_users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS c_opening_bonus_user_uidx ON c_opening_bonus(user_id);

CREATE UNIQUE INDEX IF NOT EXISTS c_coupons_subway_uidx
  ON c_coupons(user_id) WHERE kind = 'subway_guide_card';
CREATE UNIQUE INDEX IF NOT EXISTS c_points_opening_bonus_uidx
  ON c_points_ledger(user_id) WHERE kind = 'opening_bonus';
"""


def _now():
    return datetime.now(timezone.utc)


def _settings(conn):
    r = q(conn, "SELECT * FROM c_opening_settings WHERE id=1", one=True)
    if not r:
        q(conn, "INSERT INTO c_opening_settings(id,enabled) VALUES (1,FALSE) ON CONFLICT (id) DO NOTHING")
        r = q(conn, "SELECT * FROM c_opening_settings WHERE id=1", one=True)
    return r


def is_active(conn, at=None) -> bool:
    s = _settings(conn)
    if not s or not s["enabled"]:
        return False
    t = at or _now()
    if s["starts_at"] and t < s["starts_at"]:
        return False
    if s["ends_at"] and t > s["ends_at"]:
        return False
    return True


def taken(conn) -> int:
    r = q(conn, "SELECT n FROM c_opening_counter WHERE id=1", one=True)
    return int(r["n"] if r else 0)


def remaining(conn) -> int:
    return max(0, CAP - taken(conn))


def public_state(conn):
    s = _settings(conn)
    active = is_active(conn)
    left = remaining(conn) if active else (remaining(conn) if s and s["enabled"] else CAP)
    if not active:
        left = remaining(conn)
    return {
        "enabled": bool(s and s["enabled"]),
        "active": active,
        "starts_at": _ts(s["starts_at"]) if s else None,
        "ends_at": _ts(s["ends_at"]) if s else None,
        "cap": CAP,
        "remaining": remaining(conn) if (s and s["enabled"]) else CAP,
        "bonus_points": BONUS_POINTS,
        "checkin_points": (CHECKIN_BASE * CHECKIN_MULT) if active else CHECKIN_BASE,
        "checkin_base": CHECKIN_BASE,
        "badge": "开业双倍" if active else None,
        "rules": "活动期间每日签到可获 %d 旅行豆（开业双倍）。活动期内前 %d 名新注册用户额外获得 %d 旅行豆（名额有限，以系统记录为准，封禁或重复账号不计入）。每位新用户获赠「地铁攻略卡」，可打开首尔地铁攻略。" % (
            CHECKIN_BASE * CHECKIN_MULT, CAP, BONUS_POINTS),
    }


def checkin_points(conn) -> tuple:
    """返回 (points, badge_or_none)。"""
    if is_active(conn):
        return CHECKIN_BASE * CHECKIN_MULT, "开业双倍"
    return CHECKIN_BASE, None


def issue_subway_card(conn, uid):
    exp = datetime(2099, 12, 31, tzinfo=timezone.utc)
    q(conn, """INSERT INTO c_coupons(user_id,kind,title,amount_cents,status,scope_note,staff_note,expires_at)
               VALUES (%s,%s,%s,0,'available',%s,%s,%s)
               ON CONFLICT DO NOTHING""",
      (uid, SUBWAY_KIND, SUBWAY_TITLE, "打开首尔地铁攻略（首页「地铁」工具，内容对所有人免费）",
       "这是开业赠送的攻略卡，可在卡券页点击进入地铁攻略。指南本身免费。链接：" + SUBWAY_HREF, exp))


def try_opening_bonus(conn, uid) -> dict:
    """活动期内、未封禁的新用户争抢前 100 名额。用计数器行原子占位，不会超发。"""
    if not is_active(conn):
        return {"awarded": False, "reason": "不在活动期内"}
    u = q(conn, "SELECT id,is_banned,created_at,phone,email FROM c_users WHERE id=%s FOR UPDATE", (uid,), one=True)
    if not u:
        return {"awarded": False, "reason": "用户不存在"}
    if u["is_banned"]:
        return {"awarded": False, "reason": "封禁账号不参加"}
    s = _settings(conn)
    if s["starts_at"] and u["created_at"] and u["created_at"] < s["starts_at"]:
        return {"awarded": False, "reason": "不是活动期内注册"}
    if s["ends_at"] and u["created_at"] and u["created_at"] > s["ends_at"]:
        return {"awarded": False, "reason": "不是活动期内注册"}
    if q(conn, "SELECT rank FROM c_opening_bonus WHERE user_id=%s", (uid,), one=True):
        return {"awarded": False, "reason": "已经领过", "already": True}
    # 重复账号：同一手机或邮箱已占过名额（理论上注册已唯一，再防一层）
    if u["phone"]:
        dup = q(conn, """SELECT b.rank FROM c_opening_bonus b JOIN c_users x ON x.id=b.user_id
                         WHERE b.user_id<>%s AND x.phone=%s""", (uid, u["phone"]), one=True)
        if dup:
            return {"awarded": False, "reason": "重复账号不参加"}
    if u["email"]:
        dup = q(conn, """SELECT b.rank FROM c_opening_bonus b JOIN c_users x ON x.id=b.user_id
                         WHERE b.user_id<>%s AND x.email=%s""", (uid, u["email"]), one=True)
        if dup:
            return {"awarded": False, "reason": "重复账号不参加"}
    slot = q(conn, "UPDATE c_opening_counter SET n=n+1 WHERE id=1 AND n<%s RETURNING n", (CAP,), one=True)
    if not slot:
        return {"awarded": False, "reason": "名额已满"}
    rank = slot["n"]
    try:
        q(conn, "INSERT INTO c_opening_bonus(rank,user_id) VALUES (%s,%s)", (rank, uid))
    except Exception:
        # 用户已有名额或秩冲突：还回计数，避免空占
        q(conn, "UPDATE c_opening_counter SET n=GREATEST(0, n-1) WHERE id=1")
        raise
    day = datetime.now(KST).date()
    q(conn, """INSERT INTO c_points_ledger(user_id,kind,points,day,item_key,note)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
      (uid, BONUS_KIND, BONUS_POINTS, day, f"opening:{rank}", "开业活动第 %d 名注册奖励" % rank))
    return {"awarded": True, "rank": rank, "points": BONUS_POINTS}


def on_new_user(conn, uid):
    issue_subway_card(conn, uid)
    return try_opening_bonus(conn, uid)


def _parse_dt(v, field):
    if v in (None, ""):
        return None
    s = str(v).strip()
    try:
        if len(s) == 10 and s[4] == "-":
            d = datetime.fromisoformat(s)
            return d.replace(tzinfo=KST)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt
    except ValueError:
        raise HTTPException(400, field + " 日期格式应为 YYYY-MM-DD 或完整时间")


@router.get("/api/opening-promo")
def api_public():
    with db() as conn:
        return public_state(conn)


@router.get("/api/admin/opening-promo")
def api_admin_get(req: Request):
    with db() as conn:
        need_admin(conn, req)
        st = public_state(conn)
        rows = q(conn, """SELECT b.rank, b.user_id, b.created_at, u.nickname, u.member_no, u.is_banned
                          FROM c_opening_bonus b JOIN c_users u ON u.id=b.user_id
                          ORDER BY b.rank""")
        st["winners"] = [{"rank": r["rank"], "user_id": r["user_id"], "member_no": r.get("member_no") or r["user_id"],
                          "nickname": r["nickname"], "time": _ts(r["created_at"]), "banned": bool(r["is_banned"])}
                         for r in rows]
        return st


@router.post("/api/admin/opening-promo")
async def api_admin_set(req: Request):
    csrf(req)
    d = await _json(req)
    starts = _parse_dt(d.get("starts_at"), "开始时间")
    ends = _parse_dt(d.get("ends_at"), "结束时间")
    if starts and ends and ends < starts:
        raise HTTPException(400, "结束时间不能早于开始时间")
    enabled = bool(d.get("enabled"))
    with db() as conn:
        need_admin(conn, req)
        q(conn, """UPDATE c_opening_settings SET enabled=%s, starts_at=%s, ends_at=%s, updated_at=now() WHERE id=1""",
          (enabled, starts, ends))
        return public_state(conn)


@router.get("/api/admin/opening-promo.csv")
def api_admin_csv(req: Request):
    with db() as conn:
        need_admin(conn, req)
        rows = q(conn, """SELECT b.rank, b.user_id, b.created_at, u.nickname, u.member_no
                          FROM c_opening_bonus b JOIN c_users u ON u.id=b.user_id
                          ORDER BY b.rank""")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["rank", "member_no", "nickname", "user_id", "time"])
    for r in rows:
        w.writerow([r["rank"], r.get("member_no") or r["user_id"], r["nickname"], r["user_id"], _ts(r["created_at"])])
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=opening-promo.csv"})
