"""旅韩助手 · 注册会员一对一私信

复用交友私信表 c_threads / c_messages / c_blocks，不另起一套会话。
- 已登录且未封禁的注册用户可互发文字和图片
- 交友「打招呼」仍走 friends.py，保持高级会员门槛
- 普通帖子 / 作者主页「私聊」入口；商家帖仍只用「立即咨询」
- 拦截微信、电话、二维码等站外联系方式（与咨询相同，措辞更缓和）
- 诈骗词仍送达并提醒、自动进后台
- 每条消息可按需中韩翻译（Cloudflare Workers AI 免费额度，失败则提示，不阻塞发送）
"""
from __future__ import annotations

import os, re

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

import community as C
from community import (
    MERCHANT_BOARDS, MAX_IMG_BYTES, _json, _ts, clean_text, csrf, current_user,
    db, is_admin, limit, need_user, process_image, q,
)
from contact_guard import guard_images, guard_text
from friends import _names, _thread, blocked_between, scam_hits, _flag, _warning

router = APIRouter()

NEW_THREAD_DAILY = 10
MSG_DAILY_LIMIT = 300
MAX_UNANSWERED = 3
MSG_MAX = 500
TR_USER_DAY = 30
TR_SITE_DAY = 400
TR_TIMEOUT = 6.0
HANGUL = re.compile(r"[\uac00-\ud7a3]")

CHAT_GUARD_MSG = "为保护双方权益，请勿在私信中留微信、电话、二维码等站外联系方式，请通过站内私信沟通。"

MIGRATE_CHAT = """
ALTER TABLE c_threads ADD COLUMN IF NOT EXISTS ref_kind TEXT;
ALTER TABLE c_threads ADD COLUMN IF NOT EXISTS ref_id BIGINT;
ALTER TABLE c_threads ADD COLUMN IF NOT EXISTS ref_title TEXT;
ALTER TABLE c_threads ADD COLUMN IF NOT EXISTS last_text TEXT;
ALTER TABLE c_threads ADD COLUMN IF NOT EXISTS created_by BIGINT REFERENCES c_users(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS c_threads_created_by_idx ON c_threads(created_by, created_at DESC);
ALTER TABLE c_messages ADD COLUMN IF NOT EXISTS img BYTEA;
ALTER TABLE c_messages ADD COLUMN IF NOT EXISTS thumb BYTEA;
ALTER TABLE c_messages ADD COLUMN IF NOT EXISTS width INT;
ALTER TABLE c_messages ADD COLUMN IF NOT EXISTS height INT;
CREATE TABLE IF NOT EXISTS c_message_translations (
  message_id BIGINT NOT NULL REFERENCES c_messages(id) ON DELETE CASCADE,
  dir TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (message_id, dir)
);
"""

MSG_COLS = """id,sender_id,recipient_id,kind,body,flagged,flag_words,created_at,read_at,
              width,height,(img IS NOT NULL) AS has_img"""


def guard_chat_text(conn, uid, *texts):
    guard_text(conn, uid, "私信", *texts, msg=CHAT_GUARD_MSG)


def guard_chat_images(conn, uid, blobs):
    guard_images(conn, uid, "私信图片", blobs, msg=CHAT_GUARD_MSG)


def _preview(body, has_img=False, kind="msg"):
    if kind == "ref":
        return (body or "来自帖子")[:40]
    t = (body or "").strip()
    if has_img and not t:
        return "[图片]"
    if has_img:
        return "[图片] " + t[:36]
    return (t or "")[:40]


