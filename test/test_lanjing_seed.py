"""蓝憬通讯启动种子：常量、作者校验与幂等逻辑。python test/test_lanjing_seed.py"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import lanjing_seed as L

ok = fail = 0

def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS", name)
    else:
        fail += 1
        print("FAIL", name, str(info)[:300])

check("title is the approved wording", L.TITLE == "蓝憬通讯：推荐2位好友注册免费领韩国手机卡")
check("title length ok", 1 <= len(L.TITLE) <= 40)
check("board is news", L.BOARD == "news")
check("author nickname is 韩国通讯", L.NICKNAME == "韩国通讯")
check("body has no draft note", "草案" not in L.BODY and "尚未发布" not in L.BODY)
check("body keeps listing content", all(w in L.BODY for w in ["蓝憬通讯", "推荐 2 位好友", "咨询办理", "护照等资料由商家审核", "平台不收集"]))
check("body has no wechat", "微信" not in L.BODY and "二维码" not in L.BODY)
check("body has no 奋韩", "奋韩" not in L.BODY)
check("body length ok", 1 <= len(L.BODY) <= 3000)
check("poster file exists", os.path.isfile(L.poster_path()) and L.poster_path().endswith("poster-lanjing.png"))
check("ad path", L.AD_PATH == "/ads/lanjing")

check("skip when no user", L.pick_author([])[0] is None and "missing" in L.pick_author([])[1])
check("skip when not unique", L.pick_author([{"id":1},{"id":2}])[0] is None)
check("skip when banned", L.pick_author([{"id":1,"is_banned":True,"merchant_active":True}])[0] is None)
check("skip when not merchant", L.pick_author([{"id":1,"is_banned":False,"merchant_active":False}])[0] is None)
u, reason = L.pick_author([{"id":7,"is_banned":False,"merchant_active":True}])
check("accept active merchant", u and u["id"]==7 and reason is None)

src = open(os.path.join(os.path.dirname(__file__), "..", "lanjing_seed.py"), encoding="utf-8").read()
check("logs go through _log helper", "def _log(" in src and '_log("inserted:' in src)
check("does not print connection url", "print(_db_url" not in src and "print(os.environ" not in src)

index = open(os.path.join(os.path.dirname(__file__), "..", "static", "index.html"), encoding="utf-8").read()
check("home 4th ad uses /ads/lanjing", 'data-ad="lanjing"' in index and 'href="/ads/lanjing"' in index)

app = open(os.path.join(os.path.dirname(__file__), "..", "app.py"), encoding="utf-8").read()
check("app starts seed on startup", "seed_once" in app and "lanjing_router" in app)

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
