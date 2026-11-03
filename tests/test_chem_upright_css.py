"""An all-chemistry equation has no style on its element symbols (`intent` is on `<math>`), and
every place MathML is shown draws them upright with `math[intent] mi { text-transform: none }`:
the student file (`assemble.py`) and the review screen (`web/static/app.css`)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from readablestem.pipeline.chem import ce_to_latex
from readablestem.pipeline.validate import safe_latex_to_mathml

APP_CSS = Path(__file__).resolve().parents[1] / "src/readablestem/web/static/app.css"
RULE = "math[intent] mi { text-transform: none; }"


def test_the_rule_is_in_the_review_stylesheet_and_the_student_file() -> None:
    from readablestem.pipeline import assemble

    assert RULE in APP_CSS.read_text(encoding="utf-8")
    assert RULE in Path(assemble.__file__).read_text(encoding="utf-8")


def _shot(mathml: str, css: str) -> str:
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except sync_api.Error:
            pytest.skip("Playwright's Chromium is not installed")
        try:
            tab = browser.new_page(viewport={"width": 400, "height": 200})
            tab.set_content(f"<style>{css} math {{ font-size: 64px }}</style><p>{mathml}</p>")
            png = tab.locator("math").screenshot()
        finally:
            browser.close()
    return hashlib.md5(png).hexdigest()


@pytest.mark.browser
def test_chromium_draws_intent_symbols_upright_with_the_review_stylesheet() -> None:
    water = safe_latex_to_mathml(ce_to_latex("H2O"), "inline")
    assert 'intent=":chemical-formula"' in water and "mathvariant" not in water
    upright = water.replace("<mi>", '<mi mathvariant="normal">').replace(
        ' intent=":chemical-formula"', ""
    )
    css = APP_CSS.read_text(encoding="utf-8")
    plain = water.replace(' intent=":chemical-formula"', "")
    # with the rule: the same picture as an explicit upright style; without it: italic
    assert _shot(water, css) == _shot(upright, css)
    assert _shot(plain, css) != _shot(upright, "")
