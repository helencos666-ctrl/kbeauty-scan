"""拍照翻译端到端校验（被 verify_site_e2e.py 调用，也可单独运行）。

本地：Playwright 手机视口打开 /?tab=card，检查「拍照翻译」按钮、拍照/相册选择、结果逐段展示、存记录；
      本地没有视觉模型密钥，所以 /api/photo-translate 在浏览器里 mock。
线上（LH_LIVE_BASE）：用 PIL 生成真实韩文菜单图上传，确认识别出韩文并给出合理中文；再用浏览器真实走一遍并截图。
照片只在测试目录生成，服务端不保存。
"""
from __future__ import annotations

import json, os, sys, time
from pathlib import Path

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/sand-box/google/Nanum Gothic/NanumGothic-Bold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
]
CJK_FONTS = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"]

MENU_LINES = [("메뉴", 64), ("김치찌개  9,000원", 48), ("된장찌개  8,000원", 48), ("비빔밥  10,000원", 48),
              ("불고기 정식  13,000원", 48), ("공기밥  1,000원", 48), ("매운맛 조절 가능합니다", 40)]
ZH_LINES = [("请不要放香菜", 64), ("我对花生过敏", 64)]

MOCK = {"has_text": True, "kind": "菜单", "dir": "ko2zh", "notes": "",
        "blocks": [{"src": "김치찌개 9,000원", "tr": "泡菜汤 9,000韩元"}, {"src": "비빔밥 10,000원", "tr": "拌饭 10,000韩元"}]}

def _font(paths, size):
    from PIL import ImageFont
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise RuntimeError("no CJK font")

def make_menu(path: Path, lines=MENU_LINES, fonts=FONT_CANDIDATES):
    from PIL import Image, ImageDraw, ImageFilter
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (900, 120 + 95 * len(lines)), (250, 246, 236))
    d = ImageDraw.Draw(im)
    y = 50
    for text, size in lines:
        d.text((60, y), text, fill=(35, 30, 25), font=_font(fonts, size))
        y += 95
    im = im.rotate(1.5, expand=True, fillcolor=(230, 226, 216)).filter(ImageFilter.GaussianBlur(0.6))
    im.save(path, "JPEG", quality=88)
    return path

def _ctx(p):
    browser = p.chromium.launch(headless=True, channel="chrome", args=["--disable-gpu"])
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844}, device_scale_factor=2,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        is_mobile=True, has_touch=True, service_workers="block")
    return browser, ctx

def _ui_flow(page, base, img, check, shot_dir, tag, live=False):
    page.goto(base + "/?tab=card", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector("#photoTrBtn", state="visible", timeout=15000)
    check(f"[{tag}] 拍照翻译按钮可见", page.is_visible("#photoTrBtn") and "拍照翻译" in page.inner_text("#photoTrBtn"))
    mic = page.locator("#micBtn").bounding_box(); ph = page.locator("#photoTrBtn").bounding_box()
    check(f"[{tag}] 拍照翻译在按住说话正下方", bool(mic and ph and abs(mic["x"] - ph["x"]) < 4 and ph["y"] > mic["y"] + mic["height"] - 2), (mic, ph))
    page.screenshot(path=str(shot_dir / f"photo-tr-{tag}-1-button.png"))
    page.click("#photoTrBtn")
    check(f"[{tag}] 点开出现 拍照/相册 选择", page.is_visible("#ptrCam") and page.is_visible("#ptrPick"))
    check(f"[{tag}] 拍照默认 识别韩文→中文", "韩文" in page.inner_text("#ptrDir button.on"))
    cam_capture = page.get_attribute("#fTrCam", "capture"); pick_capture = page.get_attribute("#fTrPick", "capture")
    check(f"[{tag}] 拍照用后置相机、相册不强制相机", cam_capture == "environment" and pick_capture is None and page.get_attribute("#fTrPick", "accept") == "image/*")
    page.screenshot(path=str(shot_dir / f"photo-tr-{tag}-2-sheet.png"))
    with page.expect_file_chooser() as fc:
        page.click("#ptrPick")
    fc.value.set_files(str(img))
    page.wait_for_selector("#ptrBox .ptr-b", timeout=90000 if live else 15000)
    rows = page.locator("#ptrBox .ptr-b")
    n = rows.count()
    txt = page.inner_text("#ptrBox")
    check(f"[{tag}] 结果逐段展示（{n} 段）", n >= 2, txt[:300])
    check(f"[{tag}] 显示韩文原文与中文译文", "김치찌개" in txt and ("泡菜" in txt), txt[:300])
    check(f"[{tag}] 有朗读按钮", page.locator("#ptrBox .pspk").count() >= 1)
    check(f"[{tag}] 保留仅供参考提示", "仅供参考" in txt and "当面" in txt)
    page.click("#ptrSaveAll")
    hist = page.inner_text("#tHist")
    check(f"[{tag}] 全部存到翻译记录", "翻译记录" in hist and "김치찌개" in hist, hist[:200])
    page.locator("#cardOut").scroll_into_view_if_needed()
    page.evaluate("window.scrollTo(0, document.querySelector('#p-card').getBoundingClientRect().top + window.scrollY - 60)")
    page.screenshot(path=str(shot_dir / f"photo-tr-{tag}-3-result.png"), full_page=False)
    page.evaluate("document.querySelector('#ptrBox').scrollIntoView({block:'start'})")
    page.screenshot(path=str(shot_dir / f"photo-tr-{tag}-4-result-blocks.png"), full_page=False)
    return txt

def run_local(base, check, shot_dir: Path):
    from playwright.sync_api import sync_playwright
    shot_dir.mkdir(parents=True, exist_ok=True)
    img = make_menu(shot_dir / "_menu_ko.jpg")
    seen = {}
    def handle(route):
        req = route.request
        seen["n"] = seen.get("n", 0) + 1
        body = req.post_data_buffer or b""
        seen["size"] = len(body); seen["url"] = req.url
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(MOCK, ensure_ascii=False))
    with sync_playwright() as p:
        browser, ctx = _ctx(p)
        ctx.route("**/api/photo-translate**", handle)
        page = ctx.new_page(); errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        _ui_flow(page, base, img, check, shot_dir, "local")
        check("[local] 上传走 /api/photo-translate?dir=ko2zh 且已压缩", seen.get("n") == 1 and "dir=ko2zh" in seen.get("url", "") and 1000 < seen.get("size", 0) < 2_500_000, seen)
        check("[local] 页面无 JS 错误", not errs, errs[:3])
        browser.close()
    import httpx
    r = httpx.post(base + "/api/photo-translate", files={"image": ("x.jpg", b"notimage" * 200, "image/jpeg")}, timeout=15)
    check("[local] 非图片返回 400 友好提示", r.status_code == 400 and "图片" in r.text, r.text[:200])

