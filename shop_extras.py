"""推荐店铺与炸鸡店铺的菜单价格、评价和位置分享（附加字段，不改原有路径）。

- 菜单：菜名 + 韩元价格；可选按现有 /api/community/rate 估算人民币
- 评价：登录用户 1–5 星 + 短评；管理员可隐藏
- 位置：地址，以及 Naver / Kakao / Google 地图搜索链接（不使用 API 密钥）
模式只增加字段和表，不删除已有结构。
"""
import json
from urllib.parse import quote

from fastapi import APIRouter, Request, HTTPException

from community import (
    csrf, db, ip_of, limit, need_admin, need_user, current_user, q, _ts, clean_text,
)

router = APIRouter()

SHOP_KINDS = ("rec", "chicken")
KIND_TABLE = {"rec": "c_shop_recs", "chicken": "c_chicken_shops"}
ADDR_MAX = 120
MENU_MAX = 20
REVIEW_MIN, REVIEW_MAX = 4, 200
REVIEW_DAILY = 10

MIGRATE_SHOP_EXTRAS = """
ALTER TABLE c_shop_recs ADD COLUMN IF NOT EXISTS menu JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE c_shop_recs ADD COLUMN IF NOT EXISTS address TEXT;
ALTER TABLE c_chicken_shops ADD COLUMN IF NOT EXISTS address TEXT;

CREATE TABLE IF NOT EXISTS c_shop_reviews (
  id BIGSERIAL PRIMARY KEY,
  shop_kind TEXT NOT NULL,
  shop_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  stars INT NOT NULL,
  body TEXT NOT NULL,
  hidden BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT c_shop_reviews_stars_chk CHECK (stars >= 1 AND stars <= 5),
  CONSTRAINT c_shop_reviews_kind_chk CHECK (shop_kind IN ('rec', 'chicken'))
);
CREATE INDEX IF NOT EXISTS c_shop_reviews_shop_idx
  ON c_shop_reviews(shop_kind, shop_id, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS c_shop_reviews_user_shop_uidx
  ON c_shop_reviews(shop_kind, shop_id, user_id);
"""


def parse_menu(raw):
    from chicken import _menu
    return _menu(raw)[:MENU_MAX]


def menu_with_cny(menu, rate):
    out = []
    for m in menu or []:
        item = {"id": m["id"], "name": m["name"], "price_krw": m["price_krw"]}
        if rate:
            item["price_cny"] = round(m["price_krw"] / float(rate), 2)
        out.append(item)
    return out


def clean_address(raw, *, required=False):
    from points import _reject_contacts
    s = str(raw or "").strip()
    if not s:
        if required:
            raise HTTPException(400, "请填写地址")
        return ""
    s = clean_text(s, ADDR_MAX, "地址", 2)
    _reject_contacts(s)
    return s


def map_links(address):
    if not address:
        return None
    qstr = quote(address, safe="")
    return {
        "naver": "https://map.naver.com/p/search/" + qstr,
        "kakao": "https://map.kakao.com/?q=" + qstr,
        "google": "https://www.google.com/maps/search/?api=1&query=" + qstr,
    }


def rate_info(conn):
    from chicken import _rate_info
    return _rate_info(conn)


def guard_review_text(text):
    from points import _reject_contacts
    from friends import scam_hits, contact_hits
    _reject_contacts(text)
    hits = list(contact_hits(text))
    # 「换汇」在避雷相关评价里可能被正常提到，不按诈骗词拦截
    hits += [w for w in scam_hits(text) if w != "换汇"]
    if hits:
        raise HTTPException(400, "评价里不能含联系方式或敏感营销内容")


def _public_shop(conn, kind, shop_id):
    if kind == "rec":
        return q(conn, "SELECT id FROM c_shop_recs WHERE id=%s AND status='approved'", (shop_id,), one=True)
    return q(conn, "SELECT id FROM c_chicken_shops WHERE id=%s AND active", (shop_id,), one=True)