def _send(conn, tid, sender, recipient, body, kind="msg", img=None, thumb=None, w=None, h=None):
    if body:
        guard_chat_text(conn, sender, body)
    hits = scam_hits(body or "")
    r = q(conn, """INSERT INTO c_messages(thread_id,sender_id,recipient_id,kind,body,flagged,flag_words,img,thumb,width,height)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
          (tid, sender, recipient, kind, body or "", bool(hits), "、".join(hits[:5]) or None, img, thumb, w, h), one=True)
    q(conn, "UPDATE c_threads SET last_at=now(), last_text=%s WHERE id=%s",
      (_preview(body, bool(img), kind), tid))
    if hits and kind != "ref":
        _flag(conn, r["id"], hits)
    return r["id"], hits


def _peer_ok(conn, me_id, peer_id):
    if peer_id == me_id:
        raise HTTPException(400, "不能和自己发私信")
    p = q(conn, "SELECT id,is_banned,COALESCE(is_bot,FALSE) AS is_bot FROM c_users WHERE id=%s", (peer_id,), one=True)
    if not p or p["is_banned"]:
        raise HTTPException(404, "用户不存在")
    if p["is_bot"]:
        raise HTTPException(400, "不能给小助手发私信")
    i_blocked, they_blocked = blocked_between(conn, me_id, peer_id)
    if i_blocked:
        raise HTTPException(403, "你已拉黑对方，解除拉黑后才能发送")
    if they_blocked:
        raise HTTPException(403, "对方暂不接收你的消息")
    return p


def _get_or_create(conn, me_id, peer_id, ref=None):
    a, b = sorted((me_id, peer_id))
    th = q(conn, "SELECT * FROM c_threads WHERE user_a=%s AND user_b=%s", (a, b), one=True)
    if th:
        if ref and (th.get("ref_kind"), th.get("ref_id")) != (ref[0], ref[1]):
            q(conn, "UPDATE c_threads SET ref_kind=%s, ref_id=%s, ref_title=%s, last_at=now() WHERE id=%s",
              (ref[0], ref[1], ref[2], th["id"]))
            same = q(conn, """SELECT 1 AS x FROM c_messages WHERE thread_id=%s AND kind='ref' AND body=%s LIMIT 1""",
                     (th["id"], "来自帖子「%s」" % ref[2]), one=True)
            if not same:
                other = th["user_b"] if th["user_a"] == me_id else th["user_a"]
                _send(conn, th["id"], me_id, other, "来自帖子「%s」" % ref[2], kind="ref")
        return th["id"], False
    n = q(conn, "SELECT count(*) AS n FROM c_threads WHERE created_by=%s AND created_at>now()-interval '1 day'",
          (me_id,), one=True)["n"]
    if n >= NEW_THREAD_DAILY:
        raise HTTPException(429, "今天新开的会话已经够多了，明天再聊吧")
    th = q(conn, """INSERT INTO c_threads(user_a,user_b,created_by,ref_kind,ref_id,ref_title)
                    VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
          (a, b, me_id, ref[0] if ref else None, ref[1] if ref else None, ref[2] if ref else None), one=True)
    if ref:
        other = b if a == me_id else a
        _send(conn, th["id"], me_id, other, "来自帖子「%s」" % ref[2], kind="ref")
    return th["id"], True


def _check_send(conn, u, th):
    other = q(conn, "SELECT is_banned, COALESCE(is_bot,FALSE) AS is_bot FROM c_users WHERE id=%s", (th["other"],), one=True)
    if not other or other["is_banned"]:
        raise HTTPException(403, "对方账号已被封禁，无法发送")
    i_blocked, they_blocked = blocked_between(conn, u["id"], th["other"])
    if i_blocked:
        raise HTTPException(403, "你已拉黑对方，解除拉黑后才能发送")
    if they_blocked:
        raise HTTPException(403, "对方暂不接收你的消息")
    limit("msg:" + str(u["id"]), 20, 60, "发送太快了，请稍等")
    n = q(conn, "SELECT count(*) AS n FROM c_messages WHERE sender_id=%s AND kind<>'ref' AND created_at>now()-interval '1 day'",
          (u["id"],), one=True)["n"]
    if n >= MSG_DAILY_LIMIT:
        raise HTTPException(429, "今天发送的消息太多了，明天再聊吧")
    replied = q(conn, "SELECT 1 AS x FROM c_messages WHERE thread_id=%s AND sender_id=%s AND kind<>'ref' AND NOT deleted LIMIT 1",
                (th["id"], th["other"]), one=True)
    if not replied:
        mine = q(conn, "SELECT count(*) AS n FROM c_messages WHERE thread_id=%s AND sender_id=%s AND kind<>'ref' AND NOT deleted",
                 (th["id"], u["id"]), one=True)["n"]
        if mine >= MAX_UNANSWERED:
            raise HTTPException(403, f"对方回复前最多发送 {MAX_UNANSWERED} 条消息，请耐心等待")
    return th["other"]


