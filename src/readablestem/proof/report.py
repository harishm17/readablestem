"""PROOF.md: tables, number formats and the wording guard.

The report is assembled from `Section`s (headings, paragraphs, tables) that the stages
return, and rendered to Markdown with fixed number formats, so the same inputs always
give the same bytes. Nothing in here reads the clock, the host or the network.

Wording guard: a11y-stem assists a person who makes course material accessible. The
report never says that a document is "compliant", that anything is "verified", or that
accessibility is "guaranteed", and never claims conformance with WCAG or ADA.
`wording_problems` finds those words; text between `<!-- quote -->` and
`<!-- /quote -->` is exempt, because it quotes somebody else's words (the prompt the
chatbot recipe uses) and says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

NA = "n/a"
STATUS_WORDS = {
    "ok": "done",
    "not_wired": "not computed",
    "skipped": "skipped",
    "failed": "failed",
}
_FORBIDDEN = re.compile(
    r"\b(compliant|compliance|complies|verified|verifies|guarantee[sd]?|certified|"
    r"(?:wcag|ada)[ -](?:conformant|conformance|compliant))\b",
    re.IGNORECASE,
)
_QUOTE = re.compile(r"<!-- quote -->.*?<!-- /quote -->", re.DOTALL)


def wording_problems(text: str) -> list[str]:
    """Forbidden words outside quoted blocks, each with the line it is on."""
    visible = _QUOTE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    out = []
    for number, line in enumerate(visible.splitlines(), 1):
        for match in _FORBIDDEN.finditer(line):
            out.append(f"line {number}: {match.group(0)!r}")
    return out


# ------------------------------------------------------------------------ numbers


def num(value: float | int | None, digits: int = 4) -> str:
    if value is None:
        return NA
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:.{digits}f}"


def usd(value: float | None, digits: int = 4) -> str:
    return NA if value is None else f"${value:.{digits}f}"


def interval(low: float | None, high: float | None, digits: int = 4) -> str:
    if low is None or high is None:
        return NA
    return f"[{low:.{digits}f}, {high:.{digits}f}]"


def fraction(top: float, bottom: float) -> str:
    return f"{_whole(top)}/{_whole(bottom)}"


def _whole(value: float) -> str:
    return f"{value:,.0f}" if abs(value - round(value)) < 1e-9 else f"{value:,.2f}"


# ------------------------------------------------------------------------- tables


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


@dataclass
class Table:
    title: str
    headers: list[str]
    rows: list[list[str]]
    note: str = ""

    def markdown(self) -> str:
        lines = [f"**{self.title}**", ""] if self.title else []
        lines.append("| " + " | ".join(_cell(h) for h in self.headers) + " |")
        lines.append("|" + "|".join("---" for _ in self.headers) + "|")
        for row in self.rows:
            padded = [*row, *[""] * (len(self.headers) - len(row))]
            lines.append("| " + " | ".join(_cell(c) for c in padded) + " |")
        if self.note:
            lines += ["", self.note]
        return "\n".join(lines)


@dataclass
class Section:
    heading: str
    blocks: list[str | Table] = field(default_factory=list)
    level: int = 2

    def markdown(self) -> str:
        parts = [f"{'#' * self.level} {self.heading}"]
        for block in self.blocks:
            parts.append(block.markdown() if isinstance(block, Table) else block)
        return "\n\n".join(parts)


@dataclass
class StageResult:
    """What a stage returns: sections for PROOF.md, and whether it did its job."""

    status: Literal["ok", "not_wired", "skipped", "failed"]
    sections: list[Section] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # "not done" lines for the status block


def bullets(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)
