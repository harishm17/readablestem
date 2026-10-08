r"""Deterministic validators run before any model output is trusted.

Each validator returns `Check`s: one per rule, passed or not, so the review screen can
show what a fix passed as well as what it failed. Failures carry a category code
(`Check.code`), never the offending text, so checks can be logged and aggregated after
the document is deleted. Error-level failures block ACCEPTED; warnings only send a fix
to review.

LaTeX (`check_latex`)
    Model LaTeX is untrusted input headed for a converter, so it is checked against an
    allowlist before conversion. latex2mathml never runs TeX, but it does turn some
    commands into attributes (`\href` -> `href`, `\style` -> `style`, `\class` ->
    `class`), silently drops unknown environments, and copies unknown commands into the
    output as literal text. Codes: `empty`, `too_long`, `unbalanced_braces`,
    `unbalanced_env`, `math_delimiter`, `forbidden_command`, `unknown_command`,
    `unknown_environment`.

Conversion (`safe_latex_to_mathml`)
    latex2mathml 3.81.1 renders `aligned` and `gathered` as one flat row with literal
    `&` tokens, and `align`/`split` with the relation glued into the second cell. These
    environments are rewritten to `\begin{array}{rl...}` (right/left alternating
    columns, as many as the widest row needs) before conversion. The result is parsed
    and re-serialised, so the string handed on is always well-formed and namespaced.

MathML (`check_mathml`)
    Parsed with defusedxml (no DTDs, no entity expansion). Only presentation elements
    and layout attributes are allowed: no `href`, event handlers, `style`, `class` or
    colours. Size and depth are capped, fixed-arity elements must have the right number
    of children, and LaTeX leftovers (`\foo` copied into a token) are flagged.

Alt text (`check_alt`)
    Rules from common alt-text guidance: short, not "image of ...", not a file name,
    URL or markup, not a placeholder word, and not a copy of the caption (which is read
    separately).
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from typing import Literal

import defusedxml
import defusedxml.ElementTree as DET
from latex2mathml.converter import convert as _latex2mathml_convert

from a11y_stem.core.types import Check

MATHML_NS = "http://www.w3.org/1998/Math/MathML"


class LatexConversionError(ValueError):
    """latex2mathml could not convert the input. The message never contains the LaTeX."""


def _check(name: str, code: str | None, severity: Literal["error", "warning"] = "error") -> Check:
    """A passed check when `code` is None, else a failed one with that category code."""
    return Check(name=name, passed=code is None, severity=severity, code=code)


def has_errors(checks: list[Check]) -> bool:
    """True when any error-level check failed (such a fix can never be ACCEPTED)."""
    return any(not c.passed and c.severity == "error" for c in checks)


# =========================================================================== LaTeX

MAX_LATEX_CHARS = 2000


def _words(*groups: str) -> frozenset[str]:
    """Whitespace-separated names -> set (keeps the long allowlists readable)."""
    return frozenset(word for group in groups for word in group.split())


# Commands that read or write files, run code, redefine macros, change catcodes, or
# become links/styles in the MathML. Any of these means the output is not a plain
# transcription, so it is rejected rather than "cleaned".
FORBIDDEN_COMMANDS = _words(
    """
    input include includeonly InputIfFileExists write immediate openout openin read
    closeout closein newwrite newread
    def gdef edef xdef let futurelet newcommand renewcommand providecommand
    newenvironment renewenvironment DeclareMathOperator DeclareRobustCommand
    catcode csname endcsname expandafter noexpand afterassignment makeatletter makeatother
    href url usepackage RequirePackage documentclass special includegraphics
    loop repeat jobname verb lstinputlisting verbatiminput
    class style cssId htmlClass htmlId htmlStyle htmlData
    """
)

_GREEK = """
    alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda
    mu nu xi omicron pi varpi rho varrho sigma varsigma tau upsilon phi varphi chi psi
    omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
"""
_BINARY_OPERATORS = """
    pm mp times div cdot ast star circ bullet oplus ominus otimes oslash odot cap cup
    setminus wedge vee land lor lnot neg uplus sqcap sqcup dagger ddagger diamond
    triangleleft triangleright
"""
_RELATIONS = """
    leq le geq ge neq ne equiv approx approxeq cong sim simeq propto ll gg lt gt
    leqslant geqslant lesssim gtrsim prec succ preceq succeq subset supset subseteq
    supseteq in ni notin perp parallel mid nmid asymp doteq models vdash dashv
