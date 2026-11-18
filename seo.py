"""旅韩助手 · 搜索引擎优化：把每页独特的中文标题、描述、canonical、
Open Graph/Twitter、hreflang、JSON-LD 和站长验证标签写入 HTML；
并提供 /robots.txt、/sitemap.xml 与 IndexNow 密钥文件。
验证码只在对应环境变量有值时输出，不把密钥写进仓库。"""
import datetime, html, os, re

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
INDEXNOW_FILE = os.path.join(BASE, "indexnow.key")
OG_PATH = "/static/og-cover.jpg"
DEFAULT_SITE = "https://lvhanzhushou.com"

# 路径 → (标题, 描述, 关键词, 是否索引)
PAGES = {
    "/": (
        "旅韩助手｜韩国旅游攻略与首尔自由行",
        "旅韩助手免费提供韩国旅游攻略、首尔自由行路线、韩国景点指南、韩妆识别、地铁换乘和韩友圈交流，方便中国游客赴韩出行。",
        "旅韩助手,韩国旅游攻略,首尔自由行,韩国景点,韩国交友",
        True,
    ),
    "/attractions": (
        "韩国景点介绍｜首尔名胜与热门景点 · 旅韩助手",
        "韩国景点介绍：宫殿、寺庙、市场、自然风光、热门收费景点等的中文介绍和交通提示，适合首尔自由行和韩国旅游攻略参考。",
        "韩国景点,首尔景点,韩国旅游攻略,旅韩助手",
        True,
    ),
    "/food": (
        "韩国美食指南｜首尔吃什么 · 旅韩助手",
        "韩国美食与首尔吃喝指南：热门商圈、经典小吃和实用点餐提示，给首尔自由行做参考，信息来自公开资料与实地经验。",
        "韩国美食,首尔美食,韩国旅游攻略,旅韩助手",
        True,
    ),
    "/shopping": (
        "韩国购物指南｜明洞药妆与退税须知 · 旅韩助手",
        "韩国购物与明洞药妆实用提示：商圈路线、退税和沟通要点，帮助中国游客在韩国更顺利地购物。",
        "韩国购物,明洞,韩国药妆,韩国旅游攻略,旅韩助手",
        True,
    ),
    "/tips": (
        "韩国旅游攻略｜签证交通安全与礼仪 · 旅韩助手",
        "中国游客赴韩签证、交通、安全、礼仪与防骗提醒，资料来自官方网站，适合制定首尔自由行和韩国旅游攻略。",
        "韩国旅游攻略,赴韩签证,首尔自由行,旅韩助手",
        True,
    ),
    "/beauty": (
        "韩国皮肤管理与医美须知 · 旅韩助手",
        "赴韩皮肤管理、医美相关的中立须知与注意事项，帮助你自行了解与核实，不推荐具体机构，不承诺效果。",
        "韩国皮肤管理,韩国医美,韩国旅游攻略,旅韩助手",
        True,
    ),
    "/faq": (
        "赴韩常见问题｜签证与出行解答 · 旅韩助手",
        "赴韩签证、交通、手机上网和出行常见问题解答，信息仅供参考，请以官方说明为准。",
        "赴韩签证,韩国旅游攻略,旅韩助手",
        True,
    ),
    "/community": (
        "韩友圈｜韩国旅游经验与韩国交友 · 旅韩助手",
        "旅韩助手韩友圈：去韩国前问问走过的人。踩坑避雷、结伴同行、签证问答、留学打听、租房找房和韩国交友。",
        "韩国交友,韩国旅游攻略,韩友圈,旅韩助手",
        True,
    ),
    "/community/boards": (
        "韩友圈频道｜踩坑避雷签证留学租房 · 旅韩助手",
        "按主题浏览韩友圈频道：踩坑避雷、结伴同行、签证问答、留学打听、租房找房、韩国交友和出行提醒。",
        "韩国旅游攻略,韩国交友,韩友圈,旅韩助手",
        True,
    ),
    "/community/friends": (
        "韩国交友｜旅韩交友与找搭子 · 旅韩助手",
        "韩友圈·旅韩交友：在韩国认识新朋友、找旅行搭子、语言交换。请注意防骗，不要转账或透露证件。",
        "韩国交友,旅韩交友,旅韩助手",
        True,
    ),
    "/community/rate": (
        "人民币韩元汇率换算 · 旅韩助手",
        "人民币与韩元参考汇率换算，方便制定韩国旅游攻略和出行预算。汇率仅供参考，请以实际成交为准。",
        "韩元汇率,韩国旅游攻略,旅韩助手",
        True,
    ),
    "/member": (
        "韩友圈会员说明 · 旅韩助手",
        "了解旅韩助手韩友圈会员、高级会员和商家会员的权限与开通方式，用于发帖、韩国交友和查阅资料。",
        "韩友圈,韩国交友,旅韩助手",
        True,
    ),
    "/rules": (
        "韩友圈约定 · 旅韩助手",
        "旅韩助手韩友圈约定：真实分享、防骗、商家信息规范，以及签证留学内容仅供参考、本站不代办。",
        "韩友圈,旅韩助手,韩国交友",
        True,
    ),
    "/points": (
        "旅行豆 · 旅韩助手",
        "旅韩助手旅行豆：每日签到、分享内容和常用功能可累积点数，兑换权益即将上线。",
        "旅行豆,旅韩助手",
        False,
    ),
    "/coupons": (
        "卡券 · 旅韩助手",
        "查看旅韩助手卡券。新人券仅作展示，人工开通时由管理员核对抵扣。",
        "卡券,旅韩助手",
        False,
    ),
    "/shops": (
        "商家 · 旅韩助手",
        "旅韩助手商家页：精选商家推荐，以及经过审核的商家发布。可按租车、餐饮美食、地陪向导、医美、物流快递、代购、通讯电话卡等分类查看，在站内咨询联系商家。",
        "商家,精选商家,商家发布,韩国商家,首尔美食,旅韩助手",
        True,
    ),
    "/pay": (
        "韩国支付与交通卡 · 旅韩助手",
        "赴韩支付与交通卡说明：T-money、WOWPASS、外卡以及支付宝和微信在商户与地铁闸机的区别。闸机一般不能直接刷支付宝、微信或银联。",
        "T-money,韩国交通卡,韩国支付,旅韩助手",
        True,
    ),
    "/tax-refund": (
        "韩国退税须知 · 旅韩助手",
        "韩国购物退税：即时退税与机场退税的区别，以及 VisitKorea 公开的金额门槛。查阅日期 2026-11-15，以官方为准。",
        "韩国退税,即时退税,旅韩助手",
        True,
    ),
    "/visa": (
        "赴韩签证须知 · 旅韩助手",
        "中国游客赴韩签证与团体阶段性免签说明。团体免签已延长至 2026-12-31。本站不代办签证，以韩国驻华使领馆和法务部官方为准。",
        "赴韩签证,韩国免签,旅韩助手",
        True,
    ),
    "/itineraries": (
        "首尔路线卡片 · 旅韩助手",
        "首尔 3 日路线：首次经典、购物美妆、追星 K-POP 和美食，含地铁线路与站号，不推荐付费店铺。",
        "首尔自由行,首尔路线,旅韩助手",
        True,
    ),
    "/trips": (
        "行程 · 旅韩助手",
        "行程功能即将上线：我的行程、大家分享，以及根据以前游客常走路线整理的经典行程，可一键复制成自己的行程。",
        "韩国行程,首尔行程,旅韩助手",
        True,
    ),
    "/local": (
        "生活在韩国 · 旅韩助手",
        "生活在韩国：从韩友圈看生活分享，也可进入租房、招聘、二手、签证和留学等频道，内容由用户发布，仅供参考。",
        "韩国租房,韩国招聘,在韩生活,旅韩助手",
        True,
    ),
    "/chicken": (
        "炸鸡代点 · 旅韩助手",
        "旅韩助手炸鸡代点：填写酒店地址后由客服确认再付款。个人代点协助，非持牌外卖。",
        "炸鸡代点,旅韩助手",
        False,
    ),
    "/community/notices": (
        "通知 · 旅韩助手",
        "韩友圈通知：评论、点赞、关注、加精和徽章提醒。",
        "韩友圈通知,旅韩助手",
        False,
    ),
    "/community/u": (
        "个人主页 · 旅韩助手",
        "韩友圈个人主页：徽章与帖子。",
        "韩友圈,旅韩助手",
        False,
    ),
    "/consult": (
        "站内咨询 · 旅韩助手",
        "旅韩助手站内咨询：和商家一对一沟通，所有咨询在站内完成，保护双方权益。",
        "站内咨询,旅韩助手",
        False,
    ),
    "/partner": (
        "商家/服务商入驻 · 旅韩助手",
        "门店代订、接送机、中文应急协助与短时口译服务商入驻申请（中韩双语），审核通过后展示。",
        "商家入驻,服务商入驻,입점 신청,旅韩助手",
        True,
    ),
    "/deals": (
        "购物优惠合集｜免税店、药妆、退税与韩国购物季 · 旅韩助手",
        "汇总官方公开的韩国购物优惠：Korea Grand Sale、免税店会员活动、退税要点与大促提醒，每条注明来源。",
        "韩国购物优惠,免税店优惠,Korea Grand Sale,韩国退税,旅韩助手",
        True,
    ),
    "/me": ("我的 · 旅韩助手", "我的旅行豆、卡券、帖子与常用工具。", "旅韩助手", False),
    "/invite": ("邀请有奖 · 旅韩助手", "邀请同行一起用旅韩助手，好友完成首次使用后双方都可获得站内抵用券。", "旅韩助手", False),
    "/services": (
        "服务商家 · 旅韩助手",
        "经平台审核的门店代订、接送机、中文应急协助服务商，仅供参考。",
        "门店代订,接送机,中文协助,旅韩助手",
        True,
    ),
    "/chicken/join": (
        "炸鸡商家入驻 · 旅韩助手",
        "炸鸡店商家入驻旅韩助手代点：提交店铺资料，保证金到账且审核通过后上架，可随时切换营业或休息。",
        "炸鸡商家入驻,旅韩助手",
        False,
    ),
    "/login": (
        "登录或注册 · 旅韩助手",
        "登录或注册旅韩助手韩友圈，参与韩国旅游经验交流和韩国交友。",
        "旅韩助手,韩友圈",
        False,
    ),
    "/community/new": (
        "发帖 · 韩友圈 · 旅韩助手",
        "在旅韩助手韩友圈发布图文帖子，分享韩国旅游经验。",
        "韩友圈,旅韩助手",
        False,
    ),
    "/community/friends/edit": (
        "编辑交友资料 · 旅韩助手",
        "编辑你在韩友圈的旅韩交友资料。",
        "韩国交友,旅韩助手",
        False,
    ),
    "/community/friends/verify": (
        "旅游搭子实名核验 · 旅韩助手",
        "旅游搭子实名核验：只收集证件号码后四位和可供遮挡的证件照片。",
        "实名核验,旅韩助手",
        False,
    ),
    "/promo": (
        "推广中心 · 旅韩助手",
        "查看推广码和推荐佣金。",
        "推广,旅韩助手",
        False,
    ),
    "/promo/rules": (
        "推广约定 · 旅韩助手",
        "旅韩助手推广员约定：单层推荐、按实付金额 50% 计佣，禁止骚扰、虚假承诺和医疗保证。",
        "推广约定,旅韩助手",
        True,
    ),
    "/privacy": (
        "隐私政策 · 旅韩助手",
        "旅韩助手隐私政策：收集哪些个人信息、用途、保存期限、第三方处理、跨境传输，以及如何查询、更正和删除你的信息。",
        "隐私政策,旅韩助手",
        True,
    ),
    "/terms": (
        "用户协议与免责声明 · 旅韩助手",
        "旅韩助手用户协议：服务内容、账号规则、会员付费、内容责任、举报删除和免责声明。本站信息仅供参考，不是旅行社，不代办签证。",
        "用户协议,免责声明,旅韩助手",
        True,
    ),
    "/places": (
        "吃喝玩｜会员推荐地点 · 旅韩助手",
        "旅韩助手吃喝玩：会员推荐的韩国美食、咖啡、酒店住宿、景点和购物地点，审核通过后公开，可评分并查看地图。",
        "韩国美食,韩国景点,韩国咖啡,旅韩助手,吃喝玩",
        True,
    ),
    "/places/new": (
        "推荐一个地点 · 旅韩助手",
        "向旅韩助手吃喝玩推荐你去过的韩国店铺、咖啡馆、酒店或景点。",
        "韩国美食,韩国景点,旅韩助手",
        False,
    ),
    "/admin": (
        "管理后台 · 旅韩助手",
        "旅韩助手管理后台",
        "旅韩助手",
        False,
    ),
}

