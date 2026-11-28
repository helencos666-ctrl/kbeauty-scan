"""推广员自助查看与绑定：python test/test_promo_selfview.py

用户 A 不能看到推广员 B 的数据。二维码解码为目标链接。
使用本机 Postgres 独立测试库，不打印连接串或密钥。
"""
import io
import os
import re
import sys
import random
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


def decode_qr(data: bytes) -> str:
    texts = []
    try:
        import zxingcpp
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        for bc in zxingcpp.read_barcodes(im):
            if getattr(bc, "text", None):
                texts.append(bc.text)
    except Exception as e:
        print("zxing decode skip", type(e).__name__)
    if texts:
        return texts[0]
    try:
        from pyzbar.pyzbar import decode as zbar_decode
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        for rec in zbar_decode(im):
            if rec.data:
                texts.append(rec.data.decode("utf-8"))
    except Exception as e:
        print("pyzbar decode skip", type(e).__name__)
    return texts[0] if texts else ""


def check_source():
    admin = (Path(ROOT) / "static" / "admin.html").read_text(encoding="utf-8")
    me = (Path(ROOT) / "static" / "me.html").read_text(encoding="utf-8")
    promo = (Path(ROOT) / "static" / "promo.html").read_text(encoding="utf-8")
    check("admin form has 名称", 'id="pname"' in admin and "名称" in admin)
    check("admin form has 推广码", 'id="pcode"' in admin)
    check("admin form has 添加", 'id="padd"' in admin)
    check("admin form no legacy tier inputs", all(x not in admin for x in ('id="pcom"', 'id="pcom2"', 'id="pcom3"', 'id="pcom4"')))
    for line in (
        "朋友通过你的链接或二维码注册，30天内付费，你拿实付金额的50%。",
        "7天后结算，退款不算。",
        "只算你直接推荐的人。",
    ):
        check("admin policy: " + line[:10], line in admin)
        check("me fallback policy: " + line[:10], line in me)
    check("admin table headers horizontal", "付费笔数" in admin and "待结算" in admin and "推广员" in admin)
    check("admin QR button", "二维码" in admin and "qrModal" in admin and "保存图片" in admin and "复制链接" in admin)
    check("admin bind user", "绑定用户" in admin and "action: 'bind'" in admin)
    check("admin no 改佣金 button", "改佣金" not in admin)
    check("me has 我的推广 card", 'id="myPromo"' in me and "我的推广" in me)
    check("promo page has QR and 保存图片", "/api/promo/qr.png" in promo and "保存图片" in promo)
    check("stats use labeled metrics not tight table", "pmetrics" in admin and "付费笔数" in admin and "pcard" in admin)


