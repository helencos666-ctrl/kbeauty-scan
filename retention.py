"""韩友圈留存：站内通知、官方号（置顶/加精/每周话题/官方回复）、发帖评论奖励旅行豆、连续签到加成、用户徽章、个人主页

数据库只做加法。
"""
from __future__ import annotations

import re, secrets, hashlib

from fastapi import APIRouter, HTTPException, Request

from community import (
    BOARDS, LOGIN_ONLY_BOARDS, MERCHANT_BOARDS, TIER_NAMES, _json, _page, _ts, clean_text, csrf, current_user, db,
    limit, need_admin, need_user, q,
)

router = APIRouter()

MIGRATE_RETENTION = """
ALTER TABLE c_users ADD COLUMN IF NOT EXISTS is_official BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE c_posts ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE c_posts ADD COLUMN IF NOT EXISTS pinned_at TIMESTAMPTZ;
ALTER TABLE c_posts ADD COLUMN IF NOT EXISTS featured BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE c_posts ADD COLUMN IF NOT EXISTS featured_at TIMESTAMPTZ;
ALTER TABLE c_posts ADD COLUMN IF NOT EXISTS topic BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS c_posts_featured_idx ON c_posts(featured_at DESC) WHERE featured AND NOT deleted;
CREATE INDEX IF NOT EXISTS c_posts_pinned_idx ON c_posts(pinned_at DESC) WHERE pinned AND NOT deleted;
CREATE TABLE IF NOT EXISTS c_notifications (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  actor_id BIGINT REFERENCES c_users(id) ON DELETE SET NULL,
  post_id BIGINT,
  text TEXT NOT NULL,
  link TEXT,
  read_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS c_notifications_user_idx ON c_notifications(user_id, id DESC);
CREATE INDEX IF NOT EXISTS c_notifications_unread_idx ON c_notifications(user_id) WHERE read_at IS NULL;
CREATE TABLE IF NOT EXISTS c_badges (
  user_id BIGINT NOT NULL REFERENCES c_users(id) ON DELETE CASCADE,
  badge TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'auto',
  note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, badge)
);
CREATE UNIQUE INDEX IF NOT EXISTS c_points_featured_uidx ON c_points_ledger(item_key) WHERE kind = 'featured' AND item_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS c_points_first_reply_uidx ON c_points_ledger(item_key) WHERE kind = 'first_reply' AND item_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS c_points_post_uidx ON c_points_ledger(item_key) WHERE kind = 'post' AND item_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS c_points_streak_uidx ON c_points_ledger(user_id, day) WHERE kind = 'streak_bonus';
"""

# ---------------- 徽章 ----------------
BADGES = {
    # 自动授予
    "first_post": {"name": "首帖", "icon": "✍️", "desc": "发布了第一篇帖子", "auto": True},
    "posts10": {"name": "笔耕不辍", "icon": "📝", "desc": "累计发帖 10 篇", "auto": True},
    "featured_author": {"name": "精选作者", "icon": "🌟", "desc": "有帖子被加精", "auto": True},
    "streak7": {"name": "连续签到7天", "icon": "🔥", "desc": "连续签到 7 天", "auto": True},
    "streak30": {"name": "连续签到30天", "icon": "🏆", "desc": "连续签到 30 天", "auto": True},
    "helper": {"name": "热心回答", "icon": "💬", "desc": "累计评论 20 条", "auto": True},
    "daily_star": {"name": "在韩达人", "icon": "🇰🇷", "desc": "在「在韩日常」发帖 10 篇", "auto": True},
    # 管理员手动授予
    "official": {"name": "官方认证", "icon": "✅", "desc": "旅韩助手官方认证", "auto": False},
    "good_merchant": {"name": "优质商家", "icon": "🏅", "desc": "平台认定的优质商家", "auto": False},
}
HELPER_COMMENTS = 20
DAILY_STAR_POSTS = 10


