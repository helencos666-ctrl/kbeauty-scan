"""「我的 · 编辑资料」保存简介 API：python test/test_me_profile.py
使用本机 Postgres（与 test_community / test_points 相同的无密码测试实例），不打印连接串或密钥。
没有本机 Postgres 时打印 SKIP 并返回 0。
"""
import os
import sys
import random

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ok = fail = 0


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS", name)
    else:
        fail += 1
        print("FAIL", name, str(info)[:300])


def main():
    dsn = os.environ.get(
        "LVHAN_TEST_DATABASE_URL",
        "postgresql://postgres@127.0.0.1:54329/lvhan_me_profile_v1",
    )
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_me_profile_v1"
    if not dbname.isidentifier() or not dbname.isascii():
        raise RuntimeError("bad test db name")

    # 建库：只重建本测试专用的库，不动其它库。
    try:
        import psycopg
        admin = psycopg.connect("postgresql://postgres@127.0.0.1:54329/postgres", connect_timeout=3)
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=%s AND pid<>pg_backend_pid()",
                (dbname,),
            )
            cur.execute("DROP DATABASE IF EXISTS " + dbname)
            cur.execute("CREATE DATABASE " + dbname)
        admin.close()
    except Exception as e:
        print("SKIP: no local postgres (%s)" % str(e)[:80])
        return 0

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import community as C

    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "COOKIE_INSECURE", "ADMIN_PASSWORD")}
    os.environ["DATABASE_URL"] = dsn
    os.environ["COOKIE_INSECURE"] = "1"
    os.environ["ADMIN_PASSWORD"] = "test-admin-pw-1234"
    C._schema_ready = False
    C._psycopg = None

    app = FastAPI()
    app.include_router(C.router)  # community 已 include me.router
    H = {"X-LH": "1"}
    rnd = random.randint(100000, 999999)

    try:
        cli = TestClient(app)

        # 1) 未登录（带 CSRF）→ 401
        anon = TestClient(app)
        r = anon.post("/api/me/profile", json={"nickname": "路人甲乙", "bio": "游客"}, headers=H)
        check("未登录 POST /api/me/profile 为 401", r.status_code == 401, r.text)

        # 2) 已登录但缺 X-LH（无 CSRF）→ 403
        r = cli.post(
            "/api/community/register",
            json={
                "account": f"mep{rnd}@example.com",
                "password": "Passw0rd!xyz",
                "nickname": "旅友" + str(rnd),
                "agree": True,
            },
            headers=H,
        )
        check("注册测试用户", r.status_code == 200, r.text)
        r = cli.post("/api/me/profile", json={"nickname": "无头请求", "bio": "测试"})
        check("无 X-LH（无 CSRF）为 403", r.status_code == 403, r.text)

        # 3) 合法 nickname + bio → 200，回显一致
        nick = "首尔小旅"
        bio = "12月首尔自由行，爱逛药妆店"
        r = cli.post("/api/me/profile", json={"nickname": nick, "bio": bio}, headers=H)
        check("保存昵称与简介为 200", r.status_code == 200, r.text)
        data = r.json() if r.status_code == 200 else {}
        check("返回 ok=True", data.get("ok") is True, data)
        check("回显 nickname 一致", data.get("nickname") == nick, data)
        check("回显 bio 一致", data.get("bio") == bio, data)

        # 4) GET /api/me/summary 能读到刚存的简介
        r = cli.get("/api/me/summary")
        check("GET /api/me/summary 为 200", r.status_code == 200, r.text)
        summary = r.json() if r.status_code == 200 else {}
        user = summary.get("user") or {}
        check("summary 读到刚存的 bio", user.get("bio") == bio, user)
        check("summary 读到昵称", user.get("nickname") == nick, user)

        # 5) 超过 40 字 → 400，文案提示 40
        long_bio = "首尔" * 21  # 42 字
        r = cli.post("/api/me/profile", json={"nickname": nick, "bio": long_bio}, headers=H)
        check("超过 40 字的简介为 400", r.status_code == 400, r.text)
        check("超长提示包含「40」", "40" in r.text, r.text)

        # 6) 含「微信」的简介被拦截 → 400
        r = cli.post(
            "/api/me/profile",
            json={"nickname": nick, "bio": "想咨询可以加微信 abc123"},
            headers=H,
        )
        check("简介含微信被拦截为 400", r.status_code == 400, r.text)

        # 7) 简介选填：空 bio → 200
        r = cli.post("/api/me/profile", json={"nickname": nick, "bio": ""}, headers=H)
        check("空简介允许保存为 200", r.status_code == 200, r.text)
        check("空简介回显空字符串", (r.json() if r.status_code == 200 else {}).get("bio") == "", r.text)

        # 8) 昵称少于 2 字 → 400
        r = cli.post("/api/me/profile", json={"nickname": "A", "bio": "短昵称"}, headers=H)
        check("昵称少于 2 字为 400", r.status_code == 400, r.text)

        # 保存后 summary 的 bio 已变成空
        r = cli.get("/api/me/summary")
        check("summary bio 随保存更新为空", (r.json().get("user") or {}).get("bio") == "", r.text)

    finally:
        C._schema_ready = False
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print("%d passed, %d failed" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
