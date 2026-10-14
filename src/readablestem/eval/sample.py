"""The weekly sample: score a small random share of the gold set every week.

Product plan, "Quality gates": every week, a 5% sample is scored again, so drift in a
provider's model, a prompt, or the pipeline shows up between full runs. The sample is
random but reproducible: it is seeded by the ISO week ("2026-W41"), so a rerun in the
same week scores the same items, and the next week draws new ones.

`sample_gold(gold_dir, out_dir, task=..., fraction=..., seed=...)` writes the sampled
records as a small gold directory (same layout, with each book's `_book.json`), which
`eval pipeline` then runs on unchanged. Units are what the task iterates over: equations
or figures, or whole pages for headings and document. At least one unit is drawn.

TODO(Harish): also sample opted-in production pages once their review labels exist as
gold (`a11y-stem ops export-opted-in` writes labels; turning them into gold records is
not built), and decide whether a scheduled workflow should spend on this every week
(eval-live.yml is manual-only by design).
"""

from __future__ import annotations

import json
import math
import random
import shutil
from datetime import date
from pathlib import Path
from typing import Any

from readablestem.eval.pipeline_eval import load_gold, select_units, unit_ids


def iso_week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def sample_units(task: str, records: list[dict], *, fraction: float, seed: str) -> list[Any]:
    """A reproducible random `fraction` of the task's units (at least one), in gold order."""
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    units = select_units(task, records, None)
    if not units:
        return []
    k = max(1, math.ceil(fraction * len(units)))
    chosen = set(random.Random(f"{task}:{seed}").sample(range(len(units)), k))
    return [unit for index, unit in enumerate(units) if index in chosen]


def _records_of(task: str, units: list[Any]) -> list[dict]:
    """Gold records to keep: the units themselves, or every record of the chosen pages."""
    if task in {"equations", "figures"}:
        return list(units)
    return [record for page in units for record in page]


def sample_gold(gold_dir: Path, out_dir: Path, *, task: str, fraction: float, seed: str) -> dict:
    """Write the sampled gold set to `out_dir`. Returns what was sampled (ids, counts)."""
    records = load_gold(gold_dir)
    units = sample_units(task, records, fraction=fraction, seed=seed)
    by_page: dict[tuple[str, str], list[dict]] = {}
    for record in _records_of(task, units):
        by_page.setdefault((record["book_slug"], record["page_slug"]), []).append(record)
    for (book, page), rows in by_page.items():
        target = out_dir / "openstax" / book / f"{page}.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        book_meta = gold_dir / "openstax" / book / "_book.json"
        if book_meta.is_file() and not (target.parent / "_book.json").exists():
            shutil.copyfile(book_meta, target.parent / "_book.json")
    return {
        "task": task,
        "fraction": fraction,
        "seed": seed,
        "units_total": len(select_units(task, records, None)),
        "units_sampled": len(units),
        "ids": unit_ids(task, units),
    }
