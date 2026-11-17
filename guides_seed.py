"""Startup seed: insert the first batch of platform guide posts as pending.

Idempotent by official-author + title. Never prints DATABASE_URL or other secrets.
Does not approve, pin, or feature posts. Re-runs skip titles that already exist.
"""
from __future__ import annotations

import json
import os

DATA_NAME = "guides_batch1.json"
REVIEW_NOTE = "平台精选稿，待站长审核后公开。"
TAX_CHANNEL = "购物退税"
TAX_TAG = "tax"
ALLOWED_BOARDS = {"visa", "news", "daily", "rent"}
TITLE_MAX, BODY_MAX = 40, 3000


def data_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), DATA_NAME)


def load_posts():
    path = data_path()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    posts = data["posts"] if isinstance(data, dict) else data
    out = []
    for p in posts:
        board = (p.get("board") or "").strip()
        title = (p.get("标题") or "").strip()
        body = (p.get("正文") or "").strip()
        channel = (p.get("频道") or "").strip()
        tag = TAX_TAG if channel == TAX_CHANNEL else None
        out.append({"board": board, "title": title, "body": body, "channel": channel, "tag": tag})
    return out


def validate_post(p):
    if p["board"] not in ALLOWED_BOARDS:
        return "invalid board"
    if not (1 <= len(p["title"]) <= TITLE_MAX):
        return "title length"
    if not (1 <= len(p["body"]) <= BODY_MAX):
        return "body length"
    if p["channel"] == TAX_CHANNEL and p["board"] != "news":
        return "tax board"
    return None


def _log(msg):
    print("guides seed: " + msg, flush=True)


def seed_once():
    """Insert pending official guides once per title. Never raises to the caller."""
    inserted = existed = skipped = 0
    try:
        posts = load_posts()
        if len(posts) != 20:
            _log("skipped: expected 20 posts, got %s" % len(posts))
            return
        from community import _db_url, db, q
        from retention import official_user

        if not _db_url():
            _log("skipped: database not configured")
            return
        with db() as conn:
            oid = official_user(conn)
            for p in posts:
                reason = validate_post(p)
                if reason:
                    skipped += 1
                    _log("skipped one: " + reason)
                    continue
                row = q(
                    conn,
                    """SELECT id, status FROM c_posts
                       WHERE user_id=%s AND title=%s AND NOT deleted
                       ORDER BY id DESC LIMIT 1""",
                    (oid, p["title"]),
                    one=True,
                )
                if row:
                    existed += 1
                    continue
                q(
                    conn,
                    """INSERT INTO c_posts(user_id, board, title, body, status, is_merchant,
                                           review_note, tag, pinned, featured, topic)
                       VALUES (%s,%s,%s,%s,'pending',FALSE,%s,%s,FALSE,FALSE,FALSE)
                       RETURNING id""",
                    (oid, p["board"], p["title"], p["body"], REVIEW_NOTE, p["tag"]),
                    one=True,
                )
                inserted += 1
        _log("inserted=%s existed=%s skipped=%s total=%s" % (inserted, existed, skipped, len(posts)))
    except Exception:
        # Do not log the exception text: drivers may include the DSN.
        _log("failed: unexpected error inserted=%s existed=%s skipped=%s" % (inserted, existed, skipped))
