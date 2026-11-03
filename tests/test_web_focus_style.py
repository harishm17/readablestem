"""The focus indicator of the web app, read from its stylesheet (offline, no browser).

WCAG 2.4.7 (focus visible), 1.4.11 (non-text contrast) and 2.4.13 (focus appearance, AAA, the
sizes used as a guide): the ring is at least 2 CSS px thick, it is not removed on `:focus-visible`
anywhere, and its colour has at least 3:1 against every page and panel background it can sit on,
and the halo beside it has at least 3:1 against the ring, so the ring shows on dark and light
areas alike. The browser walk (tests/test_web_keyboard_browser.py) checks that every control a
Tab reaches shows a ring; this checks the colours behind it.
"""

from __future__ import annotations

import re
from pathlib import Path

CSS = (Path(__file__).parents[1] / "src/readablestem/web/static/app.css").read_text()
COLOURS = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", CSS))


def luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def rule(selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert match, f"no rule for {selector}"
    return match.group(1)


def test_the_focus_ring_is_thick_and_has_a_halo():
    body = rule(":focus-visible")
    width = re.search(r"outline:\s*(\d+)px solid var\(--focus\)", body)
    assert width and int(width.group(1)) >= 2, body
    assert "box-shadow: 0 0 0 5px var(--focus-halo)" in body


def test_the_focus_colour_has_three_to_one_against_every_background_it_sits_on():
    ring = COLOURS["--focus"]
    for background in ("--bg", "--panel", "--desk"):
        assert contrast(ring, COLOURS[background]) >= 3, background
    assert contrast(ring, COLOURS["--focus-halo"]) >= 3  # ring against its own halo


def test_only_script_focused_containers_drop_the_ring():
    """`outline: none` is allowed on `:focus` (not `:focus-visible`) of containers that take
    focus from a link or a script (tabindex -1: `main`, a heading, a region), never on a
    control that Tab reaches: those keep the `:focus-visible` ring above."""
    dropped = re.findall(r"([^{}]+)\{[^}]*outline:\s*none[^}]*\}", CSS)
    assert dropped
    for selectors in dropped:
        for selector in (s.strip() for s in selectors.split(",")):
            assert selector.endswith(":focus"), selector
