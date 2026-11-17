"""找商家入口合并：检查页面文案与 /services 重定向代码。python test/test_merchant_nav.py"""
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

index = open(os.path.join(ROOT, "static/index.html"), encoding="utf-8").read()
shops = open(os.path.join(ROOT, "static/shops.html"), encoding="utf-8").read()
me = open(os.path.join(ROOT, "static/me.html"), encoding="utf-8").read()
prov = open(os.path.join(ROOT, "providers.py"), encoding="utf-8").read()
svc = open(os.path.join(ROOT, "static/services.html"), encoding="utf-8").read()

check("no 服务与合作 section", "服务与合作" not in index)
check("no 推荐商家 tile", ">推荐商家<" not in index and "推荐商家</a>" not in index)
check("常用工具 contains 找商家", 'i>🏪</i>找商家' in index)
# homepage grid + 全部功能 each have one 找商家 tile (same markup)
check("找商家 appears on home and 全部功能", index.count('i>🏪</i>找商家') == 2)
check("商家入驻 is small bottom link", "我是商家 · 商家入驻" in index)
check("core tools on more page", all(w in index for w in ["地铁·公交路线", "中韩翻译", "常用韩语", "拍照识妆", "汇率换算", "找商家", "韩国美食"]))
check("home grid has 常用韩语", 'i>🗣️</i>常用韩语' in index)
home = index.split('id="p-home"')[1].split('id="p-cos"')[0] if 'id="p-home"' in index else ""
check("promo chips off homepage top", 'href="/invite">邀请有奖' not in home.split("shopBlk")[0] and 'href="/deals">购物优惠' not in home.split("shopBlk")[0])
check("home grid no longer leads with 炸鸡代点/景点介绍", 'i>🍗</i>炸鸡代点' not in home.split("chips")[0] and 'i>🏯</i>景点介绍' not in home.split("chips")[0])
check("shops is 找商家 hub", "找商家" in shops and "推荐店铺" in shops and "服务商家" in shops and "韩友圈商家" in shops)
check("boards still listed on shops", all(w in shops for w in ["住宿推荐", "包车用车", "皮肤管理·医美", "留学机构", "代购商家", "地陪", "追星活动", "中韩物流"]))
check("me has 商家入驻", "我是商家 · 商家入驻" in me)
check("/services 301 in providers.py", 'RedirectResponse("/shops?"' in prov and "status_code=301" in prov)
check("services.html fallback redirect", "/shops?tab=services" in svc)

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
