"""Draw every icon of the site from one SVG (no model, no network, nothing downloaded).

Run: uv run python scripts/brand_icons.py          (writes the files below)
     uv run python scripts/brand_icons.py --check  (exits 1 when a committed file differs)

The mark is the header's: an outlined page with three lines of text. It is drawn once, in the
accent color, in `web/static/icon.svg` (the SVG icon; the file also carries the dark-mode accent).
The script renders that file with PyMuPDF and writes, into `web/static/`:

- `favicon.ico`: 16, 32 and 48 px, the mark alone on a transparent ground;
- `icon-192.png`, `icon-512.png` (the web manifest) and `apple-touch-icon.png` (180 px): the mark
  on the page background, opaque (iOS fills a transparent ground with black);
- `social-card.png`: 1200 by 630, the mark, the name and the equation block, set in the site's own
  fonts (Source Sans 3, and Latin Modern Math for the equation, both under `static/vendor/fonts`).

Colors come from `static/tokens.css` (the light values), so a token change and a rerun keep the
files in step; the script refuses to run when the SVG's color is not the `--accent` token.
`tests/test_hero_assets.py` rebuilds everything and compares it with the committed files. The
header's inline glyph (`base.html`, `500.html`, `503.html`, `site/templates/base.html`) is the same
shapes as `icon.svg`, in the text color.
"""

from __future__ import annotations

import argparse
import io
import re
import struct
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "readablestem" / "web" / "static"
FONTS = STATIC / "vendor" / "fonts"
SANS = FONTS / "source-sans-3-5.3.1-latin-{weight}.woff2"
MATH = FONTS / "latinmodern-math.otf"

ICO_SIZES = (16, 32, 48)
TOUCH = {"apple-touch-icon.png": 180, "icon-192.png": 192, "icon-512.png": 512}
MARK_SHARE = 0.62  # the mark's width as a share of an opaque icon (room for a round mask)
CARD = (1200, 630)


def tokens() -> dict[str, str]:
    """The light-mode custom properties of `tokens.css` (the first `:root` block)."""
    css = (STATIC / "tokens.css").read_text(encoding="utf-8")
    root = css[css.index(":root {") :]
    root = root[: root.index("\n}")]
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", root))


def rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def render_mark(side: int) -> Image.Image:
    """The SVG icon at `side` pixels square, with a transparent ground."""
    page = pymupdf.open(STATIC / "icon.svg")[0]
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(side / page.rect.width, side / page.rect.height))
    return Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGBA")


def png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def ico() -> bytes:
    """An ICO holding one PNG frame per size, each drawn from the SVG at that size (Pillow's own
    writer scales one image down, which blurs the 16 px frame)."""
    frames = [png(render_mark(size)) for size in ICO_SIZES]
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = len(header) + 16 * len(frames)
    entries = b""
    for size, data in zip(ICO_SIZES, frames, strict=True):
        entries += struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return header + entries + b"".join(frames)


def touch_icon(side: int, ground: str) -> Image.Image:
    image = Image.new("RGBA", (side, side), (*rgb(ground), 255))
    mark = render_mark(round(side * MARK_SHARE))
    offset = (side - mark.width) // 2
    image.alpha_composite(mark, (offset, offset))
    return image.convert("RGB")


def font(weight: int, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(Path(str(SANS).format(weight=weight))), size)


def social_card(color: dict[str, str]) -> Image.Image:
    width = CARD[0]
    ground, ink, muted = rgb(color["--bg"]), rgb(color["--ink"]), rgb(color["--ink-muted"])
    image = Image.new("RGBA", CARD, (*ground, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 16), fill=rgb(color["--accent"]))

    left = 88
    image.alpha_composite(render_mark(88), (left, 76))
    draw.text((left + 88 + 28, 118), "ReadableSTEM", font=font(700, 84), fill=ink, anchor="lm")
    draw.text(
        (left, 218),
        "Accessible HTML from STEM course PDFs",
        font=font(400, 44),
        fill=muted,
        anchor="lm",
    )

    panel = (left, 270, width - left, 500)
    draw.rounded_rectangle(panel, radius=16, fill=rgb(color["--surface"]))
    middle = 560
    draw.line(
        (middle, panel[1] + 28, middle, panel[3] - 28), fill=rgb(color["--border-subtle"]), width=2
    )
    label = font(700, 28)
    draw.text((left + 36, 318), "In the PDF", font=label, fill=muted, anchor="lm")
    draw.text((middle + 44, 318), "In the web page", font=label, fill=muted, anchor="lm")

    # The equation v = Δx / Δt in Latin Modern Math (math italic letters U+1D463, U+1D465, U+1D461).
    math = ImageFont.truetype(str(MATH), 72)
    baseline, gap = 440, 28
    lead = "\U0001d463 ="
    draw.text((left + 36, baseline), lead, font=math, fill=ink, anchor="ls")
    top, bottom = "Δ\U0001d465", "Δ\U0001d461"
    fraction_x = left + 36 + int(math.getlength(lead)) + gap
    fraction_w = int(max(math.getlength(top), math.getlength(bottom))) + 24
    centre = fraction_x + fraction_w / 2
    bar = baseline - 24
    draw.text((centre, bar - 14), top, font=math, fill=ink, anchor="ms")
    draw.text((centre, bar + 62), bottom, font=math, fill=ink, anchor="ms")
    draw.line((fraction_x, bar, fraction_x + fraction_w, bar), fill=ink, width=3)

    body = font(400, 38)
    for number, line in enumerate(
        ("Real maths (MathML) that a", "screen reader can read,", "not a picture of it")
    ):
        draw.text((middle + 44, 376 + 46 * number), line, font=body, fill=ink, anchor="lm")

    draw.text(
        (left, 566),
        "readablestem.com  ·  free and open source  ·  instructor reviews before sharing",
        font=font(400, 30),
        fill=muted,
        anchor="lm",
    )
    return image.convert("RGB")


def build() -> dict[str, bytes]:
    """File name (under `web/static/`) → the bytes the script writes."""
    color = tokens()
    svg = (STATIC / "icon.svg").read_text(encoding="utf-8")
    if color["--accent"] not in svg:
        sys.exit(f"icon.svg does not use the --accent token {color['--accent']}")
    files = {"favicon.ico": ico(), "social-card.png": png(social_card(color))}
    for name, side in TOUCH.items():
        files[name] = png(touch_icon(side, color["--bg"]))
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--check", action="store_true", help="compare, write nothing")
    args = parser.parse_args()
    differing = []
    for name, data in build().items():
        path = STATIC / name
        if args.check:
            if not path.exists() or path.read_bytes() != data:
                differing.append(name)
        else:
            path.write_bytes(data)
            print(f"wrote {path.relative_to(ROOT)} ({len(data)} bytes)")
    if differing:
        sys.exit("differs from the script's output: " + ", ".join(differing))


if __name__ == "__main__":
    main()
