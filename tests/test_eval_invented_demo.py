"""The rules on a real a11y-stem output: the committed demo (OpenStax Physics 2.3, CC BY 4.0,
converted with the real models on 2026-10-09) against the PDF it was converted from.

a11y-stem copies prose from the text layer, so everything the rules can look at should be
supported; what is not is the text the tool wrote itself (the about section). If the demo
is rebuilt, this test says what changed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a11y_stem.eval.invented import pool, read, score, text

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "src" / "a11y_stem" / "web" / "static" / "demo"
GOLD = ROOT / "data" / "gold"


@pytest.fixture(scope="module")
def demo_score():
    reference = text.Reference.from_pdf((DEMO / "source.pdf").read_bytes())
    output = read.read_html((DEMO / "index.html").read_text(encoding="utf-8"))
    gold = score.gold_equation_mathml(GOLD, ["2-3-position-vs-time-graphs"], book="physics")
    return score.score_output(reference, output, gold_equations=gold)


def test_nothing_the_tool_copied_from_the_pdf_is_unsupported(demo_score):
    for kind in ("sentence", "number", "heading", "equation_aligned", "equation_unaligned"):
        counts = demo_score.counts[kind]
        assert counts.n > 0, kind
        assert counts.unsupported - counts.unsupported_authored == 0, kind


def test_the_only_unsupported_text_is_the_about_section_the_tool_writes(demo_score):
    flagged = [v for v in demo_score.verdicts if v.unsupported]
    assert flagged and all(v.authored for v in flagged)
    assert {v.kind for v in flagged} == {"sentence", "heading"}
    assert demo_score.counts["sentence"].unsupported == 4  # the about section's four sentences
    assert demo_score.counts["heading"].unsupported == 1


def test_equations_align_to_gold_and_add_no_symbol(demo_score):
    assert demo_score.aligned_pairs >= 8
    assert demo_score.added_symbols == 0 and demo_score.counts["equation_aligned"].unsupported == 0


def test_the_seven_figure_descriptions_are_counted_for_the_judge(demo_score):
    assert demo_score.counts["figure_description"].n == 7
    assert demo_score.descriptions_by_source == {"alt": 7}


def test_content_only_rate_of_the_demo_is_zero_on_every_type(demo_score):
    entries = [{"section": "2-3", "run": "1", "score": demo_score.to_dict()}]
    summary = pool.summarize(entries)
    for kind, row in summary["content_only"].items():
        assert row["unsupported"] == 0, kind
