"""Image pages: read a scanned or handwritten page from a picture of it (ADR 0007).

`page_type` calls a page scanned or handwritten when it has no text layer anyone can trust.
Until this module such a page was kept as its image, marked "Not converted". With
`FEATURE_IMAGE_PAGES` on, one vision call per page reads it into structured blocks, and
`build` turns the blocks into the same elements and fixes a typed page produces, with one
difference that decides everything else: there is no text layer, so nothing can confirm what
the model wrote, and every converted block is a review item.

What the model gives (`ImagePageOutput`): blocks in reading order, each a heading (with a
level), a paragraph or list item (a list of text runs and inline-maths runs), a display
equation (LaTeX and its printed number), a figure (a short description) or a table (a grid),
with a bounding box on a 0-1000 scale and a confidence (high, medium, low). A word it cannot
read is the literal marker `[illegible]`.

What becomes of each block (`build`; the tiers are the table in the ADR):

- A display equation is an equation element with the picture-of-an-equation reason, no text
  and the page reader's LaTeX as `Element.page_latex`. The equation step then reads the crop
  of its box like any other equation: the standing failed check `equation.no_text_layer` makes
  it a required decision, and the comparison with the page reader's reading (ADR 0006's
  `equation_step._compare_readings`) lifts the confidence on agreement and asks the main model
  once on disagreement. Without a usable box the page reader's own reading is the proposal,
  required, with no second reading.
- A table's grid goes through the table step's grid constructor and structure checks; the fix
  is always required (`table.no_text_layer`). A grid that is not a grid is kept as the
  table's image (FAILED `invalid_table`), required, as it is today.
- A heading is a heading element carrying the page reader's level as its evidence
  (`layout.REASON_IMAGE_HEADING`), so the heading step repairs the outline and groups
  repeated labels; `tier_headings` then applies the image page tier to its fix.
- A paragraph or list item is an element with a fix of kind PARAGRAPH: optional at `high`
  confidence with nothing illegible, required otherwise (`medium`, `low`, an `[illegible]`
  marker, an inline formula that did not validate). An inline formula is an inline equation
  element validated through the same LaTeX and MathML checks and accepted without a
  decision of its own: it belongs to its paragraph's item. A formula that does not validate
  stays in the text as its LaTeX in square brackets.
- A figure is a figure element with the model's description as alt text (optional note at
  `high`, required otherwise; an alt text that fails an error-level check is required).

- Ink the blocks leave out (`image_page_omission`): the page picture is compared with the
  regions the blocks' boxes cover, and a stretch of ink no block is near becomes a figure
  element (`IMAGE_PAGE_OMISSION_REASON`) with a required decision (`image_page.omitted_region`),
  because a reader that stops early says nothing about what it did not write.

The page also gets one figure element for the whole page (`ORIGINAL_REASON`): the output
writes it as the "show the original page" toggle after the page's notice.

Nothing the model says is trusted as it comes: text is scrubbed like every piece of document
text (the inline-marker brackets become look-alikes, so a scan cannot fake an equation marker),
LaTeX and MathML go through the validators, and the page image only ever reaches the model as
data (the prompt says so). Failure is fail-safe: a refused, invalid, empty or over-budget
answer leaves the page in `kept_pages` and it is kept as its image, as before.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from readablestem.config import ConfigError, Settings
from readablestem.core import triage, types
from readablestem.core.budget import BudgetExceeded
from readablestem.core.types import (
    IMAGE_PAGE_OMISSION_REASON,
    BBox,
    Check,
    Element,
    EquationFix,
    FigureFix,
    Fix,
    ImagePageRecord,
    ImagePagesSummary,
    Kind,
    ModelCall,
    Notice,
    Status,
    TableFix,
    inline_marker,
)
from readablestem.models.base import VisionClient, VisionRequest
from readablestem.models.pricing import UnknownModelError
from readablestem.models.prompts import load_prompt
from readablestem.pipeline import (
    equation_images,
    equation_step,
    layout,
    table_step,
)
from readablestem.pipeline import image_page_check as second
from readablestem.pipeline import image_page_omission as omission
from readablestem.pipeline.validate import check_alt, clean_alt, has_errors

log = logging.getLogger(__name__)

PROMPT_NAME = "image_page"
PROMPT_VERSION = load_prompt(PROMPT_NAME).version

# `Element.reasons` of everything made from an image page, of the page's own picture, and of
# an equation whose box could not be used (so it has no second reading).
IMAGE_PAGE_REASON = types.IMAGE_PAGE_REASON
ORIGINAL_REASON = types.IMAGE_PAGE_ORIGINAL_REASON
NO_BOX_REASON = types.IMAGE_PAGE_NO_BOX_REASON
NO_CROP_REASON = types.IMAGE_PAGE_NO_CROP_REASON
# Kept-page reasons (`page_type`) the pass reads. `unread_page` (a page with some typed text)
# and handwriting on a typed page are not scans or notebooks and stay as they were.
READ_REASONS = frozenset({"scanned_page", "handwritten_page"})
ILLEGIBLE = "[illegible]"

# The page picture: 200 dpi, long side at most the size at which Claude stops scaling a picture
# down (`pricing.IMAGE_MAX_EDGE_PX`), so small subscripts stay legible.
DPI = 200  # the default of `Settings.image_pages_dpi`
MAX_SIDE_PX = 2576
MAX_OUTPUT_TOKENS = 6144
# Thinking effort. Transcription is reading, not reasoning; the measurement on the
# development set (docs/eval-image-pages.md) chose this.
DEFAULT_EFFORT = "low"
DEFAULT_CONCURRENCY = 4
MAX_BLOCKS = 400
MAX_RUNS = 200
MAX_TEXT_CHARS = 20_000
# What a confidence word is worth as `Fix.confidence`.
CONFIDENCE_VALUE = {"high": 0.95, "medium": 0.7, "low": 0.4}
# The box is grown by this many thousandths of the page's width/height, then trimmed to the
# ink inside it (never beyond what was grown).
BOX_PAD = 12
INK_PAD_PX = 5
# A list marker left of a formula: a stretch of ink at most this wide, then a gap at least
# this wide (shares of the page's width).
MARKER_WIDTH = 0.06
MARKER_GAP = 0.03

Confidence = Literal["high", "medium", "low"]
BlockKind = Literal["heading", "paragraph", "list_item", "equation", "figure", "table"]


# ----------------------------------------------------------------------------- schema


class Run(BaseModel):
    """A stretch of a block's content: plain words, or a formula inside the sentence."""

    model_config = ConfigDict(strict=True, extra="forbid")

    kind: Literal["text", "math"]
    value: str = Field(description="The words as written, or the formula as LaTeX.")


