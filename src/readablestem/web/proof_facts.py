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
it was measured. Nothing here says one tool beats another.

Two rules for what a public page may show (SW26):

- The stamp. A result file may carry `profile_sha256`, the hash of the production profile the run
  used (`ops/profile.py`). A file whose hash is not the repository's profile is refused (a page
  cannot show a number the live service's settings did not earn). The fresh5 file predates the
  stamp: it is kept, labeled with its date and version, and `settings_line` says that its settings
  were not recorded. The words "the settings the live service uses" appear only for a file whose
  stamp equals the profile.
- The retired scoring. The silent-error rate, the flag recall and the unseen loss count wrong
  equations by a scorer that counted notation differences as errors (a hand check of 86 errors
  found 55 harmless, AQ1). They are not in `facts`, in the chart or on any page until a stamped
  report under the corrected scorer exists; `RETIRED` names them and `tests/test_proof_facts.py`
  fails if a page shows one.
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
    settings_line: str  # what the run's settings were, in a sentence
    numbers: dict[str, str]  # figures the pages' prose states, read from the run's files


SYSTEMS = (
    ("Candidate", "ReadableSTEM"),
    ("Chatbot, Opus", "Chatbot on Opus"),
    ("Chatbot, Haiku", "Chatbot on Haiku"),
)
# The two measures the landing page's compact chart shows. The rest stay on the comparison guide
# and the offices page.
HEADLINE = (
    "spoken-equal, end to end (primary)",
    "exact_lenient (secondary)",
)
# Counted by the scorer AQ1 found wrong (see the module docstring). Not drawn, not stated.
RETIRED_MEASURES = (
    "silent errors per 100 gold (wrong under the primary, not asked)",
    "unseen loss per 100 gold (silent plus undetected)",
    "flag recall (symbols-wrong aligned equations that were a required decision)",
)
# Figures of the retired measures, as the report and the earlier pages wrote them. A public page
# that shows one fails `tests/test_proof_facts.py`.
RETIRED = ("5.0 per 100", "5.02", "9.56", "9.6 for", "46%", "0.461", "0.46 ", "11.02")
PROFILE_KEY = "profile_sha256"
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
    ("cost per PDF page (USD)", "Cost per page", False, 0.08, "${:.3f}"),
)


def parse_chart(results: dict) -> list[ChartRow]:
    """The bars, from the registered run's results file (one row per measure, one line per
    system). A chatbot never asks: its flag recall is 0 and the page says so in words."""
    rows = []
    for key, words, higher, axis, fmt in MEASURES:
        assert key not in RETIRED_MEASURES
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


def settings_line(
    results: dict, pipeline: str, date: str, profile_sha256: str | None = None
) -> str:
    """What the run's settings were. Only a result file stamped with the production profile's
    hash may say the run used the live service's settings; a stamp that differs is refused."""
    from readablestem.ops import profile

    stamp = results.get(PROFILE_KEY)
    if stamp is None:
        return (
            f"It ran on {date} with version {pipeline}. The file did not record its settings, "
            "and some of the live service's settings have changed since, so these numbers "
            "describe that run, not today's service."
        )
    if stamp != (profile_sha256 or profile.load().sha256):
        raise ValueError(
            "the result file was run under settings that are not the production profile's: "
            "it cannot go on a public page"
        )
    return f"It ran on {date} with version {pipeline} and the settings the live service uses."


def parse_numbers(report: str, results: dict) -> dict[str, str]:
    """The figures the pages' prose states about the newest run, each read from the report or
    its results file."""
    arms = results["arms"]
    ours, opus = arms["Candidate"], arms["Chatbot, Opus"]
    primary = "spoken-equal, end to end (primary)"
    paired = next(
        p
        for p in results["paired"]
        if p["other"] == "Chatbot, Opus" and p["metric"] == primary and p["units"] == 26
    )
    strata = _found(
        report,
        r"strata A \((\d+) arXiv papers, (\d+) pages\), B \((\d+) OpenStax sections, (\d+) "
        r"pages\), C \((\d+) course LaTeX documents",
    ).groups()
    chem = results["stratum_d"]["Candidate"]
    chem_goal = _found(report, r"D1 \(at least ([0-9.]+)\)").group(1)
    size = _found(report, r"(\d+) units, (\d+) pages, ([\d,]+) gold equations")
    margin = _found(report, r"(\d+) points; every primary difference").group(1)

    def signed(value: float) -> str:
        return f"{value:+.3f}".replace("-", "\u2212")

    return {
        "units": size.group(1),
        "pages": size.group(2),
        "gold": size.group(3),
        "papers": strata[0],
        "sections": strata[2],
        "course_docs": strata[4],
        "difference": signed(paired["estimate"]),
        "difference_low": signed(paired["low_975"]).removeprefix("+"),
        "difference_high": signed(paired["high_975"]),
        "margin": margin,
        "decisions_per_page": f"{ours['required decisions per PDF page']['point']:.1f}",
        "cost_ours": f"{results['cost_per_page_all_units']['Candidate']:.2f}",
        "cost_opus": f"{results['cost_per_page_all_units']['Chatbot, Opus']:.2f}",
        "kind_papers": f"{ours['A'][primary]['point']:.3f}",
        "kind_sections": f"{ours['B'][primary]['point']:.3f}",
        "kind_course": f"{ours['C'][primary]['point']:.3f}",
        "chem_sections": str(results["stratum_d"]["Candidate"]["units"]),
        "chem_gold": str(chem["gold"]),
        "chem_right": f"{chem['chem_right_excl_times']['rate']:.2f}",
        "chem_goal": chem_goal,
        "ours_exact": f"{ours['exact_lenient (secondary)']['point']:.3f}",
        "opus_exact": f"{opus['exact_lenient (secondary)']['point']:.3f}",
    }


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
    primary = _row(section, "spoken-equal, end to end (primary)")
    exact = _row(section, "exact_lenient (secondary)")
    decisions = _row(section, "required decisions per page")
    count = f"{gold} gold equations in {units} documents"
    numbers = parse_numbers(report, results)
    chart = parse_chart(results)
    first_test = parse_first_test(proof)
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
                f"{float(_number(decisions[1])):.1f}",
                "decisions it asks the instructor for, per page",
                count,
                "A chatbot asks nothing.",
                HREF,
            ),
        ],
        chart=chart,
        first_test=first_test,
        settings_line=settings_line(results, pipeline, date),
        numbers=numbers,
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
        settings_line=data["settings_line"],
        numbers=data["numbers"],
    )


if __name__ == "__main__":
    regenerate()
    sys.stderr.write(f"wrote {SNAPSHOT}\n")
