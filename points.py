"""旅韩助手 · 旅行豆、新人券、推荐店铺（本地功能，不涉及真实支付）

- 旅行豆：签到、分享、查地铁、用沟通卡、完善资料；兑换项仅占位
- 新人券：注册时发放一张立减 ¥9.9，仅展示，人工开通时由管理员核对抵扣
- 推荐店铺：用户提交后待审核，通过后才公开，并通过后发放旅行豆
密钥、数据库地址只从环境变量读取，不写入日志或前端。
"""
import datetime, re

from fastapi import APIRouter, Request, HTTPException
from community import (
    KST, csrf, current_user, db, ip_of, limit, need_admin, need_user, q, _ts, _page,
)

router = APIRouter()

POINTS_NAME = "旅行豆"
WELCOME_KIND = "welcome_99"
WELCOME_TITLE = "新人券 立减¥9.9"
WELCOME_CENTS = 990
WELCOME_DAYS = 30
WELCOME_SCOPE = "适用范围：会员开通（待定）"
WELCOME_STAFF = "人工开通会员时由管理员核对后抵扣，网站收款码金额不变，不会自动改价或在线扣款。"

POINT_RULES = {
    "checkin": {"points": 10, "daily_cap": 1, "label": "每日签到"},
    "share": {"points": 5, "daily_cap": 3, "label": "分享内容"},
    "subway": {"points": 2, "daily_cap": 5, "label": "查询地铁路线"},
    "card": {"points": 2, "daily_cap": 5, "label": "使用沟通卡"},
    "profile": {"points": 20, "daily_cap": 0, "once": True, "label": "完善资料"},
    "shop_approve": {"points": 10, "daily_cap": 0, "label": "推荐店铺通过审核"},
    "opening_bonus": {"points": 50, "daily_cap": 0, "once": True, "label": "开业前100名奖励"},
    "post": {"points": 5, "daily_cap": 3, "label": "发帖"},
    "comment": {"points": 2, "daily_cap": 5, "label": "评论"},
    "first_reply": {"points": 2, "daily_cap": 5, "label": "帖子收到第一条回复"},
    "featured": {"points": 30, "daily_cap": 0, "label": "帖子被加精"},
    "streak_bonus": {"points": 0, "daily_cap": 0, "label": "连续签到奖励"},
}
USER_EVENT_KINDS = ("share", "subway", "card", "profile")
SHARE_TARGET_TYPES = ("post", "shop", "merchant", "trip", "card", "subway", "chicken")

REDEEM_CATALOG = [
    {"id": "member_discount", "title": "会员折扣", "cost": 100, "status": "即将上线", "note": "计划用于会员开通抵扣，尚未上线。"},
    {"id": "merchant_offer", "title": "商家优惠", "cost": 200, "status": "即将上线", "note": "计划用于到店优惠，尚未上线。"},
]

SHOP_AREAS = ["明洞", "弘大", "江南", "东大门", "梨泰院", "圣水", "其他"]
SHOP_TYPES = ["美食", "美妆", "购物", "咖啡", "避雷经验", "其他"]
SHOP_NAME_MAX = 40
SHOP_DESC_MAX = 200
SHOP_DAILY_CAP = 5

URL_RE = re.compile(r"(https?://|www\.|t\.me/|wa\.me/|(?:[\w-]+\.)+(?:com|cn|net|org|kr|cc|xyz|top)\b)", re.I)
PHONE_RE = re.compile(r"(?:(?:\+?\s*86[-\s]*)?1[3-9]\d{9}|(?:\+?\s*82[-\s]*)?0?1[016789]\d{7,8})")
CONTACT_RE = re.compile(r"(微信号?|weixin|wechat|whatsapp|\bqq\s*[:：]|加\s*v\b|加微|直线电话|联系电话)", re.I)
IDEM_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")

