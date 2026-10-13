"""Facts about the gold set the proof tables need: counts per section, and rule G1.

G1 is the first scorer gap listed in the preregistration (section 7.1): OpenStax sometimes
writes operators inside text (`<mtext>time =</mtext>`), which the LaTeX-to-MathML
converter writes as text plus `<mo>`. A correct answer then fails exact match. The rule
is read from the gold alone, before any output exists: a gold `<mtext>` of more than one
character that holds one of the operator characters below. `end_to_end_exact_lenient` is
reported over all gold equations (the headline) and over gold equations without G1.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import defusedxml.ElementTree as DET
from defusedxml import DefusedXmlException

from a11y_stem.eval.pipeline_eval import load_gold

G1_OPERATORS = frozenset(
    "=+-/()[]<>" + "".join(chr(c) for c in (0x2212, 0xD7, 0xB7, 0xF7, 0x2264, 0x2265, 0x2248, 0xB1))
)


def is_g1(mathml: str) -> bool:
    try:
        root = DET.fromstring(mathml)
    except (DET.ParseError, DefusedXmlException):
        return False
    for element in root.iter():
        if str(element.tag).rsplit("}", 1)[-1] == "mtext":
            text = "".join(element.itertext()).strip()
            if len(text) > 1 and any(c in G1_OPERATORS for c in text):
                return True
    return False


def g1_ids(records: list[dict]) -> frozenset[str]:
    return frozenset(r["id"] for r in records if r.get("kind") == "equation" and is_g1(r["mathml"]))


def section_of_record(record: dict) -> str:
    return f"{record['book_slug']}/{record['page_slug']}"


def section_of_item_id(item_id: str) -> str:
    """`openstax/physics/2-1-foo/eq-003` -> `physics/2-1-foo`; a page id stays as it is."""
    parts = item_id.split("/")
    if parts[0] == "openstax" and len(parts) >= 3:
        return f"{parts[1]}/{parts[2]}"
    return item_id


@dataclass(frozen=True)
class SectionFacts:
    id: str
    book_title: str
    page_title: str
    equations: int
    block: int
    inline: int
    g1: int
    figures: int
    figures_with_alt: int
    tables: int
    headings: int
    headings_in_boxes: int


def section_facts(records: list[dict], order: list[str]) -> list[SectionFacts]:
    """One row per section, in `order`."""
    by_section: dict[str, list[dict]] = {}
    for record in records:
        by_section.setdefault(section_of_record(record), []).append(record)
    rows = []
    for section_id in order:
        rs = by_section.get(section_id, [])
        kinds = Counter(r["kind"] for r in rs)
        equations = [r for r in rs if r["kind"] == "equation"]
        figures = [r for r in rs if r["kind"] == "figure"]
        headings = [r for r in rs if r["kind"] == "heading"]
        rows.append(
            SectionFacts(
                id=section_id,
                book_title=str(rs[0].get("book_title", "")) if rs else "",
                page_title=str(rs[0].get("page_title", "")) if rs else "",
                equations=kinds["equation"],
                block=sum(r.get("display") == "block" for r in equations),
                inline=sum(r.get("display") != "block" for r in equations),
                g1=sum(is_g1(r["mathml"]) for r in equations),
                figures=kinds["figure"],
                figures_with_alt=sum(
                    any((img.get("alt") or "").strip() for img in r.get("images", []))
                    for r in figures
                ),
                tables=kinds["table"],
                headings=kinds["heading"],
                headings_in_boxes=sum(bool(r.get("context")) for r in headings),
            )
        )
    return rows


def load_records(gold_dir: Path) -> list[dict]:
    return load_gold(gold_dir)
