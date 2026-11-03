"""Lay the page witness against layout's formulas, then settle each one (`FEATURE_PAGE_WITNESS`).

Two steps, one before the equation step and one inside it.

`reconcile` (run once per document, after layout, before the steps) gives every equation
element its witness: the verified formula of `page_witness` whose extent lies on it. The
extent of a witness formula is the box of the text-layer characters its anchors quote, so
it does not depend on the crop or on the span the model of either reading chose. An element
gets `witness_latex` and a `witness_status`:

- `match`: all four edges of the two extents are within one glyph (the witness's median
  character width and height);
- `extent`: they overlap but reach different distances.

A formula no element took (the witness read it and layout did not) is a candidate for a new
element, `added`, when it has glyph evidence (at least `MATH_SHARE` of its characters are
maths glyphs: maths fonts, maths italic letters, Greek letters, operators) and lies clear of
every other element. A formula with no maths glyphs is left out (a page number, a year, a
bracketed reference, plain words), and one inside a paragraph, a table, a caption or a figure
is left to that element: splicing it into the text is not this module's job. A new element is
always a required decision (`equation_step`).

`agrees` and `adjudicate` are what the equation step asks once the crop is read:

- agreement: the crop's MathML and the witness's LaTeX are equal after `math_canonical`'s
  notation folding, and the extents are a `match`. It is a passed note and nothing more: it
  does not lift the confidence, lower a review level or skip the main model's second reading.
  The two readers' errors are correlated (D2 oracle, 7,390 gold equations: P(correct | agree)
  was 94.8%, 92.9 to 96.5, under the 97% an acceptance would need);
- disagreement: whichever reading's symbols are closer to the glyphs of the extent (the
  Dice similarity of the symbol bags of the text layer and the reading) is kept, the other is
  recorded as `EquationFix.other_latex`, and the formula is a required decision either way. A
  difference of less than `TIE_BELOW` is a tie and the crop's reading stays. That bag holds
  letters, digits and Greek letters only, and two readings of one formula nearly always have
  the same ones (E1 dev: 23 of 24 disagreements were exact ties on it, so the crop always
  stayed), so a tie on the letters is broken by the marks the text layer shows and the bag
  cannot (`MARKS`: a slash in the glyphs against a drawn fraction), with the same `TIE_BELOW`.

Nothing here changes what text the document says; the witness is a second reading only.
"""

from __future__ import annotations

import logging
import statistics
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import defusedxml.ElementTree as DET

from readablestem.core.types import BBox, Element, Kind
from readablestem.pipeline import layout, math_canonical
from readablestem.pipeline.math_canonical import Kind as Sameness
from readablestem.pipeline.page_math import PageRow
from readablestem.pipeline.page_witness import Formula, WitnessPlan

log = logging.getLogger(__name__)

WITNESS_ADDED_REASON = "witness_added"
# A witness-only formula needs at least this share of maths glyphs among its characters.
MATH_SHARE = 0.4
# Two extents overlap when the shared area is at least this share of the smaller one.
OVERLAP_SHARE = 0.5
# The least a glyph is allowed to be when the tolerance is measured (points).
MIN_GLYPH = 1.0
# Two readings whose similarity to the glyphs differs by less than this are tied.
TIE_BELOW = 0.05
# The one mark the letter-and-digit similarity cannot see and the text layer does show: a
# slash. A written "a/b" has one, a drawn fraction bar is a line and never in the text, so
# `-2/3` against `-\frac{2}{3}` differs here and nowhere else. It decides only when the
# letters tie (`adjudicate`). Brackets are left out: the text of an element often holds the
# bracket of its neighbour, which the crop's reading drops on purpose.
MARKS = frozenset("/")
# The sameness agreement needs (`math_canonical`): equal normalised trees, or equal once
# notation that says the same is written alike. Looser kinds (the same words when spoken, the
# same symbol bag) are not agreement.
AGREE_AT = Sameness.NOTATION

Status = Literal["match", "extent", "added"]
Winner = Literal["crop", "witness", "tie"]


# ---------------------------------------------------------------------- extents


@dataclass(frozen=True)
class Extent:
    """Where a witness formula sits: the box of its quoted characters, the size of one glyph
    there (median width and height of its characters), how many characters it has and how
    many of them are maths glyphs, and their text."""

    bbox: BBox
    glyph_w: float
    glyph_h: float
    chars: int
    math_chars: int
    text: str
    regular: str | None

    @property
    def math_share(self) -> float:
        return self.math_chars / self.chars if self.chars else 0.0


