"""Reports: one scored output as JSON, and the summary of many as a table.

A report is content-free unless units are asked for: counts, rates, hashes, ids and
thresholds; the text of a flagged sentence is only written with `units=True`, to a file
next to the report and never to a log. The report carries the SHA-256 of this package's
own code (`code_fingerprint`), so a run report can say exactly which rules scored it, and
the pre-registration's "frozen and hashed" is one comparison.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from a11y_stem.eval.invented import pool
from a11y_stem.eval.invented.score import Score, rule_summary

SCHEMA_VERSION = 1
KIND = "invented-content"
TYPE_LABELS = {
    "sentence": "prose sentences (7+ words)",
    "number": "numbers in prose and headings",
    "heading": "headings",
    "equation_aligned": "equations aligned to gold (added symbol)",
    "equation_aligned_without_tags": "  same, less a kept equation number",
    "equation_unaligned": "equations with no gold partner",
    "table_cell": "table cells",
    "description_cell": "cells of figure data tables",
}


def code_fingerprint() -> dict[str, Any]:
    """SHA-256 over this package's source files (names and bytes, sorted): the 'code is
    frozen' of the pre-registration is that this value does not change between the
    development check and the run."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    files = sorted(root.glob("*.py"))
    for path in files:
        digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return {"sha256": digest.hexdigest(), "files": [path.name for path in files]}


def build_report(
    score: Score,
    *,
    system: str,
    section: str,
    run: str,
    pdf_name: str,
    output_name: str,
    units: bool = False,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One scored output as a JSON-ready dict."""
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "code": code_fingerprint(),
        "rules": rule_summary(),
        "meta": {
            "system": system,
            "section": section,
            "run": run,
            "pdf": pdf_name,
            "output": output_name,
        },
        "score": score.to_dict(units=units),
        **(dict(extra) if extra else {}),
    }


def write_report(report: Mapping[str, Any], path: Path) -> None:
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False, sort_keys=True), "utf-8")


def read_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text("utf-8"))
    if data.get("kind") != KIND:
        raise ValueError(f"{path.name} is not an invented-content report")
    return data


def entries_by_system(reports: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group reports for `pool.summarize`: {system: [{"section", "run", "score"}]}."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for report in reports:
        meta = report["meta"]
        grouped[meta["system"]].append(
            {"section": meta["section"], "run": meta["run"], "score": report["score"]}
        )
    return dict(grouped)


def summarize_reports(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Every system's pooled rates (`pool.summarize`), plus the code and rules used."""
    codes = {r["code"]["sha256"] for r in reports}
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": f"{KIND}-summary",
        "code_sha256": sorted(codes),
        "rules": rule_summary(),
        "systems": {
            system: pool.summarize(entries)
            for system, entries in sorted(entries_by_system(reports).items())
        },
    }


def _fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def _fmt_interval(interval: Sequence[float | None] | None) -> str:
    if not interval or interval[0] is None or interval[1] is None:
        return "n/a"
    return f"{100 * interval[0]:.1f} to {100 * interval[1]:.1f}"


def format_markdown(summary: Mapping[str, Any]) -> str:
    """The summary as Markdown tables, one per system and view."""
    lines: list[str] = []
    for system, data in summary["systems"].items():
        for label, title in (
            ("all_text", "all text, including text the tool wrote itself"),
            ("content_only", "without the text the tool wrote itself"),
        ):
            lines += [
                f"### {system} ({data['sections']} sections, {data['entries']} outputs): {title}",
                "",
                "| Type | Units | Unsupported | Rate | Cluster 95% | Wilson 95% | Per PDF page |",
                "|---|---|---|---|---|---|---|",
            ]
            for kind, row in data[label].items():
                cluster = row["cluster95"]
                lines.append(
                    f"| {TYPE_LABELS.get(kind, kind)} | {row['n']} | {row['unsupported']} "
                    f"| {_fmt_rate(row['rate'])} "
                    f"| {_fmt_interval([cluster['low'], cluster['high']])} "
                    f"| {_fmt_interval(row['wilson95'])} "
                    f"| {'n/a' if row['per_pdf_page'] is None else f'{row["per_pdf_page"]:.2f}'} |"
                )
            symbols = data["equation_symbols"]
            if symbols["per_1000"] is not None:
                lines += [
                    "",
                    f"Added symbols in aligned equations: {symbols['added']} in "
                    f"{symbols['aligned_output_symbols']} output symbols "
                    f"({symbols['per_1000']:.2f} per 1,000; "
                    f"{symbols['per_1000_without_tags']:.2f} per 1,000 without kept equation "
                    "numbers).",
                ]
            lines.append("")
    return "\n".join(lines)