def _msg_out(m, me_id, translations=None):
    o = {"id": m["id"], "mine": m["sender_id"] == me_id, "kind": m["kind"], "body": m["body"] or "",
         "flagged": m["flagged"], "time": _ts(m["created_at"]), "read": m["read_at"] is not None}
    if m.get("has_img"):
        o["img"] = "/api/chat/img/%d" % m["id"]
        o["thumb"] = "/api/chat/img/%d?t=1" % m["id"]
        o["w"], o["h"] = m.get("width"), m.get("height")
    if translations and m["id"] in translations:
        o["translations"] = translations[m["id"]]
    return o


@router.post("/api/chat/start")
async def api_start(req: Request):
    csrf(req)
    d = await _json(req)
    with db() as conn:
        u = need_user(conn, req)
        limit("chat-start:" + str(u["id"]), 20, 3600, "操作太频繁，请稍后再试")
        ref = None
        if d.get("post_id") not in (None, ""):
            try:
                pid = int(d.get("post_id"))
            except Exception:
                raise HTTPException(400, "帖子无效")
            p = q(conn, """SELECT p.id,p.title,p.user_id,p.board,p.status,p.deleted,p.is_merchant,
                                  u.is_banned, COALESCE(u.is_bot,FALSE) AS is_bot
                           FROM c_posts p JOIN c_users u ON u.id=p.user_id
                           WHERE p.id=%s""", (pid,), one=True)
            if not p or p["deleted"] or p["status"] != "published":
                raise HTTPException(404, "帖子不存在或已删除")
            if p["board"] in MERCHANT_BOARDS or p["is_merchant"]:
                raise HTTPException(400, "商家信息请使用「立即咨询」沟通")
            if p["user_id"] == u["id"]:
                raise HTTPException(400, "不能给自己的帖子发私信")
            if p["is_banned"] or p["is_bot"]:
                raise HTTPException(404, "用户不存在")
            peer_id = p["user_id"]
            title = (p["title"] or "")[:80]
            ref = ("post", p["id"], title)
        else:
            try:
                peer_id = int(d.get("peer_id"))
            except Exception:
                raise HTTPException(400, "请选择要私聊的会员")
        _peer_ok(conn, u["id"], peer_id)
        tid, _new = _get_or_create(conn, u["id"], peer_id, ref)
    return {"ok": True, "thread_id": tid}


@router.get("/api/chat/threads")
def api_threads(req: Request):
    with db() as conn:
        u = need_user(conn, req)
        rows = q(conn, """SELECT t.id, t.last_at, t.ref_kind, t.ref_id, t.ref_title,
                CASE WHEN t.user_a=%(me)s THEN t.user_b ELSE t.user_a END AS other,
                (SELECT body FROM c_messages m WHERE m.thread_id=t.id AND NOT m.deleted ORDER BY m.id DESC LIMIT 1) AS last_body,
                (SELECT kind FROM c_messages m WHERE m.thread_id=t.id AND NOT m.deleted ORDER BY m.id DESC LIMIT 1) AS last_kind,
                (SELECT img IS NOT NULL FROM c_messages m WHERE m.thread_id=t.id AND NOT m.deleted ORDER BY m.id DESC LIMIT 1) AS last_img,
                (SELECT count(*) FROM c_messages m WHERE m.thread_id=t.id AND m.recipient_id=%(me)s AND m.read_at IS NULL AND NOT m.deleted AND m.kind<>'ref') AS unread,
                EXISTS (SELECT 1 FROM c_messages m WHERE m.thread_id=t.id AND m.flagged AND NOT m.deleted) AS flagged
                FROM c_threads t WHERE t.user_a=%(me)s OR t.user_b=%(me)s ORDER BY t.last_at DESC LIMIT 100""", {"me": u["id"]})
        names = _names(conn, {r["other"] for r in rows})
        blocked = {r["blocked_id"] for r in q(conn, "SELECT blocked_id FROM c_blocks WHERE blocker_id=%s", (u["id"],))}
    out = []
    for r in rows:
        if r["last_body"] is None and not r["last_img"]:
            continue
        n = names.get(r["other"], {})
        out.append({"id": r["id"], "other": r["other"], "nickname": n.get("nick", "用户"), "verified": bool(n.get("verified")),
                    "has_avatar": bool(n.get("has_avatar")),
                    "cover": n.get("cover") if n.get("has_profile") else None,
                    "last": _preview(r["last_body"], bool(r["last_img"]), r["last_kind"] or "msg"),
                    "time": _ts(r["last_at"]), "unread": 0 if r["other"] in blocked else r["unread"], "flagged": r["flagged"],
                    "blocked": r["other"] in blocked, "banned": bool(n.get("is_banned"))})
    return {"threads": out, "unread": sum(t["unread"] for t in out)}


