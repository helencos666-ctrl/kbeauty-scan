"""商家活动/横幅/站内咨询/联系方式拦截 + 韩友圈留存（通知、旅行豆、加精置顶话题、官方号、徽章、个人主页）
python test/test_merch_retention.py"""
import os, sys, io
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
ok = fail = 0
def check(name, cond, info=""):
    global ok, fail
    if cond: ok += 1; print("PASS", name)
    else: fail += 1; print("FAIL", name, str(info)[:300])
import psycopg
NAME = "lvhan_merch_ret_v1"
a = psycopg.connect("postgresql://postgres@127.0.0.1:54329/postgres", connect_timeout=3); a.autocommit = True
with a.cursor() as cur:
    cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (NAME,))
    cur.execute("DROP DATABASE IF EXISTS " + NAME); cur.execute("CREATE DATABASE " + NAME)
a.close()
DSN = "postgresql://postgres@127.0.0.1:54329/" + NAME
os.environ.update(DATABASE_URL=DSN, COOKIE_INSECURE="1", ADMIN_PASSWORD="test-admin-pw-1234")
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
import community as C
app = FastAPI(); app.include_router(C.router)
H = {"X-LH": "1"}
def jpg(c=(200, 200, 230), size=(1260, 540)):
    b = io.BytesIO(); Image.new("RGB", size, c).save(b, "JPEG"); return b.getvalue()
QR = open(os.path.join(ROOT, "static", "pay-alipay.jpg"), "rb").read()

adm = TestClient(app)
adm.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H)
NUSER = [0]
def user(nick, tier=0, merchant=False):
    c = TestClient(app); NUSER[0] += 1
    r = c.post("/api/community/register", json={"account": "u%d@example.com" % NUSER[0], "password": "Passw0rd!xyz", "nickname": nick, "agree": True}, headers=H)
    assert r.status_code == 200, r.text
    uid = c.get("/api/community/me").json()["user"]["id"]
    if tier:
        adm.post(f"/api/admin/users/{uid}", json={"action": "set_tier", "tier": tier}, headers=H)
    if merchant:
        adm.post(f"/api/admin/users/{uid}", json={"action": "merchant", "plan": "month"}, headers=H)
    c.uid = uid
    return c
g = TestClient(app)

# ---------- 联系方式检测（单元） ----------
from contact_guard import find_contacts, image_has_qr
check("detect wechat", find_contacts("加我vx: abc123"))
check("detect phone", find_contacts("电话 010-1234-5678") and find_contacts("13812345678"))
check("no false positive on price/date", not find_contacts("价格 ₩89,000，2026.12.01-2026.12.31 有效，营业 10:00-22:00"))
check("qr detected in image", image_has_qr(QR))
check("plain image no qr", not image_has_qr(jpg()))

# ---------- 新频道 ----------
bl = g.get("/api/community/boards").json()
allb = {x["key"]: x for c in bl["categories"] for x in c["boards"]}
check("logistics merchant board", allb.get("logistics", {}).get("merchant") and allb["logistics"]["name"] == "中韩物流", allb.get("logistics"))
check("daily board in 住在韩国", allb.get("daily", {}).get("name") == "在韩日常" and not allb["daily"]["merchant"])
meta = g.get("/api/merch/meta").json()
check("subcats clinic 5 + logistics", len(meta["subcats"]["clinic"]) == 5 and [x["name"] for x in meta["subcats"]["clinic"]][2] == "牙齿" and meta["subcats"].get("logistics"), meta["subcats"].get("clinic"))

# ---------- 商家活动帖 ----------
mer = user("美丽诊所", merchant=True)
r = mer.post("/api/community/posts", data={"board": "clinic", "tag": "dental", "title": "牙齿美白活动", "body": "冷光美白体验，保证效果无副作用。"}, headers=H)
check("medical promise blocked", r.status_code == 400 and "承诺效果" in r.json()["detail"], r.text)
r = mer.post("/api/community/posts", data={"board": "clinic", "tag": "dental", "title": "牙齿美白活动", "body": "冷光美白体验，名额有限。"},
             files=[("images", ("q.jpg", QR, "image/jpeg"))], headers=H)
