"""The proof numbers the landing page and /limits show, read from PROOF.md once.

PROOF.md's section "Latest registered result" is the one source: the newest registered held-out
result (fresh5, 2026-11-04), taken from `docs/eval-equation-fresh5.md`. The image the service
runs from has no PROOF.md, so `parse()` turns it into `proof_facts.json` beside this file
(`uv run python -m readablestem.web.proof_facts`) and the pages read that.
`tests/test_proof_facts.py` fails when the JSON and PROOF.md differ, so a new registered result
cannot leave the pages on old numbers.

Every fact names its set, its date, that it is a registered result, its count, and a link to how
it was measured. Nothing here says one tool beats another: each fact gives the chatbot's number
beside ReadableSTEM's and says where a goal registered before the run was not met.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROOF_MD = ROOT / "PROOF.md"
SNAPSHOT = Path(__file__).with_name("proof_facts.json")
SOURCE = "https://github.com/harishm17/readablestem/blob/main/PROOF.md"

LATEST = "## Latest registered result: fresh5"
HISTORY = "## First registered result"


@dataclass(frozen=True)
class Fact:
    value: str  # "0.840"
    label: str  # what it counts
    count: str  # "1,715 gold equations in 26 documents"
    caveat: str  # a plain sentence; gives the chatbot's number and any goal not met
    href: str  # how it was measured


@dataclass(frozen=True)
class ProofFacts:
    set_label: str  # "fresh5 set, 26 documents"
    date: str  # "2026-11-04"
    blind: str  # "Registered before the run; one run."
    pipeline: str  # "version 0.5.9"
    facts: list[Fact]


def _section(proof: str) -> str:
    start = proof.index(LATEST)
    return proof[start : proof.index(HISTORY, start)]


def _row(section: str, label: str) -> list[str]:
    """The cells of the table row of the latest-result section whose first cell contains
    `label`."""
    match = re.search(rf"^\|[^|]*{re.escape(label)}[^|]*\|(.*)$", section, re.M)
    assert match, f"PROOF.md has no row {label!r} in its latest-result section"
    return [cell.strip() for cell in match.group(0).strip("|").split("|")]


def _number(cell: str) -> str:
    match = re.match(r"\$?([0-9.]+)", cell)
    assert match, cell
    return match.group(1)


def _found(section: str, pattern: str, flags: int = 0) -> re.Match[str]:
    match = re.search(pattern, section, flags)
    assert match, f"the latest-result section no longer says {pattern!r}"
    return match


def parse(proof: str) -> ProofFacts:
    section = _section(proof)
    size = _found(section, r"n = (\d+) documents, ([0-9,]+) gold equations")
    units, gold = size.group(1), size.group(2)
    assert "no difference this set can detect" in section
    assert "beats the chatbot baseline on equations is not made" in section
    pipeline = _found(section, r"ReadableSTEM pipeline ([0-9.]+)").group(1)
    date = _found(section, r"^## Latest registered result: fresh5 \(([0-9-]+)\)", re.M).group(1)
    primary = _row(section, "Spoken-equal, end to end (primary)")
    exact = _row(section, "Exact match, tree equal")
    silent = _row(section, "Wrong equations nobody is asked about")
    recall = _row(section, "flag recall")
    goal_silent = _found(section, r"above the registered ([0-9.]+) per 100 gold").group(1)
    goal_recall = _found(section, r"below the registered ([0-9.]+) \(").group(1)
    count = f"{gold} gold equations in {units} documents"
    href = f"{SOURCE}#latest-registered-result-fresh5-{date}"
    return ProofFacts(
        set_label=f"fresh5 set, {units} documents",
        date=date,
        blind="Registered before the run; one run.",
        pipeline=f"version {pipeline}",
        facts=[
            Fact(
                _number(primary[1]),
                "spoken-equal, end to end",
                count,
                f"The chatbot on Opus scored {_number(primary[2])}: this set can detect no "
                "difference between them.",
                href,
            ),
            Fact(
                _number(exact[1]),
                "exactly right, end to end",
                count,
                f"The chatbot on Opus scored {_number(exact[2])}.",
                href,
            ),
            Fact(
                f"{float(_number(silent[1])):.1f}",
                "wrong equations nobody is asked about, per 100",
                count,
                f"The chatbot on Opus: {float(_number(silent[2])):.1f}. The goal registered "
                f"before the run was {goal_silent} or fewer, so it was not met.",
                href,
            ),
            Fact(
                f"{float(_number(recall[1])):.2f}",
                "of wrong equations the review screen asks about",
                count,
                f"The goal registered before the run was {goal_recall}, so it was not met. "
                "A chatbot asks nothing.",
                href,
            ),
        ],
    )


def regenerate() -> None:
    facts = parse(PROOF_MD.read_text(encoding="utf-8"))
    SNAPSHOT.write_text(
        json.dumps(asdict(facts), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


@cache
def load() -> ProofFacts:
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return ProofFacts(
        set_label=data["set_label"],
        date=data["date"],
        blind=data["blind"],
        pipeline=data["pipeline"],
        facts=[Fact(**fact) for fact in data["facts"]],
    )


if __name__ == "__main__":
    regenerate()
    sys.stderr.write(f"wrote {SNAPSHOT}\n")