@router.get("/api/chat/threads/{tid}")
def api_thread(req: Request, tid: int, after: int = 0):
    after = max(0, int(after or 0))
    with db() as conn:
        u = need_user(conn, req)
        th = _thread(conn, tid, u["id"])
        q(conn, "UPDATE c_messages SET read_at=now() WHERE thread_id=%s AND recipient_id=%s AND read_at IS NULL", (tid, u["id"]))
        if after:
            rows = q(conn, f"SELECT {MSG_COLS} FROM c_messages WHERE thread_id=%s AND id>%s AND NOT deleted ORDER BY id LIMIT 300",
                     (tid, after))
        else:
            rows = q(conn, f"""SELECT * FROM (SELECT {MSG_COLS} FROM c_messages
                          WHERE thread_id=%s AND NOT deleted ORDER BY id DESC LIMIT 200) x ORDER BY id""", (tid,))
        n = _names(conn, {th["other"]}).get(th["other"], {})
        i_blocked, they_blocked = blocked_between(conn, u["id"], th["other"])
        mids = [m["id"] for m in rows]
        trans = {}
        if mids:
            for t in q(conn, "SELECT message_id,dir,text FROM c_message_translations WHERE message_id = ANY(%s)", (mids,)):
                trans.setdefault(t["message_id"], {})[t["dir"]] = t["text"]
        ref_href = None
        if th.get("ref_kind") == "post" and th.get("ref_id"):
            ref_href = "/community/post/%d" % th["ref_id"]
    return {"id": tid, "other": th["other"], "nickname": n.get("nick", "用户"), "verified": bool(n.get("verified")),
            "has_avatar": bool(n.get("has_avatar")),
            "cover": n.get("cover") if n.get("has_profile") else None, "has_profile": bool(n.get("has_profile")),
            "blocked": i_blocked, "banned": bool(n.get("is_banned")),
            "can_send": not i_blocked and not they_blocked and not n.get("is_banned"),
            "ref_kind": th.get("ref_kind"), "ref_id": th.get("ref_id"), "ref_title": th.get("ref_title") or "",
            "ref_href": ref_href,
            "messages": [_msg_out(m, u["id"], trans) for m in rows]}


@router.post("/api/chat/threads/{tid}/messages")
async def api_send(req: Request, tid: int):
    csrf(req)
    d = await _json(req)
    body = clean_text(d.get("body"), MSG_MAX, "消息")
    from msg_dedup import norm_cid, cid_get, cid_put
    cid = norm_cid(d.get("cid"))
    with db() as conn:
        u = need_user(conn, req)
        prev = cid_get("chat", u["id"], cid)
        if prev:
            return prev
        th = _thread(conn, tid, u["id"])
        other = _check_send(conn, u, th)
        if not cid:   # 旧客户端连点：几秒内同一内容只算一条
            dup = q(conn, """SELECT id FROM c_messages WHERE thread_id=%s AND sender_id=%s AND kind='msg' AND body=%s AND img IS NULL
                             AND created_at > now() - interval '5 seconds' ORDER BY id DESC LIMIT 1""", (tid, u["id"], body), one=True)
            if dup:
                return {"ok": True, "id": dup["id"], "warning": None, "dup": True}
        mid, hits = _send(conn, tid, u["id"], other, body, kind="msg")
    res = {"ok": True, "id": mid, "warning": _warning(hits)}
    cid_put("chat", u["id"], cid, dict(res, dup=True))
    return res


@router.post("/api/chat/threads/{tid}/images")
async def api_send_image(req: Request, tid: int, image: UploadFile = File(...), body: str = Form("")):
    csrf(req)
    caption = (body or "").replace("\x00", "").strip()
    if len(caption) > MSG_MAX:
        raise HTTPException(400, "说明最多 %d 个字" % MSG_MAX)
    raw = await image.read(MAX_IMG_BYTES + 1)
    with db() as conn:
        u = need_user(conn, req)
        th = _thread(conn, tid, u["id"])
        other = _check_send(conn, u, th)
        if caption:
            guard_chat_text(conn, u["id"], caption)
        guard_chat_images(conn, u["id"], [raw])
    big, thb, w, h = process_image(raw)
    with db() as conn:
        u = need_user(conn, req)
        th = _thread(conn, tid, u["id"])
        mid, hits = _send(conn, tid, u["id"], th["other"], caption, kind="msg", img=big, thumb=thb, w=w, h=h)
    return {"ok": True, "id": mid, "warning": _warning(hits)}


