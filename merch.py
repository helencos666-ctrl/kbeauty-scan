"""商家活动 · 横幅广告位 · 站内咨询

- 每个商家频道有一排小分类（医美：整形/皮肤管理/牙齿/体检 等），活动帖带分类标签、优惠价格、有效期，先审后发，显示阅读数
- 横幅广告位：所有商家频道页（board:<频道>，以后新增频道自动支持）、炸鸡代点页（chicken）、美食页（food 全局 / food:<区域>）、
  推荐店铺页（shops）。后台上传图片、标题、副标/优惠、关联站内页面（店铺详情/活动帖/炸鸡店/站内咨询），排序、起止日期、启停、报价；
  到期自动不再显示；跳转只允许站内页面
- 站内咨询：用户 ↔ 商家 1 对 1（文字 + 图片），未关联商家的店铺进入「平台客服」由管理员回复；
  商家资料、活动帖、横幅、聊天消息都不允许留微信/QQ/电话/二维码（见 contact_guard.py）
数据库只做加法。
"""
from __future__ import annotations

import json, os, re
from datetime import date

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse

from community import (
    BOARDS, MAX_IMG_BYTES, MERCHANT_BOARDS, STATIC, _page, _ts, clean_text, csrf, current_user, db, limit,
    need_admin, need_user, process_image, q,
)
from contact_guard import guard_images, guard_medical, guard_text

router = APIRouter()

MIGRATE_MERCH = """
ALTER TABLE c_posts ADD COLUMN IF NOT EXISTS tag TEXT;
ALTER TABLE c_posts ADD COLUMN IF NOT EXISTS offer TEXT;
ALTER TABLE c_posts ADD COLUMN IF NOT EXISTS valid_until DATE;
ALTER TABLE c_posts ADD COLUMN IF NOT EXISTS views INT NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS c_posts_board_tag_idx ON c_posts(board, tag, id DESC) WHERE NOT deleted;
ALTER TABLE c_shop_recs ADD COLUMN IF NOT EXISTS owner_id BIGINT REFERENCES c_users(id) ON DELETE SET NULL;
CREATE TABLE IF NOT EXISTS c_banners (
  id BIGSERIAL PRIMARY KEY,
  slot TEXT NOT NULL,
  title TEXT NOT NULL,
  subtitle TEXT,
  img BYTEA NOT NULL,
  thumb BYTEA,
  width INT, height INT,
  link_kind TEXT NOT NULL,
  link_id BIGINT,
  merchant_id BIGINT REFERENCES c_users(id) ON DELETE SET NULL,
  sort INT NOT NULL DEFAULT 0,
  starts_on DATE,
  ends_on DATE,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  price_cents INT NOT NULL DEFAULT 0,
  paid BOOLEAN NOT NULL DEFAULT FALSE,
  clicks INT NOT NULL DEFAULT 0,
  note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_banners_slot_idx ON c_banners(slot, sort, id);
CREATE TABLE IF NOT EXISTS c_consult_threads (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  merchant_id BIGINT REFERENCES c_users(id) ON DELETE CASCADE,
  ref_kind TEXT, ref_id BIGINT, ref_title TEXT,
  last_text TEXT,
  last_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  user_unread INT NOT NULL DEFAULT 0,
  merch_unread INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS c_consult_pair_uidx ON c_consult_threads(user_id, COALESCE(merchant_id, 0));
CREATE INDEX IF NOT EXISTS c_consult_merchant_idx ON c_consult_threads(merchant_id, last_at DESC);
CREATE TABLE IF NOT EXISTS c_consult_msgs (
  id BIGSERIAL PRIMARY KEY,
  thread_id BIGINT NOT NULL REFERENCES c_consult_threads(id) ON DELETE CASCADE,
  sender TEXT NOT NULL,
  sender_id BIGINT,
  body TEXT,
  img BYTEA, thumb BYTEA, width INT, height INT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_consult_msgs_idx ON c_consult_msgs(thread_id, id);
ALTER TABLE c_pay_proofs ADD COLUMN IF NOT EXISTS ref_id BIGINT;
"""

