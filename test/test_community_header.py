"""纯静态检查：韩友圈首页 /community 顶部已精简。

覆盖：
1. header.thero 只剩标题「韩友圈」，没有副标题「去韩国前，问问走过的人」
2. #tabs 只保留 推荐 / 精选 / 刚发布 / 我关注 四个 button（顺序固定）
3. 旧链接 ?tab=news / ?tab=hot 回退到 recommend，?tab=topic 不回退；
   推荐页仍调用 /api/community/feed?tab=hot 与 tab=latest&boards=pit,visa；旧链接 ?tab=news 保留并回退
4. 顶部不再有 #notiIc / #inboxIc，仍保留 /community/boards
5. .seg 四卡在 375px 下不横滑（flex:1 1 0 + min-width:0 + nowrap，且不再 overflow-x:auto）

不需要启服务，直接 python test/test_community_header.py 即可。
测试读取仓库根目录下的 static/ 文件（ROOT 约定与 test_no_userbar.py 相同）；
拷到仓库 test/ 目录后运行。
"""
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
    """按正则截一段 HTML；找不到返回空串。"""
    m = re.search(pattern, src, re.S)
    return m.group(0) if m else ""


def rule(css, selector):
    """取行首选择器的规则体（避免 .seg 命中 .segwrap .seg）。"""
    m = re.search(r"(?:^|\n)" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else ""


html = read("community.html")
css = read("community.css")

# ---------- 1. 顶部只剩标题 ----------
thero = block(html, r'<header class="thero">.*?</header>')
check("community.html 有 header.thero", bool(thero))
check("header.thero 含标题「韩友圈」", "韩友圈" in thero, thero[:200])
check("header.thero 有 h1", r"<h1>" in thero and "🧭" in thero)
check("header.thero 不再有副标题「去韩国前，问问走过的人」",
      "去韩国前，问问走过的人" not in thero, thero[:200])
check("header.thero 不再有副标题 <p>", "<p" not in thero, thero[:200])
check("header.thero 里除「全部频道」外没有别的图标按钮",
      len(re.findall(r"<a\b", thero)) == 1, re.findall(r"<a\b[^>]*>", thero))

# ---------- 2. tab 栏只剩四个 ----------
tabs = block(html, r'<div class="seg" id="tabs">.*?</div>')
check("community.html 有 #tabs 容器", bool(tabs))
buttons = re.findall(r"<button\b([^>]*)>(.*?)</button>", tabs, re.S)
check("#tabs 里正好四个 button", len(buttons) == 4, [b[1] for b in buttons])

data_ts = [re.search(r'data-t="([^"]+)"', a).group(1)
           for a, _ in buttons if re.search(r'data-t="([^"]+)"', a)]
check("tab 顺序为 recommend / featured / latest / follow",
      data_ts == ["recommend", "featured", "latest", "follow"], data_ts)

texts = [re.sub(r"<[^>]+>", "", t).strip() for _, t in buttons]
check("tab 文案为 推荐 / 精选 / 刚发布 / 我关注",
      texts == ["推荐", "精选", "刚发布", "我关注"], texts)

check("tab 栏里没有「实用干货」", "实用干货" not in tabs)
check("tab 栏里没有「今日热议」", "今日热议" not in tabs)
check("tab 栏里没有「我关注的」", "我关注的" not in tabs)
check("「精选」不再带 🌟 前缀（375px 排版需要）",
      "🌟" not in tabs, tabs)
check("整页不再出现「实用干货」", "实用干货" not in html)
check("整页不再出现「我关注的」", "我关注的" not in html)
check("「今日热议」栏目标题仍保留（推荐页 section）", "今日热议" in html)
check("「我关注」tab 默认可见（没有 display:none / id=tFollow）",
      "tFollow" not in tabs and "display:none" not in tabs, tabs)

# ---------- 3. 旧链接回退 + feed 调用仍在 ----------
fb = re.search(r"tab\s*===\s*'news'\s*\|\|\s*tab\s*===\s*'hot'", html)
check("有 ?tab=news / ?tab=hot 的回退判断", bool(fb), html[:0])
if fb:
    seg = html[fb.start():fb.start() + 400]
    check("news / hot 回退后把 tab 设为 recommend", "tab = 'recommend'" in seg, seg[:200])
    check("news / hot 回退后用 replaceState 回到 /community",
          "replaceState" in seg and "'/community'" in seg, seg[:200])

allowed = re.search(r"ALLOWED_TABS\s*=\s*\[([^\]]*)\]", html)
allowed_list = re.findall(r"'([^']+)'", allowed.group(1)) if allowed else []
check("允许的 tab 列表存在", bool(allowed))
check("允许的 tab 列表为 recommend/featured/latest/follow/topic（不含 news、hot）",
      allowed_list == ["recommend", "featured", "latest", "follow", "topic"], allowed_list)
check("?tab=topic 不被回退（topic 仍在允许列表）", "topic" in allowed_list, allowed_list)
check("往期话题链接 /community?tab=topic 仍保留", "/community?tab=topic" in html)
check("?tab=follow 未登录仍留在 follow（login_required 提示未改）",
      "/login?next=/community%3Ftab%3Dfollow" in html)

check("推荐页仍调用 /api/community/feed?tab=hot",
      re.search(r"/api/community/feed\?tab=hot", html) is not None)
check("推荐页避坑栏目仍调用 feed?tab=latest&boards=pit,visa",
      "/api/community/feed?tab=latest&boards=pit,visa" in html)
check("旧「更多」链接 ?tab=news 仍在（打开后回退到推荐）",
      "/community?tab=news" in html)
check("「避坑 & 签证新帖」栏目仍在",
      "避坑 &amp; 签证新帖" in html and 'id="sfList"' in html)
check("「今日热议」列表容器仍在",
      'id="hotList"' in html and 'id="hotTitle"' in html)

# ---------- 4. 通知 / 私信图标已移除，全部频道保留 ----------
check("community.html 不再有 #notiIc", "notiIc" not in html)
check("community.html 不再有 #inboxIc", "inboxIc" not in html)
check("不再给通知 / 私信节点插未读角标",
      "notif_unread" not in html and "user.unread" not in html)
check("仍保留全部频道入口 /community/boards", "/community/boards" in html)
check("仍有「全部频道」按钮（🗂️）", "全部频道" in thero and "🗂️" in thero)

# ---------- 5. 375px 下四卡不横滑 ----------
seg = rule(css, ".seg")
seg_btn = rule(css, ".seg button")
check(".seg 规则存在", bool(seg))
check(".seg 不再横向滚动（无 overflow-x:auto）",
      "overflow-x" not in seg and "overflow-x" not in seg.replace(" ", ""),
      seg)
check(".seg 用 overflow:hidden 兜底", "overflow:hidden" in seg.replace(" ", ""), seg)
check(".seg button 用 flex:1 1 0 等分",
      re.search(r"flex:\s*1\s+1\s+0", seg_btn) is not None, seg_btn)
check(".seg button 有 min-width:0（可压缩到等分宽度）",
      re.sub(r"\s", "", seg_btn).find("min-width:0") >= 0, seg_btn)
check(".seg button 单行不换行（white-space:nowrap）",
      "nowrap" in seg_btn, seg_btn)
check(".seg button 字号 <= 14px",
      float(re.search(r"font-size:\s*([\d.]+)px", seg_btn).group(1)) <= 14, seg_btn)

# 375px 宽度账（与上面的 CSS 数值对应）：
#   视口 375 - main 左右各 16 内边距 = 343
#   343 - .seg 左右各 5 内边距 = 333
#   333 - 3 个 gap * 6 = 315；315 / 4 = 78.75px 每卡
#   每卡文本可用宽度 = 78.75 - 左右各 2 内边距 = 74.75px
VIEW, MAIN_PAD, SEG_PAD, GAP, BTN_PAD, FS = 375, 16, 5, 6, 2, 13.0
content = VIEW - MAIN_PAD * 2 - SEG_PAD * 2
per_btn = (content - GAP * 3) / 4.0
text_w = per_btn - BTN_PAD * 2
widest = max(len(t) for t in texts if t) * FS  # 汉字按 1em 估
check("375px 下四卡可等分排下（每卡 %.2fpx，文本可用 %.2fpx，最长标签约 %.0fpx）"
      % (per_btn, text_w, widest), per_btn >= 60 and text_w > widest,
      (per_btn, text_w, widest))

# ---------- 6. 不误伤全局规则 ----------
check("未改全局 header 规则（仍是无 env 的 padding:20px 18px 30px）",
      re.search(r"(?:^|\n)header\{[^}]*padding:20px 18px 30px", css) is not None)
check("顶栏压缩只作用于 header.thero",
      "env(safe-area-inset-top)" in rule(css, "header.thero"), rule(css, "header.thero"))
check("main.home 单独微调上边距", bool(rule(css, "main.home")))

# ---------- 7. 版本号与第三方资源未被改动 ----------
check("community.html 使用 ?v=76", html.count("?v=76") >= 4 and "?v=72" not in html)
check("未新增外链 http(s) 资源",
      "http://" not in html and "https://" not in html.replace("https://schema.org", ""))

BAN = re.compile(r"最好的|最佳|最强|最便宜|最低价|第一名|全网第一|全国第一|唯一指定|唯一官方|独家|唯一")
m = BAN.search(thero + tabs)
check("新增的可见文案无「最 / 第一 / 唯一」类禁词", m is None, m.group(0) if m else "")
check("顶部文案不含微信 / 二维码", "微信" not in thero and "二维码" not in tabs)

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