def grant_badge(conn, uid, badge, source="auto", note=None):
    if badge not in BADGES:
        raise HTTPException(400, "徽章不存在")
    r = q(conn, """INSERT INTO c_badges(user_id,badge,source,note) VALUES (%s,%s,%s,%s)
                   ON CONFLICT DO NOTHING RETURNING badge""", (uid, badge, source, note), one=True)
    if r:
        b = BADGES[badge]
        notify(conn, uid, "badge", text="恭喜获得徽章「%s %s」：%s" % (b["icon"], b["name"], b["desc"]), link="/community/u/%d" % uid)
    return bool(r)


def check_badges(conn, uid, what=("posts", "comments")):
    try:
        if "posts" in what:
            r = q(conn, """SELECT count(*) AS n, count(*) FILTER (WHERE board='daily') AS d FROM c_posts
                           WHERE user_id=%s AND NOT deleted AND status='published'""", (uid,), one=True)
            if r["n"] >= 1:
                grant_badge(conn, uid, "first_post")
            if r["n"] >= 10:
                grant_badge(conn, uid, "posts10")
            if r["d"] >= DAILY_STAR_POSTS:
                grant_badge(conn, uid, "daily_star")
        if "comments" in what:
            n = q(conn, "SELECT count(*) AS n FROM c_comments WHERE user_id=%s AND NOT deleted", (uid,), one=True)["n"]
            if n >= HELPER_COMMENTS:
                grant_badge(conn, uid, "helper")
    except HTTPException:
        pass


def badges_out(csv):
    out = []
    for k in (csv or "").split(","):
        if k in BADGES:
            out.append(k)
    return out


# ---------------- 通知 ----------------
def notify(conn, uid, kind, actor_id=None, post_id=None, text="", link=None, dedupe=False):
    if not uid or (actor_id and actor_id == uid):
        return
    if dedupe and q(conn, """SELECT 1 AS x FROM c_notifications WHERE user_id=%s AND kind=%s AND actor_id IS NOT DISTINCT FROM %s
                             AND post_id IS NOT DISTINCT FROM %s AND created_at > now() - interval '7 days'""",
                    (uid, kind, actor_id, post_id), one=True):
        return
    q(conn, "INSERT INTO c_notifications(user_id,kind,actor_id,post_id,text,link) VALUES (%s,%s,%s,%s,%s,%s)",
      (uid, kind, actor_id, post_id, text[:200], link))


def notif_unread(conn, uid):
    return q(conn, "SELECT count(*) AS n FROM c_notifications WHERE user_id=%s AND read_at IS NULL", (uid,), one=True)["n"]


def _short(t, n=18):
    t = (t or "").strip()
    return t[:n] + ("…" if len(t) > n else "")


# 社区事件钩子（在 community.py 的接口里调用；任何异常都不影响主流程）
def on_post_published(conn, uid, pid, board):
    try:
        from points import _award
        if not is_official_uid(conn, uid):
            _award(conn, uid, "post", item_key="post:%d" % pid, note="发帖奖励")
        check_badges(conn, uid, ("posts",))
    except Exception:
        pass