MIGRATE_POINTS = """
CREATE TABLE IF NOT EXISTS c_points_ledger (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  points INT NOT NULL,
  day DATE NOT NULL,
  item_key TEXT,
  idempotency_key TEXT,
  note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_points_ledger_user_idx ON c_points_ledger(user_id, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS c_points_ledger_idem_uidx
  ON c_points_ledger(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL AND idempotency_key <> '';
CREATE UNIQUE INDEX IF NOT EXISTS c_points_checkin_day_uidx
  ON c_points_ledger(user_id, day) WHERE kind = 'checkin';
CREATE UNIQUE INDEX IF NOT EXISTS c_points_share_item_day_uidx
  ON c_points_ledger(user_id, day, item_key) WHERE kind = 'share' AND item_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS c_points_profile_uidx
  ON c_points_ledger(user_id) WHERE kind = 'profile';
CREATE UNIQUE INDEX IF NOT EXISTS c_points_shop_approve_uidx
  ON c_points_ledger(user_id, item_key) WHERE kind = 'shop_approve' AND item_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS c_coupons (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  amount_cents INT NOT NULL,
  status TEXT NOT NULL DEFAULT 'available',
  scope_note TEXT NOT NULL,
  staff_note TEXT NOT NULL,
  issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  used_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS c_coupons_welcome_uidx
  ON c_coupons(user_id) WHERE kind = 'welcome_99';
CREATE INDEX IF NOT EXISTS c_coupons_user_idx ON c_coupons(user_id, id DESC);

CREATE TABLE IF NOT EXISTS c_shop_recs (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  area TEXT NOT NULL,
  shop_type TEXT NOT NULL,
  description TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  review_note TEXT,
  points_awarded BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at TIMESTAMPTZ,
  reviewed_by BIGINT
);
CREATE INDEX IF NOT EXISTS c_shop_recs_status_idx ON c_shop_recs(status, id DESC);
CREATE INDEX IF NOT EXISTS c_shop_recs_user_idx ON c_shop_recs(user_id, id DESC);
"""


def kst_today():
    return datetime.datetime.now(KST).date()


def kst_today_sql():
    return "(timezone('Asia/Seoul', now()))::date"


def _clean_idem(raw):
    s = str(raw or "").strip()
    if not s:
        return None
    if not IDEM_RE.match(s):
        raise HTTPException(400, "请求无效，请刷新后重试")
    return s


def _item_key(kind, target_type, target_id):
    tid = str(target_id or "").strip()[:80]
    tt = str(target_type or "").strip().lower()
    if kind == "share":
        if tt and tt not in SHARE_TARGET_TYPES:
            raise HTTPException(400, "分享类型不正确")
        if tt and tid:
            if not re.fullmatch(r"[A-Za-z0-9._:%+-]{1,120}", tid):
                raise HTTPException(400, "分享对象不正确")
            return f"{tt}:{tid}"
        raise HTTPException(400, "请选择要分享的内容")
    if kind in ("subway", "card") and tid:
        if not re.fullmatch(r"[A-Za-z0-9._:%+-]{1,120}", tid):
            return None
        return f"{kind}:{tid}"
    if kind == "profile":
        return "profile"
    if kind == "checkin":
        return "checkin"
    if kind == "shop_approve" and tid:
        return f"shop:{tid}"
    return None


def _balance(conn, uid):
    r = q(conn, "SELECT COALESCE(SUM(points),0) AS n FROM c_points_ledger WHERE user_id=%s", (uid,), one=True)
    return int(r["n"] if r else 0)


def _today_counts(conn, uid, day):
    rows = q(conn, "SELECT kind, count(*) AS n FROM c_points_ledger WHERE user_id=%s AND day=%s GROUP BY kind", (uid, day))
    out = {k: 0 for k in POINT_RULES}
    for r in rows:
        out[r["kind"]] = int(r["n"])
    return out


def _streak(conn, uid, day):
    rows = q(conn, "SELECT day FROM c_points_ledger WHERE user_id=%s AND kind='checkin' ORDER BY day DESC LIMIT 60", (uid,))
    days = [r["day"] for r in rows]
    if not days:
        return 0
    streak = 0
    expect = day
    # 若今天还没签，从昨天开始算已有连续天数
    if days[0] != day:
        expect = day - datetime.timedelta(days=1)
    for d in days:
        if d == expect:
            streak += 1
            expect = expect - datetime.timedelta(days=1)
        elif d > expect:
            continue
        else:
            break
    return streak


def expire_coupons(conn, uid):
    q(conn, """UPDATE c_coupons SET status='expired'
               WHERE user_id=%s AND status='available' AND expires_at < now()""", (uid,))


