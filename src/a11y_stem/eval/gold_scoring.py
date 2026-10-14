"""Score equation predictions against the OpenStax gold set.

Prediction file: JSON lines, one per equation, with the gold record's "id" and either
"latex" or "mathml". Gold equations with no prediction count as failures. Prediction ids
that match no gold record (e.g. typos) are reported in the summary as `unknown_ids`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from a11y_stem.eval import equations_v2 as v2
from a11y_stem.eval.equations import EquationScore, score_latex, score_mathml, summarize

UNKNOWN_IDS_SHOWN = 5


def _load_json_line(path: Path, number: int, line: str) -> dict:
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}:{number}: not valid JSON ({exc.msg})") from exc
    if not isinstance(record, dict):
        raise ValueError(f"{path}:{number}: expected a JSON object")
    return record


def _in_nested_set(file: Path, root: Path) -> bool:
    """True when `file` lies in a gold set nested under `root`: a subfolder with its own
    manifest.json (data/gold/tables, the tables eval's gold), read only on its own."""
    folder = file.parent
    while folder != root and root in folder.parents:
        if (folder / "manifest.json").is_file():
            return True
        folder = folder.parent
    return False


def iter_gold_records(path: Path, kind: str | None = None) -> Iterator[dict]:
    """Yield records from one JSONL file or every *.jsonl under a directory.

    A gold set nested in the directory (a subfolder with its own manifest.json, such as
    data/gold/tables) is left out: it has its own tasks, and reading it here would change
    what the other tasks select.
    """
    if path.is_dir():
        files = sorted(f for f in path.rglob("*.jsonl") if not _in_nested_set(f, path))
    else:
        files = [path]
    for file in files:
        with file.open(encoding="utf-8") as fh:
            for number, line in enumerate(fh, 1):
                if line.strip():
                    record = _load_json_line(file, number, line)
                    if kind is None or record.get("kind") == kind:
                        yield record


def load_predictions(path: Path) -> dict[str, dict]:
    preds = {}
    with path.open(encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            record = _load_json_line(path, number, line)
            if "id" not in record or not ({"latex", "mathml"} & record.keys()):
                raise ValueError(f"{path}:{number}: need 'id' plus 'latex' or 'mathml'")
            preds[record["id"]] = record
    return preds


def score_equations(gold: list[dict], preds: dict[str, dict] | None, display: str = "all") -> dict:
    """Score predictions; preds=None scores gold against itself (pipeline sanity check).

    The summary is the primary scorer's. `secondary_stream_v2` beside it is the labelled
    secondary (`eval.equations_v2`); the rows carry `exact_stream_v2` too."""
    rows = []
    scores: list[EquationScore] = []
    secondary: list[v2.StreamScore] = []
    for record in gold:
        if display != "all" and record.get("display") != display:
            continue
        if preds is None:
            score = score_mathml(record["mathml"], record["mathml"])
            stream = v2.score_stream(record["mathml"], record["mathml"])
        elif record["id"] not in preds:
            size = score_mathml(record["mathml"], record["mathml"]).gold_nodes
            score = EquationScore(False, False, size, 0.0, size, 0, "missing prediction")
            stream = v2.missing_stream(record["mathml"])
        elif "mathml" in preds[record["id"]]:
            score = score_mathml(preds[record["id"]]["mathml"], record["mathml"])
            stream = v2.score_stream(preds[record["id"]]["mathml"], record["mathml"])
        else:
            score = score_latex(preds[record["id"]]["latex"], record["mathml"])
            stream = v2.score_stream_latex(preds[record["id"]]["latex"], record["mathml"])
        scores.append(score)
        secondary.append(stream)
        rows.append(
            {
                "id": record["id"],
                "display": record.get("display"),
                **score.to_dict(),
                "exact_stream_v2": stream.exact,
                "stream_similarity": stream.similarity,
            }
        )
    summary = summarize(scores)
    if scores:
        summary["secondary_stream_v2"] = v2.summarize(secondary)
    if preds is not None:
        gold_ids = {record["id"] for record in gold}
        unknown = sorted(pid for pid in preds if pid not in gold_ids)
        summary["unknown_ids"] = len(unknown)
        summary["unknown_ids_first"] = unknown[:UNKNOWN_IDS_SHOWN]
    return {"summary": summary, "items": rows}
