"""社区端到端测试：python test/test_community.py http://127.0.0.1:8899  （需设置 ADMIN_PASSWORD 环境变量或 /tmp/lh_admin_pw）"""
import io, os, sys, random
import httpx
from PIL import Image


def run_ai_unit_tests():
    """不依赖外部已启动的服务：纯函数 + 可选的本机 Postgres/TestClient（mock DeepSeek）。"""
    import os, sys, time, random as _rnd
    from unittest.mock import patch
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    import community as C

    check("bot password unusable", C.check_pw("anything123", C.BOT_PW_HASH) is False)
    check("sanitize drops banned words + length", (lambda s: ("奋韩" not in s and "抖音" not in s and "直播" not in s and len(s) <= C.AI_MAX_CHARS and "参考" in s))(
        C.sanitize_ai_text("到了可以上奋韩问问。抖音直播间也有攻略。" + "参考一下路线。" * 40)))
    check("sanitize strips contact/url", "http" not in C.sanitize_ai_text("详见 https://example.com 微信: abcdefg123") and "abcdefg123" not in C.sanitize_ai_text("详见 https://example.com 微信: abcdefg123"))
    check("question detector", C.looks_like_question("第一次去首尔，明洞附近住哪里方便？") and not C.looks_like_question("今天天气不错，分享一下行程。"))
    check("@mention wants AI", C.comment_wants_ai("@旅韩小助手 帮忙看看", 2, 1) and C.comment_wants_ai("旅韩小助手，这个签证怎么办理", 2, 1))
    check("plain comment no AI", C.comment_wants_ai("感谢分享，我也遇到过", 2, 1) is False)
    check("op follow-up question wants AI", C.comment_wants_ai("再问一下，明洞哪边出口近？", 9, 9) is True)
    check("system prompt has safety rules", all(w in C.AI_SYSTEM for w in ["不能代办", "不推荐", "联系方式", "奋韩", "抖音", "直播", "200", "核实", "地铁线路", "营业时间"]))
    check("default AI on", C.DEFAULT_SETTINGS["ai_reply"] == "1" and "ai_reply" in C.BOOL_SETTINGS)
    check("price keys exclude ai_reply", "ai_reply" not in C.PRICE_KEYS)

    # mock DeepSeek
    fake = "明洞交通方便，住地铁站附近通常步行更轻松。这只是公开信息参考，入住前请自己再核实价格和位置。签证信息请以官方说明为准，不能代办。"
    class _Resp:
        def __init__(self, code=200, txt=fake):
            self.status_code = code
            self._txt = txt
        def json(self):
            return {"choices": [{"message": {"content": self._txt}}]}
    class _Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, json=None, headers=None):
            assert "Authorization" in (headers or [])
            assert (headers or {}).get("Authorization", "").startswith("Bearer ")
            # never used as assertion output
            return _Resp()
    saved_key = os.environ.get("DEEPSEEK_API_KEY")
    os.environ["DEEPSEEK_API_KEY"] = "test-mock-key-not-real"
    try:
        with patch("httpx.Client", _Client):
            out = C.ai_chat(C.AI_SYSTEM, "频道：住宿推荐\n标题：住哪里\n正文：第一次去首尔，明洞附近住哪里方便？")
        check("mocked ai_chat returns cleaned text", "明洞" in out and "奋韩" not in out and len(out) <= 200, out)
        class _Bad:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, *a, **k):
                raise RuntimeError("boom")
        with patch("httpx.Client", _Bad):
            check("ai_chat failure returns empty", C.ai_chat("s", "u") == "")
        class _500:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, *a, **k): return _Resp(500, "nope")
        with patch("httpx.Client", _500):
            check("ai_chat 500 returns empty", C.ai_chat("s", "u") == "")
    finally:
        if saved_key is None:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        else:
            os.environ["DEEPSEEK_API_KEY"] = saved_key

    # 本机 Postgres 集成（mock 模型，不打真实接口）
    dsn = os.environ.get("LVHAN_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/lvhan_p14_ai")
    ai_db = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_p14_ai"
    if not ai_db.isidentifier() or not ai_db.isascii():
        raise RuntimeError("bad test db name")
    try:
        import psycopg
        conn = psycopg.connect(dsn, connect_timeout=3)
        conn.close()
    except Exception as e:
        # 尝试建库后再连
        try:
            import psycopg
            admin = psycopg.connect("postgresql://postgres@127.0.0.1:54329/postgres", connect_timeout=3)
            admin.autocommit = True
            with admin.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (ai_db,))
                if not cur.fetchone():
                    cur.execute(f"CREATE DATABASE {ai_db}")
            admin.close()
            conn = psycopg.connect(dsn, connect_timeout=3); conn.close()
        except Exception as e2:
            check("AI db integration skipped (no local postgres)", True, str(e2)[:80])
            return

    import psycopg
    admin = psycopg.connect("postgresql://postgres@127.0.0.1:54329/postgres", connect_timeout=3)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (ai_db,))
        cur.execute(f"DROP DATABASE IF EXISTS {ai_db}")
        cur.execute(f"CREATE DATABASE {ai_db}")
    admin.close()

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "COOKIE_INSECURE", "ADMIN_PASSWORD", "DEEPSEEK_API_KEY")}
    os.environ["DATABASE_URL"] = dsn
    os.environ["COOKIE_INSECURE"] = "1"
    os.environ["ADMIN_PASSWORD"] = "test-admin-pw-1234"
    os.environ["DEEPSEEK_API_KEY"] = "test-mock-key-not-real"
    C._schema_ready = False
    C._psycopg = None
    C._ai_day.update(date=None, global_=0)
    C._ai_day["global"] = 0
    C._ai_day["user"] = None
    C._ai_user_hits.clear()
    app = FastAPI(); app.include_router(C.router)
    import seo as SEO; app.include_router(SEO.router)
    def fake_chat(system, user, timeout=20):
        return "明洞交通方便，住地铁站附近通常步行更轻松。这只是公开信息参考，入住前请自己再核实。签证请以官方说明为准，不能代办。"
    orig_chat = C.ai_chat
    C.ai_chat = fake_chat
    try:
        cli = TestClient(app)
        H = {"X-LH": "1"}
        # schema + bot
        r = cli.get("/api/community/prices")
        check("prices omit ai_reply", r.status_code == 200 and "ai_reply" not in r.json() and r.json().get("price_t1") == "9.9", r.text)
        # bot exists, no login surface
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id,nickname,pw_hash,phone,email,is_bot FROM c_users WHERE is_bot")
                bot = cur.fetchone()
        check("bot account created", bot and bot[1] == "旅韩小助手" and bot[2] == "disabled" and bot[3] is None and bot[4] is None and bot[5] is True, bot)

        r = cli.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H)
        check("unit admin login", r.status_code == 200, r.text)
        st = cli.get("/api/admin/settings").json()["settings"]
        keys = {x["key"]: x for x in st}
        check("admin settings has AI toggle default on", keys.get("ai_reply", {}).get("value") == "1" and keys["ai_reply"].get("type") == "bool", keys.get("ai_reply"))

        rnd = _rnd.randint(100000, 999999)
        acct = f"ai{rnd}@example.com"
        r = cli.post("/api/community/register", json={"account": acct, "password": "Passw0rd!xyz", "nickname": "旅韩小助手", "agree": True}, headers=H)
        check("cannot register bot nickname", r.status_code == 400, r.text)
        r = cli.post("/api/community/register", json={"account": acct, "password": "Passw0rd!xyz", "nickname": "测试作者", "agree": True}, headers=H)
        check("unit register author", r.status_code == 200, r.text)
        uid = cli.get("/api/community/me").json()["user"]["id"]
        r = cli.post(f"/api/admin/users/{uid}", json={"action": "set_tier", "tier": 2}, headers=H)
        check("unit set tier 2", r.status_code == 200, r.text)
        t0 = time.time()
        r = cli.post("/api/community/posts", data={"board": "hotel"[:0] + "pit", "title": "第一次去首尔住哪", "body": "第一次去首尔，明洞附近住哪里方便？希望交通方便。"}, headers=H)
        elapsed = time.time() - t0
        check("new post does not wait for AI", r.status_code == 200 and r.json().get("id") and elapsed < 2.0, (r.status_code, elapsed, r.text[:200]))
        pid = r.json()["id"]
        d0 = cli.get(f"/api/community/posts/{pid}").json()
        # 可能已经写完，但接口本身已返回；再等待后台线程
        C.wait_ai_replies(timeout=5)
        d = cli.get(f"/api/community/posts/{pid}").json()
        bots = [c for c in d["comment_list"] if c.get("is_bot")]
        check("AI replies to new post", len(bots) == 1 and bots[0]["author"] == "旅韩小助手" and "明洞" in bots[0]["body"] and bots[0]["mine"] is False, d["comment_list"])
        check("post still published after AI", d.get("title") == "第一次去首尔住哪" and d["comments"] == 1, d)

        # 普通评论不触发
        r = cli.post(f"/api/community/posts/{pid}/comments", json={"body": "感谢分享，我也遇到过"}, headers=H)
        check("plain comment ok", r.status_code == 200, r.text)
        C.wait_ai_replies(timeout=3)
        d = cli.get(f"/api/community/posts/{pid}").json()
        check("plain comment no extra AI", sum(1 for c in d["comment_list"] if c.get("is_bot")) == 1 and len(d["comment_list"]) == 2, d["comment_list"])

        # @旅韩小助手 触发
        r = cli.post(f"/api/community/posts/{pid}/comments", json={"body": "@旅韩小助手 再补充一下交通建议可以吗？"}, headers=H)
        check("mention comment ok", r.status_code == 200, r.text)
        C.wait_ai_replies(timeout=5)
        d = cli.get(f"/api/community/posts/{pid}").json()
        check("mention triggers second AI", sum(1 for c in d["comment_list"] if c.get("is_bot")) == 2, d["comment_list"])

        # 帖主追问
        r = cli.post("/api/community/posts", data={"board": "visa", "title": "K-ETA 怎么填", "body": "第一次办 K-ETA，网上资料有点乱，想听听大家经验。"}, headers=H)
        pid2 = r.json()["id"]
        C.wait_ai_replies(timeout=5)
        r = cli.post(f"/api/community/posts/{pid2}/comments", json={"body": "再问一下，机票还没出的话可以申请吗？"}, headers=H)
        check("op question comment ok", r.status_code == 200, r.text)
        C.wait_ai_replies(timeout=5)
        d = cli.get(f"/api/community/posts/{pid2}").json()
        check("op question triggers AI", sum(1 for c in d["comment_list"] if c.get("is_bot")) >= 2, d["comment_list"])

        # 关闭开关后不再回复
        r = cli.post("/api/admin/settings", json={"ai_reply": "0"}, headers=H)
        check("admin can turn AI off", r.status_code == 200 and r.json()["settings"]["ai_reply"] == "0", r.text)
        r = cli.post("/api/community/posts", data={"board": "pit", "title": "弘大换钱注意", "body": "弘大附近换钱要先看清牌价，有的店数字容易看错。"}, headers=H)
        pid3 = r.json()["id"]
        C.wait_ai_replies(timeout=3)
        d = cli.get(f"/api/community/posts/{pid3}").json()
        check("AI off means no bot comment", d["comments"] == 0 and not any(c.get("is_bot") for c in d["comment_list"]), d)

        # 失败不影响发帖（换一个账号避免触发发帖频率限制）
        C.ai_chat = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model down"))
        cli.post("/api/admin/settings", json={"ai_reply": "1"}, headers=H)
        acct2 = f"ai2{rnd}@example.com"
        cli2 = TestClient(app)
        r = cli2.post("/api/community/register", json={"account": acct2, "password": "Passw0rd!xyz", "nickname": "测试作者二", "agree": True}, headers=H)
        uid2 = cli2.get("/api/community/me").json()["user"]["id"]
        cli.post(f"/api/admin/users/{uid2}", json={"action": "set_tier", "tier": 2}, headers=H)
        r = cli2.post("/api/community/posts", data={"board": "pit", "title": "机场到市区交通", "body": "落地仁川以后去市区，巴士和机场铁路怎么选比较稳妥？"}, headers=H)
        check("post succeeds when AI raises", r.status_code == 200 and r.json().get("id"), r.text)
        C.wait_ai_replies(timeout=3)

        # 系统账号不可改
        bot_id = bot[0]
        r = cli.post(f"/api/admin/users/{bot_id}", json={"action": "ban"}, headers=H)
        check("cannot ban bot", r.status_code == 403, r.text)
        r = cli.post(f"/api/admin/users/{bot_id}", json={"action": "reset_pw"}, headers=H)
        check("cannot reset bot password", r.status_code == 403, r.text)

        # 会员编号 / 靓号
        me = cli.get("/api/community/me").json()["user"]
        uid = me["id"]
        check("unit me member_no defaults to id", me["member_no"] == uid, me)
        with psycopg.connect(dsn) as cx:
            with cx.cursor() as cur:
                cur.execute("SELECT member_no FROM c_users WHERE id=%s", (uid,))
                check("unit member_no persisted as id", cur.fetchone()[0] == uid)
        # 占用下一个 id，再注册应跳到再下一个
        with psycopg.connect(dsn) as cx:
            with cx.cursor() as cur:
                cur.execute("SELECT last_value FROM c_users_id_seq")
                nxt = cur.fetchone()[0] + 1
            cx.execute("UPDATE c_users SET member_no=%s WHERE id=%s", (nxt, uid))
            cx.commit()
        acct3 = f"ai3{rnd}@example.com"
        cli3 = TestClient(app)
        r = cli3.post("/api/community/register", json={"account": acct3, "password": "Passw0rd!xyz", "nickname": "跳号用户", "agree": True}, headers=H)
        check("unit register when id taken as vanity", r.status_code == 200, r.text)
        me3 = cli3.get("/api/community/me").json()["user"]
        check("new user skips taken member_no", me3["id"] == nxt and me3["member_no"] == nxt + 1, me3)
        # 把第一个作者的编号改回去再测改号接口
        r = cli.post(f"/api/admin/users/{uid}/member_no", json={"member_no": uid})
        check("unit set member_no needs csrf", r.status_code == 403)
        r = cli.post(f"/api/admin/users/{uid}/member_no", json={"member_no": 8888}, headers=H)
        check("unit admin sets member_no", r.status_code == 200 and r.json()["member_no"] == 8888, r.text)
        check("unit me reflects vanity no", cli.get("/api/community/me").json()["user"]["member_no"] == 8888)
        found = cli.get("/api/admin/users", params={"s": "8888"}).json()["users"]
        check("unit search by member_no", found and found[0]["id"] == uid and found[0]["member_no"] == 8888, found)
        r = cli.post(f"/api/admin/users/{uid2}/member_no", json={"member_no": 8888}, headers=H)
        check("unit duplicate member_no 409", r.status_code == 409, r.text)
        r = cli.post(f"/api/admin/users/{bot_id}/member_no", json={"member_no": 9}, headers=H)
        check("unit cannot change bot member_no", r.status_code == 403, r.text)
        r = cli.post(f"/api/admin/users/{uid}/member_no", json={"member_no": 0}, headers=H)
        check("unit member_no must be positive", r.status_code == 400, r.text)
        # 改回，避免影响后续断言
        r = cli.post(f"/api/admin/users/{uid}/member_no", json={"member_no": uid}, headers=H)
        check("unit restore member_no", r.status_code == 200, r.text)

        # 头像：上传、格式/大小拒绝、回退、管理员移除
        def _img(fmt="PNG", size=(400, 200), color=(200, 40, 80)):
            b = __import__("io").BytesIO(); Image.new("RGB", size, color).save(b, fmt); return b.getvalue()
        me = cli.get("/api/community/me").json()["user"]
        uid = me["id"]
        check("unit me has_avatar default false", me.get("has_avatar") is False, me)
        check("unit missing avatar 404 fallback", cli.get(f"/api/community/avatar/{uid}").status_code == 404)
        data = C.process_avatar(_img("PNG", (400, 200)))
        im = Image.open(io.BytesIO(data))
        check("unit process_avatar square jpeg 256", im.format == "JPEG" and im.size == (256, 256) and max(im.size) == 256, im.size)
        check("unit process_avatar strips exif", not dict(im.getexif() or {}), im.getexif())
        r = cli.post("/api/community/avatar", files={"image": ("a.png", _img(), "image/png")})
        check("unit avatar needs csrf", r.status_code == 403, r.text)
        guest_cli = TestClient(app)
        r = guest_cli.post("/api/community/avatar", files={"image": ("a.png", _img(), "image/png")}, headers=H)
        check("unit guest cannot upload avatar", r.status_code == 401, r.text)
        r = cli.post("/api/community/avatar", files={"image": ("a.png", _img(), "image/png")}, headers=H)
        check("unit upload png avatar", r.status_code == 200 and r.json().get("has_avatar") is True, r.text)
        check("unit me has_avatar true", cli.get("/api/community/me").json()["user"]["has_avatar"] is True)
        av = cli.get(f"/api/community/avatar/{uid}")
        im = Image.open(io.BytesIO(av.content))
        check("unit serve avatar jpeg 256", av.status_code == 200 and av.headers["content-type"].startswith("image/jpeg") and im.size == (256, 256), (av.status_code, im.size))
        # webp
        try:
            w = _img("WEBP", (300, 300), (10, 120, 200))
            r = cli.post("/api/community/avatar", files={"image": ("a.webp", w, "image/webp")}, headers=H)
            check("unit upload webp avatar", r.status_code == 200, r.text)
        except Exception as e:
            check("unit upload webp avatar skipped", True, str(e)[:80])
        gif = _img("GIF", (40, 40))
        r = cli.post("/api/community/avatar", files={"image": ("a.gif", gif, "image/gif")}, headers=H)
        check("unit gif avatar rejected", r.status_code == 400, r.text)
        r = cli.post("/api/community/avatar", files={"image": ("a.txt", b"not-an-image", "text/plain")}, headers=H)
        check("unit non-image avatar rejected", r.status_code == 400, r.text)
        big = b"x" * (5 * 1024 * 1024 + 2)
        r = cli.post("/api/community/avatar", files={"image": ("big.jpg", big, "image/jpeg")}, headers=H)
        check("unit oversized avatar rejected", r.status_code == 413, r.text)
        found = [u for u in cli.get("/api/admin/users", params={"s": str(me["member_no"])}).json()["users"] if u["id"] == uid]
        check("unit admin lists has_avatar", found and found[0]["has_avatar"] is True, found)
        r = cli.post(f"/api/admin/users/{uid}/clear_avatar", json={})
        check("unit admin clear avatar needs csrf", r.status_code == 403)
        r = cli.post(f"/api/admin/users/{uid}/clear_avatar", json={}, headers=H)
        check("unit admin removes avatar", r.status_code == 200, r.text)
        check("unit avatar gone after admin remove", cli.get(f"/api/community/avatar/{uid}").status_code == 404 and cli.get("/api/community/me").json()["user"]["has_avatar"] is False)
        # 旅韩小助手默认头像
        bot_av = cli.get(f"/api/community/avatar/{bot_id}")
        bim = Image.open(io.BytesIO(bot_av.content)) if bot_av.status_code == 200 else None
        check("unit bot has default avatar", bot_av.status_code == 200 and bot_av.headers["content-type"].startswith("image/jpeg") and bim.size == (256, 256), bot_av.status_code)
        r = cli.post(f"/api/admin/users/{bot_id}/clear_avatar", json={}, headers=H)
        check("unit cannot clear bot avatar", r.status_code == 403, r.text)
        # 页面回退渲染标记
        js = open(os.path.join(root, "static", "community.js"), encoding="utf-8").read()
        css = open(os.path.join(root, "static", "community.css"), encoding="utf-8").read()
        check("unit fallback avatar markup", "av-letter" in js and "function avatar" in js and "设置头像" in js)
        check("unit avatar css sizes", ".av-sm" in css and ".av-lg" in css and "56px" in css)
        # SEO 基础
        robots = cli.get("/robots.txt")
        check("unit robots allows crawlers and sitemap", robots.status_code == 200 and "Allow: /" in robots.text and "Sitemap:" in robots.text and "lvhanzhushou.com/sitemap.xml" in robots.text)
        sm = cli.get("/sitemap.xml")
        check("unit sitemap has public pages", sm.status_code == 200 and all(p in sm.text for p in ["/community", "/rules", "/member", "/attractions"]) and "<lastmod>" in sm.text, sm.text[:200])
        ch = cli.get("/community").text
        check("unit community seo tags", all(x in ch for x in ["rel=\"canonical\"", "og:image", "application/ld+json", "hreflang=\"zh-CN\"", "韩国交友"]))
    finally:
        C.ai_chat = orig_chat
        C._schema_ready = False
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8899"
# 每个测试客户端模拟不同来源 IP（线上由 Cloudflare 写入 CF-Connecting-IP，客户端无法伪造）
_RealClient = httpx.Client
class _IPClient(_RealClient):
    def __init__(self, *a, **k):
        h = dict(k.pop("headers", None) or {})
        h.setdefault("cf-connecting-ip", "10.%d.%d.%d" % (random.randint(0, 255), random.randint(0, 255), random.randint(1, 254)))
        super().__init__(*a, headers=h, **k)