FILE_PATHS = {
    "index.html": "/",
    "attractions.html": "/attractions",
    "food.html": "/food",
    "shopping.html": "/shopping",
    "tips.html": "/tips",
    "beauty.html": "/beauty",
    "faq.html": "/faq",
    "community.html": "/community",
    "community-boards.html": "/community/boards",
    "community-new.html": "/community/new",
    "community-post.html": "/community/post",
    "login.html": "/login",
    "member.html": "/member",
    "points.html": "/points",
    "coupons.html": "/coupons",
    "shops.html": "/shops",
    "pay.html": "/pay",
    "tax-refund.html": "/tax-refund",
    "visa.html": "/visa",
    "itineraries.html": "/itineraries",
    "trips.html": "/trips",
    "local.html": "/local",
    "chicken.html": "/chicken",
    "chicken-join.html": "/chicken/join",
    "partner.html": "/partner",
    "me.html": "/me",
    "deals.html": "/deals",
    "invite.html": "/invite",
    "services.html": "/services",
    "consult.html": "/consult",
    "notices.html": "/community/notices",
    "user.html": "/community/u",
    "rules.html": "/rules",
    "friends.html": "/community/friends",
    "friend-edit.html": "/community/friends/edit",
    "buddy-verify.html": "/community/friends/verify",
    "friend.html": "/community/friends/id",
    "messages.html": "/community/messages",
    "chat.html": "/community/messages",
    "places.html": "/places",
    "places-new.html": "/places/new",
    "places-one.html": "/places",
    "rate.html": "/community/rate",
    "admin.html": "/admin",
    "promo.html": "/promo",
    "promo-rules.html": "/promo/rules",
    "privacy.html": "/privacy",
    "terms.html": "/terms",
}

