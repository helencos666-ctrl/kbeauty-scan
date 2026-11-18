#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the original info-card images for official_daily.json.

Pure PIL, no network, no external assets. Output: static/official-daily/*.jpg
1080x1350 JPEG, soft gradient background, big Chinese title, 3-4 bullets with
simple drawn shapes, small 旅韩助手 mark at the bottom. No QR codes, no URLs.
"""
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
    elif kind == "bank":
        d.polygon([(cx, y0 + w * .14), (x1 - w * .14, y0 + w * .36),
                   (x0 + w * .14, y0 + w * .36)], fill=pale, outline=accent)
        for i in range(3):
            xx = x0 + w * .26 + i * w * .18
            d.rounded_rectangle([xx, y0 + w * .40, xx + w * .08, y1 - w * .26], radius=5, fill=accent)
        d.rounded_rectangle([x0 + w * .14, y1 - w * .24, x1 - w * .14, y1 - w * .14],
                            radius=6, fill=accent)


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