# ---------------- 小分类 ----------------
SUBCATS = {
    "hotel": [("hotel", "酒店", "🏨"), ("minsu", "民宿", "🏠"), ("hanok", "韩屋", "🏯"), ("long", "长租", "🔑")],
    "car": [("charter", "包车", "🚐"), ("airport", "接送机", "✈️"), ("rent", "租车", "🚗")],
    "clinic": [("plastic", "整形", "💉"), ("skin", "皮肤管理", "✨"), ("dental", "牙齿", "🦷"), ("checkup", "体检", "🩺")],
    "agency": [("lang", "语学院", "🗣️"), ("univ", "本科", "🎓"), ("grad", "研究生", "📚"), ("visa", "签证咨询", "🛂")],
    "daigou": [("beauty", "美妆", "💄"), ("fashion", "服饰", "👗"), ("duty", "免税", "🛍️"), ("snack", "食品", "🍪")],
    "guide": [("city", "市区", "🏙️"), ("shop", "购物陪同", "🛍️"), ("daytrip", "近郊一日游", "🚌"), ("interp", "翻译陪同", "🗣️")],
    "kpop": [("concert", "演唱会", "🎤"), ("fanmeet", "见面会", "🤝"), ("support", "应援", "💡"), ("popup", "快闪店", "🏪")],
    "logistics": [("express", "国际快递", "📮"), ("sea", "海运", "🚢"), ("air", "空运", "✈️"), ("consol", "集运", "📦"), ("move", "搬家回国", "🏠")],
}
MEDICAL_BOARDS = {"clinic"}
MEDICAL_NOTE = "医疗效果因人而异，本平台不承诺任何效果；请选择持证合法医疗机构，术前与医生充分沟通风险。"


def subcats(board):
    return [{"key": k, "name": n, "icon": i} for k, n, i in SUBCATS.get(board, [])]


def tag_name(board, tag):
    for k, n, _ in SUBCATS.get(board, []):
        if k == tag:
            return n
    return ""


def clean_activity(board, tag, offer, valid_until):
    """校验活动帖附加字段，返回 (tag, offer, valid_until)"""
    tag = (tag or "").strip()
    if tag and not tag_name(board, tag):
        raise HTTPException(400, "请选择正确的分类")
    if SUBCATS.get(board) and not tag:
        raise HTTPException(400, "请选择分类标签")
    offer = clean_text(offer, 60, "优惠/价格", 0) if (offer or "").strip() else None
    vu = None
    if (valid_until or "").strip():
        try:
            vu = date.fromisoformat(valid_until.strip()[:10])
        except ValueError:
            raise HTTPException(400, "有效期格式不对")
    return tag or None, offer, vu


# ---------------- 横幅 ----------------
def _food_areas():
    try:
        with open(os.path.join(STATIC, "food.json"), encoding="utf-8") as f:
            d = json.load(f)
        return [(a["id"], a["zh"], a.get("city_zh", "")) for a in d.get("areas", [])]
    except Exception:
        return []


FOOD_AREAS = _food_areas()
FOOD_AREA_IDS = {a[0] for a in FOOD_AREAS}
FIXED_SLOTS = {"chicken": "炸鸡代点页", "food": "美食页（全部区域）", "shops": "推荐店铺页"}
LINK_KINDS = {"shop": "推荐店铺详情", "chicken": "炸鸡店", "post": "商家活动帖", "consult": "站内咨询"}


def slot_options():
    out = [{"key": "board:" + b, "name": "商家频道 · " + BOARDS[b]} for b in BOARDS if b in MERCHANT_BOARDS]
    out += [{"key": k, "name": v} for k, v in FIXED_SLOTS.items()]
    out += [{"key": "food:" + a, "name": "美食页 · %s%s" % ((c + " ") if c else "", n)} for a, n, c in FOOD_AREAS]
    return out


def valid_slot(s):
    s = (s or "").strip()
    if s in FIXED_SLOTS:
        return s
    if s.startswith("board:") and s[6:] in MERCHANT_BOARDS:
        return s
    if s.startswith("food:") and s[5:] in FOOD_AREA_IDS:
        return s
    raise HTTPException(400, "广告位不存在")


def link_target(conn, kind, lid, banner_id=None):
    """把关联对象换成站内路径；对象不存在/未上架返回 None（横幅不显示）。绝不接受外部 URL"""
    if kind == "shop":
        r = q(conn, "SELECT id FROM c_shop_recs WHERE id=%s AND status='approved'", (lid,), one=True)
        return "/shops/%d" % lid if r else None
    if kind == "chicken":
        r = q(conn, "SELECT id FROM c_chicken_shops WHERE id=%s AND active", (lid,), one=True)
        return "/chicken?shop=%d" % lid if r else None
    if kind == "post":
        r = q(conn, "SELECT id FROM c_posts WHERE id=%s AND status='published' AND NOT deleted", (lid,), one=True)
        return "/community/post/%d" % lid if r else None
    if kind == "consult":
        return "/consult/new?kind=banner&id=%d" % banner_id if banner_id else None
    return None


TODAY = "(now() AT TIME ZONE 'Asia/Seoul')::date"
LIVE = f"active AND (starts_on IS NULL OR starts_on <= {TODAY}) AND (ends_on IS NULL OR ends_on >= {TODAY})"


