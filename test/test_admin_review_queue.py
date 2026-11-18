"""内容审核队列：python test/test_admin_review_queue.py
覆盖两件事：
  1) 会员推荐地点：默认筛选 queue = pending + flagged，角标/「N 条待审」/列表同一口径；
  2) 帖子审核：/api/admin/pending 必须包含 is_merchant=TRUE 的待审商家帖。
使用本机 Postgres 独立测试库；不打印连接串或密钥，不接触生产库、不审核生产帖子。
没有本机 Postgres 时只跑静态检查（库测试 SKIP）。
"""
import base64
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ok = fail = 0
ADMIN_PW = "test-admin-pw-1234"
H = {"X-LH": "1"}
DBNAME = "lvhan_review_queue_v1"

# 测试专用标题/昵称：不要用生产数据里的标题，也不要去审核生产帖子。
MERCHANT_TITLE = "【测试】商家待审帖：推荐好友注册领手机卡"
MERCHANT_NICK = "审核队列商家"
FLAGGED_NAME = "测试待核对小店"
FLAGGED_REC = "测试投稿：地图上找不到这家店，仅供审核队列测试。"

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS", name)
    else:
        fail += 1
        print("FAIL", name, str(info)[:400])


# ---------------------------------------------------------------- 静态检查

def static_checks():
    path = os.path.join(ROOT, "static", "admin.html")
    try:
        with open(path, encoding="utf-8") as f:
            html = f.read()
    except OSError as e:
        check("能读取 static/admin.html", False, e)
        return

    check("admin.html 含「内容审核」", "内容审核" in html)
    check("admin.html 含 /api/admin/tab-counts", "/api/admin/tab-counts" in html)
    check("admin.html 含 tab-badge", "tab-badge" in html)

    m = re.search(r'id="fpstatus".*?</select>', html, re.S)
    sel = m.group(0) if m else ""
    opts = re.findall(r'<option[^>]*>[^<]*</option>', sel)
    check("地点下拉首项是 queue 且默认选中",
          bool(opts) and 'value="queue"' in opts[0] and "selected" in opts[0], opts[:2])
    check("queue 选项文案为「待审核和待核对」", bool(opts) and "待审核和待核对" in opts[0], opts[:1])
    check("地点下拉保留 pending/flagged/approved/rejected/all",
          all(('value="%s"' % v) in sel for v in ("pending", "flagged", "approved", "rejected", "all")),
          sel[:300])

    fn = re.search(r"async function loadFanplaces\(\)[\s\S]*?\n}", html)
    body = fn.group(0) if fn else ""
    check("loadFanplaces 兜底为 'queue'", "|| 'queue'" in body, body[:200])
    check("loadFanplaces 不再兜底 'pending'", "|| 'pending'" not in body, body[:200])

    tip = re.search(r'id="t-review"[\s\S]*?<div id="rvlist">', html)
    tip = tip.group(0) if tip else ""
    check("帖子审核说明同时覆盖普通商业帖与商家帖",
          "商业关键词" in tip and "商家" in tip, tip[:300])
    check("说明不再声称只审非商家帖",
          "非商家用户发布" not in html and "只审非商家" not in html)

    rv = re.search(r"async function review\(\)[\s\S]*?await loadFanplaces\(\);", html)
    rv = rv.group(0) if rv else ""
    check("帖子审核列表代码存在", bool(rv))
    check("帖子列表含「商家帖」标记", "商家帖" in rv, rv[:200])
    check("帖子列表有通过按钮 data-ok", "data-ok" in rv)
    check("帖子列表有拒绝按钮 data-no", "data-no" in rv)
    check("通过/拒绝调用 /api/admin/posts/{id}/review",
          "/api/admin/posts/" in rv and "/review" in rv)
    check("帖子图片走 /api/admin/img/", "/api/admin/img/" in rv)
    check("保留封禁作者按钮", "封禁作者" in rv)

    check("admin.html 使用 ?v=86", "?v=86" in html)


# ---------------------------------------------------------- 测试库小工具

