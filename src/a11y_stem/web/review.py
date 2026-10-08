"""Reviewer decisions on fixes: accept, edit or reject, applied to a `DocumentResult`.

The web route owns the transaction; this module owns the rules:

- accept: only a fix that has a proposal (an abstained or failed fix has nothing to accept;
  the reviewer can edit it or keep the original by rejecting it),
- edit: the reviewer's LaTeX / alt text / heading level goes through the same
  deterministic validators as model output (`pipeline.validate`) before it is stored;
  error-level failures are shown back to the reviewer, nothing is saved,
- reject: the original stays in the document, flagged.

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

logger = logging.getLogger(__name__)

ACTIONS = ("accept", "edit", "reject")
STATUS_FOR_ACTION = {"accept": Status.ACCEPTED, "edit": Status.EDITED, "reject": Status.REJECTED}
MAX_LATEX_CHARS = 5000
MAX_ALT_CHARS = 1000

# Review screen order: what needs a decision first, then what was not converted, then the
# rest. Within a group, document order.
_ATTENTION = {Status.NEEDS_REVIEW: 0, Status.FAILED: 1, Status.ABSTAINED: 1}


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


def needs_attention(status: Status | str) -> bool:
    return Status(status) in _ATTENTION


def review_order(result: DocumentResult) -> list[Fix]:
    order = {e.id: e.order for e in result.elements}
    return sorted(
        result.fixes,
        key=lambda f: (_ATTENTION.get(f.status, 2), order.get(f.element_id, 1 << 30)),
    )


def has_proposal(fix: Fix) -> bool:
    return any(p is not None for p in (fix.equation, fix.figure, fix.heading))


def snapshot(fix: Fix) -> dict[str, Any]:
    """What a ReviewEdit stores as before/after (deleted with the job)."""
    return fix.model_dump(mode="json", include={"status", "equation", "figure", "heading"})


def field_id(prefix: str, element_id: str) -> str:
    return f"{prefix}-{element_id}"


def describe_failed(checks: list[Check]) -> str:
    names = ", ".join(f"{c.name} ({c.code})" if c.code else c.name for c in checks)
    return f"It did not pass these checks: {names}."


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
        raise ReviewInvalid(
            [
                FieldError(
                    field_id("actions", element_id),
                    "There is no proposal to accept. Edit it, or reject it to keep the original.",
                )
            ]
        )
    if action == "edit":
        warnings = _edit(fix, element, form)
    fix.status = STATUS_FOR_ACTION[action]
    result.stats.by_status = dict(Counter(f.status.value for f in result.fixes))
    return ReviewChange(
        fix=fix, action=action, before=before, after=snapshot(fix), warnings=warnings
    )


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