class Cell(BaseModel):
    """One printed cell of a table. Unlike `table_step.CellOutput` it has no `ge` bound on
    the spans: the provider's decoder does not enforce bounds, and one cell with a span of 0
    must not cost the whole page (`_grid_rows` makes every span at least 1)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    text: str = Field(description="The cell's text exactly as printed; empty for an empty cell.")
    latex: str = Field(
        description="Math-mode LaTeX for the whole cell when it holds mathematics; else empty."
    )
    header: Literal["col", "row", "none"] = Field(
        description="col: labels the cells below it; row: labels the cells to its right."
    )
    row_span: int = Field(description="Rows the cell covers (1 for an ordinary cell).")
    col_span: int = Field(description="Columns the cell covers (1 for an ordinary cell).")


class Row(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    cells: list[Cell] = Field(description="The row's cells, left to right.")


class Block(BaseModel):
    """One piece of the page. Fields that do not apply to its kind are empty."""

    model_config = ConfigDict(strict=True, extra="forbid")

    kind: BlockKind
    x0: int = Field(description="Left edge, thousandths of the page width from the left.")
    y0: int = Field(description="Top edge, thousandths of the page height from the top.")
    x1: int = Field(description="Right edge, thousandths of the page width from the left.")
    y1: int = Field(description="Bottom edge, thousandths of the page height from the top.")
    confidence: Confidence
    level: int = Field(description="Heading level 1-6, list nesting level 1-3, else 0.")
    marker: str = Field(description="A list item's marker as written; else empty.")
    runs: list[Run] = Field(description="Heading, paragraph and list item content in order.")
    latex: str = Field(description="An equation's LaTeX; else empty.")
    label: str = Field(description="An equation's printed number, such as (2.4); else empty.")
    description: str = Field(description="A figure's short description; else empty.")
    caption: str = Field(description="A figure's or table's printed caption; else empty.")
    rows: list[Row] = Field(description="A table's rows; else empty.")


class ImagePageOutput(BaseModel):
    """Structured output of the image page pass (strict: no coercion, no extra keys)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    blocks: list[Block]


# ------------------------------------------------------------------------- the call


@dataclass
class PageRead:
    """What asking the model about one page gave: its parsed answer (None when none),
    every call made for it, and the outcome code (`ModelCall.outcome`, or "no_image")."""

    page: int
    output: ImagePageOutput | None
    calls: list[ModelCall] = field(default_factory=list)
    outcome: str = "ok"
    png: bytes | None = None
    # What the second reading of each block that holds maths said (`image_page_check`), by the
    # block's index in `output.blocks`.
    verdicts: dict[int, second.Verdict] = field(default_factory=dict)
    # Blocks added by reading the stretches the first reading left out (`recover_omissions`).
    recovered: int = 0


@dataclass
class PassResult:
    reads: dict[int, PageRead] = field(default_factory=dict)
    stopped: BudgetExceeded | None = None


def build_request(settings: Settings, png: bytes) -> VisionRequest:
    """The request for one page: the page picture only (there is no text to give)."""
    prompt = load_prompt(PROMPT_NAME)
    return VisionRequest(
        step="image_page",
        model=settings.image_pages_model or settings.main_model,
        prompt_version=prompt.version,
        instructions=prompt.text,
        images=[png],
        max_output_tokens=MAX_OUTPUT_TOKENS,
        image_detail="high",
        effort=settings.anthropic_effort_image_pages or DEFAULT_EFFORT,
        timeout_s=max(settings.model_timeout_s, settings.image_pages_timeout_s),
    )


def read_page(client: VisionClient, settings: Settings, page: int, png: bytes) -> PageRead:
    """Ask for one page's blocks. Never raises for a bad answer (a refused, cut-off or
    invalid one is a read with no output); `BudgetExceeded`, `ConfigError` and
    `UnknownModelError` propagate."""
    parsed, call = client.run(build_request(settings, png), ImagePageOutput)
    return PageRead(page, parsed, [call], call.outcome, png)


