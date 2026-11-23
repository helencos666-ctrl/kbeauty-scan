"""找搭子（搭子区）端到端校验（被 verify_site_e2e.py 调用，也可单独运行）。

本地：没有数据库，/api/dazi/posts 等在浏览器里 mock；/api/dazi/options、/api/dazi/check 走真实后端。
      覆盖：首页/社区/我的入口、发帖表单（多图、字段）、筛选参数、私信入口、举报、拉黑、联系方式过滤提示。
线上（LH_LIVE_BASE）：只读 + 无副作用接口：页面打开、options 词条、列表筛选、联系方式预检、
      未登录发帖/举报/私信被拒（401），并截手机尺寸图。不向线上写任何帖子或举报。
"""
from __future__ import annotations

import json, re
from pathlib import Path

H = {"X-LH": "1"}
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
TERMS = ["首尔搭子", "韩国旅游搭子", "饭搭子", "逛吃搭子", "三人团签二缺一", "团签拼人", "拼房", "演唱会拼车",
         "济州岛搭子", "釜山搭子", "在韩搭子", "长驻首尔搭子", "一日游拼团", "包车拼车", "接送机", "仅限女生"]

MOCK_POSTS = {
    "personal": [
        {"id": 901, "title": "12月首尔饭搭子，明洞烤肉AA", "excerpt": "12/5-12/8 在首尔，想找饭搭子一起吃烤肉", "author": "示例A", "author_id": 71,
         "has_avatar": False, "time": "2026-11-23 10:00", "author_official": False, "cover": None, "comments": 2, "likes": 1,
         "dazi": {"city": "seoul", "city_name": "首尔", "kind": "food", "kind_name": "饭搭子·逛吃", "date_from": "2026-12-05",
                  "date_to": "2026-12-08", "people": 2, "gender": "female", "gender_name": "仅限女生", "expired": False},
         "expired": False, "is_biz": False, "biz_verified": False},
        {"id": 902, "title": "上个月的济州岛拼车", "excerpt": "已经过期的帖子", "author": "示例B", "author_id": 72,
         "has_avatar": False, "time": "2026-10-01 10:00", "author_official": False, "cover": None, "comments": 0, "likes": 0,
         "dazi": {"city": "jeju", "city_name": "济州岛", "kind": "car", "kind_name": "包车拼车·接送机", "date_from": "2026-10-10",
                  "date_to": None, "people": 3, "gender": "any", "gender_name": "不限", "expired": True},
         "expired": True, "is_biz": False, "biz_verified": False},
    ],
    "merchant": [
        {"id": 903, "title": "首尔包车一日游（商家）", "excerpt": "正规包车服务", "author": "示例商家", "author_id": 73,
         "has_avatar": False, "time": "2026-11-22 10:00", "author_official": False, "cover": None, "comments": 0, "likes": 0,
         "dazi": {"kind": "car", "kind_name": "包车拼车·接送机"}, "expired": False, "is_biz": True, "biz_verified": True},
    ],
}


def _ctx(p):
    browser = p.chromium.launch(headless=True, channel="chrome", args=["--disable-gpu"])
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, user_agent=UA,
                              is_mobile=True, has_touch=True, service_workers="block")
    ctx.add_init_script("""(function(){try{var d=new Date(Date.now()+9*3600e3).toISOString().slice(0,10);
        localStorage.setItem('lh_opening_popup_day', d);}catch(e){}})();""")
    return browser, ctx


