"""Reviewer decisions on fixes: accept, edit or reject, applied to a `DocumentResult`.

The web route owns the transaction; this module owns the rules:

- accept: only a fix with a proposal that passed every error-level check (an abstained
  fix has nothing to accept, and an invalid proposal must be edited, which re-validates
  it; the reviewer can also keep the original by rejecting it),
- edit: the reviewer's LaTeX / alt text / heading level goes through the same
  deterministic validators as model output (`pipeline.validate`) before it is stored;
  error-level failures are shown back to the reviewer, nothing is saved,
- reject: the original stays in the document, flagged,
- bulk accept: many fixes of one kind at once, but only the ones a single "Accept" could
  take (NEEDS_REVIEW, a proposal, no failed error-level check). FAILED and ABSTAINED
  fixes have nothing safe to accept and are never included.

It also decides the order of the review screen. A reviewer's time should go where the
risk is, so fixes are grouped by kind (equations, then figures, then headings); within a
group the ones still waiting for a decision come first, in page and reading order, and
the ones already decided follow (collapsed on the page).

Validators, speech and the HTML assembler are looked up on their modules at call time,
so tests (and other work packages' stubs) can be swapped in with monkeypatch.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from a11y_stem.core.types import (
    Check,
    DocumentResult,
    Element,
    EquationFix,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    Status,
)
from a11y_stem.web.messages import confidence_percent, explain_check

logger = logging.getLogger(__name__)

ACTIONS = ("accept", "edit", "reject")
STATUS_FOR_ACTION = {"accept": Status.ACCEPTED, "edit": Status.EDITED, "reject": Status.REJECTED}
MAX_LATEX_CHARS = 5000
MAX_ALT_CHARS = 1000

# Review screen groups, in priority order, and the slug each has in URLs and anchors.
GROUP_SLUGS = {Kind.EQUATION: "equations", Kind.FIGURE: "figures", Kind.HEADING: "headings"}
GROUP_KINDS = tuple(GROUP_SLUGS)
# Waiting for a decision: a proposal to check, or nothing usable (the reviewer can write
# one, or reject to confirm the original stays).
PENDING = frozenset({Status.NEEDS_REVIEW, Status.FAILED, Status.ABSTAINED})
NOT_CONVERTED = frozenset({Status.FAILED, Status.ABSTAINED})

# Bulk accept. "Accept all" is only offered for headings: a wrong heading level is cheap
# to spot and fix later, a wrong equation or alt text is not. The confidence threshold
# is offered for every group, from a fixed list so the page can show how many each
# choice would accept (no JavaScript).
BULK_ACCEPT_ALL = "accept_all"
BULK_ACCEPT_CONFIDENT = "accept_confident"
BULK_ACTIONS = (BULK_ACCEPT_ALL, BULK_ACCEPT_CONFIDENT)
ACCEPT_ALL_KINDS = frozenset({Kind.HEADING})
BULK_THRESHOLDS = (90, 80, 70, 60, 50)  # percent
BULK_DEFAULT_THRESHOLD = 80


@dataclass
class FieldError:
    field_id: str  # id of the form control, for the error summary link
    message: str


class ReviewInvalid(Exception):
    """The reviewer's input was not saved. Messages name checks, never repeat content."""

    def __init__(self, errors: list[FieldError], values: dict[str, str] | None = None) -> None:
        super().__init__("review input rejected")
        self.errors = errors
        self.values = values or {}  # the reviewer's input, re-shown in the form


@dataclass
class ReviewChange:
    fix: Fix
    action: str
    before: dict[str, Any]
    after: dict[str, Any]
    warnings: list[Check] = field(default_factory=list)


@dataclass
class FixGroup:
    """One kind's fixes as the review screen shows them."""

    kind: Kind
    pending: list[Fix]  # waiting for a decision, page and reading order
    done: list[Fix]  # accepted, edited or rejected, page and reading order

    @property
    def slug(self) -> str:
        return GROUP_SLUGS[self.kind]


@dataclass(frozen=True)
class BulkRequest:
    kind: Kind
    min_percent: int | None  # None: every eligible proposal ("accept all", headings only)