httpx.Client = _IPClient
H = {"X-LH": "1"}
ADMIN_PW = os.environ.get("ADMIN_PASSWORD") or open("/tmp/lh_admin_pw").read().strip()
results = []
def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + str(extra)[:200]) if not cond else ""))

def img_bytes(w=3000, h=2000, color=(200, 80, 120)):
    b = io.BytesIO(); Image.new("RGB", (w, h), color).save(b, "PNG"); return b.getvalue()

run_ai_unit_tests()

rnd = random.randint(100000, 999999)
guest = httpx.Client(base_url=BASE)
alice = httpx.Client(base_url=BASE)   # 通过推广链接来的用户
bob = httpx.Client(base_url=BASE)     # 手动填推广码
carol = httpx.Client(base_url=BASE)   # 无推广码
admin = httpx.Client(base_url=BASE)

# 现有页面不受影响
for p in ["/", "/attractions", "/food", "/shopping", "/tips", "/beauty", "/sw.js", "/manifest.webmanifest", "/static/guide.css",
          "/community", "/community/new", "/community/post/1", "/login", "/member", "/admin", "/rules", "/static/community.js", "/healthz",
          "/community/boards", "/community/rate", "/community/friends", "/community/friends/edit", "/community/friends/1",
          "/community/messages", "/community/messages/1", "/community?tab=hot", "/community?board=rent"]:
    r = guest.get(p); check(f"GET {p} 200", r.status_code == 200, r.status_code)
