#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the original info-card images for official_daily.json.

Pure PIL, no network, no external assets. Output: static/official-daily/*.jpg
1080x1350 JPEG, soft gradient background, big Chinese title, 3-4 bullets with
simple drawn shapes, small 旅韩助手 mark at the bottom. No QR codes, no URLs.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1350
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, "static", "official-daily")
FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_REG = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
SC = 2  # Noto Sans CJK .ttc 里的简体中文字面

CARDS = [
    {
        "file": "2026-11-19-myeongdong-traps.jpg",
        "kicker": "避雷 · 明洞",
        "title": "明洞换钱购物\n先看这几条",
        "bullets": [
            ("coin", "换钱：看清牌价和手续费"),
            ("receipt", "退税：免税≠退税，留好小票"),
            ("hand", "拉客：主动搭话先别跟着走"),
            ("tag", "比价：按容量算单价再比"),
        ],
        "top": (232, 244, 236),
        "bottom": (208, 226, 232),
        "accent": (36, 92, 84),
    },
    {
        "file": "2026-11-19-first-week-korea.jpg",
        "kicker": "韩国生活 · 落地首周",
        "title": "初到韩国\n这四件事先排上",
        "bullets": [
            ("card", "外国人登录证：先网上预约"),
            ("phone", "手机卡：旅游卡过渡再实名"),
            ("bus", "T-money：便利店买卡充值"),
            ("bank", "银行开户：登录证＋住址证明"),
        ],
        "top": (250, 238, 224),
        "bottom": (223, 232, 244),
        "accent": (92, 62, 36),
    },
    {
        "file": "2026-11-20-hongdae-night.jpg",
        "kicker": "避雷 · 弘大夜玩",
        "title": "弘大夜里玩\n先记这几条",
        "bullets": [
            ("cup", "入场：先问清费用和消费门槛"),
            ("bag", "随身：证件自己拿，杯子不离手"),
            ("taxi", "回程：查末班车，上正规车"),
            ("camera", "街拍：先问价，不想就直接说"),
        ],
        "top": (236, 234, 246),
        "bottom": (214, 219, 238),
        "accent": (58, 54, 108),
    },
    {
        "file": "2026-11-20-korea-housing.jpg",
        "kicker": "韩国生活 · 留学生找房",
        "title": "在韩找房\n看这四步",
        "bullets": [
            ("home", "房型：先分清四种常见住处"),
            ("coin", "押金与月租：逐项写进合同"),
            ("key", "看房：水压、霉斑、暖气都试"),
            ("doc", "签约：核对房东与登记簿"),
        ],
        "top": (250, 243, 233),
        "bottom": (240, 227, 231),
        "accent": (118, 62, 54),
    },
    {
        "file": "2026-11-21-gangnam-clinic.jpg",
        "kicker": "避雷 · 江南医美咨询",
        "title": "江南医美咨询\n先问这四件事",
        "bullets": [
            ("coin", "中介抽成：总价里含什么"),
            ("chat", "翻译陪同：按什么收费"),
            ("doc", "项目与总价：写进书面"),
            ("receipt", "退款复诊：先问清条款"),
        ],
        "top": (243, 240, 238),
        "bottom": (226, 232, 240),
        "accent": (88, 58, 92),
    },
    {
        "file": "2026-11-21-korea-winter.jpg",
        "kicker": "韩国生活 · 过冬",
        "title": "在韩国过冬\n先备这几样",
        "bullets": [
            ("flame", "地暖：温控器别频繁关"),
            ("droplet", "加湿：换水清洗通风"),
            ("snow", "出行：薄冰上小步走"),
            ("coat", "保暖：羽绒服与暖宝宝"),
        ],
        "top": (232, 241, 249),
        "bottom": (236, 232, 224),
        "accent": (44, 90, 112),
    },
    {
        "file": "2026-11-22-student-winter-checklist.jpg",
        "kicker": "留学 · 期末与寒假前",
        "title": "期末与寒假前\n这几件事排上",
        "bullets": [
            ("card", "登录证：看居留期限，提前预约"),
            ("suitcase", "退宿行李：当面确认、留底"),
            ("clock", "打工时长：按许可上的条件"),
            ("plane", "回国机票与退税：单据收好"),
        ],
        "top": (247, 244, 231),
        "bottom": (229, 236, 232),
        "accent": (74, 86, 52),
    },
    {
        "file": "2026-11-22-seoul-meetup-safety.jpg",
        "kicker": "提醒 · 冬季线下聚会",
        "title": "冬天参加聚会\n先记这四条",
        "bullets": [
            ("pin", "地点：选公共场所，白天优先"),
            ("phone", "结伴：告诉朋友，散场报平安"),
            ("cup", "酒与钱：杯子看着，转账留记录"),
            ("shield", "邀请：投资代购验证码都拒绝"),
        ],
        "top": (252, 239, 232),
        "bottom": (238, 226, 232),
        "accent": (132, 52, 72),
    },
    {
        "file": "2026-11-23-group-tour-pitfalls.jpg",
        "kicker": "避坑 · 韩国跟团游",
        "title": "低价跟团游\n先看清这几条",
        "bullets": [
            ("tag", "购物站：数几处、各停多久"),
            ("doc", "合同：当地接待社与紧急联系人"),
            ("card", "备用：机票住宿自己留一份"),
            ("phone", "强迫购物：1330 旅游热线"),
        ],
        "top": (235, 242, 246),
        "bottom": (250, 240, 224),
        "accent": (150, 74, 42),
    },
    {
        "file": "2026-11-23-visa-work-checklist.jpg",
        "kicker": "留学 · 签证与打工",
        "title": "签证与打工\n先核对这几项",
        "bullets": [
            ("card", "签证：看居留期限，提前备料"),
            ("clock", "打工：先拿许可，按时长"),
            ("bank", "财力证明：以官方要求为准"),
            ("doc", "信息：出入境·Hi Korea 公告"),
        ],
        "top": (245, 238, 246),
        "bottom": (234, 244, 240),
        "accent": (72, 88, 132),
    },
    {
        "file": "2026-11-24-visa-types-pit.jpg",
        "kicker": "避坑 · 出发前签证",
        "title": "团体免签\n先分清这几种",
        "bullets": [
            ("doc", "团体免签：指定社、3人以上"),
            ("plane", "同团出入境，停留上限15天"),
            ("tag", "签证费减免不等于免签"),
            ("card", "个人自由行：仍要办签证"),
        ],
        "top": (247, 238, 232),
        "bottom": (236, 242, 245),
        "accent": (110, 44, 52),
    },
    {
        "file": "2026-11-24-peak-season-tips.jpg",
        "kicker": "攻略 · 旺季出行",
        "title": "人多的时候\n怎么玩得舒服",
        "bullets": [
            ("clock", "热门景点：赶开门或傍晚"),
            ("chat", "热门餐厅：先查取号预约"),
            ("plane", "金海机场进出：首尔+釜山"),
            ("phone", "有疑问：1330 旅游热线"),
        ],
        "top": (235, 243, 239),
        "bottom": (250, 241, 229),
        "accent": (24, 74, 92),
    },
    {
        "file": "2026-11-25-seoul-winter-travel.jpg",
        "kicker": "攻略 · 首尔入冬",
        "title": "首尔入冬\n自由行怎么走",
        "bullets": [
            ("coat", "穿衣：分层＋防风外层"),
            ("snow", "鞋：防滑底，冰面小步"),
            ("phone", "手机：低温掉电，带充电宝"),
            ("bus", "行程：户外与室内交替"),
        ],
        "top": (238, 242, 248),
        "bottom": (228, 236, 244),
        "accent": (46, 62, 104),
    },
    {
        "file": "2026-11-25-korea-winter-home.jpg",
        "kicker": "生活 · 租房过冬",
        "title": "在韩过冬\n先做这几件事",
        "bullets": [
            ("flame", "暖气：外出模式，别全关"),
            ("receipt", "账单：看懂用量，按时缴"),
            ("droplet", "水管：保温，久出细流"),
            ("shield", "燃气味：关阀开窗，别开电器"),
        ],
        "top": (248, 244, 236),
        "bottom": (236, 240, 232),
        "accent": (96, 72, 28),
    },
    {
        "file": "2026-11-26-seoul-winter-festival.jpg",
        "kicker": "攻略 · 年末首尔",
        "title": "年末看灯光\n先排好这几件",
        "bullets": [
            ("clock", "场次：以官网公布为准"),
            ("coat", "穿衣：分层＋防滑鞋"),
            ("bus", "跨年夜：地铁临时调整"),
            ("pin", "走散：先约好集合点"),
        ],
        "top": (239, 244, 246),
        "bottom": (247, 240, 228),
        "accent": (18, 70, 72),
    },
    {
        "file": "2026-11-26-student-work-check.jpg",
        "kicker": "留学 · 打工合规",
        "title": "打工之前\n先自查这几项",
        "bullets": [
            ("doc", "上岗：先取得就业许可"),
            ("shield", "禁区：建筑·配送·娱乐"),
            ("phone", "账号：不外借外卖App"),
            ("chat", "不确定：问学校或1345"),
        ],
        "top": (243, 237, 245),
        "bottom": (246, 241, 232),
        "accent": (62, 52, 66),
    },
    {
        "file": "2026-11-27-gangnam-winter-light.jpg",
        "kicker": "攻略 · 江南冬夜",
        "title": "12月江南看光影\n半日路线怎么排",
        "bullets": [
            ("clock", "时间：以主办方公告为准"),
            ("bus", "路线：三成站5号出口广场"),
            ("coat", "夜里冷：分层＋防滑鞋"),
            ("pin", "人多：约好集合点，别挡通道"),
        ],
        "top": (250, 236, 238),
        "bottom": (235, 238, 250),
        "accent": (128, 36, 64),
    },
    {
        "file": "2026-11-27-group-visa-extension.jpg",
        "kicker": "签证 · 团体免签延长",
        "title": "团体免签延长\n自由行别搞混",
        "bullets": [
            ("doc", "团体免签：指定社、3人以上"),
            ("plane", "停留：不超过15天，同团进出"),
            ("card", "自由行：仍要按规定办签证"),
            ("phone", "不确定：使领馆公告或1345"),
        ],
        "top": (233, 247, 242),
        "bottom": (248, 241, 226),
        "accent": (78, 40, 104),
    },
    {
        "file": "2026-11-28-jeju-winter-travel.jpg",
        "kicker": "攻略 · 济州12月",
        "title": "12月去济州\n冬季怎么穿怎么排",
        "bullets": [
            ("flame", "活动：海滩圣诞与冬季庆典"),
            ("coat", "穿衣：防风外套＋薄羽绒"),
            ("bus", "路线：东线与西线分开排"),
            ("snow", "汉拿山：看当日开放与预约"),
        ],
        "top": (231, 247, 243),
        "bottom": (253, 240, 224),
        "accent": (186, 86, 66),
    },
    {
        "file": "2026-11-28-jeju-visa-difference.jpg",
        "kicker": "签证 · 济州免签",
        "title": "济州免签\n和团体免签别混",
        "bullets": [
            ("plane", "直飞济州：停留上限30天"),
            ("pin", "活动范围：只在济州岛内"),
            ("shield", "转机入境：不符合免签条件"),
            ("doc", "团体免签：另一项制度"),
        ],
        "top": (250, 243, 228),
        "bottom": (234, 246, 246),
        "accent": (22, 100, 92),
    },
]


def font(size, bold=True):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size, index=SC)


def gradient(top, bottom):
    im = Image.new("RGB", (W, H), top)
    d = ImageDraw.Draw(im)
    for y in range(H):
        t = y / (H - 1)
        d.line(
            [(0, y), (W, y)],
            fill=(
                int(top[0] + (bottom[0] - top[0]) * t),
                int(top[1] + (bottom[1] - top[1]) * t),
                int(top[2] + (bottom[2] - top[2]) * t),
            ),
        )
    return im


def soft_circles(im, accent):
    """A few translucent blobs so the card does not look flat."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([-160, -220, 420, 360], fill=accent + (26,))
    d.ellipse([W - 260, H - 420, W + 220, H + 60], fill=accent + (22,))
    d.ellipse([W - 420, 120, W - 40, 500], fill=(255, 255, 255, 90))
    return Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")


def wrap(draw, text, fnt, maxw):
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if draw.textlength(cur + ch, font=fnt) <= maxw:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def icon(d, kind, box, accent):
    """Simple flat shapes; each occupies the square box (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w = x1 - x0
    dark = tuple(max(0, c - 26) for c in accent)
    pale = tuple(min(255, c + 42) for c in accent)
    if kind == "coin":
        d.ellipse([x0 + w * .16, y0 + w * .16, x1 - w * .16, y1 - w * .16], fill=pale)
        d.ellipse([x0 + w * .16, y0 + w * .16, x1 - w * .16, y1 - w * .16], outline=accent, width=6)
        d.line([(cx, y0 + w * .32), (cx, y1 - w * .32)], fill=accent, width=6)
        d.line([(cx - w * .12, y0 + w * .44), (cx + w * .12, y0 + w * .44)], fill=accent, width=6)
        d.line([(cx - w * .12, y1 - w * .44), (cx + w * .12, y1 - w * .44)], fill=accent, width=6)
    elif kind == "receipt":
        d.rounded_rectangle([x0 + w * .26, y0 + w * .12, x1 - w * .26, y1 - w * .10],
                            radius=10, fill=(255, 255, 255), outline=accent, width=5)
        for i in range(3):
            yy = y0 + w * .30 + i * w * .13
            d.line([(x0 + w * .36, yy), (x1 - w * .36, yy)], fill=pale, width=7)
        d.line([(x0 + w * .36, y1 - w * .22), (x1 - w * .44, y1 - w * .22)], fill=accent, width=7)
    elif kind == "hand":
        # 一个人站在门口招手
        d.ellipse([cx - w * .16, y0 + w * .12, cx + w * .16, y0 + w * .44], fill=accent)
        d.rounded_rectangle([cx - w * .22, y0 + w * .46, cx + w * .22, y1 - w * .14],
                            radius=16, fill=dark)
        d.line([(cx + w * .18, y0 + w * .52), (x1 - w * .10, y0 + w * .34)], fill=accent, width=9)
        d.ellipse([x1 - w * .18, y0 + w * .24, x1 - w * .06, y0 + w * .36], fill=accent)
        for i in range(2):
            d.line([(x1 - w * .06 + i * 8, y0 + w * .10 + i * 6), (x1 - w * .02 + i * 8, y0 + w * .02 + i * 6)],
                   fill=pale, width=5)
    elif kind == "tag":
        d.polygon([(x0 + w * .20, y0 + w * .34), (cx + w * .06, y0 + w * .16),
                   (x1 - w * .18, y0 + w * .40), (x1 - w * .40, y1 - w * .18),
                   (x0 + w * .18, y1 - w * .40)], fill=pale, outline=accent)
        d.ellipse([x0 + w * .30, y0 + w * .30, x0 + w * .44, y0 + w * .44], fill=accent)
    elif kind == "card":
        d.rounded_rectangle([x0 + w * .16, y0 + w * .26, x1 - w * .16, y1 - w * .26],
                            radius=12, fill=(255, 255, 255), outline=accent, width=5)
        d.rounded_rectangle([x0 + w * .16, y0 + w * .38, x1 - w * .16, y0 + w * .50],
                            radius=6, fill=pale)
        d.ellipse([x0 + w * .26, y0 + w * .58, x0 + w * .38, y0 + w * .70], fill=accent)
    elif kind == "phone":
        d.rounded_rectangle([cx - w * .20, y0 + w * .12, cx + w * .20, y1 - w * .12],
                            radius=16, fill=(255, 255, 255), outline=accent, width=5)
        d.line([(cx - w * .08, y0 + w * .22), (cx + w * .08, y0 + w * .22)], fill=pale, width=7)
        d.ellipse([cx - w * .05, y1 - w * .24, cx + w * .05, y1 - w * .14], fill=accent)
    elif kind == "bus":
        d.rounded_rectangle([x0 + w * .16, y0 + w * .26, x1 - w * .16, y1 - w * .30],
                            radius=14, fill=(255, 255, 255), outline=accent, width=5)
        d.rounded_rectangle([x0 + w * .24, y0 + w * .34, x1 - w * .24, y0 + w * .56],
                            radius=8, fill=pale)
        for xx in (x0 + w * .26, x1 - w * .40):
            d.ellipse([xx, y1 - w * .38, xx + w * .14, y1 - w * .24], fill=accent)
    elif kind == "cup":
        # 鸡尾酒杯
        d.polygon([(x0 + w * .24, y0 + w * .24), (x1 - w * .24, y0 + w * .24),
                   (cx, y1 - w * .40)], fill=pale, outline=accent)
        d.line([(x0 + w * .24, y0 + w * .24), (x1 - w * .24, y0 + w * .24)], fill=accent, width=7)
        d.line([(cx, y1 - w * .40), (cx, y1 - w * .20)], fill=accent, width=7)
        d.line([(cx - w * .16, y1 - w * .20), (cx + w * .16, y1 - w * .20)], fill=accent, width=7)
        d.line([(cx + w * .06, y0 + w * .22), (cx + w * .16, y0 + w * .04)], fill=accent, width=6)
    elif kind == "bag":
        # 斜挎包
        d.arc([cx - w * .20, y0 + w * .12, cx + w * .20, y0 + w * .54],
              start=180, end=360, fill=accent, width=7)
        d.rounded_rectangle([x0 + w * .20, y0 + w * .38, x1 - w * .20, y1 - w * .12],
                            radius=14, fill=(255, 255, 255), outline=accent, width=5)
        d.rounded_rectangle([cx - w * .05, y0 + w * .54, cx + w * .05, y0 + w * .66],
                            radius=5, fill=dark)
    elif kind == "taxi":
        # 出租车
        d.rounded_rectangle([x0 + w * .12, y0 + w * .44, x1 - w * .12, y1 - w * .22],
                            radius=14, fill=(255, 255, 255), outline=accent, width=5)
        d.rounded_rectangle([x0 + w * .30, y0 + w * .26, x1 - w * .30, y0 + w * .48],
                            radius=10, fill=pale, outline=accent, width=4)
        d.rounded_rectangle([cx - w * .09, y0 + w * .14, cx + w * .09, y0 + w * .28],
                            radius=5, fill=accent)
        for xx in (x0 + w * .22, x1 - w * .38):
            d.ellipse([xx, y1 - w * .34, xx + w * .16, y1 - w * .18], fill=accent)
    elif kind == "camera":
        # 相机
        d.rounded_rectangle([x0 + w * .12, y0 + w * .30, x1 - w * .12, y1 - w * .20],
                            radius=12, fill=(255, 255, 255), outline=accent, width=5)
        d.rounded_rectangle([cx - w * .14, y0 + w * .18, cx + w * .14, y0 + w * .34],
                            radius=6, fill=pale)
        d.ellipse([cx - w * .17, y0 + w * .40, cx + w * .17, y1 - w * .26],
                  outline=accent, width=6)
        d.ellipse([x1 - w * .30, y0 + w * .38, x1 - w * .24, y0 + w * .44], fill=accent)
    elif kind == "home":
        # 房子
        d.polygon([(cx, y0 + w * .12), (x1 - w * .14, y0 + w * .42),
                   (x0 + w * .14, y0 + w * .42)], fill=pale, outline=accent)
        d.rounded_rectangle([x0 + w * .22, y0 + w * .40, x1 - w * .22, y1 - w * .12],
                            radius=8, fill=(255, 255, 255), outline=accent, width=5)
        d.rounded_rectangle([cx - w * .07, y1 - w * .36, cx + w * .07, y1 - w * .12],
                            radius=4, fill=accent)
    elif kind == "key":
        # 钥匙
        d.ellipse([x0 + w * .14, y0 + w * .26, x0 + w * .52, y0 + w * .64],
                  outline=accent, width=9)
        d.line([(x0 + w * .50, y0 + w * .45), (x1 - w * .12, y0 + w * .45)], fill=accent, width=9)
        d.line([(x1 - w * .30, y0 + w * .45), (x1 - w * .30, y1 - w * .26)], fill=accent, width=9)
        d.line([(x1 - w * .12, y0 + w * .45), (x1 - w * .12, y1 - w * .20)], fill=accent, width=9)
    elif kind == "doc":
        # 登记簿/文件
        d.rounded_rectangle([x0 + w * .22, y0 + w * .12, x1 - w * .22, y1 - w * .12],
                            radius=10, fill=(255, 255, 255), outline=accent, width=5)
        for i in range(3):
            yy = y0 + w * .28 + i * w * .12
            d.line([(x0 + w * .32, yy), (x1 - w * .32, yy)], fill=pale, width=7)
        d.line([(x0 + w * .32, y1 - w * .26), (cx - w * .02, y1 - w * .26)], fill=accent, width=8)
        d.line([(cx - w * .02, y1 - w * .26), (x1 - w * .30, y0 + w * .58)], fill=accent, width=8)
    elif kind == "bank":
        d.polygon([(cx, y0 + w * .14), (x1 - w * .14, y0 + w * .36),
                   (x0 + w * .14, y0 + w * .36)], fill=pale, outline=accent)
        for i in range(3):
            xx = x0 + w * .26 + i * w * .18
            d.rounded_rectangle([xx, y0 + w * .40, xx + w * .08, y1 - w * .26], radius=5, fill=accent)
        d.rounded_rectangle([x0 + w * .14, y1 - w * .24, x1 - w * .14, y1 - w * .14],
                            radius=6, fill=accent)
    elif kind == "chat":
        # 对话气泡
        d.rounded_rectangle([x0 + w * .14, y0 + w * .18, x1 - w * .14, y1 - w * .34],
                            radius=18, fill=(255, 255, 255), outline=accent, width=5)
        d.polygon([(x0 + w * .28, y1 - w * .36), (x0 + w * .46, y1 - w * .36),
                   (x0 + w * .26, y1 - w * .08)], fill=accent)
        for i in range(3):
            ox = x0 + w * .26 + i * w * .18
            d.ellipse([ox, cy - w * .06, ox + w * .11, cy + w * .05], fill=pale)
    elif kind == "flame":
        # 暖气片（地暖常配的散热器）
        d.rounded_rectangle([x0 + w * .14, y0 + w * .34, x1 - w * .14, y1 - w * .14],
                            radius=10, fill=(255, 255, 255), outline=accent, width=5)
        for i in range(4):
            xx = x0 + w * .22 + i * w * .16
            d.line([(xx, y0 + w * .40), (xx, y1 - w * .20)], fill=pale, width=6)
        d.arc([cx - w * .28, y0 + w * .04, cx - w * .02, y0 + w * .32],
              start=200, end=340, fill=accent, width=5)
        d.arc([cx + w * .02, y0 + w * .04, cx + w * .28, y0 + w * .32],
              start=200, end=340, fill=accent, width=5)
    elif kind == "droplet":
        # 水滴
        d.ellipse([cx - w * .26, y0 + w * .38, cx + w * .26, y1 - w * .10],
                  fill=pale, outline=accent, width=5)
        d.polygon([(cx, y0 + w * .10), (cx + w * .22, y0 + w * .54),
                   (cx - w * .22, y0 + w * .54)], fill=pale, outline=accent)
        d.line([(cx - w * .12, y1 - w * .34), (cx - w * .14, y1 - w * .48)],
               fill=(255, 255, 255), width=5)
    elif kind == "snow":
        # 雪花
        for i in range(3):
            ang = math.radians(90 + i * 60)
            dx, dy = math.cos(ang), math.sin(ang)
            d.line([(cx - dx * w * .40, cy - dy * w * .40), (cx + dx * w * .40, cy + dy * w * .40)],
                   fill=accent, width=7)
            for t in (.22, -.22):
                bx, by = cx + dx * w * .40 * t, cy + dy * w * .40 * t
                for s in (1, -1):
                    ba = ang + s * math.radians(40)
                    d.line([(bx, by), (bx + math.cos(ba) * w * .16, by + math.sin(ba) * w * .16)],
                           fill=pale, width=5)
        d.ellipse([cx - w * .05, cy - w * .05, cx + w * .05, cy + w * .05], fill=accent)
    elif kind == "coat":
        # 羽绒服
        d.rounded_rectangle([x0 + w * .18, y0 + w * .26, x1 - w * .18, y1 - w * .10],
                            radius=14, fill=(255, 255, 255), outline=accent, width=5)
        d.rounded_rectangle([cx - w * .16, y0 + w * .12, cx + w * .16, y0 + w * .32],
                            radius=9, fill=pale, outline=accent, width=4)
        for i in range(3):
            yy = y0 + w * .46 + i * w * .16
            d.line([(x0 + w * .24, yy), (x1 - w * .24, yy)], fill=pale, width=5)
        d.line([(cx, y0 + w * .34), (cx, y1 - w * .16)], fill=accent, width=4)
        d.line([(cx - w * .16, y0 + w * .12), (cx - w * .06, y0 + w * .30)], fill=accent, width=5)
        d.line([(cx + w * .16, y0 + w * .12), (cx + w * .06, y0 + w * .30)], fill=accent, width=5)
    elif kind == "suitcase":
        # 行李箱
        d.rounded_rectangle([cx - w * .13, y0 + w * .10, cx + w * .13, y0 + w * .30],
                            radius=6, fill=(255, 255, 255), outline=accent, width=5)
        d.rounded_rectangle([x0 + w * .12, y0 + w * .28, x1 - w * .12, y1 - w * .18],
                            radius=12, fill=(255, 255, 255), outline=accent, width=5)
        d.line([(cx - w * .12, y0 + w * .34), (cx - w * .12, y1 - w * .24)], fill=pale, width=8)
        d.line([(cx + w * .12, y0 + w * .34), (cx + w * .12, y1 - w * .24)], fill=pale, width=8)
        for xx in (x0 + w * .24, x1 - w * .30):
            d.ellipse([xx, y1 - w * .20, xx + w * .06, y1 - w * .14], fill=accent)
    elif kind == "clock":
        # 时钟
        d.ellipse([cx - w * .34, cy - w * .34, cx + w * .34, cy + w * .34],
                  fill=(255, 255, 255), outline=accent, width=6)
        d.line([(cx, cy), (cx, cy - w * .20)], fill=accent, width=7)
        d.line([(cx, cy), (cx + w * .18, cy + w * .10)], fill=accent, width=7)
        for i in range(4):
            ang = math.radians(90 * i)
            d.line([(cx + math.cos(ang) * w * .26, cy + math.sin(ang) * w * .26),
                    (cx + math.cos(ang) * w * .31, cy + math.sin(ang) * w * .31)], fill=pale, width=4)
    elif kind == "plane":
        # 机票（带缺口的登机牌）
        d.rounded_rectangle([x0 + w * .10, y0 + w * .24, x1 - w * .10, y1 - w * .24],
                            radius=8, fill=(255, 255, 255), outline=accent, width=5)
        d.ellipse([cx - w * .30, cy - w * .07, cx - w * .16, cy + w * .07], fill=accent)
        d.ellipse([cx + w * .16, cy - w * .07, cx + w * .30, cy + w * .07], fill=accent)
        d.line([(cx, y0 + w * .30), (cx, y1 - w * .30)], fill=accent, width=4)
        d.line([(x0 + w * .20, y0 + w * .40), (cx - w * .10, y0 + w * .40)], fill=pale, width=6)
        d.line([(x0 + w * .20, y0 + w * .56), (cx - w * .10, y0 + w * .56)], fill=pale, width=6)
        d.line([(cx + w * .10, y0 + w * .48), (x1 - w * .20, y0 + w * .48)], fill=pale, width=6)
    elif kind == "pin":
        # 地图定位针
        d.ellipse([cx - w * .26, y0 + w * .12, cx + w * .26, y0 + w * .64],
                  fill=pale, outline=accent, width=5)
        d.polygon([(cx - w * .18, y0 + w * .52), (cx + w * .18, y0 + w * .52),
                   (cx, y1 - w * .10)], fill=pale, outline=accent)
        d.ellipse([cx - w * .10, y0 + w * .28, cx + w * .10, y0 + w * .48], fill=(255, 255, 255))
        d.ellipse([cx - w * .05, y0 + w * .33, cx + w * .05, y0 + w * .43], fill=accent)
    elif kind == "shield":
        # 盾牌（拒绝/提醒）
        d.polygon([(cx, y0 + w * .10), (x1 - w * .16, y0 + w * .26),
                   (x1 - w * .22, y1 - w * .34), (cx, y1 - w * .08),
                   (x0 + w * .22, y1 - w * .34), (x0 + w * .16, y0 + w * .26)],
                  fill=(255, 255, 255), outline=accent, width=6)
        d.line([(cx - w * .14, cy - w * .02), (cx - w * .02, cy + w * .10)], fill=accent, width=7)
        d.line([(cx - w * .02, cy + w * .10), (cx + w * .16, cy - w * .16)], fill=accent, width=7)


def build(card):
    im = soft_circles(gradient(card["top"], card["bottom"]), card["accent"])
    d = ImageDraw.Draw(im)
    accent = card["accent"]

    f_kick = font(38, bold=False)
    f_title = font(96)
    f_bullet = font(48, bold=False)

    # top kicker
    d.text((90, 96), card["kicker"], font=f_kick, fill=accent)
    d.line([(90, 158), (90 + 120, 158)], fill=accent, width=5)

    # title block
    y = 210
    for line in card["title"].split("\n"):
        d.text((90, y), line, font=f_title, fill=(28, 34, 40))
        y += 122
    y += 40

    # bullets
    panel_top = y + 16
    panel_bot = H - 214
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle([70, panel_top, W - 70, panel_bot],
                                            radius=36, fill=(255, 255, 255, 120))
    im.paste(Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB"), (0, 0))
    d = ImageDraw.Draw(im)

    n = len(card["bullets"])
    step = (panel_bot - panel_top - 40) / n
    for i, (kind, text) in enumerate(card["bullets"]):
        top = panel_top + 20 + i * step
        cy = top + step / 2
        icon(d, kind, (104, cy - 42, 104 + 84, cy + 42), accent)
        lines = wrap(d, text, f_bullet, 700)
        ty = cy - (len(lines) * 62) / 2
        for ln in lines:
            d.text((214, ty), ln, font=f_bullet, fill=(52, 60, 68))
            ty += 62
        if i < n - 1:
            d.line([(110, top + step - 2), (W - 110, top + step - 2)],
                   fill=accent + (0,) if False else (236, 240, 242), width=2)

    # bottom mark
    d.line([(90, H - 132), (W - 90, H - 132)], fill=accent, width=3)
    d.text((90, H - 110), "旅韩助手", font=font(40, bold=False), fill=accent)
    d.text((W - 90, H - 110), "小编整理 · 仅供参考", font=font(30, bold=False),
           fill=(128, 136, 144), anchor="ra")

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, card["file"])
    im.save(path, "JPEG", quality=88, optimize=True, progressive=True)
    print("wrote", path, im.size)


if __name__ == "__main__":
    for c in CARDS:
        build(c)
