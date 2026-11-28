"""生成仓库内置的中文字体子集（二维码 PNG 用，服务器没有系统中文字体）。

    python scripts/make_cjk_font_subset.py

来源：Noto Sans CJK SC（SIL OFL 1.1，许可证见 fonts/OFL.txt），需要本机装有 fonts-noto-cjk。
字集：ASCII、常用标点/全角符号、GB2312 一级常用汉字（3755）。生僻字在二维码图片里会缺字。不含韩文（体积太大），昵称里的韩文在二维码图片里会缺字。
"""
from __future__ import annotations

import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTCollection

ROOT = Path(__file__).resolve().parents[1]
SRC = Path("/usr/share/fonts/opentype/noto")
OUT = ROOT / "fonts"


def charset() -> str:
    s = {chr(c) for c in range(0x20, 0x7F)}
    for lo, hi in ((0x00A0, 0x00FF), (0x2010, 0x206F), (0x2190, 0x21FF), (0x2460, 0x24FF), (0x25A0, 0x25FF),
                   (0x2600, 0x26FF), (0x2700, 0x27BF), (0x3000, 0x303F), (0xFF00, 0xFFEF)):
        s.update(chr(c) for c in range(lo, hi + 1))
    for hi in range(0xB0, 0xD8):            # GB2312 一级汉字（3755 个常用字）
        for lo in range(0xA1, 0xFF):
            try:
                s.add(bytes([hi, lo]).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    return "".join(sorted(s))


def main() -> int:
    text = charset()
    OUT.mkdir(exist_ok=True)
    for weight in ("Regular", "Bold"):
        ttc = TTCollection(str(SRC / ("NotoSansCJK-%s.ttc" % weight)))
        font = next(f for f in ttc.fonts if f["name"].getDebugName(1) == "Noto Sans CJK SC")
        opts = subset.Options()
        opts.layout_features = ["kern", "vert", "locl"]
        opts.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14]
        opts.notdef_outline = True
        opts.hinting = False
        opts.desubroutinize = True
        sub = subset.Subsetter(opts)
        sub.populate(text=text)
        sub.subset(font)
        dst = OUT / ("NotoSansCJKsc-%s-subset.otf" % weight)
        font.flavor = None
        font.save(str(dst))
        print(dst.name, dst.stat().st_size // 1024, "KB,", len(text), "chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
