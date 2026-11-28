"""各商家分类的入驻示范案例（静态示例，不是真实商家，不写入用户/评价数据）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from community import STATIC, _page
from feature_flags import UNAVAILABLE_MSG, is_hidden_cat, unavailable_payload

router = APIRouter()

# 示范案例统一标明，避免被当成真实商家。不写评价、单量或任何虚构经营数据。
EXAMPLE_BADGE = "示范案例（仅供参考，非真实商家）"
PHOTO_CAPTIONS = (
    "示范照片 1 · 门头或形象（示意）",
    "示范照片 2 · 服务场景（示意）",
    "示范照片 3 · 服务内容（示意）",
    "示范照片 4 · 价目格式（示意）",
    "示范照片 5 · 环境或行程（示意）",
    "示范照片 6 · 补充说明（示意）",
)
CONSULT_NOTE = "示范案例不能咨询。真实商家入驻并通过审核后，访客可点「站内咨询」联系，请不要在简介里写电话、微信或二维码。"

# 店名、区域、价格都是「请填写」格式示范，不是真实店铺。
EXAMPLES = {
    "guide": {
        "name": "示范店名（请改成你的向导名称）",
        "area": "区域示例：首尔全市（请改成你的服务区域）",
        "intro": "用两三句话说明你擅长陪哪些行程、熟悉哪些区域、一次大概多长时间。不要写电话或微信。以下为填写格式示范，不是真实商家。",
        "services": "· 半日陪同（约 4 小时）：说明包含哪些内容，例如地铁换乘、定点讲解\n· 一日陪同（约 8 小时）：说明行程安排方式\n· 购物陪同：说明是否只负责带路与翻译",
        "price": "· 半日陪同：请填写价格（韩元）\n· 一日陪同：请填写价格（韩元）\n· 超时或增项：请说明计费方式\n（示范格式，请改成你的真实报价，不要写虚构数字。）",
        "hours": "可服务时间示例：每天 09:00–18:00（请改成你的时间）",
    },
    "clinic": {
        "name": "示范名称（请填写合法医疗机构全称）",
        "area": "区域示例：江南（请改成机构所在区域）",
        "intro": "仅限合法医疗机构自行入驻，不接受中介或陪诊代订。请写清诊疗科目和注意事项，不要承诺疗效或写对比广告。以下为填写格式示范，不是真实机构。",
        "services": "· 请列出可预约的项目类别（不要写保证效果的语句）\n· 请说明是否提供中文沟通\n· 请提醒客人以到院面诊和医生说明为准",
        "price": "· 项目 A：请填写价格或「到院面诊后确定」\n· 项目 B：请填写价格或区间（韩元）\n（示范格式。医疗效果因人而异，本站不承诺任何效果。）",
        "hours": "门诊时间示例：工作日 10:00–19:00（请改成实际时间）",
    },
    "food": {
        "name": "示范店名（请改成你的代点或外卖服务名称）",
        "area": "区域示例：明洞 / 弘大（请改成你能送到的区域）",
        "intro": "说明你可以代点哪些餐饮、如何确认菜单和送达时间。费用与餐费请分开写清楚。以下为填写格式示范，不是真实商家。",
        "services": "· 指定餐厅代点：说明下单和送达方式\n· 菜单沟通：说明是否可帮忙点餐、标注忌口\n· 无法配送的情况：请提前说明",
        "price": "· 代点服务费：请填写价格（韩元/次）\n· 餐费：按餐厅菜单另计\n· 偏远区域：请说明是否加收\n（示范格式，请改成你的真实报价。）",
        "hours": "可接单时间示例：每天 11:00–21:00（请改成你的时间）",
    },
    "airport": {
        "name": "示范名称（请改成你的接送机服务名称）",
        "area": "区域示例：仁川机场 ↔ 首尔市区（请改成你的线路）",
        "intro": "只写机场与住宿之间的单程接送。须有合法营运资质，不接受包车游览。以下为填写格式示范，不是真实商家。",
        "services": "· 接机：机场 → 酒店（单程）\n· 送机：酒店 → 机场（单程）\n· 请说明车型、行李件数和等候时间",
        "price": "· 仁川机场 ↔ 首尔市区：请填写价格（韩元）\n· 金浦机场 ↔ 首尔市区：请填写价格（韩元）\n· 夜间或等候超时：请说明计费方式\n（示范格式，请改成你的真实报价。）",
        "hours": "可预约时间示例：每天 05:00–23:00（请改成你的时间）",
    },
    "hotel": {
        "name": "示范店名（请改成住宿名称）",
        "area": "区域示例：弘大（请改成所在区域）",
        "intro": "介绍房型、位置和入住注意事项。价格以你填写的为准，并提醒以入住当天规则为准。以下为填写格式示范，不是真实商家。",
        "services": "· 房型示例：双人房 / 家庭房（请改成实际房型）\n· 入住与退房时间：请填写\n· 是否含早餐、是否可加床：请说明",
        "price": "· 房型 A：请填写每晚价格（韩元）\n· 房型 B：请填写每晚价格（韩元）\n· 淡旺季或节假日：请说明是否另计\n（示范格式，请改成你的真实报价。）",
        "hours": "接待时间示例：入住 15:00 后，退房 11:00 前（请改成实际时间）",
    },
    "interp": {
        "name": "示范名称（请改成你的翻译服务名称）",
        "area": "区域示例：首尔（请改成你的服务区域）",
        "intro": "说明你可以提供哪些翻译或沟通协助，例如就医沟通、办事陪同、短时口译。以下为填写格式示范，不是真实商家。",
        "services": "· 短时口译（按小时）：说明适合的场合\n· 半日 / 一日陪同翻译：说明包含内容\n· 不提供的事项：例如不代办签证，请写清楚",
        "price": "· 按小时：请填写价格（韩元/小时）\n· 半日或一日：请填写价格（韩元）\n· 交通费是否另计：请说明\n（示范格式，请改成你的真实报价。）",
        "hours": "可预约时间示例：每天 09:00–20:00（请改成你的时间）",
    },
    "kpop": {
        "name": "示范名称（请改成你的追星协助名称）",
        "area": "区域示例：首尔（请改成活动区域）",
        "intro": "可写演唱会、见面会、应援或快闪店的陪同与路线协助。不接受加价代抢票。以下为填写格式示范，不是真实商家。",
        "services": "· 活动当日陪同：说明集合、交通和翻译\n· 快闪店或周边店路线协助\n· 不包含代抢门票或加价转票，请在简介里写明",
        "price": "· 半日陪同：请填写价格（韩元）\n· 活动当日陪同：请填写价格（韩元）\n（示范格式，请改成你的真实报价。门票费用请客人自行承担。）",
        "hours": "可服务时间示例：视活动安排提前预约（请改成你的时间）",
    },
    "car": {
        "name": "示范名称（请改成你的租车服务名称）",
        "area": "区域示例：首尔 / 济州（请改成取还车地点）",
        "intro": "说明可租车型、取还车方式和所需证件。须有合法营运资质。以下为填写格式示范，不是真实商家。",
        "services": "· 短租（按天）：说明包含保险与里程\n· 取还车地点：请填写\n· 所需证件：请说明",
        "price": "· 车型 A 每天：请填写价格（韩元）\n· 车型 B 每天：请填写价格（韩元）\n· 保险或手续费：请单独写清\n（示范格式，请改成你的真实报价。）",
        "hours": "取还车时间示例：每天 08:00–20:00（请改成实际时间）",
    },
    "sim": {
        "name": "示范名称（请改成你的电话卡服务名称）",
        "area": "区域示例：仁川机场 / 市区领取（请改成领取方式）",
        "intro": "说明套餐天数、流量和领取或邮寄方式。不要在公开栏写私人电话。以下为填写格式示范，不是真实商家。",
        "services": "· 短期上网卡：请说明天数和流量\n· 领取方式：机场领取 / 市区领取 / 邮寄（请改成实际方式）\n· 激活与退换：请说明注意事项",
        "price": "· 套餐 A（天数/流量）：请填写价格（韩元）\n· 套餐 B（天数/流量）：请填写价格（韩元）\n（示范格式，请改成你的真实报价。）",
        "hours": "领取或客服时间示例：每天 08:00–22:00（请改成你的时间）",
    },
    "logistics": {
        "name": "示范名称（请改成你的物流服务名称）",
        "area": "区域示例：首尔取件 → 寄往中国（请改成你的线路）",
        "intro": "说明可寄物品类型、计费方式和时效说明。违禁品和限运请写清楚。以下为填写格式示范，不是真实商家。",
        "services": "· 国际快递 / 集运：请说明收件和打包方式\n· 计费：按重量或体积，请写清规则\n· 不可寄送的物品：请列出",
        "price": "· 计费单位：请填写（例如每千克韩元）\n· 起步价：请填写（韩元）\n· 时效：请写预估范围，并说明以承运方为准\n（示范格式，请改成你的真实报价。）",
        "hours": "取件或咨询时间示例：工作日 09:00–18:00（请改成你的时间）",
    },
    "daigou": {
        "name": "示范名称（请改成你的代购服务名称）",
        "area": "区域示例：首尔采购（请改成你的采购范围）",
        "intro": "说明可代购的品类、下单和发货方式。价格与商品须如实描述，不要写无法核实的比较。以下为填写格式示范，不是真实商家。",
        "services": "· 可代购品类：例如美妆、服饰、食品（请改成实际范围）\n· 下单流程：请说明如何确认款式和数量\n· 发货或自取：请说明",
        "price": "· 服务费：请填写（韩元或按商品比例，须写清楚）\n· 商品费用：按实际小票或标价另计\n· 运费：请说明是否另计\n（示范格式，请改成你的真实报价。）",
        "hours": "接单时间示例：每天 10:00–20:00（请改成你的时间）",
    },
    "other": {
        "name": "示范名称（请改成你的服务名称）",
        "area": "区域示例：首尔（请改成你的服务区域）",
        "intro": "若你的服务不在前面各类里，请在这里写清具体做什么、适合谁、有哪些限制。审核时会再确认是否适合展示。以下为填写格式示范，不是真实商家。",
        "services": "· 服务内容：请分点写清楚\n· 不包含的内容：请提前说明\n· 需要客人准备的资料：请列出",
        "price": "· 基础服务：请填写价格（韩元）\n· 加项或超时：请说明计费方式\n（示范格式，请改成你的真实报价。）",
        "hours": "可服务时间示例：请填写你的时间",
    },
}


def _photos(cat: str):
    return [
        {
            "src": f"/static/shop-examples/{cat}-{i}.jpg?v=100",
            "caption": PHOTO_CAPTIONS[i - 1],
        }
        for i in range(1, 7)
    ]


def example_out(cat: str, *, allow_hidden=False):
    from providers import CANON_CATS, canon, cat_name
    key = canon(cat)
    if key not in EXAMPLES or key not in CANON_CATS:
        return None
    if not allow_hidden and is_hidden_cat(key):
        return None
    e = EXAMPLES[key]
    return {
        "is_example": True,
        "badge": EXAMPLE_BADGE,
        "category": key,
        "category_name": cat_name(key),
        "name": e["name"],
        "area": e["area"],
        "intro": e["intro"],
        "services": e["services"],
        "price": e["price"],
        "hours": e["hours"],
        "photos": _photos(key),
        "consult_note": CONSULT_NOTE,
        "href": f"/shops/examples/{key}",
        "apply_href": f"/partner?cat={key}",
        "guide_href": "/shops/join-guide",
    }


@router.get("/api/shop-examples")
def api_examples(cat: str = ""):
    from providers import CANON_CATS, canon
    if cat:
        if is_hidden_cat(canon(cat) or cat):
            return unavailable_payload(item=None)
        item = example_out(cat)
        if not item:
            raise HTTPException(404, "没有这个分类的示范案例")
        return {"item": item}
    return {"items": [example_out(k) for k in CANON_CATS if example_out(k)], "badge": EXAMPLE_BADGE}


@router.get("/api/shop-examples/{cat}")
def api_example_one(cat: str):
    from providers import canon
    if is_hidden_cat(canon(cat) or cat):
        return unavailable_payload(item=None)
    item = example_out(cat)
    if not item:
        raise HTTPException(404, "没有这个分类的示范案例")
    return {"item": item}


@router.get("/shops/examples/{cat}")
def p_example(cat: str):
    from providers import canon
    if is_hidden_cat(canon(cat) or cat):
        return _page("unavailable.html", "/shops/examples/%s" % (canon(cat) or cat),
                     extra={"title": UNAVAILABLE_MSG + " · 旅韩助手",
                            "description": UNAVAILABLE_MSG, "index": False})
    item = example_out(cat)
    if not item:
        raise HTTPException(404, "页面不存在")
    return _page("shop-example.html", "/shops/examples/%s" % item["category"],
                 extra={"title": "%s · %s · 旅韩助手" % (EXAMPLE_BADGE, item["category_name"]),
                        "description": "商家入驻填写格式示范，仅供参考，不是真实商家。",
                        "index": False})


@router.get("/shops/join-guide")
def p_join_guide():
    return _page("join-guide.html", "/shops/join-guide",
                 extra={"title": "商家入驻教程 · 旅韩助手",
                        "description": "旅韩助手商家入驻步骤：看示范案例、填写申请、等待审核、通过后在对应分类展示，访客通过站内咨询联系。",
                        "index": True})


@router.get("/shops/start")
def p_start():
    return _page("merchant-start.html", "/shops/start",
                 extra={"title": "我是商家 · 入驻并发布服务 · 旅韩助手",
                        "description": "商家入驻与发布服务：尚未入驻请先填写申请，已入驻可直接发布服务信息。",
                        "index": False})


@router.get("/shops/cat/{cat}")
def p_shops_cat(cat: str):
    from providers import CANON_CATS, canon
    key = canon(cat)
    if key not in CANON_CATS:
        raise HTTPException(404, "页面不存在")
    return RedirectResponse("/shops?cat=" + key, status_code=302)


@router.get("/shops/{cat}")
def p_shops_cat_alias(cat: str):
    from providers import CANON_CATS, canon
    key = canon(cat)
    if key in CANON_CATS and cat not in ("start", "join-guide", "examples", "p", "cat"):
        return RedirectResponse("/shops?cat=" + key, status_code=302)
    raise HTTPException(404, "页面不存在")


@router.get("/join-guide")
def p_join_guide_short():
    return RedirectResponse("/shops/join-guide", status_code=302)


@router.get("/merchant-start")
def p_start_short():
    return RedirectResponse("/shops/start", status_code=302)
