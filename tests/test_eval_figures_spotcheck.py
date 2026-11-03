"""`eval figures-spotcheck`: the draw, the blind sheet, the key, the report (offline)."""

from __future__ import annotations

import csv
import json

import pytest

from readablestem.cli.main import main
from readablestem.eval import figures_spotcheck as spot


def item(i: int, *, passed: bool = True, table_ok: bool | None = None, outcome: str = "judged"):
    return {
        "id": f"fig-{i:02d}",
        "page_slug": "page",
        "figure_number": str(i),
        "image_url": f"https://example.org/{i}.jpg",
        "caption": f"Caption {i}",
        "long_description": f"A long description {i}.",
        "data_table": "x | y" if table_ok is not None else None,
        "long_judge_outcome": outcome,
        "long_pass": passed if outcome == "judged" else None,
        "long_accurate": passed,
        "long_complete": passed,
        "long_invented_values": not passed,
        "table_values_ok": table_ok,
    }


def run_items(passes: int = 20, fails: int = 3):
    items = [item(i) for i in range(passes)]
    items += [item(100 + i, passed=False) for i in range(fails)]
    items.append(item(200, outcome="judge_error"))
    items.append(item(201, outcome="not_judged"))
    return items


def test_a_fail_is_a_failed_description_or_wrong_table_values():
    assert spot.is_fail(item(1, passed=False))
    assert spot.is_fail(item(1, passed=True, table_ok=False))
    assert not spot.is_fail(item(1, passed=True, table_ok=True))
    assert not spot.is_fail(item(1, passed=True))


def test_the_draw_takes_every_fail_and_ten_passes_and_leaves_out_ungraded_figures():
    drawn = spot.draw(run_items(), seed="s")
    ids = {i["id"] for i in drawn["chosen"]}
    assert {"fig-100", "fig-101", "fig-102"} <= ids  # every fail
    assert len(drawn["sampled_passes"]) == 10 and len(ids) == 13
    assert "fig-200" not in ids and "fig-201" not in ids
    assert drawn["counts"] == {
        "items": 25,
        "judged": 23,
        "not_judged": 2,
        "judge_fails": 3,
        "judge_passes": 20,
        "passes_sampled": 10,
    }


def test_the_draw_is_seeded_and_does_not_depend_on_input_order():
    items = run_items()
    a = spot.draw(items, seed="s")
    b = spot.draw(list(reversed(items)), seed="s")
    assert a["sampled_passes"] == b["sampled_passes"]
    assert [i["id"] for i in a["chosen"]] == [i["id"] for i in b["chosen"]]
    assert spot.draw(items, seed="other")["sampled_passes"] != a["sampled_passes"]


def test_fewer_passes_than_asked_takes_all_of_them_and_zero_takes_only_fails():
    few = spot.draw(run_items(passes=4), seed="s")
    assert len(few["sampled_passes"]) == 4
    only_fails = spot.draw(run_items(), passes=0, seed="s")
    assert [i["id"] for i in only_fails["chosen"]] == sorted(
        only_fails["fails"], key=lambda i: spot._order("s|sheet", i)
    )
    with pytest.raises(ValueError):
        spot.draw(run_items(), passes=-1, seed="s")


def test_the_sheet_is_blind_and_the_key_holds_the_verdicts(tmp_path):
    sheet, key = tmp_path / "sheet.csv", tmp_path / "sheet.key.json"
    counts = spot.write_sheet(run_items(), sheet, key, passes=10, seed="s")
    assert counts["judge_fails"] == 3
    header = sheet.read_text(encoding="utf-8-sig").splitlines()[0]
    assert "judge" not in header and "pass" not in header  # no verdict to anchor the label
    with sheet.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 13
    assert all(r["human_accurate"] == "" and r["long_description"] for r in rows)
    assert set(spot.SHEET_FIELDS) == set(rows[0])
    verdicts = json.loads(key.read_text())["verdicts"]
    assert sorted(v["group"] for v in verdicts.values()).count("fail") == 3
    assert {r["id"] for r in rows} == set(verdicts)
    # fails are not listed first: the sheet order is the seeded mix
    groups = [verdicts[r["id"]]["group"] for r in rows]
    assert groups != sorted(groups)


def test_jsonl_sheets_work_too(tmp_path):
    sheet = tmp_path / "sheet.jsonl"
    spot.write_sheet(run_items(), sheet, tmp_path / "k.json", passes=2, seed="s")
    rows = [json.loads(line) for line in sheet.read_text().splitlines()]
    assert len(rows) == 5


def label(item_id: str, accurate="yes", complete="yes", invented="no", table=""):
    return {
        "id": item_id,
        "human_accurate": accurate,
        "human_complete": complete,
        "human_invented_values": invented,
        "human_table_values_ok": table,
    }


