# ruff: noqa: RUF001
# (look-alike dashes, quotes, slashes and signs are what this module unifies)
r"""Scorer v2: equations compared as a stream of symbols (backlog T2-12).

The primary scorer (`eval.equations`, ADR 0001) compares normalised MathML trees. It cannot
give full credit to a correct transcription when the answer key packs several symbols into
one token: OpenStax writes `<mtext>time =</mtext>` and `<mtext>m/s)(46 s)</mtext>` where
LaTeX gives text plus `<mo>`, writes `-5` as one `<mn>`, and writes the same dash, mu,
prime and degree sign with different code points. On the 30-equation development set 5
known-correct transcriptions fail for this reason alone (tests/fixtures/
sample_latex_predictions.jsonl, `known_gap`), and on the held-out run 230 of 1,217 matched
equations could never score exact.

This module is a secondary measure, reported beside the primary one and labelled as such
(`LABEL`); it never replaces it. The primary scorer and its numbers are unchanged. Every
equation the primary lenient scorer calls exact is exact here too: v2 starts from the same
lenient normalisation and only forgives token packing and look-alike characters.

What v2 does, in order:

1. The same lenient normalisation as the primary scorer (`equations.normalize(lenient=True)`:
   namespaces, `semantics`, `mfenced`, `mathvariant` inheritance, `mi`/`mn`/`mtext` as one
   token type, trailing sentence punctuation dropped).
2. Every token leaf is split into atoms: a number (`3.5`, `1,000`), a letter, or one other
   character. So `time =` is `t i m e =`; `-5` is `-`, `5`; `m/s)(46 s)` is
   `m / s ) ( 46 s )`. A word is its letters because the answer key writes `avg` as the
   text "avg" in one place and as the letters a, v, g in another, and the primary lenient
   scorer already treats text and identifiers as one token type.
3. Look-alike characters are one atom: every dash and the minus sign; the micro sign and
   Greek mu; apostrophe, right quote and prime (a double prime is two primes); degree
   sign, ring operator and the ring accent; the middle dot variants; the division slash
   variants; a few relation and Greek-letter glyph variants (the slanted forms of the
   less-or-equal and greater-or-equal signs, epsilon, phi, theta). Bold, double-struck
   and other `mathvariant` styles stay: they change the meaning.
4. A degree sign or primes written as a superscript (`90^\circ`, `f^{\prime}`) are the
   same atoms as the characters written inline (`90°`, `f′`).
5. A decimal point written as its own token between two numbers joins them.
6. Structure stays: fractions, scripts, roots, tables and any fixed-arity grouping are
   kept as open and close markers in the stream, so `a/b` is not `a b`. A leaf that
   splits into several atoms inside a script or fraction slot is a group there, as the
   same atoms written as an `mrow` are.

The score is exact (the two streams are equal) and a similarity (one minus the edit
distance over the longer stream, in atoms). `credit_split` and `credit_merged` also say
when several predicted pieces together equal one gold equation, or one predicted equation
equals several consecutive gold equations: the document task reports those apart
(`pipeline_eval.document_metrics`), because a split is still a layout error.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from a11y_stem.eval.equations import MathMLError, latex_to_mathml, normalize

LABEL = "stream v2 (secondary; the primary scorer is exact_lenient)"
SCORER = "stream-v2"

# Elements whose children form an implicit row; any other non-token element is a
# fixed-arity slot structure (mfrac, msub, mroot, mtable, ...).
_ROW_TAGS = {"math", "mrow", "msqrt", "mtd", "menclose"}

_ATOM = re.compile(
    r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?)"  # 1,000 or 1,000.5
    r"|(?:\d+(?:\.\d+)?)"  # 36, 9.81
    r"|(?:\.\d+)"  # .5
    r"|(?:[^\W\d_])"  # one letter (Latin, Greek, ...): a word is its letters
    r"|\S"  # any other single character
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?|\.\d+")

# Look-alike characters, mapped to one representative.
_MINUS = "−"
_MAP: dict[str, str] = {
    # dashes and minus signs
    **dict.fromkeys(
        "-‐‑‒–—―﹘﹣－➖", _MINUS
    ),  # hyphen..horizontal bar, small hyphen-minus, fullwidth hyphen-minus
    # micro sign -> Greek small letter mu
    "µ": "μ",
    # primes: apostrophes, right single quote, modifier prime, acute accent
    **dict.fromkeys("'’ʹ´ʹ", "′"),
    # degrees: degree sign, ring operator, ring above, masculine ordinal
    **dict.fromkeys("°∘˚º", "°"),
    # middle dots
    **dict.fromkeys("·⋅∙‧", "⋅"),
    # division slash, fraction slash
    "∕": "/",
    "⁄": "/",
    # asterisk operator
    "*": "∗",
    "∗": "∗",
    # relation glyph variants
    "⩽": "≤",
    "⩾": "≥",
    "∶": ":",
    "⟶": "→",
    "⟵": "←",
    # ellipses
    "⋯": "…",
    # Greek letter glyph variants (\epsilon/\varepsilon, \phi/\varphi, \theta/\vartheta)
    "ϵ": "ε",
    "ϕ": "φ",
    "ϑ": "θ",
    # fullwidth forms of the signs maths uses
    "＋": "+",
    "＝": "=",
    "（": "(",
    "）": ")",
    "，": ",",
}
_PRIMES = {"′"}
_MULTI_PRIME = {"″": 2, "‴": 3, "⁗": 4}
_DEGREE = "°"
_DECIMAL_POINT = "."
# Atoms that separate equations when several are put together (`credit_*`).
SEPARATORS = frozenset({",", ";", ".", "and", "or"})


def _atoms(text: str) -> list[str]:
    """Split one token's text into atoms (module docstring, step 2) and unify look-alikes."""
    text = text.replace("...", "…")
    out: list[str] = []
    for match in _ATOM.finditer(text):
        atom = match.group()
        if atom in _MULTI_PRIME:
            out.extend(["′"] * _MULTI_PRIME[atom])
        else:
            out.append(_MAP.get(atom, atom))
    return out


