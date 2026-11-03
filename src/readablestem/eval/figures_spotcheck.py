"""`eval figures-spotcheck`: a person looks at every judge fail and a sample of the passes.

After a figures run with long descriptions on (docs/eval-figures-long.md), the long judge has
graded each long description. The judge is not trusted on its own word, so this draws what a
person should read: **every judged figure the judge failed, plus a seeded sample of its passes**
(10 by default). It does not call a model and spends nothing.

Two steps, both offline:

1. `--items judged.jsonl --sheet spotcheck.csv`: write a labelling sheet and, beside it, a key
   (`spotcheck.key.json`). The sheet shows the figure's image URL, caption, long description and
   data table, in a seeded order that mixes fails and passes, and leaves the judge's verdict out
   so it cannot anchor the label. The key holds the verdicts and what was drawn.
2. `--key spotcheck.key.json --labels spotcheck.csv`: join the labels with the key and report
   how many judge fails the person confirms and how many sampled passes they would fail, each
   with its n and a Wilson 95% interval.

What the numbers do not show: a sample of 10 passes cannot bound the judge's miss rate tightly
(0 misses in 10 still allows about 0.28), and the sample is drawn from one run's outputs, so it
says nothing about other runs. The report prints both counts so that is visible.

A figure counts as a **judge fail** when the long judge answered and the long description is not
`accurate and complete and not invented`, or the data table has wrong values
(`table_values_ok` false). Figures the judge did not answer (no long description to grade, a
failed judge call) are counted and left out of the draw: there is nothing to check a verdict of.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from readablestem.eval.calibration import _read_rows, parse_bool
from readablestem.eval.pipeline_eval import wilson_interval

DEFAULT_PASSES = 10
SHEET_FIELDS = [
    "id",
    "stratum",
    "page_slug",
    "figure_number",
    "image_url",
    "caption",
    "long_description",
    "data_table",
    "human_accurate",
    "human_complete",
    "human_invented_values",
    "human_table_values_ok",
    "notes",
]
KEY_VERSION = 1


def judged(items: Iterable[dict]) -> list[dict]:
    """Items the long judge answered."""
    return [i for i in items if i.get("long_judge_outcome") == "judged" and i.get("id")]


def is_fail(item: dict) -> bool:
    """The judge's verdict on a graded item: fail on the description or on the table."""
    return not item.get("long_pass") or item.get("table_values_ok") is False


def _order(seed: str, item_id: str) -> str:
    return hashlib.sha256(f"{seed}|{item_id}".encode()).hexdigest()


