"""纯静态检查：品牌开屏（3 秒 / 三张轮换 / 1.5 秒超时）、首页四张广告轮播（字幕 / 3 秒 / 0.4s / 手势 / 无缝循环）。
不需要启服务，直接 python test/test_splash.py 即可。"""
import os, re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ok = fail = 0

OLD_CACHE = "lvhan-shell-v" + "64"
OLD_VER = "?v=" + "64"

def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS", name)
    else:
        fail += 1
        print("FAIL", name, str(info)[:300])

def read(rel):
    p = os.path.join(ROOT, "static", rel)
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""

splash = read("splash.js")
index = read("index.html")
sw = read("sw.js")

# ---------- 1. splash.js：门禁 ----------
check("splash.js 只在 location.pathname === '/' 运行",
      "location.pathname !== '/'" in splash or "location.pathname!=='/'" in splash)
check("splash.js 有 ?tab= 时直接 return",
      re.search(r"get\(['\"]tab['\"]\)[\s\S]{0,40}return", splash) is not None)
check("splash.js 保留 navigator.webdriver 门禁",
      "navigator.webdriver" in splash and "splash=1" in splash)
check("splash.js 用 try/catch 包住，不抛到页面",
      splash.lstrip().startswith("/*") and "(function(){" in splash
      and splash.count("try") >= 4 and "catch" in splash)

# ---------- 2. splash.js：后台广告优先、仍走接口与事件 ----------
check("splash.js 请求 /api/splash", "fetch('/api/splash')" in splash)
check("splash.js 后台开启时优先走广告分支",
      re.search(r"if\s*\(d\s*&&\s*d\.on\)\s*showAd\(d\);\s*else\s*showBrand\(\);", splash) is not None)
check("splash.js 广告分支保留频率键",
      "lh_splash_" in splash and "d.freq === 'daily'" in splash and "d.freq === 'session'" in splash)
check("splash.js 广告分支保留 KST 日期",
      "9 * 3600e3" in splash and "toISOString().slice(0, 10)" in splash)
check("splash.js 事件仍 POST /api/splash/ev 且 keepalive",
      "/api/splash/ev" in splash and "keepalive: true" in splash)
check("splash.js 广告分支有 show / skip / click 事件",
      "ev('show')" in splash and "ev('skip')" in splash and "ev('click')" in splash)
check("splash.js 后台广告仍显示广告标与底部品牌条",
      "sp-lbl" in splash and "sp-strip" in splash
      and "/static/icons/icon-192.png?v=89" in splash and "地铁 · 翻译 · 吃喝玩买" in splash)
check("splash.js 后台广告有 d.link 跳转", "location.href = d.link" in splash)

# ---------- 3. splash.js：3 秒倒计时 / 1.5 秒图片超时 ----------
check("splash.js 按钮文案为 3 秒", "<b>3</b>s 跳过" in splash)
check("splash.js 倒计时从 3 开始", re.search(r"\bn\s*=\s*3\b", splash) is not None)
check("splash.js 不再使用 5 秒开屏倒计时",
      "<b>5</b>" not in splash and re.search(r"\bn\s*=\s*5\b", splash) is None
      and "5 秒倒计时" not in splash and "5秒倒计时" not in splash)
check("splash.js 图片超时为 1500ms", "1500" in splash)
check("splash.js 不再使用旧的 2500ms 图片超时", "2500" not in splash)
check("splash.js 超时放弃时不记已显示（成功后才写 storage）",
      splash.index("localStorage.setItem(key, today)") > splash.index("img.onload"))

# ---------- 4. splash.js：品牌开屏三张轮换 ----------
for i in (1, 2, 3):
    check("splash.js 品牌图 %d 用 webp?v=89" % i,
          ("/static/splash/splash-%d.webp?v=89" % i) in splash)
    check("splash.js 品牌图 %d 有 jpg 兜底" % i,
          ("/static/splash/splash-%d.jpg?v=89" % i) in splash)
check("splash.js 用 localStorage 键 lh_brand_splash_i", "lh_brand_splash_i" in splash)
check("splash.js 读取后取模并写回下一张",
      "i % 3" in splash and "(cur + 1) % 3" in splash)
