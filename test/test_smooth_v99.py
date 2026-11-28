"""页面切换流畅度 v99 静态检查 + 发送防重单元测试：python test/test_smooth_v99.py"""
import os, sys, re
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
ok = fail = 0
def check(n, c, info=""):
    global ok, fail
    if c: ok += 1; print("PASS", n)
    else: fail += 1; print("FAIL", n, str(info)[:300])
R = lambda f: open(os.path.join(ROOT, f), encoding="utf-8").read()
sw, pwa, splash, op = R("static/sw.js"), R("static/pwa.js"), R("static/splash.js"), R("static/opening-promos.js")
dz, post, chat, cons, cjs = R("static/dazi.html"), R("static/community-post.html"), R("static/chat.html"), R("static/consult.html"), R("static/community.js")
check("SW：去过的页面先秒开，后台再更新", "staleWhileRevalidate" in sw and "event.waitUntil(fresh)" in sw)
check("SW：详情/私信/咨询/搭子用模板秒开", all(x in sw for x in ["/static/community-post.html", "/static/chat.html", "/static/consult.html", "/static/dazi.html"]))
check("SW：登录/支付/后台/跳转不走缓存", "NO_SWR" in sw and "login" in sw and "pay" in sw and "admin" in sw)
check("SW：缓存太旧（7 天）不用", "MAX_STALE_MS" in sw)
check("SW：缓存版本 v99", "lvhan-shell-v100" in sw)
check("返回键真正后退（standalone 也能恢复列表）", "a.pback" in pwa and "history.back()" in pwa and "lh_back" in pwa)
check("按下链接即预取，且不计入浏览量", "touchstart" in pwa and "X-LH-Prefetch" in pwa and "x-lh-prefetch" in R("visits.py"))
check("主屏幕 App 回到前台检查新版本", "r.update()" in pwa)
check("开屏只在打开时出现一次（站内回首页/后退不再挡）", "lh_splash_seen" in splash and "back_forward" in splash and "referrer" in splash)
check("开业弹层弹出即记今天已看", op.count("localStorage.setItem(key, kstDay())") >= 2)
check("搭子列表：返回恢复列表与滚动位置", "lh_dazi_list_v1" in dz and "restoreList()" in dz and "scrollRestoration" in dz)
check("搭子列表：列表/登录/选项并行请求", "var listP = restored ? Promise.resolve() : load(true);" in dz)
check("骨架屏代替「加载中…」", all("加载中…</div></div>" not in x for x in [post, chat, cons]) and "skel" in cjs)
check("详情：先画列表带来的标题", "lh_peek" in post and "lh_peek" in cjs)
check("详情：登录状态与帖子并行请求", "var postP = LH.api('GET', '/api/community/posts/' + pid);" in post)
check("评论：发表中禁用 + cid + 只刷新评论区", "发表中…" in post and "cid: cid" in post and "commentsOnly: true" in post)
check("私信：乐观上屏、清空、禁用、cid、保持焦点", all(x in chat for x in ["rowm me pending", "ta.value = ''", "btn.disabled = true", "cid: cid", "LH.keepFocus"]))
check("咨询：不再二次整页跳转", "history.replaceState(null, '', '/consult/' + r.thread_id)" in cons and "location.replace('/consult/' + r.thread_id);\n  }" not in cons)
check("咨询：发送防重 + 拉取不重复画", "fd.append('cid', cid)" in cons and "if (polling) return polling;" in cons)
check("静态资源长缓存", "max-age=31536000, immutable" in R("app.py"))
check("触屏无点击延迟", "touch-action:manipulation" in R("static/pwa.css"))
# 后端防重
import msg_dedup as M
check("cid 校验", M.norm_cid("abc123XYZ") == "abc123XYZ" and M.norm_cid("x") == "" and M.norm_cid("a b c d e f") == "")
M.cid_put("chat", 5, "abcdef12", {"ok": True, "id": 9, "dup": True})
check("同一 cid 返回第一次结果", M.cid_get("chat", 5, "abcdef12") == {"ok": True, "id": 9, "dup": True})
check("不同用户/类型互不影响", M.cid_get("chat", 6, "abcdef12") is None and M.cid_get("consult", 5, "abcdef12") is None)
for f, kind in [("chat.py", "chat"), ("merch.py", "consult"), ("community.py", "comment")]:
    s = R(f)
    check(f"{f} 发送接口按 cid 防重、无 cid 时短时间同内容去重", f'cid_get("{kind}"' in s and f'cid_put("{kind}"' in s and "interval '" in s)
print("%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