swt = guest.get("/sw.js").text
check("sw.js cache bumped v26", "lvhan-shell-v26" in swt and "/rules" in swt and "/static/stations.json" in swt and "/community/friends" in swt and "/static/chat.html" in swt)
stj = guest.get("/static/stations.json").json()["stations"]; check("stations.json coords", stj.get("강남역") and stj.get("홍대입구역") and stj.get("건대입구역"))
mj = guest.get("/static/maps.js").text; check("maps.js naver+amap", "map.naver.com/p/directions/" in mj and "m.map.naver.com/?slng=" in mj and "uri.amap.com/navigation" in mj and "uri.amap.com/search" in mj)
pages = "".join(guest.get(p).text for p in ["/", "/attractions", "/food", "/shopping", "/tips", "/beauty"])
check("no Kakao/Google map links", "map.kakao.com" not in pages and "google.com/maps" not in pages and "Kakao 地图" not in pages)
check("pages load maps.js", all("/static/maps.js" in guest.get(p).text for p in ["/", "/attractions", "/food"]))
mp = guest.get("/member").text; check("member page has tiers + merchant", all(w in mp for w in ['data-p="price_t1"', 'data-p="price_t2"', 'data-p="price_m_month"', 'data-p="price_m_year"', "高级会员", "商家会员", "微信收款码"]))
check("homepage has 社区 entry", 'href="/community"' in guest.get("/").text)
check("no promo words", not any(w in "".join(guest.get(p).text for p in ["/community", "/member", "/community/friends", "/community/boards", "/community/rate", "/rules", "/community/friends/edit", "/community/messages"]) for w in ["直播间", "韩国大叔", "抖音"]))
adp = guest.get("/admin").text; check("admin page has AI toggle", "AI 自动回复" in adp and "ai_reply" in adp)
check("admin page has 改编号", "改编号" in adp and "/member_no" in adp and "u.member_no" in adp)
check("admin page has 移除头像", "移除头像" in adp and "/clear_avatar" in adp)
check("member page uses member_no", "u.member_no" in guest.get("/member").text)
mp2 = guest.get("/member").text
check("member page 设置头像", "设置头像" in mp2 and "LH.avatar" in mp2)
js = guest.get("/static/community.js").text
css = guest.get("/static/community.css").text
check("js has avatar helper + fallback", "function avatar" in js and "av-letter" in js and "设置头像" in js and "pickAvatar" in js)
check("css has larger avatars", ".av-sm" in css and ".av-lg" in css and "42px" in css and "56px" in css)
check("friend page shows member_no", "P.member_no" in guest.get("/community/friends/1").text)
ptp = guest.get("/community/post/1").text; check("post page has AI tag markup", "ai-tag" in ptp and "c.is_bot" in ptp)

ch = guest.get("/community").text
check("community home: 5 tabs", all(w in ch for w in ['data-t="follow"', '>精选<', '>实用干货<', '>今日热议<', '>刚发布<', '>我关注的<']))
check("community home: journey topics", all(w in ch for w in ["出发前", "在韩国", "找商家", "签证问答", "留学打听", "结伴同行", "踩坑避雷", "出行提醒", "租房找房", "闲置转让", "工作机会", "汇率换算", "住宿推荐", "包车用车", "皮肤管理·医美"]) and ch.count('class="topic"') >= 12 and 'class="tools"' not in ch)
check("community home: 旅韩交友 + sections + fab", all(w in ch for w in ["旅韩交友", "/community/friends", "今日热议", "避坑 &amp; 签证新帖", "刚发布", 'class="fab pill"', "写帖子", "韩友圈"]))
_seo_pages = "".join(guest.get(p).text for p in ["/community", "/community/friends", "/community/boards"])
_seo_pages = _seo_pages.replace("https://lvhanzhushou.com", "").replace("https://schema.org", "")
check("no external assets on new pages", "http://" not in _seo_pages and "https://" not in _seo_pages)
home = guest.get("/").text
check("home seo: unique title + canonical + og + jsonld", all(x in home for x in ["韩国旅游攻略", "首尔自由行", 'rel="canonical"', "og:image", "/static/og-cover.jpg", "application/ld+json", "WebSite", "Organization", 'hreflang="zh-CN"', 'lang="zh-CN"']))
check("verification metas omitted when unset", "google-site-verification" not in home and "baidu-site-verification" not in home and "naver-site-verification" not in home)
rb = guest.get("/robots.txt")
check("robots.txt allows crawlers and points to sitemap", rb.status_code == 200 and "Allow: /" in rb.text and "Sitemap: https://lvhanzhushou.com/sitemap.xml" in rb.text and "Disallow: /admin" in rb.text)
sm = guest.get("/sitemap.xml")
check("sitemap.xml lists public pages and lastmod", sm.status_code == 200 and all(p in sm.text for p in ["https://lvhanzhushou.com/</loc>", "/attractions", "/tips", "/community", "/rules", "/member"]) and "<lastmod>" in sm.text, sm.status_code)
# IndexNow 密钥文件
import pathlib as _pl
_keyp = _pl.Path(__file__).resolve().parents[1] / "indexnow.key"
if _keyp.is_file():
    _ik = _keyp.read_text(encoding="utf-8").strip()
    ikr = guest.get("/" + _ik + ".txt")
    check("indexnow key file served", ikr.status_code == 200 and ikr.text.strip() == _ik, ikr.status_code)
else:
    check("indexnow key file served", False, "missing indexnow.key")
allp = "".join(guest.get(p).text for p in ["/", "/community", "/community/boards", "/rules", "/member", "/login", "/community/new", "/community/friends", "/community/messages", "/static/community.css", "/static/community.js"])
check("no third-party community names", not any(w in allp.lower() for w in ["\u594b\u97e9", "fen" + "han", "52hrtt", "icnkr"]))
rt = guest.get("/rules").text
check("rules: own wording + our tiers", all(w in rt for w in ["社区约定", "先亮出自己，再去打招呼", "最多只能发 3 条", "自我介绍里不留联系方式", 'data-p="price_t1"', 'data-p="price_t2"', 'data-p="price_m_month"', 'data-p="price_m_year"', 'data-p="price_extra"', 'id="r3"', 'id="r8"']) and "参考了" not in rt)
check("rules has 交友规则", 'id="r8"' in guest.get("/rules").text and "交友" in guest.get("/rules").text and "18 岁" in guest.get("/rules").text)
check("rate api default", guest.get("/api/community/rate").json()["cny_krw"] > 0)
bd = guest.get("/api/community/boards").json()
check("boards api: categories + new boards", len(bd["categories"]) >= 5 and any(b["key"] == "pit" and b["name"] == "踩坑避雷" and b["desc"] for c in bd["categories"] for b in c["boards"]) and any(b["key"] == "rent" for c in bd["categories"] for b in c["boards"]) and bd["joined"] == [], bd)
check("board join needs login", guest.post("/api/community/boards/rent/join", json={}, headers=H).status_code == 401)
for t in ["recommend", "news", "hot", "latest"]:
    r = guest.get(f"/api/community/feed?tab={t}"); check(f"feed {t} 200", r.status_code == 200 and "posts" in r.json(), r.text)
check("feed follow guest -> login_required", guest.get("/api/community/feed?tab=follow").json().get("login_required") is True)
check("feed bad tab 400", guest.get("/api/community/feed?tab=xx").status_code == 400)
check("feed bad board 400", guest.get("/api/community/feed?tab=latest&boards=nope").status_code == 400)

# 管理员
r = admin.post("/api/admin/login", json={"password": "wrong-password-123"}, headers=H); check("admin wrong pw 401", r.status_code == 401)
r = admin.post("/api/admin/login", json={"password": ADMIN_PW}); check("admin login w/o csrf header 403", r.status_code == 403)
r = admin.post("/api/admin/login", json={"password": ADMIN_PW}, headers=H); check("admin login ok", r.status_code == 200, r.text)
ck = r.headers.get("set-cookie", ""); check("admin cookie httponly+samesite", "HttpOnly" in ck and "SameSite=strict" in ck, ck)
r = admin.post("/api/admin/settings", json={"ai_reply": "0"}, headers=H); check("disable AI for e2e", r.status_code == 200 and r.json()["settings"].get("ai_reply") == "0", r.text)

code = f"T{rnd % 10000}"
r = admin.post("/api/admin/promoters", json={"name": "测试推广员", "code": code.lower(), "commission": 3}, headers=H)
check("create promoter", r.status_code == 200 and r.json()["code"] == code, r.text)
r = admin.post("/api/admin/promoters", json={"name": "重复", "code": code}, headers=H); check("duplicate promoter 409", r.status_code == 409)
r = guest.get("/api/admin/users"); check("admin api blocked for guest", r.status_code == 401)

# 推广访问：首次来源 Cookie
r = alice.get(f"/?ref={code}"); check("ref visit sets cookie", "lh_ref" in alice.cookies, dict(alice.cookies))
alice.get(f"/tips?ref={code}")  # 同访客同日重复，不重复计数
r = alice.get("/?ref=OTHER1"); check("first-touch kept", alice.cookies.get("lh_ref") == code)
r = alice.get("/api/community/ref"); check("ref api returns cookie code", r.json().get("ref") == code, r.text)
guest2 = httpx.Client(base_url=BASE, headers={"user-agent": "another-device"})
guest2.get(f"/community?ref={code}")

# 注册
acct_a = f"1{random.choice('3456789')}{rnd}{rnd%1000:03d}"[:11]
r = alice.post("/api/community/register", json={"account": acct_a, "password": "short", "nickname": "爱丽丝", "agree": True}, headers=H)
check("weak password rejected", r.status_code == 400)
r = alice.post("/api/community/register", json={"account": acct_a, "password": "Passw0rd!xyz", "nickname": "爱丽丝", "agree": True, "promoter": "ZZZ"}, headers=H)
check("register by phone (cookie ref wins over manual)", r.status_code == 200, r.text)
ck = r.headers.get("set-cookie", ""); check("session cookie HttpOnly", "HttpOnly" in ck and "lh_sess" in ck)
r = alice.post("/api/community/register", json={"account": acct_a, "password": "Passw0rd!xyz", "nickname": "爱丽丝2", "agree": True}, headers=H)
check("duplicate account 409", r.status_code == 409)
r = bob.post("/api/community/register", json={"account": f"bob{rnd}@Example.com", "password": "Passw0rd!xyz", "nickname": "鲍勃", "agree": True, "promoter": "NOPE99"}, headers=H)
check("invalid manual promoter rejected", r.status_code == 400, r.text)
r = bob.post("/api/community/register", json={"account": f"bob{rnd}@Example.com", "password": "Passw0rd!xyz", "nickname": "鲍勃", "agree": True, "promoter": code.lower()}, headers=H)
check("register by email w/ manual promoter", r.status_code == 200, r.text)
r = carol.post("/api/community/register", json={"account": f"010-{rnd%10000:04d}-{rnd//100:04d}", "password": "Passw0rd!xyz", "nickname": "卡罗尔", "agree": True}, headers=H)
check("register korean phone, no promoter", r.status_code == 200, r.text)
me_a = alice.get("/api/community/me").json()["user"]; check("me works, account masked", me_a and "****" in me_a["account"], me_a)
bob.post("/api/community/logout", json={}, headers=H)
check("logout clears", bob.get("/api/community/me").json()["user"] is None)
r = bob.post("/api/community/login", json={"account": f"BOB{rnd}@example.com", "password": "wrongwrong"}, headers=H); check("login wrong pw 401", r.status_code == 401)
r = bob.post("/api/community/login", json={"account": f"BOB{rnd}@example.com", "password": "Passw0rd!xyz"}, headers=H); check("login ok (email case-insensitive)", r.status_code == 200, r.text)