def _api_checks(base, check, tag, live):
    import httpx
    c = httpx.Client(base_url=base, timeout=20, follow_redirects=True)
    r = c.get("/dazi")
    check(f"[{tag}] /dazi 打开 200", r.status_code == 200 and "找搭子" in r.text, r.status_code)
    check(f"[{tag}] /dazi SEO 标题用真实词条", "<title>找搭子｜首尔搭子" in r.text and "韩国旅游搭子" in r.text, r.text[:300])
    check(f"[{tag}] /dazi 不含奋韩", "奋韩" not in r.text)
    check(f"[{tag}] /dazi/new 打开 200", c.get("/dazi/new").status_code == 200)
    o = c.get("/api/dazi/options")
    ok = o.status_code == 200
    j = o.json() if ok else {}
    words = [w["w"] for w in j.get("hot_words", [])]
    check(f"[{tag}] options 热门词条齐全", ok and all(t in words for t in TERMS), words)
    kinds = [k["name"] for k in j.get("kinds", [])]
    check(f"[{tag}] options 类型词条", all(k in kinds for k in ["饭搭子·逛吃", "团签拼人", "拼房", "演唱会拼车", "包车拼车·接送机", "一日游拼团", "在韩搭子"]), kinds)
    check(f"[{tag}] options 城市+性别", [x["name"] for x in j.get("cities", [])] == ["首尔", "釜山", "济州岛", "其他"]
          and "仅限女生" in [x["name"] for x in j.get("genders", [])], j.get("cities"))
    check(f"[{tag}] options 搭子发帖不要求会员（登录即可）", j.get("require_member") is False and j.get("daily_cap") == 5, (j.get("require_member"), j.get("daily_cap")))
    check(f"[{tag}] options 安全须知含公共场所/不转账/核实机票", any("公共场所" in t for t in j.get("safety_tips", []))
          and any("转账" in t for t in j.get("safety_tips", [])) and any("机票" in t for t in j.get("safety_tips", [])))
    for txt, bad in [("加我微信 abc12345 一起去", True), ("电话 010-1234-5678", True), ("V信：kimlove88", True),
                     ("12月5日到12月8日在首尔，想找饭搭子", False), ("12/5 明洞见，人均3万韩元", False)]:
        r = c.post("/api/dazi/check", json={"text": txt}, headers=H)
        check(f"[{tag}] 联系方式过滤「{txt}」→{'拦截' if bad else '放行'}", r.status_code == 200 and (r.json().get("ok") is (not bad)), r.text[:200])
    r = c.post("/api/dazi/check", json={"text": "x"})
    check(f"[{tag}] check 无 X-LH 头被拒", r.status_code == 403)
    # 分享不再加豆
    r = c.post("/api/points/event", json={"kind": "share", "target_type": "post", "target_id": "1"}, headers=H)
    check(f"[{tag}] 分享不再加豆（接口 awarded=0）", r.status_code == 200 and r.json().get("awarded") == 0 and "不再" in (r.json().get("reason") or ""), r.text[:200])
    js = c.get("/static/community.js?v=97").text
    check(f"[{tag}] 前端分享不再请求加豆", "kind: 'share'" not in js and "旅行豆 +" not in js)
    pts = c.get("/points").text
    check(f"[{tag}] 旅行豆说明不再写分享奖励", "分享奖励" not in pts and "分享同一条内容" not in pts and "签到、分享" not in pts)
    if live:
        for qs in ["", "?city=seoul", "?kind=food", "?tab=merchant", "?city=jeju&kind=day", "?gender=female", "?date=2026-12-01", "?kw=首尔搭子"]:
            r = c.get("/api/dazi/posts" + qs)
            okj = r.status_code == 200 and isinstance(r.json().get("posts"), list)
            check(f"[{tag}] 列表筛选 {qs or '默认'} 200", okj, r.text[:200])
            if okj and "tab=merchant" in qs:
                check(f"[{tag}] 商家服务只含商家帖", all(p.get("is_biz") for p in r.json()["posts"]))
            if okj and qs == "":
                ps = r.json()["posts"]
                check(f"[{tag}] 默认个人帖不含商家", all(not p.get("is_biz") for p in ps))
                blob = json.dumps(ps, ensure_ascii=False)
                check(f"[{tag}] 列表不露出手机号", not re.search(r"01[0-9][-\s]?\d{3,4}[-\s]?\d{4}", blob))
                exp = [p["expired"] for p in ps]
                check(f"[{tag}] 过期帖沉底", exp == sorted(exp), exp)
                check(f"[{tag}] 官方指南帖在列", any(p.get("author_official") and "找搭子使用指南" in p.get("title", "") for p in ps),
                      [p.get("title") for p in ps][:5])
        r = c.get("/api/dazi/posts?city=xx")
        check(f"[{tag}] 非法城市 400", r.status_code == 400)
        r = c.post("/api/dazi/posts", data={"title": "测试标题", "body": "测试正文测试正文，周末一起逛吃", "city": "seoul", "kind": "food",
                                            "date_from": __import__("datetime").date.today().isoformat()}, headers=H)
        check(f"[{tag}] 未登录发帖被拒且引导登录（401 非会员提示）", r.status_code == 401, (r.status_code, r.text[:100]))
        r = c.post("/api/dazi/report", json={"post_id": 1, "reason": "其他"}, headers=H)
        check(f"[{tag}] 未登录举报被拒 401", r.status_code == 401, r.status_code)
        r = c.post("/api/chat/start", json={"post_id": 1}, headers=H)
        check(f"[{tag}] 未登录私信被拒 401", r.status_code == 401, r.status_code)
        r = c.get("/community?board=group")
        check(f"[{tag}] 社区页可打开", r.status_code == 200)
        sm = c.get("/sitemap.xml")
        check(f"[{tag}] sitemap 含 /dazi", sm.status_code == 200 and "/dazi<" in sm.text, sm.status_code)