def table_columns(conn, table):
    rows = conn.execute(
        """SELECT column_name, is_nullable, column_default, data_type, is_identity
           FROM information_schema.columns
           WHERE table_schema='public' AND table_name=%s
           ORDER BY ordinal_position""", (table,)).fetchall()
    return [dict(zip(("name", "nullable", "default", "type", "identity"), r)) for r in rows]


def _fallback(col):
    """未知 NOT NULL 无默认列的最小占位值。"""
    t = (col["type"] or "").lower()
    if col["default"] is not None or col["identity"] == "YES" or col["nullable"] == "YES":
        return None, False
    if "int" in t or "numeric" in t or "real" in t or "double" in t:
        return 0, True
    if t == "boolean":
        return False, True
    if t.startswith("timestamp") or t in ("date", "time") or t.startswith("time "):
        return datetime.now(timezone.utc), True
    if t == "bytea":
        return PNG_1PX, True
    if t.endswith("[]"):
        return [], True
    if t in ("json", "jsonb"):
        try:
            from psycopg.types.json import Jsonb
            return Jsonb({}), True
        except Exception:
            return "{}", True
    return "", True


def insert_row(conn, table, values):
    """按实际表结构插入：只写存在的列，其余 NOT NULL 列给最小占位值。返回 id。"""
    cols, params = [], []
    for col in table_columns(conn, table):
        if col["name"] in values:
            v = values[col["name"]]
        else:
            v, need = _fallback(col)
            if not need:
                continue
        cols.append('"%s"' % col["name"])
        params.append(v)
    sql = 'INSERT INTO %s (%s) VALUES (%s) RETURNING id' % (
        table, ", ".join(cols), ", ".join(["%s"] * len(params)))
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def nonempty_bytes_columns(conn, table):
    """c_images 里的二进制列（bytea），用 1x1 PNG 占位。"""
    return [c["name"] for c in table_columns(conn, table)
            if (c["type"] or "").lower() == "bytea"]


# ------------------------------------------------------------------ 主流程

