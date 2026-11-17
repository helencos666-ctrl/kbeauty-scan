"""后台标签栏待处理数量：python test/test_admin_tab_counts.py
使用本机 Postgres 独立测试库，不打印连接串或密钥。"""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ok = fail = 0
ADMIN_PW = "test-admin-pw-1234"
H = {"X-LH": "1"}
KEYS = ("payproofs", "reports", "review", "merchants", "chicken", "promo",
        "friends", "idv", "consult", "providers", "referral", "feedback")


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS", name)
    else:
        fail += 1
        print("FAIL", name, str(info)[:400])


def static_checks():
    path = os.path.join(ROOT, "static", "admin.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    check("admin.html 含「内容审核」", "内容审核" in html)
    check("admin.html 含 /api/admin/tab-counts", "/api/admin/tab-counts" in html)
    check("admin.html 含 tab-badge", "tab-badge" in html)
    check("admin.html 不再含 id=\"pppend\"", 'id="pppend"' not in html)
    check("review 标签已改名为内容审核",
          not re.search(r'data-t="review">\s*审核\s*<', html))
    buttons = dict(re.findall(r'<button data-t="([A-Za-z]+)"[^>]*>(.*?)</button>', html, re.S))
    badge_tabs = set(t for t, inner in buttons.items() if "tab-badge" in inner)
    check("角标只出现在有队列的标签上", badge_tabs == set(KEYS),
          "多出 %s / 缺少 %s" % (sorted(badge_tabs - set(KEYS)), sorted(set(KEYS) - badge_tabs)))
    check("付款审核标签文字保留", "付款审核" in buttons.get("payproofs", ""), buttons.get("payproofs", "")[:80])
    check("内容审核标签文字正确", buttons.get("review", "").startswith("内容审核"), buttons.get("review", "")[:80])
    for t in ("users", "posts", "settings", "opening", "banners", "splash", "ops", "hits"):
        check("标签 %s 没有角标" % t, "tab-badge" not in buttons.get(t, ""), buttons.get(t, "")[:40])


def main():
    static_checks()

    dsn = os.environ.get("LVHAN_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/lvhan_tab_counts_v1")
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_tab_counts_v1"
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
        print("SKIP: no local postgres (%s)" % type(e).__name__)
        print("结果：PASS %d / FAIL %d" % (ok, fail))
        return 1 if fail else 0

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import community as C

    os.environ["DATABASE_URL"] = dsn
    os.environ["COOKIE_INSECURE"] = "1"
    os.environ["ADMIN_PASSWORD"] = ADMIN_PW
    C._schema_ready = False
    C._psycopg = None

    app = FastAPI()
    app.include_router(C.router)
    guest = TestClient(app)
    r = guest.get("/api/admin/tab-counts", headers=H)
    check("未登录访问计数接口返回 401", r.status_code == 401, r.status_code)

    adm = TestClient(app)
    check("管理员登录成功",
          adm.post("/api/admin/login", json={"password": ADMIN_PW}, headers=H).status_code == 200)
    r = adm.get("/api/admin/tab-counts", headers=H)
    check("计数接口返回 200", r.status_code == 200, r.status_code)
    data = r.json() if r.status_code == 200 else {}
    check("12 个队列键都在", set(KEYS) <= set(data), sorted(set(KEYS) - set(data)))
    check("没有无队列标签的键",
          not (set(data) - set(KEYS)), sorted(set(data) - set(KEYS)))
    check("值都是整数且非负",
          all(isinstance(data.get(k), int) and not isinstance(data.get(k), bool) and data.get(k) >= 0 for k in KEYS),
          {k: data.get(k) for k in KEYS})
    check("初始计数全为 0", all(data.get(k) == 0 for k in KEYS),
          {k: data.get(k) for k in KEYS if data.get(k)})

    u = TestClient(app)
    r = u.post("/api/community/register",
               json={"account": "tabcounts@example.com", "password": "Passw0rd!xyz",
                     "nickname": "角标测试", "agree": True}, headers=H)
    check("注册测试用户", r.status_code == 200, r.text[:200])
    uid = u.get("/api/community/me").json()["user"]["id"]

    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO c_pay_proofs(user_id, kind, status) VALUES (%s, 't1', 'pending')", (uid,))
        conn.execute("INSERT INTO c_feedback(user_id, body, page, status) VALUES (%s, '测试意见', '/me', 'new')", (uid,))
        conn.execute("INSERT INTO c_reports(target_type, target_id, user_id, reason, resolved) VALUES ('post', 1, %s, '测试举报', FALSE)", (uid,))
        conn.commit()

    d2 = adm.get("/api/admin/tab-counts").json()
    check("待审付款证明 payproofs=1", d2.get("payproofs") == 1, d2.get("payproofs"))
    check("新意见反馈 feedback=1", d2.get("feedback") == 1, d2.get("feedback"))
    check("未处理举报 reports=1", d2.get("reports") == 1, d2.get("reports"))
    check("无关队列仍为 0",
          all(d2.get(k) == 0 for k in KEYS if k not in ("payproofs", "feedback", "reports")),
          {k: d2.get(k) for k in KEYS if d2.get(k) and k not in ("payproofs", "feedback", "reports")})

    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE c_pay_proofs SET status='approved' WHERE user_id=%s", (uid,))
        conn.execute("UPDATE c_feedback SET status='done' WHERE user_id=%s", (uid,))
        conn.execute("UPDATE c_reports SET resolved=TRUE WHERE user_id=%s", (uid,))
        conn.commit()

    d3 = adm.get("/api/admin/tab-counts").json()
    check("处理后三项归零", all(d3.get(k) == 0 for k in KEYS),
          {k: d3.get(k) for k in KEYS if d3.get(k)})

    # 清理测试库
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
