"""旅游搭子实名核验：python test/test_buddy_verify.py
使用本机 Postgres 独立测试库，不打印连接串或密钥。"""
import io, os, sys, random, tempfile
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


def img_bytes(w=400, h=300, color=(80, 120, 200)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG", quality=80)
    return b.getvalue()


def main():
    dsn = os.environ.get("LVHAN_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/lvhan_buddy_verify_v1")
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_buddy_verify_v1"
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
    import buddy_verify as BV

    tmp = tempfile.mkdtemp(prefix="lh-idv-")
    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "COOKIE_INSECURE", "ADMIN_PASSWORD", "ID_VERIFY_DIR", "ID_VERIFY_KEY")}
    os.environ["DATABASE_URL"] = dsn
    os.environ["COOKIE_INSECURE"] = "1"
    os.environ["ADMIN_PASSWORD"] = "test-admin-pw-1234"
    os.environ["ID_VERIFY_DIR"] = tmp
    os.environ["ID_VERIFY_KEY"] = "test-id-verify-key-do-not-use"
    C._schema_ready = False
    C._psycopg = None
    app = FastAPI()
    app.include_router(C.router)
    H = {"X-LH": "1"}
    rnd = random.randint(100000, 999999)
    PF = {"nickname": "搭子甲", "gender": "m", "age": 28, "height": 175, "city": "首尔", "area": "弘大",
          "visa": "", "purpose": "buddy", "intro": "想来一次首尔三日游，找人一起坐地铁逛景点和吃饭。", "visible": True}
    DATE_PF = {**PF, "purpose": "date", "nickname": "交友乙", "intro": "喜欢咖啡和散步，想认识一起吃饭的朋友。"}

    def register(cli, name):
        r = cli.post("/api/community/register", json={
            "account": f"{name}{rnd}@example.com", "password": "Passw0rd!xyz",
            "nickname": name, "agree": True
        }, headers=H)
        return r

    def submit(cli, last4="1234", **extra):
        data = {"real_name": "测试用户", "nationality": "中国", "id_type": "passport",
                "last4": last4, "consent": "1"}
        data.update(extra)
        return cli.post("/api/id-verify", data=data, files={"image": ("doc.jpg", img_bytes(), "image/jpeg")}, headers=H)

    try:
        alice = TestClient(app)
        bob = TestClient(app)
        cara = TestClient(app)
        dan = TestClient(app)
        eve = TestClient(app)
        adm = TestClient(app)
        guest = TestClient(app)

        # pages + legal copy
        r = guest.get("/community/friends/verify")
        check("verify page 200", r.status_code == 200, r.status_code)
        for w in ("实名核验", "后四位", "不收集", "住民登录号", "仅限结伴旅游", "不是交友/约会平台"):
            check("verify page has %s" % w, w in r.text, w)
        r = guest.get("/community/friends")
        check("friends list page distinguishes badges", "已实名" in r.text and "已认证" in r.text, r.text[r.text.find("disclaimer"):r.text.find("disclaimer")+200] if "disclaimer" in r.text else r.status_code)
        check("friends list disclaimer", "仅限结伴旅游" in r.text and "不是交友/约会平台" in r.text)
        pv = (Path(ROOT) / "static" / "privacy.html").read_text(encoding="utf-8")
        check("privacy last4 only", "后四位" in pv and "不收集" in pv and "住民登录号" in pv)
        check("privacy photo deleted after review", "通过或拒绝后立即删除" in pv)
        check("privacy PIPA", "个人信息保护法" in pv)
        check("no full passport collection claim", "不收集完整住民登录号或完整护照号" in pv or "不收集、不存储完整住民登录号" in pv)

        meta = guest.get("/api/id-verify/meta").json()
        check("meta types", "passport" in meta["id_types"] and "alien_reg" in meta["id_types"], meta)
        check("meta disclaimer", "不是交友/约会平台" in meta["disclaimer"])

        check("submit needs auth", submit(guest).status_code == 401)
        check("submit needs csrf", alice.post("/api/id-verify", data={"real_name": "测试用户", "nationality": "中国",
              "id_type": "passport", "last4": "1234", "consent": "1"},
              files={"image": ("doc.jpg", img_bytes(), "image/jpeg")}).status_code in (401, 403))

        r = register(alice, "Alice")
        check("register alice", r.status_code == 200, r.text)
        r = register(bob, "Bob")
        check("register bob", r.status_code == 200, r.text)
        r = register(cara, "Cara")
        check("register cara", r.status_code == 200, r.text)
        r = register(dan, "Dan")
        check("register dan", r.status_code == 200, r.text)
        r = register(eve, "Eve")
        check("register eve", r.status_code == 200, r.text)
        uid_a = alice.get("/api/community/me").json()["user"]["id"]
        uid_b = bob.get("/api/community/me").json()["user"]["id"]
        uid_c = cara.get("/api/community/me").json()["user"]["id"]
        uid_d = dan.get("/api/community/me").json()["user"]["id"]
        uid_e = eve.get("/api/community/me").json()["user"]["id"]
        check("me has id_verified false", alice.get("/api/community/me").json()["user"]["id_verified"] is False)

        r = adm.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H)
        check("admin login", r.status_code == 200, r.text)
        for uid in (uid_a, uid_b, uid_c, uid_d, uid_e):
            rr = adm.post(f"/api/admin/users/{uid}", json={"action": "set_tier", "tier": 2}, headers=H)
            check("set tier 2 for %s" % uid, rr.status_code == 200, rr.text)

        # date purpose still works without KYC
        r = eve.post("/api/friends/me", json=DATE_PF, headers=H)
        check("date profile without verify ok", r.status_code == 200, r.text)

        # buddy post blocked until verified
        r = alice.post("/api/friends/me", json=PF, headers=H)
        check("unverified cannot post buddy", r.status_code == 403 and "实名核验" in r.text, r.text)

        # last4 validation
        r = submit(alice, last4="123456-1234567")
        check("reject full RRN", r.status_code == 400 and "后四位" in r.text, r.text)
        r = submit(alice, last4="E12345678")
        check("reject long id", r.status_code == 400 and "后四位" in r.text, r.text)
        r = submit(alice, last4="12")
        check("reject short last4", r.status_code == 400, r.text)
        r = submit(alice, last4="1234", consent="0")
        check("reject without consent", r.status_code == 400 and "同意" in r.text, r.text)

        r = submit(alice, last4="ab12")
        check("submit pending", r.status_code == 200, r.text)
        item = r.json()["item"]
        vid = item["id"]
        check("pending has last4 only", item["last4"] == "AB12" and item["status"] == "pending", item)
        check("no full id in response", "123456" not in r.text)

        stored = list(Path(tmp).glob("*.bin"))
        check("encrypted file stored privately", len(stored) == 1, stored)
        raw = stored[0].read_bytes()
        check("stored blob is not a jpeg", raw[:3] != b"\xff\xd8\xff", raw[:8])
        # public must not serve the private dir
        check("image not under /static", "/static/" not in str(stored[0]))
        check("public cannot fetch verify image", guest.get(f"/api/admin/id-verify/{vid}/image").status_code in (401, 403, 404))
        check("user cannot fetch verify image", alice.get(f"/api/admin/id-verify/{vid}/image").status_code in (401, 403, 404))

        r = submit(alice, last4="9999")
        check("second pending rejected", r.status_code == 400 and "待审核" in r.text, r.text)

        # admin queue + image decrypt
        lst = adm.get("/api/admin/id-verify?status=pending").json()
        check("admin queue has item", any(x["id"] == vid for x in lst["items"]), lst)
        im = adm.get(f"/api/admin/id-verify/{vid}/image")
        check("admin can read decrypted jpeg", im.status_code == 200 and im.headers["content-type"].startswith("image/jpeg"), im.status_code)
        check("admin image no-store", "no-store" in (im.headers.get("cache-control") or "").lower(), im.headers)

        r = adm.post(f"/api/admin/id-verify/{vid}", json={"action": "approve"}, headers=H)
        check("admin approve", r.status_code == 200, r.text)
        check("image deleted after approve", not stored[0].exists(), list(Path(tmp).iterdir()))
        me = alice.get("/api/id-verify/me").json()
        check("alice now verified", me["verified"] is True and me["application"]["status"] == "approved", me)
        check("image_name cleared", me["application"]["has_image"] is False, me)
        check("/me id_verified", alice.get("/api/community/me").json()["user"]["id_verified"] is True)

        r = alice.post("/api/friends/me", json=PF, headers=H)
        check("verified can post buddy", r.status_code == 200, r.text)

        # unverified viewer sees teaser, not contact details
        lst = bob.get("/api/friends/list?purpose=buddy").json()
        check("list has disclaimer", "不是交友/约会平台" in (lst.get("disclaimer") or ""))
        teasers = [c for c in lst["cards"] if c.get("need_verify") or c.get("teaser")]
        check("unverified sees teaser not uid", teasers and "uid" not in teasers[0] and teasers[0]["nickname"].endswith("**"), teasers)
        pr = bob.get(f"/api/friends/profile/{uid_a}").json()
        check("unverified profile is teaser", pr.get("need_verify") and "intro" not in pr, pr)
        r = bob.post(f"/api/friends/greet/{uid_a}", json={"body": "你好，想一起去景福宫逛逛"}, headers=H)
        check("unverified cannot greet buddy", r.status_code == 403, r.text)

        # also verify bob so he can see and report
        r = submit(bob, last4="7788")
        check("bob submit", r.status_code == 200, r.text)
        vid_b = r.json()["item"]["id"]
        r = adm.post(f"/api/admin/id-verify/{vid_b}", json={"action": "approve"}, headers=H)
        check("approve bob", r.status_code == 200, r.text)
        lst = bob.get("/api/friends/list?purpose=buddy").json()
        full = [c for c in lst["cards"] if c.get("uid") == uid_a]
        check("verified sees full buddy card with badge", full and full[0].get("id_verified") is True, lst)
        pr = bob.get(f"/api/friends/profile/{uid_a}").json()
        check("verified can open profile", pr.get("uid") == uid_a and pr.get("id_verified") is True, pr)

        # reject flow deletes image
        r = submit(cara, last4="4444")
        check("cara submit", r.status_code == 200, r.text)
        vid_c = r.json()["item"]["id"]
        bins = list(Path(tmp).glob("*.bin"))
        check("cara image present", len(bins) >= 1)
        r = adm.post(f"/api/admin/id-verify/{vid_c}", json={"action": "reject", "reason": "后四位无法核对"}, headers=H)
        check("admin reject", r.status_code == 200, r.text)
        cara_me = cara.get("/api/id-verify/me").json()
        check("cara rejected not verified", cara_me["verified"] is False and cara_me["application"]["status"] == "rejected", cara_me)
        check("rejected image gone", not any(p.exists() and p.stat().st_size > 0 and p.name.endswith(".bin") for p in bins) or all(not p.exists() for p in bins), list(Path(tmp).iterdir()))
        check("cara image 404 after reject", adm.get(f"/api/admin/id-verify/{vid_c}/image").status_code == 404)

        # resubmit after reject
        r = submit(cara, last4="5555")
        check("resubmit after reject", r.status_code == 200, r.text)
        vid_c2 = r.json()["item"]["id"]
        adm.post(f"/api/admin/id-verify/{vid_c2}", json={"action": "approve"}, headers=H)

        # auto-hide after 3 reports
        for cli, last in ((cara, "1111"), (dan, "2222"), (eve, "3333")):
            # eve already has date profile; still can report
            if cli is not eve:
                pass
        # eve is not id_verified; reports only need login
        r = bob.post("/api/friends/buddy-report", json={"target_id": uid_a, "reason": "虚假资料", "detail": "资料看起来不像本人。"}, headers=H)
        check("report 1", r.status_code == 200 and r.json()["hidden"] is False, r.text)
        r = cara.post("/api/friends/buddy-report", json={"target_id": uid_a, "reason": "诈骗或索要钱财", "detail": "提到转账一起订票。"}, headers=H)
        check("report 2", r.status_code == 200 and r.json()["hidden"] is False, r.text)
        r = dan.post("/api/friends/buddy-report", json={"target_id": uid_a, "reason": "其他", "detail": "感觉不太安全想请管理员看看。"}, headers=H)
        check("report 3 auto hide", r.status_code == 200 and r.json().get("hidden") is True, r.text)
        check("hidden profile 404 for others", bob.get(f"/api/friends/profile/{uid_a}").status_code == 404)
        hidden_cards = [c for c in bob.get("/api/friends/list?purpose=buddy").json()["cards"] if c.get("uid") == uid_a]
        check("hidden listing removed from list", hidden_cards == [], hidden_cards)
        check("report needs csrf", bob.post("/api/friends/buddy-report", json={"target_id": uid_a, "reason": "其他", "detail": "再报一次"}).status_code == 403)
        check("report needs auth", guest.post("/api/friends/buddy-report", json={"target_id": uid_a, "reason": "其他", "detail": "游客举报"}, headers=H).status_code == 401)

        # admin unhide
        r = adm.post(f"/api/admin/friends/{uid_a}", json={"action": "unhide"}, headers=H)
        check("admin unhide", r.status_code == 200, r.text)
        check("visible again", bob.get(f"/api/friends/profile/{uid_a}").status_code == 200)

        # ban
        r = adm.post(f"/api/admin/buddy-ban/{uid_b}", json={"action": "ban", "days": 7, "reason": "测试限制"}, headers=H)
        check("admin ban bob", r.status_code == 200 and r.json().get("banned") is True, r.text)
        check("banned cannot greet", bob.post(f"/api/friends/greet/{uid_a}", json={"body": "你好，一起去景福宫可以吗"}, headers=H).status_code == 403)
        teasers = [c for c in bob.get("/api/friends/list?purpose=buddy").json()["cards"] if c.get("teaser")]
        check("banned sees teaser", bool(teasers) or all(c.get("uid") != uid_a for c in bob.get("/api/friends/list?purpose=buddy").json()["cards"]), bob.get("/api/friends/list?purpose=buddy").json())
        pr = bob.get(f"/api/friends/profile/{uid_a}").json()
        check("banned profile teaser", pr.get("need_verify") is True, pr)
        r = adm.post(f"/api/admin/buddy-ban/{uid_b}", json={"action": "unban"}, headers=H)
        check("unban", r.status_code == 200, r.text)
        check("unbanned can open profile", bob.get(f"/api/friends/profile/{uid_a}").json().get("uid") == uid_a)

        # escaping / options
        opt = guest.get("/api/friends/options").json()
        check("options include disclaimer and reasons", "不是交友/约会平台" in opt["disclaimer"] and "虚假资料" in opt["report_reasons"], opt)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        C._schema_ready = False
        C._psycopg = None
        try:
            for p in Path(tmp).glob("*"):
                try:
                    p.unlink()
                except OSError:
                    pass
            os.rmdir(tmp)
        except OSError:
            pass

    print("RESULT", "ok", ok, "fail", fail)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
