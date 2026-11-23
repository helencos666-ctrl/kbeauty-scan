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
    check(f + " 使用 v98", "?v=98" in s and "?v=95" not in s)
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
    # ---- 商家账号在搭子区发帖：按帖子自身归类（默认个人） ----
    import contact_guard as CG
    ins = []
    def fake_cq(conn, sql, args=(), one=False):
        if "INSERT INTO c_posts" in sql:
            ins.append(args); return {"id": 999}
        return {"n": 0}
    orig2 = (C.q, C.get_settings, C.limit, CG.guard_text)
    C.q = fake_cq; C.get_settings = lambda conn: {}; C.limit = lambda *a, **k: None
    CG.guard_text = lambda *a, **k: None
    MU = {"id": 14, "tier": 3, "merchant": True, "merchant_credits": 0, "buddy_banned_until": None}
    C.need_tier = lambda conn, req, tier, msg=None: dict(MU)
    D.need_user = lambda conn, req: dict(MU)
    D.q = lambda conn, sql, args=(), one=False: {"n": 0}
    try:
        r = cli.post("/api/dazi/posts", data=form, headers=H)   # 不传 post_type = 默认个人
        a0 = ins[-1] if ins else ()
        check("商家账号发个人搭子帖：is_merchant=False、直接发布（出现在个人列表）",
              r.status_code == 200 and a0[4] == "published" and a0[5] is False, (r.status_code, r.text[:120], a0[4:6]))
        r = cli.post("/api/dazi/posts", data=dict(form, post_type="personal", body="12月首尔包车一日游，找人拼车分摊费用"), headers=H)
        a0 = ins[-1]
        check("商家账号个人帖仍走商业词审核", a0[4] == "pending" and a0[5] is False and "商业" in (a0[7] or ""), a0[4:8])
        r = cli.post("/api/dazi/posts", data=dict(form, post_type="merchant"), headers=H)
        a0 = ins[-1]
        check("商家账号选商家服务：is_merchant=True 且先审后发", a0[4] == "pending" and a0[5] is True, a0[4:8])
        n0 = len(ins)
        r = cli.post("/api/dazi/posts", data=dict(form, body="加我微信 abc12345 一起吃饭"), headers=H)
        check("商家账号个人帖仍过滤联系方式", r.status_code == 400 and len(ins) == n0, r.text[:100])
        D.need_user = lambda conn, req: {"id": 5, "tier": 0, "merchant": False, "buddy_banned_until": None}
        r = cli.post("/api/dazi/posts", data=dict(form, post_type="merchant"), headers=H)
        check("普通用户不能选商家服务", r.status_code == 403 and "入驻商家" in r.text, r.text[:100])
        r = cli.post("/api/dazi/posts", data=dict(form, post_type="xx"), headers=H)
        check("非法发帖类型 400", r.status_code == 400)
    finally:
        C.q, C.get_settings, C.limit, CG.guard_text = orig2
finally:
    D.db, D.need_user, D.q, C.db, C.need_tier = orig
    if saved_db is not None:
        os.environ["DATABASE_URL"] = saved_db

ds = open(os.path.join(ROOT, "dazi.py"), encoding="utf-8").read()
check("搭子区列表置顶优先", "COALESCE(p.pinned,FALSE) DESC" in ds)
check("一次性修正：帖子 53 归个人、指南帖置顶（只改分类/置顶字段）", "post53_personal" in ds and "pin_dazi_guide" in ds
      and "SET is_merchant=FALSE" in ds and "SET pinned=TRUE" in ds)
dh = open(os.path.join(ROOT, "static/dazi.html"), encoding="utf-8").read()
check("发帖页商家可选个人/商家，默认个人", 'data-val="personal">个人找搭子' in dh and "post_type" in dh and "ptype:'personal'" in dh)
print("%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
