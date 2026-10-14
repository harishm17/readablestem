"""mathvariant must survive rendering: Chromium (MathML Core) ignores every value except
"normal", so render.py rewrites styled tokens as Unicode math letters. Found in the first
real eval run (2026-10-08): OpenStax bold vectors were drawn as plain italic, so the
models were scored against a bold style they were never shown."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from readablestem.eval.equations import canonical, score_latex
from readablestem.eval.render import clean_mathml

GOLD = Path(__file__).resolve().parents[1] / "data" / "gold"
NS = 'xmlns="http://www.w3.org/1998/Math/MathML"'


def test_inherited_bold_becomes_math_bold_letters():
    out = clean_mathml(
        f'<math {NS}><mstyle mathvariant="bold"><mi>d</mi><mn>0</mn></mstyle></math>'
    )
    assert "\U0001d41d" in out  # MATHEMATICAL BOLD SMALL D
    assert "\U0001d7ce" in out  # MATHEMATICAL BOLD DIGIT ZERO
    assert "mathvariant" not in out


def test_normal_mi_keeps_the_one_supported_value():
    out = clean_mathml(f'<math {NS}><mi mathvariant="normal">sin</mi></math>')
    assert 'mathvariant="normal"' in out and "sin" in out


def test_letterlike_holes_and_unstyleable_characters():
    # Italic small h and double-struck R live in Letterlike Symbols (U+210E, U+211D), not
    # in the math block; an operator has no styled form and stays as it is.
    out = clean_mathml(
        f'<math {NS}><mi mathvariant="italic">h</mi><mi mathvariant="double-struck">R</mi>'
        '<mo mathvariant="bold">+</mo></math>'
    )
    assert "\u210e" in out and "\u211d" in out and ">+<" in out


def _gold_equations() -> list[dict]:
    rows = []
    for path in sorted(GOLD.glob("openstax/*/*.jsonl")):
        rows += [
            r for r in map(json.loads, path.read_text().splitlines()) if r["kind"] == "equation"
        ]
    return rows


@pytest.mark.parametrize("lenient", [False, True])
def test_cleaning_never_changes_what_the_scorer_sees(lenient):
    equations = _gold_equations()
    assert equations
    for record in equations:
        assert canonical(clean_mathml(record["mathml"]), lenient=lenient) == canonical(
            record["mathml"], lenient=lenient
        ), record["id"]


def test_scorer_reads_a_bold_word_as_letters_plus_bold():
    gold = (
        f'<math {NS}><msub><mi mathvariant="bold">v</mi>'
        '<mtext mathvariant="bold">avg</mtext></msub></math>'
    )
    assert score_latex(r"\mathbf{v}_\textbf{avg}", gold).exact_lenient