def main():
    static_checks()

    dsn = os.environ.get("LVHAN_TEST_DATABASE_URL",
                         "postgresql://postgres@127.0.0.1:54329/" + DBNAME)
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or DBNAME
    if not dbname.isidentifier() or not dbname.isascii():
        raise RuntimeError("bad test db name")

    try:
        import psycopg
        admin_db = psycopg.connect("postgresql://postgres@127.0.0.1:54329/postgres", connect_timeout=3)
        admin_db.autocommit = True
        with admin_db.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (dbname,))
            cur.execute("DROP DATABASE IF EXISTS " + dbname)
            cur.execute("CREATE DATABASE " + dbname)
        admin_db.close()
    except Exception as e:
        print("SKIP 库测试：没有本机 Postgres（%s）" % type(e).__name__)
        print("结果：PASS %d / FAIL %d" % (ok, fail))
        return 1 if fail else 0

    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "COOKIE_INSECURE", "ADMIN_PASSWORD")}
    os.environ["DATABASE_URL"] = dsn
    os.environ["COOKIE_INSECURE"] = "1"
    os.environ["ADMIN_PASSWORD"] = ADMIN_PW

    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import community as C

        C._schema_ready = False
        C._psycopg = None
        app = FastAPI()
        app.include_router(C.router)   # community.router 已包含 fanplaces.router

        def client():
            return TestClient(app, raise_server_exceptions=False)

        guest = client()
        for path in ("/api/admin/pending", "/api/admin/fanplaces", "/api/admin/tab-counts"):
            r = guest.get(path, headers=H)
            check("未登录 %s 返回 401" % path, r.status_code == 401, r.status_code)

        adm = client()
        r = adm.post("/api/admin/login", json={"password": ADMIN_PW}, headers=H)
        check("管理员登录成功", r.status_code == 200, r.text[:200])

        u = client()
        r = u.post("/api/community/register",
                   json={"account": "reviewqueue@example.com", "password": "Passw0rd!xyz",
                         "nickname": MERCHANT_NICK, "agree": True}, headers=H)
        check("注册测试用户", r.status_code == 200, r.text[:200])
        me = u.get("/api/community/me", headers=H)
        uid = (me.json().get("user") or {}).get("id") if me.status_code == 200 else None
        check("取得测试用户 id", bool(uid), me.text[:200])

        with psycopg.connect(dsn) as conn:
            # 升级为在期商家
            conn.execute("UPDATE c_users SET merchant_until=now()+interval '30 days' WHERE id=%s", (uid,))
            # 待审商家帖
            pid = insert_row(conn, "c_posts", {
                "user_id": uid, "board": "news", "title": MERCHANT_TITLE,
                "body": "这是商家会员发布的待审帖，内容仅用于测试审核队列是否包含商家帖，请勿在正式环境使用。",
                "status": "pending", "is_merchant": True,
                "review_note": None, "created_at": datetime.now(timezone.utc),
            })
            check("插入待审商家帖", bool(pid), pid)
            # 帖子图片（1x1 PNG 占位）
            img_vals = {"post_id": pid, "pos": 0}
            for n in nonempty_bytes_columns(conn, "c_images"):
                img_vals[n] = PNG_1PX
            for n, v in (("mime", "image/png"), ("content_type", "image/png"),
                         ("w", 1), ("h", 1), ("width", 1), ("height", 1)):
                img_vals[n] = v
            img_id = insert_row(conn, "c_images", img_vals)
            check("插入帖子图片", bool(img_id), img_id)
            # 一条 flagged（待核对）会员地点投稿；刻意不插 pending，证明默认列表能看到 flagged
            place_id = insert_row(conn, "c_fan_places", {
                "name": FLAGGED_NAME, "name_user": FLAGGED_NAME, "category": "coffee",
                "address": "测试地址", "address_user": "测试地址",
                "kakao_found": False, "status": "pending",
                "created_at": datetime.now(timezone.utc),
            })
            check("插入地点主记录", bool(place_id), place_id)
            sub_id = insert_row(conn, "c_fan_place_subs", {
                "place_id": place_id, "user_id": uid, "status": "flagged",
                "rec_text": FLAGGED_REC, "review_note": None,
                "created_at": datetime.now(timezone.utc),
            })
            conn.commit()
        check("插入待核对地点投稿", bool(sub_id), sub_id)

        # 1) 帖子审核：包含商家帖
        r = adm.get("/api/admin/pending", headers=H)
        check("GET /api/admin/pending 200", r.status_code == 200, r.status_code)
        posts = (r.json() or {}).get("posts") if r.status_code == 200 else []
        posts = posts or []
        hit = [p for p in posts if p.get("title") == MERCHANT_TITLE]
        check("待审列表含商家待审帖", len(hit) == 1, [p.get("title") for p in posts][:5])
        p = hit[0] if hit else {}
        check("商家帖 merchant=true", p.get("merchant") is True, p.get("merchant"))
        check("商家帖作者昵称正确", p.get("author") == MERCHANT_NICK, p.get("author"))
        check("商家帖 board=news", p.get("board") == "news", p.get("board"))
        check("商家帖 images 为非空 id 列表",
              isinstance(p.get("images"), list) and len(p["images"]) > 0, p.get("images"))
        check("商家帖字段齐全",
              all(k in p for k in ("id", "title", "author", "member_no", "user_id", "body",
                                   "note", "images", "merchant", "board", "board_name",
                                   "time", "status")),
              sorted(p.keys())[:30])
        check("商家帖正文非空", bool((p.get("body") or "").strip()), p.get("body"))
        check("商家帖 member_no 存在", p.get("member_no") not in (None, ""), p.get("member_no"))

        # 2) 地点审核：默认 = queue = pending + flagged
        r = adm.get("/api/admin/fanplaces", headers=H)
        check("默认 fanplaces 200", r.status_code == 200, r.status_code)
        d = r.json() if r.status_code == 200 else {}
        items = d.get("items") or []
        check("默认列表含 flagged 投稿（不传 status）",
              any(x.get("id") == sub_id for x in items), [x.get("id") for x in items])
        check("默认列表返回 pending=1", d.get("pending") == 1, d.get("pending"))
        check("默认列表项带审核所需字段",
              bool(items) and all(k in items[0] for k in ("id", "name", "status", "rec_text", "nickname")),
              sorted((items[0] or {}).keys())[:30] if items else [])

        r = adm.get("/api/admin/fanplaces?status=queue", headers=H)
        dq = r.json() if r.status_code == 200 else {}
        check("status=queue 200", r.status_code == 200, r.status_code)
        check("status=queue 含 flagged 投稿",
              any(x.get("id") == sub_id for x in (dq.get("items") or [])),
              [x.get("id") for x in (dq.get("items") or [])])
        check("status=queue pending=1", dq.get("pending") == 1, dq.get("pending"))
        check("status=queue 与默认列表一致",
              [x.get("id") for x in (dq.get("items") or [])] == [x.get("id") for x in items])

        r = adm.get("/api/admin/fanplaces?status=pending", headers=H)
        dp = r.json() if r.status_code == 200 else {}
        check("status=pending 200", r.status_code == 200, r.status_code)
        check("status=pending 不含 flagged 投稿",
              not any(x.get("id") == sub_id for x in (dp.get("items") or [])),
              [x.get("id") for x in (dp.get("items") or [])])
        check("status=pending 的 pending 计数仍为 1", dp.get("pending") == 1, dp.get("pending"))

        r = adm.get("/api/admin/fanplaces?status=foo", headers=H)
        check("非法 status=foo 返回 400", r.status_code == 400, r.status_code)
        check("非法 status 文案为「状态不正确」",
              r.status_code == 400 and "状态不正确" in (r.json().get("detail") or ""), r.text[:200])

        # 3) 角标：review = 1 商家待审帖 + 0 店铺推荐 + 1 flagged 地点
        r = adm.get("/api/admin/tab-counts", headers=H)
        check("GET /api/admin/tab-counts 200", r.status_code == 200, r.status_code)
        tc = r.json() if r.status_code == 200 else {}
        check("review == 2", tc.get("review") == 2, tc.get("review"))
        others = {k: v for k, v in tc.items() if k != "review"}
        check("其他队列计数为 0（本测试未插对应队列）",
              all(v == 0 for v in others.values()), {k: v for k, v in others.items() if v})

        # 4) 口径等式：review == 帖子条数 + 店铺推荐条数 + 地点 pending
        sh = adm.get("/api/admin/shop-recs?status=pending", headers=H)
        shop_items = (sh.json() or {}).get("items") if sh.status_code == 200 else []
        shop_items = shop_items or []
        eq = len(posts) + len(shop_items) + int(d.get("pending") or 0)
        check("review == len(待审帖子) + len(待审店铺推荐) + 地点 pending",
              tc.get("review") == eq, "%s vs %s" % (tc.get("review"), eq))

        # 5) 审核测试库里的商家帖（不是生产 id）：通过后离开队列，review 只剩 flagged 地点
        r = adm.post("/api/admin/posts/%s/review" % pid, json={"action": "approve"}, headers=H)
        check("审核通过测试商家帖 200", r.status_code == 200, r.text[:300])

        r = adm.get("/api/admin/pending", headers=H)
        posts2 = (r.json() or {}).get("posts") if r.status_code == 200 else []
        check("通过后待审列表不再含该帖",
              not any(x.get("id") == pid for x in (posts2 or [])),
              [(x.get("id"), x.get("title")) for x in (posts2 or [])][:5])

        tc2 = adm.get("/api/admin/tab-counts", headers=H).json()
        check("通过后 review == 1", tc2.get("review") == 1, tc2.get("review"))
        dq2 = adm.get("/api/admin/fanplaces?status=queue", headers=H).json()
        check("通过后 flagged 地点仍在默认队列",
              any(x.get("id") == sub_id for x in (dq2.get("items") or [])),
              [x.get("id") for x in (dq2.get("items") or [])])

    except Exception as e:
        check("库测试未抛异常", False, "%s: %s" % (type(e).__name__, e))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            admin_db = psycopg.connect("postgresql://postgres@127.0.0.1:54329/postgres", connect_timeout=3)
            admin_db.autocommit = True
            with admin_db.cursor() as cur:
                cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (dbname,))
                cur.execute("DROP DATABASE IF EXISTS " + dbname)
            admin_db.close()
        except Exception:
            pass

    print("结果：PASS %d / FAIL %d" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
