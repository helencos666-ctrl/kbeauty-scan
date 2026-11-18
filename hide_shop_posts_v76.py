"""One-time soft-delete for three merchant feed posts approved for removal.

Idempotent. Never prints DATABASE_URL or other secrets. Images stay in c_images.
Backup of the public records is at /workspace/lvhan-backup/2026-11-18-shop-posts/.
Does not hide post id=7. Does not touch opening-promos.json.
"""
from __future__ import annotations

from fastapi import HTTPException

TARGETS = [
    {"id": 8, "author": "韩国通讯", "title": "蓝憬通讯：推荐2位好友注册免费领韩国手机卡"},
    {"id": 6, "author": "dayone皮肤科小汪", "title": "旅韩助手推荐来的都有50000商品券（满100万可用）"},
    {"id": 5, "author": "dayone皮肤科小汪", "title": "dayone皮肤科"},
]


def _log(msg):
    print("hide shop posts v76: " + msg, flush=True)


def hide_once():
    try:
        from community import _db_url, db, q
        if not _db_url():
            _log("skipped: database not configured")
            return
        with db() as conn:
            for t in TARGETS:
                row = q(
                    conn,
                    """SELECT p.id, p.title, p.deleted, u.nickname
                       FROM c_posts p JOIN c_users u ON u.id=p.user_id
                       WHERE u.nickname=%s AND p.title=%s
                       ORDER BY p.id DESC LIMIT 1""",
                    (t["author"], t["title"]),
                    one=True,
                )
                if not row:
                    _log("not found: title=%s" % t["id"])
                    continue
                if row["deleted"]:
                    _log("already hidden: id=%s" % row["id"])
                    continue
                q(conn, "UPDATE c_posts SET deleted=TRUE WHERE id=%s AND NOT deleted", (row["id"],))
                _log("hidden: id=%s" % row["id"])
    except HTTPException:
        _log("skipped: database unavailable")
    except Exception:
        _log("failed: unexpected error")
