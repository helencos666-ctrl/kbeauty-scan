"""已合作入驻、但不走 c_providers 申请表的商家（目前只有 蓝憬通讯）。

数据写在代码里，只读展示，不写数据库，不改动任何用户数据。
商家介绍沿用商家自己提供、已在站内公开过的文案（lanjing_seed.BODY）。
公开页面不出现微信/电话/二维码，联系一律走站内咨询（kind=partner）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from lanjing_seed import BODY as LANJING_BODY, NICKNAME as LANJING_NICK, POSTER_NAME as LANJING_POSTER

router = APIRouter()

PARTNERS = [
    {
        "id": 1,
        "slug": "lanjing",
        "category": "sim",
        "category_name": "通讯电话卡",
        "name": "蓝憬通讯",
        "area": "韩国 · 持旅游签证的中国游客",
        "services": "韩国本地手机卡办理：推荐 2 位好友注册旅韩助手，可免费领取韩国手机卡。套餐内容以商家实际政策为准。",
        "body": LANJING_BODY,
        "photos": ["/static/home-ads/ad-4.jpg?v=97", "/static/home-ads/" + LANJING_POSTER],
        "author_nickname": LANJING_NICK,
    },
]
BY_SLUG = {p["slug"]: p for p in PARTNERS}
BY_ID = {p["id"]: p for p in PARTNERS}


def _out(p, full=False):
    o = {k: p[k] for k in ("id", "slug", "category", "category_name", "name", "area", "services", "photos")}
    o["photo"] = p["photos"][0] if p["photos"] else ""
    o["href"] = "/shops/m/" + p["slug"]
    o["consult_kind"] = "partner"
    if full:
        o["body"] = p["body"]
    return o


def target(conn, q, pid):
    """站内咨询目标：(merchant_user_id 或 None=平台客服, ref_title)。"""
    p = BY_ID.get(pid)
    if not p:
        raise HTTPException(404, "商家不存在")
    mid = None
    try:
        r = q(conn, """SELECT id FROM c_users WHERE nickname=%s AND NOT is_banned
                       AND merchant_until IS NOT NULL AND merchant_until>now()""", (p["author_nickname"],))
        if r and len(r) == 1:
            mid = r[0]["id"]
    except Exception:
        mid = None
    return mid, "商家：" + p["name"]


@router.get("/api/shops/partners")
def api_partners(cat: str = ""):
    cat = (cat or "").strip()
    items = [_out(p) for p in PARTNERS if not cat or cat == "all" or p["category"] == cat]
    return {"items": items}


@router.get("/api/shops/partners/{slug}")
def api_partner(slug: str):
    p = BY_SLUG.get(slug)
    if not p:
        raise HTTPException(404, "商家不存在")
    return {"item": _out(p, full=True)}


@router.get("/shops/m/{slug}")
def p_partner(slug: str):
    from community import _page
    p = BY_SLUG.get(slug)
    if not p:
        raise HTTPException(404, "商家不存在")
    extra = {"title": "%s · %s · 旅韩助手" % (p["name"], p["category_name"]),
             "description": "%s：%s" % (p["name"], p["services"][:80]), "index": True}
    return _page("provider.html", "/shops/m/" + slug, extra=extra)