# 免费用户：不能发帖/评论/点赞/举报
def tier_of(c): return c.get("/api/community/me").json()["user"]["tier"]
files = [("images", ("a.png", img_bytes(), "image/png"))]
r = alice.post("/api/community/posts", data={"board": "pit", "title": "明洞换钱踩坑", "body": "换钱所汇率看清楚，有的店写的是买入价。"}, files=files, headers=H)
check("free user cannot post 403", r.status_code == 403, r.text)
r = guest.post("/api/community/posts", data={"board": "pit", "title": "xxxx", "body": "xxxxxxxxxxxx"}, headers=H); check("guest cannot post", r.status_code in (401, 403))

# 管理员设置等级：alice=会员(1)，bob=高级(2)
users = admin.get("/api/admin/users", params={"s": "爱丽丝"}).json()["users"]
uid_a = users[0]["id"]; check("admin search user + promoter shown", users[0]["promoter"] == code, users)
uid_b = admin.get("/api/admin/users", params={"s": f"bob{rnd}"}).json()["users"][0]["id"]
uid_c = admin.get("/api/admin/users", params={"s": "卡罗尔"}).json()["users"][0]["id"]
r = admin.post(f"/api/admin/users/{uid_a}", json={"action": "set_tier", "tier": 3}, headers=H); check("invalid tier rejected", r.status_code == 400)
r = admin.post(f"/api/admin/users/{uid_a}", json={"action": "set_tier", "tier": 1}, headers=H); check("set tier 1", r.status_code == 200, r.text)
r = admin.post(f"/api/admin/users/{uid_b}", json={"action": "set_tier", "tier": 2}, headers=H); check("set tier 2", r.status_code == 200, r.text)
check("tiers visible in /me", tier_of(alice) == 1 and tier_of(bob) == 2 and tier_of(carol) == 0)
check("admin list shows tier", admin.get("/api/admin/users", params={"s": "爱丽丝"}).json()["users"][0]["tier"] == 1)
me_a = alice.get("/api/community/me").json()["user"]
check("me member_no defaults to id", me_a["member_no"] == me_a["id"] == uid_a, me_a)
au = admin.get("/api/admin/users", params={"s": "爱丽丝"}).json()["users"][0]
check("admin list returns member_no", au["member_no"] == uid_a and au["id"] == uid_a, au)
vanity = 800000 + (rnd % 100000)
r = admin.post(f"/api/admin/users/{uid_a}/member_no", json={"member_no": vanity})
check("set member_no needs csrf", r.status_code == 403)
r = admin.post(f"/api/admin/users/{uid_a}/member_no", json={"member_no": vanity}, headers=H)
check("admin sets vanity member_no", r.status_code == 200 and r.json().get("member_no") == vanity, r.text)
me_a = alice.get("/api/community/me").json()["user"]
check("me shows new member_no, id unchanged", me_a["member_no"] == vanity and me_a["id"] == uid_a, me_a)
found = admin.get("/api/admin/users", params={"s": str(vanity)}).json()["users"]
check("search by member_no", found and found[0]["id"] == uid_a and found[0]["member_no"] == vanity, found)
found_old = admin.get("/api/admin/users", params={"s": str(uid_a)}).json()["users"]
check("search by old id number misses this user", all(u["id"] != uid_a for u in found_old), found_old)
r = admin.post(f"/api/admin/users/{uid_b}/member_no", json={"member_no": vanity}, headers=H)
check("duplicate member_no rejected", r.status_code == 409, r.text)
r = admin.post(f"/api/admin/users/{uid_a}/member_no", json={"member_no": 0}, headers=H)
check("member_no must be positive", r.status_code == 400, r.text)
r = admin.post(f"/api/admin/users/{uid_a}/member_no", json={"member_no": -3}, headers=H)
check("member_no rejects negative", r.status_code == 400, r.text)
r = admin.post(f"/api/admin/users/{uid_a}/member_no", json={"member_no": vanity}, headers=H)
check("same member_no is idempotent", r.status_code == 200 and r.json().get("member_no") == vanity, r.text)
bots = [u for u in admin.get("/api/admin/users", params={"s": "旅韩小助手"}).json()["users"] if u.get("is_bot")]
check("admin can find bot by nickname", bool(bots), bots)
if bots:
    r = admin.post(f"/api/admin/users/{bots[0]['id']}/member_no", json={"member_no": 777777}, headers=H)
    check("cannot change bot member_no", r.status_code == 403, r.text)
r = guest.post(f"/api/admin/users/{uid_a}/member_no", json={"member_no": 1}, headers=H)
check("guest cannot set member_no", r.status_code == 401, r.text)
r = alice.post(f"/api/admin/users/{uid_a}/member_no", json={"member_no": 1}, headers=H)
check("non-admin cannot set member_no", r.status_code == 401, r.text)
# 新注册：若自增 id 已被别人占用为靓号，则 member_no 取下一个空号
if os.environ.get("DATABASE_URL"):
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"]) as cx:
        nxt = cx.execute("SELECT last_value + 1 FROM c_users_id_seq").fetchone()[0]
        cx.execute("UPDATE c_users SET member_no=%s WHERE id=%s", (nxt, uid_b))
        cx.commit()
    skip = httpx.Client(base_url=BASE)
    r = skip.post("/api/community/register", json={"account": f"skip{rnd}@example.com", "password": "Passw0rd!xyz", "nickname": "跳号用户", "agree": True}, headers=H)
    check("register when id taken as vanity", r.status_code == 200, r.text)
    me_s = skip.get("/api/community/me").json()["user"]
    check("new user skips taken member_no", me_s["id"] == nxt and me_s["member_no"] == nxt + 1, me_s)
    # 把鲍勃的编号改回，避免影响后续按编号搜索
    r = admin.post(f"/api/admin/users/{uid_b}/member_no", json={"member_no": uid_b}, headers=H)
    check("restore bob member_no", r.status_code == 200, r.text)


# 会员(1) 不能发帖
r = alice.post("/api/community/posts", data={"board": "pit", "title": "明洞换钱踩坑", "body": "换钱所汇率看清楚，有的店写的是买入价。"}, headers=H)
check("tier1 cannot post", r.status_code == 403 and "高级会员" in r.json()["detail"], r.text)
# 高级(2) 发帖（6 张以内，服务器压缩）
files = [("images", (f"{i}.png", img_bytes(color=(i * 30, 100, 150)), "image/png")) for i in range(3)]
r = bob.post("/api/community/posts", data={"board": "pit", "title": "明洞换钱踩坑", "body": "换钱所汇率看清楚，有的店写的是买入价。<script>alert(1)</script>"}, files=files, headers=H)
check("tier2 posts with 3 images", r.status_code == 200, r.text); pid = r.json().get("id")
files7 = [("images", (f"{i}.png", img_bytes(200, 200), "image/png")) for i in range(7)]
r = bob.post("/api/community/posts", data={"board": "pit", "title": "七张图测试", "body": "测试超过六张图片的限制"}, files=files7, headers=H)
check("7 images rejected", r.status_code == 400, r.text)
r = bob.post("/api/community/posts", data={"board": "nope", "title": "版块错误", "body": "测试错误版块的帖子"}, headers=H); check("bad board rejected", r.status_code == 400)
fimg = [("images", ("f.png", img_bytes(400, 300), "image/png"))]
r = bob.post("/api/community/posts", data={"board": "friends", "title": "找首尔饭搭子", "body": "下周在首尔，想找人一起吃烤肉。"}, files=fimg, headers=H)
check("tier2 posts in 交友", r.status_code == 200, r.text); pid_f = r.json().get("id")
r = bob.post("/api/community/posts", data={"board": "visa", "title": "五年多次签材料", "body": "我的材料清单，仅供参考，以签证中心为准。"}, headers=H); pid_v = r.json().get("id")

d = guest.get(f"/api/community/posts/{pid}").json()
check("guest can read post", d.get("title") == "明洞换钱踩坑" and len(d["images"]) == 3, d)
iid = d["images"][0]["id"]
im = guest.get(f"/api/community/img/{iid}"); th = guest.get(f"/api/community/img/{iid}?t=1")
w, h = Image.open(io.BytesIO(im.content)).size
check("image resized to <=1280 jpeg", im.headers["content-type"] == "image/jpeg" and max(w, h) <= 1280, (w, h))
check("thumb smaller", len(th.content) < len(im.content) and max(Image.open(io.BytesIO(th.content)).size) <= 480)
lst = guest.get("/api/community/posts").json()["posts"]
check("guest list hides 交友", all(p["board"] != "friends" for p in lst) and any(p["id"] == pid for p in lst))
check("free list hides 交友", all(p["board"] != "friends" for p in carol.get("/api/community/posts").json()["posts"]))
gb = guest.get("/api/community/posts?board=friends").json(); check("guest 交友 board: login+tier required", gb.get("login_required") is True and gb.get("tier_required") == 1, gb)
cb = carol.get("/api/community/posts?board=friends").json(); check("free 交友 board: tier required", cb.get("login_required") is False and cb.get("tier_required") == 1 and not cb["posts"], cb)
check("guest 交友 post 401", guest.get(f"/api/community/posts/{pid_f}").status_code == 401)
check("free 交友 post 403", carol.get(f"/api/community/posts/{pid_f}").status_code == 403)
fd = alice.get(f"/api/community/posts/{pid_f}").json(); check("tier1 can view 交友 post", fd.get("title") == "找首尔饭搭子" and fd["comment_tier"] == 2, fd)
fiid = fd["images"][0]["id"]
check("交友 image blocked for free/guest, ok for tier1", carol.get(f"/api/community/img/{fiid}").status_code == 403 and guest.get(f"/api/community/img/{fiid}").status_code == 403 and alice.get(f"/api/community/img/{fiid}").status_code == 200)
check("tier1 sees 交友 in list", any(p["id"] == pid_f for p in alice.get("/api/community/posts?board=friends").json()["posts"]) and any(p["id"] == pid_f for p in alice.get("/api/community/posts").json()["posts"]))
check("board filter", all(p["board"] == "visa" for p in guest.get("/api/community/posts?board=visa").json()["posts"]))
check("author tier in list", [p for p in lst if p["id"] == pid][0]["author_tier"] == 2)
rowp = [p for p in lst if p["id"] == pid][0]
check("list includes author_id and has_avatar", rowp.get("author_id") == uid_b and rowp.get("has_avatar") is False, rowp)

