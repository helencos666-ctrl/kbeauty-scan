"""商家升级：照片、价格区间、评价先审后发（可带照片）、审核/隐藏/删除、详情页、限流、迁移兼容。
python test/test_merchant_upgrade.py   （本机 Postgres 独立测试库，不打印连接串或密钥）"""
import os, sys, io, random, subprocess, tempfile, shutil

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ok = fail = 0
PG = "postgresql://postgres@127.0.0.1:54329/"


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS", name)
    else:
        fail += 1
        print("FAIL", name, str(info)[:300])


def jpeg(color=(200, 80, 120), size=(1600, 1200)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "JPEG", quality=85)
    return b.getvalue()


def fresh_db(name):
    import psycopg
    a = psycopg.connect(PG + "postgres", connect_timeout=3)
    a.autocommit = True
    with a.cursor() as cur:
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (name,))
        cur.execute("DROP DATABASE IF EXISTS " + name)
        cur.execute("CREATE DATABASE " + name)
    a.close()
    return PG + name


OLD_SEED = r'''
import os, sys
sys.path.insert(0, os.getcwd())
os.environ["COOKIE_INSECURE"] = "1"
import community as C
from fastapi import FastAPI
from fastapi.testclient import TestClient
app = FastAPI(); app.include_router(C.router)
H = {"X-LH": "1"}
cli = TestClient(app)
r = cli.post("/api/community/register", json={"account": "old@example.com", "password": "Passw0rd!xyz", "nickname": "老用户", "agree": True}, headers=H)
assert r.status_code == 200, r.text
r = cli.post("/api/shops", json={"name": "老店铺", "area": "明洞", "type": "美食", "description": "升级前的数据", "address": "서울 중구 명동길 1"}, headers=H)
sid = r.json()["item"]["id"]
adm = TestClient(app)
adm.post("/api/admin/login", json={"password": os.environ["ADMIN_PASSWORD"]}, headers=H)
adm.post("/api/admin/shop-recs/%s" % sid, json={"action": "approve"}, headers=H)
r = cli.post("/api/shop-reviews", json={"kind": "rec", "shop_id": sid, "stars": 4, "body": "升级前就公开的评价"}, headers=H)
assert r.status_code == 200, r.text
print("SEEDED", sid)
'''


