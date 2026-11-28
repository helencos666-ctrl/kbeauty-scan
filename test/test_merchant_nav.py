"""首页改版后的找商家入口检查：找商家只在「全部功能」出现，首页改用路线/翻译 + 吃喝玩乐 + 广告 + 吃喝玩乐推荐卡片。
python test/test_merchant_nav.py"""
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

def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()

index = read("static/index.html")
shops = read("static/shops.html")
me = read("static/me.html")
prov = read("providers.py")
svc = read("static/services.html")

home = index.split('id="p-home"')[1].split('id="p-cos"')[0] if 'id="p-home"' in index else ""
more = index.split('id="p-more"')[1].split("</section>")[0] if 'id="p-more"' in index else ""

# ---- 找商家入口：只在全部功能里出现一次 ----
check("no 服务与合作 section", "服务与合作" not in index)
check("no 推荐商家 tile", ">推荐商家<" not in index and "推荐商家</a>" not in index)
check("常用工具 contains 找商家", 'i>🏪</i>找商家' in index)
check("找商家 only once (in 全部功能)", index.count('i>🏪</i>找商家') == 1, index.count('i>🏪</i>找商家'))
check("找商家 inside #p-more", 'i>🏪</i>找商家' in more)
check("home grid has no 找商家", "找商家" not in home and "🏪" not in home)
check("全部功能 has 找商家/路线/翻译/常用韩语/识妆/汇率/美食",
      all(w in index for w in ["地铁·公交路线", "中韩翻译", "常用韩语", "拍照识妆", "汇率换算", "找商家", "韩国美食"]))
check("我是商家入口已合并", "我是商家 · 入驻并发布服务" in index and "我是商家 · 入驻并发布服务" in me and "商家发布信息" not in me.split("<nav")[0])
check("商家入口在 #p-more 底部", "我是商家 · 入驻并发布服务" in more and more.rindex("我是商家 · 入驻并发布服务") > more.rindex("常见问题") and 'href="/shops/start"' in more)

# ---- 新首页模块 ----
check("home 三大入口 路线/翻译/扫描识别", 'data-go="sub"' in home and 'data-go="card"' in home
      and 'data-go="cos"' in home and "href=\"/?tab=cos\"" in home
      and ">路线<" in home and ">翻译<" in home and ">美妆识别<" in home)
coreblk = home.split('<div class="core">', 1)[1].split('<!-- B.', 1)[0] if '<div class="core">' in home else ""
check("home .core 三张卡同一行", coreblk.count("<a href=") == 3, coreblk.count("<a href="))
check("home 扫描识别卡保留原有跳转", 'href="/?tab=cos" data-go="cos"' in coreblk
      and 'href="/scan"' not in index)
check("home 美妆识别副标题用拍照", "拍照查成分" in coreblk and "美妆拍照识别" not in coreblk and "扫码" not in index)
check("home 吃喝玩乐五格", "吃喝玩乐" in home and all(
    u in home for u in ["/food", "/dazi", "/shopping", "/attractions", "/places?category=hotel"]))
check("home 五格文案", all(w in home for w in [">美食<", ">找搭子<", ">购物<", ">景点<", ">酒店<"]))
check("home 广告轮播四张", all("/static/home-ads/ad-%d.jpg?v=102" % i in home for i in (1, 2, 3, 4)) and "蓝憬通讯" in home and 'data-ad="lanjing"' in home
      and 'class="ads' in home and "adsDots" in home)
check("home 无附近推荐板块", "附近推荐" not in home and "nearBlk" not in index and "按距离" not in home)
check("home 吃喝玩乐瀑布流紧跟广告", "playBlk" in home and "playGrid" in home
      and "/api/community/feed?tab=latest&has_image=1&show_cat=home" in index
      and "/api/places?limit_n=40" not in index
      and "会员推荐" in home and "play-lab" in home and "playChips" not in home and "playTitle" not in home and "更多 ›" not in home
      and "韩友圈分享" not in home
      and "community.js" in index)
