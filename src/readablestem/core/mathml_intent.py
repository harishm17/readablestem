"""The one MathML 4 speech attribute our allowlists accept: `intent`, with two fixed values.

`intent` tells a screen reader how to read an element, so an arbitrary value would let markup
say something other than what sighted readers and the reviewer see. The three places that
rebuild MathML from an allowlist (`pipeline.validate`, `pipeline.safe_html`, `web.mathml`) all
ask `intent_allowed`; `pipeline.chem_mathml` is the only code that writes one. MathCAT (NVDA,
JAWS) switches its chemistry reading on for these two values; a reader that does not know
`intent` ignores it.
"""

from __future__ import annotations

FORMULA_INTENT = ":chemical-formula"
EQUATION_INTENT = ":chemical-equation"
ALLOWED_INTENTS = frozenset({FORMULA_INTENT, EQUATION_INTENT})


def intent_allowed(value: str) -> bool:
    """True only for the two chemistry values, compared exactly (no trimming, no case folding)."""
    return value in ALLOWED_INTENTS
