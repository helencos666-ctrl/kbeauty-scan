"""旅韩助手 · 吃喝玩会员推荐地点

登录且未被封禁的注册会员可以提交店铺、酒店、咖啡馆、景点等，
经 Kakao 地图核对和管理员审核后，出现在「吃喝玩」列表。
审核通过时发放旅行豆（站点没有按天数到期的普通会员机制，因此不用试用天数）。
不把密钥、连接串写入日志或前端。
"""
import datetime
import logging
import re
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from community import (
    KST, _page, _ts, clean_text, csrf, current_user, db, ip_of, is_admin,
    limit, need_admin, need_user, process_image, q,
)
from contact_guard import guard_images, guard_text

log = logging.getLogger("fanplaces")

router = APIRouter()

CATS = {
    "food": "美食",
    "coffee": "咖啡",
    "hotel": "酒店住宿",
    "spot": "景点",
    "shop": "购物",
    "other": "其他",
}
CAT_KEYS = set(CATS)
SUB_DAILY = 5
RATE_DAILY = 20
REC_MAX = 200
REVIEW_MAX = 300
PHOTO_MIN, PHOTO_MAX = 1, 6
ABS_MSG = "请避免用「最」「第一」「唯一」这类说法，改得更客观一些后再提交。"
CONTACT_MSG = "请不要填写微信、电话、二维码或站外联系方式。"
KAKAO_MISS_MSG = "没有在地图上找到这家店，已提交给管理员核对。请再确认名称和地址是否写对。"

FANPLACE_QUEUE_COND = "status IN ('pending','flagged')"   # 不带别名，用于计数 SQL
FANPLACE_QUEUE_COND_S = "s.status IN ('pending','flagged')"  # 带别名 s，用于列表 SQL

# 合法筛选值：queue（默认，= 上面那条条件）/ 单个状态 / all
FANPLACE_STATUS_VALUES = ("queue", "pending", "flagged", "approved", "rejected", "all")


def _fanplace_status_where(status):
    """筛选值 → (WHERE 条件, 参数列表)。非法值返回 (None, [])，由调用方报 400。"""
    st = (status or "queue").strip()
    if st not in FANPLACE_STATUS_VALUES:
        return None, []
    if st == "all":
        return "TRUE", []
    if st == "queue":
        return FANPLACE_QUEUE_COND_S, []
    return "s.status=%s", [st]


# 绝对化用语：拦住广告式的「最好 / 第一 / 唯一」，放过「第一次」「第一天」等叙述。
ABS_RE = re.compile(
    r"最(?:好|棒|强|佳|优|顶|美味|值得|便宜|好吃|受欢迎|火爆|火|赞)|"
    r"第一(?!天|次|站|航|回|眼|年|个月)|"
    r"唯一"
)

