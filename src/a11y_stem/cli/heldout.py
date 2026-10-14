"""CLI side of the pre-registered held-out set: `--set heldout` on `gold` and `eval`.

Where the set's files live and what a `--set heldout` command may touch. The set itself
(sections, hashes, fetch, check) is `a11y_stem.gold.heldout`.

Everything the set produces stays outside the repository (pre-registration, section 10):
the gold, the OpenStax archive responses, the render cache (its printed PDFs and equation
images) and the reports. Several research scripts read every PDF under the repository's
`.cache/eval/pages/`, and `data/eval/` holds the development reports the CI gate compares,
so a held-out file placed in either would flow into development or into the gate. The
default folder is `$XDG_CACHE_HOME/a11y-stem/heldout` (`~/.cache/a11y-stem/heldout`):

    gold/        gold fetch --set heldout          (the 15 sections' answer key)
    openstax/    the archive responses behind it   (page XHTML the PDFs are printed from)
    eval-cache/  eval pipeline / render            (PDFs, equation images, figure images)
    reports/     eval pipeline                     (reports and per-item rows)
    pdfs/        eval render --all                 (the 15 printed sections, pages.json)
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from a11y_stem.gold import heldout as registry


def default_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "a11y-stem" / "heldout"


@dataclass(frozen=True)
class Locations:
    """Where a `--set heldout` command reads and writes, under one folder."""

    root: Path

    @property
    def gold(self) -> Path:
        return self.root / "gold"

    @property
    def openstax(self) -> Path:
        return self.root / "openstax"

    @property
    def eval_cache(self) -> Path:
        return self.root / "eval-cache"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def pdfs(self) -> Path:
        return self.root / "pdfs"


def locations(heldout_dir: Path | None) -> Locations:
    return Locations((heldout_dir or default_root()).expanduser())


def checkout_root() -> Path | None:
    """The project folder this command runs in; None outside a checkout."""
    from a11y_stem.cli.main import _PROJECT_NAME_LINE, project_root

    root = project_root()
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and _PROJECT_NAME_LINE in pyproject.read_text(encoding="utf-8"):
        return root
    return None


def outside_project(path: Path, what: str) -> str | None:
    """An error message when `path` is inside the repository, else None."""
    root = checkout_root()
    if root is None:
        return None
    resolved = path.expanduser().resolve()
    if resolved == root.resolve() or root.resolve() in resolved.parents:
        return (
            f"{what} {path} is inside the repository. The held-out set's files stay outside "
            f"it (pre-registration, section 10); the default is {default_root()}"
        )
    return None


def unselected(gold_dir: Path) -> str | None:
    """An error message when `gold_dir` holds the held-out gold and `--set heldout` was
    not given: without it a command writes its caches and reports to the repository's
    default places (`.cache/eval/pages/`, `data/eval/`, the demo)."""
    if any((gold_dir / s.path).is_file() for s in registry.REGISTERED.sections):
        return (
            f"{gold_dir} holds the held-out gold. Add --set heldout: it keeps the caches and "
            "reports outside the repository and checks the gold against the registered one"
        )
    return None


def gold_problem(problems: list[str], gold_dir: Path) -> str:
    lines = "".join(f"\n  - {p}" for p in problems)
    fetch = (
        "fetch it with: a11y-stem gold fetch --set heldout --allow-retired "
        f"--date {registry.REGISTERED.date}"
    )
    return f"{gold_dir} is not the pre-registered held-out gold:{lines}\n{fetch}"


def resolve(
    heldout_dir: Path | None,
    gold: Path | None,
    cache: Path | None = None,
    writes: tuple[tuple[str, Path | None], ...] = (),
) -> tuple[Locations, Path, Path] | str:
    """(locations, gold dir, render cache) for a `--set heldout` command, or an error.

    The gold must be the registered one (`verify_gold`, which a run needs before it
    starts) and nothing the command writes may lie inside the repository: `writes` names
    the output paths the caller was given (`--out`, `--items`, `--report`, ...).
    """
    loc = locations(heldout_dir)
    gold_dir = gold or loc.gold
    cache_dir = cache or loc.eval_cache
    checks = [("--heldout-dir", loc.root), ("--cache", cache_dir)]
    checks += [(name, path) for name, path in writes if path is not None]
    for what, path in checks:
        message = outside_project(path, what)
        if message:
            return message
    problems = registry.verify_gold(gold_dir)
    if problems:
        return gold_problem(problems, gold_dir)
    return loc, gold_dir, cache_dir


def pdf_page_count(pdf: bytes) -> int:
    import pymupdf

    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return doc.page_count


def add_set_arguments(parser: argparse.ArgumentParser) -> None:
    """`--set heldout|dev-v2`, `--heldout-dir` and `--part` for an `eval` subcommand (dest
    `gold_set`)."""
    parser.add_argument(
        "--set",
        dest="gold_set",
        choices=["heldout", "dev-v2"],
        help="heldout: the pre-registered held-out OpenStax set: gold from the held-out "
        "folder (checked against the registered hashes first), caches and reports outside "
        "the repository, no --fake. dev-v2: the extended development set "
        "(data/dev-v2, docs/eval.md); --part dev (default) is for building, --part check "
        "is the small sealed part",
    )
    parser.add_argument(
        "--heldout-dir",
        type=Path,
        help="with --set heldout: the folder for its gold, caches and reports "
        f"(default: {default_root()})",
    )
    parser.add_argument(
        "--devset-dir",
        type=Path,
        help="with --set dev-v2: the set's folder (default: <project>/data/dev-v2)",
    )
    parser.add_argument(
        "--part",
        choices=["dev", "check"],
        default="dev",
        help="with --set dev-v2: dev (default) or the sealed check part (totals only, "
        "no --items, files kept outside the repository)",
    )
