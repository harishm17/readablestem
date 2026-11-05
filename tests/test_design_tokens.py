"""The design tokens (web/static/tokens.css, DESIGN.md): every text colour pair is at least
7:1 in light and in dark, borders on controls at least 3:1, nothing is set below 14px, the
fonts are self-hosted and under the budget, and /style is a noindex working page."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fixtures.web.support import WebEnv

STATIC = Path(__file__).resolve().parents[1] / "src" / "readablestem" / "web" / "static"
TOKENS = (STATIC / "tokens.css").read_text(encoding="utf-8")
APP_CSS = (STATIC / "app.css").read_text(encoding="utf-8")


def block(css: str, opener: str) -> dict[str, str]:
    start = css.index(opener)
    body = css[css.index("{", start) + 1 :]
    depth, end = 1, 0
    end = 0
    for end, char in enumerate(body):  # noqa: B007 (end is read after the loop)
        depth += (char == "{") - (char == "}")
        if depth == 0:
            break
    return dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b", body[:end]))


LIGHT = block(TOKENS, ":root {")
DARK = {**LIGHT, **block(TOKENS, "@media (prefers-color-scheme: dark)")}


def luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


TEXT_ON = {
    "--ink": ["--bg", "--surface", "--surface-2", "--accent-tint", "--desk"],
    "--ink-muted": ["--bg", "--surface", "--surface-2", "--desk"],
    "--accent": ["--bg", "--surface", "--surface-2", "--accent-tint"],
    "--accent-hover": ["--bg", "--surface", "--accent-tint"],
    "--visited": ["--bg", "--surface"],
    "--ok": ["--bg", "--ok-tint", "--surface"],
    "--warn": ["--bg", "--warn-tint", "--surface"],
    "--err": ["--bg", "--err-tint", "--surface"],
    "--mark-optional": ["--bg", "--surface", "--desk"],
}


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_colours_are_at_least_7_to_1(theme):
    tokens = LIGHT if theme == "light" else DARK
    # --mark-optional is a literal in both; --warn and --ok feed --mark-required/accepted.
    failures = []
    for fg, backgrounds in TEXT_ON.items():
        if fg not in tokens:
            continue
        for bg in backgrounds:
            if bg in tokens and ratio(tokens[fg], tokens[bg]) < 7.0:
                failures.append((fg, bg, round(ratio(tokens[fg], tokens[bg]), 2)))
    assert not failures, failures


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_on_filled_controls_is_at_least_7_to_1(theme):
    tokens = LIGHT if theme == "light" else DARK
    assert ratio(tokens["--on-accent"], tokens["--accent"]) >= 7
    assert ratio(tokens["--on-accent"], tokens["--err"]) >= 7 if theme == "dark" else True
    assert ratio(tokens["--on-mark"], tokens["--warn"]) >= 7
    assert ratio(tokens["--on-mark"], tokens["--ok"]) >= 7
    assert ratio(tokens["--on-mark"], tokens["--mark-optional"]) >= 7 or theme == "light"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_control_borders_and_focus_meet_3_to_1(theme):
    tokens = LIGHT if theme == "light" else DARK
    for bg in ("--bg", "--surface"):
        assert ratio(tokens["--border-control"], tokens[bg]) >= 3
        assert ratio(tokens["--focus-outline"], tokens[bg]) >= 3


def test_nothing_is_set_below_14px():
    for css in (TOKENS, APP_CSS):
        for size in re.findall(r"font-size:\s*([0-9.]+)(px|rem)", css):
            value = float(size[0]) * (16 if size[1] == "rem" else 1)
            assert value >= 14, size
        for size in re.findall(r"font:[^;]*?\b([0-9.]+)(px|rem)\b/", css):
            value = float(size[0]) * (16 if size[1] == "rem" else 1)
            assert value >= 14, size


def test_fonts_are_self_hosted_and_under_budget():
    urls = re.findall(r'url\("([^"]+)"\)', TOKENS)
    assert urls, "tokens.css declares no font files"
    total = 0
    for url in urls:
        assert url.startswith("/static/vendor/fonts/"), url
        path = STATIC / url.removeprefix("/static/")
        assert path.is_file(), url
        total += path.stat().st_size
    assert total < 120_000, total
    assert "http" not in TOKENS.replace("https://", "x")  # no outside requests


def test_the_page_width_and_radii_follow_the_tokens():
    assert "--radius-control: 4px" in TOKENS and "--radius-frame: 8px" in TOKENS
    assert "--page-max: 75rem" in TOKENS and "--prose-max: 68ch" in TOKENS
    assert "999px" not in APP_CSS  # no pills


CONTROLS_CSS = (STATIC / "controls.css").read_text(encoding="utf-8")
LISTEN_CSS = (STATIC / "listen.css").read_text(encoding="utf-8")
PAPER_SCOPE = ".paper, .page-frame, .cmp-row, .cmp-none, .table-view {"


def test_layers_and_sticky_offsets_come_from_tokens():
    """One set of named layers (tokens.css) for the review screen, the tables, the phone bar
    and the listening player: no bare z-index number, and the stuck blocks share one gap."""
    for token in ("--z-raised", "--z-raised-top", "--z-sticky", "--z-fixed", "--z-skip"):
        assert re.search(rf"{token}:\s*\d+;", TOKENS), token
    assert "--sticky-offset:" in TOKENS and "--listen-sticky:" in TOKENS
    for name, css in (("app.css", APP_CSS), ("listen.css", LISTEN_CSS)):
        assert not re.findall(r"z-index:\s*-?\d", css), name
        assert re.findall(r"z-index:\s*var\(--z-", css), name
    # The two sticky bars use the same layer, and the review page column and decision bar the
    # same offset from the window edge; the player's scroll offset is the measured height.
    assert "z-index: var(--z-sticky)" in APP_CSS and "z-index: var(--z-sticky)" in LISTEN_CSS
    assert APP_CSS.count("var(--sticky-offset)") >= 3
    assert "scroll-margin-top: calc(var(--listen-sticky) + " in LISTEN_CSS


def test_paper_scopes_use_the_paper_tokens():
    """Light palette values on white paper are named once, in tokens.css; the landing page's
    sheet, the diagram's caption and the table header surface alias them."""
    scope = TOKENS[TOKENS.index(PAPER_SCOPE) :].split("}")[0]
    assert not re.findall(r"#[0-9a-fA-F]{3,6}\b", scope), scope
    assert "--ink-muted: var(--paper-ink-muted)" in scope and "--bg: var(--paper)" in scope
    for rule in re.findall(r"^\.sheet \{[^}]*\}", APP_CSS, re.M):
        assert not re.findall(r"#[0-9a-fA-F]{3,6}\b", rule), rule
        assert "var(--paper-ink-muted)" in rule
    figure = re.search(r"\.data-flow-figure figcaption \{[^}]*\}", APP_CSS)
    assert figure and "var(--paper-ink-muted)" in figure.group(0) and "#" not in figure.group(0)
    assert re.search(r"\.table-view \{ --surface: var\(--paper-surface\); \}", APP_CSS)


