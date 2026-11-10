"""`eval figures-spotcheck`: the draw, the blind sheet, the key, the report (offline)."""

from __future__ import annotations

import csv
import json

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


def label(item_id: str, accurate="yes", complete="yes", invented="no", table=""):
    return {
        "id": item_id,
        "human_accurate": accurate,
        "human_complete": complete,
        "human_invented_values": invented,
        "human_table_values_ok": table,
    }


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