def _banner_out(r, admin=False):
    o = {"id": r["id"], "slot": r["slot"], "title": r["title"], "subtitle": r.get("subtitle") or "",
         "img": "/api/banners/%d/img" % r["id"], "w": r.get("width"), "h": r.get("height"),
         "href": "/go/banner/%d" % r["id"], "consult": "/consult/new?kind=banner&id=%d" % r["id"]}
    if admin:
        o.update({"link_kind": r["link_kind"], "link_id": r.get("link_id"), "merchant_id": r.get("merchant_id"), "merchant_no": r.get("merchant_no"),
                  "sort": r["sort"], "starts_on": str(r["starts_on"]) if r.get("starts_on") else "",
                  "ends_on": str(r["ends_on"]) if r.get("ends_on") else "", "active": r["active"],
                  "price": round((r.get("price_cents") or 0) / 100, 2), "paid": r["paid"], "clicks": r["clicks"],
                  "note": r.get("note") or "", "live": bool(r.get("live")), "expired": bool(r.get("expired")),
                  "time": _ts(r["created_at"]), "img": "/api/banners/%d/img?a=1" % r["id"]})
    return o


@router.get("/api/merch/meta")
def api_merch_meta():
    return {"subcats": {b: subcats(b) for b in SUBCATS}, "medical_boards": sorted(MEDICAL_BOARDS), "medical_note": MEDICAL_NOTE,
            "merchant_boards": sorted(MERCHANT_BOARDS)}


@router.get("/api/banners")
def api_banners(slot: str, area: str = ""):
    try:
        slots = [valid_slot(slot)]
    except HTTPException:
        return {"banners": []}
    if slot == "food" and area in FOOD_AREA_IDS:
        slots.append("food:" + area)
    with db() as conn:
        rows = q(conn, f"""SELECT id,slot,title,subtitle,width,height,link_kind,link_id FROM c_banners
                           WHERE slot = ANY(%s) AND {LIVE}
                           ORDER BY (slot=%s) ASC, sort, id DESC LIMIT 8""", (slots, slot))
        out = [_banner_out(r) for r in rows if link_target(conn, r["link_kind"], r["link_id"], r["id"])]
    return {"banners": out}


@router.get("/api/banners/{bid}/img")
def api_banner_img(req: Request, bid: int, t: int = 0, a: int = 0):
    col = "thumb" if t else "img"
    with db() as conn:
        if a:
            need_admin(conn, req)
            r = q(conn, f"SELECT {col} AS d FROM c_banners WHERE id=%s", (bid,), one=True)
        else:
            r = q(conn, f"SELECT {col} AS d FROM c_banners WHERE id=%s AND {LIVE}", (bid,), one=True)
    if not r or r["d"] is None:
        raise HTTPException(404, "图片不存在")
    return Response(bytes(r["d"]), media_type="image/jpeg",
                    headers={"Cache-Control": "private, no-store" if a else "public, max-age=3600"})


@router.get("/go/banner/{bid}")
def go_banner(bid: int):
    with db() as conn:
        r = q(conn, f"SELECT id,link_kind,link_id FROM c_banners WHERE id=%s AND {LIVE}", (bid,), one=True)
        to = link_target(conn, r["link_kind"], r["link_id"], r["id"]) if r else None
        if to:
            q(conn, "UPDATE c_banners SET clicks=clicks+1 WHERE id=%s", (bid,))
    return RedirectResponse(to or "/", status_code=302)


@router.get("/api/banners/mine")
def api_my_banners(req: Request):
    """商家自己的横幅（用于会员页付款时选择）"""
    with db() as conn:
        u = need_user(conn, req)
        rows = q(conn, f"""SELECT *, ({LIVE}) AS live, (ends_on < {TODAY}) AS expired FROM c_banners
                           WHERE merchant_id=%s ORDER BY id DESC LIMIT 30""", (u["id"],))
    return {"banners": [{"id": r["id"], "title": r["title"], "slot": r["slot"], "price": round(r["price_cents"] / 100, 2),
                         "paid": r["paid"], "live": bool(r["live"]), "ends_on": str(r["ends_on"]) if r["ends_on"] else ""} for r in rows]}


def banner_price_cents(conn, user_id, bid):
    r = q(conn, "SELECT price_cents, paid FROM c_banners WHERE id=%s AND merchant_id=%s", (bid, user_id), one=True)
    if not r:
        raise HTTPException(400, "请选择要付款的横幅（需管理员先为你创建横幅并报价）")
    if r["paid"]:
        raise HTTPException(409, "这条横幅已付款")
    return r["price_cents"]


def mark_banner_paid(conn, bid):
    q(conn, "UPDATE c_banners SET paid=TRUE, updated_at=now() WHERE id=%s", (bid,))


