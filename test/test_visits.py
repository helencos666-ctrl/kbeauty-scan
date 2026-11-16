"""每日访问统计：python test/test_visits.py
使用本机 Postgres 独立测试库，不打印连接串或密钥。"""
import os
import sys

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
        print("FAIL", name, str(info)[:400])


def main():
    dsn = os.environ.get("LVHAN_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/lvhan_visits_v1")
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_visits_v1"
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
        print("SKIP: no local postgres (%s)" % str(e)[:80])
        return 0

    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, PlainTextResponse
    from fastapi.testclient import TestClient
    import community as C
    import visits as V

    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "COOKIE_INSECURE", "ADMIN_PASSWORD", "VISIT_SALT")}
    os.environ["DATABASE_URL"] = dsn
    os.environ["COOKIE_INSECURE"] = "1"
    os.environ["ADMIN_PASSWORD"] = "test-admin-pw-1234"
    os.environ["VISIT_SALT"] = "test-visit-salt-do-not-use-in-prod"
    C._schema_ready = False
    C._psycopg = None
    V._salt_cache["v"] = None

    app = FastAPI()
    app.include_router(C.router)
    app.middleware("http")(V.visit_middleware)

    @app.get("/")
    def home():
        return HTMLResponse("<html><body>home</body></html>")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/robots.txt")
    def robots():
        return PlainTextResponse("User-agent: *\nDisallow:\n")

    @app.get("/static/pwa.css")
    def css():
        return PlainTextResponse("body{}", media_type="text/css")

    H = {"X-LH": "1"}
    browser = {"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
               "cf-connecting-ip": "203.0.113.10"}
    browser2 = {"user-agent": "Mozilla/5.0 (Linux; Android 14) Chrome/120.0.0.0 Mobile",
                "cf-connecting-ip": "203.0.113.11"}

    try:
        cli = TestClient(app)
        adm = TestClient(app)

        r = cli.get("/", headers=browser)
        check("home 200", r.status_code == 200, r.status_code)

        r = adm.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H)
        check("admin login", r.status_code == 200, r.status_code)
        ov = adm.get("/api/admin/overview").json()
        check("overview has visits", isinstance(ov.get("visits"), dict), ov)
        today = ov["visits"]["today"]
        check("first home pv 1", today.get("pv") == 1, today)
        check("first home uv 1", today.get("uv") == 1, today)
        check("30 days present", len(ov["visits"].get("days") or []) == 30, len((ov["visits"].get("days") or [])))

        cli.get("/", headers=browser)
        today = adm.get("/api/admin/overview").json()["visits"]["today"]
        check("same visitor pv 2 uv 1", today.get("pv") == 2 and today.get("uv") == 1, today)

        cli.get("/", headers=browser2)
        today = adm.get("/api/admin/overview").json()["visits"]["today"]
        check("second visitor pv 3 uv 2", today.get("pv") == 3 and today.get("uv") == 2, today)

        cli.get("/community", headers=browser)
        today = adm.get("/api/admin/overview").json()["visits"]["today"]
        check("community page counted", today.get("pv") == 4 and today.get("uv") == 2, today)

        before = today["pv"]
        cli.get("/api/community/me", headers=browser)
        cli.get("/healthz", headers=browser)
        cli.get("/robots.txt", headers=browser)
        cli.get("/static/pwa.css", headers=browser)
        cli.head("/", headers=browser)
        bot = dict(browser)
        bot["user-agent"] = "Mozilla/5.0 compatible; Googlebot/2.1; +http://www.google.com/bot.html"
        cli.get("/", headers=bot)
        curl_h = dict(browser)
        curl_h["user-agent"] = "curl/8.0.0"
        cli.get("/", headers=curl_h)
        empty_ua = {"cf-connecting-ip": "203.0.113.10", "user-agent": ""}
        cli.get("/", headers=empty_ua)
        cli.get("/admin", headers=browser)
        today = adm.get("/api/admin/overview").json()["visits"]["today"]
        check("excludes api/static/health/robots/head/bots/admin", today.get("pv") == before, today)

        cli.get("/?utm_source=douyin&utm_campaign=airport-video", headers=browser)
        cli.get("/login?utm_source=douyin&utm_campaign=airport-video", headers=browser2)
        cli.get("/", headers={**browser, "referer": "https://www.douyin.com/video/123"})
        ov = adm.get("/api/admin/overview").json()
        src = ov["visits"]["sources"]
        kinds = {x["kind"]: x for x in src}
        check("utm source recorded", any(x["kind"] == "utm" and "airport-video" in x["value"] and "douyin" in x["value"] for x in src), src)
        check("referer host recorded", any(x["kind"] == "referer" and x["value"] == "douyin.com" for x in src), src)

        # 本站 Referer 不记
        self_ref = dict(browser2)
        self_ref["referer"] = "https://lvhanzhushou.com/community"
        before_src = len(src)
        cli.get("/member", headers=self_ref)
        src2 = adm.get("/api/admin/overview").json()["visits"]["sources"]
        check("own referer ignored", not any(x["kind"] == "referer" and "lvhanzhushou.com" in x["value"] for x in src2), src2)

        # 推广码
        with C.db() as conn:
            C.q(conn, "INSERT INTO c_promoters(code,name) VALUES (%s,%s) ON CONFLICT DO NOTHING", ("A08", "测试"))
        cli.get("/?ref=A08", headers=browser)
        src3 = adm.get("/api/admin/overview").json()["visits"]["sources"]
        check("ref source recorded", any(x["kind"] == "ref" and x["value"] == "A08" for x in src3), src3)

        # 独立接口与总览一致；响应中不含盐和 IP
        vis = adm.get("/api/admin/visits").json()
        check("visits api matches overview pv", vis["today"]["pv"] == adm.get("/api/admin/overview").json()["visits"]["today"]["pv"], vis)
        blob = str(vis) + str(adm.get("/api/admin/overview").json())
        check("no salt in api", "test-visit-salt" not in blob and "_visit_salt" not in blob, "leaked")
        check("no raw test ip in api", "203.0.113." not in blob, "ip leaked")

        # 未登录不能看
        guest = TestClient(app)
        check("visits requires admin", guest.get("/api/admin/visits").status_code == 401)

        # 哈希含盐：换盐则同一人算新 UV（只验证函数，不改库盐）
        h1 = V.visitor_hash("1.1.1.1", "UA", V.today_kst())
        os.environ["VISIT_SALT"] = "another-salt"
        V._salt_cache["v"] = None
        h2 = V.visitor_hash("1.1.1.1", "UA", V.today_kst())
        check("salt changes hash", h1 != h2, (h1, h2))
        os.environ["VISIT_SALT"] = "test-visit-salt-do-not-use-in-prod"
        V._salt_cache["v"] = None
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        V._salt_cache["v"] = None
        C._schema_ready = False

    print("RESULT passed=%s failed=%s" % (ok, fail))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