def _entries(page, base, check, tag, shot_dir):
    page.goto(base + "/", wait_until="networkidle", timeout=45000)
    page.wait_for_timeout(800)
    a = page.locator('.cats a[href="/dazi"]')
    check(f"[{tag}] 首页吃喝玩乐有找搭子", a.count() == 1 and "找搭子" in a.inner_text())
    boxes = [page.locator(".cats a").nth(i).bounding_box() for i in range(page.locator(".cats a").count())]
    ys = {round(b["y"]) for b in boxes if b}
    check(f"[{tag}] 首页五格同一行对齐", len(boxes) == 5 and len(ys) == 1, boxes)
    page.screenshot(path=str(shot_dir / f"{tag}-home.png"))
    page.goto(base + "/community", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_selector("#tabs", timeout=15000)
    first = page.locator("#tabs > *").first
    check(f"[{tag}] 社区顶部第一个标签是找搭子", first.inner_text().strip() == "找搭子" and first.get_attribute("href") == "/dazi")
    tb = [page.locator("#tabs > *").nth(i).bounding_box() for i in range(page.locator("#tabs > *").count())]
    check(f"[{tag}] 社区顶部标签一行且不溢出", len(tb) == 4 and len({round(b["y"] + b["height"]) for b in tb}) <= 2
          and all(b["x"] >= 0 and b["x"] + b["width"] <= 390 for b in tb), tb)
    page.screenshot(path=str(shot_dir / f"{tag}-community.png"))
    page.goto(base + "/me", wait_until="domcontentloaded", timeout=45000)
    check(f"[{tag}] 我的页有找搭子入口", page.locator('#daziRow[href="/dazi"]').count() == 1)


def run_local(base, check, shot_dir: Path):
    from playwright.sync_api import sync_playwright
    shot_dir.mkdir(parents=True, exist_ok=True)
    _api_checks(base, check, "本地", False)
    seen = {"list": [], "post": [], "chat": [], "report": [], "block": []}
    img = shot_dir / "_dz.jpg"
    from PIL import Image
    Image.new("RGB", (800, 600), (200, 120, 90)).save(img, "JPEG")
    with sync_playwright() as p:
        browser, ctx = _ctx(p)
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))

        def me_route(route):
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"user": {"id": 5, "nickname": "测试", "tier": 1}, "boards": {}, "prices": {}}))

        def posts_route(route):
            req = route.request
            if req.method == "GET":
                seen["list"].append(req.url)
                tab = "merchant" if "tab=merchant" in req.url else "personal"
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"tab": tab, "posts": MOCK_POSTS[tab], "more": False, "next_offset": 2}))
            else:
                body = req.post_data_buffer or b""
                seen["post"].append({"ctype": req.headers.get("content-type", ""), "body": body})
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "id": 999}))

        page.route("**/api/community/me", me_route)
        page.route(re.compile(r".*/api/dazi/posts(\?.*)?$"), posts_route)
        page.route("**/api/chat/start", lambda r: (seen["chat"].append(r.request.post_data), r.fulfill(status=200, content_type="application/json", body='{"ok":true,"thread_id":77}')))
        page.route("**/api/dazi/report", lambda r: (seen["report"].append(r.request.post_data), r.fulfill(status=200, content_type="application/json", body='{"ok":true,"message":"已收到举报"}')))
        page.route("**/api/friends/block/**", lambda r: (seen["block"].append(r.request.url), r.fulfill(status=200, content_type="application/json", body='{"blocked":true}')))
        page.route("**/community/messages/77", lambda r: r.fulfill(status=200, content_type="text/html", body="<html><body>chat</body></html>"))

        _entries(page, base, check, "本地", shot_dir)
        page.goto(base + "/dazi", wait_until="networkidle", timeout=45000)
        page.wait_for_selector(".dz-card", timeout=15000)
        check("[本地] 搭子列表渲染", page.locator(".dz-card").count() == 2)
        check("[本地] 过期帖标已过期", page.locator(".dz-card.exp .dz-tag.exp").count() == 1)
        check("[本地] 仅限女生标签", "仅限女生" in page.inner_text("#list"))
        check("[本地] 热门词条显示", all(t in page.inner_text("#hotWords") for t in ["首尔搭子", "三人团签二缺一", "济州岛搭子"]))
        check("[本地] 安全横幅", "不提前转账" in page.inner_text("#safeBar") and "核实机票" in page.inner_text("#safeBar"))
        page.screenshot(path=str(shot_dir / "本地-dazi.png"))
        n0 = len(seen["list"])
        page.click('#fCity [data-val="jeju"], #fCity [data-city="jeju"]')
        page.wait_for_timeout(500)
        page.click('#fKind [data-val="visa"], #fKind [data-kind="visa"]')
        page.wait_for_timeout(500)
        last = seen["list"][-1] if seen["list"] else ""
        check("[本地] 城市+类型筛选发请求", len(seen["list"]) > n0 and "city=jeju" in last and "kind=visa" in last, last)
        page.fill("#fDate", "2026-12-06"); page.dispatch_event("#fDate", "change"); page.wait_for_timeout(500)
        check("[本地] 日期筛选发请求", "date=2026-12-06" in seen["list"][-1], seen["list"][-1])
        page.locator("#hotWords button", has_text="仅限女生").first.click(); page.wait_for_timeout(500)
        check("[本地] 热门词「仅限女生」设性别筛选", "gender=female" in seen["list"][-1], seen["list"][-1])
        page.click('#tabs [data-tab="merchant"]'); page.wait_for_selector(".dz-tag.biz", timeout=8000)
        check("[本地] 商家服务标签页带商家+已认证", page.locator(".dz-tag.biz").count() >= 1 and page.locator(".dz-tag.ok").count() >= 1
              and "tab=merchant" in seen["list"][-1])
        check("[本地] 商家帖按钮为咨询", page.locator(".dz-msg").first.inner_text().strip() == "咨询")
        page.click('#tabs [data-tab="personal"]'); page.wait_for_timeout(600)
        # 私信入口
        page.locator('.dz-card[data-id="901"] .dz-msg').click()
        page.wait_for_url("**/community/messages/77", timeout=8000)
        check("[本地] 私信入口调用 chat/start 带 post_id", bool(seen["chat"]) and '"post_id":901' in (seen["chat"][-1] or "").replace(" ", ""), seen["chat"])
        page.goto(base + "/dazi", wait_until="networkidle"); page.wait_for_selector(".dz-card")
        # 举报
        page.locator('.dz-card[data-id="901"] .dz-more').click()
        page.wait_for_selector("#sheet", state="visible", timeout=5000)
        page.locator("#sheet button, #sheet a", has_text="举报").first.click()
        page.wait_for_timeout(300)
        page.locator("#sheet input[type=radio]").nth(0).check()
        page.locator("#sheet button", has_text="提交").first.click()
        page.wait_for_timeout(800)
        check("[本地] 举报提交到 /api/dazi/report", bool(seen["report"]) and "901" in seen["report"][-1] and "中介" in seen["report"][-1], seen["report"])
        # 拉黑
        page.goto(base + "/dazi", wait_until="networkidle"); page.wait_for_selector(".dz-card")
        page.on("dialog", lambda d: d.accept())
        page.locator('.dz-card[data-id="901"] .dz-more').click()
        page.wait_for_selector("#sheet", state="visible", timeout=5000)
        page.locator("#sheet button, #sheet a", has_text="拉黑").first.click()
        page.wait_for_timeout(800)
        check("[本地] 拉黑调用 /api/friends/block/71 并移除卡片", any(u.endswith("/api/friends/block/71") for u in seen["block"])
              and page.locator('.dz-card[data-uid="71"]').count() == 0, seen["block"])
        # 发帖
        page.goto(base + "/dazi/new", wait_until="networkidle")
        page.wait_for_selector("#composer:not([hidden])", timeout=8000)
        page.screenshot(path=str(shot_dir / "本地-dazi-new.png"))
        comp = page.locator("#composer")
        comp.locator('#cpCity .cchip[data-val="seoul"]').first.click()
        comp.locator('#cpKind .cchip[data-val="visa"]').first.click()
        comp.locator('#cpGender .cchip[data-val="female"]').first.click()
        page.fill("#cFrom", "2099-01-05"); page.fill("#cTo", "2099-01-09"); page.fill("#cPeople", "3")
        page.fill("#cTitle", "三人团签二缺一 找姐妹")
        page.fill("#cBody", "加我微信 abc12345 详聊")
        page.wait_for_timeout(1500)
        warn = page.inner_text("#cWarn") if page.locator("#cWarn").count() else ""
        check("[本地] 发帖实时提示联系方式", "站外联系方式" in warn, warn)
        page.fill("#cBody", "1月5日到9日去首尔，想找两个姐妹拼三人团签，签证各自走正规旅行社")
        page.set_input_files("#cImgs", [str(img)] * 6)
        page.wait_for_timeout(2500)
        page.click("#cSubmit"); page.wait_for_timeout(2000)
        check("[本地] 拦截到搭子发帖请求", len(seen["post"]) >= 1)
        if seen["post"]:
            rec = seen["post"][-1]; b = rec["body"]
            check("[本地] 搭子发帖为 multipart", "multipart/form-data" in rec["ctype"])
            fields = {k: (re.search((r'name="%s"\r\n\r\n([^\r]*)' % k).encode(), b) or [None, b""])[1].decode("utf-8", "ignore")
                      for k in ["city", "kind", "gender", "date_from", "date_to", "people", "title"]}
            check("[本地] 发帖字段齐全", fields["city"] == "seoul" and fields["kind"] == "visa" and fields["gender"] == "female"
                  and fields["date_from"] == "2099-01-05" and fields["date_to"] == "2099-01-09" and fields["people"] == "3"
                  and "三人团签" in fields["title"], fields)
            check("[本地] 发帖含 6 张图", b.count(b'name="images"') == 6, b.count(b'name="images"'))
        check("[本地] 搭子页无脚本报错", not errs, errs[:3])
        browser.close()