# --------------------------------------------------------------------------- order


def needs_attention(status: Status | str) -> bool:
    return Status(status) in PENDING


def kind_for_slug(slug: str) -> Kind | None:
    return next((kind for kind, s in GROUP_SLUGS.items() if s == slug), None)


def _positions(result: DocumentResult) -> dict[str, tuple[int, int, int]]:
    """element_id -> (group rank, page, reading order): the review screen's sort key."""
    elements = {e.id: e for e in result.elements}
    far = 1 << 30  # a fix whose element is missing goes last in its group
    out = {}
    for fix in result.fixes:
        if fix.kind not in GROUP_SLUGS:
            continue
        element = elements.get(fix.element_id)
        page, order = (element.page, element.order) if element else (far, far)
        out[fix.element_id] = (GROUP_KINDS.index(fix.kind), page, order)
    return out


def group_fixes(result: DocumentResult) -> list[FixGroup]:
    """Non-empty groups in priority order (equations, figures, headings). Paragraph and
    list fixes, if a future step makes any, are not reviewable here."""
    position = _positions(result)
    groups = []
    for kind in GROUP_KINDS:
        fixes = sorted(
            (f for f in result.fixes if f.kind is kind), key=lambda f: position[f.element_id]
        )
        if fixes:
            pending = [f for f in fixes if f.status in PENDING]
            done = [f for f in fixes if f.status not in PENDING]
            groups.append(FixGroup(kind, pending, done))
    return groups


def item_numbers(result: DocumentResult) -> dict[str, int]:
    """element_id -> 1-based number within its kind, in reading order. Stable while the
    reviewer works (unlike a position in the review order, which changes after every
    decision), so "Equation 3" means the same equation in every message."""
    position = _positions(result)
    numbers: dict[str, int] = {}
    for kind in GROUP_KINDS:
        ordered = sorted(
            (f.element_id for f in result.fixes if f.kind is kind), key=position.__getitem__
        )
        numbers.update({element_id: i for i, element_id in enumerate(ordered, start=1)})
    return numbers


def next_to_review(result: DocumentResult, element_id: str) -> str | None:
    """The first fix still waiting for a decision after `element_id` in review order,
    wrapping round to the first one; None when nothing is left."""
    position = _positions(result)
    pending = [f.element_id for g in group_fixes(result) for f in g.pending]
    here = position.get(element_id)
    later = [e for e in pending if here is not None and position[e] > here and e != element_id]
    remaining = later or [e for e in pending if e != element_id]
    return remaining[0] if remaining else None


# --------------------------------------------------------------------------- one fix


def has_proposal(fix: Fix) -> bool:
    return any(p is not None for p in (fix.equation, fix.figure, fix.heading))


def can_accept(fix: Fix) -> bool:
    """Only a proposal that passed every error-level check may be accepted as it is;
    anything else goes through "Save edit", which validates it again."""
    return has_proposal(fix) and not _blocking(fix.checks)


def snapshot(fix: Fix) -> dict[str, Any]:
    """What a ReviewEdit stores as before/after (deleted with the job)."""
    return fix.model_dump(mode="json", include={"status", "equation", "figure", "heading"})


def field_id(prefix: str, element_id: str) -> str:
    return f"{prefix}-{element_id}"


def describe_failed(checks: list[Check]) -> str:
    """The reviewer-facing sentences for failed checks, with their codes in brackets."""
    explained = [explain_check(c) for c in checks]
    sentences = " ".join(e.text for e in explained)
    return f"{sentences} ({'; '.join(e.code for e in explained)})"


