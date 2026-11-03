"""A calibrated flag policy for equations: ask for a decision where an accepted reading looks like
the ones that turned out wrong (docs/eval-flag-policy.md).

The equation step accepts a reading without a decision when the model was confident enough and
no check failed. Most of the wrong readings nobody is asked about (the "silent" ones) pass every
check: the checks compare symbols and signs with the PDF's text layer, and a wrong style, a cut
span or a symbol the text layer shares with the reading are not seen by them. This module adds
one more rule on top: a small logistic model over a few content-free features of the accepted
fix decides whether it becomes a required decision.

- Features (`features`) are numbers any run has when the fix is made: whether the cheap and the
  main model were asked and what the second reading did, which notes the checks left, whether the
  reading reaches past the text layer's span (or the span has symbols the reading lacks), how long
  it is, and which notations it uses. They never carry the equation's text.
- The weights, the bias and the threshold are constants below, fitted by
  `readablestem.eval.flag_features` on the development splits only and versioned (`VERSION`); a new
  fit is a new version. There is no runtime model call and no learning at run time.
- The policy only ever ADDS a required decision (`apply`): a fix that is already required, failed
  or abstained is returned as it is, and nothing is ever taken off the review screen.
- Off unless `FLAG_POLICY` is set.

`features` takes plain values (not a Fix or an Element) so the eval can compute the same numbers
from a recorded run's items as the step does from a live fix.
"""

from __future__ import annotations

import html
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping

from readablestem.core.types import Check, Fix, Status

VERSION = "flag-policy-1"

# Fitted on the development splits (`eval/flag_features.py fit`); see docs/eval-flag-policy.md.
BIAS = -2.78
WEIGHTS: dict[str, float] = {
    "esc_changed": 0.2,
    "esc_same": -0.04,
    "esc_skipped": -0.09,
    "note_style": 0.06,
    "display": 0.1,
    "no_text": 1.41,
    "no_unicode": -0.24,
    "ext_extra": 0.09,
    "ext_missing": 0.09,
    "size": 0.76,
    "single": -0.58,
    "script": 0.65,
    "frac": -0.29,
    "root": 0.13,
    "bigop": 0.66,
    "greek": -0.06,
    "bold": 0.54,
    "calligraphic": 0.35,
    "blackboard": -0.06,
    "upright": 1.39,
    "accent": -0.25,
    "delimiter": 0.57,
}
# From which probability an accepted reading becomes a required decision: the highest threshold
# that spends the decision budget on the development papers (4 required decisions a page).
THRESHOLD = 0.4541

FEATURES = (
    "esc_changed",  # the main model's second reading differs from the cheap one's
    "esc_same",  # ... gives the same LaTeX
    "esc_skipped",  # the cheap answer was kept without a second reading (`single_reading`)
    "note_style",  # a cosmetic style difference was noted
    "display",
    "no_text",  # the span has no text layer
    "no_unicode",  # the text layer has characters with no Unicode letter or digit
    "ext_extra",  # the reading has symbols the text layer's span does not (it reaches past)
    "ext_missing",  # the span's text layer has symbols the reading lacks
    "size",  # log(1 + symbols) / 4
    "single",  # one symbol only
    "script",
    "frac",
    "root",
    "bigop",
    "greek",
    "bold",
    "calligraphic",
    "blackboard",
    "upright",
    "accent",
    "delimiter",
)

_GREEK = re.compile(
    r"\\(?:alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|iota|kappa|"
    r"lambda|mu|nu|xi|pi|varpi|rho|varrho|sigma|varsigma|tau|upsilon|phi|varphi|chi|psi|omega|"
    r"Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega)(?![A-Za-z])"
)
_NOTATION = {
    "script": re.compile(r"[_^]"),
    "frac": re.compile(r"\\[dt]?frac(?![A-Za-z])|\\binom(?![A-Za-z])|\\over(?![A-Za-z])"),
    "root": re.compile(r"\\sqrt(?![A-Za-z])"),
    "bigop": re.compile(r"\\(?:sum|prod|int|oint|lim|max|min|sup|inf)(?![A-Za-z])"),
    "bold": re.compile(r"\\(?:mathbf|boldsymbol|bm|pmb)(?![A-Za-z])"),
    "calligraphic": re.compile(r"\\(?:mathcal|mathscr|mathfrak)(?![A-Za-z])"),
    "blackboard": re.compile(r"\\mathbb(?![A-Za-z])"),
    "upright": re.compile(r"\\(?:mathrm|text|operatorname|mathsf|mathtt)(?![A-Za-z])"),
    "accent": re.compile(
        r"\\(?:hat|widehat|bar|overline|tilde|widetilde|dot|ddot|vec|check)(?![A-Za-z])"
    ),
    "delimiter": re.compile(r"\\(?:left|lfloor|lceil|rfloor|rceil|langle|rangle|\|)|[|]"),
}
_TAGS = re.compile(r"<[^>]*>")
_DOTLESS = str.maketrans({"\u0131": "i", "\u0237": "j", "\u2206": "\u0394"})


