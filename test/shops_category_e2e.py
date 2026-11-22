"""商家页分类点击 → 分类列表 → 商家详情 的端到端检查（手机视口 390x844）。

run_local(base, check, shot_dir)：本地服务没有数据库，用 Playwright 拦截商家接口返回固定样例，
    只校验页面逻辑：点分类不整页刷新、列表出现在可视区域、卡片链接、空状态与入驻入口。
run_live(base, check, shot_dir)：只读访问线上（不登录、不发帖、不写库），公开分类逐个点开，
    每个真实商家卡片都要能打开详情；并专门核对用户点名的 3 家已入驻商家。

    LH_LIVE_BASE=https://lvhanzhushou.com python test/verify_site_e2e.py
"""
from __future__ import annotations

import json
import re

ALL_CATS = ["guide", "clinic", "food", "airport", "hotel", "interp", "kpop", "car", "sim", "logistics", "daigou", "other"]
HIDDEN_CATS = {"clinic"}
CATS = [c for c in ALL_CATS if c not in HIDDEN_CATS]
CAT_NAMES = {"guide": "地陪向导", "clinic": "医美", "food": "美食代点外卖", "airport": "接送机", "hotel": "住宿",
             "interp": "翻译", "kpop": "追星活动", "car": "租车", "sim": "通讯电话卡", "logistics": "物流快递",
             "daigou": "代购", "other": "其它"}
# 用户点名：点炸鸡 2 家、通讯电话卡 1 家
MUST_SEE = [("food", "明洞炸鸡带点服务"), ("food", "代点外卖 炸鸡等等"), ("sim", "蓝憬通讯")]
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
      "Mobile/15E148 MicroMessenger/8.0.50(0x18003237) NetType/WIFI Language/zh_CN")
BANNED_PUBLIC = ["奋韩", "微信号", "加微", "二维码"]


def _context(p):
    browser = p.chromium.launch(headless=True, channel="chrome", args=["--disable-gpu"])
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, user_agent=UA,
                              is_mobile=True, has_touch=True, service_workers="block")
    return browser, ctx


def _list_in_view(page):
    return page.evaluate("""() => {
      const l = document.getElementById('svcList');
      if (!l || l.hidden) return false;
      const r = l.getBoundingClientRect();
      return r.top < innerHeight * 0.6 && r.bottom > 0;
    }""")


def _cards(page):
    return page.evaluate("""() => Array.from(document.querySelectorAll('#svcList article.mpost:not(.example-card)')).map(a => ({
      kind: a.dataset.kind, href: a.querySelector('a.shop-link') ? a.querySelector('a.shop-link').href : '',
      name: a.querySelector('h3') ? a.querySelector('h3').textContent.trim() : ''}))""")


def _tap_cat(page, cat):
    """点（tap）分类磁贴，返回是否同一文档内切换（没有整页刷新）。"""
    tile = page.locator('#catChips [data-cat="%s"]' % cat)
    tile.scroll_into_view_if_needed()
    page.evaluate("() => { window.__lhNoReload = 1; }")
    tile.tap()
    page.wait_for_function("(c) => location.search.indexOf('cat=' + c) >= 0", arg=cat, timeout=8000)
    page.wait_for_function("() => !document.querySelector('#svcList .loading')", timeout=30000)
    page.wait_for_timeout(800)  # smooth scroll
    return page.evaluate("() => window.__lhNoReload === 1")


def run_local(base, check, shot_dir):
    from playwright.sync_api import sync_playwright
    providers = [{"id": 1, "category": "food", "category_name": "美食代点外卖", "name": "样例炸鸡代点", "area": "明洞",
                  "services": "样例", "photo": "", "photos": []}]
    posts = [{"id": 42, "title": "样例代点外卖帖", "merchant_cat": "food", "merchant_cat_name": "美食代点外卖",
              "author": "样例商家", "area": "首尔", "excerpt": "样例", "cover": None}]

    def fake(route):
        url = route.request.url
        m = re.search(r"cat=([a-z]+)", url)
        if "/api/providers" in url:
            items = [x for x in providers if not m or x["category"] == m.group(1)]
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"items": items}))
        if "/api/community/merchant-feed" in url:
            c = m.group(1) if m else "all"
            ps = [x for x in posts if c == "all" or x["merchant_cat"] == c]
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"posts": ps, "has_more": False}))
        return route.continue_()

    with sync_playwright() as p:
        browser, ctx = _context(p)
        ctx.route(re.compile(r".*/api/providers(\?.*)?$"), fake)
        ctx.route(re.compile(r".*/api/community/merchant-feed.*"), fake)
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(base + "/shops", wait_until="load", timeout=30000)
        check("本地：商家页没有医美分类磁贴", page.locator('#catChips [data-cat="clinic"]').count() == 0)
        page.wait_for_function("() => document.querySelector('[data-cat=food] .cnt')", timeout=15000)
        cnt = page.evaluate("() => { const t = document.querySelector('[data-cat=food] .cnt'); return t ? t.textContent : ''; }")
        check("本地：美食分类角标显示已入驻 2 家（样例）", cnt == "2", cnt)
        cnt_sim = page.evaluate("() => { const t = document.querySelector('[data-cat=sim] .cnt'); return t ? t.textContent : ''; }")
        check("本地：通讯电话卡角标显示合作商家 1 家", cnt_sim == "1", cnt_sim)
        same = _tap_cat(page, "food")
        check("本地：点分类不整页刷新", same)
        check("本地：点分类后列表滚到可视区域", _list_in_view(page))
        cards = _cards(page)
        hrefs = sorted(c["href"].replace(base, "") for c in cards)
        check("本地：美食列表含入驻申请商家与商家发布", hrefs == ["/community/post/42", "/shops/p/1"], hrefs)
        page.screenshot(path=str(shot_dir / "local-shops-food.jpg"), type="jpeg", quality=80)
        _tap_cat(page, "sim")
        cards = _cards(page)
        check("本地：通讯电话卡列表有蓝憬通讯", any(c["name"] == "蓝憬通讯" and c["href"].endswith("/shops/m/lanjing") for c in cards), cards)
        _tap_cat(page, "guide")
        check("本地：没有商家的分类显示空状态和入驻入口",
              page.locator("#svcList [data-empty='1'] a[href='/shops/join-guide']").count() == 1)
        check("本地：空分类仍附示范案例", page.locator("#svcList .example-card").count() == 1)
        page.go_back()
        page.wait_for_function("() => location.search.indexOf('cat=sim') >= 0", timeout=5000)
        page.wait_for_timeout(800)
        check("本地：返回键回到上一个分类", page.locator("#feedTitle").inner_text().strip() == "通讯电话卡")
        page.goto(base + "/shops/m/lanjing", wait_until="load")
        page.wait_for_function("() => document.getElementById('hname').textContent.trim() === '蓝憬通讯'", timeout=10000)
        check("本地：蓝憬通讯详情页可打开", True)
        check("本地：蓝憬详情用站内咨询", page.locator("a.consult-btn[href*='kind=partner']").count() == 1)
        body = page.inner_text("body")
        check("本地：蓝憬详情无站外联系方式和奋韩", not any(w in body for w in BANNED_PUBLIC), [w for w in BANNED_PUBLIC if w in body])
        check("本地：商家页/详情无脚本报错", not errs, errs[:3])
        ctx.close()
        browser.close()