"""
_ARROWS = """
    to gets rightarrow leftarrow leftrightarrow Rightarrow Leftarrow Leftrightarrow
    longrightarrow longleftarrow longleftrightarrow Longrightarrow Longleftarrow
    Longleftrightarrow mapsto longmapsto uparrow downarrow updownarrow Uparrow Downarrow
    Updownarrow nearrow searrow swarrow nwarrow implies iff impliedby rightleftharpoons
    leftrightharpoons rightharpoonup rightharpoondown leftharpoonup leftharpoondown
    hookrightarrow hookleftarrow
"""
_SYMBOLS = """
    infty partial nabla hbar ell Re Im aleph emptyset varnothing forall exists nexists
    angle measuredangle triangle square degree prime cdots ldots dots vdots ddots
    therefore because top bot imath jmath wp surd backslash
    langle rangle lvert rvert lVert rVert vert Vert lfloor rfloor lceil rceil
    lbrace rbrace lbrack rbrack
"""
_LARGE_OPERATORS = """
    sum prod coprod int iint iiint oint bigcup bigcap bigoplus bigotimes bigodot bigvee
    bigwedge bigsqcup lim liminf limsup sup inf max min argmax argmin det gcd Pr
    limits nolimits
"""
_FUNCTIONS = """
    sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh coth log ln lg exp deg
    dim ker hom mod bmod pmod
"""
_SPACING = """
    quad qquad enspace thinspace negthinspace medspace negmedspace thickspace
    negthickspace hspace
"""
_SIZING = """
    left right middle big Big bigg Bigg bigl bigr Bigl Bigr biggl biggr Biggl Biggr
    displaystyle textstyle hline nonumber notag
