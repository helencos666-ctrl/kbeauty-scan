"""Startup seed: insert the 蓝憬通讯 merchant listing as a pending post.

Idempotent and safe to re-run. Never prints DATABASE_URL or other secrets.
If the author is missing or not an active merchant, the insert is skipped
and the app continues to start.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

TITLE = "蓝憬通讯：推荐2位好友注册免费领韩国手机卡"
BOARD = "news"
NICKNAME = "韩国通讯"
REVIEW_NOTE = "商家「蓝憬通讯」通讯服务稿，待站长审核后公开。"
BODY = """蓝憬通讯面向持旅游签证来韩国的中国游客，提供韩国本地手机卡办理。建议在出发前完成准备和核实，抵达后即可使用。

【服务内容】
- 符合条件的中国游客可申请免费领取韩国本地手机卡（需完成下方活动）。
- 套餐可用于韩国境内的流量和通话。商家说明为无限流量、无限通话，具体以实际套餐政策为准。

【办理要求】
1. 提供清晰的护照扫描件（个人信息页）
2. 须提前完成本人身份核实
3. 审核通过后开通韩国手机卡

护照等资料由商家审核，平台不收集。本站不代收护照或其他证件资料，也不直接办理开卡。

【办理流程】
1. 推荐 2 位好友注册旅韩助手
2. 在旅韩助手点「咨询办理」，联系商家
3. 商家进行身份核实
4. 开通手机卡，落地使用

【活动规则】
推荐 2 位好友注册旅韩助手，即可免费领取韩国手机卡。

好友需完成注册。是否符合领取条件，以商家核实为准。套餐内容具体以实际套餐政策为准。

【说明】
- 本服务由商家蓝憬通讯提供，审核与开通均由商家负责。旅韩助手仅提供信息展示和站内联系入口。
- 请不要在本站上传或提交护照扫描件；办理所需资料请按商家指引直接提交给商家。
"""
POSTER_NAME = "poster-lanjing.png"
AD_PATH = "/ads/lanjing"

router = APIRouter()


def poster_path():
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "static", "home-ads", POSTER_NAME)


def pick_author(users):
    """Return (user, skip_reason). skip_reason is None when the user is usable."""
    if len(users) != 1:
        return None, "author missing or not unique"
    u = users[0]
    if u.get("is_banned"):
        return None, "author is banned"
    if not u.get("merchant_active"):
        return None, "author is not an active merchant"
    return u, None


def _log(msg):
    # Keep messages short and free of connection strings or secrets.
    print("lanjing seed: " + msg, flush=True)


def find_post(conn, q):
    return q(
        conn,
        """SELECT p.id, p.status
           FROM c_posts p JOIN c_users u ON u.id=p.user_id
           WHERE u.nickname=%s AND p.title=%s AND NOT p.deleted
           ORDER BY p.id DESC LIMIT 1""",
        (NICKNAME, TITLE),
        one=True,
    )


def seed_once():
    """Insert the pending listing once. Never raises to the caller."""
    try:
        if len(TITLE) > 40 or len(BODY) > 3000:
            _log("skipped: title or body too long")
            return
        path = poster_path()
        if not os.path.isfile(path):
            _log("skipped: poster file missing")
            return
        from community import _db_url, db, q, process_image
        if not _db_url():
            _log("skipped: database not configured")
            return
        raw = open(path, "rb").read()
        data, thumb, w, h = process_image(raw)
        with db() as conn:
            users = q(
                conn,
                """SELECT id, nickname, is_banned,
                          (merchant_until IS NOT NULL AND merchant_until > now()) AS merchant_active
                   FROM c_users WHERE nickname=%s""",
                (NICKNAME,),
            )
            u, reason = pick_author(users or [])
            if reason:
                _log("skipped: " + reason)
                return
            existing = q(
                conn,
                """SELECT id, status, deleted FROM c_posts
                   WHERE user_id=%s AND title=%s
                   ORDER BY id DESC""",
                (u["id"], TITLE),
            )
            if existing:
                row = existing[0]
                _log("exists: id=%s status=%s deleted=%s" % (row["id"], row["status"], row["deleted"]))
                return
            post = q(
                conn,
                """INSERT INTO c_posts(user_id, board, title, body, status, is_merchant, quota_kind, review_note)
                   VALUES (%s, %s, %s, %s, 'pending', TRUE, NULL, %s)
                   RETURNING id, status, board""",
                (u["id"], BOARD, TITLE, BODY, REVIEW_NOTE),
                one=True,
            )
            q(
                conn,
                """INSERT INTO c_images(post_id, pos, data, thumb, width, height)
                   VALUES (%s, 0, %s, %s, %s, %s)""",
                (post["id"], data, thumb, w, h),
            )
            _log("inserted: id=%s status=%s" % (post["id"], post["status"]))
    except HTTPException:
        _log("skipped: database unavailable")
    except Exception:
        # Do not log the exception text: drivers may include the DSN.
        _log("failed: unexpected error")


@router.get("/ads/lanjing")
def ads_lanjing():
    """Stable ad target: redirect to the merchant post when it exists."""
    try:
        from community import _db_url, db, q
        if not _db_url():
            return RedirectResponse("/community", status_code=302)
        with db() as conn:
            row = find_post(conn, q)
        if row:
            return RedirectResponse("/community/post/%s" % row["id"], status_code=302)
    except Exception:
        pass
    return RedirectResponse("/community", status_code=302)