def _int_or_none(v):
    v = str(v or "").strip()
    if not v:
        return None
    if not re.fullmatch(r"\d{1,12}", v):
        raise HTTPException(400, "编号格式不对")
    return int(v)


def _date_or_none(v):
    v = str(v or "").strip()[:10]
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise HTTPException(400, "日期格式不对")


def _resolve_user(conn, v):
    """管理员填写的商家：会员编号或用户 ID"""
    n = _int_or_none(v)
    if n is None:
        return None
    r = q(conn, "SELECT id FROM c_users WHERE member_no=%s OR id=%s ORDER BY (member_no=%s) DESC LIMIT 1", (n, n, n), one=True)
    if not r:
        raise HTTPException(400, "找不到这个商家用户（请填会员编号）")
    return r["id"]


def _banner_fields(conn, slot, title, subtitle, link_kind, link_id, merchant, sort, starts_on, ends_on, price, note):
    slot = valid_slot(slot)
    title = clean_text(title, 30, "标题", 2)
    subtitle = clean_text(subtitle, 40, "副标", 0) if (subtitle or "").strip() else None
    if link_kind not in LINK_KINDS:
        raise HTTPException(400, "请选择横幅跳转的站内页面")
    lid = _int_or_none(link_id)
    if link_kind != "consult":
        if not lid or not link_target(conn, link_kind, lid):
            raise HTTPException(400, "关联的%s不存在或未上架" % LINK_KINDS[link_kind])
    mid = merchant[1] if isinstance(merchant, tuple) else _resolve_user(conn, merchant)
    s, e = _date_or_none(starts_on), _date_or_none(ends_on)
    if s and e and e < s:
        raise HTTPException(400, "结束日期不能早于开始日期")
    try:
        pc = int(round(float(str(price or "0").strip() or 0) * 100))
    except ValueError:
        raise HTTPException(400, "价格格式不对")
    if pc < 0 or pc > 10_000_000:
        raise HTTPException(400, "价格超出范围")
    try:
        so = int(str(sort or "0").strip() or 0)
    except ValueError:
        so = 0
    return dict(slot=slot, title=title, subtitle=subtitle, link_kind=link_kind, link_id=lid, merchant_id=mid, sort=so,
                starts_on=s, ends_on=e, price_cents=pc, note=(note or "").strip()[:200] or None)


@router.get("/api/admin/banners")
def api_admin_banners(req: Request, slot: str = ""):
    with db() as conn:
        need_admin(conn, req)
        cond, args = "", []
        if slot:
            cond, args = "WHERE slot=%s", [slot]
        rows = q(conn, f"""SELECT b.id,slot,title,subtitle,width,height,link_kind,link_id,merchant_id,sort,starts_on,ends_on,active,
                           price_cents,paid,clicks,note,b.created_at,({LIVE}) AS live, (ends_on < {TODAY}) AS expired, mu.member_no AS merchant_no
                           FROM c_banners b LEFT JOIN c_users mu ON mu.id=b.merchant_id {cond} ORDER BY slot, sort, b.id DESC LIMIT 300""", args)
        shops = q(conn, "SELECT id,name,area,shop_type,owner_id FROM c_shop_recs WHERE status='approved' ORDER BY id DESC LIMIT 300")
        chickens = q(conn, "SELECT id,name,area FROM c_chicken_shops WHERE active ORDER BY id DESC LIMIT 100")
    return {"banners": [_banner_out(r, admin=True) for r in rows], "slots": slot_options(), "link_kinds": LINK_KINDS,
            "shops": [{"id": s["id"], "name": s["name"], "area": s["area"], "type": s["shop_type"], "owner_id": s["owner_id"]} for s in shops],
            "chicken_shops": [{"id": s["id"], "name": s["name"], "area": s["area"] or ""} for s in chickens],
            "food_areas": [{"id": a, "name": n, "city": c} for a, n, c in FOOD_AREAS]}


