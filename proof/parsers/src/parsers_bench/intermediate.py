"""The shared intermediate form: what every adapter produces and the scorer reads.

One JSON document per arm and input. It holds what a parser put in its output, mapped onto
the same few kinds of thing whatever the parser's native format: headings with levels,
text blocks in reading order, equations as LaTeX (or MathML), figures with any text
alternative, and tables as grids. Nothing in it is a score.

An adapter reads the tool's most structured output (Docling's JSON, MinerU's structured
content, Markdown for tools that only write Markdown), so that a serialiser's quirks are not
scored as the tool's. The mapping rules are in `adapters/` and in README.md.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = 1

STATUSES = ("ok", "crashed", "timeout", "empty", "truncated")
# Text blocks only. Headings, equations, figures and tables are their own lists; every item of
# every list carries one `order` from a single counter, which is the reading order.
BLOCK_KINDS = ("para", "list", "caption", "figure_text", "furniture")
EQUATION_DISPLAYS = ("block", "inline")


@dataclass
class Equation:
    """One formula the tool put in its output.

    `latex` (or `mathml`) is what the tool transcribed; both are None when it kept the formula
    only as a picture or as the PDF's own text (Docling without formula enrichment, the
    default). `raw` is whatever text the tool wrote for it anyway (for Docling, the PDF text
    layer's flattened form). `raw` feeds only the "content retained in any form" measure.
    """

    order: int
    display: Literal["block", "inline"]
    latex: str | None = None
    mathml: str | None = None
    raw: str | None = None


@dataclass
class Heading:
    order: int
    level: int
    text: str


@dataclass
class Block:
    """A run of text in reading order. Maths inside it is not in `text` (see Equation)."""

    order: int
    kind: str
    text: str


@dataclass
class Figure:
    order: int
    caption: str | None
    image_kept: bool
    text_alternative: str | None


@dataclass
class Table:
    order: int
    grid: list[list[str]]


@dataclass
class Intermediate:
    arm: str
    section: str
    status: str = "ok"
    seconds: float | None = None
    versions: dict[str, str] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    equations: list[Equation] = field(default_factory=list)
    headings: list[Heading] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    html_path: str | None = None
    markdown_path: str | None = None
    notes: list[str] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def counts(self) -> dict[str, int]:
        """Content-free summary, safe to log."""
        return {
            "equations": len(self.equations),
            "equations_transcribed": sum(
                e.latex is not None or e.mathml is not None for e in self.equations
            ),
            "headings": len(self.headings),
            "blocks": len(self.blocks),
            "figures": len(self.figures),
            "tables": len(self.tables),
        }


def validate(doc: Intermediate) -> list[str]:
    """Problems with the form itself (not with the content). Empty list = well formed."""
    problems: list[str] = []
    if doc.status not in STATUSES:
        problems.append(f"status {doc.status!r} is not one of {STATUSES}")
    for eq in doc.equations:
        if eq.display not in EQUATION_DISPLAYS:
            problems.append(f"equation {eq.order}: display {eq.display!r}")
    for block in doc.blocks:
        if block.kind not in BLOCK_KINDS:
            problems.append(f"block {block.order}: kind {block.kind!r}")
    for heading in doc.headings:
        if not 1 <= heading.level <= 6:
            problems.append(f"heading {heading.order}: level {heading.level}")
    orders = [
        *(e.order for e in doc.equations),
        *(h.order for h in doc.headings),
        *(b.order for b in doc.blocks),
        *(f.order for f in doc.figures),
        *(t.order for t in doc.tables),
    ]
    if len(orders) != len(set(orders)):
        problems.append("two items share a reading-order index")
    return problems


def write(doc: Intermediate, path: Path) -> None:
    path.write_text(json.dumps(doc.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")


def read(path: Path) -> Intermediate:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path}: schema_version {data.get('schema_version')!r}")
    return Intermediate(
        arm=data["arm"],
        section=data["section"],
        status=data["status"],
        seconds=data.get("seconds"),
        versions=data.get("versions", {}),
        settings=data.get("settings", {}),
        source=data.get("source", ""),
        equations=[Equation(**e) for e in data["equations"]],
        headings=[Heading(**h) for h in data["headings"]],
        blocks=[Block(**b) for b in data["blocks"]],
        figures=[Figure(**f) for f in data["figures"]],
        tables=[Table(**t) for t in data["tables"]],
        html_path=data.get("html_path"),
        markdown_path=data.get("markdown_path"),
        notes=data.get("notes", []),
    )