MIGRATE_FANPLACES = """
CREATE TABLE IF NOT EXISTS c_fan_places (
  id BIGSERIAL PRIMARY KEY,
  kakao_id TEXT,
  name TEXT NOT NULL,
  name_user TEXT,
  category TEXT NOT NULL,
  address TEXT,
  address_user TEXT,
  kakao_url TEXT,
  x DOUBLE PRECISION,
  y DOUBLE PRECISION,
  kakao_found BOOLEAN NOT NULL DEFAULT FALSE,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS c_fan_places_kakao_uidx
  ON c_fan_places(kakao_id) WHERE kakao_id IS NOT NULL AND kakao_id <> '';
CREATE INDEX IF NOT EXISTS c_fan_places_status_cat_idx ON c_fan_places(status, category, id DESC);

CREATE TABLE IF NOT EXISTS c_fan_place_subs (
  id BIGSERIAL PRIMARY KEY,
  place_id BIGINT NOT NULL REFERENCES c_fan_places(id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  rec_text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  review_note TEXT,
  points_awarded BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at TIMESTAMPTZ,
  reviewed_by BIGINT
);
CREATE INDEX IF NOT EXISTS c_fan_place_subs_status_idx ON c_fan_place_subs(status, id DESC);
CREATE INDEX IF NOT EXISTS c_fan_place_subs_user_idx ON c_fan_place_subs(user_id, id DESC);
CREATE INDEX IF NOT EXISTS c_fan_place_subs_place_idx ON c_fan_place_subs(place_id, status, id DESC);

CREATE TABLE IF NOT EXISTS c_fan_place_photos (
  id BIGSERIAL PRIMARY KEY,
  place_id BIGINT NOT NULL REFERENCES c_fan_places(id) ON DELETE CASCADE,
  sub_id BIGINT REFERENCES c_fan_place_subs(id) ON DELETE CASCADE,
  user_id BIGINT REFERENCES c_users(id) ON DELETE SET NULL,
  data BYTEA NOT NULL,
  thumb BYTEA NOT NULL,
  width INT,
  height INT,
  pos INT NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_fan_place_photos_place_idx ON c_fan_place_photos(place_id, status, pos, id);

CREATE TABLE IF NOT EXISTS c_fan_place_ratings (
  id BIGSERIAL PRIMARY KEY,
  place_id BIGINT NOT NULL REFERENCES c_fan_places(id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  stars INT NOT NULL,
  body TEXT,
  hidden BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (place_id, user_id)
);
CREATE INDEX IF NOT EXISTS c_fan_place_ratings_place_idx ON c_fan_place_ratings(place_id, updated_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS c_points_fanplace_approve_uidx
  ON c_points_ledger(user_id, item_key) WHERE kind = 'fanplace_approve' AND item_key IS NOT NULL;
"""


def _kst_day():
    return datetime.datetime.now(KST).date()


def _guard_abs(*texts):
    for t in texts:
        if t and ABS_RE.search(t):
            raise HTTPException(400, ABS_MSG)


def _cat(v):
    k = (v or "").strip()
    if k not in CAT_KEYS:
        raise HTTPException(400, "请选择分类（美食、咖啡、酒店住宿、景点、购物或其他）")
    return k


def _map_url(p):
    if p.get("kakao_url"):
        return p["kakao_url"]
    name = p.get("name") or "place"
    if p.get("y") is not None and p.get("x") is not None:
        return "https://map.kakao.com/link/map/%s,%s,%s" % (quote(name, safe=""), p["y"], p["x"])
    return "https://map.kakao.com/?q=" + quote(name)


def _doc(d):
    try:
        x = float(d.get("x")) if d.get("x") not in (None, "") else None
        y = float(d.get("y")) if d.get("y") not in (None, "") else None
    except (TypeError, ValueError):
        x = y = None
    return {
        "kakao_id": str(d.get("id") or ""),
        "name": (d.get("place_name") or "").strip(),
        "address": (d.get("road_address_name") or d.get("address_name") or "").strip(),
        "kakao_url": (d.get("place_url") or "").strip(),
        "x": x,
        "y": y,
    }


async def kakao_lookup(query, kakao_id=None, x=None, y=None):
    """用 Kakao Local 关键词搜索核对地点。找不到返回 None。不把密钥写入日志。"""
    query = re.sub(r"\s+", " ", (query or "").strip())[:40]
    if len(query) < 1:
        return None
    try:
        from route import _key, _kw
        key = _key()
    except Exception:
        log.warning("kakao lookup unavailable")
        return None
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            docs = await _kw(c, key, query, x, y)
    except Exception:
        log.warning("kakao lookup failed")
        return None
    items = [_doc(d) for d in (docs or []) if d]
    kid = str(kakao_id or "").strip()
    if kid:
        for it in items:
            if it["kakao_id"] == kid:
                return it
        try:
            async with httpx.AsyncClient(timeout=6.0) as c:
                docs2 = await _kw(c, key, kid)
        except Exception:
            docs2 = []
        for it in (_doc(d) for d in (docs2 or []) if d):
            if it["kakao_id"] == kid:
                return it
        return None
    return items[0] if items else None