check("qr image in activity blocked", r.status_code == 400 and "二维码" in r.json()["detail"], r.text)
r = mer.post("/api/community/posts", data={"board": "clinic", "tag": "dental", "offer": "体验价 ₩59,000", "valid_until": "2099-01-31",
             "title": "牙齿美白活动", "body": "冷光美白体验，名额有限，欢迎站内咨询。"}, files=[("images", ("a.jpg", jpg(), "image/jpeg"))], headers=H)
check("activity pending", r.status_code == 200 and r.json().get("pending"), r.text)
apid = r.json()["id"]
adm.post(f"/api/admin/posts/{apid}/review", json={"action": "approve"}, headers=H)
lst = g.get("/api/community/posts?board=clinic&tag=dental").json()["posts"]
check("activity listed with tag/views", lst and lst[0]["id"] == apid and lst[0]["tag_name"] == "牙齿" and "views" in lst[0] and lst[0]["offer"], lst[:1])
pd = g.get(f"/api/community/posts/{apid}").json()
check("medical disclaimer + consult", pd.get("medical_note") and pd.get("consult"), pd)

# ---------- 横幅 ----------
with psycopg.connect(DSN) as pc:
    sid = pc.execute("""INSERT INTO c_shop_recs(user_id,name,area,shop_type,description,status) VALUES (%s,'明洞烤肉','明洞','美食','好吃的烤肉店','approved') RETURNING id""", (mer.uid,)).fetchone()[0]
def mk(slot, title, kind="shop", lid=None, img=None, **kw):
    data = dict(slot=slot, title=title, link_kind=kind, link_id=str(lid if lid is not None else sid), **kw)
    return adm.post("/api/admin/banners", data=data, files={"image": ("b.jpg", img or jpg(), "image/jpeg")}, headers=H)
check("guest cannot create banner", g.post("/api/admin/banners", data={"slot": "food", "title": "x", "link_kind": "shop"}, files={"image": ("b.jpg", jpg(), "image/jpeg")}, headers=H).status_code == 401)
check("bad slot rejected", mk("evil", "测试横幅").status_code == 400)
check("external link kind rejected", mk("food", "测试横幅", kind="url").status_code == 400)
check("banner title contact rejected", mk("food", "加微信订座").status_code == 400)
check("banner qr image rejected", mk("food", "明洞烤肉", img=QR).status_code == 400)
r = mk("food", "明洞烤肉 · 中文菜单", subtitle="凭旅韩助手 9 折", merchant=str(mer.uid), bind_owner="1", price="300")
check("food banner created", r.status_code == 200, r.text); bid = r.json()["id"]
r = mk("food:myeongdong", "明洞专属横幅", sort="1"); bid2 = r.json()["id"]
mk("food:hongdae", "弘大横幅")
fb = g.get("/api/banners?slot=food").json()["banners"]
check("food global banner public", [b["id"] for b in fb] == [bid] and fb[0]["href"] == f"/go/banner/{bid}", fb)
fa = g.get("/api/banners?slot=food&area=myeongdong").json()["banners"]
check("food area banner included", {b["id"] for b in fa} == {bid, bid2}, fa)
check("board slot auto for new boards", mk("board:logistics", "物流横幅").status_code == 200 and len(g.get("/api/banners?slot=board:logistics").json()["banners"]) == 1)
check("empty slot returns none", g.get("/api/banners?slot=board:hotel").json()["banners"] == [])
check("bogus slot returns none (no error)", g.get("/api/banners?slot=../../x").json()["banners"] == [])
r = g.get(f"/go/banner/{bid}", follow_redirects=False)
check("banner redirects in-site to shop", r.status_code == 302 and r.headers["location"] == f"/shops/{sid}", r.headers.get("location"))
check("banner image public", g.get(f"/api/banners/{bid}/img").status_code == 200)
adm.post(f"/api/admin/banners/{bid2}", json={"ends_on": "2000-01-01"}, headers=H)
check("expired banner hidden", bid2 not in [b["id"] for b in g.get("/api/banners?slot=food&area=myeongdong").json()["banners"]])
ab = adm.get("/api/admin/banners").json()
check("admin list shows expired + slots", any(b["id"] == bid2 and b["expired"] for b in ab["banners"]) and any(s["key"] == "food:myeongdong" for s in ab["slots"]))
adm.post(f"/api/admin/banners/{bid2}", json={"action": "disable"}, headers=H)
with psycopg.connect(DSN) as pc:
    owner = pc.execute("SELECT owner_id FROM c_shop_recs WHERE id=%s", (sid,)).fetchone()[0]
