"""The landing hero is built from the stored demo by scripts/hero_assets.py: regenerating the
crop, the text layer, the MathML and the reading gives the committed files, so the hero cannot
drift from the demo or from pipeline/speech.py."""

from __future__ import annotations

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


def test_text_layer_mathml_and_reading_are_regenerated_equal(rebuilt):
    _, data = rebuilt
    for key in ("text_layer", "mathml", "label", "speech", "glyphs"):
        assert data[key] == COMMITTED[key], key