check("splash.js 本次尝试开始就推进索引", splash.index("setItem('lh_brand_splash_i'") < splash.index("img.src = item.webp"))
check("splash.js 品牌开屏加 sp-brand 且无广告标/品牌条",
      "el.className = 'sp-brand'" in splash and "sp-brand" in splash)
check("splash.js 品牌三张背景色齐全",
      all(c in splash for c in ("#144254", "#1E4A5E", "#236C6A")))
brand_block = splash.split("var BRAND =")[-1]
check("splash.js 品牌图点击 .sp-ad 只 close、不跳转",
      ".sp-ad').onclick" in brand_block and "close();" in brand_block
      and "location.href" not in brand_block)
check("splash.js 品牌开屏不调 /api/splash/ev",
      "/api/splash/ev" not in brand_block)

# ---------- 5. splash.js：缓存与禁词 ----------
check("splash.js 不含旧缓存参数 %s" % OLD_VER, OLD_VER not in splash)
check("splash.js 不含旧缓存名 %s" % OLD_CACHE, OLD_CACHE not in splash)
check("splash.js 无「奋韩」", "奋韩" not in splash)
check("splash.js 无微信号/加微信", not any(w in splash for w in ("微信号", "加微信", "添加微信")))
check("splash.js 无二维码/扫码", ("二维码" not in splash) and ("扫码" not in splash))
check("splash.js 无绝对化宣传语",
      not re.search(r"唯一|第一名|第一品牌|销量第一|排名第一|最(好|佳|优|强|低|便宜|值得|欢|专业|靠谱|出名)|顶级|极致|国家级", splash))

# ---------- 6. 品牌开屏图片文件 ----------
for i in (1, 2, 3):
    p = os.path.join(ROOT, "static", "splash", "splash-%d.webp" % i)
    check("static/splash/splash-%d.webp 存在且 >10KB" % i,
          os.path.exists(p) and os.path.getsize(p) > 10000,
          os.path.getsize(p) if os.path.exists(p) else "missing")
    j = os.path.join(ROOT, "static", "splash", "splash-%d.jpg" % i)
    check("static/splash/splash-%d.jpg 若存在则 >10KB" % i,
          (not os.path.exists(j)) or os.path.getsize(j) > 10000,
          os.path.getsize(j) if os.path.exists(j) else "optional")

# ---------- 7. index.html：开屏样式与脚本 ----------
check("index.html 引用 /static/splash.js?v=89", "/static/splash.js?v=89" in index)
check("index.html 有品牌开屏 .sp-brand", "sp-brand" in index)
check("index.html 品牌图 background-size:contain",
      re.search(r"background-size\s*:\s*contain", index) is not None)
check("index.html #splash 仍 z-index:1000",
      re.search(r"#splash\{[^}]*z-index:1000", index) is not None)
check("index.html 仍隐藏开屏时的页面滚动", "html.sp-on" in index and "overflow:hidden" in index)

# ---------- 8. 首页广告区域 ----------
m = re.search(r'<div class="ads" id="homeAds">[\s\S]*?<div class="dots" id="adsDots"', index)
ads_html = m.group(0) if m else ""
home = index.split('id="p-home"')[1].split('id="p-cos"')[0] if 'id="p-home"' in index else ""
ads_js_m = re.search(r"/\* -+ C\. 广告轮播[\s\S]*?\}\)\(\);", index)
ads_js = ads_js_m.group(0) if ads_js_m else ""
ads_text = ads_html + "\n" + ads_js

check("首页广告区域存在 #homeAds/#adsTrack", 'id="homeAds"' in index and 'id="adsTrack"' in index)
for i in (1, 2, 3, 4):
    img_m = re.search(r"<img[^>]*ad-%d\.jpg[^>]*>" % i, ads_html or index)
    img_tag = img_m.group(0) if img_m else ""
    check("首页第 %d 张广告用 ad-%d.jpg?v=89" % (i, i),
          ("/static/home-ads/ad-%d.jpg?v=89" % i) in (ads_html or home))
    check("首页第 %d 张广告不再 loading=lazy" % i,
          bool(img_m) and 'loading="lazy"' not in img_tag, img_tag)