# 头像：上传、拒绝、列表展示、管理员移除、页面回退
r = bob.post("/api/community/avatar", files={"image": ("a.png", img_bytes(400, 200), "image/png")})
check("e2e avatar needs csrf", r.status_code == 403)
r = guest.post("/api/community/avatar", files={"image": ("a.png", img_bytes(200, 200), "image/png")}, headers=H)
check("e2e guest cannot upload avatar", r.status_code == 401)
r = bob.post("/api/community/avatar", files={"image": ("a.png", img_bytes(400, 200, (30, 160, 90)), "image/png")}, headers=H)
check("e2e bob uploads avatar", r.status_code == 200 and r.json().get("has_avatar") is True, r.text)
check("e2e me has_avatar", bob.get("/api/community/me").json()["user"]["has_avatar"] is True)
av = guest.get(f"/api/community/avatar/{uid_b}")
imav = Image.open(io.BytesIO(av.content))
check("e2e public can read avatar jpeg 256", av.status_code == 200 and av.headers["content-type"].startswith("image/jpeg") and imav.size == (256, 256), (av.status_code, imav.size))
lst2 = guest.get("/api/community/posts").json()["posts"]
check("e2e list shows has_avatar", [p for p in lst2 if p["id"] == pid][0]["has_avatar"] is True)
gifb = io.BytesIO(); Image.new("RGB", (32, 32), (1, 2, 3)).save(gifb, "GIF")
r = bob.post("/api/community/avatar", files={"image": ("x.gif", gifb.getvalue(), "image/gif")}, headers=H)
check("e2e gif avatar rejected", r.status_code == 400, r.text)
r = bob.post("/api/community/avatar", files={"image": ("big.jpg", b"x" * (5 * 1024 * 1024 + 8), "image/jpeg")}, headers=H)
check("e2e oversized avatar rejected", r.status_code == 413, r.text)
bots = [u for u in admin.get("/api/admin/users").json()["users"] if u.get("is_bot")]
if bots:
    bav = guest.get(f"/api/community/avatar/{bots[0]['id']}")
    check("e2e bot default avatar", bav.status_code == 200 and bav.headers["content-type"].startswith("image/jpeg"), bav.status_code)
    r = admin.post(f"/api/admin/users/{bots[0]['id']}/clear_avatar", json={}, headers=H)
    check("e2e cannot clear bot avatar", r.status_code == 403, r.text)
r = admin.post(f"/api/admin/users/{uid_b}/clear_avatar", json={})
check("e2e admin clear avatar needs csrf", r.status_code == 403)
r = admin.post(f"/api/admin/users/{uid_b}/clear_avatar", json={}, headers=H)
check("e2e admin removes avatar", r.status_code == 200, r.text)
check("e2e avatar fallback 404 after removal", guest.get(f"/api/community/avatar/{uid_b}").status_code == 404)
# 再传回去，便于帖子页展示
bob.post("/api/community/avatar", files={"image": ("a.png", img_bytes(200, 200, (80, 80, 200)), "image/png")}, headers=H)
pt = guest.get(f"/community/post/{pid}").text
check("post page SSR + seo", "明洞换钱踩坑" in pt and "<h1>" in pt and 'rel="canonical"' in pt and "application/ld+json" in pt)
cht = guest.get("/community").text
check("community list SSR includes post title", "明洞换钱踩坑" in cht)
sm2 = guest.get("/sitemap.xml").text
check("sitemap includes public post, hides 交友 post", f"/community/post/{pid}" in sm2 and f"/community/post/{pid_f}" not in sm2, sm2[-400:])

# 评论、点赞、举报
r = carol.post(f"/api/community/posts/{pid}/comments", json={"body": "有用！"}, headers=H); check("free cannot comment", r.status_code == 403)
r = carol.post(f"/api/community/posts/{pid}/like", json={}, headers=H); check("free cannot like", r.status_code == 403)
r = carol.post("/api/community/report", json={"type": "post", "id": pid, "reason": "x"}, headers=H); check("free cannot report", r.status_code == 403)
r = alice.post(f"/api/community/posts/{pid}/comments", json={"body": "感谢分享，我也遇到过"}, headers=H); check("tier1 comments (non-交友)", r.status_code == 200, r.text); cid = r.json()["id"]
r = alice.post(f"/api/community/posts/{pid_f}/comments", json={"body": "我也想去"}, headers=H); check("tier1 cannot comment in 交友", r.status_code == 403 and "高级" in r.json()["detail"], r.text)
r = bob.post(f"/api/community/posts/{pid_f}/comments", json={"body": "可以一起"}, headers=H); check("tier2 comments in 交友", r.status_code == 200, r.text)
r = alice.post(f"/api/community/posts/{pid}/like", json={}, headers=H); check("tier1 likes", r.status_code == 200 and r.json()["liked"] and r.json()["likes"] == 1, r.text)
r = alice.post(f"/api/community/posts/{pid}/like", json={}, headers=H); check("unlike toggles", r.json()["liked"] is False and r.json()["likes"] == 0)
r = alice.post("/api/community/report", json={"type": "comment", "id": cid, "reason": "测试举报"}, headers=H); check("tier1 report comment", r.status_code == 200)
r = alice.post("/api/community/report", json={"type": "post", "id": pid_v, "reason": "疑似广告"}, headers=H); check("tier1 report post", r.status_code == 200)
r = guest.post("/api/community/report", json={"type": "post", "id": pid, "reason": "x"}, headers=H); check("guest report needs login", r.status_code == 401)
d = guest.get(f"/api/community/posts/{pid}").json(); check("comment count 1", d["comments"] == 1 and len(d["comment_list"]) == 1 and d["comment_list"][0]["author_tier"] == 1)
check("body returned raw (escaped client-side)", "<script>" in d["body"])

# 管理员：举报、删评论、删帖、封号、重置密码
reps = admin.get("/api/admin/reports").json()["reports"]; check("admin sees 2 reports", len([x for x in reps if x["target_id"] in (cid, pid_v)]) == 2, reps)
r = admin.delete(f"/api/admin/comments/{cid}", headers=H); check("admin delete comment", r.status_code == 200)
d = guest.get(f"/api/community/posts/{pid}").json(); check("comment gone, count 0", d["comments"] == 0 and not d["comment_list"])
r = admin.delete(f"/api/admin/posts/{pid_v}", headers=H); check("admin delete post", r.status_code == 200)
check("deleted post 404", guest.get(f"/api/community/posts/{pid_v}").status_code == 404)
check("reports resolved", not [x for x in admin.get("/api/admin/reports").json()["reports"] if x["target_id"] in (cid, pid_v)])
r = admin.post(f"/api/admin/users/{uid_c}", json={"action": "ban"}, headers=H); check("ban user", r.status_code == 200)
check("banned session killed", carol.get("/api/community/me").json()["user"] is None)
r = carol.post("/api/community/login", json={"account": f"010-{rnd%10000:04d}-{rnd//100:04d}", "password": "Passw0rd!xyz"}, headers=H); check("banned cannot login", r.status_code == 403, r.text)
admin.post(f"/api/admin/users/{uid_c}", json={"action": "unban"}, headers=H)
r = admin.post(f"/api/admin/users/{uid_c}", json={"action": "reset_pw"}, headers=H); tmp = r.json().get("temp_password")
r = carol.post("/api/community/login", json={"account": f"+8210{(rnd%10000):04d}{rnd//100:04d}", "password": tmp}, headers=H); check("reset pw + login w/ normalized phone", r.status_code == 200, r.text)
r = alice.delete(f"/api/community/posts/{pid_f}", headers=H); check("user can't delete others' post", r.status_code == 404)

# 推广统计：佣金改为实付 50%。alice 实付 9.9→4.95，bob 实付 19.9→9.95，alice 升级再付 10→5，合计 19.90
def prm(**kw): return [p for p in admin.get("/api/admin/promoters", params=kw).json()["promoters"] if p["code"] == code][0]
pr = prm()
check("default commissions 3/6 kept as legacy fields", pr["commission"] == 3 and pr["commission2"] == 6, pr)
check("promoter visits=2 (deduped)", pr["visits"] == 2, pr)
check("promoter regs=2", pr["regs"] == 2, pr)
r = admin.post("/api/admin/promo/settings", json={"hold_days": 0}, headers=H)
check("hold days 0 for tests", r.status_code == 200, r.text)
r = admin.post(f"/api/admin/users/{uid_a}", json={"action": "confirm_payment", "kind": "t1", "amount": 9.9}, headers=H)
check("confirm alice t1 = 4.95", r.status_code == 200 and (r.json().get("commission") or {}).get("amount") == 4.95, r.text)
r = admin.post(f"/api/admin/users/{uid_b}", json={"action": "confirm_payment", "kind": "t2", "amount": 19.9}, headers=H)
check("confirm bob t2 = 9.95", r.status_code == 200 and (r.json().get("commission") or {}).get("amount") == 9.95, r.text)
pr = prm()
check("paid1=1 paid2=1 owed ¥14.90", pr["paid1"] == 1 and pr["paid2"] == 1 and pr["owed"] == 14.9, pr)
r = admin.post(f"/api/admin/users/{uid_a}", json={"action": "confirm_payment", "kind": "t2", "amount": 10}, headers=H)
pr = prm(); check("upgrade payment +5 (owed ¥19.90, paid2=2)", pr["owed"] == 19.9 and pr["paid2"] == 2 and pr["paid1"] == 1, pr)
admin.post(f"/api/admin/users/{uid_a}", json={"action": "set_tier", "tier": 1}, headers=H)
admin.post(f"/api/admin/users/{uid_a}", json={"action": "set_tier", "tier": 2}, headers=H)
admin.post(f"/api/admin/users/{uid_a}", json={"action": "set_tier", "tier": 0}, headers=H)
pr = prm(); check("down/up toggles don't add commission", pr["owed"] == 19.9 and pr["unsettled_members"] == 3, pr)
check("tier 0 removes access", tier_of(alice) == 0 and alice.get(f"/api/community/posts/{pid_f}").status_code == 403)
pr2 = prm(start="2020-01-01", end="2020-12-31")
check("date filter excludes", pr2["visits"] == 0 and pr2["regs"] == 0 and pr2["paid"] == 0 and pr2["commission_in_range"] == 0 and pr2["owed"] == 19.9, pr2)
r = admin.post(f"/api/admin/promoters/{code}", json={"action": "commission", "commission": "5", "commission2": "8"}, headers=H); check("change commissions", r.status_code == 200)
pr = prm(); check("legacy rates updated, past rows unchanged", pr["commission"] == 5 and pr["commission2"] == 8 and pr["owed"] == 19.9, pr)
r = admin.post(f"/api/admin/promoters/{code}", json={"action": "settle"}, headers=H); check("settle ¥19.90 (3 rows)", r.json() == {"ok": True, "members": 3, "amount": 19.9}, r.text)
pr = prm(); check("after settle owed 0, settled 19.90", pr["owed"] == 0 and pr["settled_total"] == 19.9, pr)
check("settlement recorded", any(s["code"] == code for s in admin.get("/api/admin/settlements").json()["settlements"]))
r = admin.post(f"/api/admin/promoters/{code}", json={"action": "disable"}, headers=H)
x = httpx.Client(base_url=BASE, headers={"user-agent": "x3"}); x.get(f"/?ref={code}"); check("disabled code sets no cookie", "lh_ref" not in x.cookies)
r = x.post("/api/community/register", json={"account": f"x{rnd}@ex.com", "password": "Passw0rd!xyz", "nickname": "某某", "agree": True, "promoter": code}, headers=H)
check("disabled code rejected manually", r.status_code == 400)

