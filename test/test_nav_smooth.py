"""页面切换与返回：python test/test_nav_smooth.py
纯静态检查，不启服务。覆盖 Service Worker 不再用首页顶替子页、
首页不会被上次路线拽走、底部七格链接、子页返回键，以及关注/商业服务 tab。
"""
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
        print("FAIL", name, str(info)[:400])

def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()

sw = read("static/sw.js")
pwa = read("static/pwa.js")
index = read("static/index.html")
community = read("static/community.html")
shops = read("static/shops.html")
trips = read("static/trips.html")
me = read("static/me.html")
seo = read("seo.py")
lanjing = read("lanjing_seed.py")

# ---------- Service Worker ----------
check("sw.js CACHE is lvhan-shell-v83", "lvhan-shell-v83" in sw)
check("sw.js 无旧缓存名 v63", "lvhan-shell-v63" not in sw)
check("sw.js 使用 ?v=83", "?v=83" in sw)
check("sw.js SHELL 含 pwa.js?v=83", "/static/pwa.js?v=83" in sw)

# 不应再把任意导航回退到首页文档
fb = re.search(
    r"request\.mode\s*===\s*['\"]navigate['\"][\s\S]{0,400}cache\.match\(\s*['\"]/",
    sw)
check("sw.js 不再把其它导航回退到 cache.match('/')", fb is None, fb.group(0) if fb else "")
fb2 = re.search(
    r"cache\.match\(\s*['\"]/static/index\.html['\"]\s*\)\s*\s*;?\s*\n\s*if \(fallback\)",
    sw)
check("sw.js 不再用 /static/index.html 顶替失败导航", fb2 is None)

# 允许 homepage 路径匹配自己
check("sw.js 有同一路径匹配（不跨到首页）", "samePathCandidates" in sw or "matchSamePath" in sw)
check("sw.js 首页候选只用于 / 自身",
      re.search(r"p === '/'[^\n]*\n[^\n]*return \['/', '/index.html', '/static/index.html'\]", sw) is not None)
check("sw.js 仍不拦截 /api 和 /admin", "isApi(url)" in sw and "pathname === '/admin'" in sw)

# ---------- pwa.js 导航 ----------
check("pwa.js 有 lhGoBack", "window.lhGoBack" in pwa or "lhGoBack" in pwa)
check("lhGoBack 无同源上一页时回 /",
      "location.href = fallback" in pwa and "fallback || '/' " in pwa or "fallback = fallback || '/'" in pwa)
check("pwa.js 只在首页文档拦「首页」点击", "onHomeDoc" in pwa and "showTab('home'" in pwa)
check("pwa.js pageshow 刷新导航", "pageshow" in pwa and "normalizeTabbar" in pwa)
check("pwa.js pageshow 清掉 lh-overlay-open", "classList.remove('lh-overlay-open')" in pwa)
check("七格链接完整",
      all(h in pwa for h in ("href:'/'", "href:'/community?tab=follow'",
                             "href:'/community/new'", "href:'/festival'",
                             "href:'/shops'", "href:'/trips'", "href:'/me'")))

# ---------- 首页：返回键与路线恢复 ----------
check("首页 #back 不再 history.back()",
      "$('back')" in index and "history.back()" not in index)
check("首页 #back 始终回到首页 tab",
      re.search(r"\$\('back'\)\.onclick\s*=\s*e\s*=>\s*\{e\.preventDefault\(\);showTab\('home'", index) is not None)
check("restoreLastRoute 非 tab=sub 直接返回",
      "tabNow!=='sub'" in index or re.search(r"tabNow\s*!==\s*'sub'\s*\)\s*return false", index) is not None)
check("启动时只在 tab=sub 恢复路线",
      "needRestore=(!fr&&!to)&&(tab==='sub')" in index)
check("启动恢复不再因 loadLastRoute 单独成立",
      "tab==='sub'||!!loadLastRoute()" not in index)
check("visibilitychange 只在 tab=sub 时恢复路线",
      re.search(r"visibilitychange[\s\S]{0,400}t==='sub'&&loadLastRoute\(\)", index) is not None)
check("saveLastRoute 不再偷偷改成 tab=sub",
      "history.replaceState({t:'sub'},'','/?tab=sub')" not in index)
check("首页仍有七格且首页 href=/",
      'href="/" data-nav="home"' in index and 'href="/community?tab=follow"' in index
      and 'href="/festival"' in index and 'href="/shops"' in index and 'href="/trips"' in index and 'data-nav="me"' in index)

# ---------- 关注 / 商业服务 / 行程 ----------
check("community 未登录不再把 follow 改成 recommend",
      "tab === 'follow') tab = 'recommend'" not in community
      and "tab === 'follow') tab =" not in community)
check("community 保留 login_required 登录入口", "login_required" in community and "/login?next=" in community)
check("shops 无循环 location 重定向",
      not re.search(r"location\.href\s*=\s*['\"]/shops", shops)
      and "location.replace" not in shops)
check("trips 返回键 href=/",
      re.search(r'<a class="back" href="/"', trips) is not None)
check("trips/me 不用 javascript:history.back",
      all("javascript:history.back" not in x for x in (trips, me, community, shops, index)))

# ---------- 路由无死循环迹象（静态） ----------
check("seo 有 /trips 和 /shops 和 /community",
      all(s in seo for s in ('"/trips"', '"/shops"', '"/community"')))
check("/ads/lanjing 只 302 到帖子或 /community，不回自己",
      'AD_PATH = "/ads/lanjing"' in lanjing
      and 'RedirectResponse("/ads/lanjing"' not in lanjing
      and "RedirectResponse(\"/community/post/" in lanjing
      and 'RedirectResponse("/community"' in lanjing)

# ---------- 禁词 ----------
for name, txt in (("sw.js", sw), ("pwa.js", pwa), ("index.html", index),
                  ("community.html", community)):
    check("%s 无「奋韩」" % name, "奋韩" not in txt)
    check("%s 无微信号/加微信" % name, ("微信号" not in txt) and ("加微信" not in txt))

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
