# ruff: noqa: RUF001
"""Chemistry report fields (`chem_right_excl_times`, `silent_harm`) and the class script's
`--speech` and reader-harm view. Report fields only: the primary numbers do not move."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from readablestem.core.types import Element, Kind
from readablestem.eval import equations_chem as chem
from readablestem.eval import pipeline_eval as pe
from readablestem.pipeline.validate import safe_latex_to_mathml
from test_eval_support import eq_fix, equation

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import dev_chem_classes as dcc

PAGE = "1-1-chemistry"


def tex(latex: str) -> str:
    return safe_latex_to_mathml(latex, "inline")


TIMES = "<math><mo>×</mo></math>"


def element(index: int, text: str) -> Element:
    return Element(
        id=f"p1-e{index}",
        kind=Kind.EQUATION,
        page=1,
        bbox=(0, 0, 10, 10),
        order=index,
        text=text,
        display="inline",
    )


# ------------------------------------------------------------------------ silent_harm


def test_a_wrong_unasked_reading_that_sounds_different_is_silent_harm():
    gold = tex(r"\mathrm{H_2O}")
    assert chem.silent_harm(tex(r"\mathrm{H_3O}"), gold, "optional")
    assert chem.silent_harm(tex(r"\mathrm{H_3O}"), gold, None)


def test_a_reading_the_reviewer_was_asked_about_is_not_silent():
    gold = tex(r"\mathrm{H_2O}")
    assert not chem.silent_harm(tex(r"\mathrm{H_3O}"), gold, "required")


def test_a_right_reading_is_not_harm_whatever_the_markup():
    gold = tex(r"\mathrm{H_2O}")
    assert not chem.silent_harm(tex(r"\text{H}_2\text{O}"), gold, None)


def test_no_reading_kept_is_not_counted_here():
    assert not chem.silent_harm(None, tex(r"\mathrm{H_2O}"), None)


def test_a_prefix_atomic_number_the_page_does_not_show_is_not_harm():
    gold = tex(r"{}_{6}^{14}\mathrm{C}")
    pred = tex(r"{}^{14}\mathrm{C}")
    assert chem.chem_of(pred) != chem.chem_of(gold)
    assert chem.lacks_only_what_the_page_lacks(chem.chem_of(pred), chem.chem_of(gold))
    assert not chem.silent_harm(pred, gold, None)


def test_wrong_on_chem_but_spoken_alike_is_not_harm():
    """Different markup that a listener hears the same is not something they would notice."""
    gold = tex(r"x")
    pred = tex(r"\mathit{x}")
    assert chem.spoken_alike(pred, gold)
    assert not chem.silent_harm(pred, gold, None)


# ---------------------------------------------------------------- the report fields


def chem_item() -> dict:
    gold = [
        equation(PAGE, 0, tex(r"\mathrm{H_2O}"), "inline"),  # read right
        equation(PAGE, 1, tex(r"\mathrm{CO_2}"), "inline"),  # read wrong, not asked
        equation(PAGE, 2, TIMES, "inline"),  # a lone times sign nobody found
        equation(PAGE, 3, tex(r"\mathrm{NaCl}"), "inline"),  # not found, not a times sign
    ]
    elements = [element(0, "H2O"), element(1, "CO2")]
    fixes = {
        "p1-e0": eq_fix("p1-e0", r"\text{H}_2\text{O}"),
        "p1-e1": eq_fix("p1-e1", r"\mathrm{CO_3}"),
    }
    return pe.document_page_item("p", gold, elements, fixes, [], [])


def test_lone_times_is_left_out_of_the_rate_and_counted_beside_it():
    item = chem_item()
    assert len(item["pairs"]) == 2
    assert item["chem_lone_times_gold_ids"] == [g for g in item["missed_gold_ids"] if "002" in g]
    block = chem.chem_right_excl_times([item])
    assert block == {
        "n": 3,
        "right": 1,
        "rate": 1 / 3,
        "lone_times": 1,
        "chem_right_n": 4,
        "chem_right": 1,
        "chem_right_rate": 1 / 4,
    }


def test_silent_harm_is_counted_per_100_gold():
    item = chem_item()
    pairs = {p["gold_id"].rsplit("-", 1)[1]: p for p in item["pairs"]}
    assert pairs["000"]["silent_harm"] is False
    assert pairs["001"]["silent_harm"] is True
    summary = chem.silent_harm_summary([item])
    assert (summary["n"], summary["n_gold"], summary["per_100_gold"]) == (1, 4, 25.0)


def test_the_document_report_carries_the_block_and_the_primary_numbers_are_unchanged():
    item = chem_item()
    metrics = pe.document_metrics([item])
    assert metrics["chemistry"]["chem_right_excl_times"]["lone_times"] == 1
    assert metrics["chemistry"]["silent_harm"]["n"] == 1
    plain = {k: v for k, v in metrics.items() if k != "chemistry"}
    assert plain["matched_exact_lenient"] == 0.0  # primary scorer, as before
    assert plain["gold_equations"] == 4


def test_a_report_without_chemistry_gold_has_no_chemistry_block():
    gold = [equation("2-1-speed", 0, tex(r"x^{2}"), "inline")]
    item = pe.document_page_item(
        "p", gold, [element(0, "x2")], {"p1-e0": eq_fix("p1-e0", r"x^{2}")}, [], []
    )
    assert item["pairs"][0]["silent_harm"] is False
    assert "chemistry" not in pe.document_metrics([item])


# ------------------------------------------------------------ scripts/dev_chem_classes.py


@pytest.fixture
def run_rows(tmp_path):
    item = chem_item()
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    records = [
        equation(PAGE, i, mathml, "inline")
        for i, mathml in enumerate(
            [tex(r"\mathrm{H_2O}"), tex(r"\mathrm{CO_2}"), TIMES, tex(r"\mathrm{NaCl}")]
        )
    ]
    (gold_dir / "g.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    item["id"] = f"book/{PAGE}"
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "items.jsonl").write_text(json.dumps(item) + "\n")
    return gold_dir, folder, item


def test_classes_get_a_harm_label_and_a_listener_cost(run_rows):
    gold_dir, _, item = run_rows
    gold = dcc.dev_chem.load_gold(gold_dir)
    classes = dcc.classify([item], gold, {}, [])
    assert {c for c, _ in classes.values()} <= set(dcc.HARM)
    assert dict(dcc.harm_counts(classes)) == {"no": 2, "yes": 1, "partly": 1}


def test_speech_report_names_the_silent_harm(run_rows):
    gold_dir, _, item = run_rows
    gold = dcc.dev_chem.load_gold(gold_dir)
    classes = dcc.classify([item], gold, {}, [])
    report = dcc.speech_report([item], gold, classes)
    assert report["silent_harm"] == [g for g in classes if g.endswith("001")]
    assert report["per_100_gold"] == 25.0
    assert report["spoken_alike"]["right"]["same"] == 1


def test_main_reads_a_run_folder_and_prints_speech(run_rows, capsys, monkeypatch):
    gold_dir, folder, _ = run_rows
    monkeypatch.setattr(sys, "argv", ["x", "--gold", str(gold_dir), "--speech", f"run={folder}"])
    assert dcc.main() == 0
    out = capsys.readouterr().out
    assert "harms a listener, run:" in out
    assert "speech, run:" in out and '"per_100_gold": 25.0' in out
