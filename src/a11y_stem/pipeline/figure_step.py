"""Figure step: classify the figure and write short alt text.

Short alt text only in the beta (long descriptions are out of 2026). Abstains rather than
guessing: a figure the model cannot read confidently becomes ABSTAINED and keeps a
visible "needs a description" flag in the output.

Routing: figures go to the main model. Tiny figures (no side longer than one inch on the
page: icons, bullets, small logos) start on the cheap model with a low-detail image and
escalate to the main model when the result would not be accepted (confidence below the
accept bar, an error-level alt-text check, or a self-contradicting decorative answer).
The first attempt goes through `models.router.call_with_fallback`; the escalation is one
main-model call with no fallback (the fallback would resend the cheap request that has
already been answered), and if it fails the cheap answer stands.

The model sees the caption and nearby text only as `context_text`, which the client
fences as untrusted data; the instructions are the constant prompt.

Status rules, in order:
1. no parsed output -> FAILED, reason "model_<outcome>";
2. confidence < settings.abstain_confidence -> ABSTAINED "low_confidence" (the prompt
   asks the model to abstain this way when it cannot read the figure);
3. non-decorative alt text failing an error-level check -> FAILED "invalid_alt";
4. ACCEPTED when confidence clears the bar and no check failed; the bar is
   settings.auto_accept_confidence, raised to DECORATIVE_ACCEPT_CONFIDENCE for
   decorative figures, because marking a figure decorative hides it from screen-reader
   users entirely;
5. otherwise NEEDS_REVIEW.
Decorative figures get `FigureFix(alt="", decorative=True)` whatever alt the model wrote.
A decorative answer that contradicts itself (a chart, diagram or table image; a figure
with a caption; or a non-empty alt) fails the warning check
`figure.decorative_consistency` (code `decorative_conflict`), so it is never ACCEPTED on
confidence alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from a11y_stem.config import Settings
from a11y_stem.core.types import Check, Element, FigureFix, FigureType, Fix, Kind, ModelCall, Status
from a11y_stem.models import router
from a11y_stem.models.base import T, VisionClient, VisionRequest
from a11y_stem.models.prompts import load_prompt
from a11y_stem.pipeline.validate import check_alt, clean_alt, has_errors

PROMPT_NAME = "figure"
# "<name>-<sha8 of the prompt text>": changes whenever the prompt file changes.
PROMPT_VERSION = load_prompt(PROMPT_NAME).version

TINY_FIGURE_MAX_SIDE_PT = 72.0  # one inch on the page
DECORATIVE_ACCEPT_CONFIDENCE = 0.95
MAX_OUTPUT_TOKENS = 1024
# Figure types that carry information by definition; "decorative" contradicts them.
INFORMATIVE_FIGURE_TYPES = frozenset({"chart", "diagram", "table_image"})


class FigureOutput(BaseModel):
    """Structured output the model must return (strict: no coercion, no extra keys)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    figure_type: FigureType = Field(description="What kind of figure this is.")
    decorative: bool = Field(description="True only if the image adds no information.")
    alt: str = Field(
        description="Alt text, at most 150 characters; empty when decorative or unreadable."
    )
    confidence: float = Field(
        ge=0, le=1, description="Probability that the alt text is accurate and sufficient."
    )


def _call(
    client: VisionClient, request: VisionRequest, schema: type[T], settings: Settings
) -> tuple[T | None, list[ModelCall]]:
    """One routed call (model, then its fallback). Looked up on the module at call time
    so tests can substitute the router."""
    return router.call_with_fallback(client, request, schema, settings=settings)


def _call_once(
    client: VisionClient, request: VisionRequest, schema: type[T]
) -> tuple[T | None, list[ModelCall]]:
    """One call to request.model with no fallback (the escalation). The client still
    reserves spend first and raises BudgetExceeded when refused."""
    parsed, call = client.run(request, schema)
    return parsed, [call]


def is_tiny(element: Element) -> bool:
    x0, y0, x1, y1 = element.bbox
    return max(x1 - x0, y1 - y0) <= TINY_FIGURE_MAX_SIDE_PT


def figure_context(element: Element, context_text: str | None) -> str | None:
    """Caption plus nearby text, for `VisionRequest.context_text` (fenced by the client)."""
    caption = (element.caption or "").strip()
    nearby = (context_text or "").strip()
    parts = []
    if caption:
        parts.append(f"Caption: {caption}")
    if nearby and nearby != caption:
        parts.append(f"Nearby text: {nearby}")
    return "\n\n".join(parts) or None


