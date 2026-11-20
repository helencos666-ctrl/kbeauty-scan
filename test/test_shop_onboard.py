"""商家入驻示范、教程、6 张照片与我的页入口合并。python test/test_shop_onboard.py"""
import os, re, sys

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

prov = read("providers.py")
ex = read("shop_examples.py")
shops = read("static", "shops.html")
partner = read("static", "partner.html")
guide = read("static", "join-guide.html")
example = read("static", "shop-example.html")
provider = read("static", "provider.html")
start = read("static", "merchant-start.html")
me = read("static", "me.html")
newpost = read("static", "community-new.html")
shops_js = read("static", "shops.js")

CANON = ["guide","clinic","food","airport","hotel","interp","kpop","car","sim","logistics","daigou","other"]

check("providers 含 12 个正式分类", all(('"%s"' % k) in prov or ("'%s'" % k) in prov for k in CANON) and "CANON_CATS" in prov)
check("旧类别 booking/transfer/assist 仍可映射", "CAT_ALIASES" in prov and '"booking": "food"' in prov and '"transfer": "airport"' in prov)
check("照片上限为 6", "PHOTO_MAX = 6" in prov)
check("申请接口接收 photos", "photos:" in prov and "c_provider_photos" in prov)
check("不删除原 photo 列", "photo BYTEA" in prov and "ADD COLUMN IF NOT EXISTS user_id" in prov)
check("医美不整类封禁，仍禁止中介陪诊", "clinic" in prov and "医美中介" in prov and "陪诊" in prov and "仅限合法医疗机构" in prov)
check("不接受代办签证和抢票", "代办签证" in prov and "代抢" in prov)

check("每个分类都有示范案例", all(k in ex for k in CANON) and "示范案例（仅供参考，非真实商家）" in ex)
check("示范案例标明非真实商家且无评价数字", "不是真实商家" in ex and "评价" in ex and "不写评价" in ex.split("EXAMPLE_BADGE")[0])
check("示范照片是占位图路径", "/static/shop-examples/" in ex)
for k in CANON:
    for i in range(1, 7):
        fp = os.path.join(ROOT, "static", "shop-examples", "%s-%d.jpg" % (k, i))
        check("占位图存在 %s-%d" % (k, i), os.path.isfile(fp) and os.path.getsize(fp) > 1000)
        if not os.path.isfile(fp):
            break

check("教程页有 4 步和大按钮", all(w in guide for w in ("准备这些资料", "先看示范案例", "填写并提交申请", "提交后发布服务")) and 'href="/partner?from=guide"' in guide)
check("教程指向申请表", "/partner" in guide and "站内咨询" in guide)
check("示范详情页有 6 格照片和不可咨询说明", "gal" in example and "consult-off" in example and "示范案例不能咨询" in ex)
check("商家详情页最多展示 6 张图", "slice(0, 6)" in provider and "/api/providers/" in provider and "站内咨询" in provider)
check("开始页：已是商家去发布，否则去申请", "/community/new?listing=1" in start and "/partner?from=start" in start and "me.user.merchant" in start)

check("我的页只剩一条商家入口", me.count("我是商家 · 入驻并发布服务") == 1 and "商家发布信息" not in me and 'href="/shops/start"' in me)
check("申请表最多 6 张照片", 'id="f_photos"' in partner and "multiple" in partner and "最多 6 张" in partner)
check("申请表有教程和示范链接", "/shops/join-guide" in partner and "shops/examples/" in partner)
check("申请成功后去发布服务", "community/new?listing=1" in partner and "发布你的服务" in partner)
check("营业时间改为选填", "营业时间（选填）" in partner)

check("商家页分类可打开", all(('href="/shops?cat=%s"' % k) in shops for k in CANON))
check("商家页有教程按钮", "我要入驻·看教程" in shops)
check("shops.js 不把示范当真实商家", "example-card" in shops_js and "ex-badge" in shops_js and "ex.badge" in shops_js)
check("发布页未入驻会带回教程和申请", "/shops/join-guide" in newpost and "/partner?from=compose" in newpost)

# 硬性约束
for name, txt in (("shops.html", shops), ("partner.html", partner), ("join-guide.html", guide),
                  ("shop-example.html", example), ("provider.html", provider), ("shop_examples.py", ex)):
    check("%s 不提微信引流" % name, all(w not in txt for w in ("加微信", "微信号：", "微信联系")))
    check("%s 不提奋韩" % name, "奋韩" not in txt)

print("%s passed, %s failed" % (ok, fail))
if fail:
    sys.exit(1)
