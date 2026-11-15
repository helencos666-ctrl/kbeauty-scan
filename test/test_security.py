"""安全加固测试（push17）：python test/test_security.py http://127.0.0.1:8917
需要环境变量 ADMIN_PASSWORD 与 DATABASE_URL（本机测试库）。只在本机测试库上运行，不要对线上运行。"""
import io, os, random, re, sys, time
import httpx, psycopg
from PIL import Image

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8917"
ADMIN_PW = os.environ["ADMIN_PASSWORD"]
DB = os.environ["DATABASE_URL"]
H = {"X-LH": "1"}
ok = fail = 0

def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1; print("PASS", name)
    else:
        fail += 1; print("FAIL", name, str(info)[:300])

def ip():
    return "10.%d.%d.%d" % (random.randint(0, 255), random.randint(0, 255), random.randint(1, 254))

def client(**kw):
    h = {"cf-connecting-ip": ip()}
    h.update(kw.pop("headers", {}))
    return httpx.Client(base_url=BASE, headers=h, timeout=30, **kw)

rnd = random.randint(100000, 999999)
g = client()

# ---- 1. 安全响应头 ----
r = g.get("/")
hd = r.headers
check("HSTS", "max-age=31536000" in hd.get("strict-transport-security", ""))
check("nosniff", hd.get("x-content-type-options") == "nosniff")
check("XFO DENY", hd.get("x-frame-options") == "DENY")
check("referrer-policy", hd.get("referrer-policy") == "strict-origin-when-cross-origin")
check("permissions-policy", "geolocation=()" in hd.get("permissions-policy", ""))
csp = hd.get("content-security-policy", "")
m = re.search(r"'nonce-([A-Za-z0-9_\-]+)'", csp)
check("CSP with nonce + frame-ancestors", m and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "unsafe-eval" not in csp, csp)
tags = re.findall(r"<script\b[^>]*>", r.text)
check("every <script> has the nonce", tags and all(('nonce="%s"' % m.group(1)) in t for t in tags), tags[:3])
check("nonce changes per request", re.search(r"'nonce-([^']+)'", g.get("/").headers["content-security-policy"]).group(1) != m.group(1))
r = g.get("/static/index.html")
check("/static/*.html also gets nonce CSP", "nonce-" in r.headers.get("content-security-policy", "") and 'nonce="' in r.text)
r = g.get("/api/community/me")
check("api no-store + strict CSP", r.headers.get("cache-control") == "no-store" and r.headers.get("content-security-policy", "").startswith("default-src 'none'"))
r = g.get("/static/community.js")
check("static js has security headers", r.status_code == 200 and r.headers.get("x-content-type-options") == "nosniff")
check("no inline event handlers in community.js", not re.search(r"\son(error|click|load)=", r.text))
check("no server header", "server" not in {k.lower() for k in g.get("/healthz").headers})
r = g.get("/", headers={"x-forwarded-proto": "http", "host": "lvhanzhushou.com"})
check("proxied http -> 301 https", r.status_code == 301 and r.headers.get("location", "").startswith("https://lvhanzhushou.com/"), r.status_code)

# ---- 2. 法律页面 ----
for path in ("/privacy", "/terms"):
    r = g.get(path)
    check(path + " 200", r.status_code == 200 and "{{PRIVACY_CONTACT}}" not in r.text and "旅韩助手" in r.text)
pv, tm = g.get("/privacy").text, g.get("/terms").text
check("privacy covers PIPA/PIPL items", all(w in pv for w in ["收集", "保存多久", "跨境", "注销", "118", "14 岁", "DeepSeek", "Render", "Cloudflare"]))
check("terms covers disclaimers", all(w in tm for w in ["仅供参考", "不是旅行社", "不代办签证", "不承诺任何效果", "举报"]))
check("terms/privacy don't mention competitor", "奋韩" not in pv and "奋韩" not in tm)
lg = g.get("/login").text
check("login links terms+privacy", 'href="/terms"' in lg and 'href="/privacy"' in lg)
check("home footer links legal", 'href="/privacy"' in g.get("/").text and 'href="/terms"' in g.get("/").text)
sm = g.get("/sitemap.xml").text
check("sitemap has privacy/terms", "/privacy</loc>" in sm and "/terms</loc>" in sm)
check("sw v25 caches legal pages", "lvhan-shell-v25" in g.get("/sw.js").text and "'/privacy'" in g.get("/sw.js").text)

