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
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from readablestem.core.paths import cache_dir

RUNS = "runs"


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
    for attempt in range(1, 100):
        directory = runs / (moment if attempt == 1 else f"{moment}-{attempt}")
        try:
            directory.mkdir()
            break
        except FileExistsError:
            continue
    else:  # pragma: no cover - a hundred runs in one second
        raise RuntimeError(f"no free run folder under {runs}")
    sha, dirty = source_state(repo)
    recording = Recording(set_name, directory, sha, dirty)
    (directory / "run.json").write_text(
        json.dumps(
            {"set": set_name, "task": task, "started": moment, "sha": sha, "dirty": dirty},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return recording