@router.post("/api/admin/banners")
async def api_admin_banner_create(req: Request, slot: str = Form(...), title: str = Form(...), subtitle: str = Form(""),
                                  link_kind: str = Form(...), link_id: str = Form(""), merchant: str = Form(""),
                                  sort: str = Form("0"), starts_on: str = Form(""), ends_on: str = Form(""),
                                  price: str = Form("0"), note: str = Form(""), bind_owner: str = Form(""),
                                  image: UploadFile = File(...)):
    csrf(req)
    with db() as conn:
        need_admin(conn, req)
    raw = await image.read(MAX_IMG_BYTES + 1)
    with db() as conn:
        need_admin(conn, req)
        f = _banner_fields(conn, slot, title, subtitle, link_kind, link_id, merchant, sort, starts_on, ends_on, price, note)
        guard_text(conn, None, "横幅", f["title"], f["subtitle"] or "")
        guard_images(conn, None, "横幅", [raw])
        big, th, w, h = process_image(raw)
        r = q(conn, """INSERT INTO c_banners(slot,title,subtitle,img,thumb,width,height,link_kind,link_id,merchant_id,sort,
                       starts_on,ends_on,price_cents,note) VALUES (%(slot)s,%(title)s,%(subtitle)s,%(img)s,%(thumb)s,%(w)s,%(h)s,
                       %(link_kind)s,%(link_id)s,%(merchant_id)s,%(sort)s,%(starts_on)s,%(ends_on)s,%(price_cents)s,%(note)s) RETURNING id""",
              dict(f, img=big, thumb=th, w=w, h=h), one=True)
        if bind_owner == "1" and f["link_kind"] == "shop" and f["merchant_id"]:
            q(conn, "UPDATE c_shop_recs SET owner_id=%s WHERE id=%s", (f["merchant_id"], f["link_id"]))
    return {"ok": True, "id": r["id"]}


@router.post("/api/admin/banners/{bid}")
async def api_admin_banner_update(req: Request, bid: int):
    csrf(req)
    d = await req.json()
    with db() as conn:
        need_admin(conn, req)
        cur = q(conn, "SELECT * FROM c_banners WHERE id=%s", (bid,), one=True)
        if not cur:
            raise HTTPException(404, "横幅不存在")
        act = d.get("action") or "update"
        if act == "delete":
            q(conn, "DELETE FROM c_banners WHERE id=%s", (bid,))
            return {"ok": True, "deleted": True}
        if act in ("enable", "disable"):
            q(conn, "UPDATE c_banners SET active=%s, updated_at=now() WHERE id=%s", (act == "enable", bid))
            return {"ok": True}
        if act in ("paid", "unpaid"):
            q(conn, "UPDATE c_banners SET paid=%s, updated_at=now() WHERE id=%s", (act == "paid", bid))
            return {"ok": True}
        g = lambda k, dv: d.get(k, dv)
        f = _banner_fields(conn, g("slot", cur["slot"]), g("title", cur["title"]), g("subtitle", cur["subtitle"] or ""),
                           g("link_kind", cur["link_kind"]), g("link_id", cur["link_id"] or ""), (d["merchant"] if "merchant" in d else ("keep", cur["merchant_id"])),
                           g("sort", cur["sort"]), g("starts_on", cur["starts_on"] or ""), g("ends_on", cur["ends_on"] or ""),
                           g("price", (cur["price_cents"] or 0) / 100), g("note", cur["note"] or ""))
        guard_text(conn, None, "横幅", f["title"], f["subtitle"] or "")
        q(conn, """UPDATE c_banners SET slot=%(slot)s,title=%(title)s,subtitle=%(subtitle)s,link_kind=%(link_kind)s,link_id=%(link_id)s,
                   merchant_id=%(merchant_id)s,sort=%(sort)s,starts_on=%(starts_on)s,ends_on=%(ends_on)s,price_cents=%(price_cents)s,
                   note=%(note)s,updated_at=now() WHERE id=%(id)s""", dict(f, id=bid))
    return {"ok": True}


@router.post("/api/admin/shop-recs/{sid}/owner")
async def api_admin_shop_owner(req: Request, sid: int):
    """把推荐店铺绑定给商家用户（店铺详情页的「立即咨询」会发给该商家；不绑定则由平台客服接待）"""
    csrf(req)
    d = await req.json()
    with db() as conn:
        need_admin(conn, req)
        mid = _resolve_user(conn, d.get("merchant"))
        r = q(conn, "UPDATE c_shop_recs SET owner_id=%s WHERE id=%s RETURNING id", (mid, sid), one=True)
        if not r:
            raise HTTPException(404, "店铺不存在")
    return {"ok": True, "owner_id": mid}


# ---------------- 站内咨询 ----------------
PLATFORM_NAME = "旅韩助手客服"
MSG_MAX = 500


def post_offers_consult(row):
    """商家活动帖、商家标记帖、或作者仍是有效商家时，走站内「立即咨询」，不走私聊。"""
    if row.get("board") in MERCHANT_BOARDS:
        return True
    if row.get("is_merchant"):
        return True
    return bool(row.get("author_merchant"))


