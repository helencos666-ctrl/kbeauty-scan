"""邀请漏绑修复：注册即绑定 / 自动补绑 / 邀请码双存 / 中文字体随仓库 / 补录有审计。
python test/test_ref_fix.py   （用本机 Postgres 独立测试库；没有时只跑静态检查）
"""
import datetime
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
    return Path(ROOT).joinpath(*p).read_text(encoding="utf-8")


def static_checks():
    ref, login, pwa = rd("static", "ref.js"), rd("static", "login.html"), rd("static", "pwa.js")
    page, me = rd("static", "event-gma.html"), rd("static", "me.html")
    check("ref.js：邀请码 cookie + localStorage 双存", "lh_invc" in ref and "document.cookie" in ref and "localStorage" in ref)
    check("ref.js：localStorage 丢了用 cookie 恢复", "用 cookie 恢复" in ref and "getCookie" in ref)
    check("ref.js：未登录/活动未开始不清除", "'inactive'" in ref and "'guest'" in ref and "clear()" in ref)
    check("ref.js：打开页面自动补绑（soft）", "auto()" in ref and "soft" in ref)
    check("ref.js：去登录链接带 inv（URL 兜底）", "/login" in ref and "'inv='" in ref)
    check("登录页加载 ref.js 并随注册提交 inv", "/static/ref.js" in login and "body.inv = LHRef.code()" in login and "LHRef.post('/api/community/register'" in login)
    check("pwa.js：有邀请码时任何页面都加载 ref.js", "lh_invc" in pwa and "/static/ref.js" in pwa)
    check("活动页：邀请码大字 + 复制邀请码按钮", 'class="codebar"' in page and "gCpCode" in page and "font-size:30px" in page)
    check("活动页：邀请链接字号 ≥ 14px", ".gma-link{word-break:break-all;font-size:14px" in page)
    check("我的页：邀请码/推广码大字 + 复制按钮，链接 ≥ 14px", "mgCopyCode" in me and "mpCopyCode" in me and "font-size:14px;line-height:1.5" in me and "font-size:13px;color:#4A5560;margin:6px 0 10px" not in me)
    check("缓存版本 v102", "lvhan-shell-v102" in rd("static", "sw.js") and "?v=100" not in login)
    fonts = Path(ROOT, "fonts")
    check("字体子集和 OFL 许可随仓库", (fonts / "NotoSansCJKsc-Regular-subset.otf").is_file() and (fonts / "NotoSansCJKsc-Bold-subset.otf").is_file() and "SIL OPEN FONT LICENSE" in (fonts / "OFL.txt").read_text())
    import community  # noqa: F401  (先于 promo，避免循环导入)
    from promo import _cjk_font, _FONT
    check("字体路径是仓库相对路径", str(_FONT).startswith(ROOT) and _FONT.is_file(), _FONT)
    f = _cjk_font(30, bold=True)
    check("_cjk_font 不再退回位图默认字体", f.__class__.__name__ == "FreeTypeFont", f.__class__)
    need = set("旅韩助手首尔国际电影大奖扫码注册帮朋友凑齐邀请推广码 5·") | set("0123456789ABCDEFGHJKMNPQRSTUVWXYZ")
    try:
        from fontTools.ttLib import TTFont
        cmap = TTFont(str(_FONT)).getBestCmap()
        check("字体含二维码图片里全部字", all(ord(c) in cmap for c in need), [c for c in need if ord(c) not in cmap])
    except ImportError:
        print("SKIP fontTools 未安装，跳过字形覆盖检查")
    # 不用系统字体也能出中文：把系统字体路径指到不存在的位置再画
    import promo
    saved = (promo._SYS_FONT, promo._SYS_FONT_BOLD)
    promo._SYS_FONT = promo._SYS_FONT_BOLD = Path("/nonexistent/x.ttc")
    try:
        import event_gma as G
        from PIL import Image
        png = G.render_event_qr_png("https://lvhanzhushou.com/event/seoul-movie-awards?inv=3K6GZG", "3K6GZG")
        im = Image.open(io.BytesIO(png)).convert("L")
        w, h = im.size
        band = im.crop((0, 0, w, 100)).point(lambda v: 255 if v < 140 else 0)
        bbox = band.getbbox()
        check("事件二维码顶部标题宽度正常（不是极小的方框字）", bbox is not None and (bbox[2] - bbox[0]) > 400, bbox)
        png2 = promo.render_promo_qr_png("A08", "艾丽丝推广")
        check("推广员二维码也能出图", len(png2) > 2000)
    finally:
        promo._SYS_FONT, promo._SYS_FONT_BOLD = saved