def apply_review(
    result: DocumentResult, element_id: str, action: str, form: Mapping[str, str]
) -> ReviewChange:
    """Apply one decision to `result` in place. Raises LookupError or ReviewInvalid."""
    fix = result.fix_for(element_id)
    if fix is None:
        raise LookupError(element_id)
    if action not in ACTIONS:
        raise ReviewInvalid([FieldError(field_id("actions", element_id), "Choose an action.")])
    element = next((e for e in result.elements if e.id == element_id), None)
    before = snapshot(fix)
    warnings: list[Check] = []
    if action == "accept" and not has_proposal(fix):
        message = "There is no proposal to accept. Edit it, or reject it to keep the original."
        raise ReviewInvalid([FieldError(field_id("actions", element_id), message)])
    if action == "accept" and not can_accept(fix):
        message = "This proposal failed a required check, so it cannot be accepted as it is. "
        message += "Edit it, or reject it to keep the original."
        raise ReviewInvalid([FieldError(field_id("actions", element_id), message)])
    if action == "edit":
        warnings = _edit(fix, element, form)
    fix.status = STATUS_FOR_ACTION[action]
    _recount(result)
    return ReviewChange(
        fix=fix, action=action, before=before, after=snapshot(fix), warnings=warnings
    )


def _recount(result: DocumentResult) -> None:
    result.stats.by_status = dict(Counter(f.status.value for f in result.fixes))


def _edit(fix: Fix, element: Element | None, form: Mapping[str, str]) -> list[Check]:
    if fix.kind is Kind.EQUATION:
        return _edit_equation(fix, element, form)
    if fix.kind is Kind.FIGURE:
        return _edit_figure(fix, element, form)
    if fix.kind is Kind.HEADING:
        return _edit_heading(fix, element, form)
    raise ReviewInvalid([FieldError(field_id("actions", fix.element_id), "This cannot be edited.")])


def _blocking(checks: list[Check]) -> list[Check]:
    return [c for c in checks if not c.passed and c.severity == "error"]


def _edit_equation(fix: Fix, element: Element | None, form: Mapping[str, str]) -> list[Check]:
    from a11y_stem.pipeline import validate

    fid = field_id("latex", fix.element_id)
    latex = form.get("latex", "").strip()
    values = {"latex": latex}
    if not latex:
        raise ReviewInvalid([FieldError(fid, "Enter the LaTeX for this equation.")], values)
    if len(latex) > MAX_LATEX_CHARS:
        raise ReviewInvalid(
            [FieldError(fid, f"Keep the LaTeX under {MAX_LATEX_CHARS} characters.")], values
        )
    checks = list(validate.check_latex(latex))
    if failed := _blocking(checks):
        raise ReviewInvalid(
            [FieldError(fid, "The LaTeX was not saved. " + describe_failed(failed))], values
        )
    if fix.equation is not None:
        display = fix.equation.display
    else:
        display = (element.display if element else None) or "block"
    try:
        mathml = validate.safe_latex_to_mathml(latex, display)
    except ValueError:  # LatexConversionError and latex2mathml's own errors
        raise ReviewInvalid(
            [FieldError(fid, "The LaTeX was not saved: it could not be converted to MathML.")],
            values,
        ) from None
    mathml_checks = list(validate.check_mathml(mathml))
    if failed := _blocking(mathml_checks):
        raise ReviewInvalid(
            [FieldError(fid, "The LaTeX was not saved. " + describe_failed(failed))], values
        )
    fix.equation = EquationFix(latex=latex, mathml=mathml, speech=_speech(mathml), display=display)
    fix.checks = checks + mathml_checks
    return [c for c in fix.checks if not c.passed]


def _speech(mathml: str) -> str:
    """Speech text is a reading aid on the review screen; a failure must not block saving."""
    from a11y_stem.pipeline import speech

    try:
        return speech.mathml_to_speech(mathml)
    except Exception as exc:
        logger.warning("speech_failed", extra={"exc_type": type(exc).__name__})
        return ""


def _edit_figure(fix: Fix, element: Element | None, form: Mapping[str, str]) -> list[Check]:
    from a11y_stem.pipeline import validate

    fid = field_id("alt", fix.element_id)
    alt = " ".join(form.get("alt", "").split())
    decorative = form.get("decorative") in {"on", "true", "1"}
    values = {"alt": alt, "decorative": "on" if decorative else ""}
    if decorative:
        alt = ""
    elif not alt:
        raise ReviewInvalid(
            [FieldError(fid, "Enter alt text, or mark the figure as decorative.")], values
        )
    if len(alt) > MAX_ALT_CHARS:
        raise ReviewInvalid(
            [FieldError(fid, f"Keep the alt text under {MAX_ALT_CHARS} characters.")], values
        )
    checks = list(
        validate.check_alt(alt, decorative=decorative, caption=element.caption if element else None)
    )
    if failed := _blocking(checks):
        raise ReviewInvalid(
            [FieldError(fid, "The alt text was not saved. " + describe_failed(failed))], values
        )
    figure_type = fix.figure.figure_type if fix.figure else "other"
    fix.figure = FigureFix(figure_type=figure_type, alt=alt, decorative=decorative)
    fix.checks = checks
    return [c for c in checks if not c.passed]


