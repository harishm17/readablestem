"""Heading fixes (WP2 implements). Deterministic: no model call.

Turns `Kind.HEADING` elements into `Fix(kind=HEADING, heading=HeadingFix(level, text))`.
Levels come from layout clustering; this step repairs the outline so it never skips a
level going down (h1 -> h3 becomes h1 -> h2), and marks uncertain ones NEEDS_REVIEW
(e.g. bold body-size lines, all-caps lines, numbered lines that might be list items).
"""

from __future__ import annotations

from a11y_stem.core.types import Element, Fix


def heading_fixes(elements: list[Element]) -> list[Fix]:
    raise NotImplementedError("WP2")