def review_stats(conn, kind, shop_ids):
    if not shop_ids:
        return {}
    rows = q(conn, """SELECT shop_id,
                             COUNT(*)::int AS n,
                             ROUND(AVG(stars)::numeric, 1) AS avg
                      FROM c_shop_reviews
                      WHERE shop_kind=%s AND NOT hidden AND shop_id = ANY(%s)
                      GROUP BY shop_id""", (kind, list(shop_ids)))
    return {r["shop_id"]: {"rating_count": r["n"], "rating_avg": float(r["avg"]) if r["avg"] is not None else None}
            for r in rows}


def review_out(r, *, mine=False, admin=False):
    o = {
        "id": r["id"],
        "stars": int(r["stars"]),
        "body": r["body"],
        "author": r.get("nickname") or "用户",
        "time": _ts(r.get("created_at")),
    }
    if mine or admin:
        o["mine"] = True
    if admin:
        o["user_id"] = r.get("user_id")
        o["member_no"] = r.get("member_no")
        o["hidden"] = bool(r.get("hidden"))
        o["shop_kind"] = r.get("shop_kind")
        o["shop_id"] = r.get("shop_id")
    return o


def list_visible_reviews(conn, kind, shop_id, limit_n=30):
    rows = q(conn, """SELECT r.id, r.stars, r.body, r.created_at, r.user_id, u.nickname
                      FROM c_shop_reviews r JOIN c_users u ON u.id=r.user_id
                      WHERE r.shop_kind=%s AND r.shop_id=%s AND NOT r.hidden AND NOT u.is_banned
                      ORDER BY r.id DESC LIMIT %s""", (kind, shop_id, limit_n))
    return [review_out(r) for r in rows]


def attach_public(conn, kind, shops, rate=None):
    """给店铺字典加上菜单估算、地址、地图链接和评分（就地修改并返回）。"""
    ids = [s["id"] for s in shops]
    stats = review_stats(conn, kind, ids)
    for s in shops:
        menu = parse_menu(s.get("menu"))
        s["menu"] = menu_with_cny(menu, rate)
        addr = (s.get("address") or "").strip()
        s["address"] = addr
        s["map_links"] = map_links(addr)
        st = stats.get(s["id"]) or {"rating_count": 0, "rating_avg": None}
        s["rating_count"] = st["rating_count"]
        s["rating_avg"] = st["rating_avg"]
    return shops


def _parse_kind_id(kind, shop_id):
    kind = str(kind or "").strip()
    if kind not in SHOP_KINDS:
        raise HTTPException(400, "店铺类型不正确")
    try:
        sid = int(shop_id)
    except (TypeError, ValueError):
        raise HTTPException(400, "店铺不正确")
    return kind, sid


@router.get("/api/shop-reviews")
def api_reviews(req: Request, kind: str = "", shop_id: int = 0):
    kind, sid = _parse_kind_id(kind, shop_id)
    with db() as conn:
        if not _public_shop(conn, kind, sid):
            raise HTTPException(404, "店铺不存在")
        items = list_visible_reviews(conn, kind, sid)
        st = review_stats(conn, kind, [sid]).get(sid) or {"rating_count": 0, "rating_avg": None}
    return {"items": items, "rating_count": st["rating_count"], "rating_avg": st["rating_avg"]}


