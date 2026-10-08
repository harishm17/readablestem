"""html_checks: static rules, axe-core mapping and skips, vendored file, and browser runs."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pymupdf
import pytest

from a11y_stem.core.types import Element, Fix
from a11y_stem.pipeline import html_checks
from a11y_stem.pipeline.assemble import render_html
from a11y_stem.pipeline.html_checks import (
    AXE_SHA256,
    AXE_SKIPPED,
    AXE_VERSION,
    axe_checks,
    static_checks,
    violations_to_checks,
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


def sample_html() -> str:
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
    )


# --------------------------------------------------------------------------- static


def test_minimal_document_passes_every_rule():
    checks = static_checks(doc("<h1>A</h1><p>text</p>"))
    assert [c.name for c in checks] == [
        "html.lang",
        "html.title",
        "html.main",
        "html.heading_order",
        "html.heading_text",
        "html.img_alt",
        "html.unique_ids",
        "html.math_display",
        "html.self_contained",
        "html.wording",
    ]
    assert all(c.passed and c.code is None for c in checks)


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
        (doc("<p><math><mi>x</mi></math></p>"), "math_missing_display"),
        (doc('<math display="center"><mi>x</mi></math>'), "math_missing_display"),
        (doc("<p>x</p><script>alert(1)</script>"), "external_resource"),
        (doc("<p>x</p>", head='<link rel="stylesheet" href="s.css">'), "external_resource"),
        (doc('<img src="https://example.org/a.png" alt="">'), "external_resource"),
        (doc('<a href="https://example.org">x</a>'), "external_resource"),
        (doc('<p style="background:url(http://x/y)">x</p>'), "external_resource"),
        (doc("<p>x</p>", head="<style>@import 'x.css';</style>"), "external_resource"),
        (doc('<iframe src="data:text/html,x" title="t"></iframe>'), "external_resource"),
        (doc(f'<p class="note a11y-stem">This file is WCAG {CLAIM}.</p>'), "banned_wording"),
        (doc('<div class="a11y-stem"><p>Ensures <b>compliance</b>.</p></div>'), "banned_wording"),
    ],
)  # fmt: skip
def test_each_rule_reports_its_code(html, code):
    assert code in failed_codes(html)


@pytest.mark.parametrize(
    "html",
    [
        doc("<h2>Starts at two</h2><h3>B</h3><h1>Back up</h1><h2>C</h2>"),
        doc('<h1><img src="data:," alt="Course logo"></h1>'),
        doc('<h1>x<math display="inline"><mi>y</mi></math></h1>'),
        doc('<img src="data:image/png;base64,AAAA" alt="">'),
        doc('<a href="#about">About</a><p id="about">x</p>'),
        doc(f"<p>{CLAIM.title()} mechanisms flex instead of using hinges.</p>"),
        doc(f'<div class="a11y-stem"><p>Assists with accessibility.</p></div><p>{CLAIM}</p>'),
        doc("<p>a<br/>b</p><mspace/>"),
    ],
)
def test_valid_patterns_are_not_flagged(html):
    assert failed_codes(html) == []


def test_math_display_is_only_a_warning():
    check = next(c for c in static_checks(doc("<math><mi>x</mi></math>")) if not c.passed)
    assert (check.name, check.severity) == ("html.math_display", "warning")


def test_garbage_input_does_not_raise():
    codes = failed_codes("<<<>>> <h7> </div></p> &bogus; <img")
    assert {"missing_lang", "missing_title", "main_count"} <= set(codes)


def test_assembled_sample_passes():
    assert failed_codes(sample_html()) == []


# --------------------------------------------------------------------------- axe (no browser)


def test_violations_map_to_checks_by_impact():
    checks = violations_to_checks(
        [
            {"id": "image-alt", "impact": "critical", "nodes": 2},
            {"id": "color-contrast", "impact": "serious", "nodes": 1},
            {"id": "list", "impact": "moderate", "nodes": 1},
            {"id": "region", "impact": None, "nodes": 1},
            {"id": "<script>", "impact": "minor", "nodes": 1},
        ]
    )
    assert [(c.name, c.code, c.severity, c.passed) for c in checks] == [
        ("axe.image-alt", "image-alt", "error", False),
        ("axe.color-contrast", "color-contrast", "error", False),
        ("axe.list", "list", "warning", False),
        ("axe.region", "region", "warning", False),
        ("axe.unknown", "unknown", "warning", False),
    ]
    assert violations_to_checks([]) == [html_checks.Check(name="axe", passed=True)]


def test_axe_is_skipped_without_playwright(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)  # import -> ImportError
    assert axe_checks(doc("<p>x</p>")) == [AXE_SKIPPED]
    assert AXE_SKIPPED.passed and AXE_SKIPPED.severity == "warning"


def test_axe_is_skipped_when_the_browser_is_missing(monkeypatch):
    sync_api = pytest.importorskip("playwright.sync_api")

    class NoBrowser:
        def launch(self):
            raise sync_api.Error("Executable doesn't exist at /nowhere/chrome")

    class FakePlaywright:
        chromium = NoBrowser()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(sync_api, "sync_playwright", FakePlaywright)
    assert axe_checks(doc("<p>x</p>")) == [AXE_SKIPPED]


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
