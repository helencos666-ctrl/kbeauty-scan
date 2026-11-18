"""Soft-delete the remaining Dayone Seoul film-festival merchant post (id=7).

Idempotent. Never prints DATABASE_URL or other secrets. Images stay in c_images.
Backup: /workspace/lvhan-backup/2026-11-18-shop-post-7/
"""
from fastapi import HTTPException

TARGET = {"id": 7, "author": "dayone皮肤科小汪", "title": "首尔电影🎬节参加的来预约"}

def _log(msg):
    print("hide shop posts v77: " + msg, flush=True)

def hide_once():
    try:
        from community import _db_url, db, q
        if not _db_url():
            _log("skipped: database not configured")
            return
        with db() as conn:
            row = q(
                conn,
                """SELECT p.id, p.title, p.deleted, u.nickname
                   FROM c_posts p JOIN c_users u ON u.id=p.user_id
                   WHERE u.nickname=%s AND p.title=%s
                   ORDER BY p.id DESC LIMIT 1""",
                (TARGET["author"], TARGET["title"]),
                one=True,
            )
            if not row:
                _log("not found")
                return
            if row["deleted"]:
                _log("already hidden: id=%s" % row["id"])
                return
            q(conn, "UPDATE c_posts SET deleted=TRUE WHERE id=%s AND NOT deleted", (row["id"],))
            _log("hidden: id=%s" % row["id"])
    except HTTPException:
        _log("skipped: database unavailable")
    except Exception:
        _log("failed: unexpected error")
