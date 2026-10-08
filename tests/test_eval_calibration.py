"""Judge calibration: kappa, bootstrap CI, labels in and out, bias probes."""

from __future__ import annotations

import csv
import json

import pytest

from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.eval import calibration as cal
from a11y_stem.models.fake import FakeVisionClient
from test_eval_support import figure, settings

# Wikipedia's worked example for Cohen's kappa (50 proposals, two readers):
# both yes 20, A yes / B no 5, A no / B yes 10, both no 15 -> p_o 0.7, p_e 0.5, kappa 0.4.
WIKI = [(True, True)] * 20 + [(True, False)] * 5 + [(False, True)] * 10 + [(False, False)] * 15


def test_kappa_matches_the_textbook_example():
    assert cal.cohen_kappa(WIKI) == pytest.approx(0.4)
    assert cal.raw_agreement(WIKI) == pytest.approx(0.7)
    assert cal.confusion(WIKI) == {
        "judge_pass_human_pass": 20,
        "judge_pass_human_fail": 5,
        "judge_fail_human_pass": 10,
        "judge_fail_human_fail": 15,
    }


def test_kappa_edge_cases():
    assert cal.cohen_kappa([(True, True), (False, False)]) == pytest.approx(1.0)
    assert cal.cohen_kappa([(True, False), (False, True)]) == pytest.approx(-1.0)
    # Both raters say "pass" to everything: chance agreement is 1, kappa undefined.
    assert cal.cohen_kappa([(True, True)] * 5) is None
    assert cal.cohen_kappa([]) is None
    # 90% pass on both sides, independent-looking: high raw agreement, kappa near 0.
    pairs = [(True, True)] * 81 + [(True, False)] * 9 + [(False, True)] * 9 + [(False, False)]
    assert cal.raw_agreement(pairs) == pytest.approx(0.82)
    assert cal.cohen_kappa(pairs) == pytest.approx(0.0, abs=1e-12)


def test_bootstrap_ci_is_seeded_and_brackets_the_estimate():
    first = cal.bootstrap_ci(WIKI, n_boot=1000, seed=7)
    again = cal.bootstrap_ci(WIKI, n_boot=1000, seed=7)
    other = cal.bootstrap_ci(WIKI, n_boot=1000, seed=8)
    assert first == again
    assert first != other
    assert first["low"] < 0.4 < first["high"]
    # Large-sample standard error: sqrt(p_o (1 - p_o) / (n (1 - p_e)^2)) = 0.1296, so the
    # normal-approximation interval is 0.4 -/+ 1.96 * 0.1296 = 0.146 to 0.654.
    se = (0.7 * 0.3 / (50 * 0.5**2)) ** 0.5
    assert first["low"] == pytest.approx(0.4 - 1.96 * se, abs=0.03)
    assert first["high"] == pytest.approx(0.4 + 1.96 * se, abs=0.03)
    assert first["skipped"] == 0


def test_bootstrap_ci_for_perfect_agreement_is_a_point():
    pairs = [(True, True)] * 5 + [(False, False)] * 5
    ci = cal.bootstrap_ci(pairs, n_boot=500, seed=0)
    assert (ci["low"], ci["high"]) == (1.0, 1.0)
    # Resamples that drew only one class leave kappa undefined and are skipped.
    assert 0 <= ci["skipped"] < 10


def test_bootstrap_ci_of_nothing():
    assert cal.bootstrap_ci([], n_boot=10)["low"] is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("TRUE", True),
        ("yes", True),
        (" pass ", True),
        ("1", True),
        (1, True),
        ("n", False),
        ("Fail", False),
        ("0", False),
        (False, False),
        ("", None),
        (None, None),
    ],
)
def test_parse_bool(value, expected):
    assert cal.parse_bool(value) is expected


def test_parse_bool_rejects_unknown_words():
    with pytest.raises(ValueError, match="pass/fail"):
        cal.parse_bool("maybe")


def judged_rows():
    return [
        {"id": "a", "judge_outcome": "judged", "judge_pass": True, "candidate_alt": "A \u00d7"},
        {"id": "b", "judge_outcome": "judged", "judge_pass": False, "candidate_alt": "B"},
        {"id": "c", "judge_outcome": "judged", "judge_pass": True, "candidate_alt": "C"},
        {"id": "d", "judge_outcome": "no_proposal", "judge_pass": None},
    ]


def test_template_has_no_judge_verdict_and_round_trips_through_csv(tmp_path):
    path = tmp_path / "labels.csv"
    assert cal.write_label_template(judged_rows(), path) == 3  # only judged items
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == cal.TEMPLATE_FIELDS
    assert "judge_pass" not in rows[0] and rows[0]["candidate_alt"] == "A \u00d7"
    # Harish fills the sheet in a spreadsheet and saves it back.
    rows[0]["human_pass"], rows[1]["human_pass"] = "yes", "no"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cal.TEMPLATE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    assert cal.load_labels(path) == {"a": True, "b": False, "c": None}