def test_human_verdict_needs_the_three_answers_and_reads_the_table_answer():
    assert spot.human_verdict(label("a")) is True
    assert spot.human_verdict(label("a", complete="no")) is False
    assert spot.human_verdict(label("a", invented="yes")) is False
    assert spot.human_verdict(label("a", accurate="")) is None
    assert spot.human_verdict(label("a", table="no")) is False
    assert spot.human_verdict(label("a", table="yes")) is True
    with pytest.raises(ValueError):
        spot.human_verdict(label("a", accurate="maybe"))


def test_report_counts_confirmed_fails_and_missed_passes_with_n_and_intervals(tmp_path):
    sheet, key_path = tmp_path / "sheet.csv", tmp_path / "sheet.key.json"
    spot.write_sheet(run_items(), sheet, key_path, passes=10, seed="s")
    key = spot.load_key(key_path)
    verdicts = key["verdicts"]
    fails = sorted(i for i, v in verdicts.items() if v["group"] == "fail")
    passes = sorted(i for i, v in verdicts.items() if v["group"] == "pass")
    labels = [label(fails[0], accurate="no"), label(fails[1], invented="yes")]
    labels.append(label(fails[2]))  # the person would pass what the judge failed
    labels += [label(passes[0], complete="no")]  # a pass the person fails: a miss
    labels += [label(p) for p in passes[1:8]]
    labels.append(label(passes[8], accurate=""))  # not answered yet
    rep = spot.report(key, labels)
    assert rep["fails_confirmed"]["k"] == 2 and rep["fails_confirmed"]["n"] == 3
    assert rep["passes_missed"]["k"] == 1 and rep["passes_missed"]["n"] == 8
    low, high = rep["passes_missed"]["wilson95"]
    assert 0 < low < 1 / 8 < high < 1
    assert rep["unlabelled"] == 2  # one incomplete row, one pass never listed in the file
    assert rep["disagreements"] == sorted([fails[2], passes[0]])
    text = spot.format_report(rep)
    assert "2 of 3" in text and "1 of 8" in text and "Wilson" in text
    assert fails[2] in text


def test_report_ignores_ids_that_are_not_in_the_key():
    key = {"seed": "s", "counts": spot.draw([], seed="s")["counts"], "verdicts": {}}
    rep = spot.report(key, [label("stranger")])
    assert rep["ids_not_in_key"] == 1 and rep["fails_confirmed"]["n"] == 0
    assert rep["fails_confirmed"]["wilson95"] is None
    assert "n/a" in spot.format_report(rep)


def test_load_key_refuses_other_files(tmp_path):
    other = tmp_path / "x.json"
    other.write_text('{"hello": 1}')
    with pytest.raises(ValueError, match="not a figures-spotcheck key"):
        spot.load_key(other)


def test_cli_writes_the_sheet_then_scores_the_labels(tmp_path, capsys):
    items = tmp_path / "judged.jsonl"
    items.write_text("\n".join(json.dumps(i) for i in run_items()) + "\n")
    sheet = tmp_path / "out" / "sheet.csv"
    assert main(["eval", "figures-spotcheck", "--items", str(items), "--sheet", str(sheet)]) == 0
    out = capsys.readouterr().out
    assert "3 judge fails, 10 of 20 passes; 2 figures not graded" in out
    key = sheet.with_suffix(".key.json")
    assert key.exists()
    with sheet.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row.update(human_accurate="yes", human_complete="yes", human_invented_values="no")
    with sheet.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=spot.SHEET_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    report_json = tmp_path / "report.json"
    args = ["eval", "figures-spotcheck", "--key", str(key), "--labels", str(sheet)]
    assert main([*args, "--out", str(report_json)]) == 0
    assert "judge fails the person also fails | 0 of 3" in capsys.readouterr().out
    assert json.loads(report_json.read_text())["passes_missed"]["n"] == 10


def test_cli_needs_inputs_and_reports_bad_ones(tmp_path, capsys):
    assert main(["eval", "figures-spotcheck"]) == 2
    assert "give --items and --sheet" in capsys.readouterr().err
    assert main(["eval", "figures-spotcheck", "--items", str(tmp_path / "a.jsonl")]) == 2
    assert "--items needs --sheet" in capsys.readouterr().err
    missing = str(tmp_path / "nope.jsonl")
    assert main(["eval", "figures-spotcheck", "--items", missing, "--sheet", "s.csv"]) == 2
    assert "file not found" in capsys.readouterr().err
    items = tmp_path / "j.jsonl"
    items.write_text(json.dumps(item(1)) + "\n")
    args = ["eval", "figures-spotcheck", "--items", str(items), "--sheet", str(tmp_path / "s.csv")]
    assert main([*args, "--passes", "-1"]) == 2
    assert "passes must be 0 or more" in capsys.readouterr().err
