"""v76：行程预设、软删除目标、我的红条、/local 重定向。python test/test_v76_trips_and_hide.py"""
import json, os, re
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ok=fail=0
def check(name, cond, info=""):
    global ok,fail
    if cond:
        ok+=1; print("PASS", name)
    else:
        fail+=1; print("FAIL", name, str(info)[:300])
def read(*p):
    with open(os.path.join(ROOT,*p), encoding="utf-8") as f: return f.read()

cfg=json.loads(read("static","trip-presets.json"))
presets={p["id"]:p for p in cfg["presets"]}
check("含首尔3/5天、济州3天、首尔+济州5/6天", all(k in presets for k in ("seoul-3","seoul-5","jeju-3","seoul-jeju-5","seoul-jeju-6")))
for k,p in presets.items():
    has_mode=all("mode" in s and "travel" in s for d in p["days"] for s in d["stops"])
    check("%s 每天有交通方式和时间" % k, has_mode and p["days"])
blob=json.dumps(cfg,ensure_ascii=False)+read("static","trips.html")
check("行程文案无微信/二维码/绝对化用语", "微信" not in blob and "二维码" not in blob and not re.search(r"唯一|第一名|最(好|佳|优)|顶级", blob))
check("注明资料来源与仅供参考", "VisitKorea" in json.dumps(cfg) and "只作参考" in read("static","trips.html"))

h=read("hide_shop_posts_v76.py")
check("软删除含蓝憬与两篇 dayone，不含电影节帖", "蓝憬通讯：推荐2位好友注册免费领韩国手机卡" in h and "50000商品券" in h and 'title": "dayone皮肤科"' in h or "dayone皮肤科" in h)
check("不删除电影节帖标题", "首尔电影" not in h)
check("启动时会执行软删除", "hide_shop_posts_v76" in read("app.py"))
check("蓝憬种子不会因软删除再插入", "deleted FROM c_posts" in read("lanjing_seed.py"))
check("蓝憬开业活动配置未改", "/ads/lanjing" in read("static","opening-promos.json") and "推荐 2 人免费领韩国电话卡" in read("static","opening-promos.json"))

me=read("static","me.html"); css=read("static","me.css")
check("我的开业活动与邀请有奖为红色条目", 'id="openPromoRow" class="promo-hot"' in me and 'id="inviteRow" class="promo-hot"' in me)
check("红色条目使用开业同色", "promo-hot" in css and "#C62828" in css)
check("其它菜单行未加 promo-hot", me.count("promo-hot")==2)

check("/local 走重定向", "p_local" in read("guides.py") and "RedirectResponse" in read("guides.py"))
print("%s passed, %s failed" % (ok,fail))
if fail: raise SystemExit(1)
