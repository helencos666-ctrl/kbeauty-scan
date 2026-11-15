"""旅行豆 / 新人券 / 推荐店铺：python test/test_points.py
使用本机 Postgres（与 test_community 相同的无密码测试实例），不打印连接串或密钥。"""
import os, sys, random
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
        print("FAIL", name, str(info)[:300])


def main():
    dsn = os.environ.get("LVHAN_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/lvhan_points_v1")
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_points_v1"
    if not dbname.isidentifier() or not dbname.isascii():
        raise RuntimeError("bad test db name")
    try:
        import psycopg
        admin = psycopg.connect("postgresql://postgres@127.0.0.1:54329/postgres", connect_timeout=3)
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (dbname,))
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
    app.include_router(C.router)
    H = {"X-LH": "1"}
    rnd = random.randint(100000, 999999)

    try:
        cli = TestClient(app)
        # unauth
        r = cli.get("/api/points")
        check("GET /api/points requires auth", r.status_code == 401, r.text)
        r = cli.post("/api/points/checkin", json={})
        check("POST checkin requires CSRF", r.status_code == 403, r.text)
        r = cli.post("/api/points/checkin", json={}, headers=H)
        check("POST checkin requires auth", r.status_code == 401, r.text)

        r = cli.post("/api/community/register", json={
            "account": f"pt{rnd}@example.com", "password": "Passw0rd!xyz",
            "nickname": "豆豆用户", "agree": True
        }, headers=H)
        check("register ok", r.status_code == 200, r.text)

        me = cli.get("/api/community/me").json()
        check("me has points fields", me.get("user") and me["user"].get("points_name") == "旅行豆" and me["user"].get("points_balance") == 0, me.get("user"))
        check("welcome coupon counted on me", me["user"].get("coupon_available") == 1, me.get("user"))

        r = cli.get("/api/coupons")
        check("GET /api/coupons", r.status_code == 200, r.text)
        cps = r.json()["coupons"]
        check("one welcome coupon", len(cps) == 1 and cps[0]["title"] == "新人券 立减¥9.9" and cps[0]["amount"] == "9.9" and cps[0]["status"] == "available", cps)
        check("coupon scope placeholder", "会员开通（待定）" in cps[0]["scope_note"] and "收款码金额不变" in cps[0]["staff_note"], cps[0])
        check("coupon flags 9.9 free tier", "相当于免费" in (cps[0].get("flag") or ""), cps[0])

        r = cli.get("/api/points")
        check("GET /api/points 200", r.status_code == 200, r.text)
        p = r.json()
        check("balance starts 0", p["balance"] == 0 and p["name"] == "旅行豆" and p["checked_in_today"] is False, p)
        check("rules present", {x["kind"] for x in p["rules"]} == {"checkin", "share", "subway", "card", "profile"}, p["rules"])
        check("redeem catalog placeholder", all(x["status"] == "即将上线" for x in p["catalog"]) and len(p["catalog"]) >= 2, p["catalog"])
        check("coupon also in points", any(c["kind"] == "welcome_99" for c in p["coupons"]), p.get("coupons"))

        r = cli.post("/api/points/checkin", json={}, headers=H)
        check("checkin awards 10", r.status_code == 200 and r.json()["awarded"] == 10 and r.json()["balance"] == 10, r.text)
        r = cli.post("/api/points/checkin", json={}, headers=H)
        check("second checkin same day no award", r.status_code == 200 and r.json()["awarded"] == 0 and r.json()["already"] is True and r.json()["balance"] == 10, r.text)
        check("streak at least 1 after checkin", r.json().get("streak", 0) >= 1, r.json())

        r = cli.post("/api/points/event", json={"kind": "subway", "target_id": "Myeongdong-Hongdae", "idempotency_key": "sub-1"}, headers=H)
        check("subway +2", r.status_code == 200 and r.json()["awarded"] == 2 and r.json()["balance"] == 12, r.text)
        r = cli.post("/api/points/event", json={"kind": "subway", "target_id": "Myeongdong-Hongdae", "idempotency_key": "sub-1"}, headers=H)
        check("idempotent subway", r.status_code == 200 and r.json()["awarded"] == 0 and r.json()["balance"] == 12, r.text)
        r = cli.post("/api/points/event", json={"kind": "card", "target_id": "f1"}, headers=H)
        check("card +2", r.status_code == 200 and r.json()["awarded"] == 2, r.text)

        r = cli.post("/api/points/event", json={"kind": "profile"}, headers=H)
        check("profile without avatar rejected", r.status_code == 400, r.text)

        # share cap and dedupe
        r = cli.post("/api/points/event", json={"kind": "share", "target_type": "post", "target_id": "11"}, headers=H)
        check("share post +5", r.status_code == 200 and r.json()["awarded"] == 5, r.text)
        r = cli.post("/api/points/event", json={"kind": "share", "target_type": "post", "target_id": "11"}, headers=H)
        check("same post same day deduped", r.status_code == 200 and r.json()["awarded"] == 0 and "已经领过" in (r.json().get("reason") or ""), r.text)
        r = cli.post("/api/points/event", json={"kind": "share", "target_type": "shop", "target_id": "21"}, headers=H)
        check("share other item +5", r.status_code == 200 and r.json()["awarded"] == 5, r.text)
        r = cli.post("/api/points/event", json={"kind": "share", "target_type": "merchant", "target_id": "m1"}, headers=H)
        check("third share +5", r.status_code == 200 and r.json()["awarded"] == 5, r.text)
        r = cli.post("/api/points/event", json={"kind": "share", "target_type": "shop", "target_id": "22"}, headers=H)
        check("fourth share hits daily cap", r.status_code == 200 and r.json()["awarded"] == 0 and "3 次" in (r.json().get("reason") or ""), r.text)

        r = cli.post("/api/points/redeem", json={"id": "member_discount"}, headers=H)
        check("redeem still placeholder", r.status_code == 400 and "即将上线" in r.text, r.text)

        # shops
        r = cli.get("/api/shops")
        check("public shops empty", r.status_code == 200 and r.json()["items"] == [], r.text)
        r = cli.post("/api/shops", json={"name": "见 https://evil.com", "area": "明洞", "type": "美食", "description": "还可以的一家店"}, headers=H)
        check("reject url in name", r.status_code == 400, r.text)
        r = cli.post("/api/shops", json={"name": "某咖啡", "area": "明洞", "type": "咖啡", "description": "电话13812345678不错"}, headers=H)
        check("reject phone in desc", r.status_code == 400, r.text)
        r = cli.post("/api/shops", json={"name": "某咖啡", "area": "月球", "type": "咖啡", "description": "环境不错座位多"}, headers=H)
        check("reject bad area", r.status_code == 400, r.text)
        r = cli.post("/api/shops", json={"name": "圣水小店<script>", "area": "圣水", "type": "购物", "description": "逛着舒服，东西一般"}, headers=H)
        check("submit shop pending", r.status_code == 200 and r.json()["item"]["status"] == "pending", r.text)
        sid = r.json()["item"]["id"]
        check("pending not public", cli.get("/api/shops").json()["items"] == [])
        mine = cli.get("/api/shops/mine").json()["items"]
        check("mine sees pending", len(mine) == 1 and mine[0]["id"] == sid and mine[0]["status"] == "pending", mine)

        # admin
        r = cli.get("/api/admin/shop-recs")
        check("admin shops requires admin", r.status_code == 401, r.text)
        adm = TestClient(app)
        r = adm.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H)
        check("admin login", r.status_code == 200, r.text)
        pending = adm.get("/api/admin/shop-recs?status=pending").json()["items"]
        check("admin sees pending shop", any(x["id"] == sid and "<script>" in x["name"] for x in pending), pending)
        ov = adm.get("/api/admin/overview").json()
        check("overview shop_pending", ov.get("shop_pending") == 1, ov)
        r = adm.post(f"/api/admin/shop-recs/{sid}", json={"action": "approve"}, headers=H)
        check("admin approve awards 10", r.status_code == 200 and r.json()["awarded"] == 10, r.text)
        r = adm.post(f"/api/admin/shop-recs/{sid}", json={"action": "approve"}, headers=H)
        check("second approve rejected", r.status_code == 400, r.text)
        pub = cli.get("/api/shops").json()["items"]
        check("approved shop is public and escaped as data", len(pub) == 1 and pub[0]["name"] == "圣水小店<script>" and pub[0]["status"] == "approved", pub)
        pts = cli.get("/api/points").json()
        check("user got shop_approve beans", any(h["kind"] == "shop_approve" and h["points"] == 10 for h in pts["history"]), pts["history"])

        # pages
        for path, word in (("/points", "旅行豆"), ("/coupons", "新人券"), ("/shops", "推荐店铺"), ("/member", "旅行豆")):
            html = cli.get(path)
            check(path + " 200", html.status_code == 200 and word in html.text and "奋韩" not in html.text and "分含" not in html.text, html.status_code)

        # second register also gets exactly one coupon
        cli2 = TestClient(app)
        r = cli2.post("/api/community/register", json={
            "account": f"pt2{rnd}@example.com", "password": "Passw0rd!xyz",
            "nickname": "另一用户", "agree": True
        }, headers=H)
        check("second user coupon once", r.status_code == 200 and len(cli2.get("/api/coupons").json()["coupons"]) == 1, r.text)
        check("points event unknown kind", cli2.post("/api/points/event", json={"kind": "hack"}, headers=H).status_code == 400)
        check("share missing target", cli2.post("/api/points/event", json={"kind": "share"}, headers=H).status_code == 400)

        # no secrets in new frontend
        for rel in ("static/points.html", "static/coupons.html", "static/shops.html", "static/community.js", "points.py"):
            txt = Path(ROOT, rel).read_text(encoding="utf-8")
            check("no secrets/competitor in " + rel, "DATABASE_URL" not in txt and "奋韩" not in txt and "分含" not in txt and "sk-" not in txt)
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