def _is_word(atom: str) -> bool:
    return any(ch.isalnum() for ch in atom)


def _leaf(elem: ET.Element) -> list[str]:
    """Atoms of one token leaf; letters and digits carry the leaf's `mathvariant`."""
    variant = elem.get("mathvariant")
    atoms = _atoms(elem.text or "")
    if variant:
        return [f"{variant}:{a}" if _is_word(a) else a for a in atoms]
    return atoms


def _linearise(elem: ET.Element, *, slot: bool = False) -> list[str]:
    """The stream of one normalised element. `slot`: the element fills a fixed-arity slot
    (a numerator, a script), where several atoms are a group."""
    if elem.tag in {"tok", "mo"}:
        atoms = _leaf(elem)
        return ["<mrow>", *atoms, "</mrow>"] if slot and len(atoms) > 1 else atoms
    children = list(elem)
    if elem.tag in _ROW_TAGS:
        inner: list[str] = []
        for child in children:
            inner += _linearise(child)
        if elem.tag == "math":
            return inner
        marker = elem.tag
        # A row in a fixed-arity slot is a group; the top-level rows are flattened away.
        if elem.tag == "mrow":
            return ["<mrow>", *inner, "</mrow>"] if slot and len(children) != 1 else inner
        return [f"<{marker}>", *inner, f"</{marker}>"]
    # a fixed-arity element: children are slots
    slots = [_linearise(child, slot=True) for child in children]
    if elem.tag == "msup" and len(slots) == 2:
        # degree sign or primes written as a superscript are the same atoms written inline
        bare = [a for a in slots[1] if a not in {"<mrow>", "</mrow>"}]
        if bare and all(a == _DEGREE or a in _PRIMES for a in bare):
            base = slots[0]
            if len(base) > 2 and base[0] == "<mrow>" and base[-1] == "</mrow>":
                base = base[1:-1]
            return [*base, *bare]
    attrs = "".join(f"[{k}={v}]" for k, v in sorted(elem.attrib.items()))
    out = [f"<{elem.tag}{attrs}>"]
    for part in slots:
        out += part
    out.append(f"</{elem.tag}>")
    return out


