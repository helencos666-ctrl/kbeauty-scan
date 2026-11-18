"""纯静态检查：韩友圈首页顶栏为单行纯文字 关注 / 发现 / 同城。"""
import os
import re

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


def block(src, pattern):
    m = re.search(pattern, src, re.S)
    return m.group(0) if m else ""


def rule(css, selector):
    m = re.search(r"(?:^|\n)" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else ""


html = read("community.html")
css = read("community.css")

xbar = block(html, r'<header class="xbar">.*?</header>')
check("community.html 有 header.xbar", bool(xbar))
check("不再使用 header.thero 和绿色标题「🧭 韩友圈」",
      "header.thero" not in html and "🧭 韩友圈" not in html)
check("顶栏没有菜单或搜索按钮",
      "searchBtn" not in html and "xbar-side" not in html and "☰" not in xbar, xbar)
check("顶栏没有胶囊或筛选条",
      all(w not in html for w in ("areaPanel", "areaChips", "地图探索", "位置距离", "citySwitch", "searchBar", "CityWalk")))

tabs = block(html, r'<div class="xtabs" id="tabs".*?</div>')
check("community.html 有 #tabs 容器", bool(tabs))
buttons = re.findall(r"<button\b([^>]*)>(.*?)</button>", tabs, re.S)
check("#tabs 里正好三个 button", len(buttons) == 3, [b[1] for b in buttons])
data_ts = [re.search(r'data-t="([^"]+)"', a).group(1)
           for a, _ in buttons if re.search(r'data-t="([^"]+)"', a)]
check("tab 顺序为 follow / discover / area",
      data_ts == ["follow", "discover", "area"], data_ts)
texts = [re.sub(r"<[^>]+>", "", t).strip() for _, t in buttons]
check("tab 文案为 关注 / 发现 / 同城",
      texts == ["关注", "发现", "同城"], texts)
check("旧 tab 推荐/精选/刚发布/我关注已从 tab 栏移除",
      all(w not in tabs for w in ("推荐", "精选", "刚发布", "我关注")))
check("整页不再出现「实用干货」或「我关注的」",
      "实用干货" not in html and "我关注的" not in html)
check("信息流改为双列瀑布流", "appendWater" in html and "waterItem" in read("community.js") and ".wcard{" in read("community.css"))
check("默认 tab 为 discover",
      "LH.qs('tab') || 'discover'" in html or "LH.qs('tab')||'discover'" in html)

fb = re.search(r"tab\s*===\s*'news'\s*\|\|\s*tab\s*===\s*'hot'", html)
check("有 ?tab=news / ?tab=hot 的回退判断", bool(fb))
if fb:
    seg = html[fb.start():fb.start() + 500]
    check("旧链接回退后把 tab 设为 discover", "tab = 'discover'" in seg, seg[:250])
    check("旧链接回退后用 replaceState 回到 /community",
          "replaceState" in seg and "'/community'" in seg, seg[:250])

allowed = re.search(r"ALLOWED_TABS\s*=\s*\[([^\]]*)\]", html)
allowed_list = re.findall(r"'([^']+)'", allowed.group(1)) if allowed else []
check("允许的 tab 列表含 discover/follow/area/topic",
      all(t in allowed_list for t in ("discover", "follow", "area", "topic")), allowed_list)
check("?tab=topic 不被回退（topic 仍在允许列表）", "topic" in allowed_list, allowed_list)
check("往期话题链接 /community?tab=topic 仍保留", "/community?tab=topic" in html)
check("?tab=follow 未登录仍留在 follow",
      "tab === 'follow'" in html and "/login?next=/community%3Ftab%3Dfollow" in html)
load_fn = html[html.find("async function load(reset)"):html.find("async function loadTopics")]
check("关注空态不再用大块 empty 或推荐回退",
      "还没有内容。去" not in html and "先看看推荐" not in html
      and "followNeedRec" not in html and "function recUrl" not in html
      and "还没关注任何人" in html and "followHint" in load_fn)
check("关注空态只用一行 follow-hint，无推荐瀑布流回退",
      "follow-hint" in html and "showFeed" in load_fn)
check("appendWater 有缺失时的兜底，避免信息流被错误卡片清空",
      "typeof LH.appendWater !== 'function'" in html
      and "function showFeed" in html
      and "LH.appendWater = function" in html)
check("有帖时走 showFeed / appendWater 瀑布流",
      "showFeed(listEl, items, reset, lead)" in load_fn)
check("社区约定卡片保留",
      'id="rules"' in html and "社区约定（简版）" in html and "阅读完整社区约定" in html)
check(".follow-hint 是单行细提示，不是大块 empty",
      bool(rule(css, ".follow-hint")) and "padding:0" in (rule(css, ".follow-hint") or "")
      and "28px" not in (rule(css, ".follow-hint") or ""), rule(css, ".follow-hint"))

check("发现页默认不展开今日热议等栏目", "homeBlocks').style.display = 'none'" in html)
check("社区信息流不再有写帖子悬浮按钮", 'id="fab"' not in html and "fab pill" not in html)
check("发帖入口仍指向 /community/new（底栏发布）", "/community/new" in html or "/community/new" in read("pwa.js"))
check("同城仍有地理定位与城市回退首尔",
      "geolocation" in html and "首尔" in html and "area=" in html)

check("community.html 不再有 #notiIc", "notiIc" not in html)
check("community.html 不再有 #inboxIc", "inboxIc" not in html)
check("不再给通知 / 私信节点插未读角标",
      "notif_unread" not in html and "user.unread" not in html)
check("仍保留全部频道入口 /community/boards", "/community/boards" in html)

xbar_css = rule(css, "header.xbar")
on_css = rule(css, ".xtabs button.on")
btn_css = rule(css, ".xtabs button")
tabs_css = rule(css, ".xtabs")
check("header.xbar 为浅色顶栏",
      bool(xbar_css) and "background:#fff" in xbar_css.replace(" ", ""), xbar_css)
check("未激活 tab 为灰色", "#8a8a8a" in (btn_css or ""), btn_css)
check("激活 tab 加粗且深色",
      "font-weight:700" in (on_css or "").replace(" ", "") and "#111" in (on_css or ""), on_css)
check("激活 tab 有红色下划线",
      "#FF2442" in (rule(css, ".xtabs button.on::after") or ""))
check("tab 不换行、无胶囊样式",
      "nowrap" in (tabs_css or "") and "nowrap" in (btn_css or "")
      and "border-radius:0" in (btn_css or "") and "background:none" in (btn_css or "").replace(" ", ""),
      (tabs_css, btn_css))
# 390px: 三组两字 + 两档间距，远小于视口
fs = float(re.search(r"font-size:\s*([\d.]+)px", btn_css).group(1))
gap = float(re.search(r"gap:\s*([\d.]+)px", tabs_css).group(1))
row = fs * 2 * 3 + gap * 2
check("390px 宽下一行排得下三个标签（约 %.0fpx）" % row, row <= 300, row)
check("未改全局 header 规则（仍是无 env 的 padding:20px 18px 30px）",
      re.search(r"(?:^|\n)header\{[^}]*padding:20px 18px 30px", css) is not None)
check("顶栏安全区只作用于 header.xbar",
      "env(safe-area-inset-top)" in xbar_css, xbar_css)
check("main.home 单独微调上边距", bool(rule(css, "main.home")))

check("community.html 使用 ?v=89", html.count("?v=89") >= 4 and "?v=81" not in html)
check("未新增外链 http(s) 资源",
      "http://" not in html and "https://" not in html.replace("https://schema.org", ""))

BAN = re.compile(r"最好的|最佳|最强|最便宜|最低价|第一名|全网第一|全国第一|唯一指定|唯一官方|独家|唯一")
m = BAN.search(xbar + tabs)
check("新增的可见文案无「最 / 第一 / 唯一」类禁词", m is None, m.group(0) if m else "")
check("顶部文案不含微信 / 二维码", "微信" not in xbar and "二维码" not in tabs)


post = read("community-post.html")
check("帖子详情有顶图轮播和底栏", 'id="hero"' in post and 'class="pbar"' in post and "说点什么" in post and "phero" in post)
check("帖子详情有点赞收藏评论", 'id="like"' in post and 'id="fav"' in post and "/favorite" in post and "猜你想搜" in post)
newpost = read("community-new.html")
check("普通写帖子只保留照片、标题、正文和昵称",
      'id="title"' in newpost and 'id="body"' in newpost and 'id="pick"' in newpost
      and 'id="composeWho"' in newpost and "d.user.nickname" in newpost
      and 'id="send"' in newpost
      and "标题（选填）" in newpost and "正文（选填）" in newpost)
check("普通写帖子无商家专区板块", "商家专区（仅限商家会员）" not in newpost and "chip-lab" not in newpost)
check("普通写帖子去掉频道、话题和地点选项，默认 daily",
      "发到哪个频道" not in newpost and "地点（选填）" not in newpost
      and 'id="board" value="daily"' in newpost
      and 'id="chanbox" style="display:none"' in newpost
      and 'id="topicbox"' in newpost and 'id="placebox"' in newpost
      and "请选择频道" not in newpost
      and "document.getElementById('board').value = 'daily'" in newpost)
check("普通写帖子标题和正文之间有可选分类标签",
      'id="showCats"' in newpost and 'id="showCat"' in newpost
      and 'data-cat="food"' in newpost and ">美食<" in newpost
      and ">购物<" in newpost and ">景点<" in newpost and ">酒店<" in newpost
      and ">咖啡推荐<" in newpost and ">自拍<" in newpost
      and "function applyShowCat" in newpost and "fd.append('show_cat'" in newpost
      and newpost.find('id="tcount"') < newpost.find('id="showCats"')
      and newpost.find('id="showCats"') < newpost.find('id="blab"'))
check("选美食等分类时出现店名、区域和人均字段，自拍或不选则隐藏",
      'id="spotBox"' in newpost and "店名/地名" in newpost
      and "地址或区域（选填）" in newpost and "人均（选填）" in newpost
      and 'id="spotName"' in newpost and 'id="spotArea"' in newpost and 'id="avgPrice"' in newpost
      and "SPOT_CATS" in newpost and "请填写店名/地名" in newpost
      and 'id="spotBox" hidden' in newpost
      and newpost.find('id="showCats"') < newpost.find('id="spotBox"')
      and newpost.find('id="spotBox"') < newpost.find('id="blab"'))
js_w = read("community.js")
post_h = read("community-post.html")
check("无标题帖在瀑布流不渲染空标题节点", "(title ? '<div class=" + chr(34) + "wtitle" + chr(34) + ">'" in js_w)
check("详情页无标题时不渲染空 h1", "(p.title ? '<h1 class=" + chr(34) + "ptitle" + chr(34) + ">'" in post_h)
check("瀑布流卡片可展示店名和区域", "wspot" in js_w and "spot_name" in js_w and "spot_area" in js_w)
check("帖子详情可展示店名、区域和人均", "pspot" in post_h and "人均 ₩" in post_h and "spot_name" in post_h)
check("商家发布走 listing 模式", "isListingMode" in newpost and "listing=1" in newpost and "CAT_TO_BOARD" in newpost and "MCAT_TPL" in newpost)
me = read("me.html")
check("我的页有商家发布信息入口", "商家发布信息" in me and "/community/new?listing=1" in me)
pwa_js = read("pwa.js")
pwa_css = read("pwa.css")
check("底栏+发布弹出操作表",
      "从相册选择" in pwa_js and "拍摄" in pwa_js and "写文字" in pwa_js and "取消" in pwa_js
      and "openPubSheet" in pwa_js and 'data-nav="post"' in pwa_js)
check("操作表含相册多选和拍摄",
      "accept = 'image/*'" in pwa_js and "multiple = true" in pwa_js and "capture" in pwa_js)
check("操作表样式为底部圆角白行",
      "lh-pub-mask" in pwa_css and "lh-pub-group" in pwa_css and "border-radius:14px" in pwa_css)
check("发帖页可预载操作表选中的图片", "loadPendingImages" in newpost and "lh-compose-files" in newpost)
js = read("community.js")
check("瀑布流封面用 IntersectionObserver 懒加载", "IntersectionObserver" in js and "data-src" in js and "function waterItem" in js and "loading=\"lazy\"" not in js[js.find("function waterItem"):js.find("function bindWaterLazy")])
check("瀑布流卡片无置顶精选摘要评论数和宫格", "function waterItem" in js and "置顶" not in js[js.find("function waterItem"):js.find("function bindWaterLazy")] and "精选" not in js[js.find("function waterItem"):js.find("function bindWaterLazy")] and "comments" not in js[js.find("function waterItem"):js.find("function bindWaterLazy")])
check("信息流缩略图不再用 loading=lazy", "function postPhotos" in js and "loading=\"lazy\"" not in js[js.find("function postPhotos"):js.find("function postItem")])
check("底栏关注为爱心图标且保留关注文案", "icon:'❤️', label:'关注'" in pwa_js and "👥" not in pwa_js and "<i>❤️</i>关注" in me)

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
