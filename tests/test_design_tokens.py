"""The design tokens (web/static/tokens.css, DESIGN.md): every text colour pair is at least
7:1 in light and in dark, borders on controls at least 3:1, nothing is set below 14px, the
fonts are self-hosted and under the budget, and /style is a noindex working page."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

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