def test_template_as_jsonl(tmp_path):
    path = tmp_path / "labels.jsonl"
    cal.write_label_template(judged_rows(), path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["id"] for r in rows] == ["a", "b", "c"] and rows[0]["human_pass"] == ""


def test_load_labels_reports_bad_rows(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text('{"id": "a", "human_pass": "maybe"}\n')
    with pytest.raises(ValueError, match="row 1"):
        cal.load_labels(path)
    path.write_text('{"human_pass": true}\n')
    with pytest.raises(ValueError, match="no id"):
        cal.load_labels(path)


def test_agreement_report_joins_on_id_and_skips_unusable_rows():
    judged = {r["id"]: r for r in judged_rows()}
    labels = {"a": True, "b": True, "c": None, "d": False, "zzz": True}
    report = cal.agreement_report(judged, labels, n_boot=200, seed=1)
    assert report["n"] == 2  # a and b; c unlabelled, d has no verdict, zzz unknown
    assert report["unlabelled"] == 1
    assert report["labels_without_judgement"] == 2
    assert report["raw_agreement"] == 0.5
    assert report["disagreements"] == ["b"]
    assert report["confusion"]["judge_fail_human_pass"] == 1
    text = cal.format_agreement(report)
    assert "Cohen's kappa" in text and "| judge pass | 1 | 0 |" in text


# -------------------------------------------------------------------------- probes


def figures():
    return [
        figure("p", 0, "A ball on a ramp.", "Figure 1"),
        figure("p", 1, "A graph of speed against time.", "Figure 2"),
        figure("p", 2, "", "Figure 3"),  # no reference: not a probe figure
        figure("p", 3, "A car on a road.", "Figure 4"),
    ]


def test_probe_candidates():
    usable = cal.probe_figures(figures())
    assert len(usable) == 3
    rows = cal.probe_candidates(usable, paraphrases={usable[1]["id"]: "Hand-written."})
    by = {(r["id"].rsplit("-", 1)[-1], r["probe"]): r for r in rows}
    assert by[("000", "reference")]["candidate"] == "A ball on a ramp."
    assert by[("000", "verbose")]["candidate"].count("A ball on a ramp.") == 2
    assert by[("001", "verbose")]["candidate"] == "Hand-written."
    assert by[("000", "empty")]["candidate"] == ""
    assert by[("000", "irrelevant")]["candidate"] == "A graph of speed against time."
    assert {r["expected_pass"] for r in rows if r["probe"] in {"empty", "irrelevant"}} == {False}
    single = cal.probe_candidates(usable[:1])
    assert single[3]["candidate"] == cal.IRRELEVANT_FALLBACK


def length_biased_judge(request):
    """Grades by length, the failure the verbose probe exists to catch."""
    candidate = request.context_text.split("<candidate>\n", 1)[1].split("\n</candidate>")[0]
    reference = request.context_text.split("<reference>\n", 1)[1].split("\n</reference>")[0]
    shares = any(word in candidate for word in [w for w in reference.split() if len(w) > 3][:2])
    return {
        "conveys_key_information": shares,
        "factual_error": False,
        "score": min(5, 1 + len(candidate) // 15) if shares else 1,
        "confidence": 0.9,
    }


def test_probes_expose_length_bias_and_check_expected_outcomes():
    client = FakeVisionClient(responders={"judge": length_biased_judge})
    report, items = cal.run_probes(cal.probe_figures(figures()), client=client, settings=settings())
    assert report["complete"] and report["n"] == 12
    probes = report["probes"]
    assert probes["reference"]["as_expected_rate"] == 1.0
    assert probes["empty"]["as_expected_rate"] == 1.0
    assert probes["irrelevant"]["as_expected_rate"] == 1.0
    assert report["length_bias"]["mean_score_delta"] > 1  # verbose scored higher
    assert report["length_bias"]["length_ratio"] > 2
    assert {i["probe"] for i in items} == set(cal.PROBES)
    assert all(r.step == "judge" and r.images == [] for r in client.requests)
    text = cal.format_probes(report)
    assert "| verbose | pass |" in text and "Length bias" in text


def test_probes_stop_cleanly_when_the_budget_runs_out():
    budget = JobBudget(InMemorySpendGuard(cap_usd=1.0), max_usd=1.0, max_calls=5)
    client = FakeVisionClient(responders={"judge": length_biased_judge}, budget=budget)
    report, items = cal.run_probes(cal.probe_figures(figures()), client=client, settings=settings())
    assert (report["n"], report["n_planned"], report["complete"]) == (5, 12, False)
    assert report["stopped_reason"] == "budget_job_calls"
    assert len(items) == 5


def test_load_paraphrases(tmp_path):
    path = tmp_path / "p.jsonl"
    path.write_text('{"id": "a", "text": "Long."}\n{"id": "b", "text": ""}\n')
    assert cal.load_paraphrases(path) == {"a": "Long."}
