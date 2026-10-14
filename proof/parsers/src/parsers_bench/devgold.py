"""Gold for a few pages of a development section.

The gold set (`data/gold`) is per OpenStax section, with no page numbers. To check the
plumbing on two pages of a section's PDF, the gold is cut to what is on those pages: each gold
equation and heading is aligned, in reading order, to the elements `layout.analyze` finds in
the whole PDF (the same weighted alignment the document task uses), and kept when its element
sits on one of the chosen pages. Gold items nothing aligned to are `unplaced` and left out,
and counted, so that a cut is never presented as complete.

Plumbing only. The held-out run scores whole sections against whole-section gold.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from readablestem.core.types import Kind
from readablestem.eval import pipeline_eval as pe
from readablestem.eval.gold_scoring import iter_gold_records
from readablestem.pipeline import layout


@dataclass
class GoldSubset:
    section: str
    pages: list[int]  # 0-based pages of the full PDF
    equations: list[dict] = field(default_factory=list)
    headings: list[dict] = field(default_factory=list)
    figures_on_pages: int = 0
    gold_equations_in_section: int = 0
    gold_headings_in_section: int = 0
    unplaced_equations: int = 0
    unplaced_headings: int = 0

    def counts(self) -> dict[str, int]:
        return {
            "pages": len(self.pages),
            "equations": len(self.equations),
            "headings": len(self.headings),
            "figures_on_pages": self.figures_on_pages,
            "unplaced_equations": self.unplaced_equations,
            "unplaced_headings": self.unplaced_headings,
        }


@dataclass
class Placement:
    """The page (0-based) each gold item sits on, or None when nothing aligned to it."""

    section: str
    equations: list[tuple[dict, int | None]]
    headings: list[tuple[dict, int | None]]
    figure_pages: list[int]


def section_records(gold_dir: Path, book: str, page_slug: str) -> list[dict]:
    return [
        r
        for r in iter_gold_records(gold_dir)
        if r.get("book_slug") == book and r.get("page_slug") == page_slug
    ]


def place(records: Sequence[dict], full_pdf: bytes, *, section: str) -> Placement:
    lay = layout.analyze(full_pdf)
    gold_eq = pe._of_kind(records, "equation")
    gold_hd = pe._of_kind(records, "heading")
    layout_eq = sorted((e for e in lay.elements if e.kind == Kind.EQUATION), key=lambda e: e.order)
    layout_hd = sorted((e for e in lay.elements if e.kind == Kind.HEADING), key=lambda e: e.order)

    eq_pairs = pe.align(
        [pe.math_signature(g["mathml"]) for g in gold_eq],
        [pe.text_signature(e.text) for e in layout_eq],
        pe.similarity,
        min_similarity=pe.EQUATION_ALIGN_MIN,
    )
    eq_page = {gi: layout_eq[li].page for gi, li, _ in eq_pairs}
    hd_pairs = pe.align(
        [pe.heading_signature(g["text"]) for g in gold_hd],
        [pe.heading_signature(e.text) for e in layout_hd],
        pe.similarity,
        min_similarity=pe.HEADING_MATCH_RATIO,
    )
    hd_page = {gi: layout_hd[li].page for gi, li, _ in hd_pairs}
    return Placement(
        section=section,
        equations=[(g, eq_page.get(i)) for i, g in enumerate(gold_eq)],
        headings=[(g, hd_page.get(i)) for i, g in enumerate(gold_hd)],
        figure_pages=[e.page for e in lay.elements if e.kind == Kind.FIGURE],
    )


def select(placement: Placement, pages: Sequence[int]) -> GoldSubset:
    chosen = set(pages)
    return GoldSubset(
        section=placement.section,
        pages=sorted(chosen),
        equations=[g for g, page in placement.equations if page in chosen],
        headings=[g for g, page in placement.headings if page in chosen],
        figures_on_pages=sum(page in chosen for page in placement.figure_pages),
        gold_equations_in_section=len(placement.equations),
        gold_headings_in_section=len(placement.headings),
        unplaced_equations=sum(page is None for _, page in placement.equations),
        unplaced_headings=sum(page is None for _, page in placement.headings),
    )


def restrict(
    records: Sequence[dict], full_pdf: bytes, pages: Sequence[int], *, section: str
) -> GoldSubset:
    return select(place(records, full_pdf, section=section), pages)
