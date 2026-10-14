"""CLI side of the extended development set: `gold devset ...` and `--set dev-v2` on `eval`.

The set itself (classes, selection, parts, seal) is `a11y_stem.gold.devset`. Two commands
build it (`select`, `build`), two inspect it (`summary`, `verify`). `eval pipeline --set
dev-v2` runs a task on it; `--part check` runs the sealed part, which keeps its extracted
files, caches and reports outside the repository and reports totals only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from a11y_stem.cli import heldout as heldout_cli
from a11y_stem.gold import devset as ds
from a11y_stem.gold import devset_build as build

DEFAULT_SELECTION = "selection.json"


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def data_root(path: Path | None = None) -> Path:
    from a11y_stem.cli.main import project_root

    return path or project_root() / ds.DATA_DIR


def cache_home() -> Path:
    """Where the sealed part is extracted and its runs are cached: outside the repository,
    next to the held-out folder (`$XDG_CACHE_HOME/a11y-stem/dev-v2`)."""
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "a11y-stem" / "dev-v2"


# ------------------------------------------------------------------ eval --set dev-v2


def resolve_eval(
    part: str,
    root: Path | None,
    gold: Path | None,
    cache: Path | None,
    writes: tuple[tuple[str, Path | None], ...] = (),
) -> tuple[Path, Path | None, Path | None, dict] | str:
    """(gold dir, render cache, archive cache, report extras) for a `--set dev-v2` command,
    or an error message.

    `dev` is the committed gold (its files are checked against the manifest first). `check`
    is extracted from the sealed archive after its hash is checked; nothing it makes may
    lie inside the repository.
    """
    base = data_root(root)
    if gold is not None:
        return "--gold does not apply to --set dev-v2 (the set knows its own folders)"
    problems = ds.dev_problems(base) if part == "dev" else ds.seal_problems(base)
    if problems:
        lines = "".join(f"\n  - {p}" for p in problems)
        return f"the {part} part of the extended development set is not intact:{lines}"
    if part == "dev":
        return base / "dev", cache, None, {"set": ds.DEVSET, "part": "dev"}
    home = cache_home()
    cache_dir = cache or home / "eval-cache"
    for what, path in (("--cache", cache_dir), *writes):
        if path is None:
            continue
        message = heldout_cli.outside_project(path, what)
        if message:
            return message.replace("held-out", "sealed check")
    try:
        gold_dir = ds.load_check_part(base, home)
    except ds.DevsetError as exc:
        return str(exc)
    seal = json.loads((base / ds.SEAL_FILE).read_text(encoding="utf-8"))
    extra = {"set": ds.DEVSET, "part": "check", "seal_archive_sha256": seal["archive_sha256"]}
    return gold_dir, cache_dir, home / "openstax", extra


def report_path(part: str, task: str, out: Path | None) -> Path:
    if out is not None:
        return out
    from a11y_stem.cli.main import project_root

    if part == "check":
        return cache_home() / "reports" / f"check-{task}.json"
    return project_root() / ".local" / "eval" / "dev-v2" / f"dev-{task}.json"


# ----------------------------------------------------------------------- gold devset


def _config(selection: dict) -> ds.SelectionConfig:
    cfg = selection["config"]
    return ds.SelectionConfig(
        salt=cfg["salt"],
        archive_version=cfg["archive_version"],
        books=cfg["books"],
        dev_targets=cfg["dev_targets"],
        check_targets=cfg["check_targets"],
        max_sections=cfg["max_sections"],
        max_pages=cfg["max_pages"],
    )


def _cmd_summary(args: argparse.Namespace) -> int:
    base = data_root(args.root)
    manifest_path = base / "dev" / "manifest.json"
    if not manifest_path.is_file():
        return _error(f"{manifest_path} not found (build the set first)")
    dev = json.loads(manifest_path.read_text(encoding="utf-8"))
    seal_path = base / ds.SEAL_FILE
    check = (
        json.loads(seal_path.read_text(encoding="utf-8"))["class_counts"]
        if seal_path.is_file()
        else None
    )
    rows = ds.summary_rows(dev, check)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(ds.summary_markdown(rows), end="")
        docs = dev["authored_documents"]
        pages = sum(
            f["path"].startswith("openstax/") and f["path"].endswith(".jsonl") for f in dev["files"]
        )
        print(
            f"\ndev: {len(dev['books'])} OpenStax books, {pages} sections or pages, "
            f"{len(docs)} authored documents; manifest sha256 {ds.sha256_file(manifest_path)}"
        )
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    base = data_root(args.root)
    problems = ds.verify(base)
    if args.overlap:
        problems += _overlap_problems(base)
    if problems:
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    seal = json.loads((base / ds.SEAL_FILE).read_text(encoding="utf-8"))
    print(
        f"{base.name}: dev part matches its manifest; check part is the sealed archive "
        f"(sha256 {seal['archive_sha256']})"
    )
    return 0


def _overlap_problems(base: Path) -> list[str]:
    """Recompute each OpenStax page's 8-gram overlap with the held-out text (needs the
    held-out folder's archive cache, read-only)."""
    from a11y_stem.gold.openstax import HttpFetcher

    held = HttpFetcher(cache_dir=heldout_cli.locations(None).openstax)
    grams = ds.heldout_grams(held)
    from a11y_stem.cli.main import project_root

    fetcher = HttpFetcher(cache_dir=project_root() / ".cache" / "openstax")
    problems = []
    seen = set()
    for path in sorted((base / "dev" / "openstax").glob("*/*.jsonl")):
        record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        if record["content_url"] in seen:
            continue
        seen.add(record["content_url"])
        xhtml = fetcher.get_json(record["content_url"])["content"]
        share = ds.containment(ds.words_outside_math(xhtml), grams)
        print(f"{path.stem}: overlap with the held-out text {share:.4f}")
        if share >= ds.NEAR_DUP:
            problems.append(f"{path.stem} overlaps the held-out text by {share:.4f}")
    return problems


