"""限时高级会员推广：python test/test_premium_promo.py
使用本机 Postgres 独立测试库，不打印连接串或密钥。
"""
import os
import sys
import random
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
    dsn = os.environ.get(
        "LVHAN_TEST_DATABASE_URL",
        "postgresql://postgres@127.0.0.1:54329/lvhan_premium_promo_v1",
    )
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_premium_promo_v1"
    if not dbname.isidentifier() or not dbname.isascii():
        raise RuntimeError("bad test db name")
    try:
        import psycopg
        admin_db = psycopg.connect("postgresql://postgres@127.0.0.1:54329/postgres", connect_timeout=3)
        admin_db.autocommit = True
        with admin_db.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=%s AND pid<>pg_backend_pid()",
                (dbname,),
            )
            cur.execute("DROP DATABASE IF EXISTS " + dbname)
            cur.execute("CREATE DATABASE " + dbname)
        admin_db.close()
    except Exception as e:
        print("SKIP: no local postgres (%s)" % str(e)[:80])
        return 0

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import community as C
    import premium_promo as P

    keys = ("DATABASE_URL", "COOKIE_INSECURE", "ADMIN_PASSWORD",
            "PROMO_AUTO_PREMIUM", "PROMO_PREMIUM_DAYS", "PHONE_OTP_REQUIRED")
    saved = {k: os.environ.get(k) for k in keys}
    os.environ["DATABASE_URL"] = dsn
    os.environ["COOKIE_INSECURE"] = "1"
    os.environ["ADMIN_PASSWORD"] = "test-admin-pw-1234"
    os.environ["PROMO_AUTO_PREMIUM"] = "1"
    os.environ["PROMO_PREMIUM_DAYS"] = "30"
    os.environ.pop("PHONE_OTP_REQUIRED", None)
    C._schema_ready = False
    C._psycopg = None
    app = FastAPI()
    app.include_router(C.router)
    H = {"X-LH": "1"}
    rnd = random.randint(100000, 999999)

    try:
        # -------- static --------
        login = (Path(ROOT) / "static" / "login.html").read_text(encoding="utf-8")
        me = (Path(ROOT) / "static" / "me.html").read_text(encoding="utf-8")
        member = (Path(ROOT) / "static" / "member.html").read_text(encoding="utf-8")
        sw = (Path(ROOT) / "static" / "sw.js").read_text(encoding="utf-8")
        check("登录页请求推广说明", "/api/premium-promo" in login and "promoNotice" in login)
        check("我的页展示徽章和说明", "promo_notice" in me and "LH.badge" in me and "promo-note" in me)
        check("会员页也展示到期说明", "promo_notice" in member)
        check("缓存版本 v102", "lvhan-shell-v102" in sw and "?v=102" in login and "?v=101" not in login)
        check("默认开启", P.promo_auto_premium() is True)
        check("默认 30 天", P.promo_premium_days() == 30)
        check("文案模板", "新人限时福利：近期注册会员限时享高级会员" in P.NOTICE_TMPL and "之后按正常会员制度" in P.NOTICE_TMPL)
        src = Path(ROOT).joinpath("community.py").read_text(encoding="utf-8")
        mepy = Path(ROOT).joinpath("me.py").read_text(encoding="utf-8")
        check("未改邀请绑定/限流", "limit(\"reg:\" + ip_of(req), 5, 3600" in src and "limit(\"reg-day:\" + ip_of(req), 10, 86400" in src)
        check("未改手机验证开关默认", "PHONE_OTP_REQUIRED" not in Path(ROOT).joinpath("premium_promo.py").read_text(encoding="utf-8"))

        guest = TestClient(app)
        pub = guest.get("/api/premium-promo").json()
        check("公开接口开启", pub["enabled"] is True and pub["days"] == 30 and pub["until_date"], pub)
        check("公开文案含到期日", pub["notice"] and pub["until_date"] in pub["notice"] and "之后按正常会员制度" in pub["notice"], pub)
        login_html = guest.get("/login").text
        check("登录页可打开", "promoNotice" in login_html and guest.get("/login").status_code == 200)

        # -------- register with flag on --------
        u1 = TestClient(app)
        r = u1.post("/api/community/register", json={
            "account": f"p1{rnd}@example.com", "password": "Passw0rd!xyz", "nickname": "新用户甲", "agree": True
        }, headers=H)
        check("开启时注册成功", r.status_code == 200, r.text)
        me1 = u1.get("/api/me/summary").json()
        u = me1["user"]
        check("注册即为高级会员", u and u["tier"] == 2 and u["display_tier"] == 2 and u["tier_name"] == "高级会员", me1)
        check("我的页有徽章字段", u["promo_premium"] is True and u["promo_premium_until"] and u["promo_notice"], u)
        check("说明含到期日", u["promo_premium_until"] in u["promo_notice"] and "新人限时福利" in u["promo_notice"], u["promo_notice"])
        # expiry is about 30 days from now in KST
        d = datetime.strptime(u["promo_premium_until"], "%Y-%m-%d").date()
        today = datetime.now(timezone(timedelta(hours=9))).date()
        check("到期约 30 天后", 29 <= (d - today).days <= 31, (d, today))
        comm = u1.get("/api/community/me").json()["user"]
        check("社区 me 也是高级会员", comm["tier"] == 2 and comm["promo_premium"] is True, comm)

        with C.db() as conn:
            row = C.q(conn, "SELECT tier, promo_revert_tier, promo_premium_until FROM c_users WHERE nickname=%s",
                      ("新用户甲",), one=True)
            aud = C.q(conn, """SELECT action,detail,from_tier,to_tier FROM c_premium_promo_audit
                               WHERE user_id=(SELECT id FROM c_users WHERE nickname=%s) AND action='grant'""",
                      ("新用户甲",), one=True)
        check("回退档为普通用户 0", row and row["tier"] == 2 and row["promo_revert_tier"] == 0, row)
        check("审计标记推广期自动升级", aud and aud["detail"] == "推广期自动升级" and aud["from_tier"] == 0 and aud["to_tier"] == 2, aud)

        # -------- flag off: new register stays ordinary --------
        os.environ["PROMO_AUTO_PREMIUM"] = "0"
        check("关闭后不再自动升级新用户（开关）", P.promo_auto_premium() is False)
        pub_off = guest.get("/api/premium-promo").json()
        check("关闭后公开接口不承诺", pub_off["enabled"] is False and not pub_off["notice"], pub_off)
        u2 = TestClient(app)
        r = u2.post("/api/community/register", json={
            "account": f"p2{rnd}@example.com", "password": "Passw0rd!xyz", "nickname": "关闭后注册", "agree": True
        }, headers=H)
        check("关闭后仍可注册", r.status_code == 200, r.text)
        me2 = u2.get("/api/me/summary").json()["user"]
        check("关闭后注册仍是普通用户", me2["tier"] == 0 and not me2["promo_premium"], me2)
        # already granted user keeps remaining time
        still = u1.get("/api/me/summary").json()["user"]
        check("关闭开关不收回已升级账号", still["promo_premium"] is True and still["tier"] == 2, still)

        # -------- backfill --------
        os.environ["PROMO_AUTO_PREMIUM"] = "1"
        with C.db() as conn:
            # ordinary recent (tier 0)
            a = C.q(conn, """INSERT INTO c_users(nickname,pw_hash,email,tier,is_member,created_at)
                             VALUES ('补录普通','x',%s,0,FALSE, now()-interval '5 days') RETURNING id""",
                    (f"bf0{rnd}@ex.com",), one=True)
            # recent paid member (tier 1)
            b = C.q(conn, """INSERT INTO c_users(nickname,pw_hash,email,tier,is_member,member_since,created_at)
                             VALUES ('补录会员','x',%s,1,TRUE, now()-interval '3 days', now()-interval '10 days') RETURNING id""",
                    (f"bf1{rnd}@ex.com",), one=True)
            # already premium
            c = C.q(conn, """INSERT INTO c_users(nickname,pw_hash,email,tier,is_member,tier2_since,created_at)
                             VALUES ('已是高级','x',%s,2,TRUE, now(), now()-interval '2 days') RETURNING id""",
                    (f"bf2{rnd}@ex.com",), one=True)
            # official
            drow = C.q(conn, """INSERT INTO c_users(nickname,pw_hash,email,tier,is_official,created_at)
                                VALUES ('官方号','x',%s,0,TRUE, now()-interval '1 days') RETURNING id""",
                       (f"bfo{rnd}@ex.com",), one=True)
            # merchant (tier 0 but merchant_until future)
            e = C.q(conn, """INSERT INTO c_users(nickname,pw_hash,email,tier,merchant_until,merchant_started,merchant_plan,created_at)
                             VALUES ('商家号','x',%s,0, now()+interval '20 days', now(), 'month', now()-interval '4 days') RETURNING id""",
                    (f"bfm{rnd}@ex.com",), one=True)
            # too old
            f = C.q(conn, """INSERT INTO c_users(nickname,pw_hash,email,tier,created_at)
                             VALUES ('三十天前','x',%s,0, now()-interval '40 days') RETURNING id""",
                    (f"bfoold{rnd}@ex.com",), one=True)
            bot = C.ensure_bot(conn)
            res1 = P.run_backfill(conn)
            res2 = P.run_backfill(conn)

        check("补录升级普通和会员", res1["granted"] >= 2, res1)
        check("补录幂等不再重复", res2["granted"] == 0, res2)

        with C.db() as conn:
            def rec(uid):
                return C.q(conn, "SELECT nickname,tier,promo_revert_tier,promo_premium_until FROM c_users WHERE id=%s",
                           (uid,), one=True)
            ra, rb, rc = rec(a["id"]), rec(b["id"]), rec(c["id"])
            rd, re, rf = rec(drow["id"]), rec(e["id"]), rec(f["id"])
            rbot = rec(bot["id"])
            aud_b = C.q(conn, "SELECT detail,from_tier FROM c_premium_promo_audit WHERE user_id=%s AND action='grant'",
                        (b["id"],), one=True)
        check("补录普通→高级，回退档 0", ra["tier"] == 2 and ra["promo_revert_tier"] == 0 and ra["promo_premium_until"], ra)
        check("补录会员→高级，回退档 1", rb["tier"] == 2 and rb["promo_revert_tier"] == 1 and rb["promo_premium_until"], rb)
        check("已是高级不被改", rc["tier"] == 2 and rc["promo_premium_until"] is None, rc)
        check("官方号不改", rd["tier"] == 0 and rd["promo_premium_until"] is None, rd)
        check("商家号不改", re["tier"] == 0 and re["promo_premium_until"] is None, re)
        check("30 天前不改", rf["tier"] == 0 and rf["promo_premium_until"] is None, rf)
        check("机器人不改", rbot["tier"] == 0, rbot)
        check("补录审计文案", aud_b and aud_b["detail"] == "推广期自动升级" and aud_b["from_tier"] == 1, aud_b)

        # flag off: backfill does not grant more
        os.environ["PROMO_AUTO_PREMIUM"] = "0"
        with C.db() as conn:
            g = C.q(conn, """INSERT INTO c_users(nickname,pw_hash,email,tier,created_at)
                             VALUES ('关闭后补录','x',%s,0, now()-interval '1 days') RETURNING id""",
                    (f"bfx{rnd}@ex.com",), one=True)
            res_off = P.run_backfill(conn)
            rg = C.q(conn, "SELECT tier FROM c_users WHERE id=%s", (g["id"],), one=True)
        check("关闭后补录禁用", res_off.get("disabled") is True and res_off["granted"] == 0, res_off)
        check("关闭后新的近期账号不补", rg["tier"] == 0, rg)
        os.environ["PROMO_AUTO_PREMIUM"] = "1"

        # -------- paid t1 during promo: stay 2 now, revert to 1 later --------
        u3 = TestClient(app)
        r = u3.post("/api/community/register", json={
            "account": f"p3{rnd}@example.com", "password": "Passw0rd!xyz", "nickname": "中途开会员", "agree": True
        }, headers=H)
        check("中途开会员：先注册", r.status_code == 200, r.text)
        with C.db() as conn:
            uid3 = C.q(conn, "SELECT id FROM c_users WHERE nickname=%s", ("中途开会员",), one=True)["id"]
            C.set_tier(conn, uid3, 1)
            mid = C.q(conn, "SELECT tier, promo_revert_tier, promo_premium_until FROM c_users WHERE id=%s", (uid3,), one=True)
        check("推广期内付费会员仍显示高级", mid["tier"] == 2 and mid["promo_revert_tier"] == 1 and mid["promo_premium_until"], mid)

        # -------- paid t2 during promo: keep 2 after expiry, no promo countdown --------
        u4 = TestClient(app)
        r = u4.post("/api/community/register", json={
            "account": f"p4{rnd}@example.com", "password": "Passw0rd!xyz", "nickname": "正式高级", "agree": True
        }, headers=H)
        check("正式开通：先注册", r.status_code == 200, r.text)
        with C.db() as conn:
            uid4 = C.q(conn, "SELECT id FROM c_users WHERE nickname=%s", ("正式高级",), one=True)["id"]
            C.set_tier(conn, uid4, 2)
            paid = C.q(conn, "SELECT tier, promo_revert_tier, promo_premium_until FROM c_users WHERE id=%s", (uid4,), one=True)
        check("正式开通高级后结束倒计时", paid["tier"] == 2 and paid["promo_premium_until"] is None and paid["promo_revert_tier"] == 2, paid)
        me4 = u4.get("/api/me/summary").json()["user"]
        check("正式高级不再显示限时说明", me4["tier"] == 2 and not me4["promo_premium"] and not me4["promo_notice"], me4)

        # -------- expiry reverts --------
        with C.db() as conn:
            # expire 新用户甲 (revert 0) and 中途开会员 (revert 1) and 补录会员 (revert 1)
            C.q(conn, """UPDATE c_users SET promo_premium_until=now()-interval '1 hour'
                         WHERE nickname IN ('新用户甲','中途开会员','补录会员')""")
            nexp = P.expire_due(conn)
        check("到期回退人数", len(nexp) >= 3, len(nexp))
        with C.db() as conn:
            def tname(n):
                return C.q(conn, "SELECT nickname,tier,is_member,promo_premium_until,member_since,tier2_since FROM c_users WHERE nickname=%s",
                           (n,), one=True)
            xa, xb, xc = tname("新用户甲"), tname("中途开会员"), tname("补录会员")
            xd = tname("正式高级")
        check("到期普通用户回到 0", xa["tier"] == 0 and not xa["is_member"] and xa["promo_premium_until"] is None and xa["tier2_since"] is None, xa)
        check("到期付费会员回到 1", xb["tier"] == 1 and xb["is_member"] and xb["member_since"], xb)
        check("到期补录会员回到 1", xc["tier"] == 1 and xc["is_member"], xc)
        check("正式高级到期后仍是 2", xd["tier"] == 2, xd)
        me_exp = u1.get("/api/me/summary").json()["user"]
        check("到期后我的页不再显示限时说明", not me_exp["promo_premium"] and me_exp["tier"] == 0, me_exp)

        # expire is idempotent
        with C.db() as conn:
            n2 = P.expire_due(conn)
        check("到期处理幂等", n2 == [] or len(n2) == 0, n2)

        # custom days
        os.environ["PROMO_PREMIUM_DAYS"] = "7"
        check("可配置天数", P.promo_premium_days() == 7)
        u5 = TestClient(app)
        r = u5.post("/api/community/register", json={
            "account": f"p5{rnd}@example.com", "password": "Passw0rd!xyz", "nickname": "七天用户", "agree": True
        }, headers=H)
        check("7 天配置可注册", r.status_code == 200, r.text)
        d7 = u5.get("/api/me/summary").json()["user"]["promo_premium_until"]
        dd = datetime.strptime(d7, "%Y-%m-%d").date()
        check("7 天到期日", 6 <= (dd - today).days <= 8, (d7, today))
        os.environ["PROMO_PREMIUM_DAYS"] = "30"

    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        C._schema_ready = False
        C._psycopg = None

    print("RESULT ok", ok, "fail", fail)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