def on_comment(conn, user, post, cid, body):
    try:
        if post["user_id"] != user["id"]:
            notify(conn, post["user_id"], "comment", user["id"], post["id"],
                   "%s 评论了你的帖子「%s」：%s" % (user["nickname"], _short(post.get("title")), _short(body, 30)),
                   "/community/post/%d#c%d" % (post["id"], cid))
        # 同一帖子里被 @ 的人
        for nick in list(dict.fromkeys(re.findall(r"@([^\s@，,。:：]{1,20})", body or "")))[:3]:
            r = q(conn, """SELECT DISTINCT c.user_id FROM c_comments c JOIN c_users u ON u.id=c.user_id
                           WHERE c.post_id=%s AND u.nickname=%s AND NOT c.deleted LIMIT 1""", (post["id"], nick), one=True)
            if r and r["user_id"] not in (user["id"], post["user_id"]):
                notify(conn, r["user_id"], "reply", user["id"], post["id"],
                       "%s 回复了你：%s" % (user["nickname"], _short(body, 30)), "/community/post/%d#c%d" % (post["id"], cid))
        from points import _award
        if not is_official_uid(conn, user["id"]):
            _award(conn, user["id"], "comment", note="评论奖励")
        if post["user_id"] != user["id"]:
            others = q(conn, "SELECT count(*) AS n FROM c_comments WHERE post_id=%s AND user_id<>%s AND NOT deleted",
                       (post["id"], post["user_id"]), one=True)["n"]
            if others == 1:
                _award(conn, post["user_id"], "first_reply", item_key="post:%d" % post["id"], note="帖子收到第一条回复")
        check_badges(conn, user["id"], ("comments",))
    except Exception:
        pass


def on_like(conn, user, pid):
    try:
        p = q(conn, "SELECT user_id,title FROM c_posts WHERE id=%s", (pid,), one=True)
        if p:
            notify(conn, p["user_id"], "like", user["id"], pid, "%s 赞了你的帖子「%s」" % (user["nickname"], _short(p["title"])),
                   "/community/post/%d" % pid, dedupe=True)
    except Exception:
        pass


def on_follow(conn, user, uid):
    try:
        notify(conn, uid, "follow", user["id"], None, "%s 关注了你" % user["nickname"], "/community/u/%d" % user["id"], dedupe=True)
    except Exception:
        pass


# ---------------- 连续签到加成 ----------------
STREAK_BONUS = {3: 5, 7: 20, 14: 30, 30: 60}
CHECKIN_NEXT = [{"label": "去看本周话题", "href": "/community?tab=topic"},
                {"label": "用一句话记录今天", "href": "/community/new?board=daily"}]


def after_checkin(conn, uid, streak):
    """签到成功后：连续 3/7/14/30 天额外奖励；7/30 天授予徽章"""
    out = {}
    bonus = STREAK_BONUS.get(int(streak or 0))
    if bonus:
        from points import kst_today
        r = q(conn, """INSERT INTO c_points_ledger(user_id,kind,points,day,note) VALUES (%s,'streak_bonus',%s,%s,%s)
                       ON CONFLICT DO NOTHING RETURNING id""", (uid, bonus, kst_today(), "连续签到 %d 天奖励" % streak), one=True)
        if r:
            out["streak_bonus"] = bonus
    if streak >= 7:
        grant_badge(conn, uid, "streak7")
    if streak >= 30:
        grant_badge(conn, uid, "streak30")
    out["next_actions"] = CHECKIN_NEXT
    return out


# ---------------- 官方号 ----------------
OFFICIAL_NICK = "旅韩助手·平台精选"
OFFICIAL_NICK_OLD = "韩友圈小编"


def is_official_uid(conn, uid):
    r = q(conn, "SELECT is_official FROM c_users WHERE id=%s", (uid,), one=True)
    return bool(r and r["is_official"])


def official_user(conn):
    r = q(conn, "SELECT id, nickname FROM c_users WHERE is_official AND NOT is_banned ORDER BY id LIMIT 1", one=True)
    if r:
        if r["nickname"] != OFFICIAL_NICK:
            taken = q(conn, "SELECT id FROM c_users WHERE nickname=%s AND id<>%s", (OFFICIAL_NICK, r["id"]), one=True)
            if not taken:
                q(conn, "UPDATE c_users SET nickname=%s WHERE id=%s AND is_official", (OFFICIAL_NICK, r["id"]))
        return r["id"]
    pw = hashlib.sha256(secrets.token_bytes(32)).hexdigest()   # 不可登录的随机口令，官方号只从后台发帖
    r = q(conn, """INSERT INTO c_users(nickname,pw_hash,email,is_official,tier,is_member)
                   VALUES (%s,%s,%s,TRUE,2,TRUE) RETURNING id""", (OFFICIAL_NICK, "!" + pw, "official@lvhanzhushou.invalid"), one=True)
    from community import assign_member_no
    assign_member_no(conn, r["id"])
    try:
        grant_badge(conn, r["id"], "official", "admin")
    except Exception:
        pass
    return r["id"]