check("shop bound to merchant", owner == mer.uid)
# 横幅付款：商家付款截图
r = mer.post("/api/pay-proof", data={"kind": "banner", "ref_id": str(bid)}, files={"image": ("p.jpg", jpg((10, 20, 30), (600, 900)), "image/jpeg")}, headers=H)
check("banner pay proof expects quoted price", r.status_code == 200 and r.json()["item"]["expect"] == 300, r.text)
ppid = r.json()["item"]["id"]
adm.post(f"/api/admin/pay-proofs/{ppid}", json={"action": "approve"}, headers=H)
check("banner marked paid", any(b["id"] == bid and b["paid"] for b in adm.get("/api/admin/banners").json()["banners"]))
other = user("路人甲")
check("cannot pay others' banner", other.post("/api/pay-proof", data={"kind": "banner", "ref_id": str(bid)}, files={"image": ("p.jpg", jpg(), "image/jpeg")}, headers=H).status_code == 400)

# ---------- 站内咨询 ----------
cu = user("游客小王")
check("consult needs login", g.post("/api/consult/start", json={"kind": "post", "id": apid}, headers=H).status_code == 401)
r = cu.post("/api/consult/start", json={"kind": "post", "id": apid}, headers=H)
check("consult start on activity", r.status_code == 200, r.text); tid = r.json()["thread_id"]
check("same pair reuses thread", cu.post("/api/consult/start", json={"kind": "banner", "id": bid}, headers=H).json()["thread_id"] == tid)
check("self consult refused", mer.post("/api/consult/start", json={"kind": "post", "id": apid}, headers=H).status_code == 400)
r = cu.post(f"/api/consult/threads/{tid}/messages", data={"body": "你好，周六有位置吗？"}, headers=H)
check("user sends", r.status_code == 200, r.text)
r = cu.post(f"/api/consult/threads/{tid}/messages", data={"body": "加我微信 abc123 细聊"}, headers=H)
check("contact in chat blocked", r.status_code == 400 and "站外联系方式" in r.json()["detail"], r.text)
r = cu.post(f"/api/consult/threads/{tid}/messages", data={"body": ""}, files={"image": ("q.jpg", QR, "image/jpeg")}, headers=H)
check("qr image in chat blocked", r.status_code == 400 and "二维码" in r.json()["detail"], r.text)
r = cu.post(f"/api/consult/threads/{tid}/messages", data={"body": ""}, files={"image": ("a.jpg", jpg((30, 120, 90), (800, 600)), "image/jpeg")}, headers=H)
check("image message ok", r.status_code == 200, r.text)
check("merchant unread", mer.get("/api/consult/unread").json()["n"] == 2 and mer.get("/api/community/me").json()["user"]["consult_unread"] == 2)
th = mer.get("/api/consult/threads").json()
check("merchant inbox", th["as_merchant"] and th["as_merchant"][0]["id"] == tid and th["as_merchant"][0]["unread"] == 2, th)
t = mer.get(f"/api/consult/threads/{tid}").json()
img_msg = [m for m in t["messages"] if m.get("img")]
check("thread messages + system ref", t["messages"][0]["sender"] == "system" and img_msg, t["messages"])
check("image only for participants", mer.get(img_msg[0]["img"]).status_code == 200 and other.get(img_msg[0]["img"]).status_code == 404)
check("outsider cannot read thread", other.get(f"/api/consult/threads/{tid}").status_code == 404)
check("read clears unread", mer.get("/api/consult/unread").json()["n"] == 0)
mer.post(f"/api/consult/threads/{tid}/messages", data={"body": "周六下午有位置，欢迎！"}, headers=H)
check("user unread after reply", cu.get("/api/consult/unread").json()["n"] == 1)
# 未绑定商家的店 → 平台客服
with psycopg.connect(DSN) as pc:
    sid2 = pc.execute("""INSERT INTO c_shop_recs(user_id,name,area,shop_type,description,status) VALUES (%s,'弘大拉面','弘大','美食','拉面','approved') RETURNING id""", (mer.uid,)).fetchone()[0]
