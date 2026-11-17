"""Development and held-out documents, and when a score may be held against a bar.

A bar set on the pages a fix was built on says nothing about the next document. So an eval's
set is split in two, by document and never by page: two pages of one file are never on both
sides. Thresholds, prompts and rules are tuned on the development side. The held-out side is
read only by the scorer.

    chosen = split.split("order-2026-11", {"doc-a": "<sha256 of the file>", ...})
    chosen.held_out            # the document ids of that side
    chosen.stamp(HELD_OUT)     # what the run's report records under "split"

Which side a document is on depends only on the seed and the document's id
(`sha256(seed, id)`), so adding a document moves no other. A held-out set that was used to
choose between designs is spent: pass its documents as `spent` and they are development from
then on.

A report can be held against a bar only when it says four things: which side it was scored on
(`split.name`), which documents (`split.set_sha256`, a hash of the side's ids and file
hashes), the settings (`profile.sha256`, `ops/profile.py`) and the code
(`pipeline_version`). `judge` refuses a report that lacks one. A score on the development
side, or under settings that are not the production profile, is a "development score" and
meets no bar. `readablestem eval bar` is the command.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from readablestem.ops import profile as run_profile

DEVELOPMENT = "development"
HELD_OUT = "held-out"
SIDES = (DEVELOPMENT, HELD_OUT)

Status = Literal["met", "not met", "development", "refused"]
# What `eval bar` exits with: 0 only when the bar is met; 2 when no bar could be judged.
EXIT: dict[Status, int] = {"met": 0, "not met": 1, "development": 2, "refused": 2}


class SetChanged(ValueError):
    """The documents on a side are not the ones the set was registered with."""


def side(seed: str, document: str, *, held_out_share: float = 0.5) -> str:
    """The side `document` is on under `seed`."""
    digest = hashlib.sha256(f"{seed}\0{document}".encode()).digest()
    place = int.from_bytes(digest[:8], "big") / 2**64
    return HELD_OUT if place < held_out_share else DEVELOPMENT


@dataclass(frozen=True)
class Split:
    seed: str
    development: tuple[str, ...]
    held_out: tuple[str, ...]
    hashes: Mapping[str, str]  # document id -> sha256 of its file

    def documents(self, name: str) -> tuple[str, ...]:
        if name not in SIDES:
            raise ValueError(f"a side is one of {SIDES}, not {name!r}")
        return self.held_out if name == HELD_OUT else self.development

    def set_hash(self, name: str) -> str:
        """Names the side's documents and their contents: it changes when a document joins,
        leaves or is edited."""
        lines = "".join(f"{doc}\0{self.hashes[doc]}\n" for doc in self.documents(name))
        return hashlib.sha256(lines.encode()).hexdigest()

    def verify(self, name: str, registered: str) -> None:
        """Before a run: the side is the one whose hash was written down."""
        found = self.set_hash(name)
        if found != registered:
            raise SetChanged(
                f"the {name} set changed: registered {registered[:12]}, found {found[:12]}"
            )

    def stamp(self, name: str) -> dict[str, Any]:
        """What a report records under `split`."""
        return {
            "name": name,
            "seed": self.seed,
            "set_sha256": self.set_hash(name),
            "documents": len(self.documents(name)),
        }


def split(
    seed: str,
    documents: Mapping[str, str],
    *,
    held_out_share: float = 0.5,
    spent: Collection[str] = (),
) -> Split:
    """Split `documents` (id -> sha256 of the file) by document. `spent`: documents whose
    held-out reading was used to choose a design; they are development."""
    unknown = sorted(set(spent) - set(documents))
    if unknown:
        raise ValueError(f"spent documents not in the set: {', '.join(unknown)}")
    sides: dict[str, list[str]] = {DEVELOPMENT: [], HELD_OUT: []}
    for document in sorted(documents):
        name = (
            DEVELOPMENT
            if document in spent
            else side(seed, document, held_out_share=held_out_share)
        )
        sides[name].append(document)
    return Split(seed, tuple(sides[DEVELOPMENT]), tuple(sides[HELD_OUT]), dict(documents))


@dataclass(frozen=True)
class Verdict:
    status: Status
    line: str

    @property
    def exit_code(self) -> int:
        return EXIT[self.status]


def _measure(report: Mapping[str, Any], name: str) -> float:
    value: Any = report.get("metrics", {})
    for part in name.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise KeyError(name)
        value = value[part]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise KeyError(name)
    return float(value)


def _missing(report: Mapping[str, Any]) -> list[str]:
    """The four things a report must say, by what is absent."""
    chosen = report.get("split") or {}
    settings = report.get("profile") or {}
    wanted = (
        ("the split name", chosen.get("name") in SIDES),
        ("the set hash", bool(chosen.get("set_sha256"))),
        ("the profile hash", bool(settings.get("sha256"))),
        ("the pipeline version", bool(report.get("pipeline_version"))),
    )
    return [what for what, present in wanted if not present]


def judge(
    report: Mapping[str, Any],
    measure: str,
    *,
    at_least: float | None = None,
    at_most: float | None = None,
    kind: Literal["release", "goal"] = "release",
) -> Verdict:
    """Hold `report`'s `metrics.<measure>` against a bar. Exactly one of `at_least` and
    `at_most` is the bar."""
    if (at_least is None) == (at_most is None):
        raise ValueError("give one bar: at_least or at_most")
    missing = _missing(report)
    if missing:
        return Verdict("refused", f"refused: the report does not say {', '.join(missing)}")
    try:
        value = _measure(report, measure)
    except KeyError:
        return Verdict("refused", f"refused: the report has no number at metrics.{measure}")
    bar = at_least if at_least is not None else at_most
    assert bar is not None
    shown = f"{measure} = {value:g}"
    against = f"{kind} bar {'at least' if at_least is not None else 'at most'} {bar:g}"
    chosen, settings = report["split"], report["profile"]
    where = f"set {chosen['set_sha256'][:12]}, pipeline {report['pipeline_version']}"
    reasons = []
    if chosen["name"] != HELD_OUT:
        reasons.append("scored on development documents")
    if settings.get("name") == run_profile.DEVELOPMENT:
        reasons.append("not run under the production profile")
    if reasons:
        return Verdict(
            "development",
            f"development score: {shown} ({' and '.join(reasons)}; {against} not judged; {where})",
        )
    met = value >= bar if at_least is not None else value <= bar
    where = f"held-out {where}, profile {settings['sha256'][:12]}"
    if met:
        return Verdict("met", f"bar met: {shown}, {against} ({where})")
    return Verdict("not met", f"bar not met: {shown}, {against} ({where})")