def _target_for(conn, kind, oid):
    """返回 (merchant_id or None=平台客服, ref_title)"""
    if kind == "post":
        r = q(conn, """SELECT p.user_id, p.title, p.board, p.is_merchant,
                              (au.merchant_until IS NOT NULL AND au.merchant_until>now()) AS author_merchant
                       FROM c_posts p JOIN c_users au ON au.id=p.user_id
                       WHERE p.id=%s AND p.status='published' AND NOT p.deleted AND NOT au.is_banned""",
              (oid,), one=True)
        if not r or not post_offers_consult(r):
            raise HTTPException(404, "活动不存在或已下架")
        label = "活动：" if r["board"] in MERCHANT_BOARDS else "帖子："
        return r["user_id"], label + r["title"]
    if kind == "shop":
        r = q(conn, "SELECT owner_id, name FROM c_shop_recs WHERE id=%s AND status='approved'", (oid,), one=True)
        if not r:
            raise HTTPException(404, "店铺不存在")
        return r["owner_id"], "店铺：" + r["name"]
    if kind == "chicken":
        r = q(conn, "SELECT owner_id, name FROM c_chicken_shops WHERE id=%s AND active", (oid,), one=True)
        if not r:
            raise HTTPException(404, "店铺不存在")
        return r.get("owner_id"), "炸鸡店：" + r["name"]
    if kind == "banner":
        r = q(conn, f"SELECT id,title,merchant_id,link_kind,link_id FROM c_banners WHERE id=%s AND {LIVE}", (oid,), one=True)
        if not r:
            raise HTTPException(404, "广告已下架")
        mid = r["merchant_id"]
        if not mid and r["link_kind"] in ("shop", "chicken", "post") and r["link_id"]:
            try:
                mid, _ = _target_for(conn, r["link_kind"], r["link_id"])
            except HTTPException:
                mid = None
        return mid, "广告：" + r["title"]
    if kind == "platform":
        return None, "平台客服"
    raise HTTPException(400, "咨询对象不正确")


def _side(t, uid):
    if t["user_id"] == uid:
        return "user"
    if t["merchant_id"] == uid:
        return "merchant"
    return None


@router.post("/api/consult/start")
async def api_consult_start(req: Request):
    csrf(req)
    d = await req.json()
    kind = str(d.get("kind") or "")
    try:
        oid = int(d.get("id") or 0)
    except (TypeError, ValueError):
        oid = 0
    with db() as conn:
        u = need_user(conn, req)
        mid, ref = _target_for(conn, kind, oid)
        if mid == u["id"]:
            raise HTTPException(400, "这是你自己发布的内容")
        limit("cstart:%d" % u["id"], 30, 3600)
        t = q(conn, "SELECT * FROM c_consult_threads WHERE user_id=%s AND COALESCE(merchant_id,0)=%s", (u["id"], mid or 0), one=True)
        if not t:
            t = q(conn, """INSERT INTO c_consult_threads(user_id,merchant_id,ref_kind,ref_id,ref_title) VALUES (%s,%s,%s,%s,%s)
                           ON CONFLICT DO NOTHING RETURNING *""", (u["id"], mid, kind, oid, ref), one=True) or \
                q(conn, "SELECT * FROM c_consult_threads WHERE user_id=%s AND COALESCE(merchant_id,0)=%s", (u["id"], mid or 0), one=True)
        if (t.get("ref_kind"), t.get("ref_id")) != (kind, oid) or not q(conn, "SELECT 1 AS x FROM c_consult_msgs WHERE thread_id=%s LIMIT 1", (t["id"],), one=True):
            q(conn, "UPDATE c_consult_threads SET ref_kind=%s, ref_id=%s, ref_title=%s WHERE id=%s", (kind, oid, ref, t["id"]))
            q(conn, "INSERT INTO c_consult_msgs(thread_id,sender,body) VALUES (%s,'system',%s)", (t["id"], "咨询来自 · " + ref))
    return {"ok": True, "thread_id": t["id"]}


def _thread_out(conn, t, side):
    if side == "user":
        if t["merchant_id"]:
            r = q(conn, "SELECT nickname FROM c_users WHERE id=%s", (t["merchant_id"],), one=True)
            name = (r or {}).get("nickname") or "商家"
        else:
            name = PLATFORM_NAME
        unread = t["user_unread"]
    else:
        r = q(conn, "SELECT nickname FROM c_users WHERE id=%s", (t["user_id"],), one=True)
        name = (r or {}).get("nickname") or "用户"
        unread = t["merch_unread"]
    return {"id": t["id"], "side": side, "name": name, "ref": t.get("ref_title") or "", "last": t.get("last_text") or "",
            "time": _ts(t["last_at"]), "unread": unread, "platform": t["merchant_id"] is None}


