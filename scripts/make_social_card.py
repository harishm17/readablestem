"""Draw the 1200x630 link-preview image (og:image) at web/static/social-card.png.

Run: uv run python scripts/make_social_card.py [--fonts DIR]

It needs the Noto Sans and Noto Sans Math fonts (Debian/Ubuntu: fonts-noto-core,
fonts-noto-extra). The image is committed, so the app never runs this.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "readablestem" / "web" / "static" / "social-card.png"

W, H = 1200, 630
INK = (17, 24, 39)  # near-black text on white: contrast well above 7:1
MUTED = (55, 65, 81)
ACCENT = (30, 64, 175)  # the site's link blue
PANEL = (243, 244, 246)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fonts", default="/usr/share/fonts/truetype/noto")
    fonts = Path(parser.parse_args().fonts)

    def font(name: str, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(fonts / name), size)

    bold, regular, maths = "NotoSans-Bold.ttf", "NotoSans-Regular.ttf", "NotoSansMath-Regular.ttf"
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, 14], fill=ACCENT)
    d.text((72, 64), "ReadableSTEM", font=font(bold, 76), fill=INK)
    d.text((72, 160), "Accessible HTML from STEM course PDFs", font=font(regular, 40), fill=MUTED)

    # The example: an equation as the PDF prints it, and what the web page holds instead.
    d.rounded_rectangle([72, 250, W - 72, 500], radius=18, fill=PANEL)
    label = font(bold, 26)
    d.text((112, 278), "In the PDF", font=label, fill=MUTED)
    d.text((620, 278), "In the web page", font=label, fill=MUTED)

    m = font(maths, 54)
    d.text((112, 360), "\U0001d463 =", font=m, fill=INK)
    left, bar_y = 222, 392
    d.text((left + 8, 318), "Δ\U0001d465", font=m, fill=INK)
    d.line([(left, bar_y), (left + 92, bar_y)], fill=INK, width=4)
    d.text((left + 12, 398), "Δ\U0001d461", font=m, fill=INK)

    body = font(regular, 34)
    d.text((620, 330), "Real maths (MathML) that a", font=body, fill=INK)
    d.text((620, 376), "screen reader can read,", font=body, fill=INK)
    d.text((620, 422), "not a picture of it", font=body, fill=INK)
    d.line([(580, 300), (580, 470)], fill=(209, 213, 219), width=3)

    d.text(
        (72, 540),
        "readablestem.com  ·  free and open source  ·  instructor reviews before sharing",
        font=font(regular, 28),
        fill=MUTED,
    )
    img.save(OUT, optimize=True)
    print(OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