def main():
    check_source()
    # QR image itself (no db)
    os.environ.setdefault("PUBLIC_BASE_URL", "https://lvhanzhushou.com")
    import community  # noqa: F401  — 先加载 community，避免 promo 循环导入
    from promo import render_promo_qr_png, promo_public_url, POLICY_LINES
    png = render_promo_qr_png("A08", "韩国大洋洋")
    check("qr png not empty", isinstance(png, (bytes, bytearray)) and len(png) > 800 and png[:8] == b"\x89PNG\r\n\x1a\n", len(png) if png else 0)
    text = decode_qr(png)
    want = "https://lvhanzhushou.com/?ref=A08"
    check("qr decodes to public url", text == want, text)
    check("promo_public_url", promo_public_url("A08") == want, promo_public_url("A08"))
    check("policy is 3 lines", len(POLICY_LINES) == 3)

    dsn = os.environ.get("LVHAN_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/lvhan_promo_selfview_v1")
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_promo_selfview_v1"
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
        print("SKIP integration: no local postgres (%s)" % type(e).__name__)
        print("RESULT", "ok", ok, "fail", fail)
        return 0 if fail == 0 else 1

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import community as C

    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "COOKIE_INSECURE", "ADMIN_PASSWORD", "PUBLIC_BASE_URL")}
    os.environ["DATABASE_URL"] = dsn
    os.environ["COOKIE_INSECURE"] = "1"
    os.environ["ADMIN_PASSWORD"] = "test-admin-pw-1234"
    os.environ["PUBLIC_BASE_URL"] = "https://lvhanzhushou.com"
    C._schema_ready = False
    C._psycopg = None
    app = FastAPI()
    app.include_router(C.router)
    H = {"X-LH": "1"}
    rnd = random.randint(100000, 999999)

    def register(cli, nick, account, phone=None):
        if phone:
            body = {"account": phone, "password": "Passw0rd!xyz", "nickname": nick, "agree": True}
        else:
            body = {"account": f"{account}{rnd}@example.com", "password": "Passw0rd!xyz", "nickname": nick, "agree": True}
        return cli.post("/api/community/register", json=body, headers=H)

    try:
        guest = TestClient(app)
        adm = TestClient(app)
        alice = TestClient(app)
        bob = TestClient(app)
        cara = TestClient(app)

        check("guest me 401", guest.get("/api/promo/me").status_code == 401)
        check("guest qr 401", guest.get("/api/promo/qr.png").status_code == 401)
        check("guest admin qr 401/403", guest.get("/api/admin/promoters/A08/qr.png").status_code in (401, 403))

        r = register(alice, "爱丽丝", "alice")
        check("register alice", r.status_code == 200, r.text)
        phone_b = "1390000%04d" % (rnd % 10000)
        r = register(bob, "鲍勃", "bob", phone=phone_b)
        check("register bob by phone", r.status_code == 200, r.text)
        r = register(cara, "卡拉", "cara")
        check("register cara", r.status_code == 200, r.text)

        ua = alice.get("/api/community/me").json()["user"]
        ub = bob.get("/api/community/me").json()["user"]
        uid_a, no_a = ua["id"], ua.get("member_no") or ua["id"]
        uid_b, no_b = ub["id"], ub.get("member_no") or ub["id"]

        check("alice not promoter yet", alice.get("/api/promo/me").json().get("is_promoter") is False)
        check("alice no dashboard", alice.get("/api/promo/me").json().get("dashboard") is None)
        check("alice qr 404", alice.get("/api/promo/qr.png").status_code == 404)

        r = adm.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H)
        check("admin login", r.status_code == 200, r.text)

        r = adm.post("/api/admin/promoters", json={"name": "韩国大洋洋", "code": "A08"}, headers=H)
        check("create A08 name+code only", r.status_code == 200, r.text)
        r = adm.post("/api/admin/promoters", json={"name": "小李", "code": "B09"}, headers=H)
        check("create B09", r.status_code == 200, r.text)

        # bind alice by member_no
        r = adm.post("/api/admin/promoters/A08", json={"action": "bind", "user": str(no_a)}, headers=H)
        check("bind A08 by member_no", r.status_code == 200 and r.json().get("user_id") == uid_a, r.text)
        # bind bob by phone
        r = adm.post("/api/admin/promoters/B09", json={"action": "bind", "user": phone_b}, headers=H)
        check("bind B09 by phone", r.status_code == 200 and r.json().get("user_id") == uid_b, r.text)

        # conflict: bind alice to B09
        r = adm.post("/api/admin/promoters/B09", json={"action": "bind", "user": str(no_a)}, headers=H)
        check("cannot bind alice to second code", r.status_code == 409, r.text)

        # seed stats: visits/regs/commission only for A08, different numbers for B09
        with C.db() as conn:
            C.q(conn, "INSERT INTO c_ref_visits(code,visitor,day,path) VALUES ('A08','va1',CURRENT_DATE,'/'),('A08','va2',CURRENT_DATE,'/'),('B09','vb1',CURRENT_DATE,'/')")
            C.q(conn, "UPDATE c_users SET promoter_code='A08' WHERE id=%s", (cara.get("/api/community/me").json()["user"]["id"],))
        uid_c = cara.get("/api/community/me").json()["user"]["id"]
        r = adm.post("/api/admin/promo/settings", json={"hold_days": 0}, headers=H)
        check("hold 0", r.status_code == 200, r.text)
        r = adm.post(f"/api/admin/users/{uid_c}", json={"action": "confirm_payment", "kind": "t1", "amount": 9.9}, headers=H)
        check("cara payment under A08", r.status_code == 200 and (r.json().get("commission") or {}).get("amount") == 4.95, r.text)

        me_a = alice.get("/api/promo/me").json()
        me_b = bob.get("/api/promo/me").json()
        da = me_a.get("dashboard") or {}
        dbd = me_b.get("dashboard") or {}
        check("alice is promoter A08", me_a.get("is_promoter") and da.get("code") == "A08", me_a)
        check("bob is promoter B09", me_b.get("is_promoter") and dbd.get("code") == "B09", me_b)
        check("alice sees 2 visits", da.get("visits") == 2, da)
        check("bob sees 1 visit", dbd.get("visits") == 1, dbd)
        check("alice sees 1 reg", da["regs"] == 1, da)
        check("bob sees 0 regs", dbd["regs"] == 0, dbd)
        check("alice paid_count 1", da["paid_count"] == 1, da)
        check("bob paid_count 0", dbd["paid_count"] == 0, dbd)
        check("alice unsettled 4.95", da["unsettled"] == 4.95, da)
        check("bob unsettled 0", dbd["unsettled"] == 0, dbd)
        check("alice cannot see bob visits", da["visits"] != dbd["visits"])
        check("query param code ignored for alice", alice.get("/api/promo/me?code=B09").json()["dashboard"]["code"] == "A08")
        # referrals only masked names, no full nick of cara? mask is first char + *
        refs = da.get("referrals") or []
        check("alice referrals masked", refs and refs[0]["name"].endswith("*") and "卡拉" not in refs[0]["name"], refs)
        check("bob has no cara referral", not (dbd.get("referrals") or []), dbd)
        check("policy_lines in me", me_a.get("policy_lines") == list(POLICY_LINES), me_a.get("policy_lines"))

        # QR isolation
        qa = alice.get("/api/promo/qr.png")
        qb = bob.get("/api/promo/qr.png")
        check("alice qr 200", qa.status_code == 200 and qa.headers.get("content-type", "").startswith("image/png"), qa.status_code)
        check("bob qr 200", qb.status_code == 200, qb.status_code)
        da, db_txt = decode_qr(qa.content), decode_qr(qb.content)
        check("alice qr is A08", da == "https://lvhanzhushou.com/?ref=A08", da)
        check("bob qr is B09", db_txt == "https://lvhanzhushou.com/?ref=B09", db_txt)
        check("alice qr not B09", da != "https://lvhanzhushou.com/?ref=B09")
        # cannot pass someone else's code
        check("alice qr ignores ?code=", decode_qr(alice.get("/api/promo/qr.png?code=B09").content) == "https://lvhanzhushou.com/?ref=A08")
        check("alice cannot fetch admin qr for B09", alice.get("/api/admin/promoters/B09/qr.png").status_code in (401, 403))

        admin_qr = adm.get("/api/admin/promoters/A08/qr.png")
        check("admin qr 200", admin_qr.status_code == 200, admin_qr.status_code)
        check("admin qr decodes A08", decode_qr(admin_qr.content) == "https://lvhanzhushou.com/?ref=A08", decode_qr(admin_qr.content))

        # pyzbar explicitly if available
        try:
            from pyzbar.pyzbar import decode as zbar_decode
            from PIL import Image
            recs = zbar_decode(Image.open(io.BytesIO(qa.content)).convert("RGB"))
            got = recs[0].data.decode("utf-8") if recs else ""
            check("pyzbar decodes alice qr", got == "https://lvhanzhushou.com/?ref=A08", got)
        except Exception as e:
            print("pyzbar extra check skipped", type(e).__name__)

        # list shows bind info, not other phones
        lst = {p["code"]: p for p in adm.get("/api/admin/promoters").json()["promoters"]}
        check("list A08 bound to alice", lst["A08"]["bound"] and lst["A08"]["user_id"] == uid_a, lst["A08"])
        check("list has no raw phone field", "phone" not in lst["A08"] or not lst["A08"].get("phone"))
        check("paid column is 付费笔数 for A08", lst["A08"]["paid"] == 1, lst["A08"])

        # unbind alice, she loses self-view
        r = adm.post("/api/admin/promoters/A08", json={"action": "unbind"}, headers=H)
        check("unbind A08", r.status_code == 200, r.text)
        check("alice no longer promoter", alice.get("/api/promo/me").json().get("is_promoter") is False)
        check("bob still promoter after alice unbind", bob.get("/api/promo/me").json()["dashboard"]["code"] == "B09")

        # me page served
        r = alice.get("/me")
        check("me page 200 has 我的推广 container", r.status_code == 200 and 'id="myPromo"' in r.text, r.status_code)
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
