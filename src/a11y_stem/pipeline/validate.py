"""Deterministic validators run before any fix is trusted (WP3 implements).

Each returns `Check`s with category codes only. Error-level failures block ACCEPTED.
"""

from __future__ import annotations

from a11y_stem.core.types import Check


class LatexConversionError(ValueError):
    pass


def check_latex(latex: str) -> list[Check]:
    """Allowed commands only, balanced braces/environments, no file or shell commands."""
    raise NotImplementedError("WP3")


def safe_latex_to_mathml(latex: str, display: str = "block") -> str:
    """LaTeX -> MathML via latex2mathml, with the array/aligned workaround."""
    raise NotImplementedError("WP3")


def check_mathml(mathml: str) -> list[Check]:
    """Well-formed (defusedxml), <math> root in the MathML namespace, known elements only."""
    raise NotImplementedError("WP3")


def check_alt(alt: str, *, decorative: bool, caption: str | None = None) -> list[Check]:
    """Length, no 'image of', no file names, no URLs/markup, not a caption copy."""
    raise NotImplementedError("WP3")
