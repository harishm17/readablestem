"""A real eval run keeps its recording: the model's answers and the per-item rows.

A live run costs money and cannot be made again the same way, and the numbers in a report
cannot be re-scored without the rows behind them. The 2026-10-21 chemistry held-out run left
only a report: its answers were not written anywhere, so its classes could not be replayed.
So `eval pipeline`, `eval sample` and `eval calibrate --probes` write, for every run that
calls a real model, a folder

    <set root>/runs/<UTC timestamp>/
        answers.jsonl   every model answer (`eval.answers`; replay it with --answers)
        items.jsonl     the per-item rows (what `scripts/dev_chem_classes.py` reads)
        report.json     a copy of the report
        run.json        the commit the code was at, and whether the tree was clean

and the report names the folder (relative to the set root, never a home path) and the commit
under `recording`. The set root is the set's cache folder: `~/.cache/readablestem/heldout`,
`.../dev-v2`, `.../gold`, or the folder given to `--heldout-dir`. `--answers` and `--items`
still choose other files; a file given by hand is not copied. A fake run, a replay
(`--answers-offline`) and a run with no model (tables, local mode) keep nothing: they spend
nothing and can be made again. The sealed check part keeps nothing either (its items are not
listed, by design).

A recording holds the text layers of the evaluated PDFs and the model's answers, so the folders
are private to the user: `runs/` and each run folder are mode 0700 and every file in them 0600.
`prune` deletes the runs older than an age and keeps any run a committed report names.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from readablestem.core.paths import cache_dir

RUNS = "runs"
DIR_MODE = 0o700
FILE_MODE = 0o600
# A run folder of a held-out or sealed set: its rows list items one by one, so the analysis
# scripts refuse it (`sealed_reason`).
SEALED_WORDS = ("heldout", "held-out", "sealed", "check")
# A whole word of a folder or set name (`chem-heldout`, `check`), not `checkout`.
_SEALED = re.compile(r"(?:^|[-_.])(" + "|".join(SEALED_WORDS) + r")(?:$|[-_.])")


@dataclass(frozen=True)
class Recording:
    """One run's folder."""

    set_name: str
    directory: Path
    sha: str
    dirty: bool

    @property
    def answers(self) -> Path:
        return self.directory / "answers.jsonl"

    @property
    def items(self) -> Path:
        return self.directory / "items.jsonl"

    @property
    def report(self) -> Path:
        return self.directory / "report.json"

    @property
    def relative(self) -> str:
        """`<set>/runs/<timestamp>`: what a report says; no home path."""
        return f"{self.set_name}/{RUNS}/{self.directory.name}"

    def describe(self, *, answers: bool, items: bool) -> dict[str, Any]:
        files = [
            name for name, kept in (("answers.jsonl", answers), ("items.jsonl", items)) if kept
        ]
        return {
            "run": self.relative,
            "sha": self.sha + ("+dirty" if self.dirty else ""),
            "files": [*files, "report.json", "run.json"],
        }


def set_root(set_name: str) -> Path:
    """`~/.cache/readablestem/<set>`."""
    return cache_dir(set_name)


def source_state(repo: Path) -> tuple[str, bool]:
    """(commit SHA, tree has changes); ("unknown", False) outside a git checkout."""
    try:
        sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown", False
    return sha, bool(status)


def start(
    root: Path,
    set_name: str,
    *,
    repo: Path,
    now: dt.datetime | None = None,
    task: str | None = None,
) -> Recording:
    """Make `<root>/runs/<timestamp>/` (a folder that is new) and write `run.json` in it."""
    moment = (now or dt.datetime.now(dt.UTC)).strftime("%Y%m%dT%H%M%SZ")
    runs = root / RUNS
    runs.mkdir(parents=True, exist_ok=True)
    runs.chmod(DIR_MODE)
    for attempt in range(1, 100):
        directory = runs / (moment if attempt == 1 else f"{moment}-{attempt}")
        try:
            directory.mkdir(mode=DIR_MODE)
            directory.chmod(DIR_MODE)
            break
        except FileExistsError:
            continue
    else:  # pragma: no cover - a hundred runs in one second
        raise RuntimeError(f"no free run folder under {runs}")
    sha, dirty = source_state(repo)
    recording = Recording(set_name, directory, sha, dirty)
    for name in ("answers.jsonl", "items.jsonl", "report.json"):
        _private_file(directory / name)  # made now, so every writer inherits mode 0600
    (directory / "run.json").write_text(
        json.dumps(
            {"set": set_name, "task": task, "started": moment, "sha": sha, "dirty": dirty},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (directory / "run.json").chmod(FILE_MODE)
    return recording


def _private_file(path: Path) -> None:
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT, FILE_MODE))
    path.chmod(FILE_MODE)


def holds_nothing(directory: Path) -> bool:
    """The run stopped before it kept anything (only `run.json` and empty files)."""
    return all(p.name == "run.json" or p.stat().st_size == 0 for p in directory.iterdir())


def discard(directory: Path) -> None:
    shutil.rmtree(directory)


def sealed_reason(path: Path) -> str | None:
    """Why a run folder, an items file in one, or a gold folder must not be analysed item by
    item: it belongs to a held-out or sealed set. None when it does not."""
    resolved = path.resolve()
    folder = resolved if resolved.is_dir() else resolved.parent
    info = folder / "run.json"
    if info.is_file():
        try:
            name = str(json.loads(info.read_text(encoding="utf-8")).get("set", ""))
        except (OSError, ValueError):
            name = ""
        if _SEALED.search(name.lower()):
            return f"a run of the {name!r} set"
    for part in resolved.parts:
        if found := _SEALED.search(part.lower()):
            return f"the path names a {found.group(1)} set"
    return None


def referenced_runs(repo: Path) -> set[str]:
    """Every `recording.run` a committed report (any tracked JSON file) names: those runs are
    the evidence behind a published number."""
    try:
        listed = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "*.json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.split("\n")
    except (OSError, subprocess.SubprocessError):
        listed = [str(p.relative_to(repo)) for p in repo.rglob("*.json") if ".git" not in p.parts]
    found: set[str] = set()
    for name in filter(None, listed):
        try:
            data = json.loads((repo / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        run = data.get("recording", {}).get("run") if isinstance(data, dict) else None
        if isinstance(run, str):
            found.add(run)
    return found


def _started(directory: Path) -> dt.datetime | None:
    stamp = directory.name.split("-", 1)[0]
    try:
        return dt.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC)
    except ValueError:
        return None


def prune(
    root: Path,
    set_name: str,
    *,
    older_than_days: float,
    keep: set[str],
    now: dt.datetime | None = None,
    dry_run: bool = False,
) -> tuple[list[Path], list[Path]]:
    """Delete the runs of `<root>/runs/` older than the age; return (deleted, kept because a
    committed report names them). A folder whose name is not a timestamp is never touched."""
    cutoff = (now or dt.datetime.now(dt.UTC)) - dt.timedelta(days=older_than_days)
    deleted: list[Path] = []
    kept: list[Path] = []
    runs = root / RUNS
    if not runs.is_dir():
        return deleted, kept
    for directory in sorted(p for p in runs.iterdir() if p.is_dir()):
        started = _started(directory)
        if started is None or started >= cutoff:
            continue
        if f"{set_name}/{RUNS}/{directory.name}" in keep:
            kept.append(directory)
            continue
        if not dry_run:
            discard(directory)
        deleted.append(directory)
    return deleted, kept
