"""首页三张核心卡「旅韩三件套」强调样式静态检查。
python test/test_home_core_emphasis.py"""
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

index = read("static", "index.html")
home = index.split('id="p-home"')[1].split('id="p-cos"')[0]
coreblk = home.split('<div class="core">', 1)[1].split("<!-- B.", 1)[0]

def rule(sel):
    m = re.search(re.escape(sel) + r"\{([^}]*)\}", index)
    return m.group(1) if m else ""

a, b, span, ico = rule(".core a"), rule(".core b"), rule(".core span"), rule(".core .ico")

check("三卡数量为 3", coreblk.count("<a href=") == 3)
check("跳转：路线", 'href="/?tab=sub" data-go="sub"' in coreblk)
check("跳转：翻译", 'href="/?tab=card" data-go="card"' in coreblk)
check("跳转：扫描识别", 'href="/?tab=cos" data-go="cos"' in coreblk)
check("未引入 /scan", 'href="/scan"' not in index)
check("标题文案不变", all("<b>%s</b>" % t in coreblk for t in ("路线", "翻译", "美妆识别")))
check("副标题文案不变", all("<span>%s</span>" % t in coreblk for t in ("地铁公交", "沟通卡片", "拍照查成分")))
check("没有改成扫码", "扫码" not in index)
check("小标题 旅韩三件套", 'class="core-lab"' in home and ">旅韩三件套<" in home)
check("禁用词未出现", not any(w in home for w in ("最强", "第一", "唯一")))
check("未出现微信/二维码", "微信" not in home and "二维码" not in home)

check("三列 grid", "grid-template-columns:1fr 1fr 1fr" in rule(".core"))
check("标题单行截断", "white-space:nowrap" in b)
check("副标题单行截断", "white-space:nowrap" in span)
check("卡片 min-width:0", "min-width:0" in a)

check("深绿渐变底", "linear-gradient" in a and ("#146E68" in a and "#0F5A55" in a))
check("非白底/浅描边", "background:#fff" not in a and "#F0F6F5" not in a)
check("加重绿色阴影", "rgba(20,110,104,.28)" in a)
check("标题白色", "color:#fff" in b)
check("副标题实色浅白", ("#E6F3F1" in span or "#F3FAF9" in span) and "var(--sub)" not in span)
check("标题 15px / 800", "font-size:15px" in b and "font-weight:800" in b)
check("图标白色", "color:#fff" in ico and "rgba(255,255,255,.18)" in ico)
check("图标尺寸 36×36", "width:36px" in ico and "height:36px" in ico)
check("ico 内为 inline svg", coreblk.count("<svg") == 3 and "<svg" in coreblk)
check("ico 不再含 emoji", not any(e in coreblk for e in ("🚇", "💬", "📷")))
check("svg 均 aria-hidden", coreblk.count('aria-hidden="true"') == 3)
svgs = re.findall(r"<svg[^>]*>.*?</svg>", coreblk, re.S)
check("路线/翻译仍为白色描边", len(svgs)==3 and all('stroke="currentColor"' in s for s in svgs[:2]))
check("美妆图标为彩色口红", len(svgs)==3 and "#E23B4C" in svgs[2] and "#E2B84A" in svgs[2] and "#3B2A16" in svgs[2])
check("美妆图标不用 currentColor", len(svgs)==3 and "currentColor" not in svgs[2])
check("美妆图标白底瓷砖", ".core .ico-lip" in index and "background:#fff" in rule(".core .ico-lip"))
check("第三卡 ico-lip 类名", 'class="ico ico-lip"' in coreblk)
check("标题副标题未改", "<b>美妆识别</b><span>拍照查成分</span>" in coreblk)

narrow = index[index.find("@media (max-width:359px){"):]
check("窄屏图标 32×32", "width:32px" in narrow and "height:32px" in narrow)
check("窄屏标题 14px", ".core b{font-size:14px}" in narrow)
check("窄屏 .cats 未改动", ".cats i{width:46px;height:46px;font-size:21px}" in narrow
      and ".cats a{font-size:11px}" in narrow)

check("使用 ?v=84", "?v=84" in index)
check("没有残留 ?v=69", "?v=69" not in index)
for label, needle in (("吃喝玩乐", "吃喝玩乐"), ("广告", "adsDots"),
                      ("吃喝玩乐推荐卡片", "playGrid"), ("tabbar", 'class="tabbar"')):
    check("保留 " + label, needle in index)

# 375px：首页 main 后段规则为 padding:0，左右只扣 .core-wrap 的 16px
wrap_w = 375 - 2 * 16
col_w = (wrap_w - 2 * 8) / 3
inner = col_w - 2 * 6
title_w = 4 * 15
sub_w = 5 * 10
check("375px 卡片内容宽度足够", inner > title_w and inner > sub_w,
      "inner=%.1f title=%.0f sub=%.0f" % (inner, title_w, sub_w))

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
