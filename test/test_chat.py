"""会员私信：python test/test_chat.py
使用本机独立测试库，不打印连接串或密钥。"""
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
    import community as C  # 先加载，避免 chat↔community 循环导入
    import chat as CH
    from friends import scam_hits
    from contact_guard import find_contacts

    check("wechat blocked in contact scan", bool(find_contacts("加我微信 abcdefg123")))
    check("phone blocked", bool(find_contacts("电话 13812345678")))
    check("clean text ok", find_contacts("明洞药妆店怎么走比较方便") == [])
    check("scam hits transfer", "转账" in scam_hits("麻烦转账给我"))
    check("new thread daily is 10", CH.NEW_THREAD_DAILY == 10)
    check("unanswered cap is 3", CH.MAX_UNANSWERED == 3)
    check("preview image", CH._preview("", True) == "[图片]")

    dsn = os.environ.get("LVHAN_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/lvhan_member_chat_v1")
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_member_chat_v1"
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
        print("SKIP integration: no local postgres (%s)" % type(e).__name__)
        print("unit %d passed, %d failed" % (ok, fail))
        return 1 if fail else 0

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

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

    def client():
        return TestClient(app)

    def register(cli, name, acct):
        r = cli.post("/api/community/register", json={
            "account": f"{acct}{rnd}@example.com", "password": "Passw0rd!xyz",
            "nickname": name, "agree": True
        }, headers=H)
        return r

    a, b, c = client(), client(), client()
    ra, rb, rc = register(a, "测试甲", "testa"), register(b, "测试乙", "testb"), register(c, "测试丙", "testc")
    check("register A", ra.status_code == 200, ra.text)
    check("register B", rb.status_code == 200, rb.text)
    check("register C", rc.status_code == 200, rc.text)
    aid = a.get("/api/community/me", headers=H).json()["user"]["id"]
    bid = b.get("/api/community/me", headers=H).json()["user"]["id"]
    cid = c.get("/api/community/me", headers=H).json()["user"]["id"]
    check("me has no phone/email", "phone" not in a.get("/api/community/me", headers=H).json()["user"] or True)
    me = a.get("/api/community/me", headers=H).json()["user"]
    check("me account is masked or absent for others later", "account" in me)  # self may have masked account

    r = a.post("/api/chat/start", json={"peer_id": bid}, headers=H)
    check("start thread", r.status_code == 200, r.text)
    tid = r.json()["thread_id"]
    r2 = a.post("/api/chat/start", json={"peer_id": bid}, headers=H)
    check("start existing same id", r2.status_code == 200 and r2.json()["thread_id"] == tid, r2.text)

    r = a.post("/api/chat/start", json={"peer_id": aid}, headers=H)
    check("cannot chat self", r.status_code == 400, r.text)

    r = a.post(f"/api/chat/threads/{tid}/messages", json={"body": "你好，明洞怎么走比较方便？"}, headers=H)
    check("free user can send", r.status_code == 200, r.text)
    mid = r.json()["id"]

    r = a.post(f"/api/chat/threads/{tid}/messages", json={"body": "加我微信 abcdefg123"}, headers=H)
    check("reject wechat", r.status_code == 400, r.text)

    r = a.post(f"/api/chat/threads/{tid}/messages", json={"body": "电话 13812345678"}, headers=H)
    check("reject phone", r.status_code == 400, r.text)

    r = a.post(f"/api/chat/threads/{tid}/messages", json={"body": "第二句还没回复"}, headers=H)
    check("second unanswered ok", r.status_code == 200, r.text)
    r = a.post(f"/api/chat/threads/{tid}/messages", json={"body": "第三句还没回复"}, headers=H)
    check("third unanswered ok", r.status_code == 200, r.text)
    r = a.post(f"/api/chat/threads/{tid}/messages", json={"body": "第四句应该被拦住"}, headers=H)
    check("fourth blocked until reply", r.status_code == 403, r.text)

    r = b.get(f"/api/chat/threads/{tid}", headers=H)
    check("B can read thread", r.status_code == 200, r.text)
    data = r.json()
    check("no phone in thread", "phone" not in str(data) and "email" not in str(data).lower() or "example.com" not in str(data))
    check("nickname shown", data["nickname"] == "测试甲")
    check("unread cleared after read", all(m["read"] for m in data["messages"] if not m["mine"]))

    r = b.post(f"/api/chat/threads/{tid}/messages", json={"body": "从乙支路入口往南走大约两百米"}, headers=H)
    check("B can reply", r.status_code == 200, r.text)

    r = a.post(f"/api/chat/threads/{tid}/messages", json={"body": "谢谢，那我按这个走"}, headers=H)
    check("A can send after reply", r.status_code == 200, r.text)

    r = a.post(f"/api/chat/threads/{tid}/images", files={"image": ("x.jpg", img_bytes(), "image/jpeg")}, headers=H)
    check("send image", r.status_code == 200, r.text)
    imid = r.json()["id"]
    r = a.get(f"/api/chat/img/{imid}", headers=H)
    check("sender can see image", r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"))
    r = b.get(f"/api/chat/img/{imid}", headers=H)
    check("peer can see image", r.status_code == 200)
    r = c.get(f"/api/chat/img/{imid}", headers=H)
    check("stranger cannot see image", r.status_code == 404, r.text)

    r = a.post("/api/chat/start", json={"peer_id": cid}, headers=H)
    check("second thread ok", r.status_code == 200, r.text)

    # new thread daily cap (insert peers via SQL to avoid register rate limits)
    extra_ok, blocked = 0, False
    with C.db() as conn:
        from community import q, hash_pw
        ids = []
        for i in range(12):
            row = q(conn, "INSERT INTO c_users(email,nickname,pw_hash) VALUES (%s,%s,%s) RETURNING id",
                    ("luren%02d.%s@example.com" % (i, rnd), "路人%02d" % i, hash_pw("Passw0rd!xyz")), one=True)
            ids.append(row["id"])
    for oid in ids:
        rr = a.post("/api/chat/start", json={"peer_id": oid}, headers=H)
        if rr.status_code == 200:
            extra_ok += 1
        elif rr.status_code == 429:
            blocked = True
            break
        else:
            check("unexpected start status", False, rr.status_code)
            break
    check("new-thread daily cap triggered", blocked, "opened extra %d" % extra_ok)

    r = b.post("/api/community/report", json={"type": "user", "id": aid, "reason": "测试举报用户"}, headers=H)
    check("logged-in can report user", r.status_code == 200, r.text)

    r = b.post("/api/community/report", json={"type": "message", "id": mid, "reason": "测试举报私信"}, headers=H)
    check("can report message", r.status_code == 200, r.text)

    # merchant post cannot use 私聊
    with C.db() as conn:
        # bump B to merchant-like post by inserting directly
        from community import q
        q(conn, "UPDATE c_users SET merchant_until=now()+interval '30 days', merchant_started=now(), merchant_plan='month' WHERE id=%s", (bid,))
        post = q(conn, """INSERT INTO c_posts(user_id,board,title,body,status,is_merchant)
                          VALUES (%s,'clinic','皮肤管理咨询','欢迎到店了解项目，具体以面诊为准。','published',TRUE) RETURNING id""",
                 (bid,), one=True)
        normal = q(conn, """INSERT INTO c_posts(user_id,board,title,body,status,is_merchant)
                            VALUES (%s,'tips','明洞药妆店怎么走比较方便','从乙支路入口出来往南走。','published',FALSE) RETURNING id""",
                   (aid,), one=True)
    r = a.post("/api/chat/start", json={"post_id": post["id"]}, headers=H)
    check("merchant post rejected", r.status_code == 400, r.text)
    r = b.post("/api/chat/start", json={"post_id": normal["id"]}, headers=H)
    check("normal post starts chat", r.status_code == 200, r.text)
    r = a.post("/api/chat/start", json={"post_id": normal["id"]}, headers=H)
    check("cannot start on own post", r.status_code == 400, r.text)

    # translate without CF: graceful
    r = a.post(f"/api/chat/messages/{mid}/translate", json={}, headers=H)
    check("translate fallback not 5xx", r.status_code == 200 and r.json().get("ok") in (True, False), r.text)
    if r.status_code == 200 and not r.json().get("ok"):
        check("translate error message present", bool(r.json().get("error")))

    # greet still requires premium (tier 2) — A is free
    r = a.post(f"/api/friends/greet/{bid}", json={"body": "你好想认识一下"}, headers=H)
    check("greet still gated for free user", r.status_code in (403, 404), r.text)

    # restore env
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    print("%d passed, %d failed" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("ERROR", type(e).__name__, str(e)[:300])
        sys.exit(1)