# ---------- 价格设置 ----------
check("public prices default", guest.get("/api/community/prices").json() == {"price_t1": "9.9", "price_t2": "19.9", "price_m_month": "99", "price_m_year": "999", "price_extra": "19.9", "rate_cny_krw": "195"})
r = admin.post("/api/admin/settings", json={"price_t1": "12.90"}, headers=H); check("admin set price", r.status_code == 200 and r.json()["settings"]["price_t1"] == "12.9", r.text)
check("price reflected publicly", guest.get("/api/community/prices").json()["price_t1"] == "12.9")
r = alice.post(f"/api/community/posts/{pid}/like", json={}, headers=H); check("error text uses configured price", r.status_code == 403 and "12.9" in r.json()["detail"], r.text)
r = admin.post("/api/admin/settings", json={"price_t1": "abc"}, headers=H); check("bad price rejected", r.status_code == 400)
admin.post("/api/admin/settings", json={"price_t1": "9.9"}, headers=H)
check("settings admin-only", guest.post("/api/admin/settings", json={"price_t1": "1"}, headers=H).status_code == 401)
check("rules page", all(w in guest.get("/rules").text for w in ["诈骗", "代办签证", "违法医疗广告", "赌博", "不退", "商家信息免责声明"]))
check("register & post pages link rules", 'href="/rules"' in guest.get("/login").text and 'href="/rules"' in guest.get("/community/new").text and 'href="/rules"' in guest.get("/community").text)

# ---------- 商业关键词审核 ----------
eve = httpx.Client(base_url=BASE)
r = eve.post("/api/community/register", json={"account": f"eve{rnd}@ex.com", "password": "Passw0rd!xyz", "nickname": "伊芙", "agree": True}, headers=H)
uid_e = admin.get("/api/admin/users", params={"s": f"eve{rnd}"}).json()["users"][0]["id"]
admin.post(f"/api/admin/users/{uid_e}", json={"action": "set_tier", "tier": 2}, headers=H)
r = eve.post("/api/community/posts", data={"board": "pit", "title": "首尔包车一日游", "body": "提供首尔包车服务，价格优惠，加微信号 abc12345 咨询"}, files=[("images", ("x.png", img_bytes(300, 300), "image/png"))], headers=H)
j = r.json(); pid_pend = j.get("id"); check("commercial post held pending", r.status_code == 200 and j.get("pending") and "商家" in j.get("message", ""), r.text)
check("pending hidden from guest", guest.get(f"/api/community/posts/{pid_pend}").status_code == 404)
check("pending not in list", all(p["id"] != pid_pend for p in alice.get("/api/community/posts").json()["posts"]))
dd = eve.get(f"/api/community/posts/{pid_pend}").json(); check("author sees own pending", dd.get("status") == "pending" and "关键词" in (dd.get("review_note") or ""), dd)
pimg = dd["images"][0]["id"]; check("pending image author-only", eve.get(f"/api/community/img/{pimg}").status_code == 200 and guest.get(f"/api/community/img/{pimg}").status_code == 404)
check("cannot comment on pending", bob.post(f"/api/community/posts/{pid_pend}/comments", json={"body": "?"}, headers=H).status_code == 404)
pend = admin.get("/api/admin/pending").json()["posts"]; check("admin review queue", any(p["id"] == pid_pend and p["images"] for p in pend), pend)
check("admin thumb", admin.get(f"/api/admin/img/{pimg}").status_code == 200)
r = admin.post(f"/api/admin/posts/{pid_pend}/review", json={"action": "approve"}, headers=H); check("approve", r.status_code == 200)
check("approved visible", guest.get(f"/api/community/posts/{pid_pend}").status_code == 200)
r = eve.post("/api/community/posts", data={"board": "visa", "title": "签证代办包过", "body": "专业代办各类签证，加V详聊"}, headers=H); pid_rej = r.json()["id"]
check("代办 post pending", r.json().get("pending") is True)
r = admin.post(f"/api/admin/posts/{pid_rej}/review", json={"action": "reject", "reason": "签证代办广告"}, headers=H); check("reject", r.status_code == 200)
check("rejected hidden", guest.get(f"/api/community/posts/{pid_rej}").status_code == 404)
mp = {p["id"]: p for p in eve.get("/api/community/my-posts").json()["posts"]}
check("my-posts shows statuses", mp[pid_rej]["status"] == "rejected" and mp[pid_rej]["note"] == "签证代办广告" and mp[pid_pend]["status"] == "published", mp)
r = eve.post("/api/community/posts", data={"board": "pit", "title": "景福宫门票踩坑", "body": "周二闭馆，门票价格 3000 韩元，去之前一定查清楚开放时间。"}, headers=H)
check("normal post with 价格 not held", r.status_code == 200 and not r.json().get("pending"), r.text)

# ---------- 商家会员 ----------
code2 = f"M{rnd % 10000}"
admin.post("/api/admin/promoters", json={"name": "商家推广", "code": code2}, headers=H)
pm = prm_c = [p for p in admin.get("/api/admin/promoters").json()["promoters"] if p["code"] == code2][0]
check("merchant commission defaults 20/200", pm["commission3"] == 20 and pm["commission4"] == 200, pm)
dave = httpx.Client(base_url=BASE)
r = dave.post("/api/community/register", json={"account": f"dave{rnd}@ex.com", "password": "Passw0rd!xyz", "nickname": "明洞酒店", "agree": True, "promoter": code2}, headers=H)
check("merchant registers", r.status_code == 200, r.text)
uid_d = admin.get("/api/admin/users", params={"s": f"dave{rnd}"}).json()["users"][0]["id"]
r = dave.post("/api/community/posts", data={"board": "hotel", "title": "明洞酒店特价", "body": "明洞站步行三分钟，双人间每晚价格 8 万韩元。"}, headers=H)
check("non-merchant cannot post hotel", r.status_code == 403, r.text)
r = eve.post("/api/community/posts", data={"board": "car", "title": "包车服务介绍", "body": "首尔市内包车一天，价格面议。"}, headers=H)
check("tier2 non-merchant cannot post merchant board", r.status_code == 403 and "商家会员" in r.json()["detail"], r.text)
r = admin.post(f"/api/admin/users/{uid_d}", json={"action": "merchant", "plan": "week"}, headers=H); check("bad plan rejected", r.status_code == 400)
r = admin.post(f"/api/admin/users/{uid_d}", json={"action": "merchant", "plan": "month"}, headers=H); check("activate merchant month", r.status_code == 200 and r.json().get("merchant_until"), r.text)
me_d = dave.get("/api/community/me").json()["user"]
check("merchant /me", me_d["merchant"] and me_d["display_tier"] == 3 and me_d["tier"] == 2 and me_d["merchant_info"]["left"] == 10 and me_d["merchant_info"]["plan"] == "month", me_d)
until1 = me_d["merchant_info"]["until"]
r = dave.post("/api/community/posts", data={"board": "hotel", "title": "明洞酒店特价", "body": "明洞站步行三分钟，双人间每晚价格 8 万韩元，微信号 hotel888。"}, headers=H)
check("merchant posts hotel (no review)", r.status_code == 200 and not r.json().get("pending"), r.text); pid_h = r.json()["id"]
r = dave.post("/api/community/posts", data={"board": "pit", "title": "明洞住宿小贴士", "body": "订房前确认是否含税，价格以前台为准。"}, headers=H)
check("merchant normal-board post not held", r.status_code == 200 and not r.json().get("pending"), r.text)
hp = guest.get(f"/api/community/posts/{pid_h}").json(); check("merchant label + badge", hp["merchant"] is True and hp["author_tier"] == 3, hp)
check("hotel board list", any(p["id"] == pid_h and p["merchant"] for p in guest.get("/api/community/posts?board=hotel").json()["posts"]))
check("quota 9 left", dave.get("/api/community/me").json()["user"]["merchant_info"]["left"] == 9)
import psycopg
with psycopg.connect(os.environ["DATABASE_URL"]) as pc:
    for i in range(9):
        pc.execute("INSERT INTO c_posts(user_id,board,title,body,is_merchant,quota_kind,created_at) VALUES (%s,'hotel','额度测试','额度测试正文内容',TRUE,'quota',now())", (uid_d,))
r = dave.post("/api/community/posts", data={"board": "clinic", "title": "皮肤科介绍", "body": "江南区合法皮肤科诊所，提供中文咨询。"}, headers=H)
check("quota exhausted -> 403 with extra price", r.status_code == 403 and "额度已用完" in r.json()["detail"] and "19.9" in r.json()["detail"], r.text)
r = admin.post(f"/api/admin/users/{uid_d}", json={"action": "credits", "n": 2}, headers=H); check("grant 2 credits", r.json().get("credits") == 2, r.text)
r = dave.post("/api/community/posts", data={"board": "clinic", "title": "皮肤科介绍", "body": "江南区合法皮肤科诊所，提供中文咨询。"}, headers=H)
check("post using credit", r.status_code == 200, r.text)
mi = dave.get("/api/community/me").json()["user"]["merchant_info"]; check("left 0 credits 1", mi["left"] == 0 and mi["credits"] == 1, mi)
r = admin.post(f"/api/admin/users/{uid_d}", json={"action": "merchant", "plan": "year"}, headers=H); check("renew with annual", r.status_code == 200)
mi = dave.get("/api/community/me").json()["user"]["merchant_info"]
check("annual renewal extends expiry", mi["until"] > until1, (until1, mi))
check("renewal keeps cycle quota", mi["left"] == 0 and mi["plan"] == "year", mi)
au = admin.get("/api/admin/users", params={"s": f"dave{rnd}"}).json()["users"][0]
check("admin sees merchant fields", au["merchant_active"] and au["merchant_plan"] == "year" and au["merchant_credits"] == 1, au)
# 到期
with psycopg.connect(os.environ["DATABASE_URL"]) as pc:
    pc.execute("UPDATE c_users SET merchant_until=now()-interval '1 second' WHERE id=%s", (uid_d,))
me_d = dave.get("/api/community/me").json()["user"]
check("expired merchant", not me_d["merchant"] and me_d["merchant_expired"] and me_d["display_tier"] == 0, me_d)
check("expired cannot post", dave.post("/api/community/posts", data={"board": "hotel", "title": "到期测试帖", "body": "到期后不能再发商家帖子"}, headers=H).status_code == 403)
check("old merchant post keeps 商家 label", guest.get(f"/api/community/posts/{pid_h}").json()["merchant"] is True)
r = admin.post(f"/api/admin/users/{uid_d}", json={"action": "merchant", "plan": "month"}, headers=H)
mi = dave.get("/api/community/me").json()["user"]["merchant_info"]; check("reactivation starts new cycle (10 left)", mi and mi["left"] == 10, mi)
r = admin.post(f"/api/admin/users/{uid_d}", json={"action": "merchant_off"}, headers=H)
check("merchant_off", dave.get("/api/community/me").json()["user"]["merchant"] is False)
# 三次已开通的商家付费按实付 50% 补记佣金（不再重复开通权益）：99+999+99 → 49.5+499.5+49.5 = 598.5
for kind, amt in (("m_month", 99), ("m_year", 999), ("m_month", 99)):
    r = admin.post(f"/api/admin/users/{uid_d}", json={"action": "confirm_payment", "kind": kind, "amount": amt, "apply_benefit": False}, headers=H)
    check("record merchant payment %s" % kind, r.status_code == 200 and r.json().get("commission"), r.text)