def run_live(base, check, shot_dir):
    import httpx
    from playwright.sync_api import sync_playwright
    summary = {}
    seen = {}
    with sync_playwright() as p:
        browser, ctx = _context(p)
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        bad_resp = []
        page.on("response", lambda r: bad_resp.append("%s %s" % (r.status, r.url)) if r.status >= 500 else None)
        page.goto(base + "/shops", wait_until="load", timeout=60000)
        page.wait_for_timeout(2500)
        ads = page.eval_on_selector_all("#adStrip a.ad-card", "els => els.map(e => e.href)")
        check("线上：商家页轮播不含医美入口", all("cat=clinic" not in (h or "") for h in ads), ads)
        check("线上：商家页轮播仍有 3 张公开卡片", len(ads) == 3, ads)
        check("线上：商家页没有医美分类磁贴", page.locator('#catChips [data-cat="clinic"]').count() == 0)
        page.goto(base + "/shops?cat=clinic", wait_until="load", timeout=60000)
        page.wait_for_timeout(800)
        check("线上：直接打开医美分类显示暂未开放", "暂未开放" in page.inner_text("body"))
        page.goto(base + "/shops", wait_until="load", timeout=60000)
        page.wait_for_timeout(1500)
        ads = page.eval_on_selector_all("#adStrip a.ad-card", "els => els.map(e => e.href)")
        for h in ads:
            r = httpx.get(h, headers={"User-Agent": UA}, follow_redirects=True, timeout=30)
            final = str(r.url)
            check("线上：轮播 %s 可打开" % h.replace(base, ""),
                  r.status_code == 200 and final.rstrip("/") != (base + "/community") and final.rstrip("/") != (base + "/shops"),
                  "%s -> %s" % (r.status_code, final))
        for cat in CATS:
            same = _tap_cat(page, cat)
            title = page.locator("#feedTitle").inner_text().strip()
            in_view = _list_in_view(page)
            cards = _cards(page)
            empty = page.locator("#svcList [data-empty='1']").count()
            err = page.locator("#svcList .load-err").count()
            check("线上：%s 点开显示列表（不刷新、可见）" % CAT_NAMES[cat], same and in_view and title == CAT_NAMES[cat] and not err,
                  dict(same=same, in_view=in_view, title=title, err=err))
            check("线上：%s 有商家或空状态+入驻入口" % CAT_NAMES[cat],
                  bool(cards) or (empty == 1 and page.locator("#svcList a[href='/shops/join-guide']").count() >= 1))
            summary[cat] = [c["name"] for c in cards]
            for c in cards:
                seen[(cat, c["name"])] = c["href"]
            if cat in ("food", "sim"):
                page.screenshot(path=str(shot_dir / ("live-shops-%s.jpg" % cat)), type="jpeg", quality=80)
        for (cat, name), href in seen.items():
            d = ctx.new_page()
            d.goto(href, wait_until="load", timeout=60000)
            d.wait_for_timeout(1000)
            txt = d.inner_text("body")
            ok_detail = (name in txt or (name == "商家服务" and len(txt.strip()) > 20)) and ("帖子不存在" not in txt) and ("商家不存在" not in txt)  # untitled user posts show the fallback card name
            check("线上：%s「%s」详情可打开" % (CAT_NAMES[cat], name), ok_detail, href)
            if href.endswith("/shops/m/lanjing"):
                d.screenshot(path=str(shot_dir / "live-detail-lanjing.jpg"), type="jpeg", quality=80)
                check("线上：蓝憬详情无站外联系方式和奋韩", not any(w in txt for w in BANNED_PUBLIC))
            d.close()
        for cat, name in MUST_SEE:
            check("线上：用户点名商家「%s」出现在 %s 且可点开" % (name, CAT_NAMES[cat]), (cat, name) in seen, summary.get(cat))
        check("线上：页面无脚本报错", not errs, errs[:3])
        check("线上：无 5xx 响应", not bad_resp, bad_resp[:5])
        ctx.close()
        browser.close()
    print("LIVE SUMMARY", json.dumps(summary, ensure_ascii=False))
    return summary
