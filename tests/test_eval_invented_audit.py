"""The audit: a blind sample, and precision and recall from a person's labels."""

from __future__ import annotations

import csv
import json

import pytest

from readablestem.eval.invented import audit
from readablestem.eval.invented.score import Verdict
from readablestem.eval.invented.text import Reference

REFERENCE = Reference.from_pages(
    ["Average speed is the distance traveled divided by the time.\nA drop of 5 degrees today."]
)


def verdicts(n_flagged, n_unflagged, kind="sentence"):
    out = []
    for i in range(n_flagged):
        out.append(Verdict(kind, i, f"invented words number {i} appear here", True))
    for i in range(n_unflagged):
        out.append(Verdict(kind, n_flagged + i, f"average speed is the distance {i}", False))
    return out


def sources():
    return [
        audit.AuditSource("alpha", "s1", "1", verdicts(80, 120), REFERENCE),
        audit.AuditSource("alpha", "s2", "1", verdicts(10, 10, "heading"), REFERENCE),
        audit.AuditSource("beta", "s1", "1", verdicts(10, 0), REFERENCE),
    ]


def test_the_sample_takes_up_to_fifty_flagged_and_fifty_unflagged_per_system_and_type():
    items, meta = audit.build_sample(sources())
    strata = meta["strata"]
    assert strata["alpha|sentence"] == {
        "flagged": 80,
        "sampled_flagged": 50,
        "unflagged": 120,
        "sampled_unflagged": 50,
    }
    assert strata["alpha|heading"]["sampled_flagged"] == 10
    assert strata["beta|sentence"] == {"flagged": 10, "sampled_flagged": 10}
    assert len(items) == 50 + 50 + 10 + 10 + 10
    assert len({item.id for item in items}) == len(items)


def test_the_sample_is_reproducible_and_the_order_seed_changes_only_the_order():
    first, _ = audit.build_sample(sources())
    again, _ = audit.build_sample(sources())
    assert [(i.id, i.text) for i in first] == [(i.id, i.text) for i in again]
    reordered, _ = audit.build_sample(sources(), order_seed="another order")
    assert {i.text for i in first} == {i.text for i in reordered}
    assert [i.text for i in first] != [i.text for i in reordered]
    other, _ = audit.build_sample(sources(), seed="another sample")
    assert {i.text for i in first} != {i.text for i in other}


def test_the_sheet_names_no_system_and_does_not_say_what_the_rule_flagged(tmp_path):
    items, meta = audit.build_sample(sources())
    sheet, key = tmp_path / "sheet.csv", tmp_path / "key.json"
    audit.write_sheet(items, sheet)
    audit.write_key(items, meta, key)
    with sheet.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == audit.SHEET_FIELDS
    raw = sheet.read_text()
    assert "alpha" not in raw and "beta" not in raw
    assert "flagged" not in raw and "unsupported" not in raw
    assert all(row["label"] == "" for row in rows)
    data = json.loads(key.read_text())
    some = next(iter(data["items"].values()))
    assert set(some) == {"system", "section", "run", "kind", "index", "flagged"}
    assert data["seed"] == audit.SAMPLE_SEED and data["order_seed"] == audit.ORDER_SEED


def test_the_nearest_stretch_of_the_pdf_is_shown_to_help_the_person_labelling():
    near = audit.nearest_in_pdf("the distance traveled divided by the time", REFERENCE)
    assert "Average speed is the distance traveled" in near
    assert audit.nearest_in_pdf("", REFERENCE) == ""
    assert audit.nearest_in_pdf("zzz qqq", REFERENCE) == ""


def test_labels_are_read_and_a_label_outside_the_three_is_an_error(tmp_path):
    sheet = tmp_path / "s.csv"
    sheet.write_text(
        "id,type,text,nearest_in_pdf,label,note\nu0001,x,t,n,Invented ,\nu0002,x,t,n,,\n"
    )
    assert audit.read_labels(sheet) == {"u0001": "invented"}
    sheet.write_text("id,type,text,nearest_in_pdf,label,note\nu0001,x,t,n,maybe,\n")
    with pytest.raises(ValueError, match="not one of"):
        audit.read_labels(sheet)


def test_precision_recall_and_the_audited_rate_from_labels():
    # 100 flagged, 900 unflagged; 40 of 50 sampled flagged and 5 of 50 sampled unflagged invented
    row = audit.audit_stratum([True] * 40 + [False] * 10, [True] * 5 + [False] * 45, 100, 900)
    assert row["precision"] == pytest.approx(0.8)
    assert row["miss_rate"] == pytest.approx(0.1)
    assert row["recall"] == pytest.approx(80 / (80 + 90))
    assert row["raw_rate"] == pytest.approx(0.1)
    assert row["audited_rate"] == pytest.approx(170 / 1000)
    low, high = row["recall_bootstrap95"]
    assert low < row["recall"] < high
    low, high = row["precision_wilson95"]
    assert low < 0.8 < high
    assert row["population"] == {"flagged": 100, "unflagged": 900}


def test_recall_cannot_be_said_without_labels_on_both_sides():
    only_flagged = audit.audit_stratum([True, False], [], 10, 90)
    assert only_flagged["precision"] == 0.5 and only_flagged["recall"] is None
    assert only_flagged["audited_rate"] is None
    nothing_flagged = audit.audit_stratum([], [False] * 5, 0, 90)
    assert nothing_flagged["recall"] == 0.0 or nothing_flagged["recall"] is None
    none_missed = audit.audit_stratum([True] * 5, [False] * 5, 10, 90)
    assert none_missed["recall"] == pytest.approx(1.0)


def test_score_audit_reads_a_key_and_labels_and_counts_unsure_and_missing():
    key = {
        "strata": {"alpha|sentence": {"flagged": 4, "unflagged": 6}},
        "items": {
            "u1": {"system": "alpha", "kind": "sentence", "flagged": True},
            "u2": {"system": "alpha", "kind": "sentence", "flagged": True},
            "u3": {"system": "alpha", "kind": "sentence", "flagged": False},
            "u4": {"system": "alpha", "kind": "sentence", "flagged": False},
            "u5": {"system": "alpha", "kind": "sentence", "flagged": False},
        },
    }
    labels = {"u1": "invented", "u2": "supported", "u3": "supported", "u4": "unsure"}
    result = audit.score_audit(key, labels)
    row = result["strata"]["alpha|sentence"]
    assert (row["precision"], row["miss_rate"], row["unsure"]) == (0.5, 0.0, 1)
    assert result["unlabelled_items"] == 1
    assert row["labelled"] == {"flagged": 2, "unflagged": 1}
