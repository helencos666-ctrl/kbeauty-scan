"""纯静态检查：「我的 · 编辑资料」弹层不被底部五格导航遮挡，以及全站浮层层级。
不需要启服务，直接 python test/test_me_sheet.py 即可。

测试读取仓库根目录下的 static/ 文件；拷到仓库 test/ 目录后运行。
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


def rule(css, sel):
    """取选择器 sel 对应单条规则的声明块（要求 sel 后紧跟 {）。"""
    m = re.search(r"(?<![\w.#-])" + re.escape(sel) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else ""


def zindex(css, sel):
    body = rule(css, sel)
    m = re.search(r"z-index\s*:\s*(-?\d+)", body)
    return int(m.group(1)) if m else None


me_css = read("me.css")
me_html = read("me.html")
pwa_css = read("pwa.css")
comm_css = read("community.css")

# ---------- 1. 弹层高于底部导航 ----------
sheet_z = zindex(me_css, ".sheet")
check("me.css .sheet z-index 高于 60", sheet_z is not None and sheet_z > 60, sheet_z)
sheet_rule = rule(me_css, ".sheet")
check("me.css .sheet 仍是 fixed 全屏遮罩",
      "position:fixed" in sheet_rule and "inset:0" in sheet_rule, sheet_rule)
check("me.css .sheet 半透明遮罩", "rgba(0,0,0,.4)" in sheet_rule or "rgba(0,0,0,0.4)" in sheet_rule, sheet_rule)

nav_z = zindex(pwa_css, "nav.tabbar")
check("pwa.css nav.tabbar 仍为 z-index 60", nav_z == 60, nav_z)

# ---------- 2. 弹层可滚动 + 底部安全区 ----------
sheet_in = rule(me_css, ".sheet-in")
check("me.css .sheet-in 含 overflow", "overflow:auto" in sheet_in or "overflow: auto" in sheet_in, sheet_in)
check("me.css .sheet-in 含 env(safe-area-inset-bottom)",
      "env(safe-area-inset-bottom)" in sheet_in, sheet_in)
check("me.css .sheet-in 含 iOS 惯性滚动",
      "-webkit-overflow-scrolling:touch" in sheet_in, sheet_in)
check("me.css .sheet-in max-height 不被顶部安全区裁切",
      "85vh" in sheet_in and "env(safe-area-inset-top)" in sheet_in, sheet_in)
check("me.css .sheet-in 不额外叠加 58px 导航高度", "58px" not in sheet_in, sheet_in)
check("me.css .sheet 未用 pointer-events:none 盖住输入",
      "pointer-events:none" not in sheet_rule and "pointer-events:none" not in sheet_in, sheet_rule)
check("me.css 弹层内输入/按钮可点",
      "pointer-events:auto" in me_css and ".sheet input" in me_css, "")

# ---------- 3. me.html 打开/关闭弹层 ----------
check("me.html 打开弹层时加 lh-overlay-open",
      "classList.add('lh-overlay-open')" in me_html)
check("me.html 关闭弹层时移除 lh-overlay-open",
      "classList.remove('lh-overlay-open')" in me_html)
check("me.html 有 openSheet / closeSheet 两个函数",
      "function openSheet(" in me_html and "function closeSheet(" in me_html)
check("me.html 关闭按钮调用 closeSheet", "$('shX').onclick = closeSheet" in me_html)
check("me.html 点遮罩只在 target===this 时关闭",
      "e.target === this" in me_html and "closeSheet()" in me_html)
check("me.html 保存成功后关闭弹层", "closeSheet(); load();" in me_html)
check("me.html hidden 只在 openSheet/closeSheet 内改",
      me_html.count("$('sheet').hidden = false") == 1 and
      me_html.count("$('sheet').hidden = true") == 1, "")

# ---------- 4. 编辑资料输入可点可聚焦 ----------
pbio = re.search(r'<input[^>]*id="pBio"[^>]*>', me_html)
pbil = pbio.group(0) if pbio else ""
check("me.html 有简介输入 #pBio", bool(pbio))
check("me.html #pBio 不是 readonly", "readonly" not in pbil.lower(), pbil)
check("me.html #pBio 有 maxlength=40", 'maxlength="40"' in pbil, pbil)
check("me.html #pBio 是 type=text", 'type="text"' in pbil, pbil)
check("me.html #pBio 有 autocomplete=off", 'autocomplete="off"' in pbil, pbil)
check("me.html #pBio 无 pointer-events:none", "pointer-events" not in pbil, pbil)

pnick = re.search(r'<input[^>]*id="pNick"[^>]*>', me_html)
pnick_s = pnick.group(0) if pnick else ""
check("me.html 有昵称输入 #pNick", bool(pnick))
check("me.html #pNick 不是 readonly", "readonly" not in pnick_s.lower(), pnick_s)
check("me.html #pNick 是 type=text", 'type="text"' in pnick_s, pnick_s)

check("me.html 编辑资料弹层有保存按钮 #pGo", 'id="pGo"' in me_html)
check("me.html 保存按钮在 #shB 文档流里",
      re.search(r'id="pBio".*?id="pGo"', me_html, re.S) is not None, "")

# ---------- 5. pwa.css 弹层打开时隐藏导航 ----------
hide = re.search(r"body\.lh-overlay-open nav\.tabbar\s*\{([^}]*)\}", pwa_css, re.S)
check("pwa.css 有 lh-overlay-open 隐藏 tabbar 的规则", bool(hide))
hide_body = hide.group(1) if hide else ""
check("pwa.css 隐藏用 visibility:hidden", "visibility:hidden" in hide_body, hide_body)
check("pwa.css 隐藏用 pointer-events:none", "pointer-events:none" in hide_body, hide_body)
for sel in (".sheet:not([hidden])", ".preview-mask.show", ".lightbox", "#big"):
    check("pwa.css :has 覆盖 %s 打开态" % sel,
          ("body:has(" in pwa_css and sel in pwa_css), "")
check("pwa.css 没有把 tabbar 改成 display:none 误伤高度",
      "nav.tabbar{display:none" not in pwa_css and "nav.tabbar{display: none" not in pwa_css)

# ---------- 6. 全站其他浮层层级 ----------
lb_z = zindex(comm_css, ".lightbox")
pm_z = zindex(comm_css, ".preview-mask")
check("community.css .lightbox z-index 高于 60", lb_z is not None and lb_z > 60, lb_z)
check("community.css .preview-mask z-index 高于 60", pm_z is not None and pm_z > 60, pm_z)

# ---------- 7. 不回归：字号/版本/导航 ----------
check("community.css input.f 字号仍是 16px",
      re.search(r"input\.f[^{]*\{[^}]*font-size:16px", comm_css) is not None)
check("me.html 使用 ?v=66", "?v=66" in me_html)
check("me.html 没有残留 ?v=61", "?v=61" not in me_html)
check("pwa.css 未把 tabbar z-index 改为其它值", nav_z == 60, nav_z)

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
