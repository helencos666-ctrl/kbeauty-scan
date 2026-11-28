"""Startup seed: publish the platform 小编's daily posts (official account only).

Append-only: entries live in official_daily.json, one per day. Re-runs are
idempotent by official author + title, and entries dated after today (KST) are
left for later so a future-dated post never shows up early.

Posts are inserted as 'published' with is_merchant FALSE, and the picture in
static/official-daily/ is stored exactly like an uploaded post image
(community.process_image -> c_images data/thumb/width/height, pos 0).

An entry may set "sync": true to update the already-published post in place
(body, board and picture) when its text differs from what is stored; that is
the only way an existing post is ever rewritten, and it is a no-op once the
stored body matches, so the hourly keepalive re-run does nothing.

Never prints DATABASE_URL or other secrets, and never logs exception text.
"""
from __future__ import annotations

import datetime
import json
import os

DATA_NAME = "official_daily.json"
IMG_DIR = "official-daily"
ALLOWED_BOARDS = {"pit", "news", "daily", "visa", "rent", "study", "group"}
TITLE_MAX, BODY_MAX = 40, 3000
KST = datetime.timezone(datetime.timedelta(hours=9))


def base_dir():
    return os.path.dirname(os.path.abspath(__file__))


def data_path():
    return os.path.join(base_dir(), DATA_NAME)


def image_path(name):
    return os.path.join(base_dir(), "static", IMG_DIR, os.path.basename(name or ""))


def today():
    return datetime.datetime.now(KST).date()


def load_posts():
    """Return the raw entries; a dict with a "posts" key is also accepted."""
    with open(data_path(), encoding="utf-8") as f:
        data = json.load(f)
    posts = data.get("posts", []) if isinstance(data, dict) else data
    out = []
    for p in posts or []:
        out.append({
            "date": (p.get("date") or "").strip(),
            "board": (p.get("board") or "").strip(),
            "title": (p.get("title") or "").strip(),
            "body": (p.get("body") or "").strip(),
            "image": (p.get("image") or "").strip(),
            "sync": p.get("sync") is True,
        })
    return out


def validate_post(p):
    if p["board"] not in ALLOWED_BOARDS:
        return "invalid board"
    if not (1 <= len(p["title"]) <= TITLE_MAX):
        return "title length"
    if not (1 <= len(p["body"]) <= BODY_MAX):
        return "body length"
    try:
        datetime.date.fromisoformat(p["date"])
    except ValueError:
        return "bad date"
    if not p["image"] or os.path.basename(p["image"]) != p["image"]:
        return "bad image name"
    if not os.path.isfile(image_path(p["image"])):
        return "image missing"
    return None


def _log(msg):
    print("official daily seed: " + msg, flush=True)


def seed_once():
    """Publish today's (and older) entries once per title. Never raises to the caller."""
    inserted = existed = skipped = future = updated = 0
    try:
        posts = load_posts()
        from community import _db_url, db, q

        if not _db_url():
            _log("skipped: database not configured")
            return
        from retention import official_user

        day = today()
        with db() as conn:
            oid = official_user(conn)
            for p in posts:
                reason = validate_post(p)
                if reason:
                    skipped += 1
                    _log("skipped one: " + reason)
                    continue
                if datetime.date.fromisoformat(p["date"]) > day:
                    future += 1
                    continue
                row = q(
                    conn,
                    """SELECT id, body FROM c_posts
                       WHERE user_id=%s AND title=%s AND NOT deleted
                       ORDER BY id DESC LIMIT 1""",
                    (oid, p["title"]),
                    one=True,
                )
                if row:
                    if p["sync"] and row["body"] != p["body"]:
                        from community import process_image
                        with open(image_path(p["image"]), "rb") as f:
                            data, thumb, w, h = process_image(f.read())
                        q(conn, "UPDATE c_posts SET body=%s, board=%s WHERE id=%s AND user_id=%s",
                          (p["body"], p["board"], row["id"], oid))
                        if q(conn, "SELECT 1 AS x FROM c_images WHERE post_id=%s AND pos=0", (row["id"],), one=True):
                            q(conn, "UPDATE c_images SET data=%s, thumb=%s, width=%s, height=%s WHERE post_id=%s AND pos=0",
                              (data, thumb, w, h, row["id"]))
                        else:
                            q(conn, "INSERT INTO c_images(post_id,pos,data,thumb,width,height) VALUES (%s,0,%s,%s,%s,%s)",
                              (row["id"], data, thumb, w, h))
                        updated += 1
                    existed += 1
                    continue
                from community import process_image
                with open(image_path(p["image"]), "rb") as f:
                    raw = f.read()
                data, thumb, w, h = process_image(raw)
                post = q(
                    conn,
                    """INSERT INTO c_posts(user_id, board, title, body, status, is_merchant,
                                           pinned, featured, topic)
                       VALUES (%s,%s,%s,%s,'published',FALSE,FALSE,FALSE,FALSE)
                       RETURNING id""",
                    (oid, p["board"], p["title"], p["body"]),
                    one=True,
                )
                q(
                    conn,
                    """INSERT INTO c_images(post_id,pos,data,thumb,width,height)
                       VALUES (%s,0,%s,%s,%s,%s)""",
                    (post["id"], data, thumb, w, h),
                )
                inserted += 1
        _log("inserted=%s updated=%s existed=%s skipped=%s future=%s total=%s"
             % (inserted, updated, existed, skipped, future, len(posts)))
    except Exception:
        # Do not log the exception text: drivers may include the DSN.
        _log("failed: unexpected error inserted=%s existed=%s skipped=%s"
             % (inserted, existed, skipped))