# ---------------- 用户接口 ----------------
@router.get("/api/notifications")
def api_notifications(req: Request, before: int = 0, mark: int = 1):
    with db() as conn:
        u = need_user(conn, req)
        rows = q(conn, """SELECT n.*, au.nickname AS actor_name FROM c_notifications n LEFT JOIN c_users au ON au.id=n.actor_id
                          WHERE n.user_id=%s AND (%s=0 OR n.id<%s) ORDER BY n.id DESC LIMIT 30""", (u["id"], before, before))
        unread = notif_unread(conn, u["id"])
        if mark and rows:
            q(conn, "UPDATE c_notifications SET read_at=now() WHERE user_id=%s AND read_at IS NULL AND id<=%s", (u["id"], rows[0]["id"]))
    return {"items": [{"id": r["id"], "kind": r["kind"], "text": r["text"], "link": r["link"] or "", "actor_id": r["actor_id"],
                       "time": _ts(r["created_at"]), "unread": r["read_at"] is None} for r in rows],
            "unread": unread, "more": len(rows) == 30}


@router.get("/api/notifications/unread")
def api_notif_unread(req: Request):
    with db() as conn:
        u = current_user(conn, req)
        return {"n": notif_unread(conn, u["id"]) if u else 0}


@router.post("/api/notifications/read")
def api_notif_read(req: Request):
    csrf(req)
    with db() as conn:
        u = need_user(conn, req)
        q(conn, "UPDATE c_notifications SET read_at=now() WHERE user_id=%s AND read_at IS NULL", (u["id"],))
    return {"ok": True}


@router.get("/api/badges/meta")
def api_badges_meta():
    return {"badges": BADGES}


@router.get("/api/community/topics")
def api_topics(req: Request):
    """本周话题 + 置顶帖（社区首页顶部）"""
    from community import _post_out, AUTHOR_COLS
    with db() as conn:
        u = current_user(conn, req)
        tier = u["tier"] if u else 0
        base = f"""SELECT p.*, {AUTHOR_COLS}, (CASE WHEN au.merchant_until>now() THEN 3 ELSE au.tier END) AS tier,
                   (au.avatar IS NOT NULL OR COALESCE(au.is_bot, FALSE)) AS has_avatar,
                   (SELECT id FROM c_images i WHERE i.post_id=p.id ORDER BY pos LIMIT 1) AS cover,
                   (SELECT count(*) FROM c_images i WHERE i.post_id=p.id) AS img_n
                   FROM c_posts p JOIN c_users au ON au.id=p.user_id
                   WHERE NOT p.deleted AND p.status='published' AND NOT au.is_banned
                   {'' if tier >= 1 else "AND p.board <> ALL(%(lo)s)"} """
        args = {"lo": list(LOGIN_ONLY_BOARDS)}
        topics = q(conn, base + " AND p.topic AND p.created_at > now() - interval '14 days' ORDER BY p.id DESC LIMIT 3", args)
        pinned = q(conn, base + " AND p.pinned AND NOT p.topic ORDER BY p.pinned_at DESC NULLS LAST LIMIT 5", args)

    def out(rows):
        res = []
        for r in rows:
            o = _post_out(r); o["cover"] = r["cover"]; o["img_n"] = r["img_n"]; res.append(o)
        return res
    return {"topics": out(topics), "pinned": out(pinned)}


FRIEND_BOARDS = ("friends", "group")


