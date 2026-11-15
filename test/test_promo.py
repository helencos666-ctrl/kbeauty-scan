"""推广员实付佣金与申请：python test/test_promo.py
使用本机 Postgres 独立测试库，不打印连接串或密钥。"""
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
        print("FAIL", name, str(info)[:400])


def main():
    dsn = os.environ.get("LVHAN_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/lvhan_promo_v1")
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_promo_v1"
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

    def register(cli, nick, account, promoter=None):
        body = {"account": f"{account}{rnd}@example.com", "password": "Passw0rd!xyz", "nickname": nick, "agree": True}
        if promoter:
            body["promoter"] = promoter
        return cli.post("/api/community/register", json=body, headers=H)

    try:
        guest = TestClient(app)
        adm = TestClient(app)
        ann = TestClient(app)
        bob = TestClient(app)
        cara = TestClient(app)
        dan = TestClient(app)

        r = guest.get("/promo/rules")
        check("rules 200", r.status_code == 200, r.status_code)
        for w in ("单层", "50%", "不是多层", "禁止", "虚假", "医疗", "实付"):
            check("rules has %s" % w, w in r.text, w)
        r = guest.get("/promo")
        check("dashboard page 200", r.status_code == 200 and "推广" in r.text, r.status_code)
        check("page has 抖音号 field", "抖音号" in r.text, "missing label")
        check("page has 推广方式", "推广方式" in r.text, "missing method")
        check("apply needs auth", guest.post("/api/promo/apply", json={"agree": True}, headers=H).status_code == 401)
        check("apply needs csrf", ann.post("/api/promo/apply", json={"agree": True}).status_code in (401, 403))

        for cli, nick, acc in ((ann,"安娜","ann"),(bob,"鲍勃","bob"),(cara,"卡拉","cara"),(dan,"丹丹","dan")):
            r = register(cli, nick, acc)
            check("register "+acc, r.status_code == 200, r.text)
        uid_a = ann.get("/api/community/me").json()["user"]["id"]
        uid_b = bob.get("/api/community/me").json()["user"]["id"]
        uid_c = cara.get("/api/community/me").json()["user"]["id"]
        uid_d = dan.get("/api/community/me").json()["user"]["id"]

        r = adm.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H)
        check("admin login", r.status_code == 200, r.text)

        r = ann.post("/api/promo/apply", json={"agree": False, "message": "想推广"}, headers=H)
        check("apply needs agree", r.status_code == 400, r.text)
        # douyin required (server-side)
        r = ann.post("/api/promo/apply", json={"agree": True, "message": "我会在旅韩群里分享，不群发骚扰"}, headers=H)
        check("apply without douyin_id 400", r.status_code in (400, 422) and "抖音号" in r.text, r.text)
        r = ann.post("/api/promo/apply", json={"agree": True, "douyin_id": "  ", "message": "x"}, headers=H)
        check("apply blank douyin 400", r.status_code in (400, 422), r.text)
        r = ann.post("/api/promo/apply", json={"agree": True, "douyin_id": "a"}, headers=H)
        check("apply short douyin 400", r.status_code in (400, 422), r.text)
        r = ann.post("/api/promo/apply", json={"agree": True, "douyin_id": "a" * 41}, headers=H)
        check("apply long douyin 400", r.status_code in (400, 422), r.text)
        r = ann.post("/api/promo/apply", json={"agree": True, "douyin_id": "bad name!"}, headers=H)
        check("apply invalid douyin chars 400", r.status_code in (400, 422), r.text)
        r = ann.post("/api/promo/apply", json={"agree": True, "douyin_id": "ann_ok", "promo_method": "传单"}, headers=H)
        check("apply bad promo_method 400", r.status_code in (400, 422), r.text)
        r = ann.post("/api/promo/apply", json={"agree": True, "douyin_id": " 旅韩小美.ok_1- ", "promo_method": "短视频"}, headers=H)
        check("ann applies with douyin, message optional", r.status_code == 200, r.text)
        r = ann.post("/api/promo/apply", json={"agree": True, "douyin_id": "ann_ok"}, headers=H)
        check("duplicate apply rejected", r.status_code == 400, r.text)
        apps = adm.get("/api/admin/promo/applications?status=pending").json()["items"]
        check("admin sees application", len(apps) == 1 and apps[0]["user_id"] == uid_a, apps)
        check("admin sees douyin_id", apps[0].get("douyin_id") == "旅韩小美.ok_1-", apps)
        check("admin sees promo_method", apps[0].get("promo_method") == "短视频", apps)
        aid = apps[0]["id"]
        r = adm.post(f"/api/admin/promo/applications/{aid}", json={"action": "approve"}, headers=H)
        check("approve ann", r.status_code == 200 and r.json().get("code"), r.text)
        code_a = r.json()["code"]
        plist = [p for p in adm.get("/api/admin/promoters").json()["promoters"] if p["code"] == code_a]
        check("promoter list has douyin", plist and plist[0].get("douyin_id") == "旅韩小美.ok_1-", plist)
        check("promoter list has method", plist and plist[0].get("promo_method") == "短视频", plist)
        csv_app = adm.get("/api/admin/promo/applications.csv")
        check("applications csv 200", csv_app.status_code == 200 and "text/csv" in csv_app.headers.get("content-type", ""), csv_app.headers)
        check("applications csv has douyin header and value", "douyin_id" in csv_app.text and "旅韩小美.ok_1-" in csv_app.text, csv_app.text[:400])
        check("applications csv not public", guest.get("/api/admin/promo/applications.csv").status_code in (401, 403))
        me = ann.get("/api/promo/me").json()
        check("ann dashboard has code", me["is_promoter"] and me["dashboard"]["code"] == code_a, me)
        check("dashboard starts empty", me["dashboard"]["regs"] == 0 and me["dashboard"]["pending"] == 0, me)

        # first-touch: bob registers with ann's code
        # already registered bob without code; use a new client
        ed = TestClient(app)
        r = ed.post("/api/community/register", json={
            "account": f"ed{rnd}@example.com", "password": "Passw0rd!xyz", "nickname": "爱德华",
            "agree": True, "promoter": code_a
        }, headers=H)
        check("ed referred by ann", r.status_code == 200, r.text)
        uid_e = ed.get("/api/community/me").json()["user"]["id"]
        me = ann.get("/api/promo/me").json()
        check("regs=1 visits optional", me["dashboard"]["regs"] == 1, me)
        check("referral name masked", me["dashboard"]["referrals"] and me["dashboard"]["referrals"][0]["name"].endswith("*") and len(me["dashboard"]["referrals"][0]["name"]) == 2, me)

        # 50% of 9.9 = 4.95, hold 7 days => pending
        r = adm.post("/api/admin/promo/settings", json={"hold_days": 7}, headers=H)
        check("set hold 7", r.status_code == 200 and r.json()["hold_days"] == 7, r.text)
        r = adm.post(f"/api/admin/users/{uid_e}", json={"action": "confirm_payment", "kind": "t1", "amount": 9.9}, headers=H)
        check("confirm 9.9 -> 4.95 pending", r.status_code == 200, r.text)
        comm = r.json().get("commission") or {}
        check("commission 4.95 pending", comm.get("amount") == 4.95 and comm.get("status") == "pending", comm)
        cid = comm["id"]
        me = ann.get("/api/promo/me").json()
        check("dashboard pending 4.95", me["dashboard"]["pending"] == 4.95 and me["dashboard"]["payable"] == 0, me)
        r = adm.post(f"/api/admin/commissions/{cid}", json={"action": "pay", "note": "太早了"}, headers=H)
        check("cannot pay during hold", r.status_code == 400, r.text)

        # hold 0 then another payment is immediately payable
        r = adm.post("/api/admin/promo/settings", json={"hold_days": 0}, headers=H)
        check("hold 0", r.status_code == 200, r.text)
        r = adm.post(f"/api/admin/users/{uid_e}", json={"action": "confirm_payment", "kind": "t2", "amount": 10}, headers=H)
        comm2 = r.json().get("commission") or {}
        check("second payment immediate payable", comm2.get("amount") == 5.0 and comm2.get("status") == "payable", comm2)
        cid2 = comm2["id"]
        r = adm.post(f"/api/admin/commissions/{cid2}", json={"action": "pay", "note": "已转账"}, headers=H)
        check("mark paid", r.status_code == 200 and r.json()["status"] == "paid", r.text)
        me = ann.get("/api/promo/me").json()
        check("paid 5, still pending 4.95", me["dashboard"]["paid"] == 5.0 and me["dashboard"]["pending"] == 4.95, me)

        # refund voids pending
        r = adm.post(f"/api/admin/commissions/{cid}", json={"action": "void", "reason": "用户退款"}, headers=H)
        check("void pending", r.status_code == 200, r.text)
        me = ann.get("/api/promo/me").json()
        check("voided not in pending", me["dashboard"]["pending"] == 0 and me["dashboard"]["paid"] == 5.0, me)
        r = adm.post(f"/api/admin/commissions/{cid2}", json={"action": "void", "reason": "再作废"}, headers=H)
        check("cannot void paid", r.status_code == 400, r.text)

        # single-level: bob applies, approved; cara referred by bob; payment goes to bob not ann
        r = bob.post("/api/promo/apply", json={"agree": True, "douyin_id": "bob_live", "promo_method": "户外直播", "message": "申请推广"}, headers=H)
        check("bob applies", r.status_code == 200, r.text)
        bid = adm.get("/api/admin/promo/applications?status=pending").json()["items"][0]["id"]
        code_b = adm.post(f"/api/admin/promo/applications/{bid}", json={"action": "approve"}, headers=H).json()["code"]
        # 已注册的卡拉改挂鲍勃的推广码，避免同一 IP 注册频次限制
        import community as C2
        with C2.db() as conn:
            C2.q(conn, "UPDATE c_users SET promoter_code=%s WHERE id=%s", (code_b, uid_c))
        r = adm.post(f"/api/admin/users/{uid_c}", json={"action": "confirm_payment", "kind": "t1", "amount": 9.9}, headers=H)
        check("cara payment under bob", r.status_code == 200 and r.json().get("commission"), r.text)
        uid_f = uid_c
        # bob gets 4.95, ann does not get this commission
        bob_me = bob.get("/api/promo/me").json()
        ann_me = ann.get("/api/promo/me").json()
        check("single-level: bob earned 4.95", bob_me["dashboard"]["payable"] == 4.95 or bob_me["dashboard"]["pending"] == 4.95, bob_me)
        check("single-level: ann does not get second-level", ann_me["dashboard"]["payable"] == 0 and ann_me["dashboard"]["pending"] == 0, ann_me)

        # self-referral: force ann's promoter_code to her own code, confirm should not pay herself
        import community as C2
        with C2.db() as conn:
            C2.q(conn, "UPDATE c_users SET promoter_code=%s WHERE id=%s", (code_a, uid_a))
        r = adm.post(f"/api/admin/users/{uid_a}", json={"action": "confirm_payment", "kind": "t1", "amount": 9.9, "apply_benefit": False}, headers=H)
        check("self-referral yields no commission", r.status_code == 200 and r.json().get("commission") is None, r.text)

        # no referrer: dan payment, no commission
        r = adm.post(f"/api/admin/users/{uid_d}", json={"action": "confirm_payment", "kind": "t1", "amount": 9.9}, headers=H)
        check("no referrer no commission", r.status_code == 200 and r.json().get("commission") is None, r.text)

        # money stored as integer fen
        with C2.db() as conn:
            row = C2.q(conn, "SELECT cents, paid_amount_cents FROM c_commissions WHERE user_id=%s AND kind='t1' AND status<>'void' ORDER BY id DESC LIMIT 1", (uid_f,), one=True)
        check("stored as fen 495/990", row and row["cents"] == 495 and row["paid_amount_cents"] == 990, row)

        csv = adm.get("/api/admin/commissions.csv")
        check("csv 200", csv.status_code == 200 and "text/csv" in csv.headers.get("content-type", ""), csv.headers)
        check("csv has header and amount", "commission_yuan" in csv.text and "4.95" in csv.text, csv.text[:200])
        check("csv not public", guest.get("/api/admin/commissions.csv").status_code in (401, 403))
        check("confirm needs csrf", adm.post(f"/api/admin/users/{uid_d}", json={"action": "confirm_payment", "kind": "t1", "amount": 9.9}).status_code == 403)
        check("settings needs admin", ann.post("/api/admin/promo/settings", json={"hold_days": 1}, headers=H).status_code in (401, 403))
        check("rules mention no pyramid", "单层" in guest.get("/promo/rules").text and "多层" in guest.get("/promo/rules").text)
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
