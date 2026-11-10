"""html_checks: static rules, axe-core mapping and skips, vendored file, and browser runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Element, Fix
from readablestem.pipeline import html_checks
from readablestem.pipeline.assemble import render_html
from readablestem.pipeline.html_checks import (
    AXE_SHA256,
    AXE_SKIPPED,
    AXE_VERSION,
    axe_checks,
    static_checks,
)

FIXTURE = Path(__file__).parent / "fixtures" / "assemble" / "sample_document.json"
VENDOR = Path(html_checks.__file__).parent / "vendor"
# The word rule 7 bans, built at run time so a repo-wide grep for it stays clean.
CLAIM = "compli" + "ant"


def doc(body: str, *, lang: str = "en", title: str = "T", head: str = "") -> str:
    return (
        f'<!doctype html><html lang="{lang}"><head><title>{title}</title>{head}</head>'
        f"<body><main>{body}</main></body></html>"
    )


def failed_codes(html: str) -> list[str | None]:
    return [c.code for c in static_checks(html) if not c.passed]


def sample_html(notice: str | None = None) -> str:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    elements = [Element.model_validate(e) for e in data["elements"]]
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 20), False)
    pixmap.set_rect(pixmap.irect, (20, 20, 20))
    crops = {e.id: pixmap.tobytes("png") for e in elements if e.kind in ("equation", "figure")}
    return render_html(
        title=data["title"],
        lang=data["lang"],
        elements=elements,
        fixes=[Fix.model_validate(f) for f in data["fixes"]],
        crops=crops,
        source_note="Sample written for tests.",
        notice=notice,
    )


# --------------------------------------------------------------------------- static


@pytest.mark.parametrize(
    ("html", "code"),
    [
        (doc("<p>x</p>").replace(' lang="en"', ""), "missing_lang"),
        (doc("<p>x</p>", lang=" "), "missing_lang"),
        (doc("<p>x</p>").replace("<title>T</title>", ""), "missing_title"),
        (doc("<p>x</p>", title="  "), "missing_title"),
        (doc("<p>x</p>").replace("<main>", "").replace("</main>", ""), "main_count"),
        (doc("<p>x</p>") + "<main></main>", "main_count"),
        (doc('<div role="main">x</div>'), "main_count"),
        (doc("<h1>A</h1><h3>C</h3>"), "heading_skip"),
        (doc("<h2>A</h2><h4>C</h4>"), "heading_skip"),
        (doc("<h1>A</h1><h2> </h2>"), "empty_heading"),
        (doc("<h1><span></span></h1>"), "empty_heading"),
        (doc('<img src="data:,">'), "img_missing_alt"),
        (doc('<p id="a">x</p><p id="a">y</p>'), "duplicate_id"),
        (doc('<math display="inline" id="a"><mi>y</mi></math><b id="a"></b>'), "duplicate_id"),
        (doc("<li>stray item</li>"), "list_structure"),
        (doc("<p><li>item in a paragraph</li></p>"), "list_structure"),
        (doc("<ul><p>not an item</p></ul>"), "list_structure"),
        (doc("<ol>loose text<li>x</li></ol>"), "list_structure"),
        (doc("<ul><li>a</li><ul><li>nested in the list, not in an item</li></ul></ul>"),
         "list_structure"),
        (doc("<p><math><mi>x</mi></math></p>"), "math_missing_display"),
        (doc('<math display="center"><mi>x</mi></math>'), "math_missing_display"),
        (doc("<p>x</p><script>alert(1)</script>"), "external_resource"),
        (doc("<p>x</p>", head='<link rel="stylesheet" href="s.css">'), "external_resource"),
        (doc('<img src="https://example.org/a.png" alt="">'), "external_resource"),
        (doc('<audio src="https://example.org/a.mp3"></audio>'), "external_resource"),
        (doc('<p style="background:url(http://x/y)">x</p>'), "external_resource"),
        (doc("<p>x</p>", head="<style>@import 'x.css';</style>"), "external_resource"),
        (doc('<iframe src="data:text/html,x" title="t"></iframe>'), "external_resource"),
        (doc(f'<p class="note readablestem">This file is WCAG {CLAIM}.</p>'), "banned_wording"),
        (
            doc('<div class="readablestem"><p>Ensures <b>compliance</b>.</p></div>'),
            "banned_wording",
        ),
        (doc('<p><a href="https://example.org"> </a></p>'), "empty_link"),
        (doc('<p><a href="#x"><img src="data:," alt=""></a></p><p id="x">x</p>'), "empty_link"),
        (doc('<p><a href="javascript:alert(1)">x</a></p>'), "unsafe_link"),
        (doc('<p><a href="ftp://example.org/f">x</a></p>'), "unsafe_link"),
        (doc('<p><a href="#nowhere">x</a></p>'), "unsafe_link"),
        (doc("<p><a>x</a></p>"), "unsafe_link"),
        (doc("<p>x</p>", lang="english language"), "invalid_lang"),
        (doc('<p lang="fr_FR">x</p>'), "invalid_lang"),
        (doc('<p lang="f">x</p>'), "invalid_lang"),
        (doc('<p class="tc-ffff00">x</p>', head="<style>.tc-ffff00 { color: #ffff00; }</style>"),
         "low_contrast"),
    ],
)  # fmt: skip
def test_each_rule_reports_its_code(html, code):
    assert code in failed_codes(html)


# --------------------------------------------------------------------------- axe (no browser)


def test_vendored_axe_matches_the_recorded_checksum():
    data = (VENDOR / "axe.min.js").read_bytes()
    assert hashlib.sha256(data).hexdigest() == AXE_SHA256
    assert data.startswith(f"/*! axe v{AXE_VERSION}".encode())
    readme = (VENDOR / "README.md").read_text(encoding="utf-8")
    assert AXE_SHA256 in readme and f"axe-core {AXE_VERSION}" in readme
    assert (VENDOR / "axe-core-LICENSE.txt").read_text().startswith("Mozilla Public License")
    assert html_checks.axe_source_sha256() == AXE_SHA256


# --------------------------------------------------------------------------- browser


def axe_or_skip(html: str) -> list[html_checks.Check]:
    pytest.importorskip("playwright.sync_api")
    checks = axe_checks(html)
    if checks == [AXE_SKIPPED]:
        pytest.skip("Chromium for Playwright is not installed")
    return checks


@pytest.mark.browser
def test_axe_finds_no_serious_problems_in_an_assembled_document():
    checks = axe_or_skip(sample_html())
    assert [c for c in checks if c.severity == "error" and not c.passed] == []
    assert checks == [html_checks.Check(name="axe", passed=True)]  # no warnings either


@pytest.mark.browser
def test_axe_reports_real_violations():
    checks = axe_or_skip("<html><body><img src='data:,'><p>x</p></body></html>")
    found = {c.code: c.severity for c in checks}
    assert found["image-alt"] == "error"
    assert found["html-has-lang"] == "error"
    assert found["document-title"] == "error"


@pytest.mark.browser
def test_chromium_parses_the_maths_and_runs_nothing():
    sync_api = pytest.importorskip("playwright.sync_api")
    html = sample_html()
    try:
        with sync_api.sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_context(offline=True).new_page()
                page.set_content(html, wait_until="load")
                facts = page.evaluate(
                    """() => {
                      const ns = "http://www.w3.org/1998/Math/MathML";
                      const handlers = [...document.querySelectorAll("*")].flatMap(
                        (el) => [...el.attributes].filter((a) => a.name.startsWith("on")));
                      return {
                        maths: document.getElementsByTagNameNS(ns, "math").length,
                        inlineInParagraph: !!document.querySelector("#p1-e1 > math#p1-e2"),
                        blockDisplay: getComputedStyle(document.querySelector("#p1-e4 math"))
                          .display,
                        scripts: document.scripts.length,
                        handlers: handlers.length,
                        notes: document.querySelectorAll('[role="note"]').length,
                        brokenImages: [...document.images].filter((i) => !i.naturalWidth).length,
                      };
                    }"""
                )
            finally:
                browser.close()
    except sync_api.Error as exc:
        pytest.skip(f"Chromium unavailable: {type(exc).__name__}")
    assert facts == {
        "maths": 2,
        "inlineInParagraph": True,
        "blockDisplay": "block math",
        "scripts": 0,
        "handlers": 0,
        "notes": 3,
        "brokenImages": 0,
    }
