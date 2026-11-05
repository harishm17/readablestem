"""The landing hero is built from the stored demo by scripts/hero_assets.py: regenerating the
crop, the text layer, the MathML and the reading gives the committed files, so the hero cannot
drift from the demo or from pipeline/speech.py."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "readablestem" / "web"
HERO = WEB / "static" / "hero"

spec = importlib.util.spec_from_file_location("hero_assets", ROOT / "scripts" / "hero_assets.py")
assert spec and spec.loader
hero_assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hero_assets)

COMMITTED = json.loads((HERO / "hero.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rebuilt() -> tuple[bytes, dict]:
    pdf = hero_assets.DEMO / "source.pdf"
    demo_html = (hero_assets.DEMO / "index.html").read_text(encoding="utf-8")
    crop = hero_assets.build_crop(pdf)
    return crop, hero_assets.build_json(pdf, demo_html, crop)


def test_the_crop_is_the_committed_image(rebuilt):
    crop, _ = rebuilt
    # PyMuPDF is pinned (pyproject.toml), so the same page renders to the same bytes.
    assert (HERO / "before.png").read_bytes() == crop
    assert hashlib.sha256(crop).hexdigest() == COMMITTED["crop_sha256"]


def test_text_layer_mathml_and_reading_are_regenerated_equal(rebuilt):
    _, data = rebuilt
    for key in ("text_layer", "mathml", "label", "speech", "glyphs"):
        assert data[key] == COMMITTED[key], key


def test_the_hero_says_what_the_demo_and_speech_say():
    assert COMMITTED["speech"] == "capital delta bold d over capital delta t"
    assert "Δ" in COMMITTED["text_layer"] and "fraction" not in COMMITTED["text_layer"]
    assert COMMITTED["pdf_tagged"] == "no"
    import pymupdf

    doc = pymupdf.open(hero_assets.DEMO / "source.pdf")
    assert doc.xref_get_key(doc.pdf_catalog(), "StructTreeRoot")[0] == "null"  # untagged


def test_the_font_subset_is_the_committed_file_and_covers_the_mathml():
    path = hero_assets.FONT_DIR / COMMITTED["font"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == COMMITTED["font_sha256"]
    assert path.stat().st_size < 12_000  # the full font is 392 KB
    assert (
        0x1D41D in COMMITTED["glyphs"]
    )  # the bold d, as a code point (Chromium ignores mathvariant)
    css = (WEB / "static" / "app.css").read_text(encoding="utf-8")
    assert f"/static/vendor/fonts/{COMMITTED['font']}" in css


def test_the_crop_is_the_equation_and_its_number_alone(rebuilt):
    """A real crop of the PDF page (not a redraw) that is only the equation: small enough to
    show at its own size on a 320px phone, with no line of text in it."""
    import pymupdf

    crop, _ = rebuilt
    page, box, _ = hero_assets.passage(hero_assets.DEMO / "source.pdf")
    assert box.width < 100 and box.height < 50  # points; a sentence would be 400 wide
    prose = page.get_text("words", clip=box)
    assert [w[4] for w in prose] == ["Δ𝐝", "Δ𝑡.", "2.4"]  # the equation's words, no sentence
    pixmap = pymupdf.Pixmap(crop)
    assert abs(pixmap.width - box.width * hero_assets.SCALE) < 2  # the whole box, nothing more
    shown = pixmap.width * 3 // 5  # the width the landing page gives the image (routes/pages.py)
    assert shown <= 14 * 16 - 2 * 12 - 2  # inside the 14rem sheet, its padding and border