def run_live(base, check, shot_dir: Path):
    import httpx
    shot_dir.mkdir(parents=True, exist_ok=True)
    img = make_menu(shot_dir / "_menu_ko.jpg")
    t = time.time()
    r = httpx.post(base + "/api/photo-translate?dir=ko2zh", files={"image": ("menu.jpg", img.read_bytes(), "image/jpeg")},
                   headers={"X-LH": "1"}, timeout=120)
    ms = int((time.time() - t) * 1000)
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    (shot_dir / "live_ko2zh.json").write_text(json.dumps({"status": r.status_code, "ms": ms, "body": j}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("LIVE ko2zh", r.status_code, ms, "ms", json.dumps(j, ensure_ascii=False)[:1500])
    blocks = j.get("blocks") or []
    src = " ".join(b.get("src", "") for b in blocks); tr = " ".join(b.get("tr", "") for b in blocks)
    check("[live] 韩文菜单识别 200 且有多段", r.status_code == 200 and len(blocks) >= 4, r.text[:300])
    check("[live] 识别出韩文菜名", all(k in src.replace(" ", "") for k in ("김치찌개", "비빔밥", "불고기")), src)
    check("[live] 中文译文合理（泡菜/拌饭/烤肉，价格保留）", "泡菜" in tr and "拌饭" in tr and "烤肉" in tr and ("9,000" in tr or "9000" in tr), tr)
    zimg = make_menu(shot_dir / "_note_zh.jpg", ZH_LINES, CJK_FONTS)
    r2 = httpx.post(base + "/api/photo-translate?dir=zh2ko", files={"image": ("note.jpg", zimg.read_bytes(), "image/jpeg")},
                    headers={"X-LH": "1"}, timeout=120)
    j2 = r2.json() if r2.headers.get("content-type", "").startswith("application/json") else {}
    (shot_dir / "live_zh2ko.json").write_text(json.dumps({"status": r2.status_code, "body": j2}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("LIVE zh2ko", r2.status_code, json.dumps(j2, ensure_ascii=False)[:800])
    tr2 = " ".join(b.get("tr", "") for b in (j2.get("blocks") or []))
    check("[live] 中文便条→韩文（고수/땅콩）", r2.status_code == 200 and "고수" in tr2 and "땅콩" in tr2, r2.text[:300])
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser, ctx = _ctx(p)
        page = ctx.new_page(); errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        _ui_flow(page, base, img, check, shot_dir, "live", live=True)
        check("[live] 页面无 JS 错误", not errs, errs[:3])
        browser.close()

if __name__ == "__main__":
    ok = [0, 0]
    def check(name, cond, info=""):
        ok[0 if cond else 1] += 1
        print("PASS" if cond else "FAIL", name, "" if cond else str(info)[:400])
    base = (sys.argv[1] if len(sys.argv) > 1 else "https://lvhanzhushou.com").rstrip("/")
    run_live(base, check, Path("/workspace/lvhan-photo-tr"))
    print("%d passed, %d failed" % tuple(ok))
    sys.exit(1 if ok[1] else 0)
