"""The proof numbers the landing page and /limits show, read from the registered report once.

The newest registered held-out result is the fifth fresh split (fresh5, 2026-11-04), written up in
`docs/eval-equation-fresh5.md` (from `scripts/fresh5_report.py`). PROOF.md is the replayed report of
the first registered result and is checked byte for byte against its tag
(`proof/verify-rename.py`), so it is not where a newer result goes. The image the service runs
from has no docs folder, so `parse()` turns the report into `proof_facts.json` beside this file
(`uv run python -m readablestem.web.proof_facts`) and the pages read that.
`tests/test_proof_facts.py` fails when the JSON and the report differ, so a new registered result
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
REPORT = ROOT / "docs" / "eval-equation-fresh5.md"
SNAPSHOT = Path(__file__).with_name("proof_facts.json")
SOURCE = "https://github.com/harishm17/readablestem/blob/main/docs/eval-equation-fresh5.md"
HREF = SOURCE + "#primary-and-secondaries-strata-a-to-c-pooled"
TABLE = "## Primary and secondaries, strata A to C pooled"


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


def _row(section: str, label: str) -> list[str]:
    """The cells of the pooled table's row whose first cell contains `label`, without the
    bold marks: [label, candidate, control, Opus chatbot, ...]."""
    match = re.search(rf"^\|[^|]*{re.escape(label)}[^|]*\|(.*)$", section, re.M)
    assert match, f"{REPORT.name} has no row {label!r} in its pooled table"
    return [cell.strip().replace("**", "") for cell in match.group(0).strip("|").split("|")]


def _number(cell: str) -> str:
    match = re.match(r"\$?([0-9.]+)", cell)
    assert match, cell
    return match.group(1)


def _found(text: str, pattern: str, flags: int = 0) -> re.Match[str]:
    match = re.search(pattern, text, flags)
    assert match, f"{REPORT.name} no longer says {pattern!r}"
    return match


def parse(report: str) -> ProofFacts:
    assert "no difference this set can detect" in report
    assert "beats the chatbot baseline on its equation" in report and "is not made" in report
    section = report[report.index(TABLE) : report.index("## Paired differences")]
    date = _found(report, r"^Run (\d{4}-\d{2}-\d{2}), once", re.M).group(1)
    pipeline = _found(report, r"\(pipeline ([0-9.]+),").group(1)
    size = _row(section, "units / gold / pages")[1]
    units, gold, _pages = (part.strip() for part in size.split("/"))
    goal_silent = _found(report, r"silent errors at most ([0-9.]+) per 100").group(1)
    goal_recall = _found(report, r"flag recall at least ([0-9.]+)").group(1)
    primary = _row(section, "spoken-equal, end to end (primary)")
    exact = _row(section, "exact_lenient (secondary)")
    silent = _row(section, "silent errors per 100 gold")
    recall = _row(section, "flag recall")
    count = f"{gold} gold equations in {units} documents"
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
                f"The chatbot on Opus scored {_number(primary[3])}: this set can detect no "
                "difference between them.",
                HREF,
            ),
            Fact(
                _number(exact[1]),
                "exactly right, end to end",
                count,
                f"The chatbot on Opus scored {_number(exact[3])}.",
                HREF,
            ),
            Fact(
                f"{float(_number(silent[1])):.1f}",
                "wrong equations nobody is asked about, per 100",
                count,
                f"The chatbot on Opus: {float(_number(silent[3])):.1f}. The goal registered "
                f"before the run was {goal_silent} or fewer, so it was not met.",
                HREF,
            ),
            Fact(
                f"{float(_number(recall[1])):.2f}",
                "of wrong equations the review screen asks about",
                count,
                f"The goal registered before the run was {goal_recall}, so it was not met. "
                "A chatbot asks nothing.",
                HREF,
            ),
        ],
    )


def regenerate() -> None:
    facts = parse(REPORT.read_text(encoding="utf-8"))
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