r = cu.post("/api/consult/start", json={"kind": "shop", "id": sid2}, headers=H); ptid = r.json()["thread_id"]
cu.post(f"/api/consult/threads/{ptid}/messages", data={"body": "几点开门？"}, headers=H)
at = adm.get("/api/admin/consult/threads?platform=1").json()["threads"]
check("platform thread for admin", any(x["id"] == ptid and x["platform"] and x["unread"] == 1 for x in at), at)
check("admin replies platform", adm.post(f"/api/admin/consult/threads/{ptid}/messages", json={"body": "上午11点开门"}, headers=H).status_code == 200)
check("admin cannot reply merchant thread", adm.post(f"/api/admin/consult/threads/{tid}/messages", json={"body": "x"}, headers=H).status_code == 400)
check("admin can view merchant thread", len(adm.get(f"/api/admin/consult/threads/{tid}").json()["messages"]) >= 3)
hits = adm.get("/api/admin/contact-hits").json()["hits"]
check("contact hits logged", any(h["place"] == "咨询消息" for h in hits) and any(h["place"] == "咨询图片" for h in hits) and any(h["place"] == "横幅" for h in hits), hits[:5])
check("consult page 200", g.get("/consult").status_code == 200 and g.get(f"/consult/{tid}").status_code == 200 and g.get("/consult/new").status_code == 200)

# ---------- 留存：会员发帖、通知、旅行豆、徽章 ----------
m1 = user("日常阿米", tier=1)
m2 = user("热心老张", tier=1)
r = m1.post("/api/community/posts", data={"board": "daily", "title": "汉江", "body": "今天在汉江吃了炸鸡。"}, headers=H)
check("tier1 posts daily", r.status_code == 200, r.text); dp = r.json()["id"]
check("tier1 still blocked on friends", m1.post("/api/community/posts", data={"board": "friends", "title": "找搭子玩", "body": "周末一起逛街吗朋友们"}, headers=H).status_code == 403)
pts = m1.get("/api/points").json()
check("post points +5", pts["balance"] == 5 and any(h["kind"] == "post" for h in pts["history"]), pts["history"][:3])
n = m1.get("/api/notifications").json()
check("first_post badge + notice", any("首帖" in x["text"] for x in n["items"]), n)
check("me shows badges", "first_post" in m1.get("/api/community/me").json()["user"]["badges"])
r = m2.post(f"/api/community/posts/{dp}/comments", json={"body": "看起来好好吃！"}, headers=H)
check("comment ok", r.status_code == 200)
m2.post(f"/api/community/posts/{dp}/like", json={}, headers=H)
m2.post(f"/api/community/posts/{dp}/like", json={}, headers=H)
m2.post(f"/api/community/posts/{dp}/like", json={}, headers=H)   # 取消再点赞，只通知一次
m2.post(f"/api/community/follow/{m1.uid}", json={}, headers=H)
me1 = m1.get("/api/community/me").json()["user"]
check("notif unread count", me1["notif_unread"] == 3, me1["notif_unread"])
n = m1.get("/api/notifications").json()["items"]
kinds = [x["kind"] for x in n]
check("comment/like/follow notices", "comment" in kinds and kinds.count("like") == 1 and "follow" in kinds, kinds)
check("notice links to post", any(x["kind"] == "comment" and x["link"].startswith(f"/community/post/{dp}") for x in n))
check("reading clears unread", m1.get("/api/community/me").json()["user"]["notif_unread"] == 0)
check("first reply points to author", m1.get("/api/points").json()["balance"] == 7)
check("commenter points", m2.get("/api/points").json()["balance"] == 2)
for i in range(6):
    m2.post(f"/api/community/posts/{dp}/comments", json={"body": f"第{i}条评论"}, headers=H)
