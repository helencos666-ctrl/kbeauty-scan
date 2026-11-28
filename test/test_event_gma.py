"""首尔国际电影大奖入场名额（邀请5人）：python test/test_event_gma.py

用本机 Postgres 独立测试库；没有本机库时只跑静态检查。不打印连接串或密钥。
"""
import io
import os
import random
import sys
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
ok = fail = 0


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS", name)
    else:
        fail += 1
        print("FAIL", name, str(info)[:400])


def rd(*p):
    return (Path(ROOT).joinpath(*p)).read_text(encoding="utf-8")


def decode_qr(data):
    try:
        import zxingcpp
        from PIL import Image
        for bc in zxingcpp.read_barcodes(Image.open(io.BytesIO(data)).convert("RGB")):
            if getattr(bc, "text", None):
                return bc.text
    except Exception as e:
        print("zxing decode skip", type(e).__name__)
    return ""


def static_checks():
    page = rd("static", "event-gma.html")
    me = rd("static", "me.html")
    adm = rd("static", "admin.html")
    seed = rd("official_daily_seed.py")
    check("事件页：进度文案已邀请 X/5", "已邀请 ' + n + '/' + need + ' 人" in page)
    check("事件页：申请入场名额按钮和可选备注", "申请入场名额" in page and 'id="gNote"' in page and "选填" in page)
    check("事件页：二维码和复制链接", "/api/event/gma/qr.png" in page and "复制链接" in page)
    check("事件页：说明累计口径和最终以主办方确认为准", "全部时间累计" in page and "最终以主办方确认为准" in page)
    check("事件页：不含明星照片/海报/往届参与明星", all(x not in page for x in ("poster", "往届参与明星", "智秀", "BLACKPINK")))
    check("事件页：不再写未对公众售票", "未见对公众公开售票" not in page)
    check("我的页：电影大奖进度卡", 'id="myGma"' in me and "/api/event/gma/me" in me and "申请入场名额" in me and "已邀请 ' + n + '/' + need" in me)
    check("后台推广页：入场名额列表", 'id="gmaCard"' in adm and "/api/admin/event-gma" in adm and "已满 5 人的用户" in adm)
    check("种子支持 sync 更新已发布帖", '"sync"' in seed and "UPDATE c_posts SET body" in seed)


