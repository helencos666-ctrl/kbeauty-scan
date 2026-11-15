"""推荐店铺与炸鸡店铺的菜单价格、评价和位置分享（附加字段，不改原有路径）。

- 菜单：菜名 + 韩元价格；可选按现有 /api/community/rate 估算人民币
- 评价：登录用户 1–5 星 + 短评；管理员可隐藏
- 位置：地址，以及 Naver / Kakao / Google 地图搜索链接（不使用 API 密钥）
- 商家升级（feature/merchant-upgrade）：多张商家照片（管理员上传，存数据库）、人均价格区间、
  评价先审后发（可带 1 张照片）、管理员通过/拒绝/隐藏/删除、商家详情页 /shops/{id}
模式只增加字段和表，不删除已有结构。
"""
import json
from urllib.parse import quote

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response

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
SHOP_PHOTO_MAX = 8            # 每家商家最多 8 张照片
PRICE_MAX_KRW = 10_000_000
PRICE_NOTE_MAX = 60
REVIEW_STATUSES = ("pending", "approved", "rejected")

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

# 商家升级：只加列/加表/加索引，不删除、不改已有数据。
# 已有评价的 status 默认 'approved'（原来就是直接公开的，升级后保持可见）；新评价写入时显式为 'pending'。
MIGRATE_MERCHANT = """
ALTER TABLE c_shop_recs ADD COLUMN IF NOT EXISTS price_min INT;
ALTER TABLE c_shop_recs ADD COLUMN IF NOT EXISTS price_max INT;
ALTER TABLE c_shop_recs ADD COLUMN IF NOT EXISTS price_note TEXT;
ALTER TABLE c_shop_recs ADD COLUMN IF NOT EXISTS is_example BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE c_shop_recs ADD COLUMN IF NOT EXISTS official BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE c_shop_reviews ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'approved';
ALTER TABLE c_shop_reviews ADD COLUMN IF NOT EXISTS photo BYTEA;
ALTER TABLE c_shop_reviews ADD COLUMN IF NOT EXISTS photo_thumb BYTEA;
ALTER TABLE c_shop_reviews ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE c_shop_reviews ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS c_shop_reviews_status_idx ON c_shop_reviews(status, id DESC);

CREATE TABLE IF NOT EXISTS c_shop_photos (
  id BIGSERIAL PRIMARY KEY,
  shop_kind TEXT NOT NULL,
  shop_id BIGINT NOT NULL,
  pos INT NOT NULL DEFAULT 0,
  data BYTEA NOT NULL,
  thumb BYTEA NOT NULL,
  width INT,
  height INT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT c_shop_photos_kind_chk CHECK (shop_kind IN ('rec', 'chicken'))
);
CREATE INDEX IF NOT EXISTS c_shop_photos_shop_idx ON c_shop_photos(shop_kind, shop_id, pos, id);
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


def _int_or_none(v, field):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        n = int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        raise HTTPException(400, f"{field}请填数字（韩元）")
    if n < 0 or n > PRICE_MAX_KRW:
        raise HTTPException(400, f"{field}超出范围")
    return n


def clean_price(d):
    """人均价格区间（韩元）+ 一句价格说明。都可留空。"""
    pmin = _int_or_none(d.get("price_min"), "最低价")
    pmax = _int_or_none(d.get("price_max"), "最高价")
    if pmin is not None and pmax is not None and pmin > pmax:
        raise HTTPException(400, "最低价不能高于最高价")
    note = clean_text(str(d.get("price_note") or ""), PRICE_NOTE_MAX, "价格说明", 0)
    if note:
        from points import _reject_contacts
        _reject_contacts(note)
    return pmin, pmax, note


def price_text(pmin, pmax):
    f = lambda n: "{:,}".format(int(n))
    if pmin is not None and pmax is not None:
        return "₩" + f(pmin) if pmin == pmax else "₩%s–%s" % (f(pmin), f(pmax))
    if pmin is not None:
        return "₩%s 起" % f(pmin)
    if pmax is not None:
        return "₩%s 以内" % f(pmax)
    return ""


def price_out(r):
    pmin, pmax = r.get("price_min"), r.get("price_max")
    return {"price_min": pmin, "price_max": pmax, "price_note": (r.get("price_note") or "").strip(),
            "price_text": price_text(pmin, pmax)}


def shop_photos(conn, kind, shop_ids):
    if not shop_ids:
        return {}
    out = {}
    for r in q(conn, """SELECT id, shop_id, width, height FROM c_shop_photos
                        WHERE shop_kind=%s AND shop_id = ANY(%s) ORDER BY pos, id""", (kind, list(shop_ids))):
        out.setdefault(r["shop_id"], []).append({"id": r["id"], "w": r["width"], "h": r["height"]})
    return out


def review_stats(conn, kind, shop_ids):
    if not shop_ids:
        return {}
    rows = q(conn, """SELECT shop_id,
                             COUNT(*)::int AS n,
                             ROUND(AVG(stars)::numeric, 1) AS avg
                      FROM c_shop_reviews
                      WHERE shop_kind=%s AND status='approved' AND NOT hidden AND shop_id = ANY(%s)
                      GROUP BY shop_id""", (kind, list(shop_ids)))
    return {r["shop_id"]: {"rating_count": r["n"], "rating_avg": float(r["avg"]) if r["avg"] is not None else None}
            for r in rows}


REVIEW_COLS = """r.id, r.shop_kind, r.shop_id, r.user_id, r.stars, r.body, r.hidden, r.status,
                 r.created_at, r.reviewed_at, r.updated_at, (r.photo_thumb IS NOT NULL) AS has_photo"""


def review_out(r, *, mine=False, admin=False):
    o = {
        "id": r["id"],
        "stars": int(r["stars"]),
        "body": r["body"],
        "author": r.get("nickname") or "用户",
        "time": _ts(r.get("updated_at") or r.get("created_at")),
        "has_photo": bool(r.get("has_photo")),
    }
    if o["has_photo"]:
        base = "/api/admin/shop-review-photo/" if admin else "/api/shop-review-photo/"
        o["photo"] = base + str(r["id"])
        o["photo_thumb"] = base + str(r["id"]) + "?t=1"
    if mine or admin:
        o["mine"] = True
        o["status"] = r.get("status") or "approved"
        o["hidden"] = bool(r.get("hidden"))
    if mine and o["has_photo"]:
        # 作者本人看自己待审核的照片走专用地址
        o["photo"] = "/api/shop-review-photo/mine/" + str(r["id"])
        o["photo_thumb"] = o["photo"] + "?t=1"
    if admin:
        o["user_id"] = r.get("user_id")
        o["member_no"] = r.get("member_no")
        o["shop_kind"] = r.get("shop_kind")
        o["shop_id"] = r.get("shop_id")
        o["shop_name"] = r.get("shop_name") or ""
        o["reviewed_at"] = _ts(r.get("reviewed_at"))
    return o


def list_visible_reviews(conn, kind, shop_id, limit_n=30):
    rows = q(conn, f"""SELECT {REVIEW_COLS}, u.nickname
                      FROM c_shop_reviews r JOIN c_users u ON u.id=r.user_id
                      WHERE r.shop_kind=%s AND r.shop_id=%s AND r.status='approved' AND NOT r.hidden AND NOT u.is_banned
                      ORDER BY r.id DESC LIMIT %s""", (kind, shop_id, limit_n))
    return [review_out(r) for r in rows]


def attach_public(conn, kind, shops, rate=None):
    """给店铺字典加上菜单估算、地址、地图链接、价格区间、照片和评分（就地修改并返回）。"""
    ids = [s["id"] for s in shops]
    stats = review_stats(conn, kind, ids)
    photos = shop_photos(conn, kind, ids)
    for s in shops:
        menu = parse_menu(s.get("menu"))
        s["menu"] = menu_with_cny(menu, rate)
        addr = (s.get("address") or "").strip()
        s["address"] = addr
        s["map_links"] = map_links(addr)
        st = stats.get(s["id"]) or {"rating_count": 0, "rating_avg": None}
        s["rating_count"] = st["rating_count"]
        s["rating_avg"] = st["rating_avg"]
        ph = photos.get(s["id"]) or []
        s["photos"] = ph
        s["cover"] = ph[0]["id"] if ph else None
        if "price_min" not in s:
            s.update(price_out(s))
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


def _img_resp(data, *, public=True):
    cache = "public, max-age=3600" if public else "private, no-store"
    return Response(bytes(data), media_type="image/jpeg", headers={"Cache-Control": cache})


# ---------------- 公开：评价 ----------------
@router.get("/api/shop-reviews")
def api_reviews(req: Request, kind: str = "", shop_id: int = 0):
    kind, sid = _parse_kind_id(kind, shop_id)
    with db() as conn:
        if not _public_shop(conn, kind, sid):
            raise HTTPException(404, "店铺不存在")
        items = list_visible_reviews(conn, kind, sid)
        st = review_stats(conn, kind, [sid]).get(sid) or {"rating_count": 0, "rating_avg": None}
        mine = None
        u = current_user(conn, req)
        if u:
            r = q(conn, f"""SELECT {REVIEW_COLS} FROM c_shop_reviews r
                            WHERE r.shop_kind=%s AND r.shop_id=%s AND r.user_id=%s""", (kind, sid, u["id"]), one=True)
            if r:
                r["nickname"] = u.get("nickname") or "用户"
                mine = review_out(r, mine=True)
    return {"items": items, "rating_count": st["rating_count"], "rating_avg": st["rating_avg"], "mine": mine}


@router.post("/api/shop-reviews")
async def api_review_submit(req: Request):
    """提交/修改评价（JSON 或 multipart，multipart 可带 1 张照片 photo）。提交后待审核，通过后才公开。"""
    csrf(req)
    from community import _json, process_image, MAX_IMG_BYTES
    photo_file = None
    ctype = (req.headers.get("content-type") or "").lower()
    if ctype.startswith("multipart/form-data") or ctype.startswith("application/x-www-form-urlencoded"):
        try:
            form = await req.form()
        except Exception:
            raise HTTPException(400, "请求格式不正确")
        d = {k: form.get(k) for k in ("kind", "shop_id", "stars", "body")}
        f = form.get("photo")
        if f is not None and getattr(f, "filename", None):
            photo_file = f
    else:
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
        u = need_user(conn, req)                      # 先验身份，再处理图片
        if not _public_shop(conn, kind, sid):
            raise HTTPException(404, "店铺不存在或未公开")
        limit("srev:" + str(u["id"]), 8, 3600, "评价太频繁，请稍后再试")
        limit("srevip:" + ip_of(req), 20, 3600, "评价太频繁，请稍后再试")
        limit("srevall", 300, 3600, "评价太多了，请稍后再试")   # 全站每小时上限，防刷
        from points import kst_today
        day = kst_today()
        n = q(conn, """SELECT count(*) AS n FROM c_shop_reviews
                       WHERE user_id=%s AND (timezone('Asia/Seoul', created_at))::date=%s""",
              (u["id"], day), one=True)["n"]
        existed = q(conn, "SELECT id, created_at FROM c_shop_reviews WHERE shop_kind=%s AND shop_id=%s AND user_id=%s",
                    (kind, sid, u["id"]), one=True)
        if n >= REVIEW_DAILY and not existed:
            raise HTTPException(429, "今天评价已达 %d 条，请明天再试" % REVIEW_DAILY)
        big = th = None
        if photo_file is not None:
            limit("srevimg:" + str(u["id"]), 10, 3600, "上传照片太频繁，请稍后再试")
            big, th, _w, _h = process_image(await photo_file.read(MAX_IMG_BYTES + 1))
        if existed:
            if big is not None:
                row = q(conn, f"""UPDATE c_shop_reviews r SET stars=%s, body=%s, hidden=FALSE, status='pending',
                                    reviewed_at=NULL, updated_at=now(), photo=%s, photo_thumb=%s
                                 WHERE id=%s RETURNING {REVIEW_COLS}""",
                        (stars, body, big, th, existed["id"]), one=True)
            else:
                row = q(conn, f"""UPDATE c_shop_reviews r SET stars=%s, body=%s, hidden=FALSE, status='pending',
                                    reviewed_at=NULL, updated_at=now()
                                 WHERE id=%s RETURNING {REVIEW_COLS}""",
                        (stars, body, existed["id"]), one=True)
            row["nickname"] = u.get("nickname") or "用户"
            return {"ok": True, "item": review_out(row, mine=True), "updated": True,
                    "message": "已修改，管理员审核通过后显示"}
        row = q(conn, f"""INSERT INTO c_shop_reviews AS r (shop_kind,shop_id,user_id,stars,body,status,photo,photo_thumb)
                         VALUES (%s,%s,%s,%s,%s,'pending',%s,%s) RETURNING {REVIEW_COLS}""",
                (kind, sid, u["id"], stars, body, big, th), one=True)
        row["nickname"] = u.get("nickname") or "用户"
    return {"ok": True, "item": review_out(row, mine=True), "updated": False,
            "message": "评价已提交，管理员审核通过后显示"}


@router.get("/api/shop-review-photo/{rid}")
def api_review_photo(rid: int, t: int = 0):
    col = "photo_thumb" if t else "photo"
    with db() as conn:
        r = q(conn, f"""SELECT r.{col} AS d, r.shop_kind, r.shop_id FROM c_shop_reviews r
                        WHERE r.id=%s AND r.status='approved' AND NOT r.hidden""", (rid,), one=True)
        if not r or r["d"] is None or not _public_shop(conn, r["shop_kind"], r["shop_id"]):
            raise HTTPException(404, "图片不存在")
    return _img_resp(r["d"])


@router.get("/api/shop-review-photo/mine/{rid}")
def api_review_photo_mine(req: Request, rid: int, t: int = 0):
    col = "photo_thumb" if t else "photo"
    with db() as conn:
        u = need_user(conn, req)
        r = q(conn, f"SELECT {col} AS d FROM c_shop_reviews WHERE id=%s AND user_id=%s", (rid, u["id"]), one=True)
        if not r or r["d"] is None:
            raise HTTPException(404, "图片不存在")
    return _img_resp(r["d"], public=False)


# ---------------- 公开：商家照片与详情 ----------------
@router.get("/api/shop-photos/{pid}")
def api_shop_photo(pid: int, t: int = 0):
    col = "thumb" if t else "data"
    with db() as conn:
        r = q(conn, f"SELECT {col} AS d, shop_kind, shop_id FROM c_shop_photos WHERE id=%s", (pid,), one=True)
        if not r or not _public_shop(conn, r["shop_kind"], r["shop_id"]):
            raise HTTPException(404, "图片不存在")
    return _img_resp(r["d"])


def _load_public_rec(conn, sid):
    return q(conn, """SELECT * FROM c_shop_recs WHERE id=%s AND status='approved'""", (sid,), one=True)


@router.get("/api/shops/{sid:int}")
def api_shop_detail(req: Request, sid: int):
    from points import _shop_out
    with db() as conn:
        row = _load_public_rec(conn, sid)
        if not row:
            raise HTTPException(404, "店铺不存在或未公开")
        rate, rate_time = rate_info(conn)
        item = _shop_out(row)
        attach_public(conn, "rec", [item], rate)
    return {"item": item, "rate_cny_krw": rate, "rate_time": rate_time}


@router.get("/shops/{sid:int}")
def p_shop_detail(sid: int):
    from community import _page
    extra = {"index": False}
    try:
        with db() as conn:
            row = _load_public_rec(conn, sid)
        if row:
            desc = (row.get("description") or "")[:80]
            extra = {"title": "%s · %s · 旅韩助手" % (row["name"], row["area"]),
                     "description": "%s（%s · %s）%s" % (row["name"], row["area"], row["shop_type"], desc),
                     "index": not bool(row.get("is_example"))}
    except HTTPException:
        pass
    return _page("shop.html", "/shops/%d" % sid, extra=extra)


# ---------------- 后台：评价审核 ----------------
@router.get("/api/admin/shop-reviews")
def api_admin_reviews(req: Request, kind: str = "", shop_id: int = 0, hidden: str = "", status: str = ""):
    kind_f = (kind or "").strip()
    if kind_f and kind_f not in SHOP_KINDS:
        raise HTTPException(400, "店铺类型不正确")
    status_f = (status or "").strip()
    if status_f and status_f not in REVIEW_STATUSES:
        raise HTTPException(400, "状态不正确")
    with db() as conn:
        need_admin(conn, req)
        sql = f"""SELECT {REVIEW_COLS}, u.nickname, u.member_no, COALESCE(sr.name, ck.name) AS shop_name
                  FROM c_shop_reviews r
                  JOIN c_users u ON u.id=r.user_id
                  LEFT JOIN c_shop_recs sr ON r.shop_kind='rec' AND sr.id=r.shop_id
                  LEFT JOIN c_chicken_shops ck ON r.shop_kind='chicken' AND ck.id=r.shop_id
                  WHERE 1=1"""
        args = []
        if kind_f:
            sql += " AND r.shop_kind=%s"; args.append(kind_f)
        if shop_id:
            sql += " AND r.shop_id=%s"; args.append(int(shop_id))
        if status_f:
            sql += " AND r.status=%s"; args.append(status_f)
        if hidden == "1":
            sql += " AND r.hidden"
        elif hidden == "0":
            sql += " AND NOT r.hidden"
        sql += " ORDER BY r.id DESC LIMIT 200"
        rows = q(conn, sql, tuple(args))
        pend = q(conn, "SELECT count(*) AS n FROM c_shop_reviews WHERE status='pending'", one=True)["n"]
    return {"items": [review_out(r, admin=True) for r in rows], "pending": pend}


@router.post("/api/admin/shop-reviews/{rid}")
async def api_admin_review_action(req: Request, rid: int):
    csrf(req)
    from community import _json
    d = await _json(req)
    act = d.get("action")
    sets = {
        "approve": "status='approved', hidden=FALSE, reviewed_at=now()",
        "reject": "status='rejected', reviewed_at=now()",
        "hide": "hidden=TRUE",
        "unhide": "hidden=FALSE",
    }
    if act not in sets and act != "delete":
        raise HTTPException(400, "未知操作")
    with db() as conn:
        need_admin(conn, req)
        if act == "delete":
            row = q(conn, "DELETE FROM c_shop_reviews WHERE id=%s RETURNING id", (rid,), one=True)
            if not row:
                raise HTTPException(404, "找不到这条评价")
            return {"ok": True, "deleted": True, "id": rid}
        row = q(conn, f"UPDATE c_shop_reviews r SET {sets[act]} WHERE id=%s RETURNING {REVIEW_COLS}", (rid,), one=True)
        if not row:
            raise HTTPException(404, "找不到这条评价")
        u = q(conn, "SELECT nickname, member_no FROM c_users WHERE id=%s", (row["user_id"],), one=True) or {}
        row.update(u)
    return {"ok": True, "item": review_out(row, admin=True)}


@router.get("/api/admin/shop-review-photo/{rid}")
def api_admin_review_photo(req: Request, rid: int, t: int = 0):
    col = "photo_thumb" if t else "photo"
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, f"SELECT {col} AS d FROM c_shop_reviews WHERE id=%s", (rid,), one=True)
        if not r or r["d"] is None:
            raise HTTPException(404, "图片不存在")
    return _img_resp(r["d"], public=False)


# ---------------- 后台：商家资料 ----------------
def _clean_shop_core(d, *, partial=False):
    """名称/地区/类型/简介。partial=True 时只校验传入的字段。"""
    from points import SHOP_AREAS, SHOP_TYPES, SHOP_NAME_MAX, _reject_contacts
    out = {}
    if not partial or "name" in d:
        out["name"] = clean_text(d.get("name"), SHOP_NAME_MAX, "商家名称", 2)
        _reject_contacts(out["name"])
    if not partial or "area" in d:
        a = str(d.get("area") or "").strip()
        if a not in SHOP_AREAS:
            raise HTTPException(400, "请选择所在地区")
        out["area"] = a
    if not partial or ("type" in d or "shop_type" in d):
        t = str(d.get("type") or d.get("shop_type") or "").strip()
        if t not in SHOP_TYPES:
            raise HTTPException(400, "请选择商家类型")
        out["shop_type"] = t
    if not partial or "description" in d:
        out["description"] = clean_text(d.get("description"), ADMIN_DESC_MAX, "简介", 4)
        _reject_contacts(out["description"])
    return out


ADMIN_DESC_MAX = 500


def _admin_item(conn, row):
    from points import _shop_out
    rate, _t = rate_info(conn)
    item = _shop_out(row, admin=True)
    attach_public(conn, "rec", [item], rate)
    pend = q(conn, "SELECT count(*) AS n FROM c_shop_reviews WHERE shop_kind='rec' AND shop_id=%s AND status='pending'",
             (row["id"],), one=True)["n"]
    item["review_pending"] = pend
    return item


@router.get("/api/admin/merchants")
def api_admin_merchants(req: Request, status: str = "approved"):
    status = (status or "approved").strip()
    if status not in ("pending", "approved", "rejected", "all"):
        raise HTTPException(400, "状态不正确")
    with db() as conn:
        need_admin(conn, req)
        sql = """SELECT s.*, u.nickname, u.member_no FROM c_shop_recs s JOIN c_users u ON u.id=s.user_id"""
        args = ()
        if status != "all":
            sql += " WHERE s.status=%s"; args = (status,)
        rows = q(conn, sql + " ORDER BY s.id DESC LIMIT 200", args)
        items = [_admin_item(conn, r) for r in rows]
    return {"items": items}


@router.post("/api/admin/merchants")
async def api_admin_merchant_create(req: Request):
    """管理员直接新增商家（直接公开，归属系统账号，不发旅行豆）。"""
    csrf(req)
    from community import _json, bot_id
    d = await _json(req)
    core = _clean_shop_core(d)
    address = clean_address(d.get("address"))
    menu = parse_menu(d.get("menu")) if d.get("menu") is not None else []
    pmin, pmax, note = clean_price(d)
    is_example = bool(d.get("is_example"))
    with db() as conn:
        need_admin(conn, req)
        uid = bot_id(conn)
        row = q(conn, """INSERT INTO c_shop_recs(user_id,name,area,shop_type,description,status,menu,address,
                                                price_min,price_max,price_note,is_example,official,reviewed_at)
                         VALUES (%s,%s,%s,%s,%s,'approved',%s::jsonb,%s,%s,%s,%s,%s,TRUE,now()) RETURNING *""",
                (uid, core["name"], core["area"], core["shop_type"], core["description"],
                 json.dumps(menu, ensure_ascii=False), address or None, pmin, pmax, note or None, is_example), one=True)
        item = _admin_item(conn, row)
    return {"ok": True, "item": item}


@router.post("/api/admin/shop-recs/{sid}/extras")
async def api_admin_shop_extras(req: Request, sid: int):
    """编辑商家资料：地址、菜单、价格区间、示例标记，以及（可选）名称/地区/类型/简介。只改传入的字段。"""
    csrf(req)
    from community import _json
    d = await _json(req)
    sets, args = [], []
    if "address" in d:
        sets.append("address=%s"); args.append(clean_address(d.get("address")) or None)
    if d.get("menu") is not None:
        sets.append("menu=%s::jsonb"); args.append(json.dumps(parse_menu(d.get("menu")), ensure_ascii=False))
    if any(k in d for k in ("price_min", "price_max", "price_note")):
        pmin, pmax, note = clean_price(d)
        sets += ["price_min=%s", "price_max=%s", "price_note=%s"]; args += [pmin, pmax, note or None]
    if "is_example" in d:
        sets.append("is_example=%s"); args.append(bool(d.get("is_example")))
    for k, v in _clean_shop_core(d, partial=True).items():
        sets.append(k + "=%s"); args.append(v)
    with db() as conn:
        need_admin(conn, req)
        row = q(conn, "SELECT * FROM c_shop_recs WHERE id=%s", (sid,), one=True)
        if not row:
            raise HTTPException(404, "找不到这条推荐")
        if sets:
            row = q(conn, "UPDATE c_shop_recs SET " + ", ".join(sets) + " WHERE id=%s RETURNING *",
                    tuple(args) + (sid,), one=True)
        item = _admin_item(conn, row)
    return {"ok": True, "item": item}


# ---------------- 后台：商家照片 ----------------
def _shop_exists(conn, kind, sid):
    t = KIND_TABLE[kind]
    return q(conn, f"SELECT id FROM {t} WHERE id=%s", (sid,), one=True)


@router.get("/api/admin/shop-photos")
def api_admin_shop_photos(req: Request, kind: str = "rec", shop_id: int = 0):
    kind, sid = _parse_kind_id(kind, shop_id)
    with db() as conn:
        need_admin(conn, req)
        items = shop_photos(conn, kind, [sid]).get(sid) or []
    return {"items": items, "max": SHOP_PHOTO_MAX}


@router.post("/api/admin/shop-photos")
async def api_admin_shop_photo_upload(req: Request):
    """multipart：kind、shop_id、images（可多张）。服务器重新压缩（最长边 1280，去 EXIF/定位）后存数据库。"""
    csrf(req)
    from community import process_image, MAX_IMG_BYTES
    try:
        form = await req.form()
    except Exception:
        raise HTTPException(400, "请求格式不正确")
    kind, sid = _parse_kind_id(form.get("kind") or "rec", form.get("shop_id"))
    files = [f for f in form.getlist("images") if getattr(f, "filename", None)]
    if not files:
        raise HTTPException(400, "请选择照片")
    with db() as conn:
        need_admin(conn, req)                         # 先验身份，再解码图片
        if not _shop_exists(conn, kind, sid):
            raise HTTPException(404, "店铺不存在")
        have = q(conn, "SELECT count(*) AS n, COALESCE(max(pos), 0) AS mp FROM c_shop_photos WHERE shop_kind=%s AND shop_id=%s",
                 (kind, sid), one=True)
        if have["n"] + len(files) > SHOP_PHOTO_MAX:
            raise HTTPException(400, "每家最多 %d 张照片，现在已有 %d 张" % (SHOP_PHOTO_MAX, have["n"]))
        pos = have["mp"]
        for f in files:
            big, th, w, h = process_image(await f.read(MAX_IMG_BYTES + 1))
            pos += 1
            q(conn, """INSERT INTO c_shop_photos(shop_kind,shop_id,pos,data,thumb,width,height)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""", (kind, sid, pos, big, th, w, h))
        items = shop_photos(conn, kind, [sid]).get(sid) or []
    return {"ok": True, "items": items}


@router.post("/api/admin/shop-photos/{pid}")
async def api_admin_shop_photo_action(req: Request, pid: int):
    csrf(req)
    from community import _json
    d = await _json(req)
    act = d.get("action")
    if act not in ("delete", "cover"):
        raise HTTPException(400, "未知操作")
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, "SELECT id, shop_kind, shop_id FROM c_shop_photos WHERE id=%s", (pid,), one=True)
        if not r:
            raise HTTPException(404, "照片不存在")
        if act == "delete":
            q(conn, "DELETE FROM c_shop_photos WHERE id=%s", (pid,))
        else:
            q(conn, """UPDATE c_shop_photos SET pos=(SELECT COALESCE(min(pos),0)-1 FROM c_shop_photos
                                                 WHERE shop_kind=%s AND shop_id=%s) WHERE id=%s""",
              (r["shop_kind"], r["shop_id"], pid))
        items = shop_photos(conn, r["shop_kind"], [r["shop_id"]]).get(r["shop_id"]) or []
    return {"ok": True, "items": items}


@router.get("/api/admin/shop-photos/{pid}/img")
def api_admin_shop_photo_img(req: Request, pid: int, t: int = 0):
    col = "thumb" if t else "data"
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, f"SELECT {col} AS d FROM c_shop_photos WHERE id=%s", (pid,), one=True)
        if not r:
            raise HTTPException(404, "图片不存在")
    return _img_resp(r["d"], public=False)
