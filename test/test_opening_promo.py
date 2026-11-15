"""开业活动：python test/test_opening_promo.py
使用本机 Postgres 独立测试库，不打印连接串或密钥。含并发名额测试。"""
import os, sys, random, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    dsn = os.environ.get("LVHAN_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/lvhan_opening_v1")
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_opening_v1"
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
    from fastapi.testclient import TestClient
    import community as C
    import opening_promo as OP

    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "COOKIE_INSECURE", "ADMIN_PASSWORD")}
    os.environ["DATABASE_URL"] = dsn
    os.environ["COOKIE_INSECURE"] = "1"
    os.environ["ADMIN_PASSWORD"] = "test-admin-pw-1234"
    C._schema_ready = False
    C._psycopg = None
    app = FastAPI()
    app.include_router(C.router)
    H = {"X-LH": "1"}
    rnd = random.randint(100000, 999999)

    try:
        guest = TestClient(app)
        adm = TestClient(app)
        user = TestClient(app)

        home = (Path(ROOT) / "static" / "index.html").read_text(encoding="utf-8")
        check("home has banner hook", "openPromo" in home and "开业活动" in home and "/api/opening-promo" in home)
        r = guest.get("/points")
        check("points page has 开业", r.status_code == 200 and "开业活动" in r.text and "开业双倍" in r.text, r.status_code)
        pub = guest.get("/api/opening-promo").json()
        check("public default off", pub["enabled"] is False and pub["active"] is False and pub["checkin_points"] == 10, pub)
        check("public remaining cap", pub["cap"] == 100 and pub["remaining"] == 100, pub)
        check("public rules", "开业双倍" in pub["rules"] and "100" in pub["rules"] and "地铁攻略卡" in pub["rules"], pub)

        r = user.post("/api/community/register", json={
            "account": f"op{rnd}@example.com", "password": "Passw0rd!xyz", "nickname": "开业用户", "agree": True
        }, headers=H)
        check("register", r.status_code == 200, r.text)
        cps = user.get("/api/coupons").json()["coupons"]
        sub = [c for c in cps if c["kind"]=="subway_guide_card"]
        check("subway card on register", sub and sub[0]["title"]=="地铁攻略卡" and sub[0]["href"]=="/?tab=sub", cps)
        r = user.post("/api/points/checkin", json={}, headers=H)
        check("checkin off promo is 10", r.status_code == 200 and r.json()["awarded"] == 10, r.text)
        check("no double badge when off", r.json().get("badge") in (None, ""), r.json())

        r = adm.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H)
        check("admin login", r.status_code == 200, r.text)
        now = datetime.now(timezone(timedelta(hours=9)))
        starts = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        ends = (now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M")
        r = adm.post("/api/admin/opening-promo", json={"enabled": True, "starts_at": starts, "ends_at": ends}, headers=H)
        check("enable promo", r.status_code == 200 and r.json()["active"] is True, r.text)
        pub = guest.get("/api/opening-promo").json()
        check("public active", pub["active"] is True and pub["checkin_points"] == 20 and pub["badge"]=="开业双倍", pub)
        check("remaining text fields", "还剩" not in str(pub["remaining"]) and isinstance(pub["remaining"], int), pub)

        # existing user already checked in today: second checkin still 0; new day would be 20.
        # Use a second user for double checkin.
        u2 = TestClient(app)
        r = u2.post("/api/community/register", json={
            "account": f"op2{rnd}@example.com", "password": "Passw0rd!xyz", "nickname": "双倍用户", "agree": True
        }, headers=H)
        check("register during promo", r.status_code == 200, r.text)
        p = u2.get("/api/points").json()
        check("new user got +50 bonus", p["balance"] >= 50 and any(h.get("points")==50 and "开业" in (h.get("note") or h.get("label") or "") for h in p["history"]), p["history"][:3])
        pub = guest.get("/api/opening-promo").json()
        check("remaining decreased by 1", pub["remaining"] == 99, pub)
        r = u2.post("/api/points/checkin", json={}, headers=H)
        check("checkin during promo is 20", r.status_code == 200 and r.json()["awarded"]==20 and r.json().get("badge")=="开业双倍", r.text)
        check("balance 70", r.json()["balance"] == 70, r.json())

        # banned user cannot take a slot: create via SQL then try_opening_bonus
        with C.db() as conn:
            row = C.q(conn, """INSERT INTO c_users(email,nickname,pw_hash,is_banned,last_login)
                               VALUES (%s,'封禁者','x',TRUE,now()) RETURNING id""", (f"ban{rnd}@example.com",), one=True)
            res = OP.try_opening_bonus(conn, row["id"])
        check("banned excluded", res["awarded"] is False and "封禁" in res["reason"], res)
        check("remaining still 99", guest.get("/api/opening-promo").json()["remaining"] == 99)

        # admin list + csv
        lst = adm.get("/api/admin/opening-promo").json()
        check("admin lists winners", len(lst["winners"]) == 1 and lst["winners"][0]["rank"]==1, lst)
        csv = adm.get("/api/admin/opening-promo.csv")
        check("csv", csv.status_code==200 and "rank" in csv.text and "双倍用户" in csv.text, csv.text[:200])
        check("csv auth", guest.get("/api/admin/opening-promo.csv").status_code in (401,403))
        check("set needs csrf", adm.post("/api/admin/opening-promo", json={"enabled": True}).status_code==403)

        # ---- concurrency cap: 80 more users racing (already 1 taken, target 100, try 80 + leftover)
        # insert many users then grant in parallel
        extra = 120
        ids = []
        with C.db() as conn:
            for i in range(extra):
                r = C.q(conn, """INSERT INTO c_users(email,nickname,pw_hash,last_login)
                                 VALUES (%s,%s,'x',now()) RETURNING id""",
                        (f"race{i}-{rnd}@example.com", f"并发{i}"), one=True)
                ids.append(r["id"])

        lock = threading.Lock()
        results = []

        def grant_one(uid):
            with C.db() as conn:
                return OP.try_opening_bonus(conn, uid)

        with ThreadPoolExecutor(max_workers=20) as ex:
            futs = [ex.submit(grant_one, uid) for uid in ids]
            for f in as_completed(futs):
                results.append(f.result())
        won = [r for r in results if r.get("awarded")]
        lost = [r for r in results if not r.get("awarded")]
        check("concurrency awarded 99 more (total 100)", len(won)==99, (len(won), len(lost), lost[:2]))
        ranks = sorted(r["rank"] for r in won)
        check("ranks unique 2-100", ranks == list(range(2, 101)), ranks[:5],)
        pub = guest.get("/api/opening-promo").json()
        check("remaining 0 after cap", pub["remaining"]==0, pub)
        with C.db() as conn:
            n = C.q(conn, "SELECT count(*) AS n, min(rank) AS a, max(rank) AS b FROM c_opening_bonus", one=True)
            counter = C.q(conn, "SELECT n FROM c_opening_counter WHERE id=1", one=True)["n"]
        check("table has exactly 100", n["n"]==100 and n["a"]==1 and n["b"]==100, n)
        check("counter is 100 not more", counter==100, counter)
        # one more should fail
        with C.db() as conn:
            r = C.q(conn, """INSERT INTO c_users(email,nickname,pw_hash,last_login)
                             VALUES (%s,'迟到','x',now()) RETURNING id""", (f"late{rnd}@example.com",), one=True)
            late = OP.try_opening_bonus(conn, r["id"])
        check("over cap rejected", late["awarded"] is False and "名额" in late["reason"], late)
        with C.db() as conn:
            n2 = C.q(conn, "SELECT count(*) AS n FROM c_opening_bonus", one=True)["n"]
        check("still exactly 100", n2==100, n2)

        # disable: checkin back to 10 for a user who hasn't checked in
        r = adm.post("/api/admin/opening-promo", json={"enabled": False, "starts_at": starts, "ends_at": ends}, headers=H)
        check("disable", r.status_code==200 and r.json()["active"] is False, r.text)
        # user already checked in today
        r = user.post("/api/points/checkin", json={}, headers=H)
        check("already checked in today", r.json()["awarded"]==0, r.text)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        C._schema_ready = False
        C._psycopg = None

    print("RESULT", "ok", ok, "fail", fail)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
