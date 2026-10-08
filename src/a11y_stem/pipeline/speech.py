"""MathML -> plain-English speech text (WP3 implements). Deterministic.

Used on the review screen and in the report so a reviewer can read what a screen reader
would roughly say. The output HTML keeps MathML only (screen readers voice it natively);
speech is never injected as aria-label, which would hide the maths structure.
"""

from __future__ import annotations


def mathml_to_speech(mathml: str) -> str:
    raise NotImplementedError("WP3")