def issue_welcome_coupon(conn, uid):
    """注册时发放；已有则跳过。对上线前已存在的账号，在查询时补发一张。"""
    exp = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=WELCOME_DAYS)
    q(conn, """INSERT INTO c_coupons(user_id,kind,title,amount_cents,status,scope_note,staff_note,expires_at)
               VALUES (%s,%s,%s,%s,'available',%s,%s,%s)
               ON CONFLICT DO NOTHING""",
      (uid, WELCOME_KIND, WELCOME_TITLE, WELCOME_CENTS, WELCOME_SCOPE, WELCOME_STAFF, exp))


def _coupon_out(r):
    status = r["status"]
    if status == "available" and r["expires_at"] and r["expires_at"] < datetime.datetime.now(datetime.timezone.utc):
        status = "expired"
    o = {
        "id": r["id"],
        "kind": r["kind"],
        "title": r["title"],
        "amount": "%.1f" % (r["amount_cents"] / 100),
        "amount_cents": r["amount_cents"],
        "status": status,
        "scope_note": r["scope_note"],
        "staff_note": r["staff_note"],
        "issued_at": _ts(r["issued_at"]),
        "expires_at": _ts(r["expires_at"]),
        "used_at": _ts(r["used_at"]),
        "flag": "若用于会员档（¥9.9），抵扣后该档相当于免费，需人工确认。",
    }
    if r["kind"] == "subway_guide_card":
        o["href"] = "/?tab=sub"
        o["flag"] = "打开即可查看首尔地铁攻略，指南内容对所有人免费。"
        o["amount"] = ""
    return o


def list_coupons(conn, uid):
    expire_coupons(conn, uid)
    issue_welcome_coupon(conn, uid)
    from opening_promo import issue_subway_card
    issue_subway_card(conn, uid)
    expire_coupons(conn, uid)
    rows = q(conn, """SELECT id,kind,title,amount_cents,status,scope_note,staff_note,issued_at,expires_at,used_at
                      FROM c_coupons WHERE user_id=%s ORDER BY id DESC""", (uid,))
    return [_coupon_out(r) for r in rows]


def user_points_summary(conn, uid):
    issue_welcome_coupon(conn, uid)
    day = kst_today()
    counts = _today_counts(conn, uid, day)
    coupons = list_coupons(conn, uid)
    avail = sum(1 for c in coupons if c["status"] == "available")
    return {
        "name": POINTS_NAME,
        "balance": _balance(conn, uid),
        "streak": _streak(conn, uid, day),
        "checked_in_today": counts.get("checkin", 0) > 0,
        "coupon_available": avail,
    }


def _rules_out(counts):
    out = []
    for kind, rule in POINT_RULES.items():
        if kind in ("shop_approve", "opening_bonus", "featured", "streak_bonus"):
            continue
        cap = rule.get("daily_cap") or 0
        used = counts.get(kind, 0)
        out.append({
            "kind": kind,
            "label": rule["label"],
            "points": rule["points"],
            "daily_cap": cap,
            "used_today": used,
            "remaining_today": None if rule.get("once") else max(0, cap - used),
            "once": bool(rule.get("once")),
        })
    return out