def run_live(base, check, shot_dir: Path):
    from playwright.sync_api import sync_playwright
    shot_dir.mkdir(parents=True, exist_ok=True)
    _api_checks(base, check, "线上", True)
    with sync_playwright() as p:
        browser, ctx = _ctx(p)
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        _entries(page, base, check, "线上", shot_dir)
        page.goto(base + "/dazi", wait_until="networkidle", timeout=45000)
        page.wait_for_selector("#list .dz-card, #list .empty", timeout=20000)
        check("[线上] 搭子页列表加载", page.locator("#list .dz-card").count() >= 1, page.inner_text("#list")[:200])
        page.screenshot(path=str(shot_dir / "线上-dazi.png"))
        page.click('#tabs [data-tab="merchant"]'); page.wait_for_timeout(2000)
        page.screenshot(path=str(shot_dir / "线上-dazi-merchant.png"))
        page.click('#tabs [data-tab="personal"]'); page.wait_for_timeout(1200)
        c = page.locator("#list .dz-card").first
        if c.count():
            c.locator(".dz-msg").click(); page.wait_for_timeout(1500)
            check("[线上] 未登录点私信跳登录", "/login" in page.url, page.url)
        page.goto(base + "/dazi/new", wait_until="networkidle")
        page.wait_for_timeout(1200)
        check("[线上] 发帖面板未登录提示登录", "登录" in page.inner_text("#composer"))
        page.screenshot(path=str(shot_dir / "线上-dazi-new.png"))
        # 分享按钮保留，但点了不再请求加豆
        reqs = []
        page.on("request", lambda r: reqs.append(r.url) if "/api/points/event" in r.url else None)
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)
        pid = None
        try:
            import httpx
            ps = httpx.get(base + "/api/dazi/posts", timeout=20).json().get("posts") or []
            pid = ps[0]["id"] if ps else None
        except Exception:
            pass
        if pid:
            page.on("dialog", lambda d: d.dismiss())
            page.goto(base + "/community/post/%s" % pid, wait_until="networkidle")
            page.wait_for_selector("#share", timeout=15000)
            check("[线上] 帖子分享按钮仍在", page.locator("#share").count() == 1)
            page.evaluate("navigator.share = undefined")
            page.click("#share"); page.wait_for_timeout(1500)
            check("[线上] 点分享不再请求加豆", not reqs, reqs)
        check("[线上] 搭子页无脚本报错", not errs, errs[:3])
        browser.close()


if __name__ == "__main__":
    import os, sys
    ok = fail = 0
    def check(n, c, info=""):
        global ok, fail
        if c: ok += 1; print("PASS", n)
        else: fail += 1; print("FAIL", n, str(info)[:300])
    live = os.environ.get("LH_LIVE_BASE", "").rstrip("/")
    if live:
        run_live(live, check, Path("/workspace/lvhan-buddy"))
    print(ok, "passed", fail, "failed"); sys.exit(1 if fail else 0)
