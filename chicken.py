"""旅韩助手 · 炸鸡代点（个人代点协助，非持牌外卖）

- 店铺列表可由管理员编辑；菜单价格为韩元
- 下单后进入 pending_review，由管理员确认、登记付款和送达
- 不自动收款、不自动履约；付款仍用网站收款码，由人工核对
- 微信号如有填写，只存库给管理员查看，不写入日志或错误信息
密钥和数据库地址只从环境变量读取，不写入日志或前端。
"""
import json, re

from fastapi import APIRouter, Request, HTTPException

from community import (
    csrf, db, ip_of, limit, need_admin, need_user, q, _ts, _page, get_settings, current_user,
)

router = APIRouter()

NOTICE = "本服务为个人代点协助，非持牌外卖/食品配送企业；餐品由店铺制作配送，食品质量与配送时间以店铺为准"
ALLERGY = "如有过敏或忌口，请务必写在备注里。下单后由客服确认菜单、价格与送达时间，最终以客服确认为准。"
STATUSES = ("pending_review", "approved", "rejected", "paid", "delivered")
STATUS_CN = {
    "pending_review": "待确认",
    "approved": "已确认，待付款",
    "rejected": "未接单",
    "paid": "已付款，准备配送",
    "delivered": "已送达",
}
NAME_MAX, ADDR_MAX, NOTE_MAX, TIME_MAX, WECHAT_MAX = 40, 120, 200, 40, 40
QTY_MAX = 20

SEED_SHOPS = [
    {"name": "明洞炸鸡（示例）", "area": "明洞", "is_example": True, "sort_order": 1,
     "notes": "示例店铺，不是真实商家，仅用于演示下单流程。",
     "menu": [{"id": "m1", "name": "原味炸鸡（示例）", "price_krw": 18000},
              {"id": "m2", "name": "洋酿炸鸡（示例）", "price_krw": 19000},
              {"id": "m3", "name": "鸡块（示例）", "price_krw": 14000}]},
    {"name": "弘大炸鸡（示例）", "area": "弘大", "is_example": True, "sort_order": 2,
     "notes": "示例店铺，不是真实商家，仅用于演示下单流程。",
     "menu": [{"id": "h1", "name": "辣味炸鸡（示例）", "price_krw": 19000},
              {"id": "h2", "name": "蜂蜜蒜味（示例）", "price_krw": 20000},
              {"id": "h3", "name": "鸡翅（示例）", "price_krw": 15000}]},
    {"name": "江南炸鸡（示例）", "area": "江南", "is_example": True, "sort_order": 3,
     "notes": "示例店铺，不是真实商家，仅用于演示下单流程。",
     "menu": [{"id": "g1", "name": "半半炸鸡（示例）", "price_krw": 23000},
              {"id": "g2", "name": "芝士炸鸡（示例）", "price_krw": 24000},
              {"id": "g3", "name": "年糕套餐（示例）", "price_krw": 16000}]},
]

MIGRATE_CHICKEN = """
CREATE TABLE IF NOT EXISTS c_chicken_shops (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  area TEXT,
  is_example BOOLEAN NOT NULL DEFAULT FALSE,
  menu JSONB NOT NULL DEFAULT '[]'::jsonb,
  notes TEXT,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  sort_order INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS c_chicken_orders (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT REFERENCES c_users(id) ON DELETE SET NULL,
  shop_id BIGINT NOT NULL REFERENCES c_chicken_shops(id),
  shop_name TEXT NOT NULL,
  items JSONB NOT NULL,
  hotel_name TEXT NOT NULL,
  hotel_address TEXT NOT NULL,
  wechat_id TEXT,
  desired_time TEXT,
  notes TEXT,
  subtotal_krw INT NOT NULL,
  fee_krw INT NOT NULL,
  total_krw INT NOT NULL,
  rate_cny_krw NUMERIC,
  rate_time TEXT,
  estimate_cny NUMERIC,
  status TEXT NOT NULL DEFAULT 'pending_review',
  admin_note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_chicken_orders_status_idx ON c_chicken_orders(status, id DESC);
CREATE INDEX IF NOT EXISTS c_chicken_orders_user_idx ON c_chicken_orders(user_id, id DESC);
"""


