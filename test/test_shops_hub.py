"""商家页改版静态检查：python test/test_shops_hub.py

纯静态检查，不启服务、不需要 Postgres。放到仓库 test/ 后直接运行：
    python test/test_shops_hub.py

也可以用 LH_ROOT 指向输出目录做本地检查：
    LH_ROOT=/path/to/out python test_shops_hub.py
"""
import os
import re

ROOT = os.environ.get("LH_ROOT") or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ok = fail = skip = 0


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS", name)
    else:
        fail += 1
        print("FAIL", name, str(info)[:400])


def read_first(name, required=True):
    """在 static/、out/、仓库根目录里找文件，兼容输出目录本地自测。"""
    for base in (os.path.join(ROOT, "static"), os.path.join(ROOT, "out"), ROOT):
        p = os.path.join(base, name)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
    if required:
        check("找不到文件 %s" % name, False, ROOT)
    return ""


def read_snippet_or_merged():
    for p in (os.path.join(ROOT, "community_snippets.py"),
              os.path.join(ROOT, "out", "community_snippets.py"),
              os.path.join(ROOT, "community.py")):
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                return f.read(), p
    return "", ""


shops = read_first("shops.html")
newpost = read_first("community-new.html")
services = read_first("services.html", required=False)
partner = read_first("partner.html", required=False)
seo = read_first("seo.py", required=False)

# ---------- 1. 标题、副标题、旧模块 ----------
check("shops <title> 是商家", "<title>商家 · 旅韩助手</title>" in shops)
check("shops <h1> 是商家", re.search(r"<h1[^>]*>\s*商家\s*</h1>", shops) is not None)
check("shops 不再以「找商家」作标题或页脚", "找商家" not in shops)
check("shops 无「精选推荐和商家服务」副标题", "精选推荐和商家服务" not in shops and "商家推荐" not in shops)
check("shops meta description 已同步",
      'name="description"' in shops and "商家服务" in shops and "精选商家" not in shops and "找商家" not in shops.split("</head>")[0])

check("shops 没有旧 shoptabs", "shoptabs" not in shops)
for old in ("推荐店铺", "服务商家", "韩友圈商家", "还没有通过审核的服务商"):
    check("shops 没有旧文案/模块：%s" % old, old not in shops)

check("shops 没有 #ub 个人卡容器", re.search(r'\bid\s*=\s*["\']ub["\']', shops) is None)
check("shops 没有 class=\"card userbar\"", 'class="card userbar"' not in shops)
check("shops 没有 LH.userbar(带参数) 的个人卡调用",
      all(not a.strip() for a in re.findall(r"LH\.userbar\s*\(([^)]*)\)", shops)))

# ---------- 2. 精选商家广告位：只用已有 4 张 ----------
check("shops 无「精选商家」和左右滑动提示", "精选商家" not in shops and "左右滑动查看" not in shops and "featTitle" not in shops)
for i in (1, 2, 3, 4):
    check("shops 使用已有首页广告 ad-%d" % i, ("/static/home-ads/ad-%d.jpg?v=97" % i) in shops)
check("shops 恰好 4 张广告卡（没有第五家）", shops.count('class="ad-card"') == 4, shops.count('class="ad-card"'))
for name in ("咸草酱蟹", "大家烤肉", "DAYONE", "蓝憬通讯"):
    check("shops 广告店名：%s" % name, name in shops)
check("shops 咸草酱蟹链接", 'href="/food#hamcho-myeongdong"' in shops)
check("shops 大家烤肉链接", 'href="/food#daega-myeongdong"' in shops)
check("shops DAYONE 链接到医美分类（不再指回本页）", re.search(r'class="ad-card" href="/shops\?cat=clinic"', shops) is not None)
check("shops 蓝憬通讯链接与 data-ad", 'data-ad="lanjing"' in shops and 'href="/ads/lanjing"' in shops)
check("shops 蓝憬通讯副标题", "推荐2位好友注册 免费领韩国手机卡" in shops)
for fake in ("星巴克", "广藏市场", "明洞乐天", "第五家", "示例商家"):
    check("shops 没有编造店名：%s" % fake, fake not in shops)

# ---------- 3. 商家服务（无信息流） ----------
check("shops 有「商家服务」", "商家服务" in shops and "feedTitle" in shops)
for key in ("guide", "clinic", "food", "airport", "hotel", "interp",
            "kpop", "car", "sim", "logistics", "daigou", "other"):
    check("shops 分类 key：%s" % key, ('data-cat="%s"' % key) in shops)
for label in ("地陪向导", "医美", "美食代点外卖", "接送机", "住宿", "翻译",
              "追星活动", "租车", "通讯电话卡", "物流快递", "代购", "其它"):
    check("shops 分类文案：%s" % label, label in shops)
check("shops 无「留学签证」分类且韩国生活仍有签证留学",
      "留学签证" not in shops and 'data-cat="agency"' not in shops
      and "/community?board=visa" in shops and "/community?board=study" in shops)