def draw(items: Sequence[dict], *, passes: int = DEFAULT_PASSES, seed: str) -> dict[str, Any]:
    """Every fail plus `passes` judge passes drawn by SHA-256 of `seed|id`, in one mixed
    seeded order (so the sheet does not list fails first)."""
    if passes < 0:
        raise ValueError("passes must be 0 or more")
    graded = judged(items)
    fails = [i for i in graded if is_fail(i)]
    passed = sorted((i for i in graded if not is_fail(i)), key=lambda i: _order(seed, i["id"]))
    sampled = passed[:passes]
    chosen = sorted([*fails, *sampled], key=lambda i: _order(f"{seed}|sheet", i["id"]))
    return {
        "chosen": chosen,
        "fails": [i["id"] for i in fails],
        "sampled_passes": [i["id"] for i in sampled],
        "counts": {
            "items": len(items),
            "judged": len(graded),
            "not_judged": len(items) - len(graded),
            "judge_fails": len(fails),
            "judge_passes": len(passed),
            "passes_sampled": len(sampled),
        },
    }


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def write_sheet(items: Sequence[dict], sheet: Path, key: Path, *, passes: int, seed: str) -> dict:
    """Write the labelling sheet (CSV with a BOM, or JSONL by extension) and the key beside it.
    Returns the counts of the draw."""
    drawn = draw(items, passes=passes, seed=seed)
    rows = [
        {
            "id": item["id"],
            "stratum": _text(item.get("stratum")),
            "page_slug": _text(item.get("page_slug")),
            "figure_number": _text(item.get("figure_number")),
            "image_url": _text(item.get("image_url")),
            "caption": _text(item.get("caption")),
            "long_description": _text(item.get("long_description")),
            "data_table": _text(item.get("data_table")),
            "human_accurate": "",
            "human_complete": "",
            "human_invented_values": "",
            "human_table_values_ok": "",
            "notes": "",
        }
        for item in drawn["chosen"]
    ]
    sheet.parent.mkdir(parents=True, exist_ok=True)
    if sheet.suffix.lower() == ".csv":
        with sheet.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=SHEET_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    else:
        with sheet.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    verdicts = {
        item["id"]: {
            "group": "fail" if is_fail(item) else "pass",
            "accurate": item.get("long_accurate"),
            "complete": item.get("long_complete"),
            "invented_values": item.get("long_invented_values"),
            "table_values_ok": item.get("table_values_ok"),
        }
        for item in drawn["chosen"]
    }
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(
        json.dumps(
            {
                "version": KEY_VERSION,
                "seed": seed,
                "passes_requested": passes,
                "counts": drawn["counts"],
                "verdicts": verdicts,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return drawn["counts"]


def human_verdict(row: dict) -> bool | None:
    """The person's pass/fail for one sheet row: accurate, complete, no invented values, and
    (when they answered it) the table's values right. None until the three required answers
    are in."""
    accurate = parse_bool(row.get("human_accurate"))
    complete = parse_bool(row.get("human_complete"))
    invented = parse_bool(row.get("human_invented_values"))
    if accurate is None or complete is None or invented is None:
        return None
    table_ok = parse_bool(row.get("human_table_values_ok"))
    return accurate and complete and not invented and table_ok is not False


def _interval(successes: int, n: int) -> dict[str, Any]:
    ci = wilson_interval(successes, n)
    return {
        "k": successes,
        "n": n,
        "rate": successes / n if n else None,
        "wilson95": list(ci) if ci else None,
    }


def report(key: dict, labels: Sequence[dict]) -> dict[str, Any]:
    """Join the person's labels with the key.

    `fails_confirmed`: of the judge's fails the person labelled, how many they also fail.
    `passes_missed`: of the sampled passes they labelled, how many they would fail (errors the
    judge let through). Rows with no complete answer are counted as unlabelled, not used."""
    verdicts: dict[str, dict] = key["verdicts"]
    confirmed = false_alarms = missed = clean = unlabelled = unknown = 0
    disagreements: list[str] = []
    for row in labels:
        item_id = str(row.get("id") or "")
        verdict = verdicts.get(item_id)
        if verdict is None:
            unknown += 1
            continue
        human = human_verdict(row)
        if human is None:
            unlabelled += 1
            continue
        if verdict["group"] == "fail":
            if human:
                false_alarms += 1
                disagreements.append(item_id)
            else:
                confirmed += 1
        elif human:
            clean += 1
        else:
            missed += 1
            disagreements.append(item_id)
    listed = {str(r.get("id") or "") for r in labels}
    labelled_ids = {str(r.get("id") or "") for r in labels if human_verdict(r) is not None}
    unlabelled += sum(1 for i in verdicts if i not in listed)
    counts = key["counts"]
    return {
        "seed": key.get("seed"),
        "counts": counts,
        "labelled": len(labelled_ids & set(verdicts)),
        "unlabelled": unlabelled,
        "ids_not_in_key": unknown,
        "fails_confirmed": _interval(confirmed, confirmed + false_alarms),
        "passes_missed": _interval(missed, missed + clean),
        "disagreements": sorted(disagreements),
    }


def format_report(rep: dict) -> str:
    c = rep["counts"]

    def line(label: str, block: dict) -> str:
        ci = block["wilson95"]
        span = f"{ci[0]:.2f} to {ci[1]:.2f}" if ci else "n/a"
        rate = f"{block['rate']:.2f}" if block["rate"] is not None else "n/a"
        return f"| {label} | {block['k']} of {block['n']} | {rate} | {span} |"

    return (
        "\n".join(
            [
                f"## Figure spot check (seed {rep['seed']})",
                "",
                f"Run: {c['items']} figures, {c['judged']} graded by the long judge "
                f"({c['judge_fails']} fails, {c['judge_passes']} passes), {c['not_judged']} not "
                f"graded and left out. Sheet: every fail and {c['passes_sampled']} sampled passes.",
                "",
                "| check | count | rate | Wilson 95% |",
                "|---|---|---|---|",
                line("judge fails the person also fails", rep["fails_confirmed"]),
                line("sampled judge passes the person fails", rep["passes_missed"]),
                "",
                f"Rows labelled: {rep['labelled']}; not yet labelled: {rep['unlabelled']}; "
                f"ids not in the key: {rep['ids_not_in_key']}.",
                f"Disagreements to read: {', '.join(rep['disagreements']) or 'none'}.",
                "",
                "A sample this small cannot bound the judge's miss rate tightly; the interval "
                "is the honest width.",
            ]
        )
        + "\n"
    )


def load_items(path: Path) -> list[dict]:
    return _read_rows(path)


def load_key(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != KEY_VERSION or "verdicts" not in data:
        raise ValueError(f"{path}: not a figures-spotcheck key")
    return data


def load_labels(path: Path) -> list[dict]:
    return _read_rows(path)