check("comment points capped 5/day", m2.get("/api/points").json()["balance"] == 10)
# 日常每天 3 篇上限
m1.post("/api/community/posts", data={"board": "daily", "title": "地铁", "body": "今天地铁好挤啊。"}, headers=H)
m1.post("/api/community/posts", data={"board": "daily", "title": "下雨", "body": "首尔今天下雨了。"}, headers=H)
import community as CC
CC._rl.clear()
r = m1.post("/api/community/posts", data={"board": "daily", "title": "第四篇", "body": "今天第四篇日常。"}, headers=H)
check("daily cap 3/day", r.status_code == 429 and "每天最多" in r.json()["detail"], r.text)

# ---------- 加精 / 置顶 / 话题 / 官方号 ----------
check("guest cannot feature", g.post(f"/api/admin/posts/{dp}/flags", json={"featured": True}, headers=H).status_code == 401)
r = adm.post(f"/api/admin/posts/{dp}/flags", json={"featured": True}, headers=H)
check("feature awards 30", r.status_code == 200 and r.json()["featured"] and r.json()["awarded"] == 30, r.text)
r = adm.post(f"/api/admin/posts/{dp}/flags", json={"featured": False}, headers=H); adm.post(f"/api/admin/posts/{dp}/flags", json={"featured": True}, headers=H)
check("re-feature no double award", m1.get("/api/points").json()["balance"] == 7 + 30 + 10)   # 3 篇日常帖 15 豆
n = m1.get("/api/notifications").json()["items"]
check("featured notice + badge", any(x["kind"] == "featured" for x in n) and any("精选作者" in x["text"] for x in n))
ff = g.get("/api/community/feed?tab=featured").json()["posts"]
check("featured filter tab", ff and ff[0]["id"] == dp and ff[0]["featured"], ff[:1])
r = adm.post("/api/admin/official-post", json={"board": "daily", "title": "本周话题：这周生活费花在哪了", "body": "晒出你的一周花费，官方会挑选加精。", "topic": True, "pinned": True}, headers=H)
check("official post", r.status_code == 200, r.text); op = r.json()["id"]
tp = g.get("/api/community/topics").json()
check("topic on home", tp["topics"] and tp["topics"][0]["id"] == op and tp["topics"][0]["author_official"], tp)
check("topic feed tab", any(p["id"] == op for p in g.get("/api/community/feed?tab=topic").json()["posts"]))
bd = g.get("/api/community/posts?board=daily").json()
check("pinned on top of board", bd["pinned"] and bd["pinned"][0]["id"] == op and all(p["id"] != op for p in bd["posts"]), bd.get("pinned"))
un = adm.get("/api/admin/unanswered").json()["posts"]
m3 = user("新来的", tier=1)
r = m3.post("/api/community/posts", data={"board": "visa", "title": "D-2 签证续签问题", "body": "我的情况是语学院转本科，想问续签要准备什么？"}, headers=H); vp = r.json()["id"]
check("unanswered list", any(p["id"] == vp for p in adm.get("/api/admin/unanswered").json()["posts"]))
check("official comment", adm.post("/api/admin/official-comment", json={"post_id": vp, "body": "欢迎！建议先看出入境官网的清单～"}, headers=H).status_code == 200)
check("answered removed", not any(p["id"] == vp for p in adm.get("/api/admin/unanswered").json()["posts"]))
cl = g.get(f"/api/community/posts/{vp}").json()["comment_list"]
check("official comment marked", cl and cl[0]["official"] and "official" in cl[0]["badges"], cl)

