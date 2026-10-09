"""Shared data model for the conversion pipeline.

A PDF becomes a list of `Element`s (what is on the page, in reading order). Each element
that needs work gets a `Fix` (what the pipeline proposes, how sure it is, which checks it
passed, and what the model calls cost). The assembler turns elements + fixes into
accessible HTML. `DocumentResult` is the whole thing, serialisable to JSON.

Privacy rule for this module: `Check.code`, `Fix.reason`, `ModelCall.outcome`,
`Fix.group_key` and `Verification` are category codes, ids or numbers (e.g.
"unknown_command", "hg-p3-e7"), never document text, so they can be logged and aggregated
after the document itself is deleted.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

BBox = tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF points

# Inline equations sit inside a paragraph. The paragraph's `text` holds this marker where
# the equation goes, and the equation element's `parent_id` is the paragraph id.
INLINE_MARKER = "\u27e6{id}\u27e7"  # rendered e.g. "⟦p1-e7⟧"


def inline_marker(element_id: str) -> str:
    return INLINE_MARKER.format(id=element_id)


class Kind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    EQUATION = "equation"
    FIGURE = "figure"
    # One item of a bulleted or numbered list (deterministic, no Fix). Consecutive items
    # with the same `list_level` and `list_ordered` form one <ul>/<ol> in the output.
    LIST_ITEM = "list_item"


class Status(StrEnum):
    """Lifecycle of a fix. The first four are set by the pipeline, the last two by review.

    A reviewer's "Accept" also sets ACCEPTED (the proposal is used as it is, exactly as
    when the pipeline accepts it); `Fix.reviewed` tells the two apart.
    """

    # Used as proposed: nothing needs a person's decision. It may still carry optional
    # notes (failed warning checks that do not change the meaning; `Fix.review`).
    ACCEPTED = "accepted"
    NEEDS_REVIEW = "needs_review"  # usable proposal that needs a decision (`review`)
    ABSTAINED = "abstained"  # no trustworthy proposal; the original stays, flagged
    FAILED = "failed"  # model/budget/validation error; the original stays, flagged
    EDITED = "edited"  # a reviewer changed the proposal
    REJECTED = "rejected"  # a reviewer rejected it; the original stays


# Statuses that wait for a person's decision. The pipeline gives them `review="required"`.
PENDING_STATUSES = frozenset({Status.NEEDS_REVIEW, Status.FAILED, Status.ABSTAINED})

# Review tier of a fix, set by the pipeline ("meaning-level only" policy, 2026-10-08):
#   required  a person must decide (NEEDS_REVIEW, FAILED or ABSTAINED): a wrong answer
#             would change what a student reads, or nothing usable was produced;
#   optional  accepted automatically, with a note a reviewer may look at (a cosmetic
#             style difference, a heading with weak evidence, a warning-only check);
#   none      accepted automatically, nothing to note.
# A reviewer's decision changes `status`, never `review`: the tier records what the
# pipeline asked for (ops aggregates count it).
Review = Literal["required", "optional", "none"]


def default_review(status: Status | str | None) -> Review:
    """The tier of a fix stored before tiers existed: waiting -> required, else none."""
    try:
        return "required" if Status(status) in PENDING_STATUSES else "none"
    except ValueError:
        return "none"


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


class Verification(BaseModel):
    """One answer of the second automated check (`models.verifier`). Content-free.

    Kept apart from `Fix.calls`: those are the attempts that produced the proposal, and
    routing, escalation and the eval's route counts read them. This records a probability
    question asked about the proposal afterwards (OpenAI Decisions API).

    - `check`: what was asked, "figure_alt" (does the alt text match the image) or
      "equation_match" (does the LaTeX match the equation image);
    - `stage`: "final" (the proposal on the fix) or "cheap" (an equation's cheap-model
      answer, before it was escalated);
    - `mode`: "note" (may add an optional note to an accepted fix) or "shadow" (recorded
      only; changes nothing);
    - `outcome`: "ok" when every question was answered, else why not (`Outcome` codes:
      refused, timeout, api_error, invalid_output, budget_exceeded). Anything but "ok"
      leaves the fix exactly as it would be without the check (fail-open);
    - `probabilities`: answer code -> probability ("ok", "factual_error"; "match",
      "cosmetic", "meaning_changing"). Not calibrated: a ranking signal, compared with
      thresholds from settings, never shown to a reader as a confidence;
    - `flagged`: the threshold said "possible mismatch" (None when not judged).

    Codes are plain strings, not literals, so a result written by a newer release with a
    check this one does not know still loads.
    """

    check: str
    stage: str = "final"
    mode: str = "note"
    outcome: str = "ok"
    probabilities: dict[str, float] = Field(default_factory=dict)
    flagged: bool | None = None
    model: str = ""
    version: str = ""  # "<check>-<sha8>" of the question wording, like prompt versions
    input_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


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
    parent_id: str | None = None  # inline equations: id of the containing paragraph
    # Display equations: the equation number printed beside it ("2.4", "(3)"), kept out
    # of the equation's bbox and text so it is not transcribed as part of the maths.
    equation_label: str | None = None
    # List items: the marker as printed ("a.", "3)", "•"), whether the list is ordered,
    # and the nesting level (1 = top level). `text` excludes the marker.
    list_marker: str | None = None
    list_ordered: bool | None = None
    list_level: int | None = None
    caption: str | None = None  # figures: nearby "Figure N ..." text, if found
    # Equations: letters of `text` the PDF draws, not bold, in a font known to have a bold
    # face (Computer Modern, Times, ...), sorted, no repeats. Such a letter is known to be
    # plain, so a bold reading of it (a scalar read as a vector) is a real difference.
    # None when layout did not record it (older results, equations built outside layout).
    regular_letters: str | None = None


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
    # True once a reviewer accepted, edited or rejected this fix on the review screen.
    # Defaults to False so results stored before the field existed still load.
    reviewed: bool = False
    # Review tier (see `Review`). Results stored before tiers existed get it from their
    # status (`default_review`): needs_review, failed, abstained -> required, else none.
    review: Review = "none"
    # Repeated headings decided together: every heading with the same label and style that
    # needed a decision shares this id ("hg-" + the first one's element id) and the group's
    # size, and the review screen asks once for the whole group. None when on its own.
    group_key: str | None = None
    group_size: int | None = None
    # The second automated check's answers about this fix (see `Verification`). Empty
    # when it did not run, and in results stored before it existed.
    verifications: list[Verification] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _review_from_status(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("review") is None:
            return {**data, "review": default_review(data.get("status"))}
        return data

    @property
    def cost_usd(self) -> float:
        """Everything this fix cost: its model calls and its second-check requests."""
        return sum(c.cost_usd for c in self.calls) + self.verifier_cost_usd

    @property
    def verifier_cost_usd(self) -> float:
        return sum(v.cost_usd for v in self.verifications)

    @property
    def pending(self) -> bool:
        """Waiting for a person's decision (counts toward "not done yet")."""
        return self.status in PENDING_STATUSES

    @property
    def optional_note(self) -> bool:
        """Accepted automatically with an optional note no reviewer has acted on yet."""
        return self.review == "optional" and self.status is Status.ACCEPTED and not self.reviewed

    @property
    def accepted_by_reviewer(self) -> bool:
        return self.status is Status.ACCEPTED and self.reviewed

    @property
    def decided_by_reviewer(self) -> bool:
        """Accepted, edited or rejected by a reviewer (EDITED and REJECTED only come from
        review, also in results stored before `reviewed` existed)."""
        return self.reviewed or self.status in {Status.EDITED, Status.REJECTED}

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
    # Fixes per review tier ("required", "optional", "none") as the pipeline left them,
    # and the decisions the required ones make (a heading group counts once). Empty and
    # None in results stored before tiers existed (`core.triage` recomputes them).
    by_review: dict[str, int] = Field(default_factory=dict)
    required_decisions: int | None = None
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
    # Run-level outcome, content-free like the codes above. `stopped_by` is the reason a
    # spend cap stopped model work early ("budget_daily", "budget_job_usd",
    # "budget_job_calls"): every equation and figure not done by then is FAILED with that
    # reason and keeps its original image. None when the run was not cut short.
    stopped_by: str | None = None
    # 0-based pages that are only a picture of a page (one image covering it, no text):
    # scanned pages. There is nothing to convert on them; the output keeps the image,
    # flagged as not converted.
    image_only_pages: list[int] = Field(default_factory=list)
    # Static checks of `html` (pipeline.html_checks.static_checks) when it was rendered.
    html_checks: list[Check] = Field(default_factory=list)
    # 0-based page -> (width, height) in points, the frame of the element boxes (the page
    # as layout measured it, after rotation): the review screen places its marks on the
    # page images with them. Empty in results stored before it existed (the review screen
    # then asks the PDF itself).
    page_sizes: dict[int, tuple[float, float]] = Field(default_factory=dict)

    def fix_for(self, element_id: str) -> Fix | None:
        return next((f for f in self.fixes if f.element_id == element_id), None)


class ConversionOutput(BaseModel):
    """Return value of `pipeline.run.convert_pdf`: the result plus element crops (PNG)."""

    result: DocumentResult
    crops: dict[str, bytes] = Field(default_factory=dict)  # element_id -> PNG bytes
