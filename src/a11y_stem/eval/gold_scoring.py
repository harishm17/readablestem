"""Score equation predictions against the OpenStax gold set.

Prediction file: JSON lines, one per equation, with the gold record's "id" and either
"latex" or "mathml". Gold equations with no prediction count as failures.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from a11y_stem.eval.equations import EquationScore, score_latex, score_mathml, summarize


def iter_gold_records(path: Path, kind: str | None = None) -> Iterator[dict]:
    """Yield records from one JSONL file or every *.jsonl under a directory."""
    files = sorted(path.rglob("*.jsonl")) if path.is_dir() else [path]
    for file in files:
        with file.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    record = json.loads(line)
                    if kind is None or record.get("kind") == kind:
                        yield record


def load_predictions(path: Path) -> dict[str, dict]:
    preds = {}
    with path.open(encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if "id" not in record or not ({"latex", "mathml"} & record.keys()):
                raise ValueError(f"{path}:{number}: need 'id' plus 'latex' or 'mathml'")
            preds[record["id"]] = record
    return preds


def score_equations(gold: list[dict], preds: dict[str, dict] | None, display: str = "all") -> dict:
    """Score predictions; preds=None scores gold against itself (pipeline sanity check)."""
    rows = []
    scores: list[EquationScore] = []
    for record in gold:
        if display != "all" and record.get("display") != display:
            continue
        if preds is None:
            score = score_mathml(record["mathml"], record["mathml"])
        elif record["id"] not in preds:
            size = score_mathml(record["mathml"], record["mathml"]).gold_nodes
            score = EquationScore(False, False, size, 0.0, size, 0, "missing prediction")
        elif "mathml" in preds[record["id"]]:
            score = score_mathml(preds[record["id"]]["mathml"], record["mathml"])
        else:
            score = score_latex(preds[record["id"]]["latex"], record["mathml"])
        scores.append(score)
        rows.append({"id": record["id"], "display": record.get("display"), **score.to_dict()})
    return {"summary": summarize(scores), "items": rows}
