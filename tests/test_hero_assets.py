"""The landing hero is built from the stored demo by scripts/hero_assets.py: regenerating the
crop, the text layer, the MathML and the reading gives the committed files, so the hero cannot
drift from the demo or from pipeline/speech.py. The same goes for the icons and the social card,
which scripts/brand_icons.py draws from static/icon.svg."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest
from PIL import Image, ImageChops

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


def test_text_layer_mathml_and_reading_are_regenerated_equal(rebuilt):
    _, data = rebuilt
    for key in ("text_layer", "mathml", "label", "speech", "glyphs"):
        assert data[key] == COMMITTED[key], key


def test_the_icons_and_the_social_card_are_the_scripts_output():
    """Each committed file equals what the script draws now. PNG and ICO frames are compared as
    decoded pixels, not bytes: the compressor's bytes depend on the zlib in use."""
    spec = importlib.util.spec_from_file_location(
        "brand_icons", ROOT / "scripts" / "brand_icons.py"
    )
    assert spec and spec.loader
    brand_icons = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(brand_icons)
    static = WEB / "static"
    files = brand_icons.build()
    assert set(files) == {"favicon.ico", *brand_icons.TOUCH, "social-card.png"}
    for name, data in files.items():
        drawn = Image.open(io.BytesIO(data))
        committed = Image.open(static / name)
        if name.endswith(".ico"):
            assert drawn.ico.sizes() == committed.ico.sizes() == {(s, s) for s in (16, 32, 48)}
            frames = [
                (drawn.ico.getimage(s), committed.ico.getimage(s))
                for s in sorted(drawn.ico.sizes())
            ]
        else:
            frames = [(drawn, committed)]
        for now, then in frames:
            assert now.size == then.size, name
            assert (
                ImageChops.difference(now.convert("RGBA"), then.convert("RGBA")).getbbox() is None
            ), name
    assert Image.open(static / "apple-touch-icon.png").getpixel((0, 0))[3:] in (
        (),
        (255,),
    )  # opaque
