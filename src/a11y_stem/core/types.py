"""Shared data model for the conversion pipeline.

A PDF becomes a list of `Element`s (what is on the page, in reading order). Each element
that needs work gets a `Fix` (what the pipeline proposes, how sure it is, which checks it
passed, and what the model calls cost). The assembler turns elements + fixes into
accessible HTML. `DocumentResult` is the whole thing, serialisable to JSON.

Privacy rule for this module: `Check.code`, `Fix.reason` and `ModelCall.outcome` are
category codes (e.g. "unknown_command"), never document text, so they can be logged and
aggregated after the document itself is deleted.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

BBox = tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF points


class Kind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    EQUATION = "equation"
    FIGURE = "figure"


class Status(StrEnum):
    """Lifecycle of a fix. The first four are set by the pipeline, the last two by review."""

    ACCEPTED = "accepted"  # passed every error-level check, confidence >= auto threshold
    NEEDS_REVIEW = "needs_review"  # usable proposal, but low confidence or a warning
    ABSTAINED = "abstained"  # no trustworthy proposal; the original stays, flagged
    FAILED = "failed"  # model/budget/validation error; the original stays, flagged
    EDITED = "edited"  # a reviewer changed the proposal
    REJECTED = "rejected"  # a reviewer rejected it; the original stays


class Check(BaseModel):
    """Result of one deterministic validator. Content-free by design."""

    name: str  # e.g. "latex.allowed_commands", "mathml.well_formed", "alt.length"
    passed: bool
    severity: Literal["error", "warning"] = "error"
    code: str | None = None  # category when it fails, e.g. "unknown_command"


Step = Literal["equation", "figure", "judge"]

Outcome = Literal[
    "ok", "refused", "invalid_output", "timeout", "api_error", "budget_exceeded", "skipped"
]


class ModelCall(BaseModel):
    """One request to a model provider (one attempt). Content-free."""

    step: Step
    model: str
    prompt_version: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0  # includes reasoning tokens, which are billed as output
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    attempt: int = 1
    is_fallback: bool = False
    outcome: Outcome = "ok"


class EquationFix(BaseModel):
    latex: str
    mathml: str  # presentation MathML, <math> root, display attribute set
    speech: str  # plain-English reading, for the review screen and the report
    display: Literal["block", "inline"] = "block"


FigureType = Literal["chart", "diagram", "photo", "illustration", "table_image", "other"]


class FigureFix(BaseModel):
    figure_type: FigureType
    alt: str  # short alt text; "" only when decorative
    decorative: bool = False


class HeadingFix(BaseModel):
    level: int = Field(ge=1, le=6)
    text: str


class Element(BaseModel):
    """Something on a page, in document reading order."""

    id: str  # stable within a document: "p{page+1}-e{index}"
    kind: Kind
    page: int  # 0-based
    bbox: BBox
    order: int  # reading-order position across the whole document
    text: str | None = None  # source text (untrusted input; never put in instructions)
    font_size: float | None = None
    bold: bool = False
    # Equation detection evidence (cheap, explainable; see pipeline/parse.py).
    score: float | None = None
    reasons: list[str] = Field(default_factory=list)
    display: Literal["block", "inline"] | None = None  # equations only
    caption: str | None = None  # figures: nearby "Figure N ..." text, if found


class Fix(BaseModel):
    element_id: str
    kind: Kind
    status: Status
    confidence: float | None = None  # in [0, 1]; None when nothing was proposed
    equation: EquationFix | None = None
    figure: FigureFix | None = None
    heading: HeadingFix | None = None
    checks: list[Check] = Field(default_factory=list)
    calls: list[ModelCall] = Field(default_factory=list)
    reason: str | None = None  # category code for ABSTAINED / FAILED

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def failed_checks(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


class PageStats(BaseModel):
    page: int  # 0-based
    elements: int
    fixes: int
    cost_usd: float
    latency_ms: int


class DocumentStats(BaseModel):
    pages: int
    cost_usd: float
    latency_ms: int
    model_calls: int
    by_kind: dict[str, int] = Field(default_factory=dict)  # fixes per Kind
    by_status: dict[str, int] = Field(default_factory=dict)  # fixes per Status
    per_page: list[PageStats] = Field(default_factory=list)


class DocumentResult(BaseModel):
    """Everything the pipeline produced for one PDF. Crop images are kept separately."""

    pipeline_version: str
    prompt_versions: dict[str, str] = Field(default_factory=dict)  # step -> version
    title: str | None = None
    lang: str = "en"
    elements: list[Element] = Field(default_factory=list)
    fixes: list[Fix] = Field(default_factory=list)
    stats: DocumentStats
    html: str = ""

    def fix_for(self, element_id: str) -> Fix | None:
        return next((f for f in self.fixes if f.element_id == element_id), None)


class ConversionOutput(BaseModel):
    """Return value of `pipeline.run.convert_pdf`: the result plus element crops (PNG)."""

    result: DocumentResult
    crops: dict[str, bytes] = Field(default_factory=dict)  # element_id -> PNG bytes