check("shops 商家分类顺序",
      [m.group(1) for m in __import__("re").finditer(r'data-cat="([^"]+)"', shops)]
      == ["guide","clinic","food","airport","hotel","interp","kpop","car","sim","logistics","daigou","other"])
check("shops 分类与韩国生活同为三列图标网格",
      ".icon-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))" in shops
      and shops.count('class="icon-tile"') == 18
      and "overflow-x:auto" not in shops.split(".icon-grid")[1].split("}")[0])
check("shops 精选商家广告条仍可左右滑动", "ad-strip" in shops and "scroll-snap-type" in shops and "overflow-x:auto" in shops)
check("shops 无截断旧文案 餐饮美食（炸鸡等）", "餐饮美食（炸鸡等）" not in shops)
check("shops 页面本身不再拉商家信息流", "/api/community/merchant-feed" not in shops and 'id="feed"' not in shops)
check("shops 无「全部」分类钮", 'data-cat="all"' not in shops and ">全部<" not in shops)
check("shops 无入驻按钮旧文案", "我是商家，申请入驻/发布" not in shops)
check("shops 有我要入驻·看教程", "我要入驻·看教程" in shops and 'href="/shops/join-guide"' in shops)
check("shops 列表容器是 svcList 不是 feed id", 'id="svcList"' in shops and 'id="feed"' not in shops)
check("shops 商家服务下方是韩国生活", 'id="localTitle"' in shops and "韩国生活" in shops and shops.index('id="localTitle"') > shops.index('id="feedTitle"'))
check("shops 韩国生活复用六个频道卡片",
      all(w in shops for w in ("韩友圈", "租房", "招聘 · 工作", "二手 · 闲置", "签证", "留学"))
      and all(u in shops for u in ("/community", "/community?board=rent", "/community?board=jobs",
                                   "/community?board=used", "/community?board=visa", "/community?board=study")))
check("shops 正文不再出现行程安排区块",
      'id="tripTitle"' not in shops and "按天安排" not in shops and "查看独立页面" not in shops
      and 'href="/trips"' not in shops.split("<nav")[0])

check("shops 分类默认可点选且默认不选中",
      'id="catChips"' in shops and 'href="/shops?cat=guide"' in shops
      and 'class="on"' not in shops.split('id="catChips"')[1].split("</div>")[0])
shops_js = read_first("shops.js")
check("shops.js 会按分类加载列表", "getElementById('catChips')" in shops_js and "/api/providers" in shops_js and "/api/community/merchant-feed" in shops_js and "/api/shops/partners" in shops_js and "/api/shop-examples/" in shops_js and "示范案例" in shops_js)

check("shops 韩国生活无导语框且无查看韩友圈链接",
      "loc-hero" not in shops and "韩友圈是在韩国生活的交流入口" not in shops
      and "查看韩友圈" not in shops and 'id="localTitle"' in shops)
life = shops.split('aria-label="韩国生活频道"', 1)[1].split("</div>", 1)[0]
check("shops 韩国生活卡片顺序 租房/招聘/二手/签证/留学/韩友圈",
      re.findall(r'href="([^"]+)"', life) == [
          "/community?board=rent", "/community?board=jobs", "/community?board=used",
          "/community?board=visa", "/community?board=study", "/community"])
check("shops 没有旧推荐/服务商 JS 残留",
      all(x not in shops for x in ("setTab", "svcTabs", "/api/providers", "/api/shops", "typechips")))
check("shops 忽略旧 tab/type 参数，不做重定向",
      "tab=services" not in shops and "location.replace" not in shops
      and not re.search(r"location\.href\s*=\s*['\"]/shops", shops))

# ---------- 4. 页面基础依赖与免责声明 ----------
check("shops 有 banner 位", '<div data-banner-slot="shops"></div>' in shops)
check("shops 用汉江青 theme-color", 'name="theme-color" content="#146E68"' in shops)
check("shops 加载 pwa.css / community.css / community.js / banners.js / shops.js",
      all(x in shops for x in ("/static/pwa.css?v=97", "/static/community.css?v=97",
                               "/static/community.js?v=97", "/static/banners.js", "/static/shops.js?v=97")))
check("shops 加载 pwa.js?v=97 defer", '<script src="/static/pwa.js?v=97" defer></script>' in shops)
check("shops 保留底部导航", "<nav" in shops and "底部导航" in shops)
check("shops 免责声明完整",
      all(w in shops for w in ("免费信息网站", "仅供参考", "不是旅行社", "不代办签证")))
check("shops 协议链接完整", all(x in shops for x in ('href="/terms"', 'href="/privacy"', 'href="/rules"')))
check("shops 页脚「商家」仍链 /shops", re.search(r'href="/shops">商家</a>', shops) is not None)

# ---------- 5. 发帖页商家分类与医疗提示 ----------
check("community-new 有 merchant_cat", "merchant_cat" in newpost)
check("community-new 有商家分类选择器", 'id="mcat"' in newpost and "商家分类" in newpost)
check("community-new 商家分类在 listing 发布页显示",
      "listing" in newpost and "mcatbox" in newpost and "isMerchant" in newpost and "d.user.merchant" in newpost)