"""
# Commands taking arguments. Kept separate so the tests can check that latex2mathml
# converts every allowed command with its arguments (tests/test_steps_validate.py).
ONE_ARG_COMMANDS = _words(
    """
    vec hat bar dot ddot tilde overline underline overrightarrow overleftarrow
    overleftrightarrow widehat widetilde check breve acute grave mathring overbrace
    underbrace sqrt boxed cancel xrightarrow xleftarrow
    mathbf mathrm mathit mathcal mathbb mathsf mathtt mathfrak mathscr mathnormal
    boldsymbol bm text textrm textbf textit textsf texttt textnormal textup mbox
    operatorname
    """
)
TWO_ARG_COMMANDS = _words("frac dfrac tfrac cfrac binom dbinom tbinom overset underset stackrel")
ENVIRONMENT_COMMANDS = frozenset({"begin", "end"})
SYMBOL_COMMANDS = _words(
    _GREEK, _BINARY_OPERATORS, _RELATIONS, _ARROWS, _SYMBOLS, _LARGE_OPERATORS, _FUNCTIONS
) | _words(_SPACING, _SIZING)
ALLOWED_COMMANDS = SYMBOL_COMMANDS | ONE_ARG_COMMANDS | TWO_ARG_COMMANDS | ENVIRONMENT_COMMANDS

# Control symbols: backslash + one non-letter (spacing, escaped specials, line break).
ALLOWED_CONTROL_SYMBOLS = _words(r", ; : ! > \ { } | % & # _ $") | {" "}

ALLOWED_ENVIRONMENTS = _words(
    "array matrix pmatrix bmatrix Bmatrix vmatrix Vmatrix cases aligned align align* gathered split"
)
# Environments latex2mathml renders badly; rewritten to `array` before conversion.
ALIGNMENT_ENVIRONMENTS = frozenset({"aligned", "align", "align*", "split", "gathered"})

_ENV_NAME = re.compile(r"\s*\{\s*([A-Za-z]+\*?)\s*\}")


def _tokens(latex: str) -> Iterator[tuple[str, str, int]]:
    """Yield (kind, value, end) for the characters that matter to the checks.

    kind is "word" (\\name), "symbol" (\\ + one character), "open"/"close" (unescaped
    braces), "amp" (&) or "dollar" ($). Everything else is skipped.
    """
    i, n = 0, len(latex)
    while i < n:
        ch = latex[i]
        if ch == "\\":
            j = i + 1
            while j < n and latex[j].isascii() and latex[j].isalpha():
                j += 1
            if j > i + 1:
                if j < n and latex[j] == "*" and latex[i + 1 : j] == "operatorname":
                    j += 1
                yield "word", latex[i + 1 : j], j
                i = j
            elif j < n:
                yield "symbol", latex[j], j + 1
                i = j + 1
            else:
                yield "symbol", "", n  # a lone trailing backslash
                i = n
            continue
        kind = {"{": "open", "}": "close", "&": "amp", "$": "dollar"}.get(ch)
        if kind:
            yield kind, ch, i + 1
        i += 1


def _environment_name(latex: str, end: int) -> str | None:
    """Name in `{...}` right after \\begin / \\end (ending at `end`), or None."""
    match = _ENV_NAME.match(latex, end)
    return match.group(1) if match else None


def check_latex(latex: str) -> list[Check]:
    """Allowed commands only, balanced braces/environments, no file or shell commands."""
    if not latex.strip():
        return [_check("latex.not_empty", "empty")]
    checks = [
        _check("latex.not_empty", None),
        _check("latex.length", "too_long" if len(latex) > MAX_LATEX_CHARS else None),
    ]
    depth = 0
    braces_ok = True
    env_stack: list[str] = []
    env_code: str | None = None
    forbidden = unknown = unknown_env = dollar = False
    for kind, value, end in _tokens(latex):
        if kind == "open":
            depth += 1
        elif kind == "close":
            depth -= 1
            braces_ok = braces_ok and depth >= 0
        elif kind == "dollar":
            dollar = True
        elif kind == "symbol":
            unknown = unknown or value not in ALLOWED_CONTROL_SYMBOLS
        elif kind == "word":
            if value in FORBIDDEN_COMMANDS:
                forbidden = True
            elif value not in ALLOWED_COMMANDS:
                unknown = True
            elif value in ENVIRONMENT_COMMANDS:
                name = _environment_name(latex, end)
                if name is None:
                    env_code = "unbalanced_env"
                    continue
                if name not in ALLOWED_ENVIRONMENTS:
                    unknown_env = True
                if value == "begin":
                    env_stack.append(name)
                elif not env_stack or env_stack.pop() != name:
                    env_code = "unbalanced_env"
    if env_stack:
        env_code = "unbalanced_env"
    checks += [
        _check("latex.balanced_braces", None if braces_ok and depth == 0 else "unbalanced_braces"),
        _check("latex.balanced_environments", env_code),
        _check("latex.no_math_delimiters", "math_delimiter" if dollar else None),
        _check("latex.no_forbidden_commands", "forbidden_command" if forbidden else None),
        _check("latex.allowed_commands", "unknown_command" if unknown else None),
        _check("latex.allowed_environments", "unknown_environment" if unknown_env else None),
    ]
    return checks


# ====================================================================== conversion


def _find_environment_end(latex: str, start: int) -> tuple[int, int] | None:
    """Span (start, end) of the \\end{...} closing the environment whose body starts at
    `start`, honouring nesting. None when it is never closed."""
    depth = 1
    for kind, value, end in _tokens(latex[start:]):
        if kind != "word" or value not in ENVIRONMENT_COMMANDS:
            continue
        depth += 1 if value == "begin" else -1
        if depth == 0:
            match = _ENV_NAME.match(latex, start + end)
            stop = match.end() if match else start + end
            return start + end - len("\\end"), stop
    return None


def _max_columns(body: str) -> int:
    """Widest row of an alignment body: top-level `&` count + 1.

    Ampersands and row breaks inside braces or nested environments (an inner `array`)
    belong to those groups, so they are not counted.
    """
    widest = cells = 1
    depth = 0
    for kind, value, _ in _tokens(body):
        if kind == "open":
            depth += 1
        elif kind == "close":
            depth -= 1
        elif kind == "word" and value in ENVIRONMENT_COMMANDS:
            depth += 1 if value == "begin" else -1
        elif depth == 0 and kind == "amp":
            cells += 1
            widest = max(widest, cells)
        elif depth == 0 and kind == "symbol" and value == "\\":
            cells = 1
    return widest


_ALIGN_BEGIN = re.compile(r"\\begin\s*\{\s*(aligned|align\*?|split|gathered)\s*\}(\s*\[[tcb]\])?")


def rewrite_alignment_environments(latex: str) -> str:
    r"""`aligned`/`align`/`align*`/`split`/`gathered` -> `\begin{array}{rl...}`.

    Columns alternate right/left (the amsmath alignment convention), as many as the
    widest row has cells; `gathered` rows are centred. Unclosed environments are left
    alone for `check_latex` and the converter to reject.
    """
    parts: list[str] = []
    pos = 0
    while (match := _ALIGN_BEGIN.search(latex, pos)) is not None:
        closing = _find_environment_end(latex, match.end())
        if closing is None:
            break
        body = rewrite_alignment_environments(latex[match.end() : closing[0]])  # nested
        columns = _max_columns(body)
        if match.group(1) == "gathered":
            spec = "c" * columns
        else:
            spec = "".join("r" if i % 2 == 0 else "l" for i in range(columns))
        parts += [latex[pos : match.start()], f"\\begin{{array}}{{{spec}}}{body}\\end{{array}}"]
        pos = closing[1]
    parts.append(latex[pos:])
    return "".join(parts)


_SERIALIZE_MAX_DEPTH = 200


def _escape_xml(text: str, *, attribute: bool = False) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace('"', "&quot;") if attribute else text


def _local_name(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def serialize_mathml(root: ET.Element) -> str:
    """Serialise a parsed MathML tree: local tag names, one xmlns on the root, escaped
    text and attributes. Used so downstream code only ever sees re-serialised output."""

    def walk(elem: ET.Element, is_root: bool) -> str:
        tag = _local_name(elem.tag)
        attrs = "".join(
            f' {_local_name(k)}="{_escape_xml(v, attribute=True)}"' for k, v in elem.attrib.items()
        )
        if is_root:
            attrs = f' xmlns="{MATHML_NS}"' + attrs
        inner = _escape_xml(elem.text or "") + "".join(
            walk(child, False) + _escape_xml(child.tail or "") for child in elem
        )
        return f"<{tag}{attrs}>{inner}</{tag}>"

    return walk(root, True)


def _parse_xml(text: str) -> ET.Element:
    """defusedxml parse: DTDs, entity declarations and external references refused."""
    return DET.fromstring(text, forbid_dtd=True, forbid_entities=True, forbid_external=True)


def safe_latex_to_mathml(latex: str, display: str = "block") -> str:
    """LaTeX -> MathML via latex2mathml, with the array/aligned workaround.

    Returns MathML with a `<math>` root in the MathML namespace and `display` set.
    Raises `LatexConversionError` (content-free message) when conversion fails.
    """
    if display not in ("block", "inline"):
        raise ValueError("display must be 'block' or 'inline'")
    if len(latex) > MAX_LATEX_CHARS:  # same limit as check_latex; bounds the work below
        raise LatexConversionError("input too long")
    try:
        raw = _latex2mathml_convert(rewrite_alignment_environments(latex), display=display)
    except Exception as exc:  # latex2mathml raises many exception types
        raise LatexConversionError(f"latex2mathml failed ({type(exc).__name__})") from None
    try:
        root = _parse_xml(raw)
    except (ET.ParseError, defusedxml.DefusedXmlException) as exc:
        raise LatexConversionError(f"converter output unparsable ({type(exc).__name__})") from None
    if tree_depth(root) > _SERIALIZE_MAX_DEPTH:  # keeps the recursive serialiser safe
        raise LatexConversionError("converter output too deeply nested")
    if _local_name(root.tag) != "math":
        wrapper = ET.Element(f"{{{MATHML_NS}}}math")
        wrapper.append(root)
        root = wrapper
    root.set("display", display)
    return serialize_mathml(root)


# ========================================================================== MathML

MAX_MATHML_CHARS = 100_000
MAX_MATHML_ELEMENTS = 5_000
MAX_MATHML_DEPTH = 64  # a 10-level continued fraction is ~40

# MathML Core plus the presentation elements common in textbook MathML (mfenced,
# menclose, mlabeledtr, alignment marks). No maction (scripted toggles) and no mglyph
# (loads an image from a URL).
ALLOWED_ELEMENTS = _words(
    """
    math semantics annotation annotation-xml mrow mi mn mo mtext ms mspace msub msup
    msubsup munder mover munderover mmultiscripts mprescripts none mfrac msqrt mroot
    mstyle mpadded mphantom menclose mfenced merror mtable mtr mtd mlabeledtr
    maligngroup malignmark
    """
)
# Layout and meaning attributes only. Deliberately absent: href, src, style, class, id,
# event handlers, mathcolor/mathbackground (could hide content), xlink:*.
ALLOWED_ATTRIBUTES = _words(
    """
    display displaystyle scriptlevel mathvariant mathsize dir fence form stretchy
    symmetric largeop movablelimits accent accentunder separator separators lspace rspace
    minsize maxsize linethickness numalign denomalign bevelled width height depth voffset
    linebreak notation columnalign rowalign columnspacing rowspacing columnlines rowlines
    columnwidth frame framespacing equalrows equalcolumns rowspan columnspan open close
    encoding fontstyle fontweight intent arg alttext
    """
)
_UNSAFE_ATTRIBUTE_NAMES = re.compile(r"^(on.*|href|src|style|class|id|xlink:.*|xmlns:.*)$", re.I)
_UNSAFE_VALUE = re.compile(r"javascript:|vbscript:|data:|url\s*\(|expression\s*\(|[<>]", re.I)

# Elements with a fixed number of children; a mismatch is broken output (e.g. a
# `\frac{a}` with a missing argument converts to a one-child mfrac).
FIXED_ARITY = {
    "mfrac": 2,
    "mroot": 2,
    "msub": 2,
    "msup": 2,
    "msubsup": 3,
    "munder": 2,
    "mover": 2,
    "munderover": 3,
}
_TOKEN_ELEMENTS = frozenset({"mi", "mn", "mo", "mtext", "ms"})
_LATEX_LEFTOVER = re.compile(r"\\[A-Za-z]+|\\[{}]")


def tree_depth(root: ET.Element) -> int:
    """Nesting depth, computed without recursion (input may be adversarially deep)."""
    deepest = 0
    stack = [(root, 1)]
    while stack:
        elem, depth = stack.pop()
        deepest = max(deepest, depth)
        stack.extend((child, depth + 1) for child in elem)
    return deepest


def _attribute_code(elem: ET.Element) -> str | None:
    code = None
    for key, value in elem.attrib.items():
        name = _local_name(key)
        if (
            key.startswith("{")
            or _UNSAFE_ATTRIBUTE_NAMES.match(name)
            or _UNSAFE_VALUE.search(value)
        ):
            return "unsafe_attribute"
        if name not in ALLOWED_ATTRIBUTES:
            code = "unknown_attribute"
    return code


def _is_leftover(elem: ET.Element, name: str) -> bool:
    """Unconverted LaTeX: `\\cmd` text in a token, an merror, or a stray `&` cell mark."""
    if name == "merror":
        return True
    if name not in _TOKEN_ELEMENTS:
        return False
    text = "".join(elem.itertext())
    return bool(_LATEX_LEFTOVER.search(text)) or (name == "mi" and text.strip() == "&")


def check_mathml(mathml: str) -> list[Check]:
    """Well-formed (defusedxml), <math> root in the MathML namespace, known elements only."""
    if len(mathml) > MAX_MATHML_CHARS:
        return [_check("mathml.size", "too_large")]
    try:
        root = _parse_xml(mathml)
    except defusedxml.DefusedXmlException:
        return [_check("mathml.size", None), _check("mathml.well_formed", "forbidden_xml")]
    except ET.ParseError:
        return [_check("mathml.size", None), _check("mathml.well_formed", "not_well_formed")]

    elements = list(root.iter())
    root_code = None
    if _local_name(root.tag) != "math":
        root_code = "wrong_root"
    elif root.tag != f"{{{MATHML_NS}}}math":
        root_code = "missing_namespace"
    element_code = attribute_code = arity_code = leftover_code = None
    for elem in elements:
        name = _local_name(elem.tag)
        if not elem.tag.startswith(f"{{{MATHML_NS}}}") or name not in ALLOWED_ELEMENTS:
            element_code = "unknown_element"
        code = _attribute_code(elem)
        if code == "unsafe_attribute" or (code and attribute_code is None):
            attribute_code = code
        if name in FIXED_ARITY and len(elem) != FIXED_ARITY[name]:
            arity_code = "bad_arity"
        if _is_leftover(elem, name):
            leftover_code = "unconverted_command"
    too_large = len(elements) > MAX_MATHML_ELEMENTS
    return [
        _check("mathml.size", "too_large" if too_large else None),
        _check("mathml.well_formed", None),
        _check("mathml.root", root_code),
        _check("mathml.known_elements", element_code),
        _check("mathml.safe_attributes", attribute_code),
        _check("mathml.depth", "too_deep" if tree_depth(root) > MAX_MATHML_DEPTH else None),
        _check("mathml.structure", arity_code),
        _check("mathml.converted", leftover_code),
    ]


# ======================================================================== alt text

ALT_WARN_CHARS = 150
ALT_MAX_CHARS = 300

_REDUNDANT_PREFIX = re.compile(
    r"^\s*(an?\s+|the\s+)?(image|picture|graphic|photo|photograph)\s+of\b", re.I
)
_FILE_EXTENSION = re.compile(
    r"\b[\w\-]+\.(png|jpe?g|gif|svg|bmp|tiff?|webp|heic|pdf|eps|emf|wmf|ai|psd)\b", re.I
)
# Single-token camera, scan and export names: IMG_2041, DSC0042, figure_3_2, scan-0001.
_FILE_STEM = re.compile(
    r"^(img|dsc|dscn|dcim|pxl|image|fig|figure|scan|screenshot|pic|photo)[\W_]*\d[\w\-]*$"
    r"|^\w+_\w+$",
    re.I,
)
# URLs, data URIs, HTML tags or entities, Markdown links, LaTeX commands.
_URL_OR_MARKUP = re.compile(
    r"https?://|\bwww\.|\bdata:[a-z]+/[a-z]|</?[A-Za-z!][^>]*>|&#?\w+;|\]\(|\\[A-Za-z]+",
    re.I,
)
_PLACEHOLDERS = _words(
    """
    image img picture photo photograph figure fig graph chart diagram graphic illustration
    drawing icon logo alt alttext placeholder untitled description todo tbd none null
    undefined spacer blank
    """
) | {"alt text", "image description", "no description", "figure description"}
# A bare label such as "Figure 2.3" (compared after punctuation is removed: "figure 2 3").
_PLACEHOLDER_LABEL = re.compile(
    r"^(figure|fig|image|chart|graph|diagram|table|photo|picture)[\d\s]*[a-z]?$"
)
_FIGURE_LABEL = re.compile(r"^\s*(figure|fig\.?)\s*[\w]+([.\-]\w+)*\s*[:.]?\s+", re.I)


def _plain(text: str) -> str:
    """Lowercase, punctuation removed, whitespace collapsed (for fuzzy comparisons)."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(" " if unicodedata.category(ch).startswith(("P", "S")) else ch for ch in text)
    return " ".join(text.split())


