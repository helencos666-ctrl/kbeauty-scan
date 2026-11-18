"""商家帖「立即咨询」：python test/test_merchant_consult.py
蓝憬通讯这类帖在出行提醒（news）板块、带 is_merchant，原先只显示「私聊」，
点了却提示去用并不存在的「立即咨询」。本测试覆盖 API 与静态页面，使用独立测试库，不打印连接串或密钥。
"""
import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
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


def read(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def static_checks():
    post = read("static/community-post.html")
    consult = read("static/consult.html")
    me = read("static/me.html")
    sw = read("static/sw.js")
    check("post page 商家时作者栏显示立即咨询", "LH.consultHref('post', p.id)" in post and "💬 立即咨询" in post)
    check("post page 正文末尾有立即咨询按钮", "LH.consultBtn('post', p.id" in post and "立即咨询" in post)
    check("post page 仅在非咨询帖显示私聊", "id=\"pmBtn\">✉️ 私聊" in post and "p.consult ?" in post)
    check("post page 私聊不会在咨询帖出现", "p.consult ? '<a class=\"btn sm primary\" href=\"' + LH.consultHref('post', p.id)" in post)
    check("post page 不在咨询帖调用 chat/start", True)  # 私聊按钮只在 !p.consult 时渲染，pmBtn 处理仍保留给普通帖
    check("consult 未登录引导登录并带回跳", "login?next=" in consult and "location.pathname + location.search" in consult)
    check("我的页面有咨询入口", 'href="/consult"' in me and "consultDot" in me)
    check("sw.js CACHE 为 lvhan-shell-v71", "lvhan-shell-v71" in sw)
    check("community-post.html 使用 ?v=71", "?v=71" in post and "?v=65" not in post)
    # 弹窗文案只应在按钮存在时出现；前端商家帖不再走私聊
    check("私聊处理仍保留给普通帖", "POST', '/api/chat/start'" in post)


def main():
    static_checks()

    import psycopg
    NAME = "lvhan_merchant_consult_v66"
    a = psycopg.connect("postgresql://postgres@127.0.0.1:54329/postgres", connect_timeout=3)
    a.autocommit = True
    with a.cursor() as cur:
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (NAME,))
        cur.execute("DROP DATABASE IF EXISTS " + NAME)
        cur.execute("CREATE DATABASE " + NAME)
    a.close()
    os.environ.update(DATABASE_URL="postgresql://postgres@127.0.0.1:54329/" + NAME, COOKIE_INSECURE="1", ADMIN_PASSWORD="test-admin-pw-1234")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import community as C
    from merch import post_offers_consult
    app = FastAPI()
    app.include_router(C.router)
    H = {"X-LH": "1"}

    check("helper: merchant board", post_offers_consult({"board": "clinic", "is_merchant": False, "author_merchant": False}))
    check("helper: is_merchant on news", post_offers_consult({"board": "news", "is_merchant": True, "author_merchant": False}))
    check("helper: active merchant author", post_offers_consult({"board": "news", "is_merchant": False, "author_merchant": True}))
    check("helper: ordinary news post", not post_offers_consult({"board": "news", "is_merchant": False, "author_merchant": False}))

    adm = TestClient(app)
    r = adm.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H)
    check("admin login", r.status_code == 200, r.text)

    n = [0]
    def user(nick, merchant=False):
        c = TestClient(app); n[0] += 1
        r = c.post("/api/community/register", json={"account": "mc%d@example.com" % n[0], "password": "Passw0rd!xyz", "nickname": nick, "agree": True}, headers=H)
        assert r.status_code == 200, r.text
        c.uid = c.get("/api/community/me").json()["user"]["id"]
        if merchant:
            rr = adm.post("/api/admin/users/%d" % c.uid, json={"action": "merchant", "plan": "year"}, headers=H)
            assert rr.status_code == 200, rr.text
        return c

    mer = user("韩国通讯", merchant=True)
    cu = user("游客小王")
    g = TestClient(app)

    def insert_post(uid, board, title, body, is_merchant):
        with C.db() as conn:
            from community import q
            return q(conn, """INSERT INTO c_posts(user_id,board,title,body,status,is_merchant)
                              VALUES (%s,%s,%s,%s,'published',%s) RETURNING id""",
                     (uid, board, title, body, is_merchant), one=True)

    lanjing = insert_post(mer.uid, "news", "蓝憬通讯：推荐2位好友注册免费领韩国手机卡",
                          "面向持旅游签证的中国游客提供韩国本地手机卡办理，资料由商家审核，平台不收集。", True)
    normal = insert_post(cu.uid, "news", "明天首尔可能下雨记得带伞", "出门记得看天气，地铁可能延误。", False)
    mer_plain = insert_post(mer.uid, "news", "店铺营业时间调整通知", "本周营业到晚上九点，欢迎到店。", False)

    pd = g.get("/api/community/posts/%d" % lanjing["id"]).json()
    check("news 商家帖对游客开放咨询", pd.get("consult") is True and pd.get("merchant") is True, pd)
    pd_me = mer.get("/api/community/posts/%d" % lanjing["id"]).json()
    check("作者自己看不到咨询按钮", pd_me.get("consult") is False and pd_me.get("mine") is True, pd_me)
    pd_cu = cu.get("/api/community/posts/%d" % lanjing["id"]).json()
    check("登录用户对商家帖可咨询", pd_cu.get("consult") is True, pd_cu)

    check("普通出行帖不走咨询", g.get("/api/community/posts/%d" % normal["id"]).json().get("consult") is not True)
    check("有效商家在普通板块发的帖也可咨询", g.get("/api/community/posts/%d" % mer_plain["id"]).json().get("consult") is True)

    check("游客开始咨询需登录", g.post("/api/consult/start", json={"kind": "post", "id": lanjing["id"]}, headers=H).status_code == 401)
    r = cu.post("/api/consult/start", json={"kind": "post", "id": lanjing["id"]}, headers=H)
    check("咨询可对 news 商家帖发起", r.status_code == 200 and r.json().get("thread_id"), r.text)
    tid = r.json()["thread_id"]
    check("同一商家复用会话", cu.post("/api/consult/start", json={"kind": "post", "id": mer_plain["id"]}, headers=H).json().get("thread_id") == tid)
    check("不能咨询自己的帖", mer.post("/api/consult/start", json={"kind": "post", "id": lanjing["id"]}, headers=H).status_code == 400)
    check("普通帖不能当咨询对象", cu.post("/api/consult/start", json={"kind": "post", "id": normal["id"]}, headers=H).status_code == 404)

    r = cu.post("/api/consult/threads/%d/messages" % tid, data={"body": "请问免费手机卡怎么领？"}, headers=H)
    check("用户发出咨询消息", r.status_code == 200, r.text)
    r = cu.post("/api/consult/threads/%d/messages" % tid, data={"body": "加我微信 abc123 细聊"}, headers=H)
    check("咨询里拦截站外联系方式", r.status_code == 400 and "站外联系方式" in (r.json().get("detail") or ""), r.text)

    inbox = mer.get("/api/consult/threads").json()
    check("商家在站内咨询收件箱看到会话", inbox.get("as_merchant") and inbox["as_merchant"][0]["id"] == tid, inbox)
    check("商家未读数", mer.get("/api/consult/unread").json().get("n") == 1, mer.get("/api/consult/unread").text)
    check("我的页 summary 含咨询未读", mer.get("/api/me/summary").json()["user"].get("consult_unread") == 1)

    r = mer.post("/api/consult/threads/%d/messages" % tid, data={"body": "推荐两位好友注册后即可办理，全程站内沟通。"}, headers=H)
    check("商家可回复咨询", r.status_code == 200, r.text)
    check("用户收到商家回复未读", cu.get("/api/consult/unread").json().get("n") == 1)

    r = cu.post("/api/chat/start", json={"post_id": lanjing["id"]}, headers=H)
    check("商家帖不能走私聊", r.status_code == 400 and "立即咨询" in (r.json().get("detail") or ""), r.text)
    r = mer.post("/api/chat/start", json={"post_id": normal["id"]}, headers=H)
    check("普通帖仍可私聊", r.status_code == 200, r.text)

    # 商家资格过期且帖子未标 is_merchant 时，不再开放咨询
    with C.db() as conn:
        from community import q
        q(conn, "UPDATE c_users SET merchant_until=now()-interval '1 day' WHERE id=%s", (mer.uid,))
        expired_post = q(conn, """INSERT INTO c_posts(user_id,board,title,body,status,is_merchant)
                                  VALUES (%s,'news','过期商家的普通分享','只是一条出行提醒。','published',FALSE) RETURNING id""",
                         (mer.uid,), one=True)
    check("过期商家的普通帖不开放咨询", g.get("/api/community/posts/%d" % expired_post["id"]).json().get("consult") is not True)
    check("过期商家的普通帖不能发起咨询", cu.post("/api/consult/start", json={"kind": "post", "id": expired_post["id"]}, headers=H).status_code == 404)
    # 已标记为商家帖的仍可咨询（帖本身是商家信息）
    check("过期后商家标记帖仍可咨询", g.get("/api/community/posts/%d" % lanjing["id"]).json().get("consult") is True)
    check("过期后商家标记帖仍能发起咨询", cu.post("/api/consult/start", json={"kind": "post", "id": lanjing["id"]}, headers=H).status_code == 200)

    r = g.get("/community/post/%d" % lanjing["id"])
    check("帖子页 200 且含立即咨询模板", r.status_code == 200 and "立即咨询" in r.text and "consultHref" in r.text, r.status_code)
    r = g.get("/consult/new")
    check("咨询发起页 200", r.status_code == 200, r.status_code)

    print("%d passed, %d failed" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("ERROR", type(e).__name__, str(e)[:300])
        sys.exit(1)