def _cmd_select(args: argparse.Namespace) -> int:
    from a11y_stem.cli.main import project_root
    from a11y_stem.gold.openstax import HttpFetcher

    base = data_root(args.root)
    selection_path = base / DEFAULT_SELECTION
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    config = _config(selection)
    fetcher = HttpFetcher(cache_dir=project_root() / ".cache" / "openstax")
    held = HttpFetcher(cache_dir=heldout_cli.locations(None).openstax)
    printer = build.PagePrinter(config, fetcher, project_root() / ".cache" / "eval")
    try:
        plan = build.make_plan(config, fetcher, ds.heldout_grams(held), printer.count)
    finally:
        printer.close()
    print(f"candidates {plan.pool_size} (overlap below {ds.NEAR_DUP}); dev pool {plan.dev_pool}")
    print(f"dev: {len(plan.dev)} sections, {plan.pages('dev')} Letter pages with key terms")
    print(f"check: {len(plan.check)} sections, {plan.pages('check')} pages (names not printed)")
    committed = [(s["book"], s["slug"]) for s in selection.get("dev", [])]
    chosen = [(c.book, c.slug) for c in plan.dev]
    if committed and committed != chosen:
        return _error("the dev sections chosen now differ from selection.json")
    if args.write:
        selection["dev"] = plan.dev_rows()
        selection["dev_key_terms"] = plan.key_terms_rows("dev")
        selection_path.write_text(
            json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {selection_path}")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    from a11y_stem.cli.main import project_root
    from a11y_stem.gold.openstax import GoldError, HttpFetcher

    base = data_root(args.root)
    selection = json.loads((base / DEFAULT_SELECTION).read_text(encoding="utf-8"))
    config = _config(selection)
    cache = project_root() / ".cache" / "openstax"
    fetcher = HttpFetcher(cache_dir=cache)
    held = HttpFetcher(cache_dir=heldout_cli.locations(None).openstax)
    printer = build.PagePrinter(config, fetcher, project_root() / ".cache" / "eval")
    try:
        build.build_set(
            base,
            config,
            selection,
            fetcher,
            held,
            authored_pool=args.authored_pool,
            date=args.date,
            overwrite=args.force,
            page_counter=printer.count,
        )
    except (GoldError, ds.DevsetError) as exc:
        return _error(str(exc))
    finally:
        printer.close()
    problems = ds.verify(base)
    if problems:
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    return _cmd_summary(argparse.Namespace(root=args.root, json=False))


def register(gold: argparse._SubParsersAction) -> None:
    parser = gold.add_parser(
        "devset",
        help="the extended development set (dev-v2): build, summary, verify",
    ).add_subparsers(dest="devset_command", required=True)

    def add_root(p: argparse.ArgumentParser) -> None:
        p.add_argument("--root", type=Path, help="set folder (default: <project>/data/dev-v2)")

    summary = parser.add_parser("summary", help="items per class, dev and check")
    add_root(summary)
    summary.add_argument("--json", action="store_true")
    summary.set_defaults(func=_cmd_summary)

    verify = parser.add_parser(
        "verify", help="dev files against the manifest, check archive against the seal"
    )
    add_root(verify)
    verify.add_argument(
        "--overlap",
        action="store_true",
        help="also recompute every OpenStax page's overlap with the held-out text "
        "(needs the held-out folder's archive cache)",
    )
    verify.set_defaults(func=_cmd_verify)

    select = parser.add_parser(
        "select", help="choose the sections from the candidate books (prints counts only)"
    )
    add_root(select)
    select.add_argument(
        "--write", action="store_true", help="write the dev picks to selection.json"
    )
    select.set_defaults(func=_cmd_select)

    build = parser.add_parser("build", help="fetch the picks and write the set")
    add_root(build)
    build.add_argument(
        "--authored-pool",
        type=Path,
        required=True,
        help="output folder of data/dev-v2/authored/build_authored.py",
    )
    build.add_argument("--date", required=True, help="retrieval and seal date (YYYY-MM-DD)")
    build.add_argument("--force", action="store_true", help="write into a folder that has a set")
    build.set_defaults(func=_cmd_build)
