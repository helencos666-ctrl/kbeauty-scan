"""小程序专用只读接口（为下一个小程序版本准备；当前送审的 1.0.0 仍用本地演示数据，不受影响）。

- GET /api/mp/buddy/list?city=首尔&date=2026-12-01  → 真实「找搭子」帖子（网站 /dazi 同源数据，访客可见范围）
- GET /api/mp/merchants?board=hotel|car             → 真实商家专区帖子（隐藏医美）
字段与小程序 utils/api.js 注释里的约定一致。只输出网站上本来就公开显示的内容（昵称、标题、日期），不输出联系方式。
"""
from __future__ import annotations

import datetime

from fastapi import APIRouter, Request

router = APIRouter()
CITY_ZH2CODE = {"首尔": "seoul", "釜山": "busan", "济州": "jeju", "济州岛": "jeju"}
TIER_NAME = {0: "注册用户", 1: "会员", 2: "高级会员", 3: "商家"}


def _days(a, b):
    try:
        return max(1, (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days + 1)
    except Exception:  # noqa: BLE001
        return None


@router.get("/api/mp/buddy/list")
def mp_buddy_list(req: Request, city: str = "", date: str = "", people: int = 0):
    from dazi import api_list
    code = CITY_ZH2CODE.get((city or "").strip(), "")
    d = (date or "").strip()[:10]
    try:
        datetime.date.fromisoformat(d)
    except ValueError:
        d = ""
    res = api_list(req, tab="personal", city=code, kind="", date=d, gender="", kw="", offset=0, limit_n=30)
    items = []
    for p in res.get("posts") or []:
        m = p.get("dazi") or {}
        if p.get("expired") or m.get("expired"):
            continue
        total = int(m.get("people") or 0)
        items.append({"id": p["id"], "date": m.get("date_from") or (p.get("time") or "")[:10],
                      "days": _days(m.get("date_from"), m.get("date_to")) if m.get("date_to") else None,
                      "city": m.get("city_name") or p.get("area") or "", "people": 1,
                      "need": max(1, total - 1) if total else None, "theme": p.get("title") or p.get("excerpt") or "",
                      "style": [x for x in (m.get("kind_name"), m.get("gender_name") if m.get("gender") not in (None, "any") else None) if x],
                      "nick": p.get("author") or "", "tier": TIER_NAME.get(p.get("author_tier"), ""),
                      "url": "https://lvhanzhushou.com/community/post/%s" % p["id"]})
    return {"items": items}


@router.get("/api/mp/merchants")
def mp_merchants(req: Request, board: str = ""):
    from community import api_merchant_feed
    want = (board or "").strip()
    if want == "clinic":
        return {"items": [], "unavailable": True, "message": "该分类暂未开放"}
    res = api_merchant_feed(req, cat="", before=0, limit_n=30)
    items = []
    for p in res.get("posts") or []:
        b = p.get("board") or ""
        if want and b != want:
            continue
        if b == "clinic" or p.get("merchant_cat") == "clinic":
            continue
        items.append({"id": p["id"], "board": b, "cat": p.get("merchant_cat_name") or p.get("board_name") or "",
                      "name": p.get("title") or "", "area": p.get("area") or p.get("place") or "",
                      "price": p.get("offer") or "", "station": "", "lat": None, "lng": None,
                      "coupon": p.get("offer") or "", "valid_until": p.get("valid_until"),
                      "verified": bool(p.get("author_verified")), "nick": p.get("author") or "",
                      "url": "https://lvhanzhushou.com/community/post/%s" % p["id"]})
    return {"items": items}
