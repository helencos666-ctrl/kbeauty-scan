"""纯静态检查：韩友圈首页 /community 顶部改为关注 / 发现 / 地区。"""
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
check("顶栏无副标题「去韩国前，问问走过的人」", "去韩国前，问问走过的人" not in html)
check("左侧有全部频道菜单",
      'href="/community/boards"' in xbar and "全部频道" in xbar, xbar[:300])
check("右侧有搜索按钮", 'id="searchBtn"' in xbar and "搜索" in xbar, xbar[:300])

tabs = block(html, r'<div class="xtabs" id="tabs".*?</div>')
check("community.html 有 #tabs 容器", bool(tabs))
buttons = re.findall(r"<button\b([^>]*)>(.*?)</button>", tabs, re.S)
check("#tabs 里正好三个 button", len(buttons) == 3, [b[1] for b in buttons])
data_ts = [re.search(r'data-t="([^"]+)"', a).group(1)
           for a, _ in buttons if re.search(r'data-t="([^"]+)"', a)]
check("tab 顺序为 follow / discover / area",
      data_ts == ["follow", "discover", "area"], data_ts)
texts = [re.sub(r"<[^>]+>", "", t).strip() for _, t in buttons]
check("tab 文案为 关注 / 发现 / 地区（默认首尔）",
      texts[0] == "关注" and texts[1] == "发现" and "首尔" in texts[2], texts)
check("旧 tab 推荐/精选/刚发布/我关注已从 tab 栏移除",
      all(w not in tabs for w in ("推荐", "精选", "刚发布", "我关注")))
check("整页不再出现「实用干货」或「我关注的」",
      "实用干货" not in html and "我关注的" not in html)
check("「今日热议」栏目标题仍保留（发现页 section）", "今日热议" in html)
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
check("?tab=follow 未登录仍留在 follow（login_required 提示未改）",
      "/login?next=/community%3Ftab%3Dfollow" in html)
check("发现页仍调用 /api/community/feed?tab=hot",
      re.search(r"/api/community/feed\?tab=hot", html) is not None)
check("发现页避坑栏目仍调用 feed?tab=latest&boards=pit,visa",
      "/api/community/feed?tab=latest&boards=pit,visa" in html)
check("旧「更多」链接 ?tab=news 仍在（打开后回退到发现）",
      "/community?tab=news" in html)
check("「避坑 & 签证新帖」栏目仍在",
      "避坑 &amp; 签证新帖" in html and 'id="sfList"' in html)
check("「今日热议」列表容器仍在",
      'id="hotList"' in html and 'id="hotTitle"' in html)
check("写帖子按钮保留", 'id="fab"' in html and "写帖子" in html)
check("仍有地理定位与城市回退首尔",
      "geolocation" in html and "首尔" in html)

# 地区 tab 附加区块
check("地区 tab 有分类芯片 全城/美食/咖啡/购物/景点/追星/展览演出/CityWalk",
      all(w in html for w in ("全城", "美食", "咖啡", "购物", "景点", "追星", "展览演出", "CityWalk"))
      and 'id="areaChips"' in html and 'id="areaPanel"' in html)
check("地区 tab 有地图探索与城市切换",
      "地图探索" in html and 'id="mapExplore"' in html and "⇋" in html and 'id="citySwitch"' in html)
check("地区 tab 有位置距离 全城/1km/3km/5km",
      'class="adist"' in html and all(w in html for w in ("位置距离", "1km", "3km", "5km")))
check("距离筛选会带上 lat/lng（有定位时）",
      "areaKm" in html and "userLat" in html and "&lat=" in html and "&lng=" in html)

check("community.html 不再有 #notiIc", "notiIc" not in html)
check("community.html 不再有 #inboxIc", "inboxIc" not in html)
check("不再给通知 / 私信节点插未读角标",
      "notif_unread" not in html and "user.unread" not in html)
check("仍保留全部频道入口 /community/boards", "/community/boards" in html)

xbar_css = rule(css, "header.xbar")
on_css = rule(css, ".xtabs button.on")
check("header.xbar 为浅色顶栏",
      bool(xbar_css) and "background:#fff" in xbar_css.replace(" ", ""), xbar_css)
check("激活 tab 加粗", "font-weight:800" in on_css.replace(" ", ""), on_css)
check("激活 tab 有红色下划线",
      "#FF2442" in (rule(css, ".xtabs button.on::after") or "") or "#FF2442" in css)
check("未改全局 header 规则（仍是无 env 的 padding:20px 18px 30px）",
      re.search(r"(?:^|\n)header\{[^}]*padding:20px 18px 30px", css) is not None)
check("顶栏安全区只作用于 header.xbar",
      "env(safe-area-inset-top)" in xbar_css, xbar_css)
check("main.home 单独微调上边距", bool(rule(css, "main.home")))
check("位置距离选中态为红描边",
      "#FF2442" in (rule(css, ".adist button.on") or ""), rule(css, ".adist button.on"))

check("community.html 使用 ?v=81", html.count("?v=81") >= 4 and "?v=80" not in html)
check("未新增外链 http(s) 资源",
      "http://" not in html and "https://" not in html.replace("https://schema.org", ""))

BAN = re.compile(r"最好的|最佳|最强|最便宜|最低价|第一名|全网第一|全国第一|唯一指定|唯一官方|独家|唯一")
m = BAN.search(xbar + tabs + block(html, r'<div id="areaPanel".*?</div>\s*<div id="districtSheet"'))
check("新增的可见文案无「最 / 第一 / 唯一」类禁词", m is None, m.group(0) if m else "")
check("顶部文案不含微信 / 二维码", "微信" not in xbar and "二维码" not in tabs)

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
