"""Time and memory of layout analysis (`layout.analyze`, no model call) on pages that made
it slow.

    uv run python scripts/bench_layout.py                     # the synthetic cases
    uv run python scripts/bench_layout.py --pdf FILE.pdf ...  # and these files, read only
    uv run python scripts/bench_layout.py --only dense,slide --json out.json

Each case runs in a fresh child process, so its peak memory (`ru_maxrss`) is its own. The
synthetic cases come from `tests/layout_synth.py` (random strokes, dots and a slide with
pen strokes; nothing from a real course). `--pdf` files are only read: nothing is copied
or written next to them. Numbers depend on the machine and its load; compare runs made on
the same one, ideally pinned to one core (`taskset -c 2 uv run python scripts/...`) to
look like a 1 vCPU container.

    case            what it is
    dense           6 pages of 70,000 strokes, no text (a handwritten or scanned look)
    dense_titled    the same with a typed title paragraph on the first page
    typed_strokes   one typed page with 40,000 strokes (under the page cap: read in full)
    dots            one typed page with 40,000 separate small squares (a scatter plot)
    slide           a slide with 32,000 path items of pen strokes over a grid of words

The cap (`page_type.PAGE_MAX_PATHS`) and the table detector's item limit
(`tables.MAX_DETECTOR_ITEMS`) are printed with the results.
"""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))


def _cases() -> dict[str, Callable[[], bytes]]:
    from layout_synth import dots_page, ink_document, ink_page, slide_with_ink

    return {
        "dense": lambda: ink_document(6, 70_000),
        "dense_titled": lambda: ink_document(6, 70_000, typed_first=True),
        "typed_strokes": lambda: ink_page(40_000, typed=True),
        "dots": lambda: dots_page(40_000),
        "slide": lambda: slide_with_ink(400, 80),
    }


def child(path: str) -> None:
    """Analyse one PDF and print one JSON line: seconds, peak MB, what was found."""
    from a11y_stem.pipeline import layout

    data = Path(path).read_bytes()
    started = time.perf_counter()
    try:
        result = layout.analyze(data)
        found = {"elements": len(result.elements), "kept_pages": len(result.kept_pages)}
    except layout.LayoutError as exc:
        found = {"error": exc.code}
    seconds = time.perf_counter() - started
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KiB on Linux
    print(json.dumps({"seconds": round(seconds, 2), "peak_mb": round(peak_mb), **found}))


def run_case(name: str, path: Path, timeout: float) -> dict:
    command = [sys.executable, str(Path(__file__).resolve()), "--child", str(path)]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"case": name, "error": f"over {timeout:.0f} s"}
    lines = done.stdout.strip().splitlines()
    if done.returncode != 0 or not lines:
        return {"case": name, "error": f"child exited {done.returncode}"}
    return {"case": name, **json.loads(lines[-1])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pdf", action="append", default=[], help="a PDF to time (read only)")
    parser.add_argument("--only", help="comma-separated synthetic cases to run")
    parser.add_argument("--no-synthetic", action="store_true", help="only the --pdf files")
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds per case")
    parser.add_argument("--json", type=Path, help="also write the results here")
    parser.add_argument("--child", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        child(args.child)
        return 0

    from a11y_stem.pipeline import page_type, tables

    results = []
    with tempfile.TemporaryDirectory(prefix="bench-layout-") as tmp:
        jobs: list[tuple[str, Path]] = []
        if not args.no_synthetic:
            wanted = set(args.only.split(",")) if args.only else None
            for name, build in _cases().items():
                if wanted is None or name in wanted:
                    path = Path(tmp) / f"{name}.pdf"
                    path.write_bytes(build())
                    jobs.append((name, path))
        jobs += [(Path(p).name, Path(p)) for p in args.pdf]
        for name, path in jobs:
            result = run_case(name, path, args.timeout)
            results.append(result)
            print(
                f"{name:24s} " + "  ".join(f"{k}={v}" for k, v in result.items() if k != "case"),
                flush=True,
            )
    caps = {
        "PAGE_MAX_PATHS": getattr(page_type, "PAGE_MAX_PATHS", None),
        "PAGE_MAX_ITEMS": getattr(page_type, "PAGE_MAX_ITEMS", None),
        "MAX_DETECTOR_ITEMS": getattr(tables, "MAX_DETECTOR_ITEMS", None),
    }  # None: this checkout (an older src/ on PYTHONPATH) has no such cap
    print("caps: " + " ".join(f"{k}={v}" for k, v in caps.items()))
    if args.json:
        args.json.write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