@router.get("/api/community/friends-latest")
def api_friends_latest():
    """首页「交友 · 最新」：旅韩交友、结伴同行最新 3 帖（只给标题和时间；正文仍按会员规则查看）"""
    with db() as conn:
        rows = q(conn, """SELECT p.id,p.board,p.title,p.comment_count,p.created_at FROM c_posts p JOIN c_users au ON au.id=p.user_id
                          WHERE NOT p.deleted AND p.status='published' AND NOT au.is_banned AND p.board = ANY(%s)
                          ORDER BY p.id DESC LIMIT 3""", (list(FRIEND_BOARDS),))
    return {"posts": [{"id": r["id"], "title": r["title"], "board_name": BOARDS.get(r["board"], ""), "comments": r["comment_count"],
                       "time": _ts(r["created_at"])} for r in rows]}


@router.get("/api/community/users/{uid}")
def api_user_profile(req: Request, uid: int):
    from community import _post_out, AUTHOR_COLS
    with db() as conn:
        me = current_user(conn, req)
        u = q(conn, """SELECT id,nickname,member_no,tier,merchant_until,is_official,verified,created_at,
                              (avatar IS NOT NULL OR COALESCE(is_bot,FALSE)) AS has_avatar, COALESCE(is_bot,FALSE) AS is_bot
                       FROM c_users WHERE id=%s AND NOT is_banned""", (uid,), one=True)
        if not u:
            raise HTTPException(404, "用户不存在")
        bs = q(conn, "SELECT badge,source,created_at FROM c_badges WHERE user_id=%s ORDER BY created_at", (uid,))
        st = q(conn, """SELECT (SELECT count(*) FROM c_posts WHERE user_id=%s AND NOT deleted AND status='published') AS posts,
                               (SELECT count(*) FROM c_posts WHERE user_id=%s AND NOT deleted AND status='published' AND featured) AS featured,
                               (SELECT count(*) FROM c_follows WHERE followee_id=%s) AS followers,
                               (SELECT count(*) FROM c_comments WHERE user_id=%s AND NOT deleted) AS comments""", (uid, uid, uid, uid), one=True)
        tier = me["tier"] if me else 0
        rows = q(conn, f"""SELECT p.*, {AUTHOR_COLS}, (CASE WHEN au.merchant_until>now() THEN 3 ELSE au.tier END) AS tier,
                           (au.avatar IS NOT NULL OR COALESCE(au.is_bot, FALSE)) AS has_avatar,
                           (SELECT id FROM c_images i WHERE i.post_id=p.id ORDER BY pos LIMIT 1) AS cover,
                           (SELECT count(*) FROM c_images i WHERE i.post_id=p.id) AS img_n
                           FROM c_posts p JOIN c_users au ON au.id=p.user_id
                           WHERE p.user_id=%s AND NOT p.deleted AND p.status='published' {'' if tier >= 1 else 'AND p.board <> ALL(%s)'}
                           ORDER BY p.id DESC LIMIT 20""", (uid,) if tier >= 1 else (uid, list(LOGIN_ONLY_BOARDS)))
        following = bool(me and q(conn, "SELECT 1 AS x FROM c_follows WHERE follower_id=%s AND followee_id=%s", (me["id"], uid), one=True))
    import datetime
    disp = 3 if (u["merchant_until"] and u["merchant_until"] > datetime.datetime.now(datetime.timezone.utc)) else u["tier"]
    posts = []
    for r in rows:
        o = _post_out(r); o["cover"] = r["cover"]; o["img_n"] = r["img_n"]; posts.append(o)
    return {"id": u["id"], "nickname": u["nickname"], "member_no": u["member_no"] or u["id"], "tier": disp, "tier_name": TIER_NAMES.get(disp, ""),
            "official": bool(u["is_official"]), "verified": bool(u["verified"]), "has_avatar": bool(u["has_avatar"]),
            "is_bot": bool(u["is_bot"]),
            "joined": _ts(u["created_at"]), "stats": st, "following": following, "mine": bool(me and me["id"] == uid),
            "badges": [dict(BADGES[b["badge"]], key=b["badge"], time=_ts(b["created_at"])) for b in bs if b["badge"] in BADGES],
            "posts": posts}