def migration_test(py):
    """用升级前的代码（cfda814）建库并写入数据，再用新代码迁移，检查旧数据不丢、旧评价保持公开。"""
    name = "lvhan_merchant_mig_v1"
    dsn = fresh_db(name)
    tmp = tempfile.mkdtemp()
    try:
        subprocess.run(["git", "-C", ROOT, "worktree", "add", "--detach", tmp, "cfda814"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        env = dict(os.environ, DATABASE_URL=dsn, ADMIN_PASSWORD="test-admin-pw-1234")
        out = subprocess.run([py, "-c", OLD_SEED], cwd=tmp, env=env, capture_output=True, text=True)
        check("old code seeds data", "SEEDED" in out.stdout, out.stderr[-300:])
    finally:
        subprocess.run(["git", "-C", ROOT, "worktree", "remove", "--force", tmp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(tmp, ignore_errors=True)
    return dsn


def main():
    try:
        import psycopg  # noqa
        fresh_db("lvhan_merchant_probe")
    except Exception as e:
        print("SKIP: no local postgres (%s)" % str(e)[:80])
        return 0

    dsn_mig = migration_test(sys.executable)
    dsn = fresh_db("lvhan_merchant_v1")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import community as C

    os.environ["COOKIE_INSECURE"] = "1"
    os.environ["ADMIN_PASSWORD"] = "test-admin-pw-1234"
    H = {"X-LH": "1"}

    # ---- 迁移：新代码打开旧库 ----
    os.environ["DATABASE_URL"] = dsn_mig
    C._schema_ready = False
    app = FastAPI(); app.include_router(C.router)
    cli = TestClient(app)
    js = cli.get("/api/shops").json()
    old = next((x for x in js["items"] if x["name"] == "老店铺"), None)
    check("migrated: old shop still public", old is not None, js)
    if old:
        check("migrated: old shop address kept", old["address"].endswith("명동길 1"), old)
        check("migrated: old shop has new fields", old["photos"] == [] and old["price_text"] == "" and old["is_example"] is False, old)
        rv = cli.get("/api/shop-reviews?kind=rec&shop_id=%s" % old["id"]).json()
        check("migrated: old review stays visible (status approved)", rv["rating_count"] == 1 and rv["items"][0]["body"] == "升级前就公开的评价", rv)
    # 迁移可重复执行
    import psycopg
    from shop_extras import MIGRATE_MERCHANT
    with psycopg.connect(dsn_mig) as conn, conn.cursor() as cur:
        cur.execute(MIGRATE_MERCHANT); cur.execute(MIGRATE_MERCHANT)
        cur.execute("SELECT count(*) FROM c_shop_reviews"); n = cur.fetchone()[0]
    check("migration idempotent, no rows lost", n == 1, n)

    # ---- 新功能：全新库 ----
    os.environ["DATABASE_URL"] = dsn
    C._schema_ready = False
    rnd = random.randint(100000, 999999)
    cli = TestClient(app)
    adm = TestClient(app)
    check("admin login", adm.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H).status_code == 200)

    # 新增商家
    base = {"name": "测试烤肉", "area": "明洞", "type": "美食", "description": "测试用商家简介",
            "address": "서울 중구 명동길 14", "price_min": 15000, "price_max": 30000, "price_note": "含小菜",
            "menu": [{"id": "m1", "name": "五花肉", "price_krw": 16000}]}
    check("create merchant needs admin", cli.post("/api/admin/merchants", json=base, headers=H).status_code == 401)
    r = adm.post("/api/admin/merchants", json=dict(base, price_min=40000), headers=H)
    check("price min>max rejected", r.status_code == 400, r.text)
    r = adm.post("/api/admin/merchants", json=dict(base, description="加微信 abc12345 详聊"), headers=H)
    check("contact in description rejected", r.status_code == 400, r.text)
    r = adm.post("/api/admin/merchants", json=base, headers=H)
    check("create merchant", r.status_code == 200 and r.json()["item"]["official"], r.text)
    sid = r.json()["item"]["id"]
    item = r.json()["item"]
    check("price_text range", item["price_text"] == "₩15,000–30,000", item.get("price_text"))
    r = adm.post("/api/admin/shop-recs/%s/extras" % sid, json={"is_example": True, "price_max": 35000, "price_min": 15000, "price_note": "含小菜"}, headers=H)
    check("edit partial keeps address", r.status_code == 200 and r.json()["item"]["address"].endswith("14") and r.json()["item"]["is_example"], r.text)

    pub = cli.get("/api/shops").json()
    it = next(x for x in pub["items"] if x["id"] == sid)
    check("public list has price + example flag", it["price_text"] == "₩15,000–35,000" and it["is_example"] and it["cover"] is None, it)

    # 照片
    files = [("images", ("a.jpg", jpeg((10, 20, 30)), "image/jpeg")), ("images", ("b.jpg", jpeg((200, 50, 50)), "image/jpeg"))]
    r = cli.post("/api/admin/shop-photos", data={"kind": "rec", "shop_id": str(sid)}, files=files, headers=H)
    check("photo upload needs admin", r.status_code == 401, r.text)
    r = adm.post("/api/admin/shop-photos", data={"kind": "rec", "shop_id": str(sid)}, files=files)
    check("photo upload needs csrf header", r.status_code == 403, r.text)
    r = adm.post("/api/admin/shop-photos", data={"kind": "rec", "shop_id": str(sid)}, files=files, headers=H)
    check("photo upload 2", r.status_code == 200 and len(r.json()["items"]) == 2, r.text)
    p1, p2 = [p["id"] for p in r.json()["items"]]
    check("photo resized to <=1280", max(r.json()["items"][0]["w"], r.json()["items"][0]["h"]) <= 1280, r.json()["items"][0])
    r = adm.post("/api/admin/shop-photos", data={"kind": "rec", "shop_id": str(sid)},
                 files=[("images", ("x%d.jpg" % i, jpeg(), "image/jpeg")) for i in range(7)], headers=H)
    check("more than 8 photos rejected", r.status_code == 400, r.text)
    r = adm.post("/api/admin/shop-photos", data={"kind": "rec", "shop_id": str(sid)},
                 files=[("images", ("bad.jpg", b"not an image at all" * 10, "image/jpeg"))], headers=H)
    check("bad image rejected", r.status_code == 400, r.text)
    r = cli.get("/api/shop-photos/%s" % p1)
    check("public photo served", r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8", r.status_code)
    check("public thumb served", cli.get("/api/shop-photos/%s?t=1" % p1).status_code == 200)
    r = adm.post("/api/admin/shop-photos/%s" % p2, json={"action": "cover"}, headers=H)
    check("set cover", r.status_code == 200 and r.json()["items"][0]["id"] == p2, r.text)
    it = next(x for x in cli.get("/api/shops").json()["items"] if x["id"] == sid)
    check("list cover follows", it["cover"] == p2 and len(it["photos"]) == 2, it)
    r = adm.post("/api/admin/shop-photos/%s" % p1, json={"action": "delete"}, headers=H)
    check("delete photo", r.status_code == 200 and [p["id"] for p in r.json()["items"]] == [p2], r.text)
    check("deleted photo 404", cli.get("/api/shop-photos/%s" % p1).status_code == 404)

    # 未公开商家的照片不公开
    r = cli.post("/api/community/register", json={"account": f"mu{rnd}@example.com", "password": "Passw0rd!xyz", "nickname": "评价人", "agree": True}, headers=H)
    check("register", r.status_code == 200, r.text)
    r = cli.post("/api/shops", json={"name": "待审小店", "area": "弘大", "type": "咖啡", "description": "还在审核中的店"}, headers=H)
    pend_sid = r.json()["item"]["id"]
    r = adm.post("/api/admin/shop-photos", data={"kind": "rec", "shop_id": str(pend_sid)}, files=[("images", ("a.jpg", jpeg(), "image/jpeg"))], headers=H)
    pp = r.json()["items"][0]["id"]
    check("pending shop photo hidden from public", cli.get("/api/shop-photos/%s" % pp).status_code == 404)
    check("admin can view pending shop photo", adm.get("/api/admin/shop-photos/%s/img?t=1" % pp).status_code == 200)
    check("pending shop detail 404", cli.get("/api/shops/%s" % pend_sid).status_code == 404)

    # 详情
    r = cli.get("/api/shops/%s" % sid)
    check("detail api", r.status_code == 200 and r.json()["item"]["menu"][0]["name"] == "五花肉" and r.json()["item"]["map_links"]["naver"], r.text)
    r = cli.get("/shops/%s" % sid)
    check("detail page 200", r.status_code == 200 and "测试烤肉" in r.text and "写评价" in r.text and "奋韩" not in r.text, r.status_code)
    check("example detail page noindex", "noindex" in r.text.lower())
    check("missing detail page still 200", cli.get("/shops/999999").status_code == 200)
    check("/api/shops/mine still works", cli.get("/api/shops/mine").status_code == 200)

    # 评价：带照片 → 待审核
    anon = TestClient(app)
    r = anon.post("/api/shop-reviews", data={"kind": "rec", "shop_id": str(sid), "stars": "5", "body": "肉很好吃服务也好"},
                  files={"photo": ("r.jpg", jpeg(), "image/jpeg")}, headers=H)
    check("review photo needs login", r.status_code == 401, r.text)
    r = cli.post("/api/shop-reviews", data={"kind": "rec", "shop_id": str(sid), "stars": "5", "body": "肉很好吃服务也好"},
                 files={"photo": ("r.jpg", jpeg(), "image/jpeg")}, headers=H)
    check("submit review with photo", r.status_code == 200 and r.json()["item"]["status"] == "pending" and r.json()["item"]["has_photo"], r.text)
    check("message says needs review", "审核" in r.json().get("message", ""), r.text)
    rid = r.json()["item"]["id"]
    js = cli.get("/api/shop-reviews?kind=rec&shop_id=%s" % sid).json()
    check("pending review not public", js["rating_count"] == 0 and js["items"] == [], js)
    check("author sees own pending", js["mine"] and js["mine"]["status"] == "pending", js.get("mine"))
    check("author sees own photo", cli.get(js["mine"]["photo_thumb"]).status_code == 200)
    check("public photo 404 before approval", anon.get("/api/shop-review-photo/%s" % rid).status_code == 404)
    check("anon cannot see mine photo", anon.get("/api/shop-review-photo/mine/%s" % rid).status_code == 401)
    r = adm.get("/api/admin/shop-reviews?status=pending")
    check("admin pending list", r.status_code == 200 and r.json()["pending"] >= 1 and any(x["id"] == rid and x["shop_name"] == "测试烤肉" for x in r.json()["items"]), r.text)
    check("admin sees review photo", adm.get("/api/admin/shop-review-photo/%s?t=1" % rid).status_code == 200)
    check("overview counts pending reviews", adm.get("/api/admin/overview").json().get("review_pending", 0) >= 1)
    r = adm.post("/api/admin/shop-reviews/%s" % rid, json={"action": "approve"}, headers=H)
    check("approve", r.status_code == 200 and r.json()["item"]["status"] == "approved", r.text)
    js = anon.get("/api/shop-reviews?kind=rec&shop_id=%s" % sid).json()
    check("approved review public + avg", js["rating_count"] == 1 and js["rating_avg"] == 5.0 and js["items"][0]["has_photo"], js)
    check("public review photo 200", anon.get("/api/shop-review-photo/%s?t=1" % rid).status_code == 200)

    # 第二个用户，平均分
    cli2 = TestClient(app)
    cli2.post("/api/community/register", json={"account": f"mu2{rnd}@example.com", "password": "Passw0rd!xyz", "nickname": "另一人", "agree": True}, headers=H)
    r = cli2.post("/api/shop-reviews", json={"kind": "rec", "shop_id": sid, "stars": 2, "body": "排队太久了一般般"}, headers=H)
    rid2 = r.json()["item"]["id"]
    adm.post("/api/admin/shop-reviews/%s" % rid2, json={"action": "approve"}, headers=H)
    it = next(x for x in cli.get("/api/shops").json()["items"] if x["id"] == sid)
    check("average 3.5", it["rating_count"] == 2 and it["rating_avg"] == 3.5, it)
    # 修改评价 → 重新待审核，不计入
    r = cli2.post("/api/shop-reviews", json={"kind": "rec", "shop_id": sid, "stars": 4, "body": "第二次去好多了"}, headers=H)
    check("edit goes back to pending", r.status_code == 200 and r.json()["item"]["status"] == "pending", r.text)
    js = anon.get("/api/shop-reviews?kind=rec&shop_id=%s" % sid).json()
    check("edited review hidden until re-approved", js["rating_count"] == 1, js)
    # 拒绝 / 隐藏 / 删除
    r = adm.post("/api/admin/shop-reviews/%s" % rid2, json={"action": "reject"}, headers=H)
    check("reject", r.status_code == 200 and r.json()["item"]["status"] == "rejected", r.text)
    check("author sees rejected", cli2.get("/api/shop-reviews?kind=rec&shop_id=%s" % sid).json()["mine"]["status"] == "rejected")
    r = adm.post("/api/admin/shop-reviews/%s" % rid, json={"action": "hide"}, headers=H)
    check("hide", r.status_code == 200 and r.json()["item"]["hidden"], r.text)
    js = anon.get("/api/shop-reviews?kind=rec&shop_id=%s" % sid).json()
    check("hidden not public, photo 404", js["rating_count"] == 0 and anon.get("/api/shop-review-photo/%s" % rid).status_code == 404, js)
    r = adm.post("/api/admin/shop-reviews/%s" % rid2, json={"action": "delete"}, headers=H)
    check("delete review", r.status_code == 200 and r.json()["deleted"], r.text)
    check("deleted gone from admin", all(x["id"] != rid2 for x in adm.get("/api/admin/shop-reviews").json()["items"]))
    check("unknown action 400", adm.post("/api/admin/shop-reviews/%s" % rid, json={"action": "boom"}, headers=H).status_code == 400)
    check("review action needs admin", cli.post("/api/admin/shop-reviews/%s" % rid, json={"action": "approve"}, headers=H).status_code == 401)

    # 防刷：同一用户 1 小时最多 8 次提交（含修改）
    cli3 = TestClient(app)
    cli3.post("/api/community/register", json={"account": f"mu3{rnd}@example.com", "password": "Passw0rd!xyz", "nickname": "刷子", "agree": True}, headers=H)
    codes = [cli3.post("/api/shop-reviews", json={"kind": "rec", "shop_id": sid, "stars": 5, "body": "第%d次提交评价内容" % i}, headers=H).status_code for i in range(9)]
    check("rate limit kicks in on 9th", codes[:8] == [200] * 8 and codes[8] == 429, codes)
    check("still only one review row for that user", sum(1 for x in adm.get("/api/admin/shop-reviews?kind=rec").json()["items"] if x["author"] == "刷子") == 1)

    # 后台页面
    html = adm.get("/admin").text
    check("admin page has merchant tab", "商家/评价" in html and "评价审核" in html and "照片管理" in html and "新增商家" in html)
    html = cli.get("/shops").text
    check("shops page ok", "推荐店铺" in html and "奋韩" not in html)

    print("---- %d passed, %d failed ----" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
