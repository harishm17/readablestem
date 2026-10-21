"""Print the headline numbers of image page eval reports side by side (ADR 0007).

    uv run python scripts/image_pages_table.py .local/image-pages/haiku-1.json ...

One column per report: equations (primary exact_lenient and the symbols score, end to end),
detection, headings, prose word error rates, review load, equation flags, the second reading,
cost and time per page. Reads reports only; spends nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def pick(m: dict, *path, default=None):
    for key in path:
        if not isinstance(m, dict) or key not in m:
            return default
        m = m[key]
    return m


def fmt(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def rows(report: dict) -> dict[str, object]:
    m = report["metrics"]
    c = report.get("cost", {})
    pages = pick(m, "image_pages") or {}
    load = m["review_load"]
    flags = pages.get("equation_flags", {})
    second = pages.get("second_reading", {})
    return {
        "PDF pages": c.get("pdf_pages"),
        "gold equations": m["gold_equations"],
        "equations right end to end (exact_lenient)": m["end_to_end_exact_lenient"],
        "equations right end to end (symbols, credit)": pick(
            m, "secondary_symbols", "end_to_end_exact_with_credit"
        ),
        "detection recall": m["detection_recall"],
        "detection precision": m["detection_precision"],
        "prose WER (all words, formulas included)": pick(m, "prose_wer", "wer"),
        "prose WER (words of 3+ letters, formulas out)": pick(m, "prose_only_wer", "wer"),
        "required decisions per PDF page (equations, tables, figures, headings)": load[
            "required_per_pdf_page"
        ],
        "required decisions per converted page (all blocks)": pages.get(
            "required_per_converted_page"
        ),
        "optional notes per converted page (all blocks)": pages.get("optional_per_converted_page"),
        "equations wrong, matched, asked about: precision": pick(flags, "all", "precision"),
        "... recall": pick(flags, "all", "recall"),
        "wrong equations not asked, per 100 gold": pick(
            flags, "all", "wrong_not_asked_per_100_gold"
        ),
        "wrong inline not asked, per 100 gold": pick(
            flags, "inline", "wrong_not_asked_per_100_gold"
        ),
        "display: second reading agreed / disagreed": f"{second.get('agreed')} / {second.get('disagreed')}",
        "display: agreed and wrong": second.get("agreed_wrong"),
        "pages read / kept as image": f"{pages.get('converted')} / {pages.get('fell_back')}",
        "page reader $ per page": pages.get("cost_per_page_usd"),
        "whole pipeline $ per page": c.get("per_pdf_page_usd"),
        "page reader seconds per page": pages.get("seconds_per_page"),
        "low-confidence share of blocks": pages.get("low_confidence_share"),
        "[illegible] markers": pages.get("illegible"),
    }


def main(paths: list[str]) -> None:
    reports = [json.loads(Path(p).read_text()) for p in paths]
    table = [rows(r) for r in reports]
    labels = [Path(p).stem for p in paths]
    print("| | " + " | ".join(labels) + " |")
    print("|---|" + "---|" * len(labels))
    for key in table[0]:
        print(f"| {key} | " + " | ".join(fmt(t[key]) for t in table) + " |")


if __name__ == "__main__":
    main(sys.argv[1:])
