"""Checks on generated HTML (WP4 implements).

`static_checks` is pure Python (lang, title, landmarks, heading order, img alt, MathML
present). `axe_checks` runs axe-core in headless Chromium when the `browser` group is
installed, and returns a single skipped warning otherwise.
"""

from __future__ import annotations

from a11y_stem.core.types import Check


def static_checks(html: str) -> list[Check]:
    raise NotImplementedError("WP4")


def axe_checks(html: str) -> list[Check]:
    raise NotImplementedError("WP4")