NOINDEX_PREFIXES = (
    "/admin",
    "/api/",
    "/community/messages",
    "/community/new",
    "/community/friends/edit",
    "/community/friends/verify",
    "/login",
    "/points",
    "/coupons",
    "/chicken",
    "/chicken/join",
    "/consult",
    "/community/notices",
    "/invite",
    "/community/u/",
)

SITEMAP_PATHS = [
    "/", "/attractions", "/food", "/shopping", "/tips", "/beauty", "/faq",
    "/community", "/community/boards", "/community/rate", "/community/friends",
    "/rules", "/member", "/shops", "/pay", "/tax-refund", "/visa", "/itineraries", "/privacy", "/terms", "/promo/rules",
    "/services", "/partner", "/deals", "/places", "/trips", "/local",
]

router = APIRouter()


def site_origin() -> str:
    return (os.environ.get("PUBLIC_BASE_URL") or os.environ.get("SITE_URL") or DEFAULT_SITE).rstrip("/")


def indexnow_key() -> str:
    if os.path.isfile(INDEXNOW_FILE):
        k = open(INDEXNOW_FILE, encoding="utf-8").read().strip()
        if re.fullmatch(r"[A-Za-z0-9\-]+", k) and 8 <= len(k) <= 128:
            return k
    raise RuntimeError("missing indexnow key")