# ---------- 徽章：手动 + 个人主页 ----------
check("grant good_merchant", adm.post("/api/admin/badges", json={"user": str(mer.uid), "badge": "good_merchant"}, headers=H).status_code == 200)
check("bad badge rejected", adm.post("/api/admin/badges", json={"user": str(mer.uid), "badge": "king"}, headers=H).status_code == 400)
prof = g.get(f"/api/community/users/{mer.uid}").json()
check("profile shows badges", any(b["key"] == "good_merchant" and b["icon"] == "🏅" for b in prof["badges"]), prof.get("badges"))
check("badge notice to merchant", any("优质商家" in x["text"] for x in mer.get("/api/notifications").json()["items"]))
lst = g.get("/api/community/posts?board=clinic").json()["posts"]
check("author badges in list", "good_merchant" in (lst[0].get("author_badges") or []), lst[:1])
prof1 = g.get(f"/api/community/users/{m1.uid}").json()
check("profile stats + posts", prof1["stats"]["posts"] == 3 and prof1["stats"]["featured"] == 1 and len(prof1["posts"]) == 3, prof1["stats"])
check("profile/notice pages 200", g.get(f"/community/u/{m1.uid}").status_code == 200 and g.get("/community/notices").status_code == 200)
adm.post("/api/admin/badges", json={"user": str(mer.uid), "badge": "good_merchant", "action": "revoke"}, headers=H)
check("revoke badge", not any(b["key"] == "good_merchant" for b in g.get(f"/api/community/users/{mer.uid}").json()["badges"]))
# 连续签到 7 天：直接造 6 天签到记录
from points import kst_today
import datetime
with psycopg.connect(DSN) as pc:
    for k in range(1, 7):
        pc.execute("INSERT INTO c_points_ledger(user_id,kind,points,day,item_key,note) VALUES (%s,'checkin',10,%s,'checkin','x')", (m3.uid, kst_today() - datetime.timedelta(days=k)))
r = m3.post("/api/points/checkin", json={}, headers=H).json()
check("streak 7 bonus + next actions", r.get("streak_bonus") == 20 and r.get("next_actions") and r["next_actions"][1]["href"] == "/community/new?board=daily", {k: r.get(k) for k in ("streak", "streak_bonus", "reason")})
check("streak7 badge", "streak7" in m3.get("/api/community/me").json()["user"]["badges"])

# ---------- 首页只保留韩友圈·最新，交友帖仍走原列表接口 ----------
home = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
check("homepage keeps 韩友圈·最新 only", "韩友圈 · 最新" in home and 'id="postBlk"' in home and "/api/community/posts?limit_n=3" in home)
check("homepage has no 交友·最新 block", "交友 · 最新" not in home and "friendBlk" not in home and "friends-latest" not in home and "friendList" not in home)
# 接口保留但首页不再调用
fr = user("交友小美", tier=2)
fr.post("/api/community/posts", data={"board": "friends", "title": "周末一起去汉江", "body": "有没有人周末一起去汉江野餐？"}, headers=H)
fl = g.get("/api/community/friends-latest").json()["posts"]
check("friends-latest public titles", fl and fl[0]["title"] == "周末一起去汉江" and fl[0]["board_name"] == "旅韩交友" and "body" not in fl[0], fl)
print("---- %d passed, %d failed ----" % (ok, fail))
sys.exit(1 if fail else 0)