@router.get("/api/chat/img/{mid}")
def api_chat_img(req: Request, mid: int, t: int = 0):
    col = "thumb" if t else "img"
    with db() as conn:
        u = current_user(conn, req)
        admin = is_admin(conn, req)
        r = q(conn, f"""SELECT m.{col} AS d, m.sender_id, m.recipient_id
                        FROM c_messages m WHERE m.id=%s AND NOT m.deleted""", (mid,), one=True)
        if not r or r["d"] is None:
            raise HTTPException(404, "图片不存在")
        if not admin and (not u or u["id"] not in (r["sender_id"], r["recipient_id"])):
            raise HTTPException(404, "图片不存在")
    return Response(bytes(r["d"]), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/chat/unread")
def api_unread(req: Request):
    with db() as conn:
        u = current_user(conn, req)
        if not u:
            return {"unread": 0}
        n = q(conn, """SELECT count(*) AS n FROM c_messages m WHERE m.recipient_id=%s AND m.read_at IS NULL AND NOT m.deleted
                       AND m.kind<>'ref'
                       AND NOT EXISTS (SELECT 1 FROM c_blocks b WHERE b.blocker_id=%s AND b.blocked_id=m.sender_id)""",
              (u["id"], u["id"]), one=True)["n"]
    return {"unread": n}


def _cf_creds():
    a = (os.environ.get("CF_ACCOUNT_ID") or "").replace("\ufeff", "").strip()
    t = (os.environ.get("CF_AI_TOKEN") or "").replace("\ufeff", "").strip()
    return a, t


async def _cf_translate(text: str, dir_: str) -> str:
    """与沟通卡共用 translate.py（免费档 mistral-small，超时回退 m2m100）。"""
    account, token = _cf_creds()
    if not account or not token:
        return ""
    try:
        from translate import translate_text
        r = await translate_text(text, dir_ or "zh2ko", scene="general", want_roman=False)
        out = (r.get("ko") if (dir_ or "zh2ko") == "zh2ko" else r.get("zh")) or ""
        return str(out).strip()[:800]
    except Exception:
        return ""


@router.post("/api/chat/messages/{mid}/translate")
async def api_translate(req: Request, mid: int):
    csrf(req)
    try:
        d = await _json(req)
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    dir_ = d.get("dir")
    if dir_ not in ("zh2ko", "ko2zh", None, ""):
        raise HTTPException(400, "翻译方向无效")
    with db() as conn:
        u = need_user(conn, req)
        m = q(conn, """SELECT m.id,m.body,m.kind,m.sender_id,m.recipient_id,m.deleted
                       FROM c_messages m WHERE m.id=%s""", (mid,), one=True)
        if not m or m["deleted"] or u["id"] not in (m["sender_id"], m["recipient_id"]):
            raise HTTPException(404, "消息不存在")
        text = (m["body"] or "").strip()
        if not text or m["kind"] == "ref":
            raise HTTPException(400, "这条消息没有可翻译的文字")
        if not dir_:
            dir_ = "ko2zh" if HANGUL.search(text) else "zh2ko"
        hit = q(conn, "SELECT text FROM c_message_translations WHERE message_id=%s AND dir=%s", (mid, dir_), one=True)
        if hit:
            return {"ok": True, "dir": dir_, "text": hit["text"], "cached": True}
        account, token = _cf_creds()
        if not account or not token:
            return {"ok": False, "dir": dir_, "error": "翻译暂时不可用，请稍后再试"}
        limit("chat-tr:" + str(u["id"]), TR_USER_DAY, 86400, "今天的翻译次数用完了，明天再试")
        limit("chat-tr-global", TR_SITE_DAY, 86400, "翻译暂时不可用，请稍后再试")
    out = await _cf_translate(text, dir_)
    if not out:
        return {"ok": False, "dir": dir_, "error": "翻译暂时不可用，请稍后再试"}
    with db() as conn:
        q(conn, """INSERT INTO c_message_translations(message_id,dir,text) VALUES (%s,%s,%s)
                   ON CONFLICT (message_id, dir) DO NOTHING""", (mid, dir_, out))
    return {"ok": True, "dir": dir_, "text": out, "cached": False}