def _esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def page_meta(url_path: str, extra=None):
    extra = extra or {}
    path = url_path.split("?")[0] or "/"
    if not path.startswith("/"):
        path = "/" + path
    base = PAGES.get(path)
    if not base:
        for pref, data in PAGES.items():
            if pref != "/" and path.startswith(pref + "/"):
                base = data
                break
    title = extra.get("title") or (base[0] if base else "旅韩助手｜韩国旅游攻略")
    desc = extra.get("description") or (base[1] if base else "旅韩助手：韩国旅游攻略、首尔自由行、韩国景点和韩友圈。")
    keys = extra.get("keywords") or (base[2] if base else "旅韩助手,韩国旅游攻略,首尔自由行,韩国景点,韩国交友")
    indexable = extra["index"] if "index" in extra else (base[3] if base else not any(path.startswith(p) for p in NOINDEX_PREFIXES))
    if any(path.startswith(p) for p in NOINDEX_PREFIXES):
        indexable = False
    return title, desc, keys, indexable, path


def verification_tags() -> str:
    tags = []
    g = (os.environ.get("GOOGLE_SITE_VERIFICATION") or "").strip()
    b = (os.environ.get("BAIDU_SITE_VERIFICATION") or "").strip()
    n = (os.environ.get("NAVER_SITE_VERIFICATION") or "").strip()
    if g:
        tags.append(f'<meta name="google-site-verification" content="{_esc(g)}">')
    if b:
        tags.append(f'<meta name="baidu-site-verification" content="{_esc(b)}">')
    if n:
        tags.append(f'<meta name="naver-site-verification" content="{_esc(n)}">')
    return "\n".join(tags)