# ---- 3. CSRF / 同源 ----
r = g.post("/api/community/login", json={"account": "a@b.cc", "password": "x" * 8}, headers={**H, "origin": "https://evil.example"})
check("cross-origin POST blocked", r.status_code == 403, r.text)
r = g.post("/api/community/login", json={"account": "a@b.cc", "password": "x" * 8}, headers={**H, "origin": "null"})
check("null origin blocked", r.status_code == 403)
r = g.post("/api/community/login", json={"account": "a@b.cc", "password": "x" * 8}, headers={**H, "referer": "https://evil.example/x"})
check("cross-site referer blocked", r.status_code == 403)
r = g.post("/api/community/login", json={"account": "nobody%d@b.cc" % rnd, "password": "x" * 8}, headers={**H, "origin": BASE})
check("same-origin POST allowed (401 wrong pw)", r.status_code == 401, r.text)
r = g.post("/api/community/login", json={"account": "a@b.cc", "password": "x" * 8})
check("missing X-LH header blocked", r.status_code == 403)
r = g.post("/api/card", json={"text": "你好"}, headers={"origin": "https://evil.example"})
check("AI endpoint cross-origin blocked", r.status_code == 403)

# ---- 4. 输入健壮性 / 通用错误 ----
r = g.post("/api/community/login", content=b"[1,2]", headers={**H, "content-type": "application/json"})
check("non-object JSON -> 400", r.status_code == 400, r.text)
r = g.post("/api/station", content=b"\"x\"", headers={"content-type": "application/json"})
check("station non-object JSON -> 400", r.status_code == 400, r.text)
r = g.get("/api/community/posts/abc")
check("validation error is generic", r.status_code == 422 and "abc" not in r.text and "loc" not in r.text, r.text)
r = g.get("/no-such-page")
check("404 generic", r.status_code == 404 and "Traceback" not in r.text)
r = g.get("/static/../app.py")
check("path traversal blocked", r.status_code == 404)
r = g.get("/static/%2e%2e/app.py")
check("encoded path traversal blocked", r.status_code == 404)
for p in ("/docs", "/openapi.json", "/redoc", "/.env", "/.git/config"):
    check("not exposed " + p, g.get(p).status_code == 404)

# ---- 5. 请求体限长（含无 Content-Length 的分块上传）----
def gen():
    yield b'--xx\r\nContent-Disposition: form-data; name="image"; filename="a.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'
    for _ in range(140):     # ~9MB，无 Content-Length
        yield b"0" * 65536
    yield b"\r\n--xx--\r\n"
r = g.post("/api/scan", content=gen(), headers={"content-type": "multipart/form-data; boundary=xx"})
check("chunked oversized body rejected", r.status_code in (400, 413), r.status_code)

# ---- 6. 限流不能靠伪造 X-Forwarded-For 绕过 ----
fixed = ip()
codes = []
for i in range(12):
    rr = httpx.post(BASE + "/api/community/login", json={"account": f"rl{rnd}@ex.com", "password": "wrongpass1"},
                    headers={**H, "cf-connecting-ip": fixed, "x-forwarded-for": ip()})
    codes.append(rr.status_code)
check("spoofed XFF doesn't bypass login limit", 429 in codes, codes)

# ---- 7. 注册 → 发帖 XSS（JSON-LD）----
u = client()
acct = f"sec{rnd}@example.com"
r = u.post("/api/community/register", json={"account": acct, "password": "Passw0rd!sec", "nickname": "安全测试", "agree": True}, headers=H)
check("register", r.status_code == 200, r.text)
uid = u.get("/api/community/me").json()["user"]["id"]
with psycopg.connect(DB) as pc:
    pc.execute("UPDATE c_users SET tier=2 WHERE id=%s", (uid,))
payload = '</script><script>alert(1)</script>'
r = u.post("/api/community/posts", data={"board": "pit", "title": "XSS" + payload[:30], "body": "正文测试内容 " + payload + " \\1 \\g<0>"}, headers=H)
check("post with payload accepted (stored as text)", r.status_code == 200, r.text)
pid = r.json().get("id")
page = g.get(f"/community/post/{pid}").text
check("post page: no raw </script><script> injection", "<script>alert(1)" not in page and "</script><script>alert" not in page)
check("post page: title still present escaped", "XSS" in page)
r = u.post("/api/community/posts", data={"board": "pit", "title": "反斜杠\\1\\g<0>标题", "body": "正文测试内容足够长度"}, headers=H)
pid2 = r.json().get("id")
r = g.get(f"/community/post/{pid2}")
check("backslash title renders (no re.sub crash)", r.status_code == 200 and "反斜杠" in r.text, r.status_code)
r = g.get("/community/post/%3Cscript%3Ealert(1)")
check("reflected XSS via post path blocked", "<script>alert(1)" not in r.text and "/community/post/0" in r.text)
r = g.get("/community/friends/%22%3E%3Cscript%3Ealert(1)%3C%2Fscript%3E")
check("reflected XSS via friend path blocked", "<script>alert(1)" not in r.text)
r = g.get("/community")
check("community SSR escapes payload", "<script>alert(1)" not in r.text)

