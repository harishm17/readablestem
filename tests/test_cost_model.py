"""`readablestem eval cost`: what a page costs, by page kind and by step, from a replay."""

from __future__ import annotations

import dataclasses
import json
import os

import pytest

from readablestem.cli.main import main
from readablestem.core.types import CallFact, StepFacts
from readablestem.eval import cost, measure
from readablestem.eval.measure import Converted

# A file made for the corpus, so its answers are in the repository: two pages, one with a
# chart and one with a table, read by the figure step and the table step.
ENTRY = "s15_charts_figures"


# A replay of a corpus file: the workflow job has the table model its answers were made with.
@pytest.mark.workflow
def test_the_kinds_add_up_to_what_the_replayed_file_cost(tmp_path, capsys):
    if reason := measure.cannot_replay(ENTRY):
        if os.environ.get("CI"):
            raise RuntimeError(reason)
        pytest.skip(f"this machine cannot replay the corpus: {reason}")
    records, out = tmp_path / "records.json", tmp_path / "cost.json"
    assert (
        main(["eval", "cost", "--only", ENTRY, "--records", str(records), "--out", str(out)]) == 0
    )
    said = capsys.readouterr().out

    (done,) = json.loads(records.read_text())["runs"][0]
    assert done["missed"] == 0 and done["usd"] > 0.01
    record = json.loads(out.read_text())
    made = record["metrics"]
    # To the cent: every dollar of the job is on a page, and every page has one kind.
    assert sum(row["usd"] for row in made["kinds"].values()) == pytest.approx(
        done["usd"], abs=0.005
    )
    assert sum(row["pages"] for row in made["kinds"].values()) == done["pages"] == 2
    assert sum(row["usd"] for row in made["steps"].values()) == pytest.approx(
        done["usd"], abs=0.005
    )
    assert {"figure", "table"} <= set(made["steps"])
    # A step that is not built is listed with nothing spent and its estimate beside it.
    audit = next(row for row in made["added"] if row["step"] == "page_audit")
    assert (audit["calls"], audit["spent_usd"], audit["estimate_usd"]) == (0, 0.0, [0.0054, 0.01])
    assert "page audit" in said and "$0.0054 to $0.0100" in said
    # What the record says about itself: a development set, so it can meet no bar.
    assert record["split"]["name"] == "development" and record["profile"]["sha256"]
    assert "Proof owed" in said
    assert main(["eval", "bar", str(out), "--measure", "usd", "--at-most", "1.0"]) != 0


def test_a_page_has_its_own_requests_and_a_share_of_the_documents():
    def call(step: str, page: int | None, usd: float) -> CallFact:
        return CallFact(step=step, page=page, model="claude-opus-5-5", cost_usd=usd)

    facts = StepFacts(
        calls=[call("equation", 0, 0.04), call("figure", 1, 0.02), call("outline", None, 0.03)]
    )
    whole = Converted(
        "w_lecture_5111", pages=3, kinds=["dense_math", "figure", "typical"], facts=facts, usd=0.09
    )
    # Its recording lacks answers: a refused request costs nothing, so it is left out.
    short = dataclasses.replace(whole, upload="f1_notation", missed=50)

    made = cost.report([whole, short], open_pool_usd=4.0)

    assert made["uploads"] == {
        "measured": 1,
        "answers_missing": {"f1_notation": 50},
        "not_measured": [],
    }
    by_kind = {kind: row["usd"] for kind, row in made["kinds"].items()}
    assert by_kind == pytest.approx({"dense_math": 0.05, "figure": 0.03, "typical": 0.01})
    assert made["kinds"]["figure"]["steps"] == pytest.approx({"figure": 0.02, "outline": 0.01})
    assert made["usd"] == pytest.approx(0.09)