pm = [p for p in admin.get("/api/admin/promoters").json()["promoters"] if p["code"] == code2][0]
check("merchant commissions month+year+month = ¥598.5", pm["paid3"] == 2 and pm["paid4"] == 1 and pm["owed"] == 598.5, pm)
r = admin.post(f"/api/admin/promoters/{code2}", json={"action": "commission", "commission3": "30", "commission4": "300"}, headers=H)
pm = [p for p in admin.get("/api/admin/promoters").json()["promoters"] if p["code"] == code2][0]
check("merchant commission configurable", pm["commission3"] == 30 and pm["commission4"] == 300 and pm["commission"] == 3, pm)
ov = admin.get("/api/admin/overview").json(); check("overview has tiers", ov["users"] >= 3 and "premium" in ov and "merchants" in ov and "pending" in ov and "db_mb" in ov, ov)

# ---------- 第三期：首页信息流 / 关注 / 版块加入 / 汇率 ----------
def reg(nick):
    c = httpx.Client(base_url=BASE)
    acct = f"{nick.lower()}{rnd}@test.example"
    r = c.post("/api/community/register", json={"account": acct, "password": "Passw0rd!xyz", "nickname": nick, "agree": True}, headers=H)
    assert r.status_code == 200, r.text
    uid = admin.get("/api/admin/users", params={"s": acct}).json()["users"][0]["id"]
    return c, uid
def tier(uid, t): admin.post(f"/api/admin/users/{uid}", json={"action": "set_tier", "tier": t}, headers=H)
def jpeg_with_gps():
    im = Image.new("RGB", (900, 1200), (120, 160, 200)); ex = Image.Exif()
    ex[0x010F] = "TestCam"; ex[0x8825] = {1: "N", 2: (37.0, 33.0, 0.0), 3: "E", 4: (126.0, 58.0, 0.0)}
    b = io.BytesIO(); im.save(b, "JPEG", exif=ex.tobytes()); return b.getvalue()

gina, uid_g = reg("Gina"); tier(uid_g, 2)
hank, uid_hk = reg("Hank"); tier(uid_hk, 1)
r = gina.post("/api/community/posts", data={"board": "news", "title": "首尔地铁新线路开通", "body": "据首尔市官网消息，新线路本周开通。"}, headers=H); pid_news = r.json().get("id")
check("tier2 posts in 资讯", r.status_code == 200, r.text)
check("资讯 tab contains news post", any(p["id"] == pid_news for p in guest.get("/api/community/feed?tab=news").json()["posts"]))
check("最新 tab newest first", guest.get("/api/community/feed?tab=latest").json()["posts"][0]["id"] == pid_news)
check("推荐 tab non-empty", len(guest.get("/api/community/feed?tab=recommend").json()["posts"]) > 0)
hank.post(f"/api/community/posts/{pid_news}/like", json={}, headers=H)
hot = guest.get("/api/community/feed?tab=hot&limit_n=5").json()
check("24h 热门 ranks liked post first", hot["posts"] and hot["posts"][0]["id"] == pid_news, hot)
check("study+friends section filter (guest hides 交友)", all(p["board"] == "study" for p in guest.get("/api/community/feed?tab=latest&boards=study,friends").json()["posts"]))
check("follow tab empty at first", hank.get("/api/community/feed?tab=follow").json()["posts"] == [])
r = hank.post(f"/api/community/follow/{uid_g}", json={}, headers=H); check("follow user", r.status_code == 200 and r.json()["following"] is True, r.text)
check("follow self rejected", hank.post(f"/api/community/follow/{uid_hk}", json={}, headers=H).status_code == 400)
check("follow tab shows followed user's posts", any(p["id"] == pid_news for p in hank.get("/api/community/feed?tab=follow").json()["posts"]))
check("post detail has author_id/following", hank.get(f"/api/community/posts/{pid_news}").json().get("following") is True)
r = hank.post("/api/community/boards/rent/join", json={}, headers=H); check("join board", r.json().get("joined") is True, r.text)
check("joined board listed", "rent" in hank.get("/api/community/boards").json()["joined"])
ivy, uid_i = reg("Ivy"); tier(uid_i, 2)
r = ivy.post("/api/community/posts", data={"board": "rent", "title": "弘大附近求合租", "body": "两室一厅找室友，月租平摊。"}, headers=H)
pid_rent = r.json().get("id")
check("follow tab includes joined board posts", r.status_code == 200 and any(p["id"] == pid_rent for p in hank.get("/api/community/feed?tab=follow").json()["posts"]), r.text)
hank.post("/api/community/boards/rent/join", json={}, headers=H); check("leave board", "rent" not in hank.get("/api/community/boards").json()["joined"])
r = admin.post("/api/admin/settings", json={"rate_cny_krw": "190.5"}, headers=H); check("admin sets rate", r.status_code == 200, r.text)
check("rate api uses admin value", guest.get("/api/community/rate").json()["cny_krw"] == 190.5)

# ---------- 第三期：交友 / 相亲 ----------
mike, uid_m = reg("Mike"); tier(uid_m, 2)
fay, uid_f = reg("Fay"); tier(uid_f, 2)
vic, uid_v = reg("Vic"); tier(uid_v, 1)
zed, uid_z = reg("Zed")    # 免费
PF = {"nickname": "小芳", "gender": "f", "age": 27, "height": 165, "city": "首尔", "area": "弘大", "visa": "留学签证", "purpose": "date",
      "intro": "在首尔读研，喜欢咖啡和爬山，想认识靠谱的朋友。", "visible": True}
check("tier1 cannot create profile", vic.post("/api/friends/me", json=PF, headers=H).status_code == 403)
check("free cannot create profile", zed.post("/api/friends/me", json=PF, headers=H).status_code == 403)
check("profile needs csrf", fay.post("/api/friends/me", json=PF).status_code == 403)
check("minor rejected", fay.post("/api/friends/me", json={**PF, "age": 17}, headers=H).status_code == 400)
check("bad gender/purpose/city rejected", all(fay.post("/api/friends/me", json={**PF, k: "x"}, headers=H).status_code == 400 for k in ["gender", "purpose", "city"]))
r = fay.post("/api/friends/me", json={**PF, "intro": "加我微信号：abc_123456 聊聊天交朋友"}, headers=H); check("contact in intro rejected", r.status_code == 400 and "联系方式" in r.json()["detail"], r.text)
check("scam words in intro rejected", fay.post("/api/friends/me", json={**PF, "intro": "带你一起投资虚拟币，稳赚不赔的项目"}, headers=H).status_code == 400)
check("photo before profile rejected", fay.post("/api/friends/me/photos", files={"image": ("a.jpg", jpeg_with_gps(), "image/jpeg")}, headers=H).status_code == 400)
r = fay.post("/api/friends/me", json=PF, headers=H); check("tier2 creates profile", r.status_code == 200, r.text)
r = fay.post("/api/friends/me/photos", files={"image": ("a.jpg", jpeg_with_gps(), "image/jpeg")}, headers=H); ph1 = r.json().get("id")
check("upload profile photo", r.status_code == 200, r.text)
raw = fay.get(f"/api/friends/photo/{ph1}").content; im = Image.open(io.BytesIO(raw))
check("profile photo: EXIF/GPS stripped, resized", len(im.getexif()) == 0 and max(im.size) <= 1280 and im.format == "JPEG", dict(im.getexif()))
for i in range(5):
    fay.post("/api/friends/me/photos", files={"image": (f"{i}.png", img_bytes(300, 400, (i * 40, 90, 90)), "image/png")}, headers=H)
r = fay.post("/api/friends/me/photos", files={"image": ("7.png", img_bytes(300, 400), "image/png")}, headers=H); check("7th profile photo rejected", r.status_code == 400, r.text)
myp = fay.get("/api/friends/me").json()["profile"]; check("my profile has 6 photos", len(myp["photos"]) == 6 and myp["cover"] == ph1, myp)
r = fay.post(f"/api/friends/me/photos/{myp['photos'][2]}/cover", json={}, headers=H); check("set cover", fay.get("/api/friends/me").json()["profile"]["cover"] == myp["photos"][2])
check("other user cannot delete my photo", mike.delete(f"/api/friends/me/photos/{ph1}", headers=H).status_code == 404)
check("delete own photo", fay.delete(f"/api/friends/me/photos/{ph1}", headers=H).status_code == 200 and len(fay.get("/api/friends/me").json()["profile"]["photos"]) == 5)
cover = fay.get("/api/friends/me").json()["profile"]["cover"]

