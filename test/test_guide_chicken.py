"""支付/退税/签证/路线/避雷/炸鸡代点：python test/test_guide_chicken.py
使用本机 Postgres（与 test_points 相同模式的独立库），不打印连接串或密钥。"""
import os, sys, random, re

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
    dsn = os.environ.get("LVHAN_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/lvhan_guide_v1")
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_guide_v1"
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

        # pages
        pages = {
            "/pay": ["T-money", "闸机", "支付宝", "以现场为准"],
            "/tax-refund": ["即时退税", "15,000", "1,000,000", "5,000,000", "查阅"],
            "/visa": ["2026-12-31", "2026-11-15", "不代办签证", "K-ETA", "驻上海", "指定旅行社", "3 人及以上", "15 天", "2027"],
            "/itineraries": ["首次经典", "购物美妆", "追星", "美食", "itineraries.json"],
            "/chicken": ["个人代点", "非持牌", "过敏"],
            "/shops": ["避雷"],
            "/rate": ["韩元"],
        }
        for path, needles in pages.items():
            r = cli.get(path)
            check("GET %s 200" % path, r.status_code == 200, r.status_code)
            text = r.text
            for n in needles:
                check("%s contains %s" % (path, n), n in text, n)

        visa = cli.get("/visa").text
        check("visa does not claim group scheme ended 2026-06-30",
              "延长至 2026-12-31" in visa and "并未" in visa and "仍然有效" in visa)
        check("visa cites official standard", "使领馆" in visa and "法务部" in visa)
        check("visa notes no 2027 extension announcement", "2027" in visa and "尚未" in visa)
        check("visa keeps Jeju 30-day", "30 天" in visa and "直达济州" in visa)
        check("visa cites shanghai notice url", "seq=760833" in visa)

        # APIs unauth
        r = cli.get("/api/chicken/shops")
        check("GET /api/chicken/shops public", r.status_code == 200, r.text)
        js = r.json()
        check("three seed shops", len(js.get("shops") or []) == 3, js)
        check("shops are examples", all(s.get("is_example") for s in js["shops"]), js)
        check("fee default 3000", js.get("fee_krw") == 3000, js)
        check("rate present", js.get("rate_cny_krw") and js.get("rate_source") == "manual", js)
        check("chicken notice", "非持牌" in (js.get("notice") or ""), js)

        r = cli.post("/api/chicken/orders", json={}, headers=H)
        check("chicken order requires auth", r.status_code == 401, r.text)

        r = cli.get("/api/chicken/orders/mine")
        check("mine orders require auth", r.status_code == 401, r.text)

        r = cli.get("/api/shops")
        check("shops types include 避雷经验", "避雷经验" in (r.json().get("types") or []), r.text)

        # register user
        r = cli.post("/api/community/register", json={
            "account": f"ck{rnd}@example.com", "password": "Passw0rd!xyz",
            "nickname": "代点用户", "agree": True
        }, headers=H)
        check("register ok", r.status_code == 200, r.text)

        shop_id = js["shops"][0]["id"]
        menu_id = js["shops"][0]["menu"][0]["id"]
        price = js["shops"][0]["menu"][0]["price_krw"]

        r = cli.post("/api/chicken/orders", json={
            "shop_id": shop_id, "items": [{"id": menu_id, "qty": 2}],
            "hotel_name": "明洞测试酒店", "hotel_address": "首尔中区测试路 1",
            "wechat": "test_wx_id", "desired_time": "今天 19:30", "notes": "对海鲜过敏"
        }, headers=H)
        check("submit chicken order", r.status_code == 200, r.text)
        order = r.json().get("order") or {}
        check("order pending_review", order.get("status") == "pending_review", order)
        check("order totals", order.get("subtotal_krw") == price * 2 and order.get("fee_krw") == 3000
              and order.get("total_krw") == price * 2 + 3000, order)
        check("user order hides wechat", "wechat_id" not in order and order.get("has_wechat") is True, order)
        check("wechat not echoed in body", "test_wx_id" not in r.text, r.text)

        r = cli.post("/api/chicken/orders", json={
            "shop_id": shop_id, "items": [{"id": menu_id, "qty": 1}],
            "hotel_name": "另一酒店", "hotel_address": "首尔测试路 2",
            "wechat": "bad wx!!", "desired_time": "", "notes": ""
        }, headers=H)
        check("invalid wechat rejected", r.status_code == 400, r.text)
        check("invalid wechat not echoed", "bad wx" not in r.text, r.text)

        r = cli.get("/api/chicken/orders/mine")
        check("mine lists order", r.status_code == 200 and len(r.json().get("orders") or []) == 1, r.text)
        check("mine hides wechat", "test_wx_id" not in r.text, r.text)

        # 避雷 submit
        r = cli.post("/api/shops", json={
            "name": "某路口换汇", "area": "明洞", "type": "避雷经验",
            "description": "有人主动搭讪换汇，报价明显异常，建议走开。"
        }, headers=H)
        check("submit 避雷经验", r.status_code == 200, r.text)
        pit_id = r.json()["item"]["id"]
        r = cli.get("/api/shops")
        check("pitfall not public before review", all(x["id"] != pit_id for x in r.json()["items"]), r.text)

        # admin
        r = cli.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H)
        check("admin login", r.status_code == 200, r.text)

        r = cli.get("/api/admin/chicken/orders?status=pending_review")
        check("admin sees wechat", r.status_code == 200 and any(o.get("wechat_id") == "test_wx_id" for o in r.json()["orders"]), r.text)
        oid = r.json()["orders"][0]["id"]

        r = cli.post(f"/api/admin/chicken/orders/{oid}", json={"action": "approve"}, headers=H)
        check("admin approve order", r.status_code == 200 and r.json()["order"]["status"] == "approved", r.text)
        r = cli.post(f"/api/admin/chicken/orders/{oid}", json={"action": "mark-paid"}, headers=H)
        check("admin mark-paid", r.status_code == 200 and r.json()["order"]["status"] == "paid", r.text)
        r = cli.post(f"/api/admin/chicken/orders/{oid}", json={"action": "mark-delivered"}, headers=H)
        check("admin mark-delivered", r.status_code == 200 and r.json()["order"]["status"] == "delivered", r.text)

        r = cli.post("/api/admin/shop-recs/%s" % pit_id, json={"action": "approve"}, headers=H)
        check("admin approve pitfall", r.status_code == 200, r.text)
        r = cli.get("/api/shops?shop_type=避雷经验")
        check("pitfall public after approve", any(x["id"] == pit_id and x["shop_type"] == "避雷经验" for x in r.json()["items"]), r.text)

        # editable shops
        r = cli.post("/api/admin/chicken/shops", json={
            "name": "测试店铺", "area": "梨泰院", "is_example": False, "active": True,
            "menu": [{"id": "t1", "name": "测试鸡", "price_krw": 17000}]
        }, headers=H)
        check("admin add shop", r.status_code == 200 and r.json()["shop"]["name"] == "测试店铺", r.text)

        # fee setting
        r = cli.post("/api/admin/settings", json={"chicken_fee_krw": "4000"}, headers=H)
        check("update chicken fee", r.status_code == 200, r.text)
        r = cli.get("/api/chicken/shops")
        check("fee updated to 4000", r.json().get("fee_krw") == 4000, r.text)

        r = cli.get("/api/community/rate")
        check("rate api ok", r.status_code == 200 and r.json().get("source") == "manual", r.text)

        # no secrets leaked in any recent response we still have — scan chicken pages
        for path in ("/chicken", "/api/chicken/shops"):
            txt = cli.get(path).text
            check("no dsn on %s" % path, "DATABASE_URL" not in txt and "postgresql://" not in txt, "")

    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print("---- %d passed, %d failed ----" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