def _award(conn, uid, kind, *, item_key=None, idempotency_key=None, note=None, require_profile=False):
    rule = POINT_RULES[kind]
    day = kst_today()
    q(conn, "SELECT id FROM c_users WHERE id=%s FOR UPDATE", (uid,), one=True)

    if idempotency_key:
        prev = q(conn, "SELECT id,points FROM c_points_ledger WHERE user_id=%s AND idempotency_key=%s",
                 (uid, idempotency_key), one=True)
        if prev:
            return {"ok": True, "awarded": 0, "already": True, "reason": "重复请求", "ledger_id": prev["id"],
                    "balance": _balance(conn, uid)}

    if kind == "share" and item_key:
        prev = q(conn, "SELECT id FROM c_points_ledger WHERE user_id=%s AND kind='share' AND day=%s AND item_key=%s",
                 (uid, day, item_key), one=True)
        if prev:
            return {"ok": True, "awarded": 0, "already": True, "reason": "这条今天已经领过分享奖励了",
                    "balance": _balance(conn, uid)}

    if rule.get("once"):
        prev = q(conn, "SELECT id FROM c_points_ledger WHERE user_id=%s AND kind=%s", (uid, kind), one=True)
        if prev:
            return {"ok": True, "awarded": 0, "already": True, "reason": "这项奖励已经领取过了",
                    "balance": _balance(conn, uid)}

    cap = rule.get("daily_cap") or 0
    if cap:
        n = q(conn, "SELECT count(*) AS n FROM c_points_ledger WHERE user_id=%s AND kind=%s AND day=%s",
              (uid, kind, day), one=True)["n"]
        if n >= cap:
            msg = "今天的分享奖励已达 %d 次，明天再来。分享本身不受影响。" % cap if kind == "share" else "今天这项奖励次数已用完，明天再来"
            return {"ok": True, "awarded": 0, "already": True, "reason": msg, "balance": _balance(conn, uid)}

    if kind == "profile" or require_profile:
        has_av = q(conn, "SELECT (avatar IS NOT NULL) AS h FROM c_users WHERE id=%s", (uid,), one=True)
        has_pf = q(conn, "SELECT 1 AS x FROM c_dating_profiles WHERE user_id=%s", (uid,), one=True)
        if not (has_av and has_av.get("h")) and not has_pf:
            raise HTTPException(400, "请先设置头像或完善交友资料")

    pts = int(rule["points"])
    badge = None
    if kind == "checkin":
        from opening_promo import checkin_points
        pts, badge = checkin_points(conn)
        if badge and not note:
            note = "每日签到（" + badge + "）"
    row = q(conn, """INSERT INTO c_points_ledger(user_id,kind,points,day,item_key,idempotency_key,note)
                     VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
            (uid, kind, pts, day, item_key, idempotency_key, note or rule["label"]), one=True)
    if not row:
        return {"ok": True, "awarded": 0, "already": True, "reason": "已经领取过了", "balance": _balance(conn, uid)}
    out = {"ok": True, "awarded": pts, "already": False, "reason": None, "ledger_id": row["id"],
           "balance": _balance(conn, uid)}
    if badge:
        out["badge"] = badge
    return out


def _points_payload(conn, uid, extra=None):
    day = kst_today()
    counts = _today_counts(conn, uid, day)
    hist = q(conn, """SELECT id,kind,points,day,item_key,note,created_at
                      FROM c_points_ledger WHERE user_id=%s ORDER BY id DESC LIMIT 50""", (uid,))
    out = {
        "name": POINTS_NAME,
        "balance": _balance(conn, uid),
        "streak": _streak(conn, uid, day),
        "checked_in_today": counts.get("checkin", 0) > 0,
        "today": {k: counts.get(k, 0) for k in ("checkin", "share", "subway", "card", "profile")},
        "rules": _rules_out(counts),
        "catalog": REDEEM_CATALOG,
        "history": [{"id": r["id"], "kind": r["kind"], "label": POINT_RULES.get(r["kind"], {}).get("label", r["kind"]),
                     "points": r["points"], "day": r["day"].isoformat() if r["day"] else None,
                     "note": r["note"], "time": _ts(r["created_at"])} for r in hist],
        "coupons": list_coupons(conn, uid),
    }
    from opening_promo import public_state
    out["promo"] = public_state(conn)
    if out["promo"]["active"]:
        for rule in out["rules"]:
            if rule["kind"] == "checkin":
                rule["points"] = out["promo"]["checkin_points"]
                rule["badge"] = out["promo"]["badge"]
    if extra:
        out.update(extra)
    return out


@router.get("/api/points")
def api_points(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        issue_welcome_coupon(conn, u["id"])
        return _points_payload(conn, u["id"])


@router.post("/api/points/checkin")
def api_points_checkin(req: Request):
    csrf(req)
    with db() as conn:
        u = need_user(conn, req)
        limit("ptck:" + str(u["id"]), 10, 60, "操作太频繁，请稍后再试")
        limit("ptckip:" + ip_of(req), 30, 60, "操作太频繁，请稍后再试")
        result = _award(conn, u["id"], "checkin", item_key="checkin", note="每日签到")
        streak = _streak(conn, u["id"], kst_today())
        if result["awarded"]:
            result["reason"] = "签到成功，获得 %d %s" % (result["awarded"], POINTS_NAME)
            from retention import after_checkin
            extra = after_checkin(conn, u["id"], streak)
            if extra.get("streak_bonus"):
                result["reason"] += "；连续签到 %d 天，额外奖励 %d %s" % (streak, extra["streak_bonus"], POINTS_NAME)
            result.update(extra)
        else:
            from retention import CHECKIN_NEXT
            result["next_actions"] = CHECKIN_NEXT
        payload = _points_payload(conn, u["id"], result)
        payload["streak"] = streak
        return payload


@router.post("/api/points/event")
async def api_points_event(req: Request):
    csrf(req)
    from community import _json
    d = await _json(req)
    kind = str(d.get("kind") or "").strip()
    if kind not in USER_EVENT_KINDS:
        raise HTTPException(400, "不支持的奖励类型")
    idem = _clean_idem(d.get("idempotency_key"))
    item_key = _item_key(kind, d.get("target_type"), d.get("target_id") or d.get("item_id"))
    with db() as conn:
        u = need_user(conn, req)
        limit("ptev:" + str(u["id"]), 20, 60, "操作太频繁，请稍后再试")
        limit("ptevip:" + ip_of(req), 60, 60, "操作太频繁，请稍后再试")
        limit("ptevd:" + str(u["id"]), 200, 86400, "今天操作次数太多，请明天再试")
        result = _award(conn, u["id"], kind, item_key=item_key, idempotency_key=idem)
        return _points_payload(conn, u["id"], result)


@router.post("/api/points/redeem")
async def api_points_redeem(req: Request):
    csrf(req)
    with db() as conn:
        need_user(conn, req)
    raise HTTPException(400, "即将上线，暂不可兑换")


@router.get("/api/coupons")
def api_coupons(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        return {"coupons": list_coupons(conn, u["id"])}


def _shop_out(r, *, mine=False, admin=False):
    from shop_extras import parse_menu
    o = {
        "id": r["id"],
        "name": r["name"],
        "area": r["area"],
        "shop_type": r["shop_type"],
        "description": r["description"],
        "status": r["status"],
        "time": _ts(r["created_at"]),
        "menu": parse_menu(r.get("menu")),
        "address": (r.get("address") or "").strip(),
        "is_example": bool(r.get("is_example")),
        "official": bool(r.get("official")),
    }
    from shop_extras import price_out
    o.update(price_out(r))
    if mine or admin:
        o["review_note"] = r.get("review_note")
        o["points_awarded"] = bool(r.get("points_awarded"))
    if admin:
        o["user_id"] = r["user_id"]
        o["member_no"] = r.get("member_no") or r["user_id"]
        o["nickname"] = r.get("nickname") or ""
        o["reviewed_at"] = _ts(r.get("reviewed_at"))
    return o


def _reject_contacts(text: str):
    if URL_RE.search(text):
        raise HTTPException(400, "简介里不能放网址或外链")
    if CONTACT_RE.search(text) or PHONE_RE.search(text):
        raise HTTPException(400, "简介里不能放电话或联系方式")


@router.get("/api/shops")
def api_shops_public(req: Request, area: str = "", shop_type: str = ""):
    area = area.strip()
    shop_type = shop_type.strip()
    if area and area not in SHOP_AREAS:
        raise HTTPException(400, "地区不正确")
    if shop_type and shop_type not in SHOP_TYPES:
        raise HTTPException(400, "类型不正确")
    sql = "SELECT id,name,area,shop_type,description,status,created_at,menu,address,price_min,price_max,price_note,is_example,official FROM c_shop_recs WHERE status='approved'"
    args = []
    if area:
        sql += " AND area=%s"; args.append(area)
    if shop_type:
        sql += " AND shop_type=%s"; args.append(shop_type)
    sql += " ORDER BY id DESC LIMIT 100"
    with db() as conn:
        rows = q(conn, sql, tuple(args))
        from shop_extras import attach_public, rate_info
        rate, rate_time = rate_info(conn)
        items = [_shop_out(r) for r in rows]
        attach_public(conn, "rec", items, rate)
    return {"items": items, "areas": SHOP_AREAS, "types": SHOP_TYPES,
            "rate_cny_krw": rate, "rate_time": rate_time, "rate_source": "manual"}


@router.get("/api/shops/mine")
def api_shops_mine(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        rows = q(conn, """SELECT id,name,area,shop_type,description,status,review_note,points_awarded,created_at,menu,address
                          FROM c_shop_recs WHERE user_id=%s ORDER BY id DESC LIMIT 50""", (u["id"],))
    return {"items": [_shop_out(r, mine=True) for r in rows]}


@router.post("/api/shops")
async def api_shops_submit(req: Request):
    csrf(req)
    from community import _json, clean_text
    d = await _json(req)
    from shop_extras import clean_address, parse_menu
    import json as _jsonlib
    name = clean_text(d.get("name"), SHOP_NAME_MAX, "店铺名称", 2)
    area = str(d.get("area") or "").strip()
    shop_type = str(d.get("type") or d.get("shop_type") or "").strip()
    desc = clean_text(d.get("description"), SHOP_DESC_MAX, "简介", 4)
    address = clean_address(d.get("address"))
    menu = parse_menu(d.get("menu")) if d.get("menu") is not None else []
    if area not in SHOP_AREAS:
        raise HTTPException(400, "请选择所在地区")
    if shop_type not in SHOP_TYPES:
        raise HTTPException(400, "请选择店铺类型")
    _reject_contacts(name)
    _reject_contacts(desc)
    with db() as conn:
        u = need_user(conn, req)
        limit("shop:" + str(u["id"]), 8, 3600, "提交太频繁，请稍后再试")
        limit("shopip:" + ip_of(req), 20, 3600, "提交太频繁，请稍后再试")
        day = kst_today()
        n = q(conn, f"SELECT count(*) AS n FROM c_shop_recs WHERE user_id=%s AND (timezone('Asia/Seoul', created_at))::date=%s",
              (u["id"], day), one=True)["n"]
        if n >= SHOP_DAILY_CAP:
            raise HTTPException(429, "今天提交已达 %d 条，请明天再试" % SHOP_DAILY_CAP)
        row = q(conn, """INSERT INTO c_shop_recs(user_id,name,area,shop_type,description,status,menu,address)
                         VALUES (%s,%s,%s,%s,%s,'pending',%s::jsonb,%s)
                         RETURNING id,name,area,shop_type,description,status,created_at,review_note,points_awarded,menu,address""",
                (u["id"], name, area, shop_type, desc, _jsonlib.dumps(menu, ensure_ascii=False), address or None), one=True)
    return {"ok": True, "item": _shop_out(row, mine=True), "message": "已提交，审核通过后会公开，并通过后发放 10 旅行豆。避雷经验同样需要审核。"}


@router.get("/api/admin/shop-recs")
def api_admin_shop_recs(req: Request, status: str = "pending"):
    status = (status or "pending").strip()
    if status not in ("pending", "approved", "rejected", "all"):
        raise HTTPException(400, "状态不正确")
    with db() as conn:
        need_admin(conn, req)
        if status == "all":
            rows = q(conn, """SELECT s.*, u.nickname, u.member_no FROM c_shop_recs s
                              JOIN c_users u ON u.id=s.user_id ORDER BY s.id DESC LIMIT 100""")
        else:
            rows = q(conn, """SELECT s.*, u.nickname, u.member_no FROM c_shop_recs s
                              JOIN c_users u ON u.id=s.user_id WHERE s.status=%s ORDER BY s.id DESC LIMIT 100""", (status,))
    return {"items": [_shop_out(r, admin=True) for r in rows]}


@router.post("/api/admin/shop-recs/{sid}")
async def api_admin_shop_review(req: Request, sid: int):
    csrf(req)
    from community import _json, clean_text
    d = await _json(req)
    act = d.get("action")
    if act not in ("approve", "reject"):
        raise HTTPException(400, "未知操作")
    with db() as conn:
        need_admin(conn, req)
        row = q(conn, "SELECT * FROM c_shop_recs WHERE id=%s FOR UPDATE", (sid,), one=True)
        if not row:
            raise HTTPException(404, "找不到这条推荐")
        if row["status"] != "pending":
            raise HTTPException(400, "这条已经审核过了")
        note = None if act == "approve" else clean_text(d.get("reason") or "未通过审核", 100, "理由")
        q(conn, """UPDATE c_shop_recs SET status=%s, review_note=%s, reviewed_at=now()
                   WHERE id=%s""", ("approved" if act == "approve" else "rejected", note, sid))
        awarded = 0
        if act == "approve":
            result = _award(conn, row["user_id"], "shop_approve", item_key=f"shop:{sid}",
                            note="推荐店铺审核通过")
            awarded = result["awarded"]
            if awarded:
                q(conn, "UPDATE c_shop_recs SET points_awarded=TRUE WHERE id=%s", (sid,))
    return {"ok": True, "awarded": awarded}


@router.get("/points")
def p_points():
    return _page("points.html", "/points")


@router.get("/coupons")
def p_coupons():
    return _page("coupons.html", "/coupons")


@router.get("/shops")
def p_shops():
    return _page("shops.html", "/shops")