def test_each_component_has_its_rules_in_one_place():
    """No stylesheet block is named for the work that added it, and the components that used to
    be split (the figure, the player's items, the button forced colours, the delete outline)
    are not repeated."""
    for name, css in (("app.css", APP_CSS), ("listen.css", LISTEN_CSS)):
        assert not re.search(r"ux2?-lane|ux-[ab]\b|tier 1 web", css), name
    assert len(re.findall(r"^\.data-flow-figure \{", APP_CSS, re.M)) == 1
    assert len(re.findall(r"^\.listen-item \{", LISTEN_CSS, re.M)) == 1
    # Button colours in forced-colour modes are controls.css's; app.css does not repeat them.
    assert "button.button:hover" in CONTROLS_CSS and "button.button:hover" not in APP_CSS
    assert ".button-danger-outline" in CONTROLS_CSS
    assert not re.search(r"^\s*\.button-danger-outline[^{]*\{", APP_CSS, re.M)
    for selector in (".decision-notice", ".table-view", ".doc-file", ".group-state", ".parts-list"):
        rules = re.findall(rf"^{re.escape(selector)} \{{", APP_CSS, re.M)
        assert len(rules) <= (2 if selector == ".table-view" else 1), (selector, len(rules))


def test_style_page_is_noindex_and_not_in_the_sitemap(tmp_path):
    env = WebEnv(tmp_path)
    client = env.client()
    response = client.get("/style")
    assert response.status_code == 200
    assert '<meta name="robots" content="noindex">' in response.text
    assert 'id="main"' in response.text
    assert "/style" not in client.get("/sitemap.xml").text