def main():
    static_checks()
    os.environ.setdefault("PUBLIC_BASE_URL", "https://lvhanzhushou.com")
    dsn = os.environ.get("LVHAN_TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:54329/lvhan_ref_fix_v1")
    dbname = dsn.rsplit("/", 1)[-1].split("?")[0] or "lvhan_ref_fix_v1"
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
    import me as M
    app = FastAPI()
    app.include_router(C.router)
    H = {"X-LH": "1"}
    rnd = random.randint(100000, 999999)
    seq = [0]

    def dev():
        seq[0] += 1
        return "%032x" % (rnd * 1000 + seq[0])

    def reg(nick, inv=None, cookie_inv=None, device=None):
        C._rl.clear()
        cli = TestClient(app)
        cli.headers.update({"X-LH-Dev": device or dev()})
        if cookie_inv:
            cli.cookies.set("lh_invc", cookie_inv + ".1700000000000")
        body = {"account": "%s%d@example.com" % (nick, rnd), "password": "Passw0rd!xyz", "nickname": nick, "agree": True}
        if inv:
            body["inv"] = inv
        r = cli.post("/api/community/register", json=body, headers=H)
        assert r.status_code == 200, r.text
        return cli, r.json(), cli.get("/api/community/me").json()["user"]["id"]

    def cnt(cli):
        return cli.get("/api/event/gma/me").json()["count"]

    alice, _, uid_a = reg("alice")
    code = alice.get("/api/event/gma/me").json()["code"]

    # A：注册即绑定
    b1, j, u1 = reg("bob1", inv=code)
    check("A 注册带 inv → 立即绑定（ref=pending/frozen）", j.get("ref") in ("pending", "frozen"), j)
    check("A 邀请人 1/5，且被邀请人没再访问任何页面", cnt(alice) == 1, cnt(alice))
    b2, j, u2 = reg("bob2", cookie_inv=code)
    check("A 注册没传 inv 但 cookie lh_invc 有 → 也绑定", j.get("ref") in ("pending", "frozen") and cnt(alice) == 2, (j, cnt(alice)))
    s1, j, _ = reg("bob3", inv="ZZZZZZ")
    check("A 无效邀请码 → 注册仍成功，ref=bad_code", j.get("ref") == "bad_code" and cnt(alice) == 2, j)
    s2, j, _ = reg("bob4")
    check("A 没有邀请码 → ref 为空", j.get("ref") is None, j)
    # 规则仍在：同一设备
    same, j, _ = reg("samedev", inv=code, device=alice.headers["X-LH-Dev"])
    check("A 与邀请人同设备注册 → excluded，不计入", j.get("ref") == "excluded" and cnt(alice) == 2, j)
    # 同一设备只计一个号
    d2 = dev()
    reg("twin1", inv=code, device=d2)
    n_after1 = cnt(alice)
    _, j, _ = reg("twin2", inv=code, device=d2)
    check("A 同一设备再注册第二个号 → excluded，只计一个", j.get("ref") == "excluded" and cnt(alice) == n_after1, j)
    # 自己的邀请码
    r = alice.post("/api/ref/bind", json={"code": code, "soft": True}, headers=H)
    check("自己的码 soft 绑定 → self", r.json().get("status") == "self", r.text)

    # 自动补绑（soft）
    g = TestClient(app)
    r = g.post("/api/ref/bind", json={"code": code, "soft": True}, headers=H)
    check("soft 绑定：未登录 → 200 guest，不报错", r.status_code == 200 and r.json().get("status") == "guest", r.text)
    r = g.post("/api/ref/bind", json={"code": code}, headers=H)
    check("非 soft：未登录仍 401（老行为不变）", r.status_code == 401, r.status_code)
    late, _, ul = reg("late")                      # 注册时没带码
    before = cnt(alice)
    r = late.post("/api/ref/bind", json={"code": code, "soft": True}, headers=H)
    check("自动补绑：之后任意页面带暂存码 → 绑定成功并计数", r.json().get("status") in ("pending", "frozen") and cnt(alice) == before + 1, r.text)
    r = late.post("/api/ref/bind", json={"code": code, "soft": True}, headers=H)
    check("重复补绑 → already，不重复计", r.json().get("status") == "already" and cnt(alice) == before + 1, r.text)
    # 48 小时规则仍在
    old, _, uo = reg("oldie")
    import psycopg
    with psycopg.connect(dsn) as c2:
        c2.execute("UPDATE c_users SET created_at=now()-interval '49 hours' WHERE id=%s", (uo,))
        c2.commit()
    r = old.post("/api/ref/bind", json={"code": code, "soft": True}, headers=H)
    check("超过 48 小时的老账号补绑 → not_new", r.json().get("status") == "not_new", r.text)

    # C：补录 —— 规则检查 + 审计
    with psycopg.connect(dsn) as c2:
        c2.row_factory = psycopg.rows.dict_row
        def one(sql, *a):
            return c2.execute(sql, a).fetchone()
        v1, _, uv1 = reg("lost1")
        v2, _, uv2 = reg("lost2")
        v3, _, uv3 = reg("lost3")
        base = cnt(alice)
        with M.db() as conn:
            out = M.backfill_invite(conn, code, uv1, "测试补录", "admin")
        check("补录：符合规则 → added，计数 +1", out[0] == "added" and cnt(alice) == base + 1, out)
        with M.db() as conn:
            out = M.backfill_invite(conn, code, uv1, "测试补录", "admin")
        check("补录：已有邀请记录 → skipped，不重复", out[0] == "skipped" and cnt(alice) == base + 1, out)
        with M.db() as conn:
            out = M.backfill_invite(conn, code, uid_a, "测试补录", "admin")
        check("补录：邀请人自己 → skipped", out[0] == "skipped" and "自己" in out[1], out)
        with M.db() as conn:
            out = M.backfill_invite(conn, "AAAAAA", uv2, "测试补录", "admin")
        check("补录：邀请码不存在 → skipped", out[0] == "skipped", out)
        c2.execute("UPDATE c_users SET created_at=now()-interval '50 hours' WHERE id=%s", (uv2,))
        c2.commit()
        with M.db() as conn:
            out = M.backfill_invite(conn, code, uv2, "测试补录", "admin")
        check("补录：账号超过 48 小时 → skipped", out[0] == "skipped" and "48" in out[1], out)
        # 同设备：被邀请人的已知设备 = 邀请人的设备
        c2.execute("INSERT INTO c_ref_devices(user_id,dev_hash) SELECT %s, dev_hash FROM c_ref_devices WHERE user_id=%s LIMIT 1", (uv3, uid_a))
        c2.commit()
        with M.db() as conn:
            out = M.backfill_invite(conn, code, uv3, "测试补录", "admin")
        check("补录：与邀请人同设备 → skipped（同设备规则不放宽）", out[0] == "skipped" and "同一设备" in out[1], out)
        row = one("SELECT status,flags,reason FROM c_ref_invites WHERE invitee_id=%s", uv1)
        check("补录记录：pending + 标记「管理员补录」+ 依据", row["status"] == "pending" and row["flags"] == "管理员补录" and "测试补录" in row["reason"], row)
        n_audit = one("SELECT count(*) AS n FROM c_ref_backfill")["n"]
        n_added = one("SELECT count(*) AS n FROM c_ref_backfill WHERE outcome='added'")["n"]
        check("每次补录（含跳过）都有审计行", n_audit >= 6 and n_added == 1, (n_audit, n_added))

        # 一次性任务：按注册时间唯一匹配
        t_ok = datetime.datetime(2026, 11, 28, 11, 41, 45, 540000, tzinfo=datetime.timezone.utc)
        w1, _, uw1 = reg("jobok")
        w2, _, uw2 = reg("jobdup1")
        w3, _, uw3 = reg("jobdup2")
        c2.execute("UPDATE c_users SET created_at=%s WHERE id=%s", (t_ok - datetime.timedelta(seconds=1), uw1))
        t_dup = datetime.datetime(2026, 11, 28, 11, 45, 11, 242000, tzinfo=datetime.timezone.utc)
        c2.execute("UPDATE c_users SET created_at=%s WHERE id IN (%s,%s)", (t_dup - datetime.timedelta(seconds=1), uw2, uw3))
        # 让账号看起来在 48 小时内：把邀请有奖起始/结束放宽，并把「现在」之外的账号时间留给补录的 48h 判断
        c2.execute("UPDATE c_ref_config SET starts_at='2026-01-01', ends_at='2030-01-01' WHERE id=1")
        c2.commit()
        old_jobs = M.BACKFILL_JOBS
        M.BACKFILL_JOBS = [("t-job", "a", code, t_ok.isoformat()), ("t-job", "b", code, t_dup.isoformat()), ("t-job", "c", code, "2026-11-28T09:00:00+00:00")]
        try:
            M.run_backfill_jobs()
            rows = c2.execute("SELECT target,outcome,detail FROM c_ref_backfill WHERE job_key='t-job' ORDER BY target").fetchall()
            by = {r["target"]: r for r in rows}
            check("一次性任务：按时间唯一匹配到账号 + 规则通过 → added",
                  by["a"]["outcome"] == "added" and one("SELECT 1 AS x FROM c_ref_invites WHERE invitee_id=%s", uw1), by["a"])
            check("一次性任务：同一时刻匹配到多个账号 → 不补", by["b"]["outcome"] == "skipped" and "不唯一" in by["b"]["detail"], by["b"])
            check("一次性任务：找不到账号 → 不补", by["c"]["outcome"] == "skipped" and "0 个" in by["c"]["detail"], by["c"])
            n1 = one("SELECT count(*) AS n FROM c_ref_backfill WHERE job_key='t-job'")["n"]
            M.run_backfill_jobs()
            n2 = one("SELECT count(*) AS n FROM c_ref_backfill WHERE job_key='t-job'")["n"]
            check("一次性任务幂等：再跑不重复写", n1 == 3 and n2 == 3, (n1, n2))
        finally:
            M.BACKFILL_JOBS = old_jobs
        # 唯一匹配且在 48h 内 → added：把时间设成「刚刚」，并让任务时间也是刚刚
        z, _, uz = reg("jobnow")
        c2.execute("UPDATE c_users SET created_at=now()-interval '3 hours' WHERE id=%s", (uz,))
        c2.commit()
        now = c2.execute("SELECT created_at AS t FROM c_users WHERE id=%s", (uz,)).fetchone()["t"]
        M.BACKFILL_JOBS = [("t-job2", "a", code, (now + datetime.timedelta(seconds=1)).isoformat())]  # 窗口：注册时间前 6 秒~后 2 秒
        before = cnt(alice)
        try:
            M.run_backfill_jobs()
        finally:
            M.BACKFILL_JOBS = old_jobs
        r = one("SELECT outcome FROM c_ref_backfill WHERE job_key='t-job2'")
        check("一次性任务：唯一匹配 + 规则通过 → added，计数 +1", r and r["outcome"] == "added" and cnt(alice) == before + 1, (r, cnt(alice), before))
        check("正式任务清单 4 条：3K6GZG ×2、TZCGEU ×2",
              [x[2] for x in old_jobs].count("3K6GZG") == 2 and [x[2] for x in old_jobs].count("TZCGEU") == 2 and len(old_jobs) == 4)

    # 管理员接口
    adm = TestClient(app)
    check("未登录管理员不能补录", adm.post("/api/admin/referral/backfill", json={}, headers=H).status_code in (401, 403))
    r = adm.post("/api/admin/login", json={"password": "test-admin-pw-1234"}, headers=H)
    check("admin login", r.status_code == 200, r.text)
    q1, _, uq = reg("manual1")
    r = adm.post("/api/admin/referral/backfill", json={"code": code, "invitee": str(uq), "evidence": "日志：先看活动页后注册"}, headers=H)
    check("管理员补录接口：added", r.status_code == 200 and r.json()["outcome"] == "added", r.text)
    r = adm.post("/api/admin/referral/backfill", json={"code": code, "invitee": str(uq), "evidence": "日志：先看活动页后注册"}, headers=H)
    check("管理员补录接口：重复 → skipped", r.json()["outcome"] == "skipped", r.text)
    r = adm.post("/api/admin/referral/backfill", json={"code": code, "invitee": str(uq), "evidence": "x"}, headers=H)
    check("管理员补录接口：必须写依据", r.status_code == 400, r.text)
    items = adm.get("/api/admin/referral/backfill").json()["items"]
    check("审计列表：含 actor=admin 且有依据", any(i["actor"] == "admin" and i["outcome"] == "added" and "活动页" in i["detail"] for i in items), items[:2])
    check("迁移可重复执行", True)
    with psycopg.connect(dsn) as c4:
        c4.execute(M.MIGRATE_ME)
        c4.execute(M.MIGRATE_ME)
        c4.commit()

    print("RESULT", "ok", ok, "fail", fail)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    code = main()
    print("%s passed, %s failed" % (ok, fail))
    sys.exit(code)