def _today_subs(conn, uid):
    return q(conn, """SELECT count(*) AS n FROM c_fan_place_subs
                      WHERE user_id=%s AND (timezone('Asia/Seoul', created_at))::date = %s""",
             (uid, _kst_day()), one=True)["n"]


def _today_rates(conn, uid):
    return q(conn, """SELECT count(*) AS n FROM c_fan_place_ratings
                      WHERE user_id=%s AND (timezone('Asia/Seoul', updated_at))::date = %s""",
             (uid, _kst_day()), one=True)["n"]


def _rating_sum(conn, pid):
    r = q(conn, """SELECT COALESCE(AVG(stars),0) AS avg, count(*) AS n
                   FROM c_fan_place_ratings WHERE place_id=%s AND NOT hidden""", (pid,), one=True)
    return round(float(r["avg"] or 0), 2), int(r["n"] or 0)


def _public_place(conn, row, *, with_recs=False, with_ratings=False, me=None):
    avg, n = _rating_sum(conn, row["id"])
    recs = q(conn, """SELECT s.id, s.rec_text, s.user_id, s.created_at, u.nickname
                      FROM c_fan_place_subs s JOIN c_users u ON u.id=s.user_id
                      WHERE s.place_id=%s AND s.status='approved' AND NOT u.is_banned
                      ORDER BY s.id""", (row["id"],))
    rec_items = [{"id": s["id"], "text": s["rec_text"], "user_id": s["user_id"],
                  "nickname": s["nickname"], "time": _ts(s["created_at"])} for s in recs]
    photos = q(conn, """SELECT id, width, height FROM c_fan_place_photos
                        WHERE place_id=%s AND status='approved' ORDER BY pos, id""", (row["id"],))
    out = {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "category_label": CATS.get(row["category"], row["category"]),
        "address": row["address"] or row.get("address_user") or "",
        "kakao_found": bool(row["kakao_found"]),
        "map_url": _map_url(row),
        "x": row["x"],
        "y": row["y"],
        "avg_rating": avg,
        "rating_count": n,
        "cover_id": photos[0]["id"] if photos else None,
        "photos": [{"id": p["id"], "width": p["width"], "height": p["height"]} for p in photos],
        "recommended_by": rec_items[0]["nickname"] if rec_items else None,
        "rec_text": rec_items[0]["text"] if rec_items else None,
        "created_at": _ts(row["created_at"]),
    }
    if with_recs:
        out["recs"] = rec_items
    if with_ratings:
        mine = None
        ratings = q(conn, """SELECT r.id, r.user_id, r.stars, r.body, r.updated_at, u.nickname
                             FROM c_fan_place_ratings r JOIN c_users u ON u.id=r.user_id
                             WHERE r.place_id=%s AND NOT r.hidden AND NOT u.is_banned
                             ORDER BY r.updated_at DESC LIMIT 80""", (row["id"],))
        items = []
        for r in ratings:
            item = {"id": r["id"], "user_id": r["user_id"], "nickname": r["nickname"],
                    "stars": r["stars"], "body": r["body"] or "", "time": _ts(r["updated_at"])}
            items.append(item)
            if me and r["user_id"] == me["id"]:
                mine = item
        out["ratings"] = items
        out["my_rating"] = mine
    return out


def _notify(conn, uid, text, link):
    try:
        from retention import notify
        notify(conn, uid, "fanplace", text=text, link=link)
    except Exception:
        pass


def _can_see_photo(conn, req, ph):
    if ph["status"] == "approved":
        place = q(conn, "SELECT status FROM c_fan_places WHERE id=%s", (ph["place_id"],), one=True)
        if place and place["status"] == "approved":
            return True
    if is_admin(conn, req):
        return True
    u = current_user(conn, req)
    return bool(u and ph.get("user_id") == u["id"])