def main():
    static_checks()
    os.environ.setdefault("PUBLIC_BASE_URL", "https://lvhanzhushou.com")
    dsn = os.environ.get("LVHAN_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/lvhan_event_gma_v1")
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_event_gma_v1"
    if not dbname.isidentifier() or not dbname.isascii():
        raise RuntimeError("bad test db name")
    try:
        import psycopg
        a = psycopg.connect("postgresql://postgres@127.0.0.1:54329/postgres", connect_timeout=3)
        a.autocommit = True
        with a.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (dbname,))
            cur.execute("DROP DATABASE IF EXISTS " + dbname)
            cur.execute("CREATE DATABASE " + dbname)
        a.close()
    except Exception as e:
        print("SKIP integration: no local postgres (%s)" % type(e).__name__)
        print("RESULT", "ok", ok, "fail", fail)
        return 0 if fail == 0 else 1

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import community as C

    os.environ["DATABASE_URL"] = dsn
    os.environ["COOKIE_INSECURE"] = "1"
    os.environ["ADMIN_PASSWORD"] = "test-admin-pw-1234"
    C._schema_ready = False
    C._psycopg = None
    import event_gma as G
    app = FastAPI()
    app.include_router(C.router)
    H = {"X-LH": "1"}
    rnd = random.randint(100000, 999999)
    seq = [0]

    def dev():
        seq[0] += 1
        return "%032x" % (rnd * 1000 + seq[0])

    def mk(nick, promoter=None, device=None):
        C._rl.clear()
        cli = TestClient(app)
        cli.headers.update({"X-LH-Dev": device or dev()})
        body = {"account": "%s%d@example.com" % (nick, rnd), "password": "Passw0rd!xyz", "nickname": nick, "agree": True}
        if promoter:
            body["promoter"] = promoter
        r = cli.post("/api/community/register", json=body, headers=H)
        assert r.status_code == 200, r.text
        return cli, cli.get("/api/community/me").json()["user"]["id"]

    def me(cli):
        return cli.get("/api/event/gma/me").json()

    guest = TestClient(app)
    adm = TestClient(app)

    # ---- 游客 ----
    j = guest.get("/api/event/gma/me").json()
    check("游客：user 为空且 need=5", j["user"] is None and j["need"] == 5 and j["open"] is True, j)
    check("游客：二维码 401", guest.get("/api/event/gma/qr.png").status_code == 401)
    check("游客：申请 401", guest.post("/api/event/gma/claim", json={}, headers=H).status_code == 401)
    check("游客：后台接口 401/403", guest.get("/api/admin/event-gma").status_code in (401, 403))
    pg = guest.get(G.EVENT_PATH)
    check("事件页可访问且含活动名", pg.status_code == 200 and "首尔国际电影大奖" in pg.text and "邀请 5 位好友" in pg.text)

    # ---- 邀请人 alice ----
    alice, uid_a = mk("alice")
    j = me(alice)
    code = j["code"]
    check("登录用户拿到邀请码和事件页链接", len(code) == 6 and j["link"] == "https://lvhanzhushou.com%s?inv=%s" % (G.EVENT_PATH, code), j)
    check("初始 0/5 且不可申请", j["count"] == 0 and j["qualified"] is False)
    r = alice.post("/api/event/gma/claim", json={}, headers=H)
    check("0 人申请被拒（400）", r.status_code == 400 and "5" in r.text, r.text)
    png = alice.get("/api/event/gma/qr.png")
    check("二维码 PNG 解码为邀请链接", png.status_code == 200 and decode_qr(png.content) == j["link"], png.status_code)

    # 自己绑定自己不算
    r = alice.post("/api/ref/bind", json={"code": code}, headers=H)
    check("自己邀请自己不计入", r.json().get("status") == "self" and me(alice)["count"] == 0, r.text)

    # 4 位新用户（各自独立设备）经邀请码绑定
    friends = []
    for i in range(4):
        cli, uid = mk("friend%d" % i)
        r = cli.post("/api/ref/bind", json={"code": code}, headers=H)
        check("好友%d 绑定邀请码" % i, r.status_code == 200 and r.json()["status"] in ("pending", "frozen"), r.text)
        friends.append((cli, uid))
    j = me(alice)
    check("4 位好友 → 已邀请 4/5", j["count"] == 4 and not j["qualified"], j)
    r = alice.post("/api/event/gma/claim", json={}, headers=H)
    check("4 人申请被拒并提示还差 1 位", r.status_code == 400 and "还差 1" in r.text, r.text)

    # 同设备互邀：与 alice 同一设备注册的账号 → 不计入
    adev = alice.headers["X-LH-Dev"]
    same, _ = mk("samedev", device=adev)
    r = same.post("/api/ref/bind", json={"code": code}, headers=H)
    check("与邀请人同设备 → excluded 不计入", r.json()["status"] == "excluded" and me(alice)["count"] == 4, r.text)

    # 已封禁账号不计入
    ban, uid_ban = mk("banned1")
    ban.post("/api/ref/bind", json={"code": code}, headers=H)
    check("绑定后 5 人（含待封禁的）", me(alice)["count"] == 5)
    import psycopg
    with psycopg.connect(dsn) as c2:
        c2.execute("UPDATE c_users SET is_banned=TRUE WHERE id=%s", (uid_ban,))
        c2.commit()
    j = me(alice)
    check("封禁账号不计入 → 回到 4/5", j["count"] == 4, j)

    # 第 5 位：推广员来源 + 邀请有奖来源，同一账号只算一次
    adm_r = adm.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H)
    check("admin login", adm_r.status_code == 200, adm_r.text)
    r = adm.post("/api/admin/promoters", json={"name": "艾丽丝推广", "code": "A08"}, headers=H)
    check("创建推广员 A08", r.status_code == 200, r.text)
    nos = alice.get("/api/community/me").json()["user"]
    r = adm.post("/api/admin/promoters/A08", json={"action": "bind", "user": str(nos.get("member_no") or uid_a)}, headers=H)
    check("A08 绑定到 alice", r.status_code == 200, r.text)
    p1, uid_p1 = mk("promoreg", promoter="A08")
    j = me(alice)
    check("推广码注册的账号计入（5/5）", j["count"] == 5 and j["qualified"], j)
    p1.post("/api/ref/bind", json={"code": code}, headers=H)   # 同一账号再走邀请有奖：不重复计数
    j = me(alice)
    check("同一账号两种来源只算一次", j["count"] == 5, j)

    # ---- 申请 ----
    r = alice.post("/api/event/gma/claim", json={"note": "微信 alice_test"}, headers=H)
    cj = r.json()
    check("满 5 人可申请，状态待主办方确认", r.status_code == 200 and cj["ok"] and cj["claim"]["status"] == "pending" and cj["already"] is False, r.text)
    r = alice.post("/api/event/gma/claim", json={"note": "改一下"}, headers=H)
    check("重复申请幂等，不新增记录", r.status_code == 200 and r.json()["already"] is True and r.json()["claim"]["id"] == cj["claim"]["id"], r.text)
    j = me(alice)
    check("me 返回申请状态和备注", j["claim"]["label"] == "待主办方确认" and j["claim"]["note"] == "微信 alice_test", j)
    # 备注超长对新用户：用 bob 验证
    bob, uid_b = mk("bobinv")
    j = me(bob)
    for i in range(5):
        c, _ = mk("bf%d" % i)
        c.post("/api/ref/bind", json={"code": j["code"]}, headers=H)
    check("bob 5 人满额", me(bob)["count"] == 5, me(bob))
    r = bob.post("/api/event/gma/claim", json={"note": "x" * 101}, headers=H)
    check("备注 101 字被拒", r.status_code == 400, r.text)
    r = bob.post("/api/event/gma/claim", json={"note": ""}, headers=H)
    check("不填备注也能申请", r.status_code == 200 and r.json()["claim"]["note"] == "", r.text)
    r = bob.post("/api/event/gma/claim", json={})
    check("缺少 X-LH 头被拒（CSRF）", r.status_code in (400, 403), r.status_code)

    # 活动开始后截止
    carl, _ = mk("carlinv")
    jc = me(carl)
    for i in range(5):
        c, _ = mk("cf%d" % i)
        c.post("/api/ref/bind", json={"code": jc["code"]}, headers=H)
    import datetime
    old = G.CLOSES_AT
    G.CLOSES_AT = datetime.datetime(2020, 1, 1, tzinfo=G.KST)
    try:
        r = carl.post("/api/event/gma/claim", json={}, headers=H)
        check("活动开始后申请截止", r.status_code == 400 and "截止" in r.text and me(carl)["open"] is False, r.text)
    finally:
        G.CLOSES_AT = old

    # ---- 后台 ----
    r = adm.get("/api/admin/event-gma")
    a = r.json()
    check("后台：列表含 alice 和 bob 的申请，待确认 2", r.status_code == 200 and a["stats"]["claims"] == 2 and a["stats"]["pending"] == 2, a["stats"])
    ca = next(c for c in a["claims"] if c["note"] == "微信 alice_test")
    check("后台：行含账号、邀请人数、申请时间", ca["account"].startswith("alice") and ca["count"] == 5 and ca["time"], ca)
    check("后台：满 5 人名单含 alice/bob/carl，carl 未申请", a["stats"]["qualified"] == 3 and a["stats"]["qualified_unclaimed"] == 1, a["stats"])
    r = adm.post("/api/admin/event-gma/claims/%d" % ca["id"], json={"action": "confirm"}, headers=H)
    check("后台：确认名额", r.status_code == 200 and r.json()["status"] == "confirmed", r.text)
    check("用户端看到已确认", me(alice)["claim"]["label"] == "已确认")
    r = adm.post("/api/admin/event-gma/claims/%d" % ca["id"], json={"action": "bogus"}, headers=H)
    check("后台：非法操作 400", r.status_code == 400)
    check("后台：普通用户不能访问", alice.get("/api/admin/event-gma").status_code in (401, 403))
    check("后台：普通用户不能确认", alice.post("/api/admin/event-gma/claims/%d" % ca["id"], json={"action": "decline"}, headers=H).status_code in (401, 403))
    r = adm.post("/api/admin/event-gma/claims/999999", json={"action": "confirm"}, headers=H)
    check("后台：不存在的记录 404", r.status_code == 404)

    # 复核标记：同一网络短时间多人会被「邀请有奖」冻结，仍计数并在后台打标
    flagged = sum(c["flagged"] for c in a["claims"]) + sum(q_["flagged"] for q_ in a["qualified"])
    check("后台：同网络批量注册被标记（复核中）但不拦截", flagged > 0, flagged)

    # ---- 官方帖 sync 更新 ----
    import official_daily_seed as S
    S.seed_once()
    with psycopg.connect(dsn) as c3:
        row = c3.execute("SELECT id, body FROM c_posts WHERE title=%s AND NOT deleted", ("2026 第14届首尔国际电影大奖",)).fetchone()
        check("种子已发布电影大奖帖", row is not None and "邀请5位好友" in row[1])
        pid = row[0]
        c3.execute("UPDATE c_posts SET body=%s WHERE id=%s", ("旧内容：目前未见对公众公开售票", pid))
        other = c3.execute("SELECT id FROM c_posts WHERE title<>%s AND NOT deleted ORDER BY id LIMIT 1", ("2026 第14届首尔国际电影大奖",)).fetchone()
        c3.execute("UPDATE c_posts SET body=%s WHERE id=%s", ("人工改过的正文", other[0]))
        c3.commit()
    S.seed_once()
    with psycopg.connect(dsn) as c3:
        b1 = c3.execute("SELECT body FROM c_posts WHERE id=%s", (pid,)).fetchone()[0]
        b2 = c3.execute("SELECT body FROM c_posts WHERE id=%s", (other[0],)).fetchone()[0]
        n_img = c3.execute("SELECT count(*) FROM c_images WHERE post_id=%s", (pid,)).fetchone()[0]
    check("sync 标记的帖被更新为新正文", "邀请5位好友" in b1 and "未见对公众公开售票" not in b1, b1[:80])
    check("没有 sync 标记的帖不被改写", b2 == "人工改过的正文", b2)
    check("帖子仍只有 1 张图", n_img == 1, n_img)

    # 迁移可重复执行
    with psycopg.connect(dsn) as c4:
        c4.execute(G.MIGRATE_EVENT_GMA)
        c4.execute(G.MIGRATE_EVENT_GMA)
        c4.commit()
    check("迁移可重复执行", True)

    print("RESULT", "ok", ok, "fail", fail)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    code = main()
    print("%s passed, %s failed" % (ok, fail))
    sys.exit(code)