gl = guest.get("/api/friends/list").json()
gcard = [c for c in gl["cards"] if c.get("blur") == cover]
check("guest list locked + blurred, no uid", gl["locked"] and gl["login_required"] and gcard and "uid" not in gcard[0] and gcard[0]["nickname"].endswith("**"), gl)
b = guest.get(f"/api/friends/photo/{cover}?blur=1"); check("blur photo public + small", b.status_code == 200 and max(Image.open(io.BytesIO(b.content)).size) <= 240)
check("guest/free cannot load clear photo", guest.get(f"/api/friends/photo/{cover}").status_code == 403 and zed.get(f"/api/friends/photo/{cover}?t=1").status_code == 403)
check("free list locked", zed.get("/api/friends/list").json()["locked"] is True)
vl = vic.get("/api/friends/list").json(); check("tier1 list unlocked with uid", not vl["locked"] and any(c.get("uid") == uid_f for c in vl["cards"]), vl)
check("tier1 can load clear photo", vic.get(f"/api/friends/photo/{cover}").status_code == 200)
check("filter gender=m excludes", all(c["uid"] != uid_f for c in vic.get("/api/friends/list?gender=m").json()["cards"]))
check("filter age range", any(c["uid"] == uid_f for c in vic.get("/api/friends/list?age_min=26&age_max=30").json()["cards"]) and all(c["uid"] != uid_f for c in vic.get("/api/friends/list?age_min=31&age_max=35").json()["cards"]))
check("filter city + purpose", any(c["uid"] == uid_f for c in vic.get("/api/friends/list?city=首尔&purpose=date").json()["cards"]) and all(c["uid"] != uid_f for c in vic.get("/api/friends/list?city=釜山").json()["cards"]))
check("profile detail: guest 401 / free 403", guest.get(f"/api/friends/profile/{uid_f}").status_code == 401 and zed.get(f"/api/friends/profile/{uid_f}").status_code == 403)
pd = vic.get(f"/api/friends/profile/{uid_f}").json(); check("tier1 views detail, cannot message", pd.get("nickname") == "小芳" and pd["can_message"] is False and pd["visa"] == "留学签证", pd)
check("tier1 cannot greet", vic.post(f"/api/friends/greet/{uid_f}", json={}, headers=H).status_code == 403)
r = mike.post(f"/api/friends/greet/{uid_f}", json={}, headers=H); check("greet requires own profile", r.status_code == 400 and "资料" in r.json()["detail"], r.text)
mike.post("/api/friends/me", json={**PF, "nickname": "阿明", "gender": "m", "age": 30, "city": "仁川", "purpose": "friend", "intro": "在仁川工作，周末喜欢去首尔逛展。"}, headers=H)
check("greet self rejected", mike.post(f"/api/friends/greet/{uid_m}", json={}, headers=H).status_code == 400)
r = mike.post(f"/api/friends/greet/{uid_f}", json={}, headers=H); tid = r.json().get("thread_id")
check("tier2 greets", r.status_code == 200 and tid and r.json()["warning"] is None, r.text)
check("repeat greet same day 429", mike.post(f"/api/friends/greet/{uid_f}", json={}, headers=H).status_code == 429)
me_f = fay.get("/api/community/me").json()["user"]; check("unread count in /me", me_f["unread"] == 1 and me_f["has_profile"] is True, me_f)
check("unread api", fay.get("/api/friends/unread").json()["unread"] == 1)
th = fay.get("/api/friends/threads").json(); check("inbox lists thread", th["threads"] and th["threads"][0]["id"] == tid and th["threads"][0]["unread"] == 1 and th["threads"][0]["nickname"] == "阿明", th)
tv = fay.get(f"/api/friends/threads/{tid}").json(); check("thread view: greet message", tv["messages"][0]["kind"] == "greet" and not tv["messages"][0]["mine"] and tv["can_send"], tv)
check("reading clears unread", fay.get("/api/friends/unread").json()["unread"] == 0)
check("outsider cannot read thread", vic.get(f"/api/friends/threads/{tid}").status_code == 404 and guest.get(f"/api/friends/threads/{tid}").status_code == 401)
r1 = mike.post(f"/api/friends/threads/{tid}/messages", json={"body": "你好呀"}, headers=H)
r2 = mike.post(f"/api/friends/threads/{tid}/messages", json={"body": "周末有空吗"}, headers=H)
r3 = mike.post(f"/api/friends/threads/{tid}/messages", json={"body": "在吗"}, headers=H)
check("max 3 unanswered then 403", r1.status_code == 200 and r2.status_code == 200 and r3.status_code == 403 and "回复前" in r3.json()["detail"], r3.text)
r = fay.post(f"/api/friends/threads/{tid}/messages", json={"body": "你好，你能先用微信转账给我吗？我在做USDT投资"}, headers=H)
check("scam words: delivered + warning", r.status_code == 200 and r.json()["warning"] and "转账" in r.json()["warning"], r.text)
mid_scam = r.json().get("id")
tv = mike.get(f"/api/friends/threads/{tid}").json(); check("recipient sees flagged message", any(m["flagged"] and m["id"] == mid_scam for m in tv["messages"]), tv)
check("normal message not flagged", not [m for m in tv["messages"] if m["body"] == "你好呀"][0]["flagged"])
check("after reply sender can send again", mike.post(f"/api/friends/threads/{tid}/messages", json={"body": "我不做投资哦"}, headers=H).status_code == 200)
check("message too long rejected", mike.post(f"/api/friends/threads/{tid}/messages", json={"body": "啊" * 501}, headers=H).status_code == 400)
rp = [x for x in admin.get("/api/admin/reports").json()["reports"] if x["type"] == "message" and x["target_id"] == mid_scam]
check("auto-flag goes to admin reports", rp and rp[0]["reporter"] == "系统自动标记" and rp[0]["target_user"] == uid_f, rp)
check("admin flagged-messages list", any(x["id"] == mid_scam for x in admin.get("/api/admin/flagged-messages").json()["messages"]))
check("report message by participant", mike.post("/api/community/report", json={"type": "message", "id": mid_scam, "reason": "要钱"}, headers=H).status_code == 200)
check("report message by outsider 404", vic.post("/api/community/report", json={"type": "message", "id": mid_scam, "reason": "x"}, headers=H).status_code == 404)
check("report profile", vic.post("/api/community/report", json={"type": "profile", "id": uid_m, "reason": "疑似假照片"}, headers=H).status_code == 200)
check("admin sees profile report", any(x["type"] == "profile" and x["target_id"] == uid_m for x in admin.get("/api/admin/reports").json()["reports"]))
r = admin.delete(f"/api/admin/messages/{mid_scam}", headers=H); check("admin deletes message", r.status_code == 200 and all(m["id"] != mid_scam for m in mike.get(f"/api/friends/threads/{tid}").json()["messages"]))
# 拉黑
r = fay.post(f"/api/friends/block/{uid_m}", json={}, headers=H); check("block", r.json().get("blocked") is True, r.text)
check("blocked: cannot send", mike.post(f"/api/friends/threads/{tid}/messages", json={"body": "?"}, headers=H).status_code == 403)
check("blocked: profile hidden from blocked user", mike.get(f"/api/friends/profile/{uid_f}").status_code == 404 and all(c["uid"] != uid_f for c in mike.get("/api/friends/list").json()["cards"]))
check("blocker cannot send either", fay.post(f"/api/friends/threads/{tid}/messages", json={"body": "hi"}, headers=H).status_code == 403)
check("blocks list", any(b["uid"] == uid_m for b in fay.get("/api/friends/blocks").json()["blocks"]))
r = fay.post(f"/api/friends/block/{uid_m}", json={}, headers=H); check("unblock", r.json().get("blocked") is False and mike.get(f"/api/friends/profile/{uid_f}").status_code == 200)
# 打招呼每日上限（直接写库模拟已打 20 次）
if os.environ.get("DATABASE_URL"):
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"]) as cx:
        cx.execute("INSERT INTO c_messages(thread_id,sender_id,recipient_id,kind,body) SELECT %s,%s,%s,'greet','x' FROM generate_series(1,19)", (tid, uid_m, uid_f))
    gina.post("/api/friends/me", json={**PF, "nickname": "吉娜"}, headers=H)
    r = mike.post(f"/api/friends/greet/{uid_g}", json={}, headers=H); check("greet daily limit 20 -> 429", r.status_code == 429 and "20" in r.json()["detail"], r.text)
# 可见性 / 管理员
r = fay.post("/api/friends/me/visible", json={"visible": False}, headers=H); check("hide own profile", r.json().get("visible") is False)
check("invisible profile: 404 for others, ok for owner", vic.get(f"/api/friends/profile/{uid_f}").status_code == 404 and fay.get(f"/api/friends/profile/{uid_f}").status_code == 200)
check("invisible photo 404 for others", vic.get(f"/api/friends/photo/{cover}").status_code == 404 and guest.get(f"/api/friends/photo/{cover}?blur=1").status_code == 404)
fay.post("/api/friends/me/visible", json={"visible": True}, headers=H)
mno_f = fay.get("/api/community/me").json()["user"]["member_no"]
af = admin.get("/api/admin/friends", params={"s": str(mno_f)}).json()["profiles"]; check("admin lists profile", af and af[0]["uid"] == uid_f and af[0]["member_no"] == mno_f and len(af[0]["photos"]) == 5, af)
check("admin friend photo", admin.get(f"/api/admin/friend-photo/{cover}").status_code == 200 and guest.get(f"/api/admin/friend-photo/{cover}").status_code == 401)
r = admin.post(f"/api/admin/friends/{uid_f}", json={"action": "verify"}, headers=H)
check("admin verify -> badge", vic.get(f"/api/friends/profile/{uid_f}").json()["verified"] is True and [c for c in vic.get("/api/friends/list").json()["cards"] if c["uid"] == uid_f][0]["verified"])
check("verified listed first", vic.get("/api/friends/list").json()["cards"][0]["uid"] == uid_f)
check("admin users shows verified", admin.get("/api/admin/users", params={"s": f"fay{rnd}"}).json()["users"][0]["verified"] is True)
admin.post(f"/api/admin/users/{uid_f}", json={"action": "unverify"}, headers=H); check("unverify via users", vic.get(f"/api/friends/profile/{uid_f}").json()["verified"] is False)
admin.post(f"/api/admin/friends/{uid_f}", json={"action": "hide"}, headers=H)
check("admin hide: gone from list/detail", all(c.get("uid") != uid_f for c in vic.get("/api/friends/list").json()["cards"]) and vic.get(f"/api/friends/profile/{uid_f}").status_code == 404)
check("hidden: owner still sees with flag", fay.get(f"/api/friends/profile/{uid_f}").json()["hidden"] is True)
check("hidden: cannot greet", gina.post(f"/api/friends/greet/{uid_f}", json={}, headers=H).status_code == 404)
admin.post(f"/api/admin/friends/{uid_f}", json={"action": "unhide"}, headers=H); check("admin unhide", vic.get(f"/api/friends/profile/{uid_f}").status_code == 200)
admin.post(f"/api/admin/friends/{uid_f}", json={"action": "delete_photo", "photo": cover}, headers=H); check("admin delete photo", cover not in fay.get("/api/friends/me").json()["profile"]["photos"])
check("admin friends api blocked for guest", guest.get("/api/admin/friends").status_code == 401)
admin.post(f"/api/admin/users/{uid_m}", json={"action": "ban"}, headers=H)
check("banned user's profile disappears", vic.get(f"/api/friends/profile/{uid_m}").status_code == 404 and all(c["uid"] != uid_m for c in vic.get("/api/friends/list").json()["cards"]))
check("cannot message banned user", fay.post(f"/api/friends/threads/{tid}/messages", json={"body": "hi"}, headers=H).status_code == 403)
tier(uid_f, 1)
check("downgraded: cannot edit, can hide/delete", fay.post("/api/friends/me", json=PF, headers=H).status_code == 403 and fay.post("/api/friends/me/visible", json={"visible": True}, headers=H).status_code == 200)
check("delete own profile", fay.delete("/api/friends/me", headers=H).status_code == 200 and fay.get("/api/friends/me").json()["profile"] is None)
for x in admin.get("/api/admin/reports").json()["reports"]:   # 清理，便于在同一数据库重复运行
    admin.post(f"/api/admin/reports/{x['id']}/resolve", json={}, headers=H)
admin.post("/api/admin/settings", json={"rate_cny_krw": "195"}, headers=H)
ov = admin.get("/api/admin/overview").json(); check("overview has profiles/flagged", "profiles" in ov and "flagged7" in ov, ov)
r = carol.post("/api/community/register", json={"account": f"botnick{rnd}@example.com", "password": "Passw0rd!xyz", "nickname": "旅韩小助手", "agree": True}, headers=H)
check("e2e cannot register 旅韩小助手", r.status_code == 400, r.text)
st = admin.get("/api/admin/settings").json()["settings"]
check("e2e admin settings includes ai_reply", any(x.get("key")=="ai_reply" for x in st), st)
admin.post("/api/admin/logout", json={}, headers=H); check("admin logout", admin.get("/api/admin/overview").status_code == 401)

ok = sum(1 for _, c in results if c); print(f"\n{ok}/{len(results)} passed")
sys.exit(0 if ok == len(results) else 1)