def json_ld(origin, title, desc, path, extra=None):
    extra = extra or {}
    org = {
        "@type": "Organization",
        "@id": origin + "/#org",
        "name": "旅韩助手",
        "url": origin + "/",
        "logo": origin + "/static/icons/icon-512.png?v=72",
    }
    website = {
        "@type": "WebSite",
        "@id": origin + "/#website",
        "name": "旅韩助手",
        "alternateName": ["Korea Travel Assistant", "韩友圈"],
        "url": origin + "/",
        "inLanguage": "zh-CN",
        "description": "韩国旅游攻略、首尔自由行、韩国景点与韩友圈交流。",
        "publisher": {"@id": origin + "/#org"},
    }
    page = {
        "@type": extra.get("schema_type") or "WebPage",
        "@id": origin + path + "#webpage",
        "url": origin + path,
        "name": title,
        "description": desc,
        "inLanguage": "zh-CN",
        "isPartOf": {"@id": origin + "/#website"},
    }
    if extra.get("date"):
        page["datePublished"] = extra["date"]
    if extra.get("author"):
        page["author"] = {"@type": "Person", "name": extra["author"]}
    graph = [org, website, page]
    import json
    data = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False, separators=(",", ":"))
    # 用户写的标题/昵称会进 JSON-LD：转义 < > & 和行分隔符，防止 </script> 截断脚本造成 XSS
    data = (data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
                .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    return '<script type="application/ld+json">' + data + "</script>"


def head_extra(url_path: str, extra=None) -> str:
    title, desc, keys, indexable, path = page_meta(url_path, extra)
    origin = site_origin()
    canon = origin + (path if path != "/" else "/")
    if path == "/":
        canon = origin + "/"
    og_img = origin + OG_PATH
    robots = "index,follow" if indexable else "noindex,nofollow"
    parts = [
        f'<link rel="canonical" href="{_esc(canon)}">',
        f'<link rel="alternate" hreflang="zh-CN" href="{_esc(canon)}">',
        f'<link rel="alternate" hreflang="zh" href="{_esc(canon)}">',
        f'<link rel="alternate" hreflang="x-default" href="{_esc(canon)}">',
        f'<meta name="robots" content="{robots}">',
        f'<meta name="keywords" content="{_esc(keys)}">',
        f'<meta property="og:type" content="{"article" if extra and extra.get("schema_type") else "website"}">',
        f'<meta property="og:site_name" content="旅韩助手">',
        f'<meta property="og:locale" content="zh_CN">',
        f'<meta property="og:title" content="{_esc(title)}">',
        f'<meta property="og:description" content="{_esc(desc)}">',
        f'<meta property="og:url" content="{_esc(canon)}">',
        f'<meta property="og:image" content="{_esc(og_img)}">',
        f'<meta property="og:image:width" content="1200">',
        f'<meta property="og:image:height" content="630">',
        f'<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{_esc(title)}">',
        f'<meta name="twitter:description" content="{_esc(desc)}">',
        f'<meta name="twitter:image" content="{_esc(og_img)}">',
        json_ld(origin, title, desc, path if path != "/" else "/", extra),
    ]
    v = verification_tags()
    if v:
        parts.append(v)
    return "\n".join(parts)


def _ensure_lang(html_text: str) -> str:
    if re.search(r"<html[^>]*lang=", html_text, re.I):
        return re.sub(r"(<html\b[^>]*\blang=)['\"][^'\"]*['\"]", r'\1"zh-CN"', html_text, count=1, flags=re.I)
    return re.sub(r"<html\b", '<html lang="zh-CN"', html_text, count=1, flags=re.I)


def _replace_or_insert_meta(html_text, title, desc):
    if re.search(r"<title>[^<]*</title>", html_text, re.I):
        html_text = re.sub(r"<title>[^<]*</title>", lambda m: f"<title>{_esc(title)}</title>", html_text, count=1, flags=re.I)
    else:
        html_text = re.sub(r"<head[^>]*>", lambda m: m.group(0) + f"\n<title>{_esc(title)}</title>", html_text, count=1, flags=re.I)
    desc_tag = f'<meta name="description" content="{_esc(desc)}">'
    if re.search(r'<meta\s+name=["\']description["\']', html_text, re.I):
        html_text = re.sub(r'<meta\s+name=["\']description["\'][^>]*>', lambda m: desc_tag, html_text, count=1, flags=re.I)
    else:
        html_text = html_text.replace("</title>", "</title>\n" + desc_tag, 1)
    return html_text


def _inject_ssr(html_text, target, inner):
    if not target or not inner:
        return html_text
    # 替换 id 容器里原本的“加载中…”，方便爬虫直接读到正文
    pat = re.compile(rf'(<(?:div|section)[^>]*\bid=["\']{re.escape(target)}["\'][^>]*>)(.*?)(</(?:div|section)>)', re.I | re.S)
    m = pat.search(html_text)
    if not m:
        return html_text
    # 只替换最浅层：加载中占位通常没有嵌套复杂结构
    return html_text[:m.start()] + m.group(1) + inner + m.group(3) + html_text[m.end():]


def inject_page(html_text: str, url_path: str, extra=None, ssr_target=None, ssr_html=""):
    extra = extra or {}
    title, desc, keys, indexable, path = page_meta(url_path, extra)
    html_text = _ensure_lang(html_text)
    html_text = _replace_or_insert_meta(html_text, title, desc)
    # 去掉旧的 canonical / og / 重复 robots，避免叠加
    html_text = re.sub(r'\n?<link rel="canonical"[^>]*>', "", html_text, flags=re.I)
    html_text = re.sub(r'\n?<link rel="alternate" hreflang=[^>]*>', "", html_text, flags=re.I)
    html_text = re.sub(r'\n?<meta property="og:[^"]+"[^>]*>', "", html_text, flags=re.I)
    html_text = re.sub(r'\n?<meta name="twitter:[^"]+"[^>]*>', "", html_text, flags=re.I)
    html_text = re.sub(r'\n?<meta name="keywords"[^>]*>', "", html_text, flags=re.I)
    html_text = re.sub(r'\n?<script type="application/ld\+json">.*?</script>', "", html_text, flags=re.I | re.S)
    if not re.search(r'<meta\s+name=["\']robots["\']', html_text, re.I):
        pass
    else:
        html_text = re.sub(r'<meta\s+name=["\']robots["\'][^>]*>', "", html_text, count=1, flags=re.I)
    html_text = re.sub(r"<meta name=\"google-site-verification\"[^>]*>", "", html_text, flags=re.I)
    html_text = re.sub(r"<meta name=\"baidu-site-verification\"[^>]*>", "", html_text, flags=re.I)
    html_text = re.sub(r"<meta name=\"naver-site-verification\"[^>]*>", "", html_text, flags=re.I)
    extra_head = head_extra(url_path, extra)
    if re.search(r"</head>", html_text, re.I):
        # 用函数做替换：用户内容里的反斜杠不会被当成正则分组引用
        html_text = re.sub(r"</head>", lambda m: extra_head + "\n</head>", html_text, count=1, flags=re.I)
    html_text = _inject_ssr(html_text, ssr_target, ssr_html)
    return html_text


def html_file_response(filename: str, url_path=None, extra=None, ssr_target=None, ssr_html=""):
    path = os.path.join(STATIC, filename)
    if not os.path.isfile(path):
        # 兼容测试目录只放部分静态文件：回退到同级已部署静态目录
        for alt in (os.path.join(os.path.dirname(BASE), "kbeauty-scan", "static", filename),
                    os.path.join("/workspace/kbeauty-scan/static", filename)):
            if os.path.isfile(alt):
                path = alt
                break
    with open(path, encoding="utf-8") as f:
        html_text = f.read()
    url_path = url_path or FILE_PATHS.get(filename, "/")
    html_text = inject_page(html_text, url_path, extra=extra, ssr_target=ssr_target, ssr_html=ssr_html)
    html_text = html_text.replace("{{PRIVACY_CONTACT}}", _esc(privacy_contact()))
    from security import new_nonce, add_nonce, csp
    nonce = new_nonce()
    html_text = add_nonce(html_text, nonce)
    return HTMLResponse(html_text, headers={"Cache-Control": "no-cache", "Content-Security-Policy": csp(nonce)})


def privacy_contact() -> str:
    """隐私/删除请求联系方式：环境变量 PRIVACY_CONTACT_EMAIL 有值就显示，否则提示站内渠道。"""
    v = (os.environ.get("PRIVACY_CONTACT_EMAIL") or "").strip()
    if v and re.fullmatch(r"[^@\s<>\"']{1,64}@[^@\s<>\"']{1,190}", v):
        return v
    return "会员页「我的状态」里的注销账号功能，或在站内点“🚩 举报”留言给管理员"


def _iso(dt):
    if not dt:
        return datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    if getattr(dt, "tzinfo", None):
        return dt.astimezone(datetime.timezone.utc).date().isoformat()
    return dt.date().isoformat() if hasattr(dt, "date") else str(dt)[:10]


def _static_lastmod(filename):
    for folder in (STATIC, os.path.join("/workspace/kbeauty-scan/static")):
        p = os.path.join(folder, filename)
        if os.path.isfile(p):
            return datetime.datetime.fromtimestamp(os.path.getmtime(p), datetime.timezone.utc).date().isoformat()
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


FILE_FOR_PATH = {v: k for k, v in FILE_PATHS.items()}


def build_sitemap_body(extra_urls=None) -> str:
    origin = site_origin()
    items = []
    for path in SITEMAP_PATHS:
        fn = FILE_FOR_PATH.get(path) or ("index.html" if path == "/" else path.strip("/") + ".html")
        last = _static_lastmod(fn)
        items.append((origin + (path if path != "/" else "/"), last, "weekly"))
    for url, last in extra_urls or []:
        items.append((url, last, "weekly"))
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    seen = set()
    for loc, last, freq in items:
        if loc in seen:
            continue
        seen.add(loc)
        lines.append("  <url>")
        lines.append(f"    <loc>{_esc(loc)}</loc>")
        lines.append(f"    <lastmod>{_esc(last)}</lastmod>")
        lines.append(f"    <changefreq>{freq}</changefreq>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


@router.get("/robots.txt")
def robots():
    origin = site_origin()
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        "Disallow: /admin\n"
        "Disallow: /api/\n"
        "Disallow: /community/messages\n"
        "Disallow: /community/messages/\n"
        "Disallow: /community/new\n"
        "Disallow: /community/friends/edit\n"
        "Disallow: /community/friends/verify\n"
        "Allow: /promo/rules\n"
        "Disallow: /promo\n"
        "Disallow: /login\n"
        "\n"
        f"Sitemap: {origin}/sitemap.xml\n"
    )
    return Response(body, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/sitemap.xml")
def sitemap():
    extra = []
    try:
        import community as C
        extra = C.sitemap_public_posts(site_origin())
    except Exception:
        extra = []
    return Response(build_sitemap_body(extra), media_type="application/xml; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=600"})


@router.get("/{key}.txt")
def indexnow_key_file(key: str):
    try:
        expect = indexnow_key()
    except Exception:
        raise HTTPException(404, "Not Found")
    if key != expect:
        raise HTTPException(404, "Not Found")
    return Response(expect + "\n", media_type="text/plain; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=86400"})
