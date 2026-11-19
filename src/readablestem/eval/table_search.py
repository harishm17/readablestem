"""What the table search's time limit would stop on slower machines, from recorded searches.

`python -m readablestem.eval.table_search` reads the seconds each table search took on two sets
of arXiv papers (`data/eval/table-search-seconds.json`: per document, a short hash and the
seconds of each searched page, no text) and replays them through the limit the conversion uses
(`pipeline.tables.DetectorBudget`), each search taking `factor` times as long. For each factor
and set it prints the documents the limit stops and the pages it would leave unsearched. No
PDF is opened and no model is asked.

The limit was sized with it (docs/tables.md, "Pages of pen strokes"): no document of either set
is stopped at four times its recorded cost, the share of a core a small hosting plan gives a
job. The seconds are wall seconds on a quiet machine, where wall over thread CPU time (the
limit's meter) was 1.02, so the replay is a little stricter than the limit.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path

from readablestem.pipeline.tables import DetectorBudget

SECONDS = Path(__file__).resolve().parents[3] / "data" / "eval" / "table-search-seconds.json"
FACTORS = (1, 2, 4, 8)


def not_searched(
    seconds: Sequence[float], factor: float, budget: Callable[[], DetectorBudget] = DetectorBudget
) -> int:
    """How many of a document's searches the limit refuses when each takes `factor` times
    its recorded `seconds`, in page order (`layout` asks the budget before each search)."""
    left = budget()
    for spent in seconds:
        if left.open():
            left.spend(factor * spent)
    return left.refused


def simulate(
    sets: dict[str, dict[str, list[float]]],
    factors: Sequence[float] = FACTORS,
    budget: Callable[[], DetectorBudget] = DetectorBudget,
) -> list[dict[str, object]]:
    """One row per factor and set: documents, those stopped, and the pages left unsearched."""
    rows: list[dict[str, object]] = []
    for factor in factors:
        for name, documents in sets.items():
            refused = [not_searched(s, factor, budget) for s in documents.values()]
            rows.append(
                {
                    "factor": factor,
                    "set": name,
                    "documents": len(refused),
                    "stopped": sum(1 for n in refused if n),
                    "pages_not_searched": sum(refused),
                }
            )
    return rows


def main() -> int:
    recorded = json.loads(SECONDS.read_text(encoding="utf-8"))
    pages = sum(len(s) for docs in recorded["sets"].values() for s in docs.values())
    print(f"Recorded {recorded['recorded']}: {pages} searched pages. {recorded['what']}")
    for row in simulate(recorded["sets"]):
        print(
            f"x{row['factor']} {row['set']}: {row['stopped']} of {row['documents']} documents "
            f"stopped, {row['pages_not_searched']} pages not searched"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