def _join_decimals(stream: list[str]) -> list[str]:
    """`3`, `.`, `5` -> `3.5` when the point stands alone between two plain numbers."""
    out: list[str] = []
    i = 0
    while i < len(stream):
        atom = stream[i]
        if (
            atom == _DECIMAL_POINT
            and out
            and i + 1 < len(stream)
            and _NUMBER.fullmatch(out[-1])
            and "." not in out[-1]
            and re.fullmatch(r"\d+", stream[i + 1])
        ):
            out[-1] = f"{out[-1]}.{stream[i + 1]}"
            i += 2
            continue
        out.append(atom)
        i += 1
    return out


def stream_of(source: str | ET.Element) -> list[str]:
    """The comparable token stream of a MathML tree (module docstring)."""
    root = normalize(source, lenient=True)
    return _join_decimals(_linearise(root))


def stream_of_latex(latex: str) -> list[str]:
    return stream_of(latex_to_mathml(latex))


def edit_distance(a: Sequence[str], b: Sequence[str]) -> int:
    """Levenshtein distance between two streams, in atoms."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


@dataclass
class StreamScore:
    exact: bool
    similarity: float
    gold_atoms: int
    pred_atoms: int
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def score_stream(pred: str | ET.Element, gold: str | ET.Element) -> StreamScore:
    """Score predicted MathML against gold MathML as streams. An unparseable prediction
    scores zero with the error, like the primary scorer."""
    gold_stream = stream_of(gold)
    try:
        pred_stream = stream_of(pred)
    except MathMLError as exc:
        return StreamScore(False, 0.0, len(gold_stream), 0, str(exc))
    largest = max(len(pred_stream), len(gold_stream))
    distance = edit_distance(pred_stream, gold_stream)
    return StreamScore(
        exact=pred_stream == gold_stream,
        similarity=1.0 if largest == 0 else max(0.0, 1.0 - distance / largest),
        gold_atoms=len(gold_stream),
        pred_atoms=len(pred_stream),
    )


def score_stream_latex(pred_latex: str, gold: str | ET.Element) -> StreamScore:
    try:
        pred = latex_to_mathml(pred_latex)
    except MathMLError as exc:
        return StreamScore(False, 0.0, len(stream_of(gold)), 0, str(exc))
    return score_stream(pred, gold)


def missing_stream(gold: str | ET.Element) -> StreamScore:
    """Score for "no proposal": wrong, like a missing prediction in the primary scorer."""
    return StreamScore(False, 0.0, len(stream_of(gold)), 0, "no proposal")


# ----------------------------------------------------------- split and merged gold


def _without_separators(stream: Sequence[str]) -> list[str]:
    return [a for a in stream if a not in SEPARATORS]


def credit_split(pieces: Sequence[Sequence[str]], gold: Sequence[str]) -> bool:
    """Do two or more predicted pieces, in order, make the gold equation? Separators
    (comma, semicolon, point, "and", "or") between or around the pieces are ignored on
    both sides."""
    if len(pieces) < 2:
        return False
    joined = [a for piece in pieces for a in _without_separators(piece)]
    return bool(joined) and joined == _without_separators(gold)


def credit_merged(pred: Sequence[str], golds: Sequence[Sequence[str]]) -> int:
    """How many consecutive gold equations, from the first, one predicted equation is
    exactly the concatenation of (2 or more), else 0. The caller slides the start, so a
    merge is only credited for gold equations it fully covers."""
    target = _without_separators(pred)
    if not target:
        return 0
    joined: list[str] = []
    for count, gold in enumerate(golds, 1):
        joined += _without_separators(gold)
        if count >= 2 and joined == target:
            return count
        if len(joined) >= len(target):
            break
    return 0


def summarize(scores: Sequence[StreamScore]) -> dict:
    """Aggregate secondary scores; rates are over all items, errors included."""
    n = len(scores)
    if n == 0:
        return {"scorer": SCORER, "label": LABEL, "n": 0}
    return {
        "scorer": SCORER,
        "label": LABEL,
        "n": n,
        "exact": sum(s.exact for s in scores) / n,
        "mean_similarity": sum(s.similarity for s in scores) / n,
        "errors": sum(s.error is not None for s in scores),
    }
