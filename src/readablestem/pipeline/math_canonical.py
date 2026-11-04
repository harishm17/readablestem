r"""Are two readings of a formula the same formula? One answer for the product and the scorers.

A reading is LaTeX or MathML (`is_mathml` tells them apart). `compare(a, b)` says which
kinds of sameness hold, strongest first:

- `TREE`: the normalised MathML trees are equal, the primary scorer's `exact_lenient`
  (`eval.equations`, ADR 0001).
- `NOTATION`: the same once notation that does not change what is said is written alike
  (`canonical_latex`): `\mathrm{in}`, `\textrm{in}` and `\operatorname{in}` as `\text{in}`;
  `\bm` as `\boldsymbol`; `\left(` as `(` (and `\bigl`, `\Bigr`...); spacing commands and
  spaces; braces around one symbol (`x^{2}`, `{x}`, `{{x}}`); `x^b_a` as `x_a^b`. Only LaTeX
  has notation to fold; for two MathML readings it is `TREE`.
- `SPOKEN`: ReadableSTEM's speech (`pipeline.speech.mathml_to_speech`) says both in the same
  words, so a listener hears one formula (`\mathbf{x}` and `\boldsymbol{x}`: "bold x").
- `SYMBOLS`: the same symbol stream, in the same order, with the same fractions, roots and
  scripts (`eval.equations_symbols`, tables flattened), letter styles included.
- `SYMBOLS_UNSTYLED`: the same stream with the style of letters ignored (the comparison
  `page_math` makes between two readings of one span).

The kinds are not strictly nested (two formulas can sound alike with different symbols), so
`Comparison.holds` lists every kind that holds and `kind` is the strongest, in the order above.
When none holds, `reason` is a short code and `detail` says where: "unreadable_a",
"unreadable_b", "empty_a", "empty_b", "order" (the same symbols in another order or
structure) and "symbols" (other symbols). `detail` is "index:a_atom|b_atom" of the first
difference in the symbol streams. It holds formula text: never log it.

Pure functions, no model call, no network. `fold_upright_words`, `latex_to_mathml` and `speech_of`
repeat the steps of the offline scorers `scripts/rw_notation_neutral.py` and
`scripts/rw_spoken.py` (as written). `rw_notation_neutral.py` is hashed by the registrations
(`scripts/realworld_register.py`) and must not change, so it does not import this module;
`tests/test_math_canonical_scale.py` fails if the two ever disagree. `rw_spoken.py` is the
primary score of fresh5 and is hashed with this file; it imports `speakable_latex` and
`repair_converter_gaps` from here.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

from readablestem.eval import equations as eq
from readablestem.eval import equations_symbols as sym
from readablestem.pipeline import page_math
from readablestem.pipeline.speech import mathml_to_speech
from readablestem.pipeline.validate import safe_latex_to_mathml


class Kind(StrEnum):
    """What holds between two readings; the order here is strongest first."""

    TREE = "tree"
    NOTATION = "notation"
    SPOKEN = "spoken"
    SYMBOLS = "symbols"
    SYMBOLS_UNSTYLED = "symbols_unstyled"
    NONE = "none"


_ORDER = (Kind.TREE, Kind.NOTATION, Kind.SPOKEN, Kind.SYMBOLS, Kind.SYMBOLS_UNSTYLED)

# Reasons when no kind holds.
UNREADABLE_A = "unreadable_a"
UNREADABLE_B = "unreadable_b"
EMPTY_A = "empty_a"
EMPTY_B = "empty_b"
ORDER = "order"
SYMBOLS_DIFFER = "symbols"


@dataclass(frozen=True)
class Comparison:
    kind: Kind
    holds: frozenset[Kind] = field(default_factory=frozenset)
    reason: str | None = None  # set only when `kind` is NONE
    detail: str | None = None  # formula text: never log it

    @property
    def equal(self) -> bool:
        return self.kind is not Kind.NONE

    def holds_at(self, kind: Kind) -> bool:
        return kind in self.holds


# ------------------------------------------------------------------ notation folding

# The upright-word rule of the registered notation-neutral score (`scripts/rw_notation_neutral.py`):
# no brace inside the word.
_WORD = re.compile(
    r"\\(?:mathrm|textrm|mbox|textnormal|operatorname\*?|text)\s*\{([^{}]*)\}|\{\\rm\s+([^{}]*)\}"
)


def fold_upright_words(latex: str) -> str:
    r"""`\mathrm{w}`, `\textrm{w}`, `\mbox{w}`, `\textnormal{w}`, `\operatorname{w}`,
    `\operatorname*{w}` and `{\rm w}`, with no brace inside `w`, become `\text{w}`. Nothing
    else changes."""
    return _WORD.sub(
        lambda m: "\\text{" + (m.group(1) if m.group(1) is not None else m.group(2)) + "}", latex
    )


_TEXT_BODY = re.compile(r"\\text\{[^{}]*\}")
_PLACEHOLDER_BASE = 0xE000
_NULL_DELIMITER = re.compile(r"\\(?:left|right)(?![A-Za-z])\s*\.")
_SIZED = re.compile(r"\\(?:left|right|[bB]ig{1,2}[lrm]?)(?![A-Za-z])\s*")
_SPACING = re.compile(
    r"\\\\"  # a line break stays
    r"|(\\(?:[,;:! ]|q?quad(?![A-Za-z])|thinspace(?![A-Za-z])|medspace(?![A-Za-z])"
    r"|thickspace(?![A-Za-z])|enspace(?![A-Za-z])|negthinspace(?![A-Za-z])"
    r"|(?:hspace|mspace)\*?\s*\{[^{}]*\})|~)"
)
_BOLD = re.compile(r"\\(?:bm|pmb)(?![A-Za-z])")
_SCRIPT_SINGLE = re.compile(r"([_^])\{\s*(\\[A-Za-z]+|[^\s{}\\])\s*\}")
_DOUBLE_BRACE = re.compile(r"\{\{([^{}]*)\}\}")
_BARE_SINGLE = re.compile(r"(^|[+\-=*/(,<>|&])\{\s*(\\[A-Za-z]+|[^\s{}\\])\s*\}")


def _brace_ends(s: str) -> dict[int, int]:
    """Index of each `{` to the index just past its matching `}` (one pass; escaped braces
    are not groups)."""
    ends: dict[int, int] = {}
    stack: list[int] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            ends[stack.pop()] = i + 1
        i += 1
    return ends


def _read_arg(s: str, i: int, ends: dict[int, int]) -> int | None:
    """End index of the argument that starts at `i`: a braced group, a command or one
    character; None when there is none."""
    if i >= len(s) or s[i] in "^_}":
        return None
    if s[i] == "{":
        return ends.get(i)
    if s[i] == "\\":
        j = i + 1
        while j < len(s) and s[j].isascii() and s[j].isalpha():
            j += 1
        return j if j > i + 1 else min(i + 2, len(s))
    return i + 1


def _order_scripts(s: str) -> str:
    """`x^b_a` as `x_a^b` (one `^` argument directly followed by one `_` argument)."""
    ends = _brace_ends(s)
    out: list[str] = []
    i = 0
    while i < len(s):
        if s[i] == "\\":  # a command or an escaped character: copy it whole
            j = _read_arg(s, i, ends) or len(s)
            out.append(s[i:j])
            i = j
            continue
        if s[i] == "^":
            sup_end = _read_arg(s, i + 1, ends)
            if sup_end is not None and sup_end < len(s) and s[sup_end] == "_":
                sub_end = _read_arg(s, sup_end + 1, ends)
                if sub_end is not None:
                    out.append("_" + s[sup_end + 1 : sub_end] + "^" + s[i + 1 : sup_end])
                    i = sub_end
                    continue
        out.append(s[i])
        i += 1
    return "".join(out)


def _squeeze(s: str) -> str:
    """Spaces removed, except the one that ends a command name before a letter (one pass)."""
    out: list[str] = []
    in_macro = False  # the text so far ends in a backslash and letters: `\\alpha`
    after_backslash = False
    n = len(s)
    for i, ch in enumerate(s):
        if not ch.isspace():
            out.append(ch)
            letter = ch.isascii() and ch.isalpha()
            in_macro = letter and (in_macro or after_backslash)
            after_backslash = ch == "\\"
            continue
        if in_macro:
            j = i + 1
            while j < n and s[j].isspace():
                j += 1
            if j < n and s[j].isascii() and s[j].isalpha():
                out.append(" ")
        in_macro = False  # a dropped space leaves a backslash before it still the last
    return "".join(out)


def canonical_latex(latex: str) -> str:
    r"""LaTeX with the notation that does not change what is said written one way (module
    docstring). The text of `\text{...}` is kept as written. A string for comparing, not for
    converting: it is not meant to be shown or typeset."""
    bodies: list[str] = []

    def hold(match: re.Match[str]) -> str:
        bodies.append(match.group(0))
        return chr(_PLACEHOLDER_BASE + len(bodies) - 1)

    s = _TEXT_BODY.sub(hold, fold_upright_words(latex))
    s = _NULL_DELIMITER.sub("", s)
    s = _SIZED.sub("", s)
    s = _SPACING.sub(lambda m: m.group(0) if m.group(1) is None else "", s)
    s = _BOLD.sub(r"\\boldsymbol", s)
    s = _squeeze(s)
    for _ in range(4):  # nested redundant braces peel one level a pass
        before = s
        s = _SCRIPT_SINGLE.sub(r"\1\2", s)
        s = _DOUBLE_BRACE.sub(r"{\1}", s)
        s = _BARE_SINGLE.sub(r"\1\2", s)
        if s == before:
            break
    s = _order_scripts(s)
    return re.sub("[\ue000-\uf8ff]", lambda m: bodies[ord(m.group(0)) - _PLACEHOLDER_BASE], s)


# ---------------------------------------------------------- speaking LaTeX that prints alike
#
# `speakable_latex` and `repair_converter_gaps` are for the spoken-equal scorer
# (`scripts/rw_spoken.py`); `compare` does not use them, so no product decision changes. Two
# LaTeX strings that print the same must be spoken the same, and the converter and the speech
# rules read some spellings of one print differently (`\mathrm{in}` letter by
# letter, `\text{in}` as a word; `\min` "minimum", `\text{min}` "min"; `\textbf{R}` loses its
# bold; `\bigl(` is not converted; `...` is "period period period"). Each rule here writes one
# print one way, and never joins two different prints: a `\min_x` keeps its script (in display
# it is set under the word, `\text{min}_x` is not), and a `\mathrm` with a script or a command
# inside is not text. A guard test holds recorded pairs that differ in print and must stay
# different (`tests/test_math_canonical_spoken.py`).

_UPRIGHT_SPEAK = re.compile(
    r"\\(mathrm|textrm|mbox|textnormal|operatorname|text)\s*\{((?:[^{}\\$_^]|\\[,;: ])*)\}"
    r"|\{\\rm\s+((?:[^{}\\$_^]|\\[,;: ])*)\}"
)
_TEXT_SPACE = re.compile(r"\\[,;: ]|~|\s+")
_OPERATOR_NAMES = (
    *("arccos", "arcsin", "arctan", "arg", "cos", "cosh", "cot", "coth", "csc", "deg", "dim"),
    *("exp", "hom", "ker", "lg", "ln", "log", "sec", "sin", "sinh", "tan", "tanh"),
    *("min", "max", "sup", "inf", "lim", "liminf", "limsup", "det", "gcd", "Pr"),
)
# Limits go under these in display style, so `\max_x` is not `\text{max}_x`.
_NAMED_OPERATOR = re.compile(r"\\(" + "|".join(_OPERATOR_NAMES) + r")(?![A-Za-z])(?!\s*[_^])")
_BOLD_TEXT = re.compile(r"\\textbf\s*\{([^{}]*)\}|\{\\bf\s+([^{}]*)\}")
_ELLIPSIS = re.compile(r"(?<![.\\])\.\.\.(?!\.)")
_SIZE_COMMAND = re.compile(r"\\[bB]ig{1,2}[lrm]?(?![A-Za-z])\s*")
_SCRIPT_ON_SCRIPT = re.compile(r"\^\{\^(\{[^{}]*\}|\\text\{[^{}]*\}|[^\s{}\\])\}")
_PRIME_SCRIPT = re.compile(r"\^\s*(?:\{\s*((?:\\prime\s*)+)\}|\\prime(?![A-Za-z]))")
_ROW_SPACE = re.compile(r"\\\\\s*\[\s*-?[\d.]+\s*(?:pt|em|ex|mm|cm|in|bp|pc)\s*\]")
_DOLLAR_TEXT = re.compile(r"\\text\{([^{}$]*\$[^{}]*)\}")
_PAREN_TEXT_SCRIPT = re.compile(r"([_^])\s*\\text\{\(([^{}()]*)\)\}")
_PAREN_TEXT = re.compile(r"\\text\{\(([^{}()]*)\)\}")
_PRIME_AFTER_SUBSCRIPT = re.compile(r"(_\{[^{}]*\}|_[^\s{}\\])\s*'")
_UNDERLINED_ACCENT = re.compile(r"\\(widehat|hat|widetilde|tilde)\{\\underline\{([^{}]*)\}\}")
_WORD_THEN_DIGITS = re.compile(r"\\text\{([A-Za-z]+)\}(\d+)")


def _upright_text(m: re.Match[str]) -> str:
    """Upright letters as `\\text{...}`. Spaces count in text (`\\text{if } x`) and not in
    `\\mathrm` or `\\operatorname`, which ignore them."""
    command = m.group(1)
    body = m.group(2) if command else m.group(3)
    if command in ("mathrm", "operatorname", None):  # typed spaces only; `\;` and `\ ` still space
        body = re.sub(r"(?<!\\)\s+", "", body)
    return "\\text{" + _TEXT_SPACE.sub(" ", body) + "}"


def _dollar_text(m: re.Match[str]) -> str:
    r"""`\text{$1$r}` (a number or letter set in maths inside text) is `\text{1r}`;
    `\text{$m$-r}` is `{m\text{-r}}`: the same print without the maths-in-text."""
    parts = m.group(1).split("$")
    if len(parts) % 2 == 0:  # an unpaired dollar: leave it
        return m.group(0)
    if all(p.isdigit() for p in parts[1::2]):
        return "\\text{" + "".join(parts) + "}"
    out = []
    for i, part in enumerate(parts):
        if i % 2:  # maths
            out.append(part)
        elif part:
            out.append("\\text{" + part + "}")
    return "{" + "".join(out) + "}"


def repair_converter_gaps(latex: str) -> str:
    r"""The two spellings the converter reads wrongly, not differently: `\textbf{R}` and
    `{\bf R}` in maths lose their bold (written `\mathbf{R}`), and a sized delimiter (`\bigl(`,
    `\Bigr\rangle`) is not converted (the size is dropped; a size is not spoken). Nothing else
    changes."""
    s = _SIZE_COMMAND.sub("", latex)
    return _BOLD_TEXT.sub(lambda m: "\\mathbf{" + (m.group(1) or m.group(2) or "") + "}", s)


def speakable_latex(latex: str) -> str:
    r"""LaTeX with each print written one way for the speech rules (the comment above). A
    string for converting and speaking, not for showing; `\text{...}` bodies are kept."""
    bodies: list[str] = []

    def hold(match: re.Match[str]) -> str:
        bodies.append(match.group(0))
        return chr(_PLACEHOLDER_BASE + len(bodies) - 1)

    s = _DOLLAR_TEXT.sub(_dollar_text, latex)
    s = _UPRIGHT_SPEAK.sub(_upright_text, s)
    s = _WORD_THEN_DIGITS.sub(r"\\text{\1\2}", s)
    s = _SCRIPT_ON_SCRIPT.sub(r"^{\1}", s)
    s = _PAREN_TEXT_SCRIPT.sub(lambda m: m.group(1) + "{(\\text{" + m.group(2) + "})}", s)
    s = _PAREN_TEXT.sub(lambda m: "(\\text{" + m.group(1) + "})", s)
    s = _TEXT_BODY.sub(hold, s)
    s = _ROW_SPACE.sub(lambda _: "\\\\", s)
    s = repair_converter_gaps(s)
    s = _ELLIPSIS.sub(r"\\ldots ", s)
    s = re.sub(r"\\backslash(?![A-Za-z])", r"\\setminus ", s)
    s = _NAMED_OPERATOR.sub(lambda m: "\\text{" + m.group(1) + "}", s)
    s = _PRIME_SCRIPT.sub(lambda m: "'" * (m.group(1).count("\\prime") if m.group(1) else 1), s)
    s = _PRIME_AFTER_SUBSCRIPT.sub(lambda m: "'" + m.group(1), s)
    s = _UNDERLINED_ACCENT.sub(r"\\underline{\\\1{\2}}", s)
    return re.sub("[\ue000-\uf8ff]", lambda m: bodies[ord(m.group(0)) - _PLACEHOLDER_BASE], s)


# --------------------------------------------------------------------- readings

_MATHML_START = re.compile(r"\s*<(?:\?xml|m[a-z]+(?=[\s>/]))")


def is_mathml(reading: str) -> bool:
    """MathML, not LaTeX: markup that starts with `<math` or another MathML element (LaTeX
    can start with `<`, as in `<x`)."""
    return bool(_MATHML_START.match(reading)) and reading.rstrip().endswith(">")


def latex_to_mathml(latex: str, *, display: bool = False) -> str:
    """MathML of LaTeX as the product writes it (`equation_step.clean_latex`, then
    `validate.safe_latex_to_mathml`), never taking the text for MathML. Raises on LaTeX that
    does not convert."""
    from readablestem.pipeline.equation_step import clean_latex

    return safe_latex_to_mathml(clean_latex(latex), "block" if display else "inline")


def to_mathml(reading: str, *, display: bool = False) -> str:
    """MathML of a reading: MathML as it is, LaTeX converted (`latex_to_mathml`)."""
    return reading if is_mathml(reading) else latex_to_mathml(reading, display=display)


def speech_of(mathml: str, *, readings: dict[str, str] | None = None) -> str | None:
    """What the speech rules say for MathML, or None when they cannot read it (an empty
    reading is returned as it is: it says nothing, and `compare` never matches on it)."""
    try:
        said = mathml_to_speech(mathml, readings=readings) if readings else mathml_to_speech(mathml)
    except Exception:  # a formula the speech cannot read never matches
        return None
    return said


def _convert(reading: str, display: bool) -> str | None:
    try:
        return to_mathml(reading, display=display)
    except Exception:  # whatever the converter raises: not a reading
        return None


def _tree(mathml: str) -> str | None:
    try:
        return eq.canonical(mathml, lenient=True)
    except Exception:
        return None


def tree_equal(a: str, b: str, *, display: bool = False) -> bool:
    """Do two readings have one normalised tree (`exact_lenient`)? Unreadable is not equal."""
    ma, mb = _convert(a, display), _convert(b, display)
    if ma is None or mb is None:
        return False
    ta, tb = _tree(ma), _tree(mb)
    return ta is not None and ta == tb


# ------------------------------------------------------------------- comparison


def _first_difference(a: list[str], b: list[str]) -> str:
    for i, (x, y) in enumerate(zip(a, b, strict=False)):
        if x != y:
            return f"{i}:{x}|{y}"
    return f"{min(len(a), len(b))}:{'|'.join(a[len(b) :][:1])}|{'|'.join(b[len(a) :][:1])}"


def _styled(mathml: str) -> list[str] | None:
    try:
        return sym.symbols_of(mathml)
    except Exception:
        return None


def _notation_equal(a: str, b: str, display: bool) -> bool:
    """The LaTeX of both, folded, is one string or converts to one tree (nothing to fold
    in MathML)."""
    if is_mathml(a) or is_mathml(b):
        return False
    ca, cb = canonical_latex(a), canonical_latex(b)
    if ca == cb:
        return True
    ma, mb = _convert(ca, display), _convert(cb, display)
    ta = _tree(ma) if ma is not None else None
    return ta is not None and ta == (_tree(mb) if mb is not None else None)


def _result(holds: set[Kind]) -> Comparison:
    kind = next(k for k in _ORDER if k in holds)
    return Comparison(kind, frozenset(holds))


def compare(
    a: str,
    b: str,
    *,
    display: bool = False,
    readings: dict[str, str] | None = None,
) -> Comparison:
    """Which kinds of sameness hold between readings `a` and `b` (LaTeX or MathML).

    `display` is how LaTeX is converted; `readings` are the course readings handed to the
    speech rules (`speech.mathml_to_speech(readings=...)`), the same for both sides."""
    if not a.strip():
        return Comparison(Kind.NONE, reason=EMPTY_A)
    if not b.strip():
        return Comparison(Kind.NONE, reason=EMPTY_B)
    holds: set[Kind] = set()
    ma, mb = _convert(a, display), _convert(b, display)
    ta = _tree(ma) if ma is not None else None
    tb = _tree(mb) if mb is not None else None
    if ta is not None and ta == tb:
        holds.add(Kind.TREE)
    if Kind.TREE in holds or _notation_equal(a, b, display):
        holds.add(Kind.NOTATION)
    if ma is None or mb is None:
        if not holds:
            return Comparison(Kind.NONE, reason=UNREADABLE_A if ma is None else UNREADABLE_B)
        return _result(holds)
    sa, sb = speech_of(ma, readings=readings), speech_of(mb, readings=readings)
    if sa and sa == sb:
        holds.add(Kind.SPOKEN)
    xa, xb = _styled(ma), _styled(mb)
    if xa is not None and xa == xb:
        holds.add(Kind.SYMBOLS)
    ua, ub = page_math.mathml_stream(ma), page_math.mathml_stream(mb)
    if ua is not None and ua == ub:
        holds.add(Kind.SYMBOLS_UNSTYLED)
    if holds:
        return _result(holds)
    if xa is None or xb is None:
        return Comparison(Kind.NONE, reason=UNREADABLE_A if xa is None else UNREADABLE_B)
    if Counter(xa) == Counter(xb):
        return Comparison(Kind.NONE, reason=ORDER, detail=_first_difference(xa, xb))
    return Comparison(Kind.NONE, reason=SYMBOLS_DIFFER, detail=_first_difference(xa, xb))


def equal(
    a: str,
    b: str,
    at: Kind = Kind.SPOKEN,
    *,
    display: bool = False,
    readings: dict[str, str] | None = None,
) -> bool:
    """Does kind `at` hold between the readings? (`compare(...).holds_at(at)`)"""
    return compare(a, b, display=display, readings=readings).holds_at(at)
