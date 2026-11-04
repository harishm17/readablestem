"""The proof numbers the landing page and /limits show, read from PROOF.md once.

PROOF.md is written by `make proof` from the recorded held-out run (CI replays it and fails on
any difference), so it is the one source. The image the service runs from has no PROOF.md, so
`parse()` turns it into `proof_facts.json` beside this file (`uv run python -m
readablestem.web.proof_facts`) and the pages read that. `tests/test_proof_facts.py` fails when
the JSON and PROOF.md differ, so a new proof run cannot leave the pages on old numbers.

Every fact names its set, its date, whether it is a blind result, its count, and a link to how
it was measured. Nothing here says one tool beats another: where a general chatbot scored higher
on the same sections, the fact says so.
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


@dataclass(frozen=True)
class Fact:
    value: str  # "89%"
    label: str  # what it counts
    count: str  # "2,434 of 2,738 equations"
    caveat: str  # a plain sentence; names where a general chatbot did better
    href: str  # how it was measured


@dataclass(frozen=True)
class ProofFacts:
    set_label: str  # "held-out set, 15 OpenStax sections"
    date: str  # "2026-10-14"
    blind: str  # "Registered before the run; blind."
    pipeline: str  # "version 0.4.0"
    facts: list[Fact]


def _row(proof: str, label: str, table: str = "") -> list[str]:
    """The cells of the PROOF.md table row whose first cell contains `label`, searched
    after the heading `table` when one is given."""
    start = proof.index(table) if table else 0
    match = re.search(rf"^\|[^|]*{re.escape(label)}[^|]*\|(.*)$", proof[start:], re.M)
    assert match, f"PROOF.md has no row {label!r}"
    return [cell.strip() for cell in match.group(0).strip("|").split("|")]


def _field(proof: str, name: str) -> str:
    match = re.search(rf"^\| {re.escape(name)} \| ([^|]+) \|", proof, re.M)
    assert match, name
    return match.group(1).strip()


def _percent(value: str) -> str:
    return f"{round(float(value) * 100)}%"


def parse(proof: str) -> ProofFacts:
    sections = re.search(r"^Fifteen sections", proof, re.M)
    assert sections, "PROOF.md no longer describes fifteen sections"
    recall = _row(proof, "detection_recall", "### ReadableSTEM on the whole section")
    exact = _row(
        proof,
        "end_to_end_exact_lenient (all gold equations)",
        "### ReadableSTEM on the whole section",
    )
    headings = _row(proof, "heading F1", "### Headings")
    judge = _row(proof, "judge_pass_rate", "### Figure descriptions")
    chatbot = _row(
        proof,
        "end_to_end_exact_lenient (all gold equations)",
        "## Comparison with the chatbot recipe",
    )
    chatbot_exact = re.match(r"([0-9.]+)", chatbot[2])
    assert chatbot_exact
    chatbot_recall = re.match(
        r"([0-9.]+)", _row(proof, "detection_recall", "## Comparison with the chatbot recipe")[2]
    )
    assert chatbot_recall
    heading_row = re.search(r"^\| heading F1 \| chatbot-opus-purdue .*$", proof, re.M)
    assert heading_row and "no difference this set can detect" in heading_row.group(0)
    heading_total = sum(
        int(m.group(1))
        for m in re.finditer(
            r"^\| [a-z-]+/[^|]*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\| ([0-9]+) \(",
            proof,
            re.M,
        )
    )
    assert heading_total > 0
    base = f"{SOURCE}#"
    return ProofFacts(
        set_label="held-out set, 15 OpenStax sections",
        date=_field(proof, "Run recorded on"),
        blind="Registered before the run; blind.",
        pipeline=f"version {_field(proof, 'Pipeline version')}",
        facts=[
            Fact(
                _percent(recall[1]),
                "of equations found",
                f"{recall[2].replace('/', ' of ')} equations",
                f"A general chatbot recipe found {_percent(chatbot_recall.group(1))}.",
                base + "readablestem-on-the-whole-section-document-task",
            ),
            Fact(
                _percent(exact[1]),
                "of equations exactly right, end to end",
                f"{exact[2].replace('/', ' of ')} equations",
                "A general chatbot recipe scored "
                f"{_percent(chatbot_exact.group(1))} on the same sections.",
                base + "comparison-with-the-chatbot-recipe",
            ),
            Fact(
                f"{float(headings[1]):.2f}",
                "heading F1",
                f"{heading_total} headings",
                "Not different from a general chatbot's, as far as this set can tell.",
                base + "headings-headings-task",
            ),
            Fact(
                _percent(judge[1]),
                "of figure descriptions passed a model judge",
                f"{judge[2].replace('/', ' of ')} figures",
                "This is agreement with a model judge, not a person's check.",
                base + "figure-descriptions-figures-task",
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