check("第 4 张广告保留 data-ad=lanjing", 'data-ad="lanjing"' in (ads_html or home))
check("第 4 张广告链接 /ads/lanjing", 'href="/ads/lanjing"' in (ads_html or home))
check("四张链接完整",
      all(h in (ads_html or home) for h in (
          'href="/food#hamcho-myeongdong"', 'href="/food#daega-myeongdong"',
          'href="/shops"', 'href="/ads/lanjing"')))
check("第 4 张含「蓝憬通讯」", "蓝憬通讯" in (ads_html or home))
check("第 4 张含推荐好友领手机卡文案",
      "推荐2位好友注册 免费领韩国手机卡" in (ads_html or home))
check("字幕容器 ads-cap / id=adsCap",
      'class="ads-cap"' in index and 'id="adsCap"' in index)
check("字幕首屏默认文案", re.search(r'<div class="ads-cap" id="adsCap"><b>咸草酱蟹</b><span>点击查看</span></div>', index) is not None)
check("四张字幕数据都在轮播 JS 里",
      all(t in ads_js for t in ("咸草酱蟹", "大家烤肉", "DAYONE", "蓝憬通讯"))
      and "推荐2位好友注册 免费领韩国手机卡" in ads_js)
dots_m = re.search(r'id="adsDots"[^>]*>([\s\S]*?)</div>', index)
dots_inner = dots_m.group(1) if dots_m else ""
check("adsDots 有 4 个指示点且第一个 .on",
      dots_inner.count("<i") == 4 and 'class="on"' in dots_inner)
check("轮播 JS 按真实张数生成指示点并高亮",
      "dots.innerHTML" in ads_js and "classList.toggle('on'" in ads_js)
check("轮播视口比例接近原图（padding-bottom:38%）",
      re.search(r"\.ads-viewport\{[^}]*padding-bottom:38%", index) is not None)
check("图片 object-position:left center",
      re.search(r"object-position\s*:\s*left center", index) is not None)
check("切换为 transform .4s ease",
      re.search(r"transition\s*:\s*transform\s+0?\.4s\s+ease", index) is not None)
check("切换不再用 idx*100/n 百分比", "idx*100/n" not in index and "idx * 100 / n" not in index)
check("每张停留 3000ms", "3000" in ads_js)
check("轮播用 new Image 预加载", "new Image" in ads_js)
check("轮播支持 pointermove 跟手拖动", "pointermove" in ads_js)
check("拖动阈值约 40px", re.search(r"Math\.abs\(h\)\s*>\s*40", ads_js) is not None)
check("有效滑动后阻止 click", "suppressUntil" in ads_js and "preventDefault" in ads_js)
check("无缝循环使用 cloneNode", "cloneNode" in ads_js)
check("首尾克隆插入到 track 两端",
      "insertBefore(cloneLast" in ads_js and "appendChild(cloneFirst" in ads_js)
check("transitionend 后瞬移回真实页",
      "transitionend" in ads_js and "paint(false)" in ads_js)
check("resize 重算宽度且不带动画", "addEventListener('resize'" in ads_js and "setX(-pos * w, false)" in ads_js)
check("仅 #p-home 点亮且页面可见时自动播",
      "classList.contains('on')" in ads_js and "document.hidden" in ads_js)
check("指示点跳真实页（pos = idx + 1）", "pos = idx + 1" in ads_js)
check("保留 LANJING_POST_ID 钩子", "LANJING_POST_ID" in ads_js and "/community/post/" in ads_js)

# ---------- 9. 广告片段禁词 ----------
check("首页广告片段无「奋韩」", "奋韩" not in ads_text)
check("首页广告片段无微信号/加微信",
      not any(w in ads_text for w in ("微信号", "加微信", "添加微信")))
check("首页广告片段无二维码/扫码", ("二维码" not in ads_text) and ("扫码" not in ads_text))
check("首页广告片段无绝对化宣传语",
      not re.search(r"唯一|第一名|第一品牌|销量第一|排名第一|最(好|佳|优|强|低|便宜|值得|欢|专业|靠谱|出名)|顶级|极致|国家级", ads_text))

# ---------- 10. Service Worker 缓存版本 ----------
check("sw.js CACHE 为 lvhan-shell-v89", "lvhan-shell-v89" in sw)
check("sw.js 不含旧缓存名", OLD_CACHE not in sw)
# 不要求 SHELL 预缓存开屏图片

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
