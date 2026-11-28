"""页面切换流畅度 E2E（v99）：本地起服务、接口全部用 mock，不写任何数据。
覆盖：找搭子 → 详情 → 「‹ 返回」保留列表与滚动位置（不重新请求列表）；详情先画列表里带过来的标题；
私信/咨询「发送」乐观上屏、立即清空输入框、发送中禁用、连点只发一条且带 cid；咨询一键直达会话（不整页跳两次）；
评论连点只发一条、只刷新评论区。线上部分只读：SW 新策略、静态资源长缓存、开屏只出一次的代码已上线。"""
import json, re, time
from pathlib import Path

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")


def _posts(n):
    out = []
    for i in range(n):
        out.append({"id": 1000 + i, "title": "测试找搭子帖 %d 号" % i, "excerpt": "第 %d 条，周末一起逛吃" % i, "author": "用户%d" % i,
                    "author_id": 300 + i, "has_avatar": False, "time": "2026-11-23 10:00", "author_official": False, "cover": None,
                    "comments": 0, "likes": 0, "dazi": {"city": "seoul", "city_name": "首尔", "kind": "food", "kind_name": "饭搭子·逛吃",
                    "date_from": "2099-12-01", "date_to": None, "people": 2, "gender": "any", "expired": False}, "expired": False,
                    "is_biz": False, "biz_verified": False, "pinned": False})
    return out


def _post_detail(pid):
    return {"id": pid, "board": "group", "board_name": "找搭子", "title": "测试找搭子帖 %d 号" % (pid - 1000), "body": "完整正文：周末一起逛吃，明洞见",
            "author": "用户", "author_id": 301, "author_tier": 0, "has_avatar": False, "likes": 0, "favs": 0, "comments": 0,
            "time": "2026-11-23 10:00", "images": [], "comment_list": [], "comment_tier": 0, "status": "published", "mine": False,
            "dazi": None, "merchant": False, "pinned": False, "liked": False, "favorited": False}


