"""炸鸡商家入驻：加密身份信息、保证金、审核上架、营业/休息、注销。python test/test_chicken_merchant.py"""
import os, sys, io
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
ok = fail = 0
def check(name, cond, info=""):
    global ok, fail
    if cond: ok += 1; print("PASS", name)
    else: fail += 1; print("FAIL", name, str(info)[:300])
import psycopg
NAME = "lvhan_chicken_merchant_v1"
a = psycopg.connect("postgresql://postgres@127.0.0.1:54329/postgres", connect_timeout=3); a.autocommit = True
with a.cursor() as cur:
    cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (NAME,))
    cur.execute("DROP DATABASE IF EXISTS " + NAME); cur.execute("CREATE DATABASE " + NAME)
a.close()
DSN = "postgresql://postgres@127.0.0.1:54329/" + NAME
os.environ.update(DATABASE_URL=DSN, COOKIE_INSECURE="1", ADMIN_PASSWORD="test-admin-pw-1234")
os.environ.pop("LVHAN_PII_KEY", None)
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
import community as C
app = FastAPI(); app.include_router(C.router)
H = {"X-LH": "1"}
def jpg(c=(200, 200, 230)):
    b = io.BytesIO(); Image.new("RGB", (1200, 800), c).save(b, "JPEG"); return b.getvalue()
FORM = {"shop_name": "明洞测试炸鸡", "address": "서울 중구 명동길 20", "area": "明洞", "menu": "原味炸鸡,18000\n洋酿炸鸡,19000",
        "contact": "010-1234-5678", "hours": "每天 16:00-02:00", "real_name": "王小明", "id_type": "护照", "id_no": "E12345678", "agree": "1"}
u = TestClient(app)
u.post("/api/community/register", json={"account": "boss@example.com", "password": "Passw0rd!xyz", "nickname": "炸鸡老板", "agree": True}, headers=H)
check("meta closed without key", u.get("/api/chicken/merchant/meta").json()["open"] is False)
r = u.post("/api/chicken/merchant/apply", data=FORM, files={"id_photo": ("id.jpg", jpg(), "image/jpeg")}, headers=H)
check("apply refused without key", r.status_code == 503, r.text)
os.environ["LVHAN_PII_KEY"] = "unit-test-key-abcdefghijklmnopqrstuvwxyz0123"
check("page 200", u.get("/chicken/join").status_code == 200)
g = TestClient(app)
check("guest me 401", g.get("/api/chicken/merchant/me").status_code == 401)
check("guest apply 401", g.post("/api/chicken/merchant/apply", data=FORM, files={"id_photo": ("id.jpg", jpg(), "image/jpeg")}, headers=H).status_code == 401)
check("csrf", u.post("/api/chicken/merchant/apply", data=FORM, files={"id_photo": ("id.jpg", jpg(), "image/jpeg")}).status_code == 403)
bad = dict(FORM, agree="")
check("agree required", u.post("/api/chicken/merchant/apply", data=bad, files={"id_photo": ("id.jpg", jpg(), "image/jpeg")}, headers=H).status_code == 400)
bad = dict(FORM, menu="随便写写")
check("bad menu", u.post("/api/chicken/merchant/apply", data=bad, files={"id_photo": ("id.jpg", jpg(), "image/jpeg")}, headers=H).status_code == 400)
bad = dict(FORM, id_no="<script>")
check("bad id no", u.post("/api/chicken/merchant/apply", data=bad, files={"id_photo": ("id.jpg", jpg(), "image/jpeg")}, headers=H).status_code == 400)
check("bad photo", u.post("/api/chicken/merchant/apply", data=FORM, files={"id_photo": ("id.jpg", b"not image" * 20, "image/jpeg")}, headers=H).status_code == 400)
r = u.post("/api/chicken/merchant/apply", data=FORM, files={"id_photo": ("id.jpg", jpg(), "image/jpeg")}, headers=H)
check("apply ok", r.status_code == 200 and r.json()["app"]["status"] == "pending", r.text)
me = u.get("/api/chicken/merchant/me").json()["app"]
check("masked id", me["id_masked"].endswith("5678") and "E1234" not in me["id_masked"], me)
check("masked name", me["real_name_masked"] == "王**", me)
check("menu parsed", len(me["menu"]) == 2 and me["menu"][0]["price_krw"] == 18000, me)
with psycopg.connect(DSN) as pc:
    row = pc.execute("SELECT id_no_enc, id_photo_enc FROM c_chicken_merchants").fetchone()
