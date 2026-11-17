"""纯静态检查：首页五格底部导航、?v=61 缓存版本、新页面（trips/local）与禁词。
不需要启服务，直接 python test/test_home_nav.py 即可。"""
import os, re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ok = fail = 0

def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS", name)
    else:
        fail += 1
        print("FAIL", name, str(info)[:300])

def read(rel):
    with open(os.path.join(ROOT, "static", rel), encoding="utf-8") as f:
        return f.read()

def read_root(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()

index = read("index.html")
me = read("me.html")
trips = read("trips.html")
local = read("local.html")
pwa_css = read("pwa.css")
pwa_js = read("pwa.js")
comm_css = read("community.css")
sw = read("sw.js")

PAGES = {
    "index.html": index, "me.html": me, "trips.html": trips, "local.html": local,
    "pwa.css": pwa_css, "pwa.js": pwa_js,
}

# ---------- 1. 缓存版本 v61 ----------
check("sw.js CACHE is lvhan-shell-v61", "'lvhan-shell-v61'" in sw or '"lvhan-shell-v61"' in sw)
check("sw.js uses ?v=61", "?v=61" in sw)
check("sw.js has no ?v=60", "?v=60" not in sw)
for name, txt in PAGES.items():
    check("%s uses ?v=61 (or tabbar)" % name, ("?v=61" in txt) or ("tabbar" in txt))
    check("%s has no ?v=60" % name, "?v=60" not in txt)
check("sw.js SHELL has four home ads", all("/static/home-ads/ad-%d.jpg?v=61" % i in sw for i in (1, 2, 3, 4)))
check("sw.js SHELL has /trips and /local", "'/trips'" in sw and "'/local'" in sw)
check("sw.js SHELL has trips.html/local.html", "/static/trips.html" in sw and "/static/local.html" in sw)

# ---------- 2. 底部五格 ----------
m = re.search(r'<nav class="tabbar"[^>]*>.*?</nav>', index, re.S)
navblock = m.group(0) if m else ""
check("index has nav.tabbar", bool(navblock))
check("五格文案 首页/关注/发布/商业服务/本地生活",
      all(w in navblock for w in ["首页", "关注", "发布", "商业服务", "本地生活"]))
check("关注 -> /community?tab=follow", 'href="/community?tab=follow"' in navblock and 'data-nav="follow"' in navblock)
check("发布 -> /community/new", 'href="/community/new"' in navblock and 'class="pub"' in navblock and "＋" in navblock)
check("商业服务 -> /shops?tab=services", 'href="/shops?tab=services"' in navblock and 'data-nav="services"' in navblock)
check("本地生活 -> /local", 'href="/local"' in navblock and 'data-nav="local"' in navblock)
check("首页 -> / 且 data-nav=home", re.search(r'<a href="/" data-nav="home"', navblock) is not None)
check("首页默认点亮", 'data-nav="home" class="on"' in navblock)
check("行程入口 -> /trips", 'href="/trips"' in index and "🗓️" in index)
check("首页头像 -> /me", 'class="me-ava" href="/me"' in index or 'href="/me" title="我的"' in index)

for name, txt in (("me.html", me), ("trips.html", trips), ("local.html", local)):
    n = re.search(r'<nav class="tabbar"[^>]*>.*?</nav>', txt, re.S)
    nb = n.group(0) if n else ""
    check("%s has five-grid tabbar" % name,
          bool(nb) and all(w in nb for w in ["首页", "关注", "发布", "商业服务", "本地生活"]))
check("local.html 点亮 本地生活", 'data-nav="local" class="loc on"' in local)
check("me.html 五项都不点亮", "class=\"on\"" not in re.search(r'<nav class="tabbar".*?</nav>', me, re.S).group(0))

# ---------- 3. pwa.js 注入/校正 tabbar ----------
check("pwa.js mentions nav.tabbar", "nav.tabbar" in pwa_js)
check("pwa.js builds data-nav links", "data-nav" in pwa_js and "tabbar" in pwa_js)
check("pwa.js replaces old nav", "replaceChild" in pwa_js or "buildTabbar" in pwa_js)
check("pwa.js appends tabbar when missing", "appendChild" in pwa_js)
check("pwa.js keeps SW registration", "serviceWorker" in pwa_js and "/sw.js" in pwa_js)
check("pwa.js keeps install prompt", "beforeinstallprompt" in pwa_js and "pwaBar" in pwa_js)
check("tabbar style is scoped", "nav.tabbar" in pwa_css and "nav.tabbar a.pub .plus" in pwa_css)
check("community.css does not override tabbar", "nav:not(.tabbar)" in comm_css)
check("body has-tabbar padding", "body.has-tabbar" in pwa_css)

# ---------- 4. local.html 六个频道 ----------
for board, label in (("rent", "租房"), ("jobs", "招聘"), ("used", "二手"),
                     ("visa", "签证"), ("study", "留学"), ("daily", "在韩日常")):
    check("local has %s" % label, ("/community?board=%s" % board) in local)

# ---------- 5. trips.html 即将上线 ----------
check("trips is 即将上线", "即将上线" in trips or "即将" in trips)
check("trips mentions 我的行程/大家分享/平台推荐",
      all(w in trips for w in ["我的行程", "大家分享", "平台推荐"]))
check("trips links /itineraries", "/itineraries" in trips)
check("trips has no sample store names",
      not any(w in trips for w in ["广藏市场", "星巴克", "明洞乐天免税", "北村韩屋"]))
check("trips has back and me", 'href="/"' in trips and 'href="/me"' in trips)
check("trips loads pwa.js?v=61", "/static/pwa.js?v=61" in trips)

# ---------- 6. 禁词 ----------
ALL = dict(PAGES)
ALL["community.css"] = comm_css
ALL["sw.js"] = sw
ALL["guides.py"] = read_root("guides.py")

for name, txt in ALL.items():
    check("%s 无「奋韩」" % name, "奋韩" not in txt)
    check("%s 无微信号/加微信" % name,
          ("微信号" not in txt) and ("加微信" not in txt) and ("添加微信" not in txt))
    check("%s 无二维码引导" % name, ("二维码" not in txt) and ("扫码" not in txt))
    check("%s 无绝对化宣传语" % name,
          not re.search(r"唯一|第一名|第一品牌|销量第一|排名第一|最(好|佳|优|强|低|便宜|值得|受欢|专业|靠谱|出名)|顶级|极致|国家级", txt))

# ---------- 7. 首页不出现假数据 ----------
home = index.split('id="p-home"')[1].split('id="p-cos"')[0] if 'id="p-home"' in index else ""
check("home 无示例标签", "示例" not in index)
check("home 无示例店名", not any(w in home for w in ["广藏市场", "星巴克", "明洞乐天免税"]))
check("home 附近推荐走 /api/places", "/api/places" in index)
check("home 韩友圈走 /api/community/posts", "/api/community/posts" in index)
check("lanjing ad links /ads/lanjing", 'data-ad="lanjing"' in index and "ad-4.jpg" in index and 'href="/ads/lanjing"' in index)
check("home 空状态有去处链接", "/places/new" in index and "/community/new" in index)

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
