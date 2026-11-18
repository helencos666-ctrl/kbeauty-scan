"""开业活动配置、弹层、活动页与「我的」红标：python test/test_opening_promos_ui.py"""
import json, os, re

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

def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()

cfg = json.loads(read("static", "opening-promos.json"))
js = read("static", "opening-promos.js")
page = read("static", "opening.html")
me = read("static", "me.html")
pwa_css = read("static", "pwa.css")
pwa_js = read("static", "pwa.js")
seo = read("seo.py")
sw = read("static", "sw.js")

items = cfg.get("items") or []
by_id = {i["id"]: i for i in items}

check("配置默认开启且有起止日期", cfg.get("enabled") is True and "starts" in cfg and "ends" in cfg)
check("配置至少 8 条活动", len(items) >= 8, len(items))
for key in ("invite", "lanjing-sim", "hamcho-10", "daega-10", "seoul-film", "merchant-free", "promoter", "blogger"):
    check("含活动 %s" % key, key in by_id)
    it = by_id.get(key) or {}
    check("%s 为进行中且可关闭" % key, it.get("enabled") is True and it.get("status") == "active")

inv = by_id.get("invite") or {}
inv_text = (inv.get("body") or "") + (inv.get("summary") or "")
check("邀请规则含 10/15/40/80 与上限 150", all(x in inv_text for x in ("10 元", "15 元", "40 元", "80 元", "150 元")))
check("邀请只计一级且需首次使用", "只计一级" in inv_text and "首次使用" in inv_text)
check("邀请不奖励分享动作", "分享这个动作本身没有奖励" in inv_text)
check("邀请券不可提现", "不可提现" in inv_text)
check("邀请链到 /invite", inv.get("href") == "/invite")

pr = by_id.get("promoter") or {}
pr_text = (pr.get("title") or "") + (pr.get("summary") or "") + (pr.get("body") or "") + (pr.get("note") or "")
check("推广员标题为招募韩国地区推广员", "招募韩国地区推广员" in (pr.get("title") or ""))
check("推广员 50% 平分且只计一级", "50%" in pr_text and "只计一级" in pr_text)
check("推广员不设下级招募奖励", "下级" in pr_text)
check("推广员应聘走站内咨询", (pr.get("href") or "").startswith("/consult/new") and "kind=platform" in (pr.get("href") or ""))
check("推广员按钮为应聘/咨询", "应聘" in (pr.get("cta") or "") and "咨询" in (pr.get("cta") or ""))
check("推广员注明合作协议为准", pr.get("note") == "具体规则以合作协议为准" or "具体规则以合作协议为准" in pr_text)

for key in ("hamcho-10", "daega-10", "seoul-film", "lanjing-sim"):
    it = by_id[key]
    blob = json.dumps(it, ensure_ascii=False)
    check("%s 不是筹备中/即将开放" % key, "筹备中" not in blob and "即将开放" not in blob)
    check("%s 有现场规则说明" % key, "现场" in (it.get("note") or it.get("body") or ""))


bl = by_id.get("blogger") or {}
bl_text = (bl.get("title") or "") + (bl.get("summary") or "") + (bl.get("body") or "") + (bl.get("note") or "")
check("博主标题为招募第一批博主", "招募第一批博主" in (bl.get("title") or ""))
check("博主优质帖每篇 2 元且分红 5%", "2 元" in bl_text and "5%" in bl_text)
check("博主含重点培养、探店佣金与演出活动", "重点培养" in bl_text and "探店" in bl_text and "演出" in bl_text and "颁奖典礼" in bl_text)
check("博主只计一级且不设下级奖励", "只计一级" in bl_text and "下级" in bl_text)
check("博主应聘走站内咨询", (bl.get("href") or "").startswith("/consult/new") and "kind=platform" in (bl.get("href") or ""))
check("博主按钮为应聘/咨询", "应聘" in (bl.get("cta") or "") and "咨询" in (bl.get("cta") or ""))
check("博主注明标准结算与名额规则", "优质标准与结算规则以平台公布为准" in bl_text and "活动名额以平台邀请为准" in bl_text)

check("酱蟹链到美食页", (by_id["hamcho-10"].get("href") or "").startswith("/food#hamcho"))
check("烤肉链到美食页", (by_id["daega-10"].get("href") or "").startswith("/food#daega"))
check("电话卡链到蓝憬活动", by_id["lanjing-sim"].get("href") == "/ads/lanjing")
check("入驻链到 /partner", by_id["merchant-free"].get("href") == "/partner")

blob = json.dumps(cfg, ensure_ascii=False) + js + page
for name, txt in (("开业配置和活动页", blob), ("pwa.css", pwa_css), ("pwa.js", pwa_js)):
    check("%s 无微信/二维码引导" % name, "微信" not in txt and "二维码" not in txt and "扫码" not in txt)
    check("%s 无绝对化宣传语" % name,
          not re.search(r"唯一|第一名|第一品牌|最(好|佳|优|强|低|便宜)|顶级", txt))

check("活动页读取配置并渲染列表", "opening-promos.json" in js and "renderList" in js and "LHOpening" in page)
check("弹层每日一次写入 localStorage", "lh_opening_popup_day" in json.dumps(cfg) and "localStorage.setItem" in js)
check("弹层只在首页出现", "onHome" in js and "openPromoSheet" in js)
check("pwa.js 会加载开业脚本", "opening-promos.js?v=84" in pwa_js)
check("pwa.css 开业期间我的为红色", "body.lh-opening nav.tabbar a.me" in pwa_css and "#C62828" in pwa_css)
check("pwa.css 有「开」角标", "tab-hot" in pwa_css)
check("我的页有开业活动入口且保留邀请有奖", 'id="openPromoRow"' in me and 'href="/opening"' in me and 'href="/invite"' in me)
check("邀请有奖仍在开业活动下方", me.index('id="openPromoRow"') < me.index('href="/invite"'))
check("存在 /opening 路由与 SEO", 'p_opening' in read("me.py") and '"/opening"' in seo)
check("sw.js 含活动页与配置", "'/opening'" in sw and "opening-promos.json?v=84" in sw)
check("sw.js CACHE 为 v76", "lvhan-shell-v84" in sw)

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
