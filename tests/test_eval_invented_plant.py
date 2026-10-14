"""Inventions planted by hand: the plan is applied, found, and the blind spot is measured."""

from __future__ import annotations

import json

import pytest

from readablestem.cli.main import main
from readablestem.eval.invented import plant
from readablestem.eval.invented import text as T

PAGE = """2.1 Speed and Velocity
Average speed is the distance traveled divided by the time it takes to travel it.
A car travels 150 km in 3.2 h, so its average speed is about 47 km/h on that trip.
A scalar quantity is fully described by its magnitude while a vector also needs a direction.
Newton's second law says that the net force on an object equals its mass times acceleration.
v = d / t
Access for free at openstax.org. Physics (OpenStax), CC BY 4.0. Page 1 of 1
"""
REFERENCE = T.Reference.from_pages([PAGE])

HTML = (
    "<!doctype html><html lang='en'><body>"
    "<h2>2.1 Speed and Velocity</h2>"
    "<p>Average speed is the distance traveled divided by the time it takes to travel it.</p>"
    "<p>A car travels 150 km in 3.2 h, so its average speed is about 47 km/h on that trip.</p>"
    "<p>A scalar quantity is fully described by its magnitude while a vector also needs a "
    "direction.</p>"
    "<p>Newton's second law says that the net force on an object equals its mass times "
    "acceleration.</p>"
    "<math><mi>v</mi><mo>=</mo><mfrac><mi>d</mi><mi>t</mi></mfrac></math>"
    "</body></html>"
)

PLAN = {
    "sentences": ["Students usually find it easier to learn this topic after watching a video."],
    "headings": ["Study Tips for This Section"],
    "equations": [
        "<math><mi>K</mi><mo>=</mo><mfrac><mn>1</mn><mn>2</mn></mfrac><mi>m</mi>"
        "<msup><mi>v</mi><mn>2</mn></msup></math>"
    ],
    "edits": [
        {"kind": "number", "find": "150 km in 3.2 h", "replace": "150 km in 3.9 h"},
        {"kind": "word", "find": "A scalar quantity is", "replace": "A vector quantity is"},
    ],
}


def test_the_plan_is_applied_and_each_planted_unit_is_listed():
    changed, planted = plant.apply_plan(HTML, PLAN)
    assert "3.9 h" in changed and "<h3>Study Tips for This Section</h3>" in changed
    assert "Students usually find it easier" in changed
    assert sorted(p.kind for p in planted) == ["equation", "heading", "number", "sentence", "word"]
    [number] = [p for p in planted if p.kind == "number"]
    assert number.number == "3.9"


def test_an_edit_whose_text_is_not_in_the_output_is_an_error():
    with pytest.raises(ValueError, match="not found"):
        plant.apply_plan(HTML, {"edits": [{"kind": "word", "find": "absent text", "replace": "x"}]})


def test_inserted_lines_are_spread_through_the_paragraphs():
    plan = {
        "sentences": [f"Planted sentence number {i} says nothing the pdf says." for i in range(4)]
    }
    changed, _ = plant.apply_plan(HTML, plan)
    positions = [changed.index(f"Planted sentence number {i}") for i in range(4)]
    assert positions == sorted(positions) and len(set(positions)) == 4


def test_planted_text_is_found_and_the_blind_spot_is_measured():
    # One word changed inside a copied sentence keeps most of its 4-grams: not found.
    swap = {"kind": "word", "find": "its magnitude while", "replace": "its direction while"}
    plan = {**PLAN, "edits": [PLAN["edits"][0], swap]}
    result = plant.self_check(REFERENCE, HTML, plan)
    assert result["found"]["sentence"] == result["planted"]["sentence"] == 1
    assert result["found"]["heading"] == 1
    assert result["found"]["equation"] == 1
    assert result["found"]["number"] == 1  # 3.9 is not printed in the PDF
    assert result["found"]["word"] == 0 and result["planted"]["word"] == 1
    assert result["recall"]["word"] == 0.0
    assert result["newly_flagged_unexplained"] == 0
    assert result["plan_in_reference"]["numbers_the_pdf_prints"] == 0


def test_a_planted_number_the_pdf_prints_anyway_is_reported():
    plan = {"edits": [{"kind": "number", "find": "150 km in 3.2 h", "replace": "150 km in 47 h"}]}
    result = plant.self_check(REFERENCE, HTML, plan)
    assert result["plan_in_reference"]["numbers_the_pdf_prints"] == 1
    assert result["found"]["number"] == 0


def test_a_symbol_added_to_a_real_equation_is_found_through_the_gold_equation():
    gold = ["<math><mi>v</mi><mo>=</mo><mfrac><mi>d</mi><mi>t</mi></mfrac></math>"]
    plan = {
        "edits": [
            {
                "kind": "equation_symbol",
                "find": "<mfrac><mi>d</mi><mi>t</mi></mfrac></math>",
                "replace": "<mfrac><mi>d</mi><mi>t</mi></mfrac><mo>+</mo><mi>q</mi></math>",
            }
        ]
    }
    result = plant.self_check(REFERENCE, HTML, plan, gold_equations=gold)
    assert result["found"] == {"equation_symbol": 1}


def test_load_plan_rejects_an_edit_without_a_kind(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"edits": [{"find": "a", "replace": "b"}]}))
    with pytest.raises(ValueError, match="kind"):
        plant.load_plan(path)


def test_the_selfcheck_command_prints_a_table_and_writes_json(tmp_path, capsys):
    import pymupdf

    document = pymupdf.open()
    page = document.new_page()
    for i, line in enumerate(PAGE.splitlines()):
        page.insert_text((40, 60 + 18 * i), line, fontsize=9)
    pdf = tmp_path / "s.pdf"
    document.save(pdf)
    document.close()
    out = tmp_path / "out.html"
    out.write_text(HTML)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(PLAN))
    result = tmp_path / "result.json"
    code = main(
        [
            "eval",
            "invented",
            "selfcheck",
            "--pdf",
            str(pdf),
            "--output",
            str(out),
            "--plan",
            str(plan),
            "--out",
            str(result),
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "| sentence | 1 | 1 | 100% |" in printed
    assert "| word | 1 | 0 | 0% |" in printed
    assert json.loads(result.read_text())["planted"]["sentence"] == 1


def test_the_selfcheck_command_reports_a_missing_edit_as_an_error(tmp_path, capsys):
    (tmp_path / "s.pdf").write_bytes(b"x")  # never read: the plan fails first
    code = main(
        [
            "eval",
            "invented",
            "selfcheck",
            "--pdf",
            str(tmp_path / "missing.pdf"),
            "--output",
            str(tmp_path / "o.html"),
            "--plan",
            str(tmp_path / "p.json"),
        ]
    )
    assert code == 2
    assert "file not found" in capsys.readouterr().err
