"""Validators: LaTeX allowlist, safe conversion, MathML allowlist, alt-text rules.

Every failure is asserted by category code, because codes (never content) are what gets
logged and aggregated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from latex2mathml.converter import convert

from a11y_stem.eval.equations import canonical, score_mathml
from a11y_stem.eval.gold_scoring import iter_gold_records
from a11y_stem.pipeline import validate
from a11y_stem.pipeline.validate import (
    LatexConversionError,
    check_alt,
    check_latex,
    check_mathml,
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


def test_checks_are_one_per_rule_with_unique_names():
    checks = check_latex(r"\frac{a}{b}")
    names = [c.name for c in checks]
    assert len(names) == len(set(names)) >= 8
    assert all(c.name.startswith("latex.") for c in checks)


@pytest.mark.parametrize("latex", ["", "   ", "\n"])
def test_empty_latex(latex):
    assert failed_codes(check_latex(latex)) == {"empty"}


def test_too_long_latex():
    assert "too_long" in failed_codes(check_latex("x+" * 1001))
    assert "too_long" not in failed_codes(check_latex("x+" * 999 + "x"))


@pytest.mark.parametrize("latex", [r"\frac{a}{b", "x}", "}{", r"\sqrt{x"])
def test_unbalanced_braces(latex):
    assert "unbalanced_braces" in failed_codes(check_latex(latex))


def test_escaped_braces_are_not_grouping():
    assert all(c.passed for c in check_latex(r"\left\{ x \right\}"))


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


@pytest.mark.parametrize("latex", [r"\foo x", r"\color{red}{x}", r"\label{eq1} x", r"\not= x"])
def test_unknown_commands(latex):
    assert "unknown_command" in failed_codes(check_latex(latex))


@pytest.mark.parametrize(
    "latex",
    [r"\begin{equation} x \end{equation}", r"\begin{tabular}{c} x \end{tabular}"],
)
def test_unknown_environments(latex):
    assert "unknown_environment" in failed_codes(check_latex(latex))


@pytest.mark.parametrize(
    "env", ["array", "matrix", "pmatrix", "bmatrix", "vmatrix", "cases", "aligned", "align*"]
)
def test_allowed_environments(env):
    spec = "{cc}" if env == "array" else ""
    latex = rf"\begin{{{env}}}{spec} a & b \\ c & d \end{{{env}}}"
    assert all(c.passed for c in check_latex(latex)), failed_codes(check_latex(latex))


def test_dollar_delimiters_flagged_but_escaped_dollar_allowed():
    assert "math_delimiter" in failed_codes(check_latex("$x$"))
    assert all(c.passed for c in check_latex(r"\$5"))


def test_check_messages_never_contain_the_latex():
    secret = r"\input{secret-file-name}"
    for check in check_latex(secret):
        assert "secret" not in check.model_dump_json()


# Structural commands that only make sense with a partner or inside an environment.
_STRUCTURAL = {
    "left", "right", "middle", "begin", "end", "limits", "nolimits", "hspace", "hline",
    "nonumber", "notag", "displaystyle", "textstyle", "pmod", "mod",
    "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr", "biggl", "biggr",
    "Biggl", "Biggr",
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


def test_forbidden_and_allowed_lists_do_not_overlap():
    assert not validate.FORBIDDEN_COMMANDS & validate.ALLOWED_COMMANDS


# ------------------------------------------------------------- safe_latex_to_mathml


def test_conversion_sets_namespace_and_display():
    block = safe_latex_to_mathml("x = 1")
    inline = safe_latex_to_mathml("x = 1", "inline")
    assert block.startswith(f'<math {NS} display="block">')
    assert 'display="inline"' in inline
    assert all(c.passed for c in check_mathml(block))


def test_conversion_rejects_bad_display():
    with pytest.raises(ValueError):
        safe_latex_to_mathml("x", "full")


@pytest.mark.parametrize("latex", [r"\left( x", r"\begin{array}{c} x", "x^"])
def test_conversion_errors_are_content_free(latex):
    with pytest.raises(LatexConversionError) as exc:
        safe_latex_to_mathml(latex)
    assert latex not in str(exc.value)
    assert exc.value.__cause__ is None  # the converter's message (with the input) is dropped


def test_aligned_becomes_a_table_not_a_flat_row():
    raw = convert(r"\begin{aligned} a &= b \\ &= c \end{aligned}")
    assert "<mtable" not in raw and "<mi>&</mi>" in raw  # the latex2mathml 3.81.1 bug
    mathml = safe_latex_to_mathml(r"\begin{aligned} a &= b \\ &= c \end{aligned}")
    assert mathml.count("<mtr>") == 2
    assert mathml.count('columnalign="right"') == 2 and mathml.count('columnalign="left"') == 2
    assert all(c.passed for c in check_mathml(mathml))


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


def test_deeply_nested_alignment_converts_but_is_rejected():
    latex = r"\begin{aligned}" * 60 + "x" + r"\end{aligned}" * 60
    assert "too_deep" in failed_codes(check_mathml(safe_latex_to_mathml(latex)))


def test_many_alignment_blocks_in_sequence():
    latex = " + ".join([r"\begin{aligned} a &= b \end{aligned}"] * 40)
    assert validate.rewrite_alignment_environments(latex).count(r"\begin{array}{rl}") == 40


def test_unclosed_alignment_is_left_for_the_checks():
    latex = r"\begin{aligned} a &= b"
    assert validate.rewrite_alignment_environments(latex) == latex
    assert "unbalanced_env" in failed_codes(check_latex(latex))


def test_aligned_transcription_matches_openstax_table_structure():
    """`aligned` (rewritten) and `array{rcl}` give the same tree once the relation sits in
    its own column; the prompt asks for `rcl` so the output matches OpenStax."""
    gold = GOLD["openstax/physics/2-2-speed-and-velocity/eq-005"]["mathml"]
    rcl = next(r["latex"] for r in SAMPLE_LATEX if r["id"].endswith("speed-and-velocity/eq-005"))
    assert score_mathml(safe_latex_to_mathml(rcl), gold).exact_lenient
    aligned = (
        r"\begin{aligned} v_\text{avg} & = & \frac{\text{distance}}{\text{time}} \\ "
        r"& = & \frac{150\ \text{km}}{3.2\ \text{h}} \\ & = & 47\ \text{km/h}. \end{aligned}"
    )
    assert canonical(safe_latex_to_mathml(aligned)) == canonical(safe_latex_to_mathml(rcl))


@pytest.mark.parametrize("row", SAMPLE_LATEX, ids=lambda r: r["id"].rsplit("/", 2)[-2][:3])
def test_sample_latex_converts_to_valid_mathml(row):
    assert all(c.passed for c in check_mathml(safe_latex_to_mathml(row["latex"])))


# ----------------------------------------------------------------------- check_mathml


def test_gold_mathml_passes():
    for record in GOLD.values():
        checks = check_mathml(record["mathml"])
        assert all(c.passed for c in checks), (record["id"], failed_codes(checks))


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
    ],
)
def test_mathml_failures(mathml, code):
    checks = check_mathml(mathml)
    assert code in failed_codes(checks)
    assert has_errors(checks)


def test_converter_leftovers_are_caught():
    """Inputs that latex2mathml converts without complaint but wrongly."""
    assert "unconverted_command" in failed_codes(check_mathml(convert(r"\foo x")))
    assert "bad_arity" in failed_codes(check_mathml(convert(r"\frac{a}")))
    assert "unsafe_attribute" in failed_codes(check_mathml(convert(r"\href{https://x.org}{y}")))


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


def test_adversarially_deep_mathml_does_not_crash():
    deep = f"<math {NS}>" + "<mrow>" * 5_000 + "</mrow>" * 5_000 + "</math>"
    assert "too_deep" in failed_codes(check_mathml(deep))


# -------------------------------------------------------------------------- check_alt


GOOD_ALT = "Line graph of position versus time: position rises steadily, then levels off at 5 km."


def test_good_alt_passes_everything():
    checks = check_alt(GOOD_ALT, decorative=False, caption="Figure 2.3 The drive to school.")
    assert all(c.passed for c in checks)
    assert {c.name for c in checks} >= {"alt.length", "alt.not_filename", "alt.not_caption_copy"}


def test_decorative():
    assert all(c.passed for c in check_alt("", decorative=True))
    checks = check_alt("A border", decorative=True)
    assert failed_codes(checks) == {"decorative_with_text"} and has_errors(checks)


def test_empty_alt_on_informative_figure():
    checks = check_alt("  ", decorative=False)
    assert failed_codes(checks) == {"empty"} and has_errors(checks)


def test_length_is_a_warning_then_an_error():
    warn = check_alt("A bar chart. " + "x" * 150, decorative=False)
    assert failed_codes(warn) == {"too_long"} and not has_errors(warn)
    error = check_alt("A bar chart. " + "x" * 300, decorative=False)
    assert failed_codes(error) == {"too_long"} and has_errors(error)


@pytest.mark.parametrize(
    "alt",
    ["Image of a car on a road", "picture of Mars", "A photo of a jet car", "Graphic of a vector"],
)
def test_redundant_prefix_is_a_warning(alt):
    checks = check_alt(alt, decorative=False)
    assert failed_codes(checks) == {"redundant_prefix"} and not has_errors(checks)


@pytest.mark.parametrize(
    "alt", ["IMG_2041.jpg", "figure_3_2", "DSC0042", "chart.png", "See graph-2.svg for details"]
)
def test_file_names(alt):
    checks = check_alt(alt, decorative=False)
    assert "filename" in failed_codes(checks) and has_errors(checks)


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


@pytest.mark.parametrize(
    ("alt", "caption"),
    [
        ("The drive to school.", "The drive to school"),
        ("the DRIVE to school", "Figure 2.3 The drive, to school."),
    ],
)
def test_caption_copy_is_a_warning(alt, caption):
    checks = check_alt(alt, decorative=False, caption=caption)
    assert failed_codes(checks) == {"caption_copy"} and not has_errors(checks)


def test_data_word_is_not_a_url():
    checks = check_alt("Bar chart of survey data: most students walk to school.", decorative=False)
    assert all(c.passed for c in checks)


def test_openstax_alt_text_has_no_error_except_length():
    """Real alt text from the gold set: long, but never a file name, URL or placeholder."""
    figures = list(iter_gold_records(REPO / "data" / "gold", kind="figure"))
    assert figures
    for record in figures:
        for image in record["images"]:
            codes = failed_codes(
                check_alt(image["alt"], decorative=False, caption=record["caption"])
            )
            assert codes <= {"too_long", "redundant_prefix"}, codes