def _bag(text: str) -> Counter[str]:
    """Letters, digits and Greek letters, case-folded (the step's `_symbol_bag`: accents out)."""
    folded = unicodedata.normalize("NFKC", text).translate(_DOTLESS).casefold()
    return Counter(ch for ch in folded if ch.isalnum() and unicodedata.category(ch) != "Lm")


def _no_unicode(text: str) -> bool:
    """A character the text layer has no letter for: private use, an unmapped `(cid:..)`."""
    return "(cid:" in text or any(unicodedata.category(ch) in ("Co", "Cn") for ch in text)


def features(
    *,
    display: str | None,
    escalation: str | None,
    codes: Iterable[str],
    text: str | None,
    latex: str,
    mathml: str,
) -> dict[str, float]:
    """The feature vector of one accepted reading. `escalation` is `Fix.escalation`, `codes` the
    codes of its failed checks, `text` the span's text layer (`Element.text`), `latex` and
    `mathml` the reading. Every feature is 0 or 1, except `size` (0 to about 1)."""
    codes = set(codes)
    text = text or ""
    got = _bag(html.unescape(_TAGS.sub(" ", mathml)))
    have = _bag(text)
    ext_extra = sum((got - have).values())
    ext_missing = sum((have - got).values())
    symbols = sum(got.values())
    out = {
        "esc_changed": float(escalation == "changed"),
        "esc_same": float(escalation == "same"),
        "esc_skipped": float(escalation == "skipped" or "single_reading" in codes),
        "note_style": float("style_cosmetic" in codes),
        "display": float(display == "block"),
        "no_text": float(not text.strip()),
        "no_unicode": float(_no_unicode(text)),
        "ext_extra": float(ext_extra > 0 and bool(text.strip())),
        "ext_missing": float(ext_missing > 0),
        "size": min(math.log1p(symbols) / 4.0, 1.5),
        "single": float(symbols == 1),
        "greek": float(bool(_GREEK.search(latex))),
    }
    for name, pattern in _NOTATION.items():
        out[name] = float(bool(pattern.search(latex)))
    return {name: out[name] for name in FEATURES}


def logit(feats: Mapping[str, float], weights: Mapping[str, float], bias: float) -> float:
    return bias + sum(weights.get(name, 0.0) * value for name, value in feats.items())


def probability(
    feats: Mapping[str, float],
    weights: Mapping[str, float] | None = None,
    bias: float | None = None,
) -> float:
    """P(this accepted reading is wrong) under the policy's weights."""
    z = logit(feats, WEIGHTS if weights is None else weights, BIAS if bias is None else bias)
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def should_flag(feats: Mapping[str, float], threshold: float | None = None) -> bool:
    return probability(feats) >= (THRESHOLD if threshold is None else threshold)


def eligible(fix: Fix) -> bool:
    """Only an accepted equation that nobody is asked about can gain a decision."""
    return fix.status is Status.ACCEPTED and fix.review != "required" and fix.equation is not None


def apply(fix: Fix, *, text: str | None) -> Fix:
    """`fix` with a required decision added when the policy says so; `fix` itself otherwise.
    Nothing is taken off a fix: a required, failed or abstained fix is returned unchanged, and the
    checks, the proposal and the confidence stay as they were."""
    if not eligible(fix) or fix.equation is None:
        return fix
    feats = features(
        display=fix.equation.display,
        escalation=fix.escalation,
        codes=[c.code or c.name for c in fix.checks if not c.passed],
        text=text,
        latex=fix.equation.latex,
        mathml=fix.equation.mathml,
    )
    if not should_flag(feats):
        return fix
    # literals: tests/test_web_review_messages.py reads the checks the pipeline emits from source
    note = Check(name="equation.flag_policy", passed=False, severity="warning", code="flag_policy")
    return fix.model_copy(
        update={"status": Status.NEEDS_REVIEW, "review": "required", "checks": [*fix.checks, note]}
    )