def extent_of(
    formula: Formula, rows: Mapping[str, PageRow], objects: Mapping[str, Any]
) -> Extent | None:
    """The extent of `formula` from layout's characters; None when a row is not known."""
    chars: list[Any] = []
    parts: list[str] = []
    for seg in formula.span.segments:
        row, obj = rows.get(seg.row), objects.get(seg.row)
        if row is None or obj is None:
            return None
        picked = [
            obj.chars[row.chars[i]]
            for i in range(seg.start, seg.end)
            if row.chars[i] < len(obj.chars)
        ]
        chars.extend(picked)
        parts.append("".join(c.styled for c in picked).strip())
    inked = [c for c in chars if not c.blank]
    if not inked:
        return None
    box = (
        min(c.x0 for c in inked),
        min(c.y0 for c in inked),
        max(c.x1 for c in inked),
        max(c.y1 for c in inked),
    )
    return Extent(
        bbox=box,
        glyph_w=max(MIN_GLYPH, statistics.median(c.x1 - c.x0 for c in inked)),
        glyph_h=max(MIN_GLYPH, statistics.median(c.y1 - c.y0 for c in inked)),
        chars=len(inked),
        math_chars=sum(1 for c in inked if c.math),
        text=" ".join(p for p in parts if p),
        regular=layout._regular_letters(inked),
    )


def _area(box: BBox) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(a: BBox, b: BBox) -> float:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def _iou(a: BBox, b: BBox) -> float:
    inter = _intersection(a, b)
    union = _area(a) + _area(b) - inter
    return inter / union if union > 0 else 0.0


def _overlaps(a: BBox, b: BBox) -> bool:
    smaller = min(_area(a), _area(b))
    return smaller > 0 and _intersection(a, b) / smaller >= OVERLAP_SHARE


def within_one_glyph(extent: Extent, box: BBox) -> bool:
    """Do the four edges of `box` lie within one glyph of the extent's?"""
    x0, y0, x1, y1 = extent.bbox
    tol_x, tol_y = extent.glyph_w, extent.glyph_h
    return (
        abs(box[0] - x0) <= tol_x
        and abs(box[2] - x1) <= tol_x
        and abs(box[1] - y0) <= tol_y
        and abs(box[3] - y1) <= tol_y
    )


# -------------------------------------------------------------------- reconcile


@dataclass
class ReconcileStats:
    matched: int = 0
    extent_differs: int = 0
    added: int = 0
    no_glyph_evidence: int = 0
    inside_text: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass
class Reconciled:
    elements: list[Element]
    stats: ReconcileStats = field(default_factory=ReconcileStats)


def reconcile(
    elements: Sequence[Element],
    plan: WitnessPlan,
    rows: Mapping[int, Sequence[PageRow]],
    objects: Mapping[int, Mapping[str, Any]],
) -> Reconciled:
    """`elements` with the witness's formulas laid against them (module docstring).
    `rows` and `objects` are the first layout run's, by 0-based page
    (`PageMathHook.rows`, `.objects`). Elements of a page the witness did not read, or read
    with no answer, are returned as they were. Never raises for one formula it cannot place."""
    result = Reconciled(list(elements))
    for page in sorted(plan.pages):
        witness = plan.pages[page]
        if witness.failed is not None and not witness.formulas:
            continue
        by_id = {r.id: r for r in rows.get(page, ())}
        extents: list[tuple[Formula, Extent]] = []
        for formula in witness.formulas:
            try:
                found = extent_of(formula, by_id, objects.get(page, {}))
            except Exception as exc:  # one formula never fails the page
                log.warning("page witness: an extent was unreadable: %s", type(exc).__name__)
                found = None
            if found is not None:
                extents.append((formula, found))
        _place_page(result, page, extents)
    return result


def _place_page(result: Reconciled, page: int, extents: list[tuple[Formula, Extent]]) -> None:
    elements = result.elements
    stats = result.stats
    equations = [i for i, e in enumerate(elements) if e.page == page and e.kind is Kind.EQUATION]
    # One-to-one by overlap, the best pair first.
    pairs = sorted(
        (
            (_iou(elements[i].bbox, ext.bbox), i, j)
            for i in equations
            for j, (_, ext) in enumerate(extents)
            if _overlaps(elements[i].bbox, ext.bbox)
        ),
        key=lambda p: (-p[0], p[1], p[2]),
    )
    taken_elements: set[int] = set()
    taken_formulas: set[int] = set()
    for _, i, j in pairs:
        if i in taken_elements or j in taken_formulas:
            continue
        taken_elements.add(i)
        taken_formulas.add(j)
        formula, ext = extents[j]
        status: Status = "match" if within_one_glyph(ext, elements[i].bbox) else "extent"
        stats.matched += status == "match"
        stats.extent_differs += status == "extent"
        elements[i] = elements[i].model_copy(
            update={"witness_latex": formula.latex, "witness_status": status}
        )
    others = [e for e in elements if e.page == page]
    added: list[Element] = []
    for j, (formula, ext) in enumerate(extents):
        if j in taken_formulas:
            continue
        if ext.math_chars < 1 or ext.math_share < MATH_SHARE:
            stats.no_glyph_evidence += 1
            continue
        if any(_overlaps(o.bbox, ext.bbox) for o in [*others, *added]):
            stats.inside_text += 1
            continue
        added.append(_new_element(page, formula, ext, [*others, *added]))
        stats.added += 1
    for element in added:
        _insert(elements, element)
    if added:
        result.elements = [e.model_copy(update={"order": n}) for n, e in enumerate(elements)]