@router.get("/places")
def page_list():
    return _page("places.html", "/places")


@router.get("/places/new")
def page_new():
    return _page("places-new.html", "/places/new")


@router.get("/places/{pid}")
def page_one(pid: int):
    if pid <= 0:
        raise HTTPException(404, "地点不存在")
    return _page("places-one.html", "/places/%d" % pid)


@router.get("/api/places/meta")
def api_meta():
    return {
        "categories": [{"id": k, "label": v} for k, v in CATS.items()],
        "sub_daily": SUB_DAILY,
        "rate_daily": RATE_DAILY,
        "rec_max": REC_MAX,
        "review_max": REVIEW_MAX,
        "photo_min": PHOTO_MIN,
        "photo_max": PHOTO_MAX,
    }


@router.get("/api/places")
def api_list(req: Request, category: str = "", query: str = "", before: int = 0, limit_n: int = 20):
    cat = (category or "").strip()
    if cat and cat not in CAT_KEYS:
        raise HTTPException(400, "分类不正确")
    raw = query or req.query_params.get("q") or ""
    query = re.sub(r"\s+", " ", raw.strip())[:40]
    lim = max(1, min(int(limit_n or 20), 40))
    cond = ["p.status='approved'"]
    args = []
    if cat:
        cond.append("p.category=%s")
        args.append(cat)
    if query:
        cond.append("(p.name ILIKE %s OR COALESCE(p.address,'') ILIKE %s)")
        args.extend(["%" + query + "%", "%" + query + "%"])
    if before:
        cond.append("p.id<%s")
        args.append(int(before))
    sql = ("SELECT p.* FROM c_fan_places p WHERE " + " AND ".join(cond) +
           " ORDER BY p.id DESC LIMIT %s")
    args.append(lim)
    with db() as conn:
        rows = q(conn, sql, tuple(args))
        items = [_public_place(conn, r) for r in rows]
    return {"items": items, "categories": [{"id": k, "label": v} for k, v in CATS.items()]}