def ensure_chicken_shops(conn):
    n = q(conn, "SELECT count(*) AS n FROM c_chicken_shops", one=True)["n"]
    if n:
        return
    for s in SEED_SHOPS:
        q(conn, """INSERT INTO c_chicken_shops(name,area,is_example,menu,notes,active,sort_order)
                   VALUES (%s,%s,%s,%s::jsonb,%s,TRUE,%s)""",
          (s["name"], s["area"], s["is_example"], json.dumps(s["menu"], ensure_ascii=False), s["notes"], s["sort_order"]))


def _menu(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    out = []
    for i, it in enumerate(raw or []):
        if not isinstance(it, dict):
            continue
        mid = str(it.get("id") or ("i%d" % (i + 1)))[:20]
        name = str(it.get("name") or "").strip()[:40]
        try:
            price = int(it.get("price_krw"))
        except (TypeError, ValueError):
            continue
        if not name or price < 0 or price > 1000000:
            continue
        out.append({"id": mid, "name": name, "price_krw": price})
    return out


def _shop_out(r):
    return {"id": r["id"], "name": r["name"], "area": r.get("area") or "", "is_example": bool(r["is_example"]),
            "notes": r.get("notes") or "", "active": bool(r.get("active", True)),
            "sort_order": r.get("sort_order") or 0, "menu": _menu(r.get("menu"))}


def _fee(conn):
    st = get_settings(conn)
    try:
        return max(0, int(round(float(st.get("chicken_fee_krw") or 3000))))
    except (TypeError, ValueError):
        return 3000


def _rate_info(conn):
    st = get_settings(conn)
    try:
        rate = float(st.get("rate_cny_krw") or 195)
    except (TypeError, ValueError):
        rate = 195.0
    row = q(conn, "SELECT updated_at FROM c_settings WHERE key='rate_cny_krw'", one=True)
    return rate, (_ts(row["updated_at"]) if row and row.get("updated_at") else None)


def _clean_wechat(raw):
    s = str(raw or "").strip()
    if not s:
        return None
    if len(s) > WECHAT_MAX or not re.fullmatch(r"[A-Za-z0-9_\-]{2,40}", s):
        raise HTTPException(400, "微信号格式不正确")
    return s


def _quote(menu, items, fee, rate):
    by = {m["id"]: m for m in menu}
    lines, subtotal = [], 0
    if not items or not isinstance(items, list):
        raise HTTPException(400, "请选择要代点的餐品")
    if len(items) > 20:
        raise HTTPException(400, "一次点的品类太多")
    for it in items:
        if not isinstance(it, dict):
            raise HTTPException(400, "餐品格式不正确")
        mid = str(it.get("id") or it.get("menu_id") or "").strip()
        try:
            qty = int(it.get("qty") or it.get("quantity") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "数量不正确")
        if qty < 1 or qty > QTY_MAX:
            raise HTTPException(400, "数量需在 1–20 之间")
        m = by.get(mid)
        if not m:
            raise HTTPException(400, "菜单已更新，请刷新后重选")
        line = m["price_krw"] * qty
        subtotal += line
        lines.append({"id": m["id"], "name": m["name"], "price_krw": m["price_krw"], "qty": qty, "line_krw": line})
    if subtotal <= 0:
        raise HTTPException(400, "请选择要代点的餐品")
    total = subtotal + fee
    cny = round(total / rate, 2) if rate else None
    return lines, subtotal, total, cny


def _order_out(r, *, admin=False):
    items = r.get("items")
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            items = []
    o = {
        "id": r["id"], "shop_id": r["shop_id"], "shop_name": r["shop_name"], "items": items,
        "hotel_name": r["hotel_name"], "hotel_address": r["hotel_address"],
        "has_wechat": bool(r.get("wechat_id")),
        "desired_time": r.get("desired_time") or "", "notes": r.get("notes") or "",
        "subtotal_krw": r["subtotal_krw"], "fee_krw": r["fee_krw"], "total_krw": r["total_krw"],
        "rate_cny_krw": float(r["rate_cny_krw"]) if r.get("rate_cny_krw") is not None else None,
        "rate_time": r.get("rate_time"),
        "estimate_cny": float(r["estimate_cny"]) if r.get("estimate_cny") is not None else None,
        "status": r["status"], "status_name": STATUS_CN.get(r["status"], r["status"]),
        "admin_note": r.get("admin_note") or "",
        "time": _ts(r.get("created_at")), "updated": _ts(r.get("updated_at")),
    }
    if admin:
        o["user_id"] = r.get("user_id")
        o["member_no"] = r.get("member_no")
        o["nickname"] = r.get("nickname") or ""
        o["wechat_id"] = r.get("wechat_id") or ""
    return o


@router.get("/api/chicken/shops")
def api_chicken_shops():
    with db() as conn:
        ensure_chicken_shops(conn)
        fee = _fee(conn)
        rate, rate_time = _rate_info(conn)
        rows = q(conn, "SELECT * FROM c_chicken_shops WHERE active ORDER BY sort_order, id")
    return {"shops": [_shop_out(r) for r in rows], "fee_krw": fee, "rate_cny_krw": rate,
            "rate_time": rate_time, "rate_source": "manual",
            "notice": NOTICE, "allergy": ALLERGY,
            "confirm_note": "金额为参考估算，最终以客服确认为准。付款在客服确认后再扫网站收款码，由人工核对。"}


@router.post("/api/chicken/orders")
async def api_chicken_submit(req: Request):
    csrf(req)
    from community import _json, clean_text
    d = await _json(req)
    with db() as conn:
        u = need_user(conn, req)
        # 微信号单独取出，后续异常信息不得回显或打印
        wechat = _clean_wechat(d.get("wechat") or d.get("wechat_id"))
        hotel = clean_text(d.get("hotel_name") or d.get("hotel"), NAME_MAX, "酒店名称", 2)
        addr = clean_text(d.get("hotel_address") or d.get("address"), ADDR_MAX, "酒店地址", 4)
        when = str(d.get("desired_time") or d.get("time") or "").strip()
        if len(when) > TIME_MAX:
            raise HTTPException(400, "希望送达时间过长")
        notes = str(d.get("notes") or "").strip()
        if notes:
            notes = clean_text(notes, NOTE_MAX, "备注", 1)
        try:
            shop_id = int(d.get("shop_id"))
        except (TypeError, ValueError):
            raise HTTPException(400, "请选择店铺")
        limit("chk:" + str(u["id"]), 8, 3600, "提交太频繁，请稍后再试")
        limit("chkd:" + str(u["id"]), 12, 86400, "今天下单次数已用完，请明天再试或联系客服")
        limit("chkip:" + ip_of(req), 20, 3600, "提交太频繁，请稍后再试")
        ensure_chicken_shops(conn)
        shop = q(conn, "SELECT * FROM c_chicken_shops WHERE id=%s AND active", (shop_id,), one=True)
        if not shop:
            raise HTTPException(400, "店铺不存在或已下架")
        fee = _fee(conn)
        rate, rate_time = _rate_info(conn)
        lines, subtotal, total, cny = _quote(_menu(shop["menu"]), d.get("items"), fee, rate)
        row = q(conn, """INSERT INTO c_chicken_orders(
                            user_id,shop_id,shop_name,items,hotel_name,hotel_address,wechat_id,
                            desired_time,notes,subtotal_krw,fee_krw,total_krw,rate_cny_krw,rate_time,estimate_cny,status)
                         VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending_review')
                         RETURNING *""",
                (u["id"], shop["id"], shop["name"], json.dumps(lines, ensure_ascii=False),
                 hotel, addr, wechat, when or None, notes or None,
                 subtotal, fee, total, rate, rate_time, cny), one=True)
    return {"ok": True, "order": _order_out(row),
            "message": "已提交，客服确认后再扫码付款。请勿先付款。"}


@router.get("/api/chicken/orders/mine")
def api_chicken_mine(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        rows = q(conn, "SELECT * FROM c_chicken_orders WHERE user_id=%s ORDER BY id DESC LIMIT 50", (u["id"],))
    return {"orders": [_order_out(r) for r in rows], "notice": NOTICE}


@router.get("/api/admin/chicken/shops")
def api_admin_chicken_shops(req: Request):
    with db() as conn:
        need_admin(conn, req)
        ensure_chicken_shops(conn)
        rows = q(conn, "SELECT * FROM c_chicken_shops ORDER BY sort_order, id")
    return {"shops": [_shop_out(r) for r in rows]}


@router.post("/api/admin/chicken/shops")
async def api_admin_chicken_shop_save(req: Request):
    csrf(req)
    from community import _json, clean_text
    d = await _json(req)
    name = clean_text(d.get("name"), NAME_MAX, "店铺名称", 2)
    area = clean_text(d.get("area") or "其他", 20, "地区", 1)
    notes = clean_text(d.get("notes") or " ", NOTE_MAX, "说明", 0) if d.get("notes") else ""
    menu = _menu(d.get("menu"))
    if not menu:
        raise HTTPException(400, "请至少添加一个菜品")
    is_example = bool(d.get("is_example"))
    active = False if d.get("active") in (False, 0, "0") else True
    try:
        sort_order = int(d.get("sort_order") or 0)
    except (TypeError, ValueError):
        sort_order = 0
    with db() as conn:
        need_admin(conn, req)
        sid = d.get("id")
        if sid:
            row = q(conn, """UPDATE c_chicken_shops SET name=%s,area=%s,is_example=%s,menu=%s::jsonb,notes=%s,active=%s,sort_order=%s
                             WHERE id=%s RETURNING *""",
                    (name, area, is_example, json.dumps(menu, ensure_ascii=False), notes, active, sort_order, int(sid)), one=True)
            if not row:
                raise HTTPException(404, "找不到店铺")
        else:
            row = q(conn, """INSERT INTO c_chicken_shops(name,area,is_example,menu,notes,active,sort_order)
                             VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s) RETURNING *""",
                    (name, area, is_example, json.dumps(menu, ensure_ascii=False), notes, active, sort_order), one=True)
    return {"ok": True, "shop": _shop_out(row)}


@router.get("/api/admin/chicken/orders")
def api_admin_chicken_orders(req: Request, status: str = ""):
    status = (status or "").strip()
    if status and status not in STATUSES and status != "all":
        raise HTTPException(400, "状态不正确")
    with db() as conn:
        need_admin(conn, req)
        if status and status != "all":
            rows = q(conn, """SELECT o.*, u.nickname, u.member_no FROM c_chicken_orders o
                              LEFT JOIN c_users u ON u.id=o.user_id
                              WHERE o.status=%s ORDER BY o.id DESC LIMIT 100""", (status,))
        else:
            rows = q(conn, """SELECT o.*, u.nickname, u.member_no FROM c_chicken_orders o
                              LEFT JOIN c_users u ON u.id=o.user_id
                              ORDER BY o.id DESC LIMIT 100""")
    return {"orders": [_order_out(r, admin=True) for r in rows]}


@router.post("/api/admin/chicken/orders/{oid}")
async def api_admin_chicken_review(req: Request, oid: int):
    csrf(req)
    from community import _json, clean_text
    d = await _json(req)
    act = d.get("action")
    mapping = {"approve": "approved", "reject": "rejected", "mark-paid": "paid", "mark-delivered": "delivered"}
    if act not in mapping:
        raise HTTPException(400, "未知操作")
    new_status = mapping[act]
    allowed = {
        "approve": {"pending_review"},
        "reject": {"pending_review", "approved"},
        "mark-paid": {"approved"},
        "mark-delivered": {"paid", "approved"},
    }
    note = d.get("note") or d.get("reason")
    note = clean_text(note, 100, "备注", 1) if note else None
    with db() as conn:
        need_admin(conn, req)
        row = q(conn, "SELECT * FROM c_chicken_orders WHERE id=%s FOR UPDATE", (oid,), one=True)
        if not row:
            raise HTTPException(404, "找不到订单")
        if row["status"] not in allowed[act]:
            raise HTTPException(400, "当前状态不能做这个操作")
        row = q(conn, """UPDATE c_chicken_orders SET status=%s, admin_note=COALESCE(%s, admin_note), updated_at=now()
                         WHERE id=%s RETURNING *""", (new_status, note, oid), one=True)
    return {"ok": True, "order": _order_out(row, admin=True)}


@router.get("/chicken")
def p_chicken():
    return _page("chicken.html", "/chicken")