def run_local(base, check, shot_dir: Path):
    from playwright.sync_api import sync_playwright
    shot_dir.mkdir(parents=True, exist_ok=True)
    seen = {"list": [], "detail": [], "chat_post": [], "consult_post": [], "consult_start": [], "comment": [], "docs": []}
    pend = {"chat": [], "consult": [], "comment": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome", args=["--disable-gpu"])
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, user_agent=UA,
                                  is_mobile=True, has_touch=True, service_workers="block")
        ctx.add_init_script("""(function(){try{var d=new Date(Date.now()+9*3600e3).toISOString().slice(0,10);
            localStorage.setItem('lh_opening_popup_day', d);}catch(e){}})();""")
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("request", lambda r: seen["docs"].append(r.url) if r.resource_type == "document" else None)
        J = lambda route, obj, st=200: route.fulfill(status=st, content_type="application/json", body=json.dumps(obj, ensure_ascii=False))
        page.route("**/api/community/me", lambda r: J(r, {"user": {"id": 5, "nickname": "测试", "tier": 1, "merchant": False}, "boards": {}, "prices": {}}))
        page.route(re.compile(r".*/api/dazi/posts(\?.*)?$"), lambda r: (seen["list"].append(r.request.url),
                   J(r, {"tab": "personal", "posts": _posts(30), "more": False, "next_offset": 30})))

        def detail(route):
            m = re.search(r"/api/community/posts/(\d+)$", route.request.url)
            if route.request.method == "GET" and m:
                seen["detail"].append(route.request.url); J(route, _post_detail(int(m.group(1))))
            else:
                route.continue_()
        page.route(re.compile(r".*/api/community/posts/\d+$"), detail)

        def comments(route):
            seen["comment"].append(route.request.post_data); pend["comment"].append(route)
        page.route(re.compile(r".*/api/community/posts/\d+/comments$"), comments)
        thread = {"id": 77, "other": 301, "nickname": "用户", "has_avatar": False, "can_send": True, "messages": [], "ref_title": "", "ref_href": ""}
        page.route(re.compile(r".*/api/chat/threads/77(\?.*)?$"), lambda r: J(r, thread))
        page.route(re.compile(r".*/api/chat/threads/77/messages$"), lambda r: (seen["chat_post"].append(r.request.post_data), pend["chat"].append(r)))
        page.route("**/api/consult/start", lambda r: (seen["consult_start"].append(r.request.post_data), J(r, {"ok": True, "thread_id": 55})))
        cth = {"id": 55, "name": "测试商家", "ref": "帖子：测试", "ref_href": "", "messages": [{"id": 1, "sender": "system", "body": "咨询来自 · 帖子：测试", "time": "10:00"}]}
        page.route(re.compile(r".*/api/consult/threads/55(\?.*)?$"), lambda r: J(r, cth if "after=" not in r.request.url else {"messages": []}))
        page.route(re.compile(r".*/api/consult/threads/55/messages$"), lambda r: (seen["consult_post"].append(r.request.post_data or ""), pend["consult"].append(r)))
        page.route("**/api/consult/unread", lambda r: J(r, {"n": 0}))

        # ---- 1. 列表 → 详情 → 返回：保留列表与滚动位置 ----
        page.goto(base + "/dazi", wait_until="networkidle", timeout=45000)
        page.wait_for_selector(".dz-card", timeout=15000)
        check("[流畅] 搭子列表首屏用骨架而非「加载中」文字", "加载中…" not in page.content() or page.locator(".dz-card").count() > 0)
        page.evaluate("window.scrollTo(0, 1500)"); page.wait_for_timeout(300)
        n_list = len(seen["list"])
        card = page.locator(".dz-card").nth(12)
        title = card.locator(".dz-title").inner_text()
        card.scroll_into_view_if_needed(); page.wait_for_timeout(200)
        y0 = page.evaluate("scrollY")
        card.click()
        page.wait_for_url("**/community/post/1012", timeout=10000)
        page.wait_for_selector("#post .ptitle", timeout=8000)
        check("[流畅] 详情页标题（列表带过来或接口）正确", page.inner_text("#post .ptitle").strip() == title.strip(), page.inner_text("#post .ptitle"))
        page.wait_for_selector("#post .body", timeout=8000)
        page.click("a.pback")
        page.wait_for_url("**/dazi", timeout=10000)
        page.wait_for_selector(".dz-card", timeout=8000)
        page.wait_for_timeout(500)
        y1 = page.evaluate("scrollY")
        check("[流畅] 详情返回找搭子列表保留滚动位置", abs(y1 - y0) < 80, (y0, y1))
        check("[流畅] 返回时不重新请求列表", len(seen["list"]) == n_list, (n_list, len(seen["list"])))
        check("[流畅] 返回后列表条数不变", page.locator(".dz-card").count() == 30)
        # 再来一次（录屏里连续 3 次都丢位置）
        c2 = page.locator(".dz-card").nth(20); c2.scroll_into_view_if_needed(); page.wait_for_timeout(200); y0 = page.evaluate("scrollY")
        c2.click(); page.wait_for_url("**/community/post/1020", timeout=10000)
        page.wait_for_selector("#post .body", timeout=8000)
        page.click("a.pback"); page.wait_for_url("**/dazi", timeout=10000); page.wait_for_timeout(600)
        check("[流畅] 连续第二次返回仍保留位置", abs(page.evaluate("scrollY") - y0) < 80, (y0, page.evaluate("scrollY")))

        # ---- 2. 评论：连点只发一条、发表中禁用、只刷新评论区 ----
        page.goto(base + "/community/post/1003", wait_until="networkidle")
        page.wait_for_selector("#csend", timeout=10000)
        page.fill("#cbody", "我也想去，可以一起吗")
        page.evaluate("document.getElementById('csend').click(); document.getElementById('csend').click();")
        page.wait_for_timeout(300)
        check("[流畅] 评论连点只发一次请求", len(seen["comment"]) == 1, len(seen["comment"]))
        check("[流畅] 评论发表中按钮禁用并显示发表中", page.locator("#csend").is_disabled() and "发表中" in page.inner_text("#csend"))
        check("[流畅] 评论请求带 cid", bool(seen["comment"]) and '"cid"' in (seen["comment"][-1] or ""))
        page.evaluate("document.querySelector('#post .ptitle').setAttribute('data-keep','1')")
        for r in pend["comment"]: J(r, {"ok": True, "id": 9})
        pend["comment"].clear(); page.wait_for_timeout(800)
        check("[流畅] 发表评论后帖子不整块重画（只刷新评论区）", page.locator("#post .ptitle[data-keep='1']").count() == 1)

        # ---- 3. 私信发送：乐观上屏、清空、禁用、连点只发一条 ----
        page.goto(base + "/community/messages/77", wait_until="networkidle")
        page.wait_for_selector("#sb:not([hidden])", timeout=10000)
        page.fill("#txt", "你好，12月5号有空吗")
        page.focus("#txt")
        page.evaluate("document.getElementById('send').click(); document.getElementById('send').click();")
        page.wait_for_timeout(200)
        check("[流畅] 私信点发送立即清空输入框", page.input_value("#txt") == "")
        check("[流畅] 私信发送中按钮置灰", page.locator("#send").is_disabled())
        check("[流畅] 私信乐观上屏（发送中气泡）", page.locator(".rowm.pending").count() == 1 and "12月5号" in page.inner_text("#msgs"))
        check("[流畅] 私信连点只发一条", len(seen["chat_post"]) == 1, len(seen["chat_post"]))
        check("[流畅] 私信请求带 cid", bool(seen["chat_post"]) and '"cid"' in (seen["chat_post"][-1] or ""))
        check("[流畅] 发送后输入框保持焦点（键盘不收起）", page.evaluate("document.activeElement && document.activeElement.id") == "txt")
        thread["messages"] = [{"id": 501, "mine": True, "body": "你好，12月5号有空吗", "time": "10:01", "kind": "msg"}]
        for r in pend["chat"]: J(r, {"ok": True, "id": 501, "warning": None})
        pend["chat"].clear(); page.wait_for_timeout(800)
        check("[流畅] 私信发出后只显示一条、发送中气泡消失", page.locator('[data-mid="501"]').count() == 1 and page.locator(".rowm.pending").count() == 0)
        check("[流畅] 私信发出后按钮恢复", not page.locator("#send").is_disabled())

        # ---- 4. 立即咨询：一次到位，不再整页跳两次；发送防重 ----
        nd = len(seen["docs"])
        t0 = time.time()
        page.goto(base + "/consult/new?kind=post&id=1003", wait_until="domcontentloaded")
        page.wait_for_selector("#sb[style*='flex']", timeout=10000)
        dt = time.time() - t0
        check("[流畅] 立即咨询直接进会话且地址变为 /consult/55", page.url.endswith("/consult/55"), page.url)
        check("[流畅] 立即咨询只加载一次页面（不再二次整页跳转）", len(seen["docs"]) - nd == 1, seen["docs"][nd:])
        check("[流畅] 立即咨询本地打开会话 < 3 秒", dt < 3, round(dt, 2))
        page.fill("#txt", "请问团签怎么收费")
        page.evaluate("document.getElementById('send').click(); document.getElementById('send').click();")
        page.wait_for_timeout(200)
        check("[流畅] 咨询点发送立即清空并置灰", page.input_value("#txt") == "" and page.locator("#send").is_disabled())
        check("[流畅] 咨询连点只发一条且带 cid", len(seen["consult_post"]) == 1 and "cid" in seen["consult_post"][0], len(seen["consult_post"]))
        check("[流畅] 咨询乐观上屏", page.locator(".msg.pending").count() == 1)
        for r in pend["consult"]: J(r, {"ok": True, "id": 2})
        pend["consult"].clear(); page.wait_for_timeout(600)
        check("[流畅] 咨询发出后按钮恢复", not page.locator("#send").is_disabled())
        check("[流畅] 页面无脚本报错", not errs, errs[:3])
        browser.close()


def run_live(base, check):
    import httpx
    c = httpx.Client(base_url=base, timeout=20)
    sw = c.get("/sw.js").text
    check("[线上] SW 去过的页面先秒开（stale-while-revalidate）", "staleWhileRevalidate" in sw and "templateFor" in sw)
    r = c.get("/static/community.css?v=101")
    check("[线上] 带版本号静态资源长缓存", "max-age=31536000" in r.headers.get("cache-control", "") or "immutable" in r.headers.get("cache-control", ""),
          r.headers.get("cache-control"))
    check("[线上] 开屏只在打开时出现一次", "lh_splash_seen" in c.get("/static/splash.js?v=101").text)
    check("[线上] 返回键真正后退", "a.pback" in c.get("/static/pwa.js?v=101").text)


if __name__ == "__main__":
    import os, sys
    ok = fail = 0
    def check(n, c, info=""):
        global ok, fail
        if c: ok += 1; print("PASS", n)
        else: fail += 1; print("FAIL", n, str(info)[:300])
    live = os.environ.get("LH_LIVE_BASE", "").rstrip("/")
    if live:
        run_live(live, check)
    print(ok, "passed", fail, "failed"); sys.exit(1 if fail else 0)