@router.get("/api/places/mine")
def api_mine(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        rows = q(conn, """SELECT s.id, s.status, s.rec_text, s.review_note, s.created_at, s.place_id,
                                 p.name, p.category, p.status AS place_status, p.kakao_found
                          FROM c_fan_place_subs s JOIN c_fan_places p ON p.id=s.place_id
                          WHERE s.user_id=%s ORDER BY s.id DESC LIMIT 50""", (u["id"],))
        remaining = max(0, SUB_DAILY - _today_subs(conn, u["id"]))
    return {"items": [{
        "id": r["id"], "place_id": r["place_id"], "name": r["name"],
        "category": r["category"], "category_label": CATS.get(r["category"], r["category"]),
        "status": r["status"], "place_status": r["place_status"],
        "kakao_found": bool(r["kakao_found"]), "rec_text": r["rec_text"],
        "review_note": r["review_note"] or "", "time": _ts(r["created_at"]),
    } for r in rows], "remaining_today": remaining}


@router.get("/api/places/{pid}")
def api_detail(req: Request, pid: int):
    with db() as conn:
        row = q(conn, "SELECT * FROM c_fan_places WHERE id=%s", (pid,), one=True)
        if not row:
            raise HTTPException(404, "地点不存在")
        me = current_user(conn, req)
        admin = is_admin(conn, req)
        if row["status"] != "approved" and not admin:
            if not me or not q(conn, "SELECT 1 AS x FROM c_fan_place_subs WHERE place_id=%s AND user_id=%s",
                               (pid, me["id"]), one=True):
                raise HTTPException(404, "地点不存在")
        out = _public_place(conn, row, with_recs=True, with_ratings=True, me=me)
        out["status"] = row["status"]
        return out


def _photo_response(data, thumb=False):
    if not data:
        raise HTTPException(404, "照片不存在")
    return Response(content=bytes(data), media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400" if not thumb else "public, max-age=86400"})


@router.get("/api/places/photos/{phid}")
def api_photo(req: Request, phid: int, thumb: int = 0):
    with db() as conn:
        ph = q(conn, "SELECT id, place_id, user_id, status, data, thumb FROM c_fan_place_photos WHERE id=%s",
               (phid,), one=True)
        if not ph or not _can_see_photo(conn, req, ph):
            raise HTTPException(404, "照片不存在")
        data = ph["thumb"] if thumb else ph["data"]
    return _photo_response(data, bool(thumb))


@router.post("/api/places")
async def api_submit(req: Request):
    csrf(req)
    form = await req.form()
    name = clean_text(form.get("name"), 60, "名称")
    address = clean_text(form.get("address"), 80, "地址", minlen=0)
    rec = clean_text(form.get("rec_text") or form.get("rec"), REC_MAX, "推荐语")
    category = _cat(form.get("category"))
    kakao_id = str(form.get("kakao_id") or "").strip()[:40]
    if kakao_id and not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", kakao_id):
        raise HTTPException(400, "地图地点编号不正确")
    _guard_abs(name, address, rec)
    files = form.getlist("photos") or form.getlist("photos[]")
    blobs = []
    for f in files:
        if not hasattr(f, "read"):
            continue
        raw = await f.read()
        if raw:
            blobs.append(raw)
    if not (PHOTO_MIN <= len(blobs) <= PHOTO_MAX):
        raise HTTPException(400, "请上传 %d 到 %d 张现场照片" % (PHOTO_MIN, PHOTO_MAX))
    processed = [process_image(raw) for raw in blobs]
    query = " ".join(x for x in (name, address) if x).strip()
    hit = await kakao_lookup(query, kakao_id=kakao_id or None)
    if not hit and kakao_id:
        hit = await kakao_lookup(name, kakao_id=kakao_id)
    with db() as conn:
        u = need_user(conn, req)
        ip = ip_of(req)
        limit("fpsubh:" + str(u["id"]), 8, 3600, "提交太频繁，请稍后再试")
        limit("fpsubip:" + ip, 20, 3600, "这个网络提交太频繁，请稍后再试")
        n = _today_subs(conn, u["id"])
        if n >= SUB_DAILY:
            raise HTTPException(400, "今天已提交 %d 次，明天再来吧" % SUB_DAILY)
        guard_text(conn, u["id"], "fanplace", name, address, rec, msg=CONTACT_MSG)
        guard_images(conn, u["id"], "fanplace", blobs, msg=CONTACT_MSG)
        official_name = (hit or {}).get("name") or name
        official_addr = (hit or {}).get("address") or address
        kid = (hit or {}).get("kakao_id") or None
        found = bool(hit and kid)
        place = None
        if kid:
            place = q(conn, "SELECT * FROM c_fan_places WHERE kakao_id=%s", (kid,), one=True)
        if place:
            # 已有同一地图地点：追加推荐和照片，不再新建地点
            if found and not place["kakao_found"]:
                q(conn, """UPDATE c_fan_places
                           SET kakao_found=TRUE, name=%s, address=%s, kakao_url=%s, x=%s, y=%s, updated_at=now()
                           WHERE id=%s""",
                  (official_name, official_addr, hit.get("kakao_url"), hit.get("x"), hit.get("y"), place["id"]))
                place = q(conn, "SELECT * FROM c_fan_places WHERE id=%s", (place["id"],), one=True)
        else:
            place = q(conn, """INSERT INTO c_fan_places
                               (kakao_id,name,name_user,category,address,address_user,kakao_url,x,y,kakao_found,status)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               RETURNING *""",
                      (kid, official_name, name, category, official_addr, address,
                       (hit or {}).get("kakao_url"), (hit or {}).get("x"), (hit or {}).get("y"),
                       found, "flagged" if not found else "pending"), one=True)
        sub_status = "flagged" if not found else "pending"
        # 已通过的地点再提交：仍须审核照片/推荐语
        if place["status"] == "rejected":
            q(conn, "UPDATE c_fan_places SET status=%s, category=%s, updated_at=now() WHERE id=%s",
              ("flagged" if not found else "pending", category, place["id"]))
        sub = q(conn, """INSERT INTO c_fan_place_subs(place_id,user_id,rec_text,status)
                         VALUES (%s,%s,%s,%s) RETURNING *""",
                (place["id"], u["id"], rec, sub_status), one=True)
        for i, (data, thumb, w, h) in enumerate(processed):
            q(conn, """INSERT INTO c_fan_place_photos(place_id,sub_id,user_id,data,thumb,width,height,pos,status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              (place["id"], sub["id"], u["id"], data, thumb, w, h, i, "pending"))
        remaining = max(0, SUB_DAILY - _today_subs(conn, u["id"]))
    msg = "已提交，请等待管理员审核。"
    if not found:
        msg = KAKAO_MISS_MSG
    elif place and place["status"] == "approved":
        msg = "这家店已经在列表里了，你的照片和推荐语将在审核通过后显示。"
    return {"ok": True, "place_id": place["id"], "sub_id": sub["id"], "status": sub_status,
            "kakao_found": found, "existing": bool(kid and place), "remaining_today": remaining,
            "message": msg}


@router.post("/api/places/{pid}/rate")
async def api_rate(req: Request, pid: int):
    csrf(req)
    d = await req.json()
    try:
        stars = int(d.get("stars"))
    except Exception:
        raise HTTPException(400, "请选择 1 到 5 星")
    if stars < 1 or stars > 5:
        raise HTTPException(400, "请选择 1 到 5 星")
    body = (d.get("body") or d.get("review") or "").strip()
    if body:
        body = clean_text(body, REVIEW_MAX, "评价", minlen=1)
        _guard_abs(body)
    else:
        body = None
    with db() as conn:
        u = need_user(conn, req)
        limit("fprateh:" + str(u["id"]), 10, 3600, "评分太频繁，请稍后再试")
        place = q(conn, "SELECT * FROM c_fan_places WHERE id=%s AND status='approved'", (pid,), one=True)
        if not place:
            raise HTTPException(404, "地点不存在或还在审核中")
        prev = q(conn, "SELECT id, updated_at FROM c_fan_place_ratings WHERE place_id=%s AND user_id=%s",
                 (pid, u["id"]), one=True)
        if not prev:
            if _today_rates(conn, u["id"]) >= RATE_DAILY:
                raise HTTPException(400, "今天已评分 %d 次，明天再来吧" % RATE_DAILY)
        else:
            # 更新也计入当日次数，避免刷评
            day = prev["updated_at"]
            if getattr(day, "tzinfo", None):
                same = day.astimezone(KST).date() == _kst_day()
            else:
                same = False
            if not same and _today_rates(conn, u["id"]) >= RATE_DAILY:
                raise HTTPException(400, "今天已评分 %d 次，明天再来吧" % RATE_DAILY)
        if body:
            guard_text(conn, u["id"], "fanplace_review", body, msg=CONTACT_MSG)
        if prev:
            q(conn, """UPDATE c_fan_place_ratings SET stars=%s, body=%s, hidden=FALSE, updated_at=now()
                       WHERE place_id=%s AND user_id=%s""", (stars, body, pid, u["id"]))
        else:
            q(conn, """INSERT INTO c_fan_place_ratings(place_id,user_id,stars,body)
                       VALUES (%s,%s,%s,%s)""", (pid, u["id"], stars, body))
        avg, n = _rating_sum(conn, pid)
        mine = q(conn, "SELECT id, stars, body, updated_at FROM c_fan_place_ratings WHERE place_id=%s AND user_id=%s",
                 (pid, u["id"]), one=True)
    return {"ok": True, "avg_rating": avg, "rating_count": n,
            "my_rating": {"id": mine["id"], "stars": mine["stars"], "body": mine["body"] or "",
                          "time": _ts(mine["updated_at"])}}


@router.post("/api/places/reviews/{rid}/report")
async def api_report_review(req: Request, rid: int):
    csrf(req)
    d = await req.json()
    reason = clean_text(d.get("reason"), 200, "举报理由")
    with db() as conn:
        u = need_user(conn, req)
        limit("rep:" + str(u["id"]), 10, 3600, "举报太频繁，请稍后再试")
        r = q(conn, """SELECT r.id, r.user_id, r.place_id, r.body FROM c_fan_place_ratings r
                       WHERE r.id=%s AND NOT r.hidden""", (rid,), one=True)
        if not r:
            raise HTTPException(404, "评价不存在")
        if r["user_id"] == u["id"]:
            raise HTTPException(400, "不能举报自己的评价")
        exists = q(conn, """SELECT 1 AS x FROM c_reports
                            WHERE target_type='fan_review' AND target_id=%s AND user_id=%s AND NOT resolved""",
                   (rid, u["id"]), one=True)
        if exists:
            return {"ok": True, "already": True}
        q(conn, "INSERT INTO c_reports(target_type,target_id,user_id,reason) VALUES ('fan_review',%s,%s,%s)",
          (rid, u["id"], reason))
    return {"ok": True}


# ---------- admin ----------
@router.get("/api/admin/fanplaces")
def api_admin_list(req: Request, status: str = "queue"):
    """会员推荐地点审核列表。默认 status=queue，与角标/「N 条待审」同一口径。"""
    cond, args = _fanplace_status_where(status)
    if cond is None:
        raise HTTPException(400, "状态不正确")
    with db() as conn:
        need_admin(conn, req)
        rows = q(conn, f"""SELECT s.*, p.name, p.name_user, p.category, p.address, p.address_user,
                                  p.kakao_id, p.kakao_url, p.kakao_found, p.x, p.y, p.status AS place_status,
                                  u.nickname, u.member_no
                           FROM c_fan_place_subs s
                           JOIN c_fan_places p ON p.id=s.place_id
                           JOIN c_users u ON u.id=s.user_id
                           WHERE {cond}
                           ORDER BY CASE s.status WHEN 'flagged' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, s.id DESC
                           LIMIT 80""", tuple(args))
        items = []
        for r in rows:
            photos = q(conn, "SELECT id FROM c_fan_place_photos WHERE sub_id=%s ORDER BY pos, id", (r["id"],))
            items.append({
                "id": r["id"], "place_id": r["place_id"], "name": r["name"], "name_user": r["name_user"],
                "category": r["category"], "category_label": CATS.get(r["category"], r["category"]),
                "address": r["address"], "address_user": r["address_user"],
                "kakao_id": r["kakao_id"], "kakao_url": r["kakao_url"], "map_url": _map_url(r),
                "kakao_found": bool(r["kakao_found"]), "x": r["x"], "y": r["y"],
                "status": r["status"], "place_status": r["place_status"],
                "rec_text": r["rec_text"], "review_note": r["review_note"] or "",
                "nickname": r["nickname"], "member_no": r["member_no"],
                "user_id": r["user_id"], "time": _ts(r["created_at"]),
                "photo_ids": [p["id"] for p in photos],
            })
        # 与 tab-counts 的会员地点一段同一条条件：pending + flagged
        pending = q(conn, "SELECT count(*) AS n FROM c_fan_place_subs WHERE " + FANPLACE_QUEUE_COND,
                    one=True)["n"]
    return {"items": items, "pending": int(pending)}


@router.get("/api/admin/fanplaces/photos/{phid}")
def api_admin_photo(req: Request, phid: int, thumb: int = 0):
    with db() as conn:
        need_admin(conn, req)
        ph = q(conn, "SELECT data, thumb FROM c_fan_place_photos WHERE id=%s", (phid,), one=True)
        if not ph:
            raise HTTPException(404, "照片不存在")
        data = ph["thumb"] if thumb else ph["data"]
    return _photo_response(data)


@router.post("/api/admin/fanplaces/subs/{sid}")
async def api_admin_review(req: Request, sid: int):
    csrf(req)
    d = await req.json()
    action = (d.get("action") or "").strip()
    reason = (d.get("reason") or "").strip()[:200]
    if action not in ("approve", "reject"):
        raise HTTPException(400, "操作不正确")
    if action == "reject" and len(reason) < 2:
        raise HTTPException(400, "请填写拒绝理由（提交人可见）")
    awarded = 0
    with db() as conn:
        need_admin(conn, req)
        sub = q(conn, "SELECT * FROM c_fan_place_subs WHERE id=%s", (sid,), one=True)
        if not sub:
            raise HTTPException(404, "投稿不存在")
        place = q(conn, "SELECT * FROM c_fan_places WHERE id=%s", (sub["place_id"],), one=True)
        q(conn, """UPDATE c_fan_place_subs SET status=%s, review_note=%s, reviewed_at=now()
                   WHERE id=%s""",
          ("approved" if action == "approve" else "rejected",
           reason if action == "reject" else (sub.get("review_note") or ""), sid))
        if action == "approve":
            q(conn, "UPDATE c_fan_place_photos SET status='approved' WHERE sub_id=%s", (sid,))
            q(conn, "UPDATE c_fan_places SET status='approved', updated_at=now() WHERE id=%s",
              (place["id"],))
            if not sub["points_awarded"]:
                from points import _award
                res = _award(conn, sub["user_id"], "fanplace_approve",
                             item_key="fanplace:%d" % sid, note="推荐地点通过审核")
                awarded = int(res.get("awarded") or 0)
                if awarded or res.get("already"):
                    q(conn, "UPDATE c_fan_place_subs SET points_awarded=TRUE WHERE id=%s", (sid,))
            _notify(conn, sub["user_id"],
                    "你推荐的「%s」已通过审核，感谢分享。" % (place["name"] or "地点"),
                    "/places/%d" % place["id"])
        else:
            q(conn, "UPDATE c_fan_place_photos SET status='rejected' WHERE sub_id=%s", (sid,))
            # 若该地点已无通过的投稿，则回到 pending/flagged/rejected
            left = q(conn, "SELECT count(*) AS n FROM c_fan_place_subs WHERE place_id=%s AND status='approved'",
                     (place["id"],), one=True)["n"]
            if not left:
                pending = q(conn, """SELECT count(*) AS n FROM c_fan_place_subs
                                     WHERE place_id=%s AND status IN ('pending','flagged')""",
                            (place["id"],), one=True)["n"]
                new_st = "pending" if pending else "rejected"
                if new_st == "rejected" and not place["kakao_found"]:
                    new_st = "rejected"
                q(conn, "UPDATE c_fan_places SET status=%s, updated_at=now() WHERE id=%s",
                  (new_st, place["id"]))
            _notify(conn, sub["user_id"],
                    "你推荐的「%s」未通过审核：%s" % (place["name"] or "地点", reason),
                    "/places")
    return {"ok": True, "awarded": awarded, "action": action}


@router.post("/api/admin/fanplaces/ratings/{rid}")
async def api_admin_rating(req: Request, rid: int):
    csrf(req)
    d = await req.json()
    action = (d.get("action") or "").strip()
    if action not in ("hide", "unhide"):
        raise HTTPException(400, "操作不正确")
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, "SELECT id FROM c_fan_place_ratings WHERE id=%s", (rid,), one=True)
        if not r:
            raise HTTPException(404, "评价不存在")
        q(conn, "UPDATE c_fan_place_ratings SET hidden=%s, updated_at=updated_at WHERE id=%s",
          (action == "hide", rid))
    return {"ok": True}
