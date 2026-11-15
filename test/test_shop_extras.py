"""店铺菜单价格、评价与位置分享：python test/test_shop_extras.py
使用本机 Postgres 独立测试库，不打印连接串或密钥。"""
import os, sys, random, json
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
    dsn = os.environ.get("LVHAN_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/lvhan_shop_extras_v1")
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_shop_extras_v1"
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
        for path, words in (("/shops", ["推荐店铺", "菜单", "评价", "Naver", "Kakao", "Google"]),
                            ("/chicken", ["代点店铺", "评价", "Naver", "复制地址"])):
            r = cli.get(path)
            check("%s 200" % path, r.status_code == 200, r.status_code)
            for w in words:
                check("%s has %s" % (path, w), w in r.text, w)
            check("%s no competitor wording" % path, "奋韩" not in r.text and "分含" not in r.text)

        r = cli.get("/api/chicken/shops")
        check("chicken shops 200", r.status_code == 200, r.text)
        js = r.json()
        shops = js.get("shops") or []
        check("seed has address and map links", all(s.get("address") and s.get("map_links", {}).get("naver") for s in shops), shops[:1])
        check("map links are search URLs without API keys", all(
            "map.naver.com" in (s.get("map_links") or {}).get("naver", "")
            and "map.kakao.com" in (s.get("map_links") or {}).get("kakao", "")
            and "google.com/maps/search" in (s.get("map_links") or {}).get("google", "")
            and "key=" not in json.dumps(s.get("map_links"))
            for s in shops
        ), shops[:1])
        m0 = shops[0]["menu"][0]
        check("menu has price_cny", m0.get("price_krw") and m0.get("price_cny") is not None, m0)
        check("ratings default zero", shops[0].get("rating_count") == 0 and shops[0].get("rating_avg") is None, shops[0])
        ck_id = shops[0]["id"]

        r = cli.get("/api/shop-reviews?kind=chicken&shop_id=%s" % ck_id)
        check("public reviews empty", r.status_code == 200 and r.json()["items"] == [] and r.json()["rating_count"] == 0, r.text)
        r = cli.post("/api/shop-reviews", json={"kind": "chicken", "shop_id": ck_id, "stars": 5, "body": "味道不错值得再来"})
        check("review requires CSRF", r.status_code == 403, r.text)
        r = cli.post("/api/shop-reviews", json={"kind": "chicken", "shop_id": ck_id, "stars": 5, "body": "味道不错值得再来"}, headers=H)
        check("review requires auth", r.status_code == 401, r.text)

        r = cli.post("/api/community/register", json={
            "account": f"sx{rnd}@example.com", "password": "Passw0rd!xyz",
            "nickname": "评价用户", "agree": True
        }, headers=H)
        check("register", r.status_code == 200, r.text)
        adm = TestClient(app)
        r = adm.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H)
        check("admin login (early)", r.status_code == 200, r.text)

        def approve_pending():
            # 评价先审后发：把待审核的评价全部通过
            for it in adm.get("/api/admin/shop-reviews?status=pending").json()["items"]:
                adm.post("/api/admin/shop-reviews/%s" % it["id"], json={"action": "approve"}, headers=H)

        r = cli.post("/api/shop-reviews", json={"kind": "chicken", "shop_id": ck_id, "stars": 6, "body": "味道不错值得再来"}, headers=H)
        check("stars out of range", r.status_code == 400, r.text)
        r = cli.post("/api/shop-reviews", json={"kind": "chicken", "shop_id": ck_id, "stars": 5, "body": "短"}, headers=H)
        check("body too short", r.status_code == 400, r.text)
        r = cli.post("/api/shop-reviews", json={"kind": "chicken", "shop_id": ck_id, "stars": 5, "body": "加微详聊请加微信 abcde123"}, headers=H)
        check("reject contact in review", r.status_code == 400, r.text)
        r = cli.post("/api/shop-reviews", json={"kind": "chicken", "shop_id": ck_id, "stars": 5, "body": "见 https://evil.example 这家很好"}, headers=H)
        check("reject url in review", r.status_code == 400, r.text)
        r = cli.post("/api/shop-reviews", json={"kind": "chicken", "shop_id": ck_id, "stars": 5, "body": "让我转账再给优惠，稳赚"}, headers=H)
        check("reject scam wording", r.status_code == 400, r.text)

        r = cli.post("/api/shop-reviews", json={
            "kind": "chicken", "shop_id": ck_id, "stars": 5,
            "body": "外皮很脆<script>alert(1)</script>，还可以再来。"
        }, headers=H)
        check("submit review", r.status_code == 200 and r.json().get("updated") is False, r.text)
        item = r.json()["item"]
        check("stored body keeps brackets as data", "<script>alert(1)</script>" in item["body"], item)
        approve_pending()
        r = cli.get("/api/shop-reviews?kind=chicken&shop_id=%s" % ck_id)
        js = r.json()
        check("review listed", js["rating_count"] == 1 and js["rating_avg"] == 5.0, js)
        check("html not interpreted in json", js["items"][0]["body"].count("<script>") == 1, js["items"][0])

        r = cli.post("/api/shop-reviews", json={
            "kind": "chicken", "shop_id": ck_id, "stars": 3,
            "body": "第二次来，味道一般般吧。"
        }, headers=H)
        check("update own review", r.status_code == 200 and r.json().get("updated") is True, r.text)
        approve_pending()
        js = cli.get("/api/shop-reviews?kind=chicken&shop_id=%s" % ck_id).json()
        check("updated stars and single review", js["rating_count"] == 1 and js["rating_avg"] == 3.0 and js["items"][0]["stars"] == 3, js)

        # second user for average
        cli2 = TestClient(app)
        r = cli2.post("/api/community/register", json={
            "account": f"sx2{rnd}@example.com", "password": "Passw0rd!xyz",
            "nickname": "另一评价", "agree": True
        }, headers=H)
        check("second user", r.status_code == 200, r.text)
        r = cli2.post("/api/shop-reviews", json={"kind": "chicken", "shop_id": ck_id, "stars": 5, "body": "我觉得还不错再来一次"}, headers=H)
        check("second review", r.status_code == 200, r.text)
        approve_pending()
        js = cli.get("/api/shop-reviews?kind=chicken&shop_id=%s" % ck_id).json()
        check("average 4.0", js["rating_count"] == 2 and js["rating_avg"] == 4.0, js)

        # rec shop extras
        r = cli.post("/api/shops", json={
            "name": "圣水面馆", "area": "圣水", "type": "美食",
            "description": "汤头干净，座位不多。",
            "address": "서울 성동구 연무장길 10",
            "menu": [{"id": "n1", "name": "冷面", "price_krw": 11000}]
        }, headers=H)
        check("submit rec with menu/address", r.status_code == 200, r.text)
        sid = r.json()["item"]["id"]
        check("pending has extras in mine", cli.get("/api/shops/mine").json()["items"][0]["address"] and
              cli.get("/api/shops/mine").json()["items"][0]["menu"][0]["name"] == "冷面")
        check("pending not reviewable", cli.post("/api/shop-reviews", json={
            "kind": "rec", "shop_id": sid, "stars": 5, "body": "还没公开不能评吧这样"
        }, headers=H).status_code == 404)

        r = cli.get("/api/admin/shop-recs?status=pending")
        check("admin extras requires admin", r.status_code == 401, r.text)
        r = adm.post("/api/admin/shop-recs/%s" % sid, json={"action": "approve"}, headers=H)
        check("approve rec", r.status_code == 200, r.text)
        r = adm.post("/api/admin/shop-recs/%s/extras" % sid, json={
            "address": "서울 성동구 연무장길 12",
            "menu": [{"id": "n1", "name": "冷面", "price_krw": 12000}, {"id": "n2", "name": "拌饭", "price_krw": 13000}]
        }, headers=H)
        check("admin edit extras", r.status_code == 200 and r.json()["item"]["address"].endswith("12"), r.text)
        pub = cli.get("/api/shops").json()
        item = next(x for x in pub["items"] if x["id"] == sid)
        check("public rec has rate and cny", pub.get("rate_cny_krw") and item["menu"][0]["price_cny"] is not None, item)
        check("public rec map links", item["map_links"]["google"].startswith("https://www.google.com/maps/search"), item)
        r = cli.post("/api/shop-reviews", json={"kind": "rec", "shop_id": sid, "stars": 4, "body": "冷面很爽口，还会再去。"}, headers=H)
        check("review rec shop", r.status_code == 200, r.text)
        approve_pending()
        pub = cli.get("/api/shops").json()
        item = next(x for x in pub["items"] if x["id"] == sid)
        check("rec rating attached", item["rating_count"] == 1 and item["rating_avg"] == 4.0, item)

        # hide
        rid = cli.get("/api/shop-reviews?kind=rec&shop_id=%s" % sid).json()["items"][0]["id"]
        r = adm.get("/api/admin/shop-reviews?kind=rec")
        check("admin lists reviews", r.status_code == 200 and any(x["id"] == rid for x in r.json()["items"]), r.text)
        r = adm.post("/api/admin/shop-reviews/%s" % rid, json={"action": "hide"}, headers=H)
        check("admin hide", r.status_code == 200 and r.json()["item"]["hidden"] is True, r.text)
        js = cli.get("/api/shop-reviews?kind=rec&shop_id=%s" % sid).json()
        check("hidden review not public", js["rating_count"] == 0 and js["items"] == [], js)
        r = adm.post("/api/admin/shop-reviews/%s" % rid, json={"action": "unhide"}, headers=H)
        check("admin unhide", r.status_code == 200, r.text)
        js = cli.get("/api/shop-reviews?kind=rec&shop_id=%s" % sid).json()
        check("unhidden again", js["rating_count"] == 1, js)

        # chicken admin address
        r = adm.post("/api/admin/chicken/shops", json={
            "id": ck_id, "name": shops[0]["name"], "area": shops[0]["area"],
            "address": "서울 중구 테스트로 1", "is_example": True, "active": True,
            "menu": shops[0]["menu"], "notes": shops[0].get("notes") or "", "sort_order": 1
        }, headers=H)
        check("admin update chicken address", r.status_code == 200 and "테스트로" in r.json()["shop"]["address"], r.text)

        # share hook for chicken
        r = cli.post("/api/points/event", json={"kind": "share", "target_type": "chicken", "target_id": str(ck_id)}, headers=H)
        check("share chicken +5", r.status_code == 200 and r.json()["awarded"] == 5, r.text)

        # admin page mentions reviews
        html = adm.get("/admin").text
        check("admin has review tools", "评价审核" in html and "编辑菜单/地址" in html and "照片管理" in html and "奋韩" not in html)

        for rel in ("shop_extras.py", "points.py", "chicken.py", "static/shops.html", "static/chicken.html", "static/admin.html", "static/community.js"):
            txt = Path(ROOT, rel).read_text(encoding="utf-8")
            check("no secrets in " + rel, "DATABASE_URL" not in txt and "奋韩" not in txt and "分含" not in txt and "sk-" not in txt)
    finally:
        C._schema_ready = False
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print("---- %d passed, %d failed ----" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
