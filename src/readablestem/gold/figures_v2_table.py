"""Cell accuracy of a chart data table against a gold reference table (docs/eval-figures-long.md).

Offline and exact: no model. The judge's `table_values_ok` is a model's reading of the picture;
this is the number-by-number check for the charts whose source gave the plotted data.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

TABLE_TOLERANCE = 0.05  # of the reference column's range (of |value| for a one-row column)


def _number(cell: str) -> float | None:
    text = cell.strip().replace("−", "-").replace(",", "").rstrip("%")
    try:
        return float(text)
    except ValueError:
        return None


def _key(cells: list[str], width: int) -> tuple[str, ...]:
    out = []
    for cell in cells[:width]:
        value = _number(cell)
        out.append(f"{value:.9g}" if value is not None else " ".join(cell.lower().split()))
    return tuple(out)


def score_table(
    candidate: Mapping[str, Any] | None,
    reference: Mapping[str, Any],
    tolerance: float = TABLE_TOLERANCE,
) -> dict[str, float | int]:
    """Cell accuracy of `candidate` against `reference` (both with `header` and `rows`).

    Rows are matched by their label cells: the first column, or the first two when the
    reference's first header is "series". A reference row with no match counts all its value
    cells as wrong, so a missing row cannot raise the score; extra rows are counted in
    `rows_extra` and not rewarded. A value cell is right when both parse as numbers and differ
    by at most `tolerance` of the reference column's range (of |value| when the range is
    zero), or when the texts are equal after trimming. Columns are compared by position.
    """
    header = [str(h) for h in reference.get("header") or []]
    width = 2 if header and header[0].strip().lower() == "series" else 1
    ref_rows = [[str(c) for c in row] for row in reference.get("rows") or []]
    cand_rows = [[str(c) for c in row] for row in (candidate or {}).get("rows") or []]
    by_key: dict[tuple[str, ...], list[str]] = {}
    for row in cand_rows:
        by_key.setdefault(_key(row, width), row)
    ranges: dict[int, float] = {}
    for col in range(width, len(header)):
        values = [v for row in ref_rows if col < len(row) if (v := _number(row[col])) is not None]
        ranges[col] = (max(values) - min(values)) if len(values) > 1 else 0.0
    cells = right = 0
    for ref in ref_rows:
        match = by_key.get(_key(ref, width))
        for col in range(width, len(header)):
            cells += 1
            if match is None or col >= len(match) or col >= len(ref):
                continue
            want, got = _number(ref[col]), _number(match[col])
            if want is not None and got is not None:
                allowed = tolerance * (ranges[col] or abs(want))
                right += abs(want - got) <= allowed + 1e-12
            else:
                right += ref[col].strip().lower() == match[col].strip().lower()
    ref_keys = {_key(ref, width) for ref in ref_rows}
    return {
        "cells": cells,
        "cells_right": int(right),
        "cell_accuracy": (right / cells) if cells else 0.0,
        "rows": len(ref_rows),
        "rows_matched": sum(1 for key in ref_keys if key in by_key),
        "rows_extra": sum(1 for row in cand_rows if _key(row, width) not in ref_keys),
        "columns_match": int(bool(cand_rows) and all(len(r) == len(header) for r in cand_rows)),
    }