def _edit_heading(fix: Fix, element: Element | None, form: Mapping[str, str]) -> list[Check]:
    fid = field_id("level", fix.element_id)
    raw = form.get("level", "")
    if not raw.isdigit() or not 1 <= int(raw) <= 6:
        raise ReviewInvalid(
            [FieldError(fid, "Choose a heading level from 1 to 6.")], {"level": raw}
        )
    if fix.heading is not None:
        text = fix.heading.text
    else:
        text = (element.text if element else None) or ""
    fix.heading = HeadingFix(level=int(raw), text=text)
    return []


# --------------------------------------------------------------------------- bulk


def bulk_eligible(fix: Fix) -> bool:
    """What a bulk accept may take: exactly what a single "Accept" could, and only while
    it is still waiting for review (a reviewer's own decision is never overwritten)."""
    return fix.status is Status.NEEDS_REVIEW and can_accept(fix)


def at_or_above(fix: Fix, min_percent: int) -> bool:
    return fix.confidence is not None and confidence_percent(fix.confidence) >= min_percent


def bulk_candidates(result: DocumentResult, request: BulkRequest) -> list[Fix]:
    return [
        f
        for f in result.fixes
        if f.kind is request.kind
        and bulk_eligible(f)
        and (request.min_percent is None or at_or_above(f, request.min_percent))
    ]


def bulk_field_id(slug: str) -> str:
    """The control a bulk error points at: the threshold select of that group."""
    return f"min-confidence-{slug}"


def parse_bulk(slug: str, action: str, min_confidence: str) -> BulkRequest:
    """The bulk form's fields as a request. Raises LookupError for an unknown group and
    ReviewInvalid for anything the page never offers."""
    kind = kind_for_slug(slug)
    if kind is None:
        raise LookupError(slug)
    fid = bulk_field_id(slug)
    if action == BULK_ACCEPT_ALL:
        if kind not in ACCEPT_ALL_KINDS:
            message = "Accepting every proposal at once is only offered for headings."
            raise ReviewInvalid([FieldError(fid, message)])
        return BulkRequest(kind, None)
    if action != BULK_ACCEPT_CONFIDENT:
        raise ReviewInvalid([FieldError(fid, "Choose an action.")])
    raw = min_confidence.strip().removesuffix("%")
    if not raw.isdigit() or int(raw) not in BULK_THRESHOLDS:
        choices = ", ".join(f"{t}%" for t in BULK_THRESHOLDS)
        raise ReviewInvalid([FieldError(fid, f"Choose a confidence from the list ({choices}).")])
    return BulkRequest(kind, int(raw))


def apply_bulk_accept(result: DocumentResult, request: BulkRequest) -> list[ReviewChange]:
    """Accept every candidate in place; one ReviewChange each (empty if none were left,
    so repeating the same request changes nothing)."""
    changes = []
    for fix in bulk_candidates(result, request):
        before = snapshot(fix)
        fix.status = Status.ACCEPTED
        changes.append(ReviewChange(fix=fix, action="accept", before=before, after=snapshot(fix)))
    if changes:
        _recount(result)
    return changes


# --------------------------------------------------------------------------- output


def rerender_html(result: DocumentResult, crops: dict[str, bytes]) -> str:
    """The output HTML after a review decision, from the same assembler the pipeline uses."""
    from a11y_stem.pipeline import assemble

    return assemble.render_html(
        title=result.title,
        lang=result.lang,
        elements=result.elements,
        fixes=result.fixes,
        crops=crops,
        source_note=None,
    )