# ---- 8. 图片：格式白名单 + 解压炸弹 ----
def png(w, h, mode="1"):
    b = io.BytesIO(); Image.new(mode, (w, h)).save(b, "PNG"); return b.getvalue()
bomb = png(9000, 9000)       # 8100 万像素，文件很小
r = u.post("/api/community/avatar", files={"image": ("a.png", bomb, "image/png")}, headers=H)
check("decompression bomb rejected", r.status_code == 400, r.text)
b = io.BytesIO(); Image.new("RGB", (64, 64)).save(b, "TIFF")
r = u.post("/api/community/avatar", files={"image": ("a.tif", b.getvalue(), "image/tiff")}, headers=H)
check("TIFF avatar rejected", r.status_code == 400, r.text)
r = u.post("/api/community/avatar", files={"image": ("a.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "image/svg+xml")}, headers=H)
check("SVG avatar rejected", r.status_code == 400, r.text)
r = u.post("/api/community/avatar", files={"image": ("a.png", png(300, 200, "RGB"), "image/png")}, headers=H)
check("normal avatar ok", r.status_code == 200, r.text)
guest_post = client().post("/api/community/posts", data={"board": "pit", "title": "游客发帖测试", "body": "正文测试内容足够长度"},
                           files={"images": ("a.png", bomb, "image/png")}, headers=H)
check("guest post rejected before image processing", guest_post.status_code == 401, guest_post.text)

# ---- 9. 会话 Cookie ----
sc = u.post("/api/community/login", json={"account": acct, "password": "Passw0rd!sec"}, headers=H).headers.get("set-cookie", "")
check("session cookie HttpOnly + SameSite", "httponly" in sc.lower() and "samesite=lax" in sc.lower(), sc.split("=")[0])

# ---- 10. 管理员：失败锁定 + 常量时间比较 ----
aip = ip()
codes = []
for i in range(6):
    rr = httpx.post(BASE + "/api/admin/login", json={"password": "wrong-password-%d" % i}, headers={**H, "cf-connecting-ip": aip})
    codes.append(rr.status_code)
check("admin: 5 failures then locked", codes[:5] == [401] * 5 and codes[5] == 429, codes)
rr = httpx.post(BASE + "/api/admin/login", json={"password": ADMIN_PW}, headers={**H, "cf-connecting-ip": aip})
check("admin: locked IP can't log in even with right password", rr.status_code == 429)
adm = client()
rr = adm.post("/api/admin/login", json={"password": ADMIN_PW}, headers=H)
check("admin: other IP logs in", rr.status_code == 200, rr.text)
ac = rr.headers.get("set-cookie", "").lower()
check("admin cookie HttpOnly + SameSite=Strict + 12h", "httponly" in ac and "samesite=strict" in ac and "max-age=43200" in ac)
rr = adm.post("/api/admin/login", json={"password": None}, headers=H)
check("admin: bad body type handled", rr.status_code in (401, 429))

# ---- 11. 账号注销（自助）与管理员删除 ----
r = u.post("/api/community/account/delete", json={"password": "Passw0rd!sec", "confirm": "好"}, headers=H)
check("delete: needs 注销 confirm", r.status_code == 400)
r = u.post("/api/community/account/delete", json={"password": "wrong-password", "confirm": "注销"}, headers=H)
check("delete: wrong password rejected", r.status_code == 400)
r = client().post("/api/community/account/delete", json={"password": "Passw0rd!sec", "confirm": "注销"}, headers=H)
check("delete: requires login", r.status_code == 401)
r = u.post("/api/community/account/delete", json={"password": "Passw0rd!sec", "confirm": "注销"}, headers=H)
check("delete: ok", r.status_code == 200, r.text)
with psycopg.connect(DB) as pc:
    gone = pc.execute("SELECT count(*) FROM c_users WHERE id=%s", (uid,)).fetchone()[0] == 0
    posts_gone = pc.execute("SELECT count(*) FROM c_posts WHERE user_id=%s", (uid,)).fetchone()[0] == 0
check("delete: user + posts really gone", gone and posts_gone)
check("delete: session invalid", u.get("/api/community/me").json()["user"] is None)
check("delete: post page no longer shows content", "XSS" not in g.get(f"/community/post/{pid}").text)
v = client()
v.post("/api/community/register", json={"account": f"victim{rnd}@example.com", "password": "Passw0rd!sec", "nickname": "待删用户", "agree": True}, headers=H)
vid = v.get("/api/community/me").json()["user"]["id"]
r = adm.post(f"/api/admin/users/{vid}", json={"action": "delete_account"}, headers=H)
check("admin delete_account", r.status_code == 200 and r.json().get("deleted"), r.text)
r = client().post(f"/api/admin/users/{vid}", json={"action": "delete_account"}, headers=H)
check("non-admin can't delete", r.status_code == 401)

print(f"\n{ok}/{ok + fail} passed")
sys.exit(1 if fail else 0)