check("community-new 普通发帖页无商家专区板块",
      "商家专区（仅限商家会员）" not in newpost and "chip-lab" not in newpost)
check("community-new 提交时 append merchant_cat", "fd.append('merchant_cat'" in newpost)
check("community-new 有医疗中介提示", "不提供医疗中介" in newpost)
check("community-new 有价格/疗效承诺提示", "不要发布价格承诺或疗效承诺" in newpost)
check("community-new 保留先审后发提示", "先审后发" in newpost)
check("community-new 保留禁止站外联系方式提示",
      "请勿填写微信" in newpost and "二维码" in newpost)

# ---------- 6. 后端合并片段 ----------
snip, snip_path = read_snippet_or_merged()
if not snip:
    skip += 1
    print("SKIP 后端片段：没找到 community_snippets.py 或 community.py")
else:
    check("片段有 MIGRATE7", "MIGRATE7" in snip)
    check("MIGRATE7 幂等加 merchant_cat 列",
          re.search(r"ALTER\s+TABLE\s+c_posts\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+merchant_cat\s+TEXT", snip, re.I) is not None)
    check("MIGRATE7 有商家信息流部分索引",
          "c_posts_merchant_feed_idx" in snip and "WHERE is_merchant AND NOT deleted AND status='published'" in snip)
    check("片段没有把帖子改成 published 的 UPDATE",
          re.search(r"UPDATE\s+c_posts\s+SET[^;]{0,300}?status\s*=\s*'published'", snip, re.I | re.S) is None)
    check("片段有 merchant-feed 路由", '"/api/community/merchant-feed"' in snip or "/api/community/merchant-feed" in snip)
    check("片段返回 has_more/cats", "has_more" in snip and '"cats"' in snip)
    check("非法分类返回 400", 'raise HTTPException(400, "分类不存在")' in snip)
    check("api_new_post 增加 Form 参数", 'merchant_cat: str = Form("")' in snip)
    check("非商家选分类返回 400", "开通商家" in snip and "选择商家分类" in snip and "HTTPException(400" in snip)
    check("商家分类入库前校验", ("MERCHANT_CAT_KEYS" in snip or "MERCHANT_STORE_KEYS" in snip)
          and "分类不存在" in snip)
    check("后端含住宿/接送机/翻译，无留学签证分类",
          all(k in snip for k in ('("hotel", "住宿")', '("airport", "接送机")',
                                 '("interp", "翻译")'))
          and '("agency", "留学签证")' not in snip)
    check("后端接送机/翻译按 tag 兜底筛选", "CAT_TAG_FALLBACK" in snip and "p.tag=%s" in snip)
    check("医美分类继续调用 guard_medical", '== "clinic"' in snip and "guard_medical(" in snip)
    check("商家帖先审后发", 'status, note = "pending", "商家帖，先审后发"' in snip)

# ---------- 7. 其它页面小改 ----------
if services:
    check("services.html 跳到 /shops", "location.replace('/shops')" in services and "/shops?tab=services" not in services)
    check("services.html 可见文案不含找商家", "找商家" not in services)
else:
    skip += 1
    print("SKIP services.html 不存在")
if partner:
    check("partner.html 文案「去商家页看看」", "去商家页看看" in partner)
    check("partner.html 链到 /shops", 'href="/shops"' in partner)
    check("partner.html 无「找商家」", "找商家" not in partner)
else:
    skip += 1
    print("SKIP partner.html 不存在")
if seo:
    check("seo /shops 标题是商家", '"商家 · 旅韩助手"' in seo)
    check("seo /shops 描述含商家服务且无精选商家",
          "商家服务" in seo and "精选商家" not in seo and "商家推荐" not in seo)
    check("seo 不再用找商家作 /shops 标题", "找商家 · 旅韩助手" not in seo)
else:
    skip += 1
    print("SKIP seo.py 不存在")

# ---------- 8. 禁词与版本 ----------
SUPERLATIVES = ("最好", "最佳", "最强", "最全", "最低价", "最便宜", "第一名", "全网第一", "唯一")
for name, txt in (("shops.html", shops), ("community-new.html", newpost)):
    check("%s 无夸张承诺用语" % name, all(w not in txt for w in SUPERLATIVES),
          [w for w in SUPERLATIVES if w in txt])
    # 允许「请勿公开微信号」这类禁止性提示；只查主动引流写法。
    wx_solicit = ("加微信号", "微信号：", "微信号:", "加微信", "微信联系", "vx:", "VX:")
    check("%s 无微信号引流" % name, all(w not in txt for w in wx_solicit),
          [w for w in wx_solicit if w in txt])
for name, txt in (("shops.html", shops), ("community-new.html", newpost)):
    vers = re.findall(r"\?v=(\d+)", txt)
    check("%s 缓存版本一致" % name, bool(vers) and set(vers) == {"97"}, set(vers))

print("%s passed, %s failed, %s skipped" % (ok, fail, skip))
if fail:
    raise SystemExit(1)
