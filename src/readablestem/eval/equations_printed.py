r"""Scorer "printed": do the answer and the answer key print the same? (beside `exact_lenient`)

`exact_lenient` (`eval.equations`) compares normalised MathML trees. On the five development
parts (9,321 equations, recorded runs) it calls 669 accepted equations wrong. Read by eye, about
370 of those print the same as the key and differ only in how the LaTeX is spelled (`\text`
against `\mathrm`, braces round one token, `\ldots` against `\dots`), about 200 are paired with
another region's key (the answer says what the file's own characters say, the key does not), and
55 to 100 are really wrong (docs/eval.md, "The printed score"). A rate on the old score moves with
the key's style and with the pairing more than with accuracy.

Two things here, both pure functions of text, no model, no network:

* `exact_printed(pred_latex, gold_latex)`: the two LaTeX sources print the same. `canon` spells
  every pair of equivalent notations one way. It does not forgive a different character, a sign,
  a relation or an operator, an accent on one side only, a prime added or missing, a different
  structure (a fraction against a product, a subscript against a superscript) or a letter style
  (bold, script, blackboard) on one side only: those print differently.
* `is_pairing_miss`: the answer says what the file's own characters say and the key says
  something else, so the key is another region's. That is a fault of the pairing (the span the
  key was matched to), not a wrong reading, and it is never counted as right.

The rule is the one that sorted the 669 (the measurement of 2026-11-18, finding F-580), moved
here with its reasons.
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter

import defusedxml.ElementTree as DET

from readablestem.pipeline.text_layer_compare import latex_letter_styles

# Commands that change spacing, size or numbering, never what is printed.
#
# A command is dropped only as a whole word. The first version of this rule dropped `\left` and
# `\right` as text, so `\rightarrow` became `arrow` and `\leftarrow`, `\bigcup`, `\bigoplus` were
# cut the same way; the lookahead keeps `\to` and `\rightarrow` equal and every other name whole.
_DROP = (
    r"\\(?:left|right|bigl|bigr|Bigl|Bigr|biggl|biggr|Biggl|Biggr|bigg|Bigg|big|Big|displaystyle"
    r"|textstyle|scriptstyle|nonumber|notag|hfill|quad|qquad|enspace)(?![A-Za-z])"
    r"|\\(?:vphantom|label|tag)\{[^}]*\}|\\hspace\*?\{[^}]*\}"
    r"|\\(?:no)?limits(?![A-Za-z])|\\color\{[^}]*\}"
    r"|\\,|\\;|\\:|\\!|\\ |~"
)
# Upright text in a formula prints the same whichever command sets it.
_UPRIGHT = r"\\(?:mathrm|textrm|text|mbox|hbox|operatorname)"
_BOLD = r"\\(?:mathbf|textbf|bm|boldsymbol|pmb)"
_FUNCTIONS = "exp|sin|cos|tan|log|ln|lim|max|min|sup|inf|det|tr|arg|sgn|deg|dim|ker|gcd|Pr"
_SYNONYMS = (
    ("ast", "*"),
    ("backslash", "\\setminus"),
    ("lbrace", "\\{"),
    ("rbrace", "\\}"),
    ("le", "\\leq"),
    ("ge", "\\geq"),
    ("leqslant", "\\leq"),
    ("geqslant", "\\geq"),
    ("varepsilon", "\\epsilon"),
    ("to", "\\rightarrow"),
    ("ne", "\\neq"),
    ("gets", "\\leftarrow"),
    ("colon", ":"),
    ("vert", "|"),
    ("lvert", "|"),
    ("rvert", "|"),
    ("stackrel", "\\overset"),
)
# A command that scopes over the rest of its group: braces round it are not redundant.
_SCOPING = re.compile(
    r"\\(?:over|atop|choose|above|it|tt|sf|sl|sc|em|boldmath|color|[Ll]arge|Huge|huge|tiny|small"
    r"|footnotesize|normalsize)(?![A-Za-z])"
)
_ENVIRONMENTS = "array|aligned|align\\*?|eqnarray\\*?|split|gathered"


# Commands whose braces are their arguments, and the ones that take two.
_TAKES_ARGUMENT = frozenset(
    (
        "frac",
        "dfrac",
        "tfrac",
        "binom",
        "sqrt",
        "mathrm",
        "mathbf",
        "mathbb",
        "mathcal",
        "mathfrak",
        "mathsf",
        "mathtt",
        "mathit",
        "mathscr",
        "text",
        "textbf",
        "textit",
        "textrm",
        "textsf",
        "texttt",
        "operatorname",
        "boldsymbol",
        "bm",
        "pmb",
        "hat",
        "tilde",
        "bar",
        "vec",
        "dot",
        "ddot",
        "dddot",
        "overline",
        "underline",
        "overbrace",
        "underbrace",
        "overset",
        "underset",
        "substack",
        "begin",
        "end",
        "label",
        "tag",
        "color",
        "hspace",
        "vphantom",
        "phantom",
        "cancel",
        "boxed",
        "mathop",
        "mathrel",
        "mathbin",
        "mathord",
        "mathpunct",
        "mathclose",
        "mathopen",
        "xrightarrow",
        "xleftarrow",
        "ket",
        "bra",
        "not",
        "ensuremath",
        "textcolor",
    )
)
_TAKES_TWO = frozenset(("frac", "dfrac", "tfrac", "binom", "overset", "underset"))


def _group_open(s: str, close: int) -> int:
    """The index of the `{` that the `}` at `close` closes (-1 when unbalanced)."""
    depth = 0
    for i in range(close, -1, -1):
        if s[i] == "}" and (i == 0 or s[i - 1] != "\\"):
            depth += 1
        elif s[i] == "{" and (i == 0 or s[i - 1] != "\\"):
            depth -= 1
            if depth == 0:
                return i
    return -1


def _is_argument(s: str, start: int) -> bool:
    """The group opening at `start` belongs to what is before it: a script (`x_{..}`), the argument
    of a command (`\\frac{..}{..}`, `\\mathbf{..}`), a root's order (`\\sqrt[3]{x}`), a digit
    (`\\frac1{23}`) or an escaped brace. A group after anything else is only a grouping. Groups
    that follow each other are counted from the first: `\\frac{a}{b}{c}` has a grouping third."""
    before = s[:start]
    if not before:
        return False
    chain = 0  # groups between this one and what they belong to
    while before.endswith("}") and not before.endswith("\\}"):
        opened = _group_open(before, len(before) - 1)
        if opened < 0:
            return True
        before, chain = before[:opened], chain + 1
    if not before:
        return False
    prev = before[-1]
    if prev.isdigit():
        return chain == 0 and re.search(r"[_^][0-9]$", before) is None
    if prev in "_^]*\\":
        return chain == 0
    if prev.isalpha():
        command = re.search(r"\\([A-Za-z]+)$", before)
        if command is None:
            return False
        name = command.group(1)
        return chain < (2 if name in _TAKES_TWO else 1 if name in _TAKES_ARGUMENT else 0)
    return False


def _drop_free_groups(s: str) -> str:
    """`s` without the `{...}` groups that are only a grouping. A group that holds a command
    scoping over its end (`\\over`, `\\it`, a size) keeps its braces."""
    opens: list[int] = []
    drop: set[int] = set()
    for i, char in enumerate(s):
        if char not in "{}" or (i and s[i - 1] == "\\"):
            continue
        if char == "{":
            opens.append(i)
        elif opens:
            start = opens.pop()
            if not _is_argument(s, start) and not _SCOPING.search(s[start + 1 : i]):
                drop.update((start, i))
    return "".join(char for i, char in enumerate(s) if i not in drop)


def canon(latex: str, keep_styles: bool = True) -> str:
    """One spelling for every notation that prints alike. `keep_styles=False` also drops the
    letter-style commands (`\\mathbf`, `\\mathcal`, ...), for the second test of `exact_printed`."""
    s = latex
    # The alignment environments differ in name and column spec, not in print.
    # A matrix is its rows between the brackets the name stands for.
    s = re.sub(r"\\begin\{bmatrix\}", r"[\\begin{aligned}", s)
    s = re.sub(r"\\end\{bmatrix\}", r"\\end{aligned}]", s)
    s = re.sub(r"\\begin\{pmatrix\}", r"(\\begin{aligned}", s)
    s = re.sub(r"\\end\{pmatrix\}", r"\\end{aligned})", s)
    s = re.sub(rf"\\begin\{{(?:{_ENVIRONMENTS})\}}(\{{[^}}]*\}})?", r"\\begin{aligned}", s)
    s = re.sub(rf"\\end\{{(?:{_ENVIRONMENTS})\}}", r"\\end{aligned}", s)
    # Italic is what a bare letter already is.
    s = re.sub(r"\\ensuremath\{|\\mathit\{|\\textit\{", "{", s)
    s = s.replace("^{^{", "^{{")
    # A line break goes first, before the control spaces after it are taken off (`\\ \nu`).
    s = s.replace("\\\\", ";")
    s = re.sub(_DROP, " ", s)  # a space, not nothing: `\\log\\,p` is not `\\logp`
    # `{\rm x}` is `\mathrm{x}`; likewise bold and script.
    s = re.sub(r"\{\\(?:rm|mathrm)\s+([^{}]*)\}", r"\\mathrm{\1}", s)
    s = re.sub(r"\{\\(?:bf|mathbf)\s+([^{}]*)\}", r"\\mathbf{\1}", s)
    s = re.sub(r"\{\\(?:cal|mathcal)\s+([^{}]*)\}", r"\\mathcal{\1}", s)
    s = re.sub(r"\{\\(?:tt|mathtt)\s+([^{}]*)\}", r"\\mathtt{\1}", s)
    s = re.sub(r"\{\\(?:sf|mathsf)\s+([^{}]*)\}", r"\\mathsf{\1}", s)
    s = re.sub(r"\{\\(?:it|mit)\s+([^{}]*)\}", r"{\1}", s)
    s = re.sub(_BOLD + r"(?![A-Za-z])", r"\\mathbf", s)
    s = re.sub(_UPRIGHT + r"(?![A-Za-z])", r"\\mathrm", s)
    s = re.sub(r"\\scriptscriptstyle|\\scriptstyle", "", s)
    # A named function prints upright, as `\mathrm{sin}` does.
    s = re.sub(rf"\\({_FUNCTIONS})(?![A-Za-z])", r"\\mathrm{\1}", s)
    s = re.sub(r"\{([:,;])\}", r"\1", s)
    s = re.sub(r"\\rm(?=\{)", r"\\mathrm", s)
    s = re.sub(r"\\\||\\Vert|\\lVert|\\rVert|\|\|", "\u2016", s)
    s = re.sub(r"\\mathscr|\\cal\b", r"\\mathcal", s)
    # The wide and the plain accent are one accent over one thing (a width, not a mark).
    s = re.sub(r"\\wide(hat|tilde)(?![A-Za-z])", r"\\\1", s)
    s = re.sub(r"\\mathds(?![A-Za-z])", r"\\mathbb", s)
    # `\hat x` is `\hat{x}`.
    s = re.sub(
        r"\\(mathrm|mathbf|mathcal|mathbb|hat|tilde|bar|vec|widehat|widetilde)\s*"
        r"([A-Za-z]|\\[A-Za-z]+)",
        r"\\\1{\2}",
        s,
    )
    s = re.sub(r"\\(?:ldots|dots|cdots|hdots|dotsc|dotsb)|\.\s*\.\s*\.", "\u2026", s)
    s = re.sub(r"\\(?:dfrac|tfrac)", r"\\frac", s)
    s = re.sub(r"\\(?:coloneqq|coloneq|colonequals)(?![A-Za-z])", ":=", s)
    s = s.replace("\\lbrack", "[").replace("\\rbrack", "]")
    s = re.sub(r"\^\{\\prime\}|\^\\prime|\\prime", "'", s)
    s = re.sub(r"\\bf(?=\{)|\\bf\s+(?=[A-Za-z0-9])", r"\\mathbf", s)
    s = re.sub(r"\\mathrm\{\\%\}|\\%", "%", s)
    if not keep_styles:
        s = re.sub(r"\\(?:mathrm|mathbf|mathcal|mathbb|mathfrak|mathsf|mathtt)(?=\{)", "", s)
    # One name for the symbols that have two (whole commands only: `\\to` is not `\\top`).
    for old, new in _SYNONYMS:
        s = re.sub(rf"\\{old}(?![A-Za-z])", lambda _match, new=new: new, s)
    s = s.replace("\\\\", ";").replace("&", "")
    # A ket and a bra are bars and an angle bracket.
    s = re.sub(r"\\ket\{([^{}]*)\}", r"|\1\\rangle", s)
    s = re.sub(r"\\bra\{([^{}]*)\}", r"\\langle\1|", s)
    s = re.sub(r"\s+", "", s)
    # `\frac{a}2` and `\frac12` are `\frac{a}{2}` and `\frac1{2}`.
    s = re.sub(r"(\\frac\{(?:[^{}]|\{[^{}]*\})*\})([A-Za-z0-9])", r"\1{\2}", s)
    s = re.sub(r"\\frac([A-Za-z0-9])([A-Za-z0-9])", r"\\frac\1{\2}", s)
    s = s.replace("{}", "")  # an empty group prints nothing: `{}_d`, `\operatorname{E}{}[`
    # Upright round a styled letter is that style: `\operatorname{\mathbb E}` is `\mathbb{E}`.
    s = re.sub(r"\\mathrm\{(\\math(?:bb|bf|cal|frak)\{[^{}]*\})\}", r"\1", s)
    # Groups come off, and neighbouring groups in one style join (`\mathbf{w}\mathbf{t}`,
    # `\mathrm{p},\mathrm{q}`), until nothing changes: each of the two exposes the other.
    for _ in range(6):
        # A group of one token needs no braces.
        t = re.sub(r"(?<![A-Za-z}])\{([A-Za-z0-9]|\\[A-Za-z]+|\\.)\}", r"\1", s)
        t = re.sub(r"([_^])\{([A-Za-z0-9'*+\-])\}", r"\1\2", t)
        t = re.sub(r"(?<![A-Za-z0-9}_^\\])\{([A-Za-z0-9]+)\}", r"\1", t)
        t = re.sub(r"\{\{([^{}]*)\}\}", r"{\1}", t)
        t = re.sub(r"(\\frac|\\sqrt)\{([A-Za-z0-9])\}", r"\1\2", t)
        # A group that nothing refers to is a grouping that prints nothing: `m-{m_L}`, `{x(i)}`.
        t = _drop_free_groups(t)
        t = re.sub(r"\\(mathrm|mathbf|mathcal|mathbb)\{([^{}]*)\}(,?)\\\1\{", r"\\\1{\2\3", t)
        if t == s:
            break
        s = t
    # A style command sets letters and digits only: `\mathbf{k=p+q}` is
    # `\mathbf{k}=\mathbf{p}+\mathbf{q}`.
    s = re.sub(
        r"\\(mathrm|mathbf|mathcal|mathbb|mathfrak|mathsf|mathtt)\{([^{}\\_^]*)\}",
        lambda m: "".join(
            f"\\{m.group(1)}{{{run}}}" if run[0].isalnum() else run
            for run in re.findall(r"[A-Za-z0-9]+|[^A-Za-z0-9]+", m.group(2))
        ),
        s,
    )
    # A script that is only primes is those primes (`l^{\prime\prime}` is `l''`), and a prime
    # before or after a subscript prints in one place (`u'_\theta`, `u_\theta'`).
    s = re.sub(r"\^\{('+)\}", r"\1", s)
    s = re.sub(r"(_\{[^{}]*\}|_\\[A-Za-z]+|_[A-Za-z0-9])('+)", r"\2\1", s)
    # `_a^b` and `^b_a` print alike.
    group = r"\{(?:[^{}]|\{[^{}]*\})*\}"
    tok = rf"({group}|\([^()]*\)|\\mathrm\{{[^{{}}]*\}}|\\[A-Za-z]+|[^_^{{}}\\])"
    s = re.sub(r"\^" + tok + r"_" + tok, r"_\2^\1", s)
    # A trailing comma, full stop or line break is punctuation of the sentence, not of the formula.
    return re.sub(r"[,.;:\\]+$", "", s)


_STYLE_COMMANDS = re.compile(r"\\math(?:bf|cal|bb|frak)|\\boldsymbol")


def exact_printed(pred_latex: str | None, gold_latex: str | None) -> bool:
    """The answer's LaTeX and the key's print the same.

    True when the canonical forms are equal. Also true when they are equal once the letter-style
    commands are taken off and the two sources set the same letters in the same styles (the same
    bold letters, the same script letters): that is one style spelled two ways. A style on one side
    only (a bold letter against a plain one) stays wrong. False when either side has no LaTeX."""
    if not pred_latex or not gold_latex:
        return False
    cp, cg = canon(pred_latex), canon(gold_latex)
    if cp == cg:
        return True
    return (
        canon(pred_latex, keep_styles=False) == canon(gold_latex, keep_styles=False)
        and Counter(latex_letter_styles(pred_latex)) == Counter(latex_letter_styles(gold_latex))
        and Counter(_STYLE_COMMANDS.findall(cp)) == Counter(_STYLE_COMMANDS.findall(cg))
    )


# ----------------------------------------------------------------------------- pairing misses

_DOTLESS = str.maketrans({"\u0131": "i", "\u0237": "j", "\u2206": "\u0394"})


def _letters_and_digits(text: str) -> str:
    """Letters, digits and Greek letters; case kept; math alphanumerics folded to plain; accents
    (modifier letters, category Lm) out."""
    folded = unicodedata.normalize("NFKC", text).translate(_DOTLESS)
    return "".join(c for c in folded if c.isalnum() and unicodedata.category(c) != "Lm")


def _letters(mathml: str) -> str:
    """The letters and digits of every symbol in a MathML formula, in order."""
    root = DET.fromstring(mathml, forbid_dtd=True)
    return "".join(
        _letters_and_digits(el.text or "")
        for el in root.iter()
        if el.tag.split("}")[-1] in ("mi", "mn", "mo", "mtext", "ms") and (el.text or "").strip()
    )


def is_pairing_miss(
    pred_text: str | None, pred_mathml: str | None, gold_mathml: str | None
) -> bool:
    """The answer says what the file's own characters say and the key does not.

    `pred_text` is the characters of the file's region (its text layer). The letters and digits of
    the answer's MathML are counted against those of the region and against those of the key. A
    miss when the answer's equal the region's and the key's differ from the answer's, with case
    kept; or, failing that, with case folded, unless the key then equals the region (the region
    and the key agree and the answer's case is what differs: a wrong reading). It only says the key
    was matched to another region, so the pair says nothing about the reading: it is never counted
    as right. Call it for a pair `exact_printed` does not call right."""
    if not (pred_text and pred_mathml and gold_mathml):
        return False
    try:
        answer, key = Counter(_letters(pred_mathml)), Counter(_letters(gold_mathml))
    except (ET.ParseError, ValueError):
        return False
    region = Counter(_letters_and_digits(pred_text))
    if not region or answer == key:
        return False
    if answer == region:
        return True
    folded = Counter(_letters_and_digits(pred_text).casefold())
    key_folded = Counter(_letters(gold_mathml).casefold())
    answer_folded = Counter(_letters(pred_mathml).casefold())
    return folded != key_folded and folded == answer_folded
