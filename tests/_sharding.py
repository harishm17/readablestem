"""Split the test run into shards for CI, balanced by recorded durations.

`pytest --shard 2/4` runs the second of four shards. Tests are grouped by file (module-scoped
fixtures, such as the browser tests' servers, stay together) and the groups are dealt out
longest first to the lightest shard. The result depends only on the collected test ids and
`tests/shard_durations.json`, so every shard computes the same split. A test or file missing
from the durations file gets a default weight and still lands in exactly one shard.

CI proves that nothing is lost: each shard writes a report (`--shard-report`) and
`scripts/check_shards.py` checks that the shards' collections agree and that every collected
test ran in exactly one shard.

`--shard-record FILE` writes fresh durations after a run (see docs/development.md).
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

DURATIONS = Path(__file__).with_name("shard_durations.json")
# Seconds assumed for a test with no recorded duration. Recorded files list only tests above
# MIN_RECORDED, so most tests are "unknown" by design and cost this much each.
DEFAULT_SECONDS = 0.02
MIN_RECORDED = 0.05


def parse_shard(value: str) -> tuple[int, int]:
    """`"2/4"` -> (2, 4). Shards count from 1."""
    try:
        index, total = (int(part) for part in value.split("/"))
    except ValueError:
        raise ValueError(f"--shard wants INDEX/TOTAL, for example 2/4 (got {value!r})") from None
    if not 1 <= index <= total:
        raise ValueError(f"--shard index must be within 1..TOTAL (got {value!r})")
    return index, total


def load_durations(path: Path = DURATIONS) -> dict[str, float]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def assign(nodeids: list[str], total: int, durations: dict[str, float]) -> dict[str, int]:
    """Shard number (1..total) for every file in `nodeids`, keyed by file path."""
    weight: dict[str, float] = defaultdict(float)
    for nodeid in nodeids:
        weight[nodeid.split("::", 1)[0]] += durations.get(nodeid, DEFAULT_SECONDS)
    load = [0.0] * total
    shard_of: dict[str, int] = {}
    for path in sorted(weight, key=lambda p: (-weight[p], p)):
        lightest = min(range(total), key=lambda i: (load[i], i))
        load[lightest] += weight[path]
        shard_of[path] = lightest + 1
    return shard_of


def select(items: list, index: int, total: int, durations: dict[str, float]):
    """Split `items` into (kept, dropped) for shard `index` of `total`."""
    shard_of = assign([item.nodeid for item in items], total, durations)
    kept, dropped = [], []
    for item in items:
        (kept if shard_of[item.nodeid.split("::", 1)[0]] == index else dropped).append(item)
    return kept, dropped
