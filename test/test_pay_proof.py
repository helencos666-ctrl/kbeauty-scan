"""付款截图上传 + 后台付款审核。python test/test_pay_proof.py （本机 Postgres 独立测试库）"""
import os, sys, io
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
ok = fail = 0
def check(name, cond, info=""):
    global ok, fail
    if cond: ok += 1; print("PASS", name)
    else: fail += 1; print("FAIL", name, str(info)[:300])

import psycopg
NAME = "lvhan_pay_proof_v1"
a = psycopg.connect("postgresql://postgres@127.0.0.1:54329/postgres", connect_timeout=3); a.autocommit = True
with a.cursor() as cur:
    cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (NAME,))
    cur.execute("DROP DATABASE IF EXISTS " + NAME); cur.execute("CREATE DATABASE " + NAME)
a.close()
os.environ["DATABASE_URL"] = "postgresql://postgres@127.0.0.1:54329/" + NAME
os.environ["COOKIE_INSECURE"] = "1"
os.environ["ADMIN_PASSWORD"] = "test-admin-pw-1234"
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
import community as C
app = FastAPI(); app.include_router(C.router)
H = {"X-LH": "1"}
def jpg(size=(900, 1600)):
    b = io.BytesIO(); Image.new("RGB", size, (30, 160, 90)).save(b, "JPEG", quality=85); return b.getvalue()

guest = TestClient(app)
check("meta", guest.get("/api/pay-proof/meta").status_code == 200)
check("mine needs login", guest.get("/api/pay-proof/mine").status_code == 401)
r = guest.post("/api/pay-proof", data={"kind": "t1"}, files={"image": ("a.jpg", jpg(), "image/jpeg")}, headers=H)
check("guest cannot upload", r.status_code == 401, r.text)

u = TestClient(app)
r = u.post("/api/community/register", json={"account": "payer@example.com", "password": "Passw0rd!xyz", "nickname": "付款人", "agree": True}, headers=H)
check("register", r.status_code == 200, r.text)
r = u.post("/api/pay-proof", data={"kind": "t1"}, files={"image": ("a.jpg", jpg(), "image/jpeg")})
check("csrf required", r.status_code == 403, r.text)
r = u.post("/api/pay-proof", data={"kind": "vip"}, files={"image": ("a.jpg", jpg(), "image/jpeg")}, headers=H)
check("bad kind", r.status_code == 400, r.text)
r = u.post("/api/pay-proof", data={"kind": "t1"}, files={"image": ("a.txt", b"hello not an image" * 10, "text/plain")}, headers=H)
check("non-image rejected", r.status_code == 400, r.text)
r = u.post("/api/pay-proof", data={"kind": "t1"}, files={"image": ("a.jpg", b"x" * (C.MAX_IMG_BYTES + 10), "image/jpeg")}, headers=H)
check("too big rejected", r.status_code == 413, r.text)
r = u.post("/api/pay-proof", data={"kind": "extra"}, files={"image": ("a.jpg", jpg(), "image/jpeg")}, headers=H)
check("extra needs merchant", r.status_code == 400, r.text)
r = u.post("/api/pay-proof", data={"kind": "t1", "note": "新人券"}, files={"image": ("a.jpg", jpg((3000, 5000)), "image/jpeg")}, headers=H)
check("upload ok", r.status_code == 200 and r.json()["item"]["status"] == "pending" and r.json()["member_no"], r.text)
pid1 = r.json()["item"]["id"]
mine = u.get("/api/pay-proof/mine").json()["items"]
check("mine shows pending", mine and mine[0]["status"] == "pending" and mine[0]["kind_label"] == "会员", mine)
with psycopg.connect(os.environ["DATABASE_URL"]) as pc:
    sz = pc.execute("SELECT length(img), length(thumb) FROM c_pay_proofs WHERE id=%s", (pid1,)).fetchone()
check("stored compressed", sz[0] < 400_000 and sz[1] < 60_000, sz)
check("user cannot read admin img", u.get(f"/api/admin/pay-proofs/{pid1}/img").status_code == 401)
check("user cannot list admin", u.get("/api/admin/pay-proofs").status_code == 401)
check("user cannot approve", u.post(f"/api/admin/pay-proofs/{pid1}", json={"action": "approve"}, headers=H).status_code == 401)