@dataclass
class _Candidate:
    output: FigureOutput | None
    calls: list[ModelCall]
    alt: str = ""
    checks: list[Check] = field(default_factory=list)

    @property
    def confidence(self) -> float | None:
        return None if self.output is None else self.output.confidence

    @property
    def usable(self) -> bool:
        return self.output is not None and not has_errors(self.checks)


def _decorative_conflict(output: FigureOutput, element: Element) -> bool:
    """A decorative answer that its own fields (or the page) contradict."""
    return (
        output.figure_type in INFORMATIVE_FIGURE_TYPES
        or bool((element.caption or "").strip())
        or bool(output.alt.strip())
    )


def _evaluate(output: FigureOutput | None, calls: list[ModelCall], element: Element) -> _Candidate:
    if output is None:
        return _Candidate(output, calls)
    if output.decorative:
        conflict = _decorative_conflict(output, element)
        consistency = Check(
            name="figure.decorative_consistency",
            passed=not conflict,
            severity="warning",
            code="decorative_conflict" if conflict else None,
        )
        return _Candidate(output, calls, "", [*check_alt("", decorative=True), consistency])
    alt = clean_alt(output.alt)  # stored as validated: no bidi controls or invisible marks
    return _Candidate(output, calls, alt, check_alt(alt, decorative=False, caption=element.caption))


def _conflicted(candidate: _Candidate) -> bool:
    return any(c.code == "decorative_conflict" for c in candidate.checks)


def _accept_bar(output: FigureOutput, settings: Settings) -> float:
    if output.decorative:
        return max(settings.auto_accept_confidence, DECORATIVE_ACCEPT_CONFIDENCE)
    return settings.auto_accept_confidence


def _needs_escalation(candidate: _Candidate, settings: Settings) -> bool:
    output = candidate.output
    if output is None:
        return False  # the router already tried the other model as the fallback
    if candidate.calls and candidate.calls[-1].model == settings.main_model:
        return False
    return (
        output.confidence < _accept_bar(output, settings)
        or not candidate.usable
        or _conflicted(candidate)
    )


def _prefer(main: _Candidate, cheap: _Candidate) -> _Candidate:
    if main.output is None or (not main.usable and cheap.usable):
        return cheap
    return main


def _fix(
    element: Element, candidate: _Candidate, calls: list[ModelCall], settings: Settings
) -> Fix:
    def make(status: Status, reason: str | None = None, proposal: bool = False) -> Fix:
        figure = None
        if proposal and candidate.output is not None:
            figure = FigureFix(
                figure_type=candidate.output.figure_type,
                alt=candidate.alt,
                decorative=candidate.output.decorative,
            )
        return Fix(
            element_id=element.id,
            kind=Kind.FIGURE,
            status=status,
            confidence=candidate.confidence,
            figure=figure,
            checks=candidate.checks,
            calls=calls,
            reason=reason,
        )

    output = candidate.output
    if output is None:
        outcome = calls[-1].outcome if calls else "skipped"
        return make(Status.FAILED, f"model_{outcome}")
    if output.confidence < settings.abstain_confidence:
        return make(Status.ABSTAINED, "low_confidence")
    if has_errors(candidate.checks):
        return make(Status.FAILED, "invalid_alt")
    failed = any(not c.passed for c in candidate.checks)
    if output.confidence >= _accept_bar(output, settings) and not failed:
        return make(Status.ACCEPTED, proposal=True)
    return make(Status.NEEDS_REVIEW, proposal=True)


def describe(
    element: Element,
    png: bytes,
    *,
    client: VisionClient,
    settings: Settings,
    context_text: str | None = None,
) -> Fix:
    """Alt text for one figure crop. Never raises for model or validation problems (they
    become FAILED/ABSTAINED fixes); raises BudgetExceeded when spend is refused."""
    prompt = load_prompt(PROMPT_NAME)
    tiny = is_tiny(element)
    context = figure_context(element, context_text)

    def attempt(model: str, *, escalation: bool = False) -> _Candidate:
        request = VisionRequest(
            step="figure",
            model=model,
            prompt_version=prompt.version,
            instructions=prompt.text,
            images=[png],
            context_text=context,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            image_detail="low" if tiny else "high",
        )
        if escalation:
            output, calls = _call_once(client, request, FigureOutput)
        else:
            output, calls = _call(client, request, FigureOutput, settings)
        return _evaluate(output, calls, element)

    first_model = settings.cheap_model if tiny else settings.main_model
    candidate = attempt(first_model)
    calls = list(candidate.calls)
    if first_model != settings.main_model and _needs_escalation(candidate, settings):
        escalated = attempt(settings.main_model, escalation=True)
        calls += escalated.calls
        candidate = _prefer(escalated, candidate)
    return _fix(element, candidate, calls, settings)
