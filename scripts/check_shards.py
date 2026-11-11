"""Check that CI's test shards together ran every collected test exactly once, and with
`--routes` that together they requested every route of the app.

Usage: python scripts/check_shards.py REPORT_DIR EXPECTED_SHARDS [--routes]

Each shard writes a JSON report with `pytest --shard-report` (tests/conftest.py). A shard
that did not run, a test that no shard ran, and a test that two shards ran all fail the job.
With `--routes`, so does a route of the app that gave no shard's tests its own answer:
either no test requests it, or every test of it is refused (tests/_routes.py says what
counts and holds the list of routes let off, which is empty). Standard library only, so the
CI job needs no install.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def check(reports: list[dict], expected: int) -> list[str]:
    problems: list[str] = []
    if len(reports) != expected:
        problems.append(f"expected {expected} shard reports, found {len(reports)}")
    if not reports:
        return problems
    collected = set(reports[0]["collected"])
    for report in reports[1:]:
        if set(report["collected"]) != collected:
            problems.append(f"shard {report['shard']} collected a different set of tests")
    seen: dict[str, str] = {}
    for report in reports:
        for nodeid in report["ran"]:
            if nodeid in seen:
                problems.append(
                    f"ran twice (shards {seen[nodeid]} and {report['shard']}): {nodeid}"
                )
            seen[nodeid] = report["shard"]
    for nodeid in sorted(collected - set(seen)):
        problems.append(f"collected but ran in no shard: {nodeid}")
    for nodeid in sorted(set(seen) - collected):
        problems.append(f"ran but not in the collection: {nodeid}")
    return problems


def routes(reports: list[dict]) -> tuple[int, list[str]]:
    """How many routes the shards' apps had, and one problem for each that gave no shard's
    tests its own answer. A report with no route list is a problem itself: nothing was
    measured."""
    known: set[str] = set()
    requested: set[str] = set()
    covered: set[str] = set()
    problems = []
    for report in reports:
        seen = report.get("routes")
        if seen is None:
            problems.append(f"shard {report['shard']} wrote no list of routes")
            continue
        known.update(seen["known"])
        requested.update(seen["requested"])
        covered.update(seen["answered"], seen["allowed"])
    if not known and not problems:
        problems.append("no shard built the app: no route was seen")
    for route in sorted(known - covered):
        if route in requested:
            problems.append(f"tests of this route only get refusals, never its own answer: {route}")
        else:
            problems.append(f"no test requests the route: {route}")
    return len(known), problems


def main(argv: list[str]) -> int:
    folder, expected = Path(argv[1]), int(argv[2])
    reports = [json.loads(p.read_text()) for p in sorted(folder.glob("**/shard-*.json"))]
    problems = check(reports, expected)
    count = 0
    if "--routes" in argv[3:] and reports:
        count, missing = routes(reports)
        problems += missing
    for line in problems[:50]:
        print(f"::error::{line}")
    if problems:
        return 1
    total = len(reports[0]["collected"])
    print(f"ok: {total} tests, each ran in exactly one of {expected} shards")
    if count:
        print(f"ok: {count} routes, each gave a test its own answer")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
