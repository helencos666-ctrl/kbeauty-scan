"""纯静态检查：首页六格底部导航、?v=72 缓存版本、新页面（trips/local）与禁词。
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

# ---------- 1. 缓存版本 v63 ----------
check("sw.js CACHE is lvhan-shell-v72", "'lvhan-shell-v72'" in sw or '"lvhan-shell-v72"' in sw)
check("sw.js uses ?v=72", "?v=72" in sw)
check("sw.js has no ?v=60", "?v=60" not in sw)
for name, txt in PAGES.items():
    check("%s uses ?v=72 (or tabbar)" % name, ("?v=72" in txt) or ("tabbar" in txt))
    check("%s has no ?v=60" % name, "?v=60" not in txt)
check("sw.js SHELL has four home ads", all("/static/home-ads/ad-%d.jpg?v=72" % i in sw for i in (1, 2, 3, 4)))
check("sw.js SHELL has /trips and /local", "'/trips'" in sw and "'/local'" in sw)
check("sw.js SHELL has trips.html/local.html", "/static/trips.html" in sw and "/static/local.html" in sw)

# ---------- 2. 底部六格 ----------
m = re.search(r'<nav class="tabbar"[^>]*>.*?</nav>', index, re.S)
navblock = m.group(0) if m else ""
check("index has nav.tabbar", bool(navblock))
check("六格文案 首页/关注/发布/商业服务/生活在韩国/我的",
      all(w in navblock for w in ["首页", "关注", "发布", "商业服务", "生活在韩国", "我的"]))
check("关注 -> /community?tab=follow", 'href="/community?tab=follow"' in navblock and 'data-nav="follow"' in navblock)
check("发布 -> /community/new", 'href="/community/new"' in navblock and 'class="pub"' in navblock and "＋" in navblock)
check("商业服务 -> /shops", 'href="/shops"' in navblock and 'data-nav="services"' in navblock)
check("生活在韩国 -> /local", 'href="/local"' in navblock and 'data-nav="local"' in navblock and "生活在韩国" in navblock)
check("首页 -> / 且 data-nav=home", re.search(r'<a href="/" data-nav="home"', navblock) is not None)
check("首页默认点亮", 'data-nav="home" class="on"' in navblock)
check("行程入口 -> /trips", 'href="/trips"' in index and "🗓️" in index)
check("底部我的 -> /me", 'href="/me"' in navblock and 'data-nav="me"' in navblock and "我的" in navblock)
check("首页不再放右上角我的头像", "me-ava" not in index)
check("local 不再放右上角我的头像", "me-ava" not in local)
check("trips 不再放右上角我的头像", "me-ava" not in trips)

for name, txt in (("me.html", me), ("trips.html", trips), ("local.html", local)):
    n = re.search(r'<nav class="tabbar"[^>]*>.*?</nav>', txt, re.S)
    nb = n.group(0) if n else ""
    check("%s has six-grid tabbar" % name,
          bool(nb) and all(w in nb for w in ["首页", "关注", "发布", "商业服务", "生活在韩国", "我的"]))
check("local.html 点亮 生活在韩国", 'data-nav="local" class="loc on"' in local)
check("me.html 点亮最后一个「我的」", 'data-nav="me" class="me on"' in me)

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
check("local 韩友圈是六宫格第一张且链到韩友圈首页",
      "loc-grid" in local and local.split('class="loc-grid"', 1)[1].find("韩友圈") < local.split('class="loc-grid"', 1)[1].find("租房")
      and 'href="/community"' in local and "<b>韩友圈</b>" in local
      and "/community?board=daily" not in local)
for board, label in (("rent", "租房"), ("jobs", "招聘"), ("used", "二手"),
                     ("visa", "签证"), ("study", "留学")):
    check("local has %s" % label, ("/community?board=%s" % board) in local and label in local)
check("local 标题是生活在韩国", "生活在韩国" in local and "在韩国生活的信息频道" not in local)
check("pwa.js 含生活在韩国和我的", "生活在韩国" in pwa_js and "label:'我的'" in pwa_js and "label:'本地生活'" not in pwa_js)
check("pwa.js currentKey 会点亮 /me", "p === '/me'" in pwa_js or 'p === "/me"' in pwa_js)
check("pwa.js 为「我的」拉未读角标", "/api/me/summary" in pwa_js and "tab-dot" in pwa_js and "paintMeBadge" in pwa_js)
check("pwa.css 生活在韩国 tab 单行缩小字号",
      "nav.tabbar a.loc" in pwa_css and "white-space:nowrap" in pwa_css and "font-size:8px" in pwa_css)

# ---------- 5. trips.html 即将上线 ----------
check("trips is 即将上线", "即将上线" in trips or "即将" in trips)
check("trips mentions 我的行程/大家分享/平台推荐",
      all(w in trips for w in ["我的行程", "大家分享", "平台推荐"]))
check("trips links /itineraries", "/itineraries" in trips)
check("trips has no sample store names",
      not any(w in trips for w in ["广藏市场", "星巴克", "明洞乐天免税", "北村韩屋"]))
check("trips has back and me", 'href="/"' in trips and 'href="/me"' in trips)
check("trips loads pwa.js?v=72", "/static/pwa.js?v=72" in trips)

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

# ---------- 8. 弹层不被底部六格导航遮挡（2026-11-17） ----------
# 追加到 test_home_nav.py 末尾（print 汇总之前）。复用已有的 me / pwa_css / comm_css 变量。
check("me.html 打开弹层时加 lh-overlay-open", "classList.add('lh-overlay-open')" in me)
check("me.html 关闭弹层时移除 lh-overlay-open", "classList.remove('lh-overlay-open')" in me)
check("me.html 弹层有 openSheet/closeSheet", "function openSheet(" in me and "function closeSheet(" in me)
check("me.html 保存成功后关闭弹层", "closeSheet(); load();" in me)
check("me.html 点遮罩只在 target===this 时关闭", "e.target === this" in me)
_pbio = re.search(r'<input[^>]*id="pBio"[^>]*>', me)
_pbio_s = _pbio.group(0) if _pbio else ""
check("me.html 简介输入可编辑且限 40 字",
      bool(_pbio) and "readonly" not in _pbio_s.lower() and 'maxlength="40"' in _pbio_s, _pbio_s)
check("me.html 编辑资料有保存按钮 #pGo", 'id="pGo"' in me)

_navz = re.search(r'body nav\.tabbar\{[^}]*z-index:(\d+)', pwa_css)
check("pwa.css tabbar 仍为 z-index 60", bool(_navz) and _navz.group(1) == "60",
      _navz.group(1) if _navz else "missing")
check("pwa.css 弹层打开时隐藏 tabbar",
      re.search(r'body\.lh-overlay-open nav\.tabbar\s*\{[^}]*visibility:hidden', pwa_css) is not None)
check("pwa.css 用 :has 覆盖弹层/预览/大图/大字卡",
      all(s in pwa_css for s in ("body:has(.sheet:not([hidden])) nav.tabbar",
                                  "body:has(.preview-mask.show) nav.tabbar",
                                  'body:has(.lightbox[style*="flex"]) nav.tabbar',
                                  'body:has(#big[style*="flex"]) nav.tabbar')))

_lb = re.search(r'\.lightbox\{[^}]*z-index:(\d+)', comm_css)
_pm = re.search(r'\.preview-mask\{[^}]*z-index:(\d+)', comm_css)
check("community.css .lightbox 高于 tabbar", bool(_lb) and int(_lb.group(1)) > 60,
      _lb.group(1) if _lb else "missing")
check("community.css .preview-mask 高于 tabbar", bool(_pm) and int(_pm.group(1)) > 60,
      _pm.group(1) if _pm else "missing")


# ---------- 首页广告轮播：字幕与四张指示点 ----------
_ads = index.split('id="homeAds"')[1].split('id="nearBlk"')[0] if 'id="homeAds"' in index else ""
check("home 广告字幕容器 #adsCap", 'class="ads-cap" id="adsCap"' in _ads)
check("home 广告四张字幕",
      all(x in _ads for x in ("咸草酱蟹", "大家烤肉", "DAYONE", "蓝憬通讯")))
check("home 第 4 张推荐好友领手机卡文案",
      "推荐2位好友注册 免费领韩国手机卡" in _ads)
check("home 广告 4 个指示点且首个点亮",
      re.search(r'id="adsDots"[^>]*>\s*<i class="on"></i>\s*<i></i>\s*<i></i>\s*<i></i>\s*</div>', index) is not None)
check("home 广告四张 img 不再 lazy",
      all('loading="lazy"' not in m
          for m in re.findall(r'<img[^>]*ad-[1-4]\.jpg[^>]*>', _ads)))
check("home 广告切换 0.4s transform",
      re.search(r"transition\s*:\s*transform\s+0?\.4s\s+ease", index) is not None)
check("home 广告停留 3000ms 且有无缝克隆",
      "3000" in index and "cloneNode" in index and "pointermove" in index)


print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