# ---------------- 管理员 ----------------
@router.post("/api/admin/posts/{pid}/flags")
async def api_admin_post_flags(req: Request, pid: int):
    """置顶 / 加精 / 每周话题。加精第一次时奖励作者旅行豆、发通知、授予「精选作者」徽章"""
    csrf(req)
    d = await _json(req)
    with db() as conn:
        need_admin(conn, req)
        p = q(conn, "SELECT id,user_id,title,pinned,featured,topic,status FROM c_posts WHERE id=%s AND NOT deleted FOR UPDATE", (pid,), one=True)
        if not p:
            raise HTTPException(404, "帖子不存在")
        out = {"ok": True}
        if "pinned" in d:
            on = bool(d["pinned"])
            q(conn, "UPDATE c_posts SET pinned=%s, pinned_at=CASE WHEN %s THEN now() ELSE pinned_at END WHERE id=%s", (on, on, pid))
            if on and not p["pinned"]:
                notify(conn, p["user_id"], "pinned", None, pid, "你的帖子「%s」被置顶啦" % _short(p["title"]), "/community/post/%d" % pid)
        if "topic" in d:
            q(conn, "UPDATE c_posts SET topic=%s WHERE id=%s", (bool(d["topic"]), pid))
        if "featured" in d:
            on = bool(d["featured"])
            q(conn, "UPDATE c_posts SET featured=%s, featured_at=CASE WHEN %s THEN now() ELSE featured_at END WHERE id=%s", (on, on, pid))
            if on and not p["featured"]:
                from points import _award, POINT_RULES
                aw = {"awarded": 0}
                if not is_official_uid(conn, p["user_id"]):
                    aw = _award(conn, p["user_id"], "featured", item_key="post:%d" % pid, note="帖子被加精")
                notify(conn, p["user_id"], "featured", None, pid,
                       "你的帖子「%s」被加精%s" % (_short(p["title"]), ("，获得 %d 旅行豆" % aw["awarded"]) if aw.get("awarded") else ""),
                       "/community/post/%d" % pid)
                grant_badge(conn, p["user_id"], "featured_author")
                out["awarded"] = aw.get("awarded", 0)
        r = q(conn, "SELECT pinned,featured,topic FROM c_posts WHERE id=%s", (pid,), one=True)
        out.update(r)
    return out


@router.post("/api/admin/official-post")
async def api_admin_official_post(req: Request):
    csrf(req)
    d = await _json(req)
    board = d.get("board") or "daily"
    if board not in BOARDS or board in MERCHANT_BOARDS:
        raise HTTPException(400, "请选择频道")
    title = clean_text(d.get("title"), 40, "标题", 2)
    body = clean_text(d.get("body"), 3000, "正文", 2)
    with db() as conn:
        need_admin(conn, req)
        oid = official_user(conn)
        r = q(conn, """INSERT INTO c_posts(user_id,board,title,body,status,is_merchant,pinned,pinned_at,topic)
                       VALUES (%s,%s,%s,%s,'published',FALSE,%s,CASE WHEN %s THEN now() END,%s) RETURNING id""",
              (oid, board, title, body, bool(d.get("pinned")), bool(d.get("pinned")), bool(d.get("topic"))), one=True)
    return {"ok": True, "id": r["id"]}