def _new_element(page: int, formula: Formula, ext: Extent, taken: Sequence[Element]) -> Element:
    prefix = f"p{page + 1}-e"
    used = [
        int(e.id.removeprefix(prefix))
        for e in taken
        if e.id.startswith(prefix) and e.id.removeprefix(prefix).isdecimal()
    ]
    x0, y0, x1, y1 = ext.bbox
    return Element(
        id=f"{prefix}{max(used) + 1 if used else 0}",
        kind=Kind.EQUATION,
        page=page,
        bbox=(round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)),
        order=0,  # set once it is placed
        text=ext.text,
        font_size=round(ext.glyph_h, 2),
        display=formula.display,
        reasons=[WITNESS_ADDED_REASON],
        regular_letters=ext.regular,
        witness_latex=formula.latex,
        witness_status="added",
    )


def _insert(elements: list[Element], new: Element) -> None:
    """Put `new` in reading order: after the last element of its page that sits above it in
    the same column, else before the first one below it, else at the end of its page."""
    on_page = [i for i, e in enumerate(elements) if e.page == new.page]
    width = max(1e-6, new.bbox[2] - new.bbox[0])

    def same_column(e: Element) -> bool:
        shared = min(e.bbox[2], new.bbox[2]) - max(e.bbox[0], new.bbox[0])
        return shared / width >= 0.5

    above = [i for i in on_page if same_column(elements[i]) and elements[i].bbox[1] <= new.bbox[1]]
    if above:
        elements.insert(above[-1] + 1, new)
        return
    below = [i for i in on_page if same_column(elements[i]) and elements[i].bbox[1] > new.bbox[1]]
    if below:
        elements.insert(below[0], new)
        return
    after = [i for i, e in enumerate(elements) if e.page <= new.page]
    elements.insert((after[-1] + 1) if after else 0, new)


# -------------------------------------------------------- what the equation step asks


def reading(element: Element) -> str | None:
    """The witness's LaTeX for `element` when its extent matches the element's (within one
    glyph), else None: only such a reading can agree."""
    if element.witness_status in ("match", "added") and element.witness_latex:
        return element.witness_latex
    return None


def agrees(witness_latex: str, crop_mathml: str, *, display: str = "block") -> bool:
    """Is the crop's MathML the witness's formula after `math_canonical`'s folding?"""
    try:
        return math_canonical.equal(
            witness_latex, crop_mathml, at=AGREE_AT, display=display == "block"
        )
    except Exception:  # whatever the converters raise: they do not agree
        return False


def adjudicate(text_layer: str | None, crop_mathml: str, witness_mathml: str) -> Winner:
    """Which reading's symbols are closer to the glyphs of the extent (`text_layer`: the text
    of the characters there): the crop's, the witness's, or neither by `TIE_BELOW`. Without a
    text layer, or when either reading is not MathML, nobody wins."""
    from readablestem.pipeline.equation_step import symbol_dice  # equation_step imports this

    if not text_layer or not text_layer.strip():
        return "tie"
    try:
        crop = symbol_dice(text_layer, crop_mathml)
        witness = symbol_dice(text_layer, witness_mathml)
    except Exception:
        return "tie"
    if abs(crop - witness) < TIE_BELOW:
        try:
            crop, witness = (
                marks_dice(text_layer, crop_mathml),
                marks_dice(text_layer, witness_mathml),
            )
        except Exception:
            return "tie"
        if abs(crop - witness) < TIE_BELOW:
            return "tie"
    return "crop" if crop > witness else "witness"


def _marks(text: str) -> Counter[str]:
    return Counter(ch for ch in unicodedata.normalize("NFKC", text) if ch in MARKS)


def marks_dice(text: str, mathml: str) -> float:
    """Dice similarity of the `MARKS` of the text layer and of a MathML (1.0 when neither
    has one)."""
    expected = _marks(text)
    got = _marks(" ".join(DET.fromstring(mathml, forbid_dtd=True).itertext()))
    total = sum(expected.values()) + sum(got.values())
    return 2 * sum((expected & got).values()) / total if total else 1.0
