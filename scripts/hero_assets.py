"""Build the landing page's "Hear the difference" block from the stored demo (no model, no network).

Run: uv run python scripts/hero_assets.py            (crop, text layer, MathML, reading)
     uv run --with fonttools --with brotli python scripts/hero_assets.py --font
                                                       (also the maths-font subset)

The hero shows one real equation of the demo, OpenStax Physics 2.3 (CC BY 4.0), page 3 of
`web/static/demo/source.pdf`: equation 2.4 as the PDF has it, beside the converted page's live
MathML, with the readings of both:

- `hero/before.png`: the equation and its number cut from the PDF page at 4x (PyMuPDF; the box is
  found from the words on the page, so no coordinates are written here; the page shows it at
  2.4 times the PDF's size);
- `hero/hero.json`: the PDF's text layer for the equation (what a screen reader gets from the
  untagged PDF), the stored MathML, its number and the reading `pipeline/speech.py` gives it;
- `vendor/fonts/lm-math-hero-1.959.woff2` (with `--font`): Latin Modern Math cut down to the
  glyphs of that MathML and renamed, as the GUST Font License asks for a derived font.

`tests/test_hero_assets.py` regenerates the crop, the JSON and the glyph list and compares them
with the committed files, so the hero cannot drift from the demo. The committed files are what
the site serves; the app never runs this.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "src" / "readablestem" / "web"
DEMO = WEB / "static" / "demo"
OUT = WEB / "static" / "hero"
FONT_DIR = WEB / "static" / "vendor" / "fonts"
FONT_SOURCE = FONT_DIR / "latinmodern-math.otf"
FONT_NAME = "lm-math-hero-1.959.woff2"
PAGE_INDEX = 2  # page 3 of source.pdf
SCALE = 4.0  # pixels per PDF point; the page shows the crop at 0.6 of this
PAD = 6.0  # points of paper around the equation's own ink

sys.path.insert(0, str(ROOT / "src"))


def passage(pdf: Path) -> tuple[pymupdf.Page, pymupdf.Rect, pymupdf.Rect]:
    """The page, the box of the equation and its number alone (found from the words, with a
    little paper round them), and the wider band the text layer is read from."""
    page = pymupdf.open(pdf)[PAGE_INDEX]
    line = page.search_for("position, (i.e., displacement)")[0]
    label = page.search_for("2.4")[0]
    start = page.search_for("the change in time.")[0]
    end = page.search_for("This relationship can also be written")[0]
    # The line under the lead-in sentence: the equation and its label.
    band = pymupdf.Rect(start.x0 - 1, line.y1 + 2, end.x1 + 12, label.y1 + 2)
    words = page.get_text("words", clip=band)
    ink = pymupdf.Rect(
        min(w[0] for w in words),
        min(w[1] for w in words),
        max(w[2] for w in words),
        max(w[3] for w in words),
    )
    box = pymupdf.Rect(ink.x0 - PAD, ink.y0 - 1, ink.x1 + PAD, ink.y1 + 3)
    return page, box, band


def build_crop(pdf: Path) -> bytes:
    page, box, _ = passage(pdf)
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(SCALE, SCALE), clip=box, alpha=False)
    return pixmap.tobytes("png")


def text_layer(pdf: Path) -> str:
    """The equation's text as the PDF stores it, lines joined by a space, as a reader
    that has only the text layer would get it."""
    page, _, equation = passage(pdf)
    lines = [ln.strip() for ln in page.get_text("text", clip=equation).splitlines()]
    return " ".join(ln for ln in lines if ln)


def demo_fragments(demo_html: str) -> dict[str, str]:
    """Equation 2.4's MathML and its number, from the stored demo."""
    equation = re.search(
        r'<div class="equation equation-labelled" id="p3-e5">(.*?)</div>', demo_html, re.S
    )
    assert equation, "the demo changed shape"
    mathml = re.search(r"<math.*?</math>", equation.group(1), re.S)
    label = re.search(
        r"<span class=\"equation-label\">.*?</span>\s*([0-9.]+)</span>", equation.group(1), re.S
    )
    assert mathml and label
    return {"mathml": mathml.group(0), "label": label.group(1)}


def codepoints(*texts: str) -> list[int]:
    return sorted({ord(c) for text in texts for c in text if not c.isspace()})


def build_json(pdf: Path, demo_html: str, crop: bytes) -> dict:
    from readablestem.pipeline.speech import mathml_to_speech

    parts = demo_fragments(demo_html)
    math_text = html.unescape(re.sub(r"<[^>]+>", "", parts["mathml"]))
    font_path = FONT_DIR / FONT_NAME
    return {
        "source": "OpenStax Physics 2.3 Position vs. Time Graphs, page 3 of 13, CC BY 4.0",
        "pdf_tagged": "no",
        "crop_sha256": hashlib.sha256(crop).hexdigest(),
        "text_layer": text_layer(pdf),
        "mathml": parts["mathml"],
        "label": parts["label"],
        "speech": mathml_to_speech(parts["mathml"]),
        "glyphs": codepoints(math_text, "0123456789.,()=+-"),
        "font": FONT_NAME,
        "font_sha256": hashlib.sha256(font_path.read_bytes()).hexdigest()
        if font_path.exists()
        else "",
    }


def build_font(glyphs: list[int]) -> bytes:
    """Latin Modern Math cut down to `glyphs`, renamed (the GUST Font License asks that a
    derived font not keep the name), as WOFF2. Needs fonttools and brotli."""
    from fontTools import subset
    from fontTools.ttLib import TTFont

    options = subset.Options()
    options.layout_features = ["*"]
    options.flavor = "woff2"
    options.notdef_outline = True
    options.name_IDs = ["*"]
    font = TTFont(FONT_SOURCE, recalcTimestamp=False)
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=glyphs)
    subsetter.subset(font)
    for record in font["name"].names:
        if record.nameID in (1, 4, 16):
            record.string = "LM Math Hero"
        elif record.nameID == 6:
            record.string = "LMMathHero"
    font.flavor = "woff2"
    from io import BytesIO

    buffer = BytesIO()
    font.save(buffer)
    return buffer.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--font", action="store_true", help="also write the maths-font subset")
    args = parser.parse_args()
    pdf = DEMO / "source.pdf"
    demo_html = (DEMO / "index.html").read_text(encoding="utf-8")
    OUT.mkdir(exist_ok=True)
    crop = build_crop(pdf)
    (OUT / "before.png").write_bytes(crop)
    data = build_json(pdf, demo_html, crop)
    if args.font:
        packed = build_font(data["glyphs"])
        (FONT_DIR / FONT_NAME).write_bytes(packed)
        data["font_sha256"] = hashlib.sha256(packed).hexdigest()
    (OUT / "hero.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT / 'before.png'} and {OUT / 'hero.json'}")
    print("speech:", data["speech"])


if __name__ == "__main__":
    main()