@router.get("/api/consult/threads")
def api_consult_threads(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        mine = q(conn, "SELECT * FROM c_consult_threads WHERE user_id=%s ORDER BY last_at DESC LIMIT 100", (u["id"],))
        merch = q(conn, "SELECT * FROM c_consult_threads WHERE merchant_id=%s ORDER BY last_at DESC LIMIT 200", (u["id"],))
        return {"as_user": [_thread_out(conn, t, "user") for t in mine],
                "as_merchant": [_thread_out(conn, t, "merchant") for t in merch], "merchant": bool(u.get("merchant")) or bool(merch)}


def consult_unread(conn, uid):
    r = q(conn, """SELECT COALESCE((SELECT sum(user_unread) FROM c_consult_threads WHERE user_id=%s),0)
                        + COALESCE((SELECT sum(merch_unread) FROM c_consult_threads WHERE merchant_id=%s),0) AS n""", (uid, uid), one=True)
    return int(r["n"] or 0)


@router.get("/api/consult/unread")
def api_consult_unread(req: Request):
    with db() as conn:
        u = current_user(conn, req)
        return {"n": consult_unread(conn, u["id"]) if u else 0}


def _msgs_out(rows, side, admin=False):
    out = []
    for m in rows:
        o = {"id": m["id"], "sender": m["sender"], "body": m["body"] or "", "time": _ts(m["created_at"]),
             "mine": m["sender"] == side}
        if m["has_img"]:
            base = "/api/admin/consult/img/%d" if admin else "/api/consult/img/%d"
            o["img"] = base % m["id"]
            o["w"], o["h"] = m["width"], m["height"]
        out.append(o)
    return out


MSG_COLS = "id,sender,body,created_at,width,height,(img IS NOT NULL) AS has_img"


@router.get("/api/consult/threads/{tid}")
def api_consult_thread(req: Request, tid: int, after: int = 0):
    with db() as conn:
        u = need_user(conn, req)
        t = q(conn, "SELECT * FROM c_consult_threads WHERE id=%s", (tid,), one=True)
        side = _side(t, u["id"]) if t else None
        if not side:
            raise HTTPException(404, "会话不存在")
        rows = q(conn, f"SELECT {MSG_COLS} FROM c_consult_msgs WHERE thread_id=%s AND id>%s ORDER BY id LIMIT 300", (tid, after))
        col = "user_unread" if side == "user" else "merch_unread"
        if t[col]:
            q(conn, f"UPDATE c_consult_threads SET {col}=0 WHERE id=%s", (tid,))
        o = _thread_out(conn, t, side)
        o["unread"] = 0
        if t["ref_kind"] in ("post", "shop", "chicken") and t["ref_id"]:
            o["ref_href"] = {"post": "/community/post/%d", "shop": "/shops/%d", "chicken": "/chicken?shop=%d"}[t["ref_kind"]] % t["ref_id"]
        o["messages"] = _msgs_out(rows, side)
    return o


@router.post("/api/consult/threads/{tid}/messages")
async def api_consult_send(req: Request, tid: int, body: str = Form(""), image: UploadFile | None = File(default=None)):
    csrf(req)
    body = (body or "").replace("\x00", "").strip()
    if len(body) > MSG_MAX:
        raise HTTPException(400, "消息太长（最多 %d 字）" % MSG_MAX)
    has_img = bool(image and image.filename)
    if not body and not has_img:
        raise HTTPException(400, "请输入内容或选择图片")
    with db() as conn:
        u = need_user(conn, req)
        t = q(conn, "SELECT * FROM c_consult_threads WHERE id=%s", (tid,), one=True)
        side = _side(t, u["id"]) if t else None
        if not side:
            raise HTTPException(404, "会话不存在")
        limit("cmsg:%d" % u["id"], 30, 600, "发送太频繁，请稍后再试")
        guard_text(conn, u["id"], "咨询消息", body)
    big = th = w = h = None
    if has_img:
        raw = await image.read(MAX_IMG_BYTES + 1)
        with db() as conn:
            guard_images(conn, u["id"], "咨询图片", [raw])
        big, th, w, h = process_image(raw)
    with db() as conn:
        r = q(conn, """INSERT INTO c_consult_msgs(thread_id,sender,sender_id,body,img,thumb,width,height)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""", (tid, side, u["id"], body or None, big, th, w, h), one=True)
        other = "merch_unread" if side == "user" else "user_unread"
        q(conn, f"UPDATE c_consult_threads SET last_text=%s, last_at=now(), {other}={other}+1 WHERE id=%s",
          ((body or "[图片]")[:60], tid))
    return {"ok": True, "id": r["id"]}


@router.get("/api/consult/img/{mid}")
def api_consult_img(req: Request, mid: int, t: int = 0):
    col = "thumb" if t else "img"
    with db() as conn:
        u = need_user(conn, req)
        r = q(conn, f"""SELECT m.{col} AS d, th.user_id, th.merchant_id FROM c_consult_msgs m JOIN c_consult_threads th ON th.id=m.thread_id
                        WHERE m.id=%s""", (mid,), one=True)
        if not r or u["id"] not in (r["user_id"], r["merchant_id"]) or r["d"] is None:
            raise HTTPException(404, "图片不存在")
    return Response(bytes(r["d"]), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# 管理员：查看全部会话（举报核查）、以平台客服身份回复「平台客服」会话、查看联系方式拦截记录
@router.get("/api/admin/consult/threads")
def api_admin_consult_threads(req: Request, platform: int = 0):
    with db() as conn:
        need_admin(conn, req)
        cond = "WHERE t.merchant_id IS NULL" if platform else ""
        rows = q(conn, f"""SELECT t.*, cu.nickname AS user_name, cu.member_no AS user_no, mu.nickname AS merch_name, mu.member_no AS merch_no
                           FROM c_consult_threads t JOIN c_users cu ON cu.id=t.user_id LEFT JOIN c_users mu ON mu.id=t.merchant_id
                           {cond} ORDER BY t.last_at DESC LIMIT 200""")
    return {"threads": [{"id": r["id"], "user": r["user_name"], "user_no": r["user_no"],
                         "merchant": r["merch_name"] or PLATFORM_NAME, "merchant_no": r["merch_no"], "platform": r["merchant_id"] is None,
                         "ref": r["ref_title"] or "", "last": r["last_text"] or "", "time": _ts(r["last_at"]),
                         "unread": r["merch_unread"] if r["merchant_id"] is None else 0} for r in rows]}


@router.get("/api/admin/consult/threads/{tid}")
def api_admin_consult_thread(req: Request, tid: int):
    with db() as conn:
        need_admin(conn, req)
        t = q(conn, "SELECT * FROM c_consult_threads WHERE id=%s", (tid,), one=True)
        if not t:
            raise HTTPException(404, "会话不存在")
        rows = q(conn, f"SELECT {MSG_COLS} FROM c_consult_msgs WHERE thread_id=%s ORDER BY id LIMIT 1000", (tid,))
        if t["merchant_id"] is None and t["merch_unread"]:
            q(conn, "UPDATE c_consult_threads SET merch_unread=0 WHERE id=%s", (tid,))
    return {"id": tid, "platform": t["merchant_id"] is None, "ref": t["ref_title"] or "",
            "messages": _msgs_out(rows, "merchant" if t["merchant_id"] is None else "", admin=True)}


@router.post("/api/admin/consult/threads/{tid}/messages")
async def api_admin_consult_reply(req: Request, tid: int):
    csrf(req)
    d = await req.json()
    body = clean_text(d.get("body"), MSG_MAX, "回复", 1)
    with db() as conn:
        need_admin(conn, req)
        t = q(conn, "SELECT * FROM c_consult_threads WHERE id=%s", (tid,), one=True)
        if not t:
            raise HTTPException(404, "会话不存在")
        if t["merchant_id"] is not None:
            raise HTTPException(400, "这是用户与商家的会话，管理员只能查看")
        q(conn, "INSERT INTO c_consult_msgs(thread_id,sender,body) VALUES (%s,'merchant',%s)", (tid, body))
        q(conn, "UPDATE c_consult_threads SET last_text=%s, last_at=now(), user_unread=user_unread+1 WHERE id=%s", (body[:60], tid))
    return {"ok": True}


@router.get("/api/admin/consult/img/{mid}")
def api_admin_consult_img(req: Request, mid: int, t: int = 0):
    col = "thumb" if t else "img"
    with db() as conn:
        need_admin(conn, req)
        r = q(conn, f"SELECT {col} AS d FROM c_consult_msgs WHERE id=%s", (mid,), one=True)
    if not r or r["d"] is None:
        raise HTTPException(404, "图片不存在")
    return Response(bytes(r["d"]), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.get("/api/admin/contact-hits")
def api_admin_contact_hits(req: Request):
    with db() as conn:
        need_admin(conn, req)
        rows = q(conn, """SELECT h.*, u.nickname, u.member_no FROM c_contact_hits h LEFT JOIN c_users u ON u.id=h.user_id
                          ORDER BY h.id DESC LIMIT 200""")
    return {"hits": [{"id": r["id"], "user": r["nickname"] or "（管理员/游客）", "member_no": r["member_no"], "place": r["place"],
                      "kind": r["kind"], "snippet": r["snippet"] or "", "time": _ts(r["created_at"])} for r in rows]}


# ---------------- 页面 ----------------
@router.get("/consult")
def p_consult():
    return _page("consult.html", "/consult")


@router.get("/consult/new")
def p_consult_new():
    return _page("consult.html", "/consult")


@router.get("/consult/{tid}")
def p_consult_thread(tid: str):
    n = int(tid) if re.fullmatch(r"\d{1,12}", tid or "") else 0
    return _page("consult.html", "/consult/%d" % n)
