"""找搭子：纯函数与静态检查（不需要数据库）。python test/test_dazi.py"""
import os, sys, datetime
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
ok = fail = 0
def check(n, c, info=""):
    global ok, fail
    if c: ok += 1; print("PASS", n)
    else: fail += 1; print("FAIL", n, info)

import dazi as D
check("打码微信号", "abc12345" not in D.mask_contacts("加我微信 abc12345 一起"), D.mask_contacts("加我微信 abc12345 一起"))
check("打码手机号", "1234-5678" not in D.mask_contacts("电话 010-1234-5678"), D.mask_contacts("电话 010-1234-5678"))
check("不误伤日期金额", D.mask_contacts("12/5-12/8 人均30000韩元 2026-12-05") == "12/5-12/8 人均30000韩元 2026-12-05")
check("检测到联系方式", bool(D.contact_hits("vx: kimlove88")) and not D.contact_hits("首尔饭搭子 12月5日明洞见"))
today = D._today()
m = D.meta_out({"city": "seoul", "kind": "visa", "date_from": today - datetime.timedelta(days=3), "date_to": today - datetime.timedelta(days=1), "people": 3, "gender": "female"})
check("过期判断", m["expired"] is True and m["kind_name"] == "团签拼人" and m["gender_name"] == "仅限女生")
m2 = D.meta_out({"city": "jeju", "kind": "food", "date_from": today, "date_to": None, "people": None, "gender": "any"})
check("今天不算过期", m2["expired"] is False and m2["city_name"] == "济州岛")
o = D.api_options()
words = [w["w"] for w in o["hot_words"]]
check("热门词条用真实说法", all(w in words for w in ["首尔搭子", "三人团签二缺一", "济州岛搭子", "长驻首尔搭子", "接送机"]))
for f in ["static/dazi.html"]:
    s = open(os.path.join(ROOT, f), encoding="utf-8").read()
    check(f + " 无奋韩/外链", "奋韩" not in s and "cdn" not in s.lower())
    check(f + " 无 inline onclick", "onclick=" not in s)
    check(f + " 使用 v97", "?v=97" in s and "?v=95" not in s)
cp = open(os.path.join(ROOT, "community.py"), encoding="utf-8").read()
check("社区发帖对找搭子频道做联系方式拦截", 'if board == "group":' in cp and "找搭子发帖" in cp)
check("频道显示名改为找搭子（键名不变）", '"group": "找搭子"' in cp)
check("迁移已挂载", "MIGRATE_DAZI" in cp)
idx = open(os.path.join(ROOT, "static/index.html"), encoding="utf-8").read()
check("首页吃喝玩乐仍五格且含找搭子", idx.split('<div class="cats">', 1)[1].split("</div>", 1)[0].count("<a ") == 5 and 'href="/dazi"' in idx)
cm = open(os.path.join(ROOT, "static/community.html"), encoding="utf-8").read()
check("社区顶部第一个标签是找搭子", cm.split('id="tabs"', 1)[1].split("<button", 1)[0].count('href="/dazi"') == 1)

# ---- 发帖门槛：非会员登录用户可发搭子帖（DAZI_REQUIRE_MEMBER 默认 false），其他板块不变 ----
import contextlib
from fastapi import HTTPException
os.environ.setdefault("COOKIE_INSECURE", "1")
saved_db = os.environ.pop("DATABASE_URL", None)
import community as C
from app import app
from fastapi.testclient import TestClient
seen = []
@contextlib.contextmanager
def fake_db():
    yield None
def fake_need_tier(conn, req, tier, msg=None):
    seen.append(tier)
    if tier > 0:   # 测试用户 tier=0（非会员）
        raise HTTPException(403, "开通会员后即可发帖")
    raise HTTPException(418, "passed-tier-gate")
state = {"n": 0}
orig = (D.db, D.need_user, D.q, C.db, C.need_tier)
D.db = fake_db; C.db = fake_db
D.need_user = lambda conn, req: {"id": 5, "tier": 0, "buddy_banned_until": None}
D.q = lambda conn, sql, args=(), one=False: {"n": state["n"]}
C.need_tier = fake_need_tier
cli = TestClient(app)
H = {"X-LH": "1"}
form = {"title": "12月首尔饭搭子", "body": "12月5日到8日在首尔，想找饭搭子一起吃烤肉", "city": "seoul", "kind": "food",
        "date_from": (D._today() + datetime.timedelta(days=3)).isoformat(), "people": "2", "gender": "any"}
try:
    os.environ.pop("DAZI_REQUIRE_MEMBER", None)
    r = cli.post("/api/dazi/posts", data=form, headers=H)
    check("非会员登录用户可发搭子帖（通过发帖门槛，tier=0）", r.status_code == 418 and seen[-1] == 0, (r.status_code, r.text[:120], seen))
    check("options 显示不要求会员", cli.get("/api/dazi/options").json().get("require_member") is False)
    os.environ["DAZI_REQUIRE_MEMBER"] = "true"
    r = cli.post("/api/dazi/posts", data=form, headers=H)
    check("DAZI_REQUIRE_MEMBER=true 时改回会员门槛", r.status_code == 403 and seen[-1] == 1, (r.status_code, seen))
    os.environ.pop("DAZI_REQUIRE_MEMBER", None)
    state["n"] = D.DAZI_DAILY_CAP
    r = cli.post("/api/dazi/posts", data=form, headers=H)
    check("每人每日搭子帖上限", r.status_code == 429 and "每天最多" in r.text, (r.status_code, r.text[:100]))
    state["n"] = 0
    r = cli.post("/api/dazi/posts", data=dict(form, body="加我微信 abc12345 一起吃"), headers=H)
    check("非会员发帖仍过滤联系方式", r.status_code == 400 and "站外联系方式" in r.text, r.text[:120])
    r = cli.post("/api/community/posts", data={"board": "daily", "title": "今天天气不错", "body": "随便记一下今天的心情和天气"}, headers=H)
    check("其他板块仍要求会员（tier=1）", r.status_code == 403 and seen[-1] == 1, (r.status_code, seen))
    r = cli.post("/api/community/posts", data={"board": "group", "title": "旧入口发结伴", "body": "从旧发帖页直接发到找搭子频道"}, headers=H)
    check("旧发帖页入口不受开关影响（仍 tier=1）", r.status_code == 403 and seen[-1] == 1, (r.status_code, seen))
finally:
    D.db, D.need_user, D.q, C.db, C.need_tier = orig
    if saved_db is not None:
        os.environ["DATABASE_URL"] = saved_db

print("%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
