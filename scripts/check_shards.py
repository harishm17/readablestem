"""Check that CI's test shards together ran every collected test exactly once.

Usage: python scripts/check_shards.py REPORT_DIR EXPECTED_SHARDS

Each shard writes a JSON report with `pytest --shard-report` (tests/conftest.py). A shard
that did not run, a test that no shard ran, and a test that two shards ran all fail the job.
Standard library only, so the CI job needs no install.
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


def main(argv: list[str]) -> int:
    folder, expected = Path(argv[1]), int(argv[2])
    reports = [json.loads(p.read_text()) for p in sorted(folder.glob("**/shard-*.json"))]
    problems = check(reports, expected)
    for line in problems[:50]:
        print(f"::error::{line}")
    if problems:
        return 1
    total = len(reports[0]["collected"])
    print(f"ok: {total} tests, each ran in exactly one of {expected} shards")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