check("id encrypted", b"E12345678" not in bytes(row[0]) and bytes(row[0])[:2] == b"v1")
check("photo encrypted", bytes(row[1])[2:16] and b"\xff\xd8\xff" not in bytes(row[1])[:40] and b"JFIF" not in bytes(row[1]))
check("user cannot see id", u.get("/api/admin/chicken/merchants/1/id").status_code == 401)
check("user cannot see photo", u.get("/api/admin/chicken/merchants/1/id-photo").status_code == 401)
adm = TestClient(app)
adm.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H)
d = adm.get("/api/admin/chicken/merchants").json()
check("admin list", d["pending"] == 1 and d["items"][0]["real_name"] == "王小明" and d["pii_ready"], d)
mid = d["items"][0]["id"]
check("admin decrypt id", adm.get(f"/api/admin/chicken/merchants/{mid}/id").json()["id_no"] == "E12345678")
ph = adm.get(f"/api/admin/chicken/merchants/{mid}/id-photo")
check("admin decrypt photo", ph.status_code == 200 and ph.content[:2] == b"\xff\xd8")
r = adm.post(f"/api/admin/chicken/merchants/{mid}", json={"action": "approve"}, headers=H)
check("approve blocked without deposit", r.status_code == 400 and "保证金" in r.text, r.text)
# 保证金：付款截图
r = TestClient(app); r.post("/api/community/register", json={"account": "nobody@example.com", "password": "Passw0rd!xyz", "nickname": "路人", "agree": True}, headers=H)
check("deposit needs application", r.post("/api/pay-proof", data={"kind": "chk_deposit"}, files={"image": ("p.jpg", jpg(), "image/jpeg")}, headers=H).status_code == 400)
r = u.post("/api/pay-proof", data={"kind": "chk_deposit"}, files={"image": ("p.jpg", jpg(), "image/jpeg")}, headers=H)
check("deposit proof uploaded", r.status_code == 200 and r.json()["item"]["expect"] == 500, r.text)
pid = r.json()["item"]["id"]
check("me deposit pending", u.get("/api/chicken/merchant/me").json()["app"]["deposit_pending"] is True)
r = adm.post(f"/api/admin/pay-proofs/{pid}", json={"action": "approve"}, headers=H)
check("deposit approved via pay review", r.status_code == 200 and r.json().get("deposit") == "paid", r.text)
check("deposit paid", u.get("/api/chicken/merchant/me").json()["app"]["deposit_status"] == "paid")
check("open toggle blocked before listing", u.post("/api/chicken/merchant/open", json={"open": False}, headers=H).status_code == 400)
r = adm.post(f"/api/admin/chicken/merchants/{mid}", json={"action": "approve"}, headers=H)
check("approve ok", r.status_code == 200 and r.json()["shop_id"], r.text)
sid = r.json()["shop_id"]
shops = g.get("/api/chicken/shops").json()["shops"]
check("listed and open; examples hidden", [s["id"] for s in shops] == [sid] and shops[0]["is_open"] and shops[0]["merchant"], shops)
check("reapply blocked after approve", u.post("/api/chicken/merchant/apply", data=FORM, files={"id_photo": ("id.jpg", jpg(), "image/jpeg")}, headers=H).status_code in (409, 429))
# 下单
cu = TestClient(app); cu.post("/api/community/register", json={"account": "eater@example.com", "password": "Passw0rd!xyz", "nickname": "吃货", "agree": True}, headers=H)
order = {"shop_id": sid, "items": [{"id": shops[0]["menu"][0]["id"], "qty": 1}], "hotel_name": "测试酒店", "hotel_address": "首尔中区测试路 1"}
r = cu.post("/api/chicken/orders", json=order, headers=H)
check("order open shop", r.status_code == 200 and r.json()["order"]["shop_id"] == sid, r.text)
check("close shop", u.post("/api/chicken/merchant/open", json={"open": False}, headers=H).json()["is_open"] is False)
s2 = g.get("/api/chicken/shops").json()["shops"]
check("listed as closed", s2 and s2[0]["is_open"] is False, s2)
r = cu.post("/api/chicken/orders", json=order, headers=H)
check("order closed shop blocked", r.status_code == 400 and "休息" in r.text, r.text)
check("admin open_only filter", adm.get("/api/admin/chicken/shops?open_only=1").json()["shops"] == [])
u.post("/api/chicken/merchant/open", json={"open": True}, headers=H)
check("admin open_only shows open", [x["id"] for x in adm.get("/api/admin/chicken/shops?open_only=1").json()["shops"]] == [sid])
check("other user cannot toggle", cu.post("/api/chicken/merchant/open", json={"open": False}, headers=H).status_code == 400)
# 保证金已退 → 下架
r = adm.post(f"/api/admin/chicken/merchants/{mid}", json={"action": "deposit_refunded"}, headers=H)
check("refund unlists", r.status_code == 200 and g.get("/api/chicken/shops").json()["shops"] == [])
adm.post(f"/api/admin/chicken/merchants/{mid}", json={"action": "deposit_paid"}, headers=H)
check("resume relists", adm.post(f"/api/admin/chicken/merchants/{mid}", json={"action": "resume"}, headers=H).status_code == 200 and len(g.get("/api/chicken/shops").json()["shops"]) == 1)
# 注销
check("delete needs confirm", u.post("/api/chicken/merchant/delete", json={"confirm": "x"}, headers=H).status_code == 400)
r = u.post("/api/chicken/merchant/delete", json={"confirm": "注销"}, headers=H)
check("delete ok", r.status_code == 200, r.text)
with psycopg.connect(DSN) as pc:
    n = pc.execute("SELECT count(*) FROM c_chicken_merchants").fetchone()[0]
    sh = pc.execute("SELECT active, owner_id FROM c_chicken_shops WHERE id=%s", (sid,)).fetchone()
check("pii deleted, shop unlisted", n == 0 and sh == (False, None), (n, sh))
check("order history kept", len(cu.get("/api/chicken/orders/mine").json()["orders"]) == 1)
print(f"---- {ok} passed, {fail} failed ----")
sys.exit(1 if fail else 0)
