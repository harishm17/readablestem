"""Upgrade check for PyMuPDF and its layout add-on: does the probe still guard the table
step? Run before any bump of pymupdf, pymupdf-layout or onnxruntime (`make fuzz-tables`;
marked `slow`, so the default run skips it).

Byte mutants of the repo's own fixtures (MIT, no instructor content) go through
`layout.analyze` in a child process each, exactly as a job would: the probe child first,
then extraction, table detection and structure in the child itself. A child that dies (a
signal, MuPDF's abort) or hangs after the probe let its file in is an escape: the worker
would have died the same way. Files the probe rejects, and Python errors, are fine.

TABLE_FUZZ_MUTANTS sets how many mutants (default 300; the 2026-10-09 evaluation ran 837
files through the same check with no escape); TABLE_FUZZ_JOBS how many run at once.
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SEEDS = sorted(
    [
        *(FIXTURES / "layout").glob("*.pdf"),
        *(FIXTURES / "layout" / "latex").glob("*.pdf"),
        *(FIXTURES / "tables").glob("*.pdf"),
    ]
)
MUTANTS = int(os.environ.get("TABLE_FUZZ_MUTANTS", "300"))
JOBS = int(os.environ.get("TABLE_FUZZ_JOBS", str(max(1, min(8, (os.cpu_count() or 2) // 2)))))
TIMEOUT_S = 180.0
MEMORY_BYTES = 3 << 30

CHILD = r"""
import os, resource, sys
cpus = sorted(os.sched_getaffinity(0))
os.sched_setaffinity(0, {cpus[int(sys.argv[1]) % len(cpus)]})
limit = int(sys.argv[2])
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
from a11y_stem.pipeline import layout
data = sys.stdin.buffer.read()
try:
    layout.analyze(data, max_pages=30)
except Exception:  # LayoutError (the probe said no) and Python errors are handled
    pass
sys.stdout.write("ok")
"""


def mutant(seed: Path, index: int) -> bytes:
    """The same mutation as the 2026-10-08 evaluation: 1-64 byte edits (overwrite,
    delete a run, or copy a run from elsewhere in the file)."""
    rng = random.Random(index)
    data = bytearray(seed.read_bytes())
    for _ in range(rng.choice([1, 4, 16, 64])):
        pos = rng.randrange(len(data))
        op = rng.random()
        if op < 0.6:
            data[pos] = rng.randrange(256)
        elif op < 0.8:
            data[pos : pos + rng.randrange(1, 64)] = b""
        else:
            chunk = data[rng.randrange(len(data)) :][: rng.randrange(1, 128)]
            data[pos:pos] = chunk
    return bytes(data)


def outcome(index: int) -> tuple[str, int]:
    seed = SEEDS[index % len(SEEDS)]
    data = mutant(seed, index)
    try:
        result = subprocess.run(
            [sys.executable, "-c", CHILD, str(index), str(MEMORY_BYTES)],
            input=data,
            capture_output=True,
            timeout=TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "timeout", index
    if result.returncode < 0:
        return f"signal{-result.returncode}", index
    if result.returncode != 0 or not result.stdout.startswith(b"ok"):
        return f"exit{result.returncode}", index
    return "ok", index


@pytest.mark.slow
def test_no_mutant_passes_the_probe_and_then_kills_the_table_step():
    assert SEEDS, "no fixture PDFs to mutate"
    with ThreadPoolExecutor(JOBS) as pool:
        results = list(pool.map(outcome, range(MUTANTS)))
    counts = Counter(name for name, _ in results)
    escapes = [(name, SEEDS[i % len(SEEDS)].name, i) for name, i in results if name != "ok"]
    print(f"\n{MUTANTS} mutants of {len(SEEDS)} fixtures: {dict(counts)}")
    # An escape: rebuild it with mutant(SEEDS[i % len(SEEDS)], i) and keep it as a fixture.
    assert escapes == []