@router.post("/api/admin/official-comment")
async def api_admin_official_comment(req: Request):
    csrf(req)
    d = await _json(req)
    body = clean_text(d.get("body"), 500, "评论", 1)
    try:
        pid = int(d.get("post_id") or 0)
    except (TypeError, ValueError):
        pid = 0
    with db() as conn:
        need_admin(conn, req)
        p = q(conn, "SELECT id,user_id,title,board FROM c_posts WHERE id=%s AND NOT deleted AND status='published'", (pid,), one=True)
        if not p:
            raise HTTPException(404, "帖子不存在")
        oid = official_user(conn)
        r = q(conn, "INSERT INTO c_comments(post_id,user_id,body) VALUES (%s,%s,%s) RETURNING id", (pid, oid, body), one=True)
        q(conn, "UPDATE c_posts SET comment_count=(SELECT count(*) FROM c_comments WHERE post_id=%s AND NOT deleted) WHERE id=%s", (pid, pid))
        on_comment(conn, {"id": oid, "nickname": OFFICIAL_NICK}, p, r["id"], body)
    return {"ok": True, "id": r["id"]}


@router.get("/api/admin/unanswered")
def api_admin_unanswered(req: Request):
    """近 7 天还没有任何人（作者以外）回复的帖子：官方号逐条首回"""
    with db() as conn:
        need_admin(conn, req)
        rows = q(conn, """SELECT p.id,p.board,p.title,p.body,p.created_at,au.nickname FROM c_posts p JOIN c_users au ON au.id=p.user_id
                          WHERE NOT p.deleted AND p.status='published' AND p.created_at > now() - interval '7 days'
                          AND NOT au.is_official AND NOT COALESCE(au.is_bot,FALSE)
                          AND NOT EXISTS (SELECT 1 FROM c_comments c JOIN c_users cu ON cu.id=c.user_id
                                          WHERE c.post_id=p.id AND c.user_id<>p.user_id AND NOT c.deleted AND NOT COALESCE(cu.is_bot,FALSE))
                          ORDER BY p.id DESC LIMIT 50""")
    return {"posts": [{"id": r["id"], "board_name": BOARDS.get(r["board"], ""), "title": r["title"], "excerpt": _short(r["body"], 60),
                       "author": r["nickname"], "time": _ts(r["created_at"])} for r in rows]}


@router.get("/api/admin/badges")
def api_admin_badges(req: Request, user: str = ""):
    with db() as conn:
        need_admin(conn, req)
        out = {"badges": BADGES, "user": None}
        if user.strip():
            from merch import _resolve_user
            uid = _resolve_user(conn, user)
            u = q(conn, "SELECT id,nickname,member_no,is_official FROM c_users WHERE id=%s", (uid,), one=True)
            bs = q(conn, "SELECT badge,source,created_at FROM c_badges WHERE user_id=%s ORDER BY created_at", (uid,))
            out["user"] = {"id": u["id"], "nickname": u["nickname"], "member_no": u["member_no"], "official": u["is_official"],
                           "badges": [{"key": b["badge"], "source": b["source"], "time": _ts(b["created_at"])} for b in bs]}
    return out


@router.post("/api/admin/badges")
async def api_admin_badge_set(req: Request):
    csrf(req)
    d = await _json(req)
    badge = d.get("badge")
    if badge not in BADGES:
        raise HTTPException(400, "徽章不存在")
    with db() as conn:
        need_admin(conn, req)
        from merch import _resolve_user
        uid = _resolve_user(conn, d.get("user"))
        if not uid:
            raise HTTPException(400, "请填写会员编号")
        if d.get("action") == "revoke":
            q(conn, "DELETE FROM c_badges WHERE user_id=%s AND badge=%s", (uid, badge))
            if badge == "official":
                q(conn, "UPDATE c_users SET is_official=FALSE WHERE id=%s", (uid,))
        else:
            grant_badge(conn, uid, badge, "admin", (d.get("note") or "")[:100] or None)
            if badge == "official":
                q(conn, "UPDATE c_users SET is_official=TRUE WHERE id=%s", (uid,))
    return {"ok": True}


# ---------------- 页面 ----------------
@router.get("/community/notices")
def p_notices():
    return _page("notices.html", "/community/notices")


@router.get("/community/u/{uid}")
def p_user(uid: str):
    n = int(uid) if re.fullmatch(r"\d{1,12}", uid or "") else 0
    return _page("user.html", "/community/u/%d" % n)
