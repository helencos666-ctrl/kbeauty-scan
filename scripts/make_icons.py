#!/usr/bin/env python3
"""Generate simple, clean PWA icons for 旅韩助手."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUT = Path("/workspace/kbeauty-scan/static/icons")
OUT.mkdir(parents=True, exist_ok=True)
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

def gradient(size, c1=(0x14, 0x6E, 0x68), c2=(0x3D, 0x8F, 0x88)):
    img = Image.new("RGB", (size, size), c1)
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))
    return img

def font_for(size, char="旅"):
    # Noto Sans CJK SC is typically index 1 in this TTC
    for idx in (1, 0, 2, 3, 4):
        try:
            f = ImageFont.truetype(FONT, size=size, index=idx)
            # sanity: can get bbox
            f.getbbox(char)
            return f
        except Exception:
            continue
    return ImageFont.truetype("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", size=size)

def draw_char(img, char, scale, fill=(255, 255, 255)):
    w, h = img.size
    fsize = int(w * scale)
    font = font_for(fsize, char)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    bbox = font.getbbox(char)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (w - tw) // 2 - bbox[0]
    y = (h - th) // 2 - bbox[1] - int(h * 0.02)
    # soft shadow for contrast
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.text((x, y + int(h * 0.015)), char, font=font, fill=(0, 0, 0, 50))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=max(1, w // 80)))
    base = img.convert("RGBA")
    base = Image.alpha_composite(base, shadow)
    d.text((x, y), char, font=font, fill=fill + (255,))
    return Image.alpha_composite(base, layer)

def rounded_mask(size, radius_ratio=0.22):
    m = Image.new("L", (size, size), 0)
    r = int(size * radius_ratio)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=255)
    return m

def save_any(size, path):
    img = draw_char(gradient(size), "旅", 0.58)
    # rounded square with transparent corners
    rgba = img.convert("RGBA")
    rgba.putalpha(rounded_mask(size))
    rgba.save(path, "PNG", optimize=True)

def save_maskable(size, path):
    # full-bleed; glyph stays inside ~80% safe zone
    img = draw_char(gradient(size), "旅", 0.46)
    img.convert("RGB").save(path, "PNG", optimize=True)

def save_apple(size, path):
    img = draw_char(gradient(size), "旅", 0.58)
    img.convert("RGB").save(path, "PNG", optimize=True)

def main():
    save_any(192, OUT / "icon-192.png")
    save_any(512, OUT / "icon-512.png")
    save_maskable(192, OUT / "icon-192-maskable.png")
    save_maskable(512, OUT / "icon-512-maskable.png")
    save_apple(180, OUT / "apple-touch-icon.png")
    for p in sorted(OUT.iterdir()):
        print(p.name, p.stat().st_size, Image.open(p).size, Image.open(p).mode)

if __name__ == "__main__":
    main()