def _without_figure_label(text: str) -> str:
    return _FIGURE_LABEL.sub("", text, count=1)


def check_alt(alt: str, *, decorative: bool, caption: str | None = None) -> list[Check]:
    """Length, no 'image of', no file names, no URLs/markup, not a caption copy."""
    text = alt.strip()
    if decorative:
        return [_check("alt.decorative", "decorative_with_text" if text else None)]
    if not text:
        return [_check("alt.not_empty", "empty")]
    plain = _plain(text)
    if len(text) > ALT_MAX_CHARS:
        length = _check("alt.length", "too_long")
    elif len(text) > ALT_WARN_CHARS:
        length = _check("alt.length", "too_long", "warning")
    else:
        length = _check("alt.length", None)
    is_filename = bool(_FILE_EXTENSION.search(text)) or (
        " " not in text and bool(_FILE_STEM.match(text))
    )
    is_placeholder = plain in _PLACEHOLDERS or bool(_PLACEHOLDER_LABEL.match(plain))
    is_caption_copy = bool(caption and caption.strip()) and _plain(
        _without_figure_label(text)
    ) == _plain(_without_figure_label(caption or ""))
    return [
        _check("alt.not_empty", None),
        length,
        _check(
            "alt.no_redundant_prefix",
            "redundant_prefix" if _REDUNDANT_PREFIX.match(text) else None,
            "warning",
        ),
        _check("alt.not_filename", "filename" if is_filename else None),
        _check("alt.no_url_or_markup", "url_or_markup" if _URL_OR_MARKUP.search(text) else None),
        _check("alt.not_placeholder", "placeholder" if is_placeholder else None),
        _check("alt.not_caption_copy", "caption_copy" if is_caption_copy else None, "warning"),
    ]
