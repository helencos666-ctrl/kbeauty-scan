"""商家分类发布模板：每个分类有结构化预填，禁词，选分类才覆盖未改模板。"""
import os, re, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
html = (ROOT / "static/community-new.html").read_text(encoding="utf-8")

def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        raise SystemExit(1)

keys = ["guide","clinic","food","airport","hotel","interp","kpop","car","sim","logistics","daigou","other"]
check("有 MCAT_TPL 与 applyMcat", "var MCAT_TPL" in html and "function applyMcat" in html)
check("商家分类选项含美食代点外卖", 'option value="food">美食代点外卖</option>' in html)
m = re.search(r"var MCAT_TPL = \{.*?\n\};", html, re.S)
check("MCAT_TPL 对象可解析", m is not None)
block = m.group(0) if m else ""
for k in keys:
    check("模板含分类 %s" % k, re.search(r"\b%s:" % k, block) is not None)
check("美食模板讲代点或外卖", "代点" in block and "外卖" in block)
check("每份模板提示站内咨询", block.count("立即咨询") >= 12)
forbid = ("微信","QQ","电话","手机号","二维码","扫码","WhatsApp","最好","最佳","第一","唯一")
hits = [w for w in forbid if w in block]
check("模板无站外联系与夸张用语", not hits, hits)
check("切换分类走 applyMcat", "mcat').addEventListener('change'" in html and "applyMcat()" in html)
check("只覆盖空正文或上一份模板", "lastTpl" in html and "applyMcat" in html)
check("表单分类顺序与网格一致",
      [m.group(1) for m in re.finditer(r'<option value="(guide|clinic|food|airport|hotel|interp|kpop|car|sim|logistics|daigou|other)">', html)] == keys)
check("无留学签证模板与选项", "留学签证" not in html and 'value="agency"' not in html)
print("all merchant template checks passed")
