"""首页改版后的找商家入口检查：找商家只在「全部功能」出现，首页改用路线/翻译 + 吃喝玩乐 + 广告 + 附近推荐 + 韩友圈分享。
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
check("我是商家 · 商家入驻 kept", "我是商家 · 商家入驻" in index and "我是商家 · 商家入驻" in me)
check("商家入驻 is at bottom of #p-more", "我是商家 · 商家入驻" in more and more.rindex("我是商家 · 商家入驻") > more.rindex("常见问题"))

# ---- 新首页模块 ----
check("home 两大入口 路线/翻译", 'data-go="sub"' in home and 'data-go="card"' in home
      and ">路线<" in home and ">翻译<" in home)
check("home 吃喝玩乐五格", "吃喝玩乐" in home and all(
    u in home for u in ["/food", "/food?cat=cafe", "/shopping", "/attractions", "/places?category=hotel"]))
check("home 五格文案", all(w in home for w in [">美食<", ">咖啡<", ">购物<", ">景点<", ">酒店<"]))
check("home 广告轮播四张", all("/static/home-ads/ad-%d.jpg?v=67" % i in home for i in (1, 2, 3, 4)) and "蓝憬通讯" in home and 'data-ad="lanjing"' in home
      and 'class="ads' in home and "adsDots" in home)
check("home 附近推荐", "附近推荐" in home and 'data-sort="dist"' in home and 'data-sort="rec"' in home
      and "/api/places" in index)
check("home 韩友圈分享", "韩友圈分享" in home and "/api/community/posts" in index
      and all(w in home for w in ["全部", "美食", "风景", "自拍"]))
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
check("shops 有精选商家和商家发布", "精选商家" in shops and "商家发布" in shops)
check("shops 有四张已有广告", all(x in shops for x in ("咸草酱蟹", "大家烤肉", "DAYONE", "蓝憬通讯", "/ads/lanjing")))
check("shops 不再是旧三 tab", "shoptabs" not in shops and "推荐店铺" not in shops)
check("/services 301 in providers.py", "RedirectResponse(\"/shops" in prov and "status_code=301" in prov)
check("services.html fallback redirect", "/shops" in svc and "商家" in svc)
check("me 有全部功能/识妆入口", "/?tab=more" in me and "/?tab=cos" in me)


check("home 广告字幕 #adsCap 且四张齐全",
      'id="adsCap"' in home and all(x in home for x in ("咸草酱蟹", "大家烤肉", "DAYONE", "蓝憬通讯")))
check("home 第 4 张副标题：推荐好友领手机卡",
      "推荐2位好友注册 免费领韩国手机卡" in home)

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
