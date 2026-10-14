"""Check that a change to layout analysis leaves its output exactly as it was.

    uv run python scripts/layout_identity.py snapshot OUT_DIR [--extra DIR ...] [--jobs N]
    uv run python scripts/layout_identity.py compare BEFORE_DIR AFTER_DIR

`snapshot` runs `layout.analyze` (no model call) on every PDF of the repository's own
fixtures and development material and writes one JSON file per PDF to OUT_DIR: every
element in full (kind, page, bbox, text, display, reasons, ... as `Element.model_dump`),
the kept pages and their reasons, the content-free page-type records, the notices, the
title, the language and the page sizes. Run it on the code before a change and again after
it, then `compare` the two directories: it lists every PDF whose output differs, with the
first differing field, and exits 1 if there is any.

PDFs covered without arguments: `tests/fixtures/**.pdf`, `data/**.pdf` (the development
set `data/dev-v2/dev`, the authored table sets, the gold material) and the development
pages printed into `~/.cache/a11y-stem/dev-v2/eval-cache/pages` when that exists. The
sealed check part of the development set (`data/dev-v2/check*`, `~/.cache/a11y-stem/
dev-v2/check-*`) and `proof/` are never read. `--extra DIR` adds the PDFs under DIR
(each is written under a name made from its path).

The ink analysis of a document has a time budget (`page_type.INK_BUDGET_S`), so a PDF of
very dense pages can read differently on a loaded machine: take both snapshots on a quiet
machine, or look at such a PDF by hand.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CACHE_PAGES = Path.home() / ".cache" / "a11y-stem" / "dev-v2" / "eval-cache" / "pages"
SKIP_PARTS = ("check", "proof", ".venv", "node_modules")


def repository_pdfs() -> list[Path]:
    found: list[Path] = []
    for base in (ROOT / "tests" / "fixtures", ROOT / "data", CACHE_PAGES):
        if base.is_dir():
            found += [p for p in base.rglob("*.pdf") if not _skipped(p, base)]
    return sorted(found)


def _skipped(path: Path, base: Path) -> bool:
    parts = path.relative_to(base).parts
    return any(part.startswith("check") or part in SKIP_PARTS for part in parts)


def label(path: Path) -> str:
    for base, prefix in ((ROOT, ""), (CACHE_PAGES, "devcache/")):
        try:
            rel = path.resolve().relative_to(base)
        except ValueError:
            continue
        return prefix + rel.as_posix()
    return "extra/" + hashlib.sha1(str(path).encode()).hexdigest()[:8] + "-" + path.name


def describe(path: str) -> dict[str, Any]:
    """The analysis of one PDF as plain data."""
    from a11y_stem.pipeline import layout

    started = time.perf_counter()
    try:
        result = layout.analyze(Path(path).read_bytes())
    except layout.LayoutError as exc:
        return {"error": exc.code, "seconds": round(time.perf_counter() - started, 2)}
    return {
        "elements": [e.model_dump(mode="json") for e in result.elements],
        "kept_pages": {str(k): v for k, v in result.kept_pages.items()},
        "page_types": {
            str(n): result.page_types[n].to_record(n).model_dump(mode="json")
            for n in sorted(result.page_types)
        },
        "notices": [n.model_dump(mode="json") for n in result.notices],
        "title": result.title,
        "lang": result.lang,
        "page_count": result.page_count,
        "pages": result.pages,
        "page_sizes": [list(s) for s in result.page_sizes],
        "seconds": round(time.perf_counter() - started, 2),
    }


def snapshot(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pdfs = repository_pdfs()
    for extra in args.extra or []:
        pdfs += sorted(Path(extra).rglob("*.pdf"))
    names = {label(p): p for p in pdfs}
    print(f"{len(names)} PDFs -> {out}", flush=True)
    failed = 0
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(describe, str(p)): name for name, p in names.items()}
        for done in as_completed(futures):
            name = futures[done]
            try:
                data = done.result()
            except Exception as exc:  # a crash is a finding, recorded and shown
                data = {"crash": f"{type(exc).__name__}: {exc}"}
                failed += 1
            target = out / (name.replace("/", "__") + ".json")
            target.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False))
            print(f"  {name}: {data.get('seconds', '-')} s", flush=True)
    return 1 if failed else 0


def _first_difference(a: Any, b: Any, path: str = "") -> str | None:
    if type(a) is not type(b):
        return f"{path}: {a!r} != {b!r}"[:300]
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                return f"{path}/{key}: present in one only"
            found = _first_difference(a[key], b[key], f"{path}/{key}")
            if found:
                return found
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} items != {len(b)} items"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            found = _first_difference(x, y, f"{path}[{i}]")
            if found:
                return found
        return None
    return None if a == b else f"{path}: {a!r} != {b!r}"[:300]


def compare(args: argparse.Namespace) -> int:
    before, after = Path(args.before), Path(args.after)
    names = sorted({p.name for p in before.glob("*.json")} | {p.name for p in after.glob("*.json")})
    different = same = 0
    for name in names:
        if not (before / name).exists() or not (after / name).exists():
            print(f"MISSING {name}: only in {'before' if (before / name).exists() else 'after'}")
            different += 1
            continue
        a = json.loads((before / name).read_text())
        b = json.loads((after / name).read_text())
        a.pop("seconds", None)
        b.pop("seconds", None)
        found = _first_difference(a, b)
        if found:
            different += 1
            print(f"DIFFERENT {name}: {found}")
        else:
            same += 1
    elements = sum(len(json.loads(p.read_text()).get("elements", [])) for p in after.glob("*.json"))
    print(f"{same} identical, {different} different, {len(names)} PDFs, {elements} elements")
    return 1 if different else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("out")
    snap.add_argument("--extra", action="append", help="another directory of PDFs")
    snap.add_argument("--jobs", type=int, default=max(1, min(4, (os.cpu_count() or 2) // 2)))
    snap.set_defaults(run=snapshot)
    comp = sub.add_parser("compare")
    comp.add_argument("before")
    comp.add_argument("after")
    comp.set_defaults(run=compare)
    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
