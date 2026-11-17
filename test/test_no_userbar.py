"""纯静态检查：各页顶部已登录个人卡片（LH.userbar 渲染的 #ub）已整块移除，
且「我的」页仍保留头像、旅行豆、通知、咨询、私信、退出和未登录入口。

不需要启服务，直接 python test/test_no_userbar.py 即可。
测试读取仓库根目录下的 static/ 文件；拷到仓库 test/ 目录后运行。
"""
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ok = fail = 0

PAGES = [
    "community.html", "shops.html", "community-post.html", "member.html",
    "messages.html", "friends.html", "friend-edit.html", "places.html",
    "places-one.html", "places-new.html", "chicken.html", "chicken-join.html",
    "points.html", "coupons.html", "promo.html", "buddy-verify.html",
]


def check(name, cond, info=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS", name)
    else:
        fail += 1
        print("FAIL", name, str(info)[:300])


def read(rel):
    with open(os.path.join(ROOT, "static", rel), encoding="utf-8") as f:
        return f.read()


def js_function(src, header):
    """按花括号配对截出函数体；找不到返回空串。"""
    i = src.find(header)
    if i < 0:
        return ""
    j = src.find("{", i)
    if j < 0:
        return ""
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
    return src[j:]


# ---------- 1. 16 个页面都不再有个人卡片容器 ----------
for name in PAGES:
    html = read(name)
    check("%s 不含 class=\"card userbar\"" % name,
          'class="card userbar"' not in html and "class='card userbar'" not in html)
    check("%s 不含元素 id=\"ub\"" % name,
          re.search(r'\bid\s*=\s*["\']ub["\']', html) is None)
    check("%s 不再通过 getElementById('ub') / $('ub') 取节点" % name,
          "getElementById('ub')" not in html and 'getElementById("ub")' not in html and
          "$('ub')" not in html and '$("ub")' not in html)
    # LH.userbar 如果仍被调用，只能是空参数，不能把已删除的节点传进去
    calls = re.findall(r"LH\.userbar\s*\(([^)]*)\)", html)
    check("%s 的 LH.userbar 调用不传 #ub 节点" % name,
          all(not a.strip() for a in calls), calls)

# ---------- 2. community.js 的 userbar 不再画卡片，且空值安全 ----------
js = read("community.js")
body = js_function(js, "async function userbar(el)")
check("community.js 仍有 userbar 函数", bool(body))
check("community.js 继续导出 LH.userbar",
      re.search(r"userbar\s*:\s*userbar", js) is not None)
check("userbar 仍调用 me()", "await me()" in body)
check("userbar 成功时仍返回 d", re.search(r"return\s+d\s*;", body) is not None)
check("userbar 失败时返回 null（不抛错）",
      re.search(r"catch\s*\([^)]*\)\s*\{[^}]*return\s+null", body, re.S) is not None)
check("userbar 对已登录用户仍启动咨询轮询", "startConsultPoll()" in body)
check("userbar 不再拼接设置头像 / 退出等卡片 HTML",
      "设置头像" not in body and "lhAv" not in body and "lhLogout" not in body and
      "innerHTML =" not in body and not re.search(r"<(?:button|a)\b", body))
check("userbar 不再查卡片内部节点", "el.querySelector" not in body)
if "el.innerHTML" in body:
    head = body[:body.find("el.innerHTML")]
    check("userbar 访问 el.innerHTML 前做了空值判断", "el &&" in head[-80:], head[-80:])
else:
    check("userbar 未直接写 el.innerHTML", True)
check("userbar 对 el 做了空值保护或完全不依赖 el",
      "el &&" in body or "el." not in body)

# ---------- 3. 「我的」页入口完整 ----------
me = read("me.html")
for path, label in [
    ("/points", "旅行豆统计"),
    ("/community/notices", "通知"),
    ("/consult", "咨询"),
    ("/community/messages", "私信"),
    ("/api/community/logout", "退出登录"),
    ("/login", "登录 / 注册"),
]:
    check("me.html 保留%s入口 %s" % (label, path), path in me)
for el_id, label in [
    ("bellDot", "通知未读红点"),
    ("consultDot", "咨询未读红点"),
    ("msgDot", "私信未读红点"),
    ("avBtn", "设置头像"),
    ("sOut", "退出登录"),
    ("guest", "游客注册 / 登录区"),
]:
    check("me.html 保留 %s（%s）" % (el_id, label), ('id="%s"' % el_id) in me)
check("me.html 头像入口调用 LH.pickAvatar",
      "LH.pickAvatar" in me and 'id="avBtn"' in me)
check("me.html 未登录仍有免费注册 / 登录文案",
      "免费注册 / 登录" in me and "/login" in me)

# ---------- 4. 韩友圈顶栏已精简：通知 / 私信入口移到「我的」页 ----------
comm = read("community.html")
check("community.html 不再有通知铃铛 #notiIc",
      'id="notiIc"' not in comm and "notiIc" not in comm)
check("community.html 不再有私信入口 #inboxIc",
      'id="inboxIc"' not in comm and "inboxIc" not in comm)
check("community.html 不再给通知 / 私信节点插未读角标",
      "notif_unread" not in comm and "user.unread" not in comm)
check("community.html 仍保留全部频道入口 /community/boards",
      "/community/boards" in comm)

# ---------- 5. 不误删解除拉黑 ----------
msgs = read("messages.html")
check("messages.html 保留解除拉黑的 data-ub",
      "data-ub=" in msgs and "btn.dataset.ub" in msgs)

# ---------- 6. 不新增夸张宣传文案（只查明显短语，避开“最多/最后/第一篇”等既有用法） ----------
BAN = re.compile(r"最好的|最佳|最强|最便宜|最低价|第一名|全网第一|全国第一|唯一指定|唯一官方|独家")
for name in PAGES + ["me.html"]:
    html = read(name)
    m = BAN.search(html)
    check("%s 无夸张宣传禁词短语" % name, m is None, m.group(0) if m else "")

print("%s passed, %s failed" % (ok, fail))
if fail:
    raise SystemExit(1)