@router.post("/api/shop-reviews")
async def api_review_submit(req: Request):
    csrf(req)
    from community import _json
    d = await _json(req)
    kind, sid = _parse_kind_id(d.get("kind"), d.get("shop_id") or d.get("id"))
    try:
        stars = int(d.get("stars"))
    except (TypeError, ValueError):
        raise HTTPException(400, "请选择 1–5 星")
    if stars < 1 or stars > 5:
        raise HTTPException(400, "请选择 1–5 星")
    body = clean_text(d.get("body") or d.get("text"), REVIEW_MAX, "评价", REVIEW_MIN)
    guard_review_text(body)
    with db() as conn:
        u = need_user(conn, req)
        if not _public_shop(conn, kind, sid):
            raise HTTPException(404, "店铺不存在或未公开")
        limit("srev:" + str(u["id"]), 8, 3600, "评价太频繁，请稍后再试")
        limit("srevip:" + ip_of(req), 20, 3600, "评价太频繁，请稍后再试")
        from points import kst_today
        day = kst_today()
        n = q(conn, """SELECT count(*) AS n FROM c_shop_reviews
                       WHERE user_id=%s AND (timezone('Asia/Seoul', created_at))::date=%s""",
              (u["id"], day), one=True)["n"]
        existed = q(conn, "SELECT id, created_at FROM c_shop_reviews WHERE shop_kind=%s AND shop_id=%s AND user_id=%s",
                    (kind, sid, u["id"]), one=True)
        if n >= REVIEW_DAILY and not existed:
            raise HTTPException(429, "今天评价已达 %d 条，请明天再试" % REVIEW_DAILY)
        if existed:
            row = q(conn, """UPDATE c_shop_reviews SET stars=%s, body=%s, hidden=FALSE
                             WHERE id=%s RETURNING id,stars,body,created_at,user_id""",
                    (stars, body, existed["id"]), one=True)
            row["nickname"] = u.get("nickname") or "用户"
            return {"ok": True, "item": review_out(row, mine=True), "updated": True, "message": "已更新评价"}
        row = q(conn, """INSERT INTO c_shop_reviews(shop_kind,shop_id,user_id,stars,body)
                         VALUES (%s,%s,%s,%s,%s) RETURNING id,stars,body,created_at,user_id""",
                (kind, sid, u["id"], stars, body), one=True)
        row["nickname"] = u.get("nickname") or "用户"
    return {"ok": True, "item": review_out(row, mine=True), "updated": False, "message": "评价已发布"}


@router.get("/api/admin/shop-reviews")
def api_admin_reviews(req: Request, kind: str = "", shop_id: int = 0, hidden: str = ""):
    kind_f = (kind or "").strip()
    if kind_f and kind_f not in SHOP_KINDS:
        raise HTTPException(400, "店铺类型不正确")
    with db() as conn:
        need_admin(conn, req)
        sql = """SELECT r.*, u.nickname, u.member_no FROM c_shop_reviews r
                 JOIN c_users u ON u.id=r.user_id WHERE 1=1"""
        args = []
        if kind_f:
            sql += " AND r.shop_kind=%s"; args.append(kind_f)
        if shop_id:
            sql += " AND r.shop_id=%s"; args.append(int(shop_id))
        if hidden == "1":
            sql += " AND r.hidden"
        elif hidden == "0":
            sql += " AND NOT r.hidden"
        sql += " ORDER BY r.id DESC LIMIT 200"
        rows = q(conn, sql, tuple(args))
    return {"items": [review_out(r, admin=True) for r in rows]}


@router.post("/api/admin/shop-reviews/{rid}")
async def api_admin_review_hide(req: Request, rid: int):
    csrf(req)
    from community import _json
    d = await _json(req)
    act = d.get("action")
    if act not in ("hide", "unhide"):
        raise HTTPException(400, "未知操作")
    with db() as conn:
        need_admin(conn, req)
        row = q(conn, """UPDATE c_shop_reviews SET hidden=%s WHERE id=%s RETURNING *""",
                (act == "hide", rid), one=True)
        if not row:
            raise HTTPException(404, "找不到这条评价")
        u = q(conn, "SELECT nickname, member_no FROM c_users WHERE id=%s", (row["user_id"],), one=True) or {}
        row.update(u)
    return {"ok": True, "item": review_out(row, admin=True)}


@router.post("/api/admin/shop-recs/{sid}/extras")
async def api_admin_shop_extras(req: Request, sid: int):
    csrf(req)
    from community import _json
    d = await _json(req)
    address = clean_address(d.get("address"))
    menu = parse_menu(d.get("menu")) if d.get("menu") is not None else None
    with db() as conn:
        need_admin(conn, req)
        row = q(conn, "SELECT * FROM c_shop_recs WHERE id=%s", (sid,), one=True)
        if not row:
            raise HTTPException(404, "找不到这条推荐")
        if menu is None:
            menu = parse_menu(row.get("menu"))
        row = q(conn, """UPDATE c_shop_recs SET address=%s, menu=%s::jsonb WHERE id=%s RETURNING *""",
                (address or None, json.dumps(menu, ensure_ascii=False), sid), one=True)
    from points import _shop_out
    with db() as conn:
        rate, _t = rate_info(conn)
        item = _shop_out(row, admin=True)
        attach_public(conn, "rec", [item], rate)
    return {"ok": True, "item": item}