adm = TestClient(app)
check("admin login", adm.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H).status_code == 200)
d = adm.get("/api/admin/pay-proofs").json()
check("admin list", d["pending"] == 1 and d["items"][0]["id"] == pid1 and d["items"][0]["note"] == "新人券" and d["items"][0]["expect"] == 9.9, d)
img = adm.get(f"/api/admin/pay-proofs/{pid1}/img")
check("admin img jpeg", img.status_code == 200 and img.headers["content-type"] == "image/jpeg" and img.content[:2] == b"\xff\xd8")
r = adm.post(f"/api/admin/pay-proofs/{pid1}", json={"action": "approve", "amount": "9.9"}, headers=H)
check("approve t1", r.status_code == 200 and r.json()["status"] == "approved", r.text)
check("tier now 1", u.get("/api/community/me").json()["user"]["tier"] == 1)
check("double approve blocked", adm.post(f"/api/admin/pay-proofs/{pid1}", json={"action": "approve"}, headers=H).status_code == 409)
check("mine approved", u.get("/api/pay-proof/mine").json()["items"][0]["status"] == "approved")
# 高级会员补差价 → 驳回
r = u.post("/api/pay-proof", data={"kind": "t2"}, files={"image": ("a.jpg", jpg(), "image/jpeg")}, headers=H)
check("upload t2", r.status_code == 200 and abs(r.json()["item"]["expect"] - 10.0) < 0.01, r.text); pid2 = r.json()["item"]["id"]
r = adm.post(f"/api/admin/pay-proofs/{pid2}", json={"action": "reject", "note": "金额不对"}, headers=H)
check("reject", r.status_code == 200)
m = u.get("/api/pay-proof/mine").json()["items"][0]
check("mine rejected with note", m["status"] == "rejected" and m["admin_note"] == "金额不对", m)
check("tier still 1", u.get("/api/community/me").json()["user"]["tier"] == 1)
# 第 3 次提交触发每小时 3 次限制
r = u.post("/api/pay-proof", data={"kind": "m_month"}, files={"image": ("a.jpg", jpg(), "image/jpeg")}, headers=H)
check("upload m_month", r.status_code == 200, r.text); pid3 = r.json()["item"]["id"]
r = u.post("/api/pay-proof", data={"kind": "m_year"}, files={"image": ("a.jpg", jpg(), "image/jpeg")}, headers=H)
check("rate limited", r.status_code == 429, r.text)
r = adm.post(f"/api/admin/pay-proofs/{pid3}", json={"action": "approve"}, headers=H)
check("approve merchant month (default amount)", r.status_code == 200, r.text)
me = u.get("/api/community/me").json()["user"]
check("merchant active", me["merchant"] and me["merchant_info"]["plan"] == "month", me)
# 增发帖：新用户（避开上面用户的频率限制），先开通商家
v = TestClient(app)
v.post("/api/community/register", json={"account": "biz@example.com", "password": "Passw0rd!xyz", "nickname": "商家二", "agree": True}, headers=H)
vid = adm.get("/api/admin/users", params={"s": "biz@example.com"}).json()["users"][0]["id"]
adm.post(f"/api/admin/users/{vid}", json={"action": "merchant", "plan": "month"}, headers=H)
r = v.post("/api/pay-proof", data={"kind": "extra", "extra_n": "3"}, files={"image": ("a.png", jpg(), "image/png")}, headers=H)
check("upload extra x3", r.status_code == 200 and abs(r.json()["item"]["expect"] - 59.7) < 0.01, r.text); pid4 = r.json()["item"]["id"]
r = v.post("/api/pay-proof", data={"kind": "extra", "extra_n": "99"}, files={"image": ("a.jpg", jpg(), "image/jpeg")}, headers=H)
check("extra_n bound", r.status_code == 400, r.text)
r = adm.post(f"/api/admin/pay-proofs/{pid4}", json={"action": "approve"}, headers=H)
check("approve extra", r.status_code == 200 and r.json()["credits"] == 3, r.text)
check("credits 3", v.get("/api/community/me").json()["user"]["merchant_info"]["credits"] == 3)
check("admin filter approved", len(adm.get("/api/admin/pay-proofs?status=approved").json()["items"]) == 3)
check("bad status filter", adm.get("/api/admin/pay-proofs?status=x").status_code == 400)
# 注销账号时截图一起删除
r = v.post("/api/community/account/delete", json={"password": "Passw0rd!xyz", "confirm": "注销"}, headers=H)
with psycopg.connect(os.environ["DATABASE_URL"]) as pc:
    n = pc.execute("SELECT count(*) FROM c_pay_proofs WHERE user_id=%s", (vid,)).fetchone()[0]
check("account delete removes proofs", r.status_code == 200 and n == 0, (r.text, n))
print(f"---- {ok} passed, {fail} failed ----")
sys.exit(1 if fail else 0)