def run(
    client: VisionClient,
    settings: Settings,
    pages: Mapping[int, Callable[[], bytes | None]],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> PassResult:
    """Read the pages given (`{page: render}`, `render()` makes the page's PNG; called on this
    thread, since PyMuPDF is not thread-safe). A page whose picture cannot be made is a read
    with outcome "no_image". A budget refusal stops the pass: the pages answered before it
    stay, the others are not read."""
    result = PassResult()
    work: dict[int, bytes] = {}
    for number, render in sorted(pages.items()):
        try:
            png = render()
        except Exception as exc:  # a page picture is never worth failing the conversion for
            log.warning("image page picture failed: %s", type(exc).__name__)
            png = None
        if png:
            work[number] = png
        else:
            result.reads[number] = PageRead(number, None, [], "no_image")

    def one(number: int) -> tuple[int, PageRead | None, Exception | None]:
        try:
            return number, read_page(client, settings, number, work[number]), None
        except (BudgetExceeded, ConfigError, UnknownModelError) as exc:
            return number, None, exc
        except Exception as exc:
            log.warning("image page failed: %s", type(exc).__name__)
            return number, PageRead(number, None, [], "error", work[number]), None

    with ThreadPoolExecutor(
        max_workers=max(1, concurrency), thread_name_prefix="imagepage"
    ) as pool:
        outcomes = list(pool.map(one, sorted(work)))
    for number, read, error in outcomes:
        if isinstance(error, (ConfigError, UnknownModelError)):
            raise error
        if isinstance(error, BudgetExceeded):
            result.stopped = error
            result.reads[number] = PageRead(number, None, [], "budget_exceeded", work[number])
            continue
        if read is not None:
            result.reads[number] = read
    return result


def second_reading_crops(output: ImagePageOutput, png: bytes) -> list[second.Crop]:
    """The lines to read a second time: each `medium` paragraph or list item that holds a
    formula and has a usable box, cut out of the page picture. A block of one formula only is a
    display equation and the equation step reads its crop; `high` and `low` blocks are not
    asked about again (the first is an optional note already, the second always required)."""
    try:
        ink = Ink(png)
    except Exception as exc:  # an unreadable picture only costs the second reading
        log.warning("image page ink failed: %s", type(exc).__name__)
        return []
    crops: list[second.Crop] = []
    for index, block in enumerate(output.blocks[:MAX_BLOCKS]):
        if block.kind not in ("paragraph", "list_item") or block.confidence != "medium":
            continue
        formulas = [r.value for r in block.runs[:MAX_RUNS] if r.kind == "math" and r.value.strip()]
        units = _units(block)
        if not formulas or units is None or lone_formula(block) is not None:
            continue
        picture = second.cut(png, ink.region(units))
        if picture is None:
            continue
        words = "".join(r.value for r in block.runs[:MAX_RUNS] if r.kind == "text")
        crops.append(second.Crop(index, picture, formulas, words))
    return crops


def second_readings(
    client: VisionClient,
    settings: Settings,
    result: PassResult,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> None:
    """Read the blocks that hold maths a second time, from a picture of each (in place: each
    read's `verdicts`, its `calls` and `result.stopped`). Fail-safe: no verdict for a block the
    second reading could not give."""
    work = {
        number: second_reading_crops(read.output, read.png)
        for number, read in sorted(result.reads.items())
        if read.output is not None and read.png
    }
    work = {number: crops for number, crops in work.items() if crops}
    if not work:
        return

    def one(number: int) -> tuple[int, second.CheckResult]:
        return number, second.check(client, settings, work[number])

    with ThreadPoolExecutor(
        max_workers=max(1, concurrency), thread_name_prefix="imagepage2"
    ) as pool:
        outcomes = list(pool.map(one, sorted(work)))
    for number, checked in outcomes:
        read = result.reads[number]
        read.verdicts = checked.verdicts
        read.calls.extend(checked.calls)
        if checked.stopped is not None:
            result.stopped = result.stopped or checked.stopped


RECOVERY_MARGIN = 15  # thousandths of the page a left-out stretch's picture is grown by


def _shifted(block: Block, region: tuple[int, int, int, int]) -> Block:
    """A block read from the picture of `region` (thousandths of the page) with its box in
    the picture's thousandths, as a block with its box in the page's."""
    rx0, ry0, rx1, ry1 = region
    width, height = rx1 - rx0, ry1 - ry0

    def put(value: int, origin: int, extent: int) -> int:
        return origin + round(min(1000, max(0, value)) * extent / 1000)

    return block.model_copy(
        update={
            "x0": put(block.x0, rx0, width),
            "x1": put(block.x1, rx0, width),
            "y0": put(block.y0, ry0, height),
            "y1": put(block.y1, ry0, height),
        }
    )


def recover_omissions(
    client: VisionClient,
    settings: Settings,
    result: PassResult,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> None:
    """Read the stretches of ink the first reading left out, from a picture of each (in place:
    each read's `output`, `recovered` and `calls`, and `result.stopped`). The blocks that come
    back are joined to the page in reading order, so the omission check no longer flags what
    was read; a stretch that gives nothing stays flagged. At most `omission.PARAMS.max_regions`
    requests a page, none for a page read completely."""

    def one(
        number: int,
    ) -> tuple[int, ImagePageOutput | None, list[ModelCall], int, Exception | None]:
        read = result.reads[number]
        assert read.output is not None and read.png is not None
        blocks = list(read.output.blocks[:MAX_BLOCKS])
        calls: list[ModelCall] = []
        added = 0
        try:
            ink = Ink(read.png)
            regions = omitted_regions(blocks, ink)
            for region in regions:
                grown = (
                    max(0, region.x0 - RECOVERY_MARGIN),
                    max(0, region.y0 - RECOVERY_MARGIN),
                    min(1000, region.x1 + RECOVERY_MARGIN),
                    min(1000, region.y1 + RECOVERY_MARGIN),
                )
                picture = second.cut(
                    read.png,
                    (
                        round(grown[0] / 1000 * ink.width),
                        round(grown[1] / 1000 * ink.height),
                        round(grown[2] / 1000 * ink.width),
                        round(grown[3] / 1000 * ink.height),
                    ),
                    margin=0,
                )
                if picture is None:
                    continue
                parsed, call = client.run(build_request(settings, picture), ImagePageOutput)
                calls.append(call)
                if parsed is None:
                    continue
                for new in parsed.blocks[:MAX_BLOCKS]:
                    if _units(new) is None:
                        continue
                    shifted = _shifted(new, grown)
                    above = [i for i, b in enumerate(blocks) if b.y0 <= shifted.y0]
                    blocks.insert((above[-1] if above else -1) + 1, shifted)
                    added += 1
        except (BudgetExceeded, ConfigError, UnknownModelError) as exc:
            return number, None, calls, 0, exc
        except Exception as exc:  # a failed recovery leaves the page as it was read
            log.warning("image page recovery failed: %s", type(exc).__name__)
            return number, None, calls, 0, None
        return number, ImagePageOutput(blocks=blocks) if added else None, calls, added, None

    work = [
        number
        for number, read in sorted(result.reads.items())
        if read.output is not None and read.png and read.outcome == "ok"
    ]
    with ThreadPoolExecutor(
        max_workers=max(1, concurrency), thread_name_prefix="imagepage3"
    ) as pool:
        outcomes = list(pool.map(one, work))
    for number, output, calls, added, error in outcomes:
        read = result.reads[number]
        read.calls.extend(calls)
        if output is not None:
            read.output, read.recovered = output, added
        if isinstance(error, (ConfigError, UnknownModelError)):
            raise error
        if isinstance(error, BudgetExceeded):
            result.stopped = result.stopped or error


# -------------------------------------------------------------------------- the ink


class Ink:
    """The page picture as a mask of ink, to trim a block's box to what is drawn in it."""

    def __init__(self, png: bytes) -> None:
        from PIL import Image

        with Image.open(io.BytesIO(png)) as image:
            gray = np.asarray(image.convert("L"), dtype=np.uint8)
        self.height, self.width = gray.shape
        paper = float(np.percentile(gray, 90))
        # Ink is clearly darker than the paper: a scan's grain and a photo's shading are not.
        self.mask = gray < max(40.0, paper - 70.0)

    def region(
        self, units: tuple[int, int, int, int], pad: int = BOX_PAD
    ) -> tuple[int, int, int, int]:
        """The box (thousandths) as pixels, grown by `pad` thousandths, trimmed to its ink."""
        x0, y0, x1, y1 = units
        px0 = max(0, round((x0 - pad) / 1000 * self.width))
        px1 = min(self.width, round((x1 + pad) / 1000 * self.width))
        py0 = max(0, round((y0 - pad) / 1000 * self.height))
        py1 = min(self.height, round((y1 + pad) / 1000 * self.height))
        if px1 - px0 < 2 or py1 - py0 < 2:
            return px0, py0, px1, py1
        sub = self.mask[py0:py1, px0:px1]
        cols = np.nonzero(sub.sum(axis=0) >= 2)[0]
        rows = np.nonzero(sub.sum(axis=1) >= 2)[0]
        if cols.size == 0 or rows.size == 0:  # nothing drawn there: the grown box stands
            return px0, py0, px1, py1
        return (
            max(px0, px0 + int(cols[0]) - INK_PAD_PX),
            max(py0, py0 + int(rows[0]) - INK_PAD_PX),
            min(px1, px0 + int(cols[-1]) + 1 + INK_PAD_PX),
            min(py1, py0 + int(rows[-1]) + 1 + INK_PAD_PX),
        )

    def after_marker(self, pixels: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        """`pixels` with a list marker ("1.", "(b)") at its left edge left out: when the ink
        begins with a small cluster (at most 6% of the page's width) and a clear gap (at least
        3%), the box starts where the ink after the gap starts. Otherwise as it was."""
        px0, py0, px1, py1 = pixels
        if px1 - px0 < 2 or py1 - py0 < 2:
            return pixels
        columns = self.mask[py0:py1, px0:px1].sum(axis=0) >= 1
        ink = np.nonzero(columns)[0]
        if ink.size == 0:
            return pixels
        first_end = int(ink[0])
        for column in ink:
            if column - first_end > MARKER_GAP * self.width:
                break
            first_end = int(column)
        else:
            return pixels  # no gap: the whole box is one stretch of ink
        later = ink[ink > first_end + MARKER_GAP * self.width]
        if first_end - int(ink[0]) > MARKER_WIDTH * self.width or later.size == 0:
            return pixels
        return max(px0, px0 + int(later[0]) - INK_PAD_PX), py0, px1, py1


def _units(block: Block) -> tuple[int, int, int, int] | None:
    """The block's box clamped to 0-1000, or None when it has no area."""
    x0, y0, x1, y1 = (min(1000, max(0, v)) for v in (block.x0, block.y0, block.x1, block.y1))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return x0, y0, x1, y1


# ------------------------------------------------------------------------- the text

_NEUTRAL = {
    **dict.fromkeys(map(ord, "­​‌‍⁠﻿")),
    ord("⟦"): "〚",  # an inline-marker bracket becomes its look-alike
    ord("⟧"): "〛",
}
_NO_SPACE_BEFORE = frozenset(",.;:!?)]}%")
_NO_SPACE_AFTER = frozenset("([{")


def scrub(text: str) -> str:
    """Model text as document text: invisible characters out, control characters out, the
    marker brackets neutralised (`layout._TEXT_MAP`'s rule). Spaces are kept."""
    text = text.translate(_NEUTRAL)
    return "".join(c for c in text if c.isspace() or unicodedata.category(c) not in {"Cc", "Cf"})


def _clean_literal(text: str) -> str:
    return " ".join(scrub(text).split())


def count_illegible(text: str) -> int:
    return text.count(ILLEGIBLE)


def _is_ordered(marker: str) -> bool:
    core = marker.strip().strip("().")
    return bool(core) and len(core) <= 6 and core.isalnum() and core != "o"


def _spaced(parts: list[tuple[str, str]]) -> list[str]:
    """The runs' texts with a space where a text run and a formula meet without one."""
    out: list[str] = []
    previous: tuple[str, str] | None = None
    for kind, text in parts:
        if previous is not None and out:
            tail, head = out[-1][-1:], text[:1]
            meets = (previous[0] == "text") != (kind == "text")  # exactly one is a formula
            if (
                meets
                and tail
                and head
                and not tail.isspace()
                and not head.isspace()
                and head not in _NO_SPACE_BEFORE
                and tail not in _NO_SPACE_AFTER
            ):
                out.append(" ")
        out.append(text)
        previous = (kind, text)
    return out


# ------------------------------------------------------------------------- building


@dataclass
class Built:
    """One page turned into elements and the fixes decided without a model: everything except
    the display equations (the equation step reads their crops) and the headings (the heading
    step). `elements` are in reading order with `order` counting from 0 on this page; ids are
    "p{page+1}-e{n}"."""

    page: int
    elements: list[Element]
    fixes: dict[str, Fix]
    record: ImagePageRecord


@dataclass
class _Builder:
    page: int
    box: BBox
    ink: Ink | None
    elements: list[Element] = field(default_factory=list)
    fixes: dict[str, Fix] = field(default_factory=dict)
    next_index: int = 0
    kept_as_image: int = 0
    illegible: int = 0
    low_confidence: int = 0
    omitted: int = 0

    def new_id(self) -> str:
        eid = f"p{self.page + 1}-e{self.next_index}"
        self.next_index += 1
        return eid

    def bbox(self, block: Block, *, skip_marker: bool = False) -> tuple[BBox, bool]:
        """The block's box in points, and whether it came from a usable box (an unusable one
        is the whole page). `skip_marker` leaves a list marker at the left edge out."""
        units = _units(block)
        if units is None:
            return self.box, False
        x0, y0, x1, y1 = self.box
        width, height = x1 - x0, y1 - y0
        if self.ink is not None:
            px0, py0, px1, py1 = self.ink.region(units)
            if skip_marker:
                px0, py0, px1, py1 = self.ink.after_marker((px0, py0, px1, py1))
            fx0, fy0 = px0 / self.ink.width, py0 / self.ink.height
            fx1, fy1 = px1 / self.ink.width, py1 / self.ink.height
        else:
            fx0, fy0, fx1, fy1 = (v / 1000 for v in units)
        return (
            (
                round(x0 + fx0 * width, 2),
                round(y0 + fy0 * height, 2),
                round(x0 + fx1 * width, 2),
                round(y0 + fy1 * height, 2),
            ),
            True,
        )

    def add(self, element: Element) -> None:
        self.elements.append(element.model_copy(update={"order": len(self.elements)}))


def _inline_equation(
    latex: str, parent: str, box: BBox, builder: _Builder, block: Block
) -> Element | None:
    """The inline equation element and its fix for one formula run, or None when the LaTeX
    does not validate (the formula then stays in the text)."""
    cleaned = equation_step.strip_trailing_punctuation(equation_step.clean_latex(latex))
    if not cleaned.strip():
        return None
    mathml, checks = equation_step._convert(cleaned, "inline")
    if mathml is None or has_errors(checks):
        return None
    speech, speech_check = equation_step._speech(mathml)
    element_id = builder.new_id()
    element = Element(
        id=element_id,
        kind=Kind.EQUATION,
        page=builder.page,
        bbox=box,
        order=0,
        display="inline",
        parent_id=parent,
        reasons=[IMAGE_PAGE_REASON, NO_CROP_REASON],
        image_confidence=block.confidence,
    )
    builder.fixes[element_id] = Fix(
        element_id=element_id,
        kind=Kind.EQUATION,
        status=Status.ACCEPTED,
        confidence=CONFIDENCE_VALUE[block.confidence],
        equation=EquationFix(latex=cleaned, mathml=mathml, speech=speech, display="inline"),
        checks=[*checks, speech_check],
        review="none",
    )
    return element


def _runs_text(
    block: Block, builder: _Builder, parent: str, box: BBox
) -> tuple[str, list[Element], int]:
    """The text of a block's runs with a marker where each valid formula goes, the inline
    equation elements, and how many formulas did not validate."""
    parts: list[tuple[str, str]] = []
    inline: list[Element] = []
    invalid = 0
    for run_ in block.runs[:MAX_RUNS]:
        if run_.kind == "text":
            if run_.value:
                parts.append(("text", scrub(run_.value)))
            continue
        if not run_.value.strip():
            continue
        element = _inline_equation(run_.value, parent, box, builder, block)
        if element is None:
            invalid += 1
            literal = _clean_literal(run_.value)
            parts.append(("math", f"[{literal}]" if literal else ""))
        else:
            inline.append(element)
            parts.append(("math", inline_marker(element.id)))
    text = " ".join("".join(_spaced(parts)).split())
    return text[:MAX_TEXT_CHARS], inline, invalid


def _tier_checks(
    confidence: Confidence, illegible: int, invalid_inline: int = 0
) -> tuple[list[Check], bool]:
    """The checks that say why a prose-like block has its tier, and whether it is required.
    The first (`no_text_layer`) is on every block: nothing can confirm a reading made from an
    image, so every one is at least a note."""
    checks = [
        Check(name="image_page.no_text_layer", passed=False, severity="warning", code="image_page")
    ]
    if confidence != "high":
        checks.append(
            Check(
                name="image_page.read_confidence",
                passed=False,
                severity="warning",
                code="read_low" if confidence == "low" else "read_medium",
            )
        )
    if illegible:
        checks.append(
            Check(name="image_page.illegible", passed=False, severity="warning", code="illegible")
        )
    if invalid_inline:
        checks.append(
            Check(
                name="image_page.inline_math",
                passed=False,
                severity="warning",
                code="inline_math_invalid",
            )
        )
    return checks, confidence != "high" or bool(illegible) or bool(invalid_inline)


AGREED_CONFIDENCE = 0.85


def _prose_fix(
    element: Element,
    block: Block,
    illegible: int,
    invalid_inline: int,
    verdict: second.Verdict | None = None,
    accept_agreed: bool = False,
) -> Fix:
    checks, required = _tier_checks(block.confidence, illegible, invalid_inline)
    confidence = CONFIDENCE_VALUE[block.confidence]
    if verdict is not None:
        agreed = verdict.agree
        code = "second_reading_differs"
        if agreed:
            code = (
                "second_reading_agrees"
                if verdict.confidence == "high"
                else "second_reading_agrees_unsure"
            )
        checks.append(
            Check(name="image_page.second_reading", passed=agreed, severity="warning", code=code)
        )
        # The one place a second reading lifts a block out of the decisions: the page reader
        # called it `medium` (not `low`, not illegible, every formula valid), the line read
        # alone said the same and was sure of every symbol, and the owner turned this on.
        if (
            accept_agreed
            and agreed
            and verdict.confidence == "high"
            and block.confidence == "medium"
            and not illegible
            and not invalid_inline
        ):
            required, confidence = False, AGREED_CONFIDENCE
    return Fix(
        element_id=element.id,
        kind=Kind.PARAGRAPH,
        status=Status.NEEDS_REVIEW if required else Status.ACCEPTED,
        confidence=confidence,
        checks=checks,
        review="required" if required else "optional",
    )


def _add_text_block(
    block: Block,
    builder: _Builder,
    verdict: second.Verdict | None = None,
    accept_agreed: bool = False,
) -> None:
    kind = {
        "heading": Kind.HEADING,
        "paragraph": Kind.PARAGRAPH,
        "list_item": Kind.LIST_ITEM,
    }[block.kind]
    box, _ = builder.bbox(block)
    element_id = builder.new_id()
    text, inline, invalid = _runs_text(block, builder, element_id, box)
    if not re.sub(r"\s", "", re.sub(r"⟦[^⟧]*⟧", "x", text)):
        builder.next_index -= 1  # nothing to write (so no formula either): give the id back
        return
    illegible = count_illegible(text)
    builder.illegible += illegible
    builder.low_confidence += block.confidence != "high"
    extra: dict[str, object] = {}
    reasons = [IMAGE_PAGE_REASON]
    if kind is Kind.HEADING:
        reasons.append(f"{layout.REASON_IMAGE_HEADING}{min(6, max(1, block.level or 1))}")
    if kind is Kind.LIST_ITEM:
        marker = _clean_literal(block.marker)
        extra = {
            "list_marker": marker or "•",
            "list_ordered": _is_ordered(marker) if marker else False,
            "list_level": min(4, max(1, block.level or 1)),
        }
    element = Element(
        id=element_id,
        kind=kind,
        page=builder.page,
        bbox=box,
        order=0,
        text=text,
        reasons=reasons,
        image_confidence=block.confidence,
        **extra,  # ty: ignore[invalid-argument-type]
    )
    builder.add(element)
    for eq in inline:
        builder.add(eq)
    if kind is not Kind.HEADING:  # a heading's fix comes from the heading step
        builder.fixes[element_id] = _prose_fix(
            element, block, illegible, invalid, verdict, accept_agreed
        )


def lone_formula(block: Block) -> Block | None:
    """The display equation a paragraph or list item really is, or None. A line that holds
    one formula and no words is an equation set on its own line, whatever marker ("1.",
    "(b)") stands beside it; the reader often wrote it as a list item (35 of the 56 display
    equations of the handwriting held-out set). Its marker becomes its number. The formula
    must validate as display maths: otherwise the block stays as it was, text and all."""
    if block.kind not in ("paragraph", "list_item"):
        return None
    formulas = [r.value for r in block.runs if r.kind == "math" and r.value.strip()]
    words = "".join(r.value for r in block.runs if r.kind == "text")
    if len(formulas) != 1 or any(c.isalnum() for c in words):
        return None
    latex = equation_step.clean_latex(formulas[0])
    mathml, checks = equation_step._convert(latex, "block")
    if not latex.strip() or mathml is None or has_errors(checks):
        return None
    return block.model_copy(
        update={
            "kind": "equation",
            "latex": latex,
            "label": block.marker if block.kind == "list_item" else "",
            "marker": "",
            "runs": [],
            "level": 0,
        }
    )


def _add_equation(block: Block, builder: _Builder, *, skip_marker: bool = False) -> None:
    latex = equation_step.clean_latex(block.latex)
    if not latex.strip():
        return
    box, usable = builder.bbox(block, skip_marker=skip_marker)
    mathml, checks = equation_step._convert(latex, "block")
    reading = mathml is not None and not has_errors(checks)
    element_id = builder.new_id()
    reasons = [equation_images.REASON, IMAGE_PAGE_REASON]
    if not usable:
        reasons.append(NO_BOX_REASON)
    element = Element(
        id=element_id,
        kind=Kind.EQUATION,
        page=builder.page,
        bbox=box,
        order=0,
        display="block",
        equation_label=_clean_literal(block.label) or None,
        reasons=reasons,
        image_confidence=block.confidence,
        page_latex=latex if reading else None,
        page_status="span" if reading and usable else None,
    )
    builder.add(element)
    builder.low_confidence += block.confidence != "high"
    if usable:
        return  # the equation step reads the crop of the box
    # No box, no crop to read: the page reader's own reading is the proposal, to be checked.
    if reading and mathml is not None:
        speech, speech_check = equation_step._speech(mathml)
        fix = Fix(
            element_id=element_id,
            kind=Kind.EQUATION,
            status=Status.NEEDS_REVIEW,
            confidence=min(CONFIDENCE_VALUE[block.confidence], 0.6),
            equation=EquationFix(latex=latex, mathml=mathml, speech=speech, display="block"),
            checks=[
                *checks,
                speech_check,
                Check(
                    name="equation.no_text_layer",
                    passed=False,
                    severity="warning",
                    code=equation_step.NO_TEXT_LAYER_CODE,
                ),
                Check(
                    name="image_page.single_reading",
                    passed=False,
                    severity="warning",
                    code="single_reading_no_box",
                ),
            ],
            review="required",
        )
    else:
        fix = Fix(
            element_id=element_id,
            kind=Kind.EQUATION,
            status=Status.FAILED,
            reason="invalid_latex",
            checks=checks,
            review="required",
        )
        builder.kept_as_image += 1
    builder.fixes[element_id] = fix


def _grid_rows(rows: list[Row]) -> list[table_step.RowOutput]:
    """The model's rows as the table step's, every span at least 1."""
    return [
        table_step.RowOutput(
            cells=[
                table_step.CellOutput(
                    text=c.text,
                    latex=c.latex,
                    header=c.header,
                    row_span=max(1, c.row_span),
                    col_span=max(1, c.col_span),
                )
                for c in row.cells
            ]
        )
        for row in rows
    ]


def _add_table(block: Block, builder: _Builder) -> None:
    box, _ = builder.bbox(block)
    caption = _clean_literal(block.caption) or None
    output = table_step.TableOutput(
        is_data_table=True, caption=caption or "", rows=_grid_rows(block.rows)
    )
    grid = table_step.grid_from_output(output, caption=caption)
    element_id = builder.new_id()
    element = Element(
        id=element_id,
        kind=Kind.TABLE,
        page=builder.page,
        bbox=box,
        order=0,
        caption=caption,
        reasons=[IMAGE_PAGE_REASON],
        image_confidence=block.confidence,
    )
    builder.add(element)
    builder.low_confidence += block.confidence != "high"
    if grid is None or not grid.cells:
        builder.kept_as_image += 1
        builder.fixes[element_id] = Fix(
            element_id=element_id,
            kind=Kind.TABLE,
            status=Status.FAILED,
            reason="invalid_table",
            review="required",
        )
        return
    checks = [*table_step.structure_checks(grid), *table_step.flattened_check(grid)]
    maths = [c for c in grid.cells if c.latex]
    if maths:
        invalid = any(c.mathml is None for c in maths)
        checks.append(
            Check(
                name="table.math",
                passed=not invalid,
                severity="error",
                code="invalid_latex" if invalid else None,
            )
        )
    checks.append(
        Check(name="table.no_text_layer", passed=False, severity="warning", code="no_text_layer")
    )
    if block.confidence != "high":
        checks.append(
            Check(
                name="image_page.read_confidence",
                passed=False,
                severity="warning",
                code="read_low" if block.confidence == "low" else "read_medium",
            )
        )
    builder.fixes[element_id] = Fix(
        element_id=element_id,
        kind=Kind.TABLE,
        status=Status.NEEDS_REVIEW,
        confidence=CONFIDENCE_VALUE[block.confidence],
        table=TableFix(grid=grid),
        checks=checks,
        review="required",
    )


def _add_figure(block: Block, builder: _Builder) -> None:
    box, _ = builder.bbox(block)
    caption = _clean_literal(block.caption) or None
    alt = clean_alt(block.description)
    illegible = count_illegible(alt)
    builder.illegible += illegible
    builder.low_confidence += block.confidence != "high"
    element_id = builder.new_id()
    builder.add(
        Element(
            id=element_id,
            kind=Kind.FIGURE,
            page=builder.page,
            bbox=box,
            order=0,
            caption=caption,
            reasons=[IMAGE_PAGE_REASON],
            image_confidence=block.confidence,
        )
    )
    checks = check_alt(alt, decorative=False, caption=caption)
    tier, required = _tier_checks(block.confidence, illegible)
    if has_errors(checks) or not alt:
        required = True
    builder.fixes[element_id] = Fix(
        element_id=element_id,
        kind=Kind.FIGURE,
        status=Status.NEEDS_REVIEW if required else Status.ACCEPTED,
        confidence=CONFIDENCE_VALUE[block.confidence],
        figure=FigureFix(figure_type="other", alt=alt),
        checks=[*checks, *tier],
        review="required" if required else "optional",
    )


def _original_figure(builder: _Builder) -> Element:
    """The figure for the whole page, the original behind the conversion (the output's "show
    the original page" toggle). First in the page. It has no fix: there is nothing to decide
    about it, and the steps leave it alone (`is_original`)."""
    element_id = builder.new_id()
    element = Element(
        id=element_id,
        kind=Kind.FIGURE,
        page=builder.page,
        bbox=builder.box,
        order=0,
        reasons=[ORIGINAL_REASON],
    )
    return element


def omitted_regions(blocks: Sequence[Block], ink: Ink | None) -> list[omission.Region]:
    """The stretches of ink no block accounts for (`image_page_omission`). A page with a block
    that has no usable box cannot be judged: none."""
    if ink is None:
        return []
    boxes = [_units(b) for b in blocks]
    if not boxes or any(u is None for u in boxes):
        return []
    return omission.find(ink.mask, [u for u in boxes if u is not None])


def _add_omissions(builder: _Builder, blocks: Sequence[Block]) -> None:
    """Make each stretch of ink that no block accounts for (`image_page_omission`) a figure
    element with a required decision, placed after the last block above it. A page with a
    block that has no usable box cannot be judged and is left as it is."""
    x0, y0, x1, y1 = builder.box
    width, height = x1 - x0, y1 - y0
    for region in omitted_regions(blocks, builder.ink):
        bbox: BBox = (
            round(x0 + region.x0 / 1000 * width, 2),
            round(y0 + region.y0 / 1000 * height, 2),
            round(x0 + region.x1 / 1000 * width, 2),
            round(y0 + region.y1 / 1000 * height, 2),
        )
        element_id = builder.new_id()
        element = Element(
            id=element_id,
            kind=Kind.FIGURE,
            page=builder.page,
            bbox=bbox,
            order=0,
            reasons=[IMAGE_PAGE_OMISSION_REASON],
        )
        above = [
            i
            for i, e in enumerate(builder.elements)
            if i > 0 and e.bbox is not None and e.bbox[1] <= bbox[1]
        ]
        builder.elements.insert((above[-1] if above else 0) + 1, element)
        builder.fixes[element_id] = Fix(
            element_id=element_id,
            kind=Kind.FIGURE,
            status=Status.NEEDS_REVIEW,
            confidence=0.3,
            figure=FigureFix(figure_type="other", alt=""),
            checks=[
                Check(
                    name="image_page.omitted_region",
                    passed=False,
                    severity="warning",
                    code="omitted_region",
                )
            ],
            review="required",
        )
        builder.omitted += 1
    builder.elements = [e.model_copy(update={"order": i}) for i, e in enumerate(builder.elements)]


def build(
    page: int,
    output: ImagePageOutput,
    *,
    box: BBox,
    png: bytes | None,
    check_omission: bool = True,
    verdicts: Mapping[int, second.Verdict] | None = None,
    accept_agreed: bool = False,
) -> Built | None:
    """The elements and decided fixes of one page, or None when nothing on it was usable.

    `box` is the page's frame in points (layout's), `png` the picture the model read (its
    ink trims the boxes and is compared with them; None leaves them as given and skips the
    comparison), `check_omission` turns the comparison off. `verdicts` are the second readings
    of blocks (by index in `output.blocks`); with `accept_agreed` a block whose two readings
    agree, each sure of itself, is an optional note instead of a required decision."""
    try:
        ink = Ink(png) if png else None
    except Exception as exc:  # an unreadable picture only costs the trimming
        log.warning("image page ink failed: %s", type(exc).__name__)
        ink = None
    builder = _Builder(page, box, ink)
    original = _original_figure(builder)
    builder.elements.append(original.model_copy(update={"order": 0}))
    by_kind: dict[str, int] = {}
    for index, block in enumerate(output.blocks[:MAX_BLOCKS]):
        before = len(builder.elements)
        lone = lone_formula(block)
        if lone is not None:
            _add_equation(lone, builder, skip_marker=bool(block.marker.strip()))
            block = lone
        elif block.kind in {"heading", "paragraph", "list_item"}:
            _add_text_block(block, builder, (verdicts or {}).get(index), accept_agreed)
        elif block.kind == "equation":
            _add_equation(block, builder)
        elif block.kind == "table":
            _add_table(block, builder)
        elif block.kind == "figure":
            _add_figure(block, builder)
        if len(builder.elements) > before:
            by_kind[block.kind] = by_kind.get(block.kind, 0) + 1
    blocks = sum(by_kind.values())
    if blocks == 0:
        return None
    if check_omission:
        try:
            _add_omissions(builder, output.blocks[:MAX_BLOCKS])
        except Exception as exc:  # the check only adds; a failure leaves the page as read
            log.warning("image page omission check failed: %s", type(exc).__name__)
    required = sum(1 for f in builder.fixes.values() if f.review == "required")
    optional = sum(1 for f in builder.fixes.values() if f.review == "optional")
    record = ImagePageRecord(
        page=page,
        outcome="converted",
        blocks=blocks,
        by_kind=dict(sorted(by_kind.items())),
        illegible=builder.illegible,
        low_confidence=builder.low_confidence,
        kept_as_image=builder.kept_as_image,
        omitted=builder.omitted,
        required=required,
        optional=optional,
    )
    return Built(page, builder.elements, builder.fixes, record)


# ------------------------------------------------------------------------ the headings


def tier_headings(fixes: Sequence[Fix], elements: Sequence[Element]) -> None:
    """Apply the image page tier to the heading fixes of elements read from an image (in
    place): never below an optional note; required when the page reader was not sure of the
    block or the text carries an `[illegible]` marker. A heading the heading step already
    asks about stays asked about."""
    by_id = {e.id: e for e in elements}
    for fix in fixes:
        element = by_id.get(fix.element_id)
        if fix.kind is not Kind.HEADING or element is None or element.image_confidence is None:
            continue
        confidence: Confidence = element.image_confidence
        text = element.text or ""
        checks, required = _tier_checks(confidence, count_illegible(text))
        fix.checks = [*fix.checks, *checks]
        if fix.status is Status.ACCEPTED and required:
            fix.status = Status.NEEDS_REVIEW
        if fix.review == "none" or (fix.review == "optional" and required):
            fix.review = "required" if required else "optional"
        if fix.confidence is not None:
            fix.confidence = min(fix.confidence, CONFIDENCE_VALUE[confidence])


# ------------------------------------------------------------------------------ pages


def rescued(fix: Fix, element: Element) -> Fix:
    """A display equation of an image page whose crop could not be read (the equation step
    abstained or failed: the box may be misplaced) keeps the page reader's own reading as a
    proposal, required, with no second reading, as when there was no box at all. Any other
    fix, and a step that stopped for a spend cap, is returned as it is."""
    if (
        IMAGE_PAGE_REASON not in element.reasons
        or element.kind is not Kind.EQUATION
        or element.display != "block"
        or fix.status not in (Status.ABSTAINED, Status.FAILED)
        or (fix.reason or "").startswith("budget_")
        or not element.page_latex
    ):
        return fix
    mathml, checks = equation_step._convert(element.page_latex, "block")
    if mathml is None or has_errors(checks):
        return fix
    speech, speech_check = equation_step._speech(mathml)
    return fix.model_copy(
        update={
            "status": Status.NEEDS_REVIEW,
            "reason": None,
            "review": "required",
            "confidence": min(CONFIDENCE_VALUE.get(element.image_confidence or "low", 0.4), 0.6),
            "equation": EquationFix(
                latex=element.page_latex, mathml=mathml, speech=speech, display="block"
            ),
            "checks": [
                *checks,
                speech_check,
                Check(
                    name="equation.no_text_layer",
                    passed=False,
                    severity="warning",
                    code=equation_step.NO_TEXT_LAYER_CODE,
                ),
                Check(
                    name="image_page.single_reading",
                    passed=False,
                    severity="warning",
                    code="single_reading_crop_failed",
                ),
            ],
        }
    )


def is_original(element: Element) -> bool:
    """The figure that stands for a converted page's own picture."""
    return ORIGINAL_REASON in element.reasons


def wants_crop(element: Element, fixes: Mapping[str, Fix]) -> bool:
    """Is this element, decided without a model, one whose region the review screen shows?
    A prose block that needs a decision (its crop is what the person checks it against)."""
    fix = fixes.get(element.id)
    return (
        IMAGE_PAGE_REASON in element.reasons
        and element.kind in {Kind.PARAGRAPH, Kind.LIST_ITEM}
        and fix is not None
        and fix.review == "required"
    )


def no_crop(element: Element, fixes: Mapping[str, Fix]) -> bool:
    """An inline equation of an image page: no region of its own, nothing to crop."""
    return NO_CROP_REASON in element.reasons and element.id in fixes


def kept_reads(kept: Mapping[int, str]) -> list[int]:
    """The kept pages (page -> reason) this pass reads, in page order."""
    return sorted(page for page, reason in kept.items() if reason in READ_REASONS)


def record_of(read: PageRead, settings: Settings, built: Built | None) -> ImagePageRecord:
    """The page's record: `built.record` with its calls' cost, or why the page stayed an image."""
    model = (
        read.calls[-1].model if read.calls else (settings.image_pages_model or settings.main_model)
    )
    cost = round(sum(c.cost_usd for c in read.calls), 8)
    if built is not None:
        base = built.record
    else:
        outcome = "empty" if read.output is not None else read.outcome
        base = ImagePageRecord(page=read.page, outcome=outcome)
    return base.model_copy(
        update={
            "model": model if read.calls else "",
            "calls": len(read.calls),
            "cost_usd": cost,
            "latency_ms": sum(c.latency_ms for c in read.calls),
            "input_tokens": sum(c.input_tokens for c in read.calls),
            "output_tokens": sum(c.output_tokens for c in read.calls),
            "recovered": read.recovered,
        }
    )


def summarise(
    reads: Mapping[int, PageRead],
    built: Mapping[int, Built],
    settings: Settings,
    stopped: BudgetExceeded | None,
) -> ImagePagesSummary:
    records = [record_of(reads[p], settings, built.get(p)) for p in sorted(reads)]
    converted = sum(1 for r in records if r.outcome == "converted")
    return ImagePagesSummary(
        pages=len(records),
        converted=converted,
        fell_back=len(records) - converted,
        calls=sum(r.calls for r in records),
        cost_usd=round(sum(r.cost_usd for r in records), 8),
        blocks=sum(r.blocks for r in records),
        stopped_by=stopped.scope if stopped is not None else None,
        per_page=records,
    )


def final_counts(
    summary: ImagePagesSummary, elements: Sequence[Element], fixes: Sequence[Fix]
) -> None:
    """Set each converted page's decisions and notes from the finished fixes (the headings'
    and the equation step's included), in place. Decisions are counted as the review
    counts them (`triage.units`: a group of repeated headings is one)."""
    page_of = {e.id: e.page for e in elements}
    for record in summary.per_page:
        if record.outcome != "converted":
            continue
        mine = [f for f in fixes if page_of.get(f.element_id) == record.page]
        record.required = len(triage.units(f for f in mine if f.review == "required"))
        record.optional = sum(1 for f in mine if f.review == "optional")


def notices(built: Mapping[int, Built]) -> list[Notice]:
    """One content-free notice per converted page: how many blocks were read from the image."""
    return [
        Notice(
            code="image_page_converted", page=page, count=max(1, b.record.blocks), review="optional"
        )
        for page, b in sorted(built.items())
    ]
