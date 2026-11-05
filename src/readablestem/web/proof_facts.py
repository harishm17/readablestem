"""The proof numbers the landing page and /limits show, read from the registered report once.

The newest registered held-out result is the fifth fresh split (fresh5, 2026-11-04), written up in
`docs/eval-equation-fresh5.md` (from `scripts/fresh5_report.py`). PROOF.md is the replayed report of
the first registered result and is checked byte for byte against its tag
(`proof/verify-rename.py`), so it is not where a newer result goes. The image the service runs
from has no docs folder, so `parse()` turns the report into `proof_facts.json` beside this file
(`uv run python -m readablestem.web.proof_facts`) and the pages read that.
`tests/test_proof_facts.py` fails when the JSON and the report differ, so a new registered result
cannot leave the pages on old numbers.

The comparison chart (`chart`) is read from `data/realworld/results-fresh5.json`, the same run's
numbers, and the small headings-and-figures block (`first_test`) from the first registered test's
tables in `PROOF.md`, so the pages draw their bars and counts from the reports and cannot drift
from them (`tests/test_proof_facts.py` compares the snapshot with both).

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
RESULTS = ROOT / "data" / "realworld" / "results-fresh5.json"
PROOF = ROOT / "PROOF.md"
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
class ChartLine:
    system: str  # "ReadableSTEM", "Chatbot on Opus", "Chatbot on Haiku"
    value: str  # as shown: "0.793", "5.0", "$0.022", "never asks"
    width: float  # the bar's length, percent of the row's axis


@dataclass(frozen=True)
class ChartRow:
    measure: str  # what is counted, in plain words
    better: str  # "Higher is better" or "Lower is better"
    lines: list[ChartLine]
    headline: bool = False  # one of the two the landing page's compact chart shows


@dataclass(frozen=True)
class FirstTest:
    """The first registered test (PROOF.md): headings and figure descriptions, with counts."""

    set_label: str  # "15 OpenStax sections, 242 pages, October 2026, version 0.4.0"
    levels_ours: str  # "497 of 497 (100%)"
    levels_chatbot: str  # "320 of 939 (34%)"
    errors_ours: str  # "0 of 138"
    errors_chatbot: str  # "3 of 134"


@dataclass(frozen=True)
class ProofFacts:
    set_label: str  # "fresh5 set, 26 documents"
    date: str  # "2026-11-04"
    blind: str  # "Registered before the run; one run."
    pipeline: str  # "version 0.5.9"
    facts: list[Fact]
    chart: list[ChartRow]
    first_test: FirstTest


SYSTEMS = (
    ("Candidate", "ReadableSTEM"),
    ("Chatbot, Opus", "Chatbot on Opus"),
    ("Chatbot, Haiku", "Chatbot on Haiku"),
)
# The two measures the landing page's compact chart shows: what a listener hears, and the wrong
# equations nobody is asked about. The rest stay on the comparison guide and the offices page.
HEADLINE = (
    "spoken-equal, end to end (primary)",
    "silent errors per 100 gold (wrong under the primary, not asked)",
)
# (key in the results file, plain words, direction, the axis the bars are drawn against, format)
MEASURES = (
    (
        "spoken-equal, end to end (primary)",
        "Reads the same to a listener as the original",
        True,
        1.0,
        "{:.3f}",
    ),
    ("exact_lenient (secondary)", "Exactly right, symbol for symbol", True, 1.0, "{:.3f}"),
    (
        "silent errors per 100 gold (wrong under the primary, not asked)",
        "Wrong, and nobody is asked, per 100 equations",
        False,
        20.0,
        "{:.1f}",
    ),
    (
        "unseen loss per 100 gold (silent plus undetected)",
        "Wrong and not flagged, or not found at all, per 100 equations",
        False,
        20.0,
        "{:.1f}",
    ),
    ("cost per PDF page (USD)", "Cost per page", False, 0.08, "${:.3f}"),
    (
        "flag recall (symbols-wrong aligned equations that were a required decision)",
        "Wrong equations the tool asks you about",
        True,
        1.0,
        "{:.2f}",
    ),
)


def parse_chart(results: dict) -> list[ChartRow]:
    """The bars, from the registered run's results file (one row per measure, one line per
    system). A chatbot never asks: its flag recall is 0 and the page says so in words."""
    rows = []
    for key, words, higher, axis, fmt in MEASURES:
        lines = []
        for arm, name in SYSTEMS:
            point = float(results["arms"][arm][key]["point"])
            assert point <= axis, (key, point)
            shown = (
                "never asks" if key.startswith("flag recall") and point == 0 else fmt.format(point)
            )
            lines.append(ChartLine(name, shown, round(100 * point / axis, 1)))
        better = "Higher is better" if higher else "Lower is better"
        rows.append(ChartRow(words, better, lines, key in HEADLINE))
    return rows


def parse_first_test(proof: str) -> FirstTest:
    """Heading levels and figure-description errors of the first registered test, from the
    counts table of PROOF.md: ReadableSTEM, then the chatbot recipe on Opus."""
    levels = _found(
        proof,
        r"^\| heading level_accuracy \(among matched\) \| (\d+)/(\d+) \| (\d+)/(\d+) \|",
        re.M,
    ).groups()
    errors = _found(
        proof,
        r"^\| figure judge_factual_error_rate \(among judged\) \| (\d+)/(\d+) \| (\d+)/(\d+) \|",
        re.M,
    ).groups()
    assert "Fifteen sections of five OpenStax books (242 Letter pages)" in proof
    return FirstTest(
        set_label="15 OpenStax sections, 242 pages, October 2026, version 0.4.0",
        levels_ours=f"{levels[0]} of {levels[1]} ({100 * int(levels[0]) // int(levels[1])}%)",
        levels_chatbot=(
            f"{levels[2]} of {levels[3]} ({round(100 * int(levels[2]) / int(levels[3]))}%)"
        ),
        errors_ours=f"{errors[0]} of {errors[1]}",
        errors_chatbot=f"{errors[2]} of {errors[3]}",
    )


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


def parse(report: str, results: dict | None = None, proof: str | None = None) -> ProofFacts:
    if results is None:
        results = json.loads(RESULTS.read_text(encoding="utf-8"))
    if proof is None:
        proof = PROOF.read_text(encoding="utf-8")
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
        chart=parse_chart(results),
        first_test=parse_first_test(proof),
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
        chart=[
            ChartRow(
                r["measure"],
                r["better"],
                [ChartLine(**line) for line in r["lines"]],
                r["headline"],
            )
            for r in data["chart"]
        ],
        first_test=FirstTest(**data["first_test"]),
    )


if __name__ == "__main__":
    regenerate()
    sys.stderr.write(f"wrote {SNAPSHOT}\n")