check("home 开业活动条保留", 'id="openPromo"' in home and "开业活动" in index and "/api/opening-promo" in index)
check("home 没有出发前 chips", 'class="chips home-low"' not in index and "出发前" not in index)
check("home 没有示例店名", not any(w in home for w in ["广藏市场", "星巴克", "明洞乐天免税"]))
check("no 示例 label anywhere", "示例" not in index)
check("no 热点商家横滑块", 'id="shopBlk"' not in index and 'id="postBlk"' not in index)
check("home 搜索框移到 #p-more", 'id="q"' in more and 'id="qres"' in more and 'id="q"' not in home)
check("底部五格 nav", 'nav class="tabbar"' in index or '<nav class="tabbar"' in index)

# ---- 保留的功能代码路径 ----
check("拍照识妆入口保留", "p-cos" in index and "data-pick=\"cos\"" in index)
check("地铁路线保留", "p-sub" in index and ("rtBtn" in index or "route-box" in index))
check("中韩翻译保留", "p-card" in index and "/api/translate" in index)
check("常用韩语保留", "p-ph" in index and "phraseList" in index)
check("TTS 保留", "/api/tts" in index and "speak(" in index)

# ---- 商家 hub ----
check("shops is 商家 hub", "<h1>商家</h1>" in shops or "<h1>🏪 商家</h1>" in shops or "<title>商家" in shops)
check("shops 有商家服务、无精选商家标题", "商家服务" in shops and "精选商家" not in shops)
check("shops 有四张已有广告", all(x in shops for x in ("咸草酱蟹", "大家烤肉", "DAYONE", "蓝憬通讯", "/ads/lanjing")))
check("shops 不再是旧三 tab", "shoptabs" not in shops and "推荐店铺" not in shops)
check("/services 301 in providers.py", "RedirectResponse(\"/shops" in prov and "status_code=301" in prov)
check("services.html fallback redirect", "/shops" in svc and "商家" in svc)
check("me 有全部功能/识妆入口", "/?tab=more" in me and "/?tab=cos" in me)


check("home 广告字幕 #adsCap 且四张齐全",
      'id="adsCap"' in home and all(x in home for x in ("咸草酱蟹", "大家烤肉", "DAYONE", "蓝憬通讯")))
check("home 第 4 张副标题：推荐好友领手机卡",
      "推荐2位好友注册 免费领韩国手机卡" in home)


# ---- 首页三张核心卡强调（深绿渐变 + 白字 + 白色 SVG 图标） ----
def css_first(sel):
    m = re.search(re.escape(sel) + r"\{([^}]*)\}", index)
    return m.group(1) if m else ""

core_a = css_first(".core a")
core_b = css_first(".core b")
core_span = css_first(".core span")
core_ico = css_first(".core .ico")

check("home 三卡上方有小标题 旅韩三件套", 'class="core-lab"' in home and "旅韩三件套" in home)
check("home .core 仍是三列 grid", "grid-template-columns:1fr 1fr 1fr" in css_first(".core"))
check("home 三卡 href/data-go 不变",
      'href="/?tab=sub" data-go="sub"' in coreblk
      and 'href="/?tab=card" data-go="card"' in coreblk
      and 'href="/?tab=cos" data-go="cos"' in coreblk)
check("home .core a 深绿渐变底", "linear-gradient" in core_a
      and ("#146E68" in core_a or "#0F5A55" in core_a))
check("home .core a 不再是白底", "background:#fff" not in core_a and "background: #fff" not in core_a)
check("home .core b 文字为白色", "color:#fff" in core_b)
check("home .core span 为实色浅白", ("#E6F3F1" in core_span or "#F3FAF9" in core_span)
      and "var(--sub)" not in core_span)
check("home .core b 字号至少 15px", bool(re.search(r"font-size:(1[5-9]|[2-9]\d)px", core_b)), core_b)
check("home .core .ico 内为 svg 而非 emoji", "<svg" in coreblk
      and not any(e in coreblk for e in ("🚇", "💬", "📷")))
check("home .core .ico 保留 36×36", "width:36px" in core_ico and "height:36px" in core_ico)
check("home 美妆识别标题副标题未改", "<b>美妆识别</b><span>拍照查成分</span>" in coreblk)
check("home 美妆图标为彩色口红", "#E23B4C" in coreblk and "#E2B84A" in coreblk and "#3B2A16" in coreblk)
check("home 美妆图标白底瓷砖", 'class="ico ico-lip"' in coreblk and "background:#fff" in css_first(".core .ico-lip"))

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
