"""Figure step: classify the figure and write short alt text (and, with long descriptions
on, a long description, a chart's data table and the colour-only flag).

Abstains rather than guessing: a figure the model cannot read confidently becomes
ABSTAINED and keeps a visible "needs a description" flag in the output.

Long descriptions (`ConversionOptions.long_descriptions`, FEATURE_LONG_DESCRIPTIONS; off
by default). Off, the step sends the prompt `figure.md` with the schema `FigureOutput`,
exactly as before. On, it sends `figure_long.md` with `FigureLongOutput` in the same one
call per attempt, plus the labels printed in the figure (`Element.text`) as fenced
context, and the proposal may also carry (`pipeline.figure_details`):
- `long_description`: for charts, diagrams and complex illustrations, plain text after
  the figure. A description that is not usable (markup, a URL, a placeholder, too short
  or too long) is dropped, never shown; a chart or diagram without one gets the notice
  `long_description_missing` (optional; `long_description_missing` below). One the model
  marks unsure fails the warning check `figure.long_description`
  (`long_description_unsure`), a note: review "optional", never a decision;
- `data_table`: for charts only, the plotted values (`TableGrid`, source "model").
  `data_values_from_text` is True only when the model says every value is printed in
  the figure *and* every number in the table appears in the PDF's own text inside the
  figure region: the table then goes into the student file. Otherwise the warning check
  `figure.data_values` fails with `values_from_image`, a note (review "optional"): the
  table is kept on the fix as an optional extra the review screen offers ("Add the data
  table"), not written into the student file until a reviewer adds it
  (`figure_details.data_table_shown`). An additive extra never adds a required decision
  (docs/design/content-features.md "Review tiers");
- `colour_only`: the figure tells things apart by colour alone. It changes no status or
  tier (the job page lists it as the notice `colour_only`).
The alt text, the status rules and the escalation below are the same either way.

Exam mode (`ConversionOptions.exam_mode`; a per-job choice, off by default). The step sends
`figure_exam.md` with the schema `FigureOutput`: the alt text describes what the figure
shows and never the result (no trend, extreme, total, net quantity or value read off a
graph that is not printed in it). Long descriptions and data tables are not asked for in
an exam even when they are on, because a description of a trend or a table of values is a
result. Everything else, the status rules and the escalation included, is as above.

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
4. ACCEPTED when confidence clears the bar and no check failed other than a note
   (`NOTE_CODES`: alt text a little long, a redundant "image of"); the bar is
   settings.auto_accept_confidence, raised to DECORATIVE_ACCEPT_CONFIDENCE for
   decorative figures, because marking a figure decorative hides it from screen-reader
   users entirely. Review "optional" with notes, else "none";
5. otherwise NEEDS_REVIEW.
Rules 1-3 and 5 are review "required": the figure has no usable description yet (none
at all, one that failed a check, one the model doubts, a caption copy, or a
decorative answer that contradicts itself).
Decorative figures get `FigureFix(alt="", decorative=True)` whatever alt the model wrote.
A decorative answer that contradicts itself (a chart, diagram or table image; a figure
with a caption; or a non-empty alt) fails the warning check
`figure.decorative_consistency` (code `decorative_conflict`), so it is never ACCEPTED on
confidence alone.

Second automated check (`second_check`, ADR 0005): once a figure is ACCEPTED with alt
text, the job's verifier (the Decisions API, `models.verifier`) is asked whether the alt
text matches the image. When min(P(ok), 1 - P(factual error)) is below
settings.decisions_figure_flag_below, the warning check `figure.second_check` fails with
code `second_check_mismatch`, a note: the figure stays ACCEPTED and becomes review
"optional" ("A second automated check found a possible mismatch between the alt text and
the image"). It never makes a figure required and never changes its status or
confidence. If the check is off, has no answer (an error, a timeout, a refusal, no
budget left) or the figure is not an accepted description, the fix is exactly what it
would be without it; the answer, or why there is none, is recorded on
`Fix.verifications` either way.

With the Anthropic provider the job's verifier is the Claude check (`models.claude_verifier`,
T2-20): another Claude model, an audit prompt, yes/no answers. In shadow mode (the default,
CLAUDE_SECOND_CHECK=shadow) the answer is recorded with mode "shadow" and nothing else
happens: no check, no note, no change of review. Only CLAUDE_SECOND_CHECK=enforce gives the
note above, from the same threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from a11y_stem.config import Settings
from a11y_stem.core.types import (
    Check,
    Element,
    FigureFix,
    FigureType,
    Fix,
    Kind,
    ModelCall,
    Status,
    TableGrid,
)
from a11y_stem.models import router
from a11y_stem.models.base import T, VisionClient, VisionRequest
from a11y_stem.models.prompts import load_prompt
from a11y_stem.models.verifier import figure_check_mode, figure_score, verifier_of
from a11y_stem.pipeline import figure_details
from a11y_stem.pipeline import options as run_options
from a11y_stem.pipeline.validate import check_alt, clean_alt, has_errors

PROMPT_NAME = "figure"
# "<name>-<sha8 of the prompt text>": changes whenever the prompt file changes.
PROMPT_VERSION = load_prompt(PROMPT_NAME).version
# The prompt sent instead when long descriptions are on (`ConversionOptions`).
LONG_PROMPT_NAME = "figure_long"
LONG_PROMPT_VERSION = load_prompt(LONG_PROMPT_NAME).version
# The prompt sent instead for an exam (`ConversionOptions.exam_mode`): the same output
# fields, and alt text rules that describe what is shown and never the result.
EXAM_PROMPT_NAME = "figure_exam"
EXAM_PROMPT_VERSION = load_prompt(EXAM_PROMPT_NAME).version

TINY_FIGURE_MAX_SIDE_PT = 72.0  # one inch on the page
DECORATIVE_ACCEPT_CONFIDENCE = 0.95
MAX_OUTPUT_TOKENS = 1024
# A long description and a table are up to ~1,000 tokens more, plus reasoning: an answer
# cut off by the limit fails the schema and is billed anyway.
LONG_MAX_OUTPUT_TOKENS = 4096
# The figure's printed labels sent as context (long descriptions on): tick labels, axis
# titles, legend entries. Enough for a chart; the rest is cut.
MAX_LABEL_CHARS = 600
# Figure types that carry information by definition; "decorative" contradicts them.
INFORMATIVE_FIGURE_TYPES = frozenset({"chart", "diagram", "table_image"})
# Figure types that need a long description (the notice `long_description_missing`).
COMPLEX_FIGURE_TYPES = frozenset({"chart", "diagram"})
# Failed warning checks that do not change what the description says: the fix is still
# ACCEPTED (review "optional"). A caption copy is not one: it describes nothing. The
# second automated check's flag is one by policy: an optional tier only, never required
# in this version (its thresholds are not calibrated on human labels yet). A long
# description the model was unsure of is one, and so is a data table with values read
# from the image (`values_from_image`): it is not in the student file until a reviewer
# adds it, so it changes nothing a student reads (docs/design/content-features.md
# "Review tiers").
NOTE_CODES = frozenset(
    {
        "too_long",
        "redundant_prefix",
        "second_check_mismatch",
        "long_description_unsure",
        "values_from_image",
    }
)


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


class DataTableOutput(BaseModel):
    """A chart's values: column names with units, then one row per point or category."""

    model_config = ConfigDict(strict=True, extra="forbid")

    header: list[str] = Field(description='Column names with units, e.g. "Time (s)".')
    rows: list[list[str]] = Field(description="One row per point, one cell per column.")


class FigureLongOutput(FigureOutput):
    """`FigureOutput` plus the long-description fields (prompt `figure_long.md`)."""

    long_description: str = Field(
        description="Plain-text long description for complex figures; empty when not needed."
    )
    long_description_unsure: bool = Field(
        description="True when part of the figure could not be read clearly."
    )
    data_table: DataTableOutput | None = Field(
        description="Charts only: the values that can be read, else null."
    )
    values_printed: bool = Field(
        description="True only when every table value is printed as a number in the figure."
    )
    colour_only: bool = Field(
        description="True when series or parts are told apart by colour alone."
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


def figure_context(
    element: Element, context_text: str | None, *, labels: bool = False
) -> str | None:
    """Caption plus nearby text, for `VisionRequest.context_text` (fenced by the client).
    With `labels` (long descriptions on), also the text printed in the figure itself, so
    the model can read tick labels and values exactly."""
    caption = (element.caption or "").strip()
    nearby = (context_text or "").strip()
    printed = " ".join((element.text or "").split()) if labels else ""
    parts = []
    if caption:
        parts.append(f"Caption: {caption}")
    if nearby and nearby != caption:
        parts.append(f"Nearby text: {nearby}")
    if printed:
        parts.append(f"Labels in the figure: {printed[:MAX_LABEL_CHARS]}")
    return "\n\n".join(parts) or None


@dataclass(frozen=True)
class _Details:
    """What a long-prompt answer adds to the proposal (`FigureFix` fields)."""

    long_description: str | None = None
    data_table: TableGrid | None = None
    data_values_from_text: bool | None = None
    colour_only: bool = False


@dataclass
class _Candidate:
    output: FigureOutput | None
    calls: list[ModelCall]
    alt: str = ""
    checks: list[Check] = field(default_factory=list)
    details: _Details = field(default_factory=_Details)

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
    checks = check_alt(alt, decorative=False, caption=element.caption)
    if not isinstance(output, FigureLongOutput):
        return _Candidate(output, calls, alt, checks)
    details, detail_checks = _details(output, element)
    return _Candidate(output, calls, alt, [*checks, *detail_checks], details)


def _details(output: FigureLongOutput, element: Element) -> tuple[_Details, list[Check]]:
    """The long description, data table and colour flag of a (non-decorative) answer, and
    their checks: `figure.long_description` (a note when the model was unsure) and
    `figure.data_values` (values read from the image: a note, the table an optional
    extra)."""
    checks: list[Check] = []
    text = figure_details.clean_long_description(output.long_description)
    long_description = text if text and figure_details.long_description_ok(text) else None
    if long_description is not None:
        unsure = output.long_description_unsure
        checks.append(
            Check(
                name="figure.long_description",
                passed=not unsure,
                severity="warning",
                code="long_description_unsure" if unsure else None,
            )
        )
    grid = None
    if output.data_table is not None and output.figure_type == "chart":
        grid = figure_details.data_grid(output.data_table.header, output.data_table.rows)
    from_text = None
    if grid is not None:
        from_text = output.values_printed and figure_details.values_in_text(grid, element.text)
        checks.append(
            Check(
                name="figure.data_values",
                passed=from_text,
                severity="warning",
                code=None if from_text else "values_from_image",
            )
        )
    details = _Details(long_description, grid, from_text, output.colour_only)
    return details, checks


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
    def make(
        status: Status, reason: str | None = None, proposal: bool = False, review: Any = None
    ) -> Fix:
        figure = None
        if proposal and candidate.output is not None:
            details = candidate.details
            figure = FigureFix(
                figure_type=candidate.output.figure_type,
                alt=candidate.alt,
                decorative=candidate.output.decorative,
                long_description=details.long_description,
                data_table=details.data_table,
                data_values_from_text=details.data_values_from_text,
                colour_only=details.colour_only,
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
            review=review,  # None: from the status (waiting -> required)
        )

    output = candidate.output
    if output is None:
        outcome = calls[-1].outcome if calls else "skipped"
        return make(Status.FAILED, f"model_{outcome}")
    if output.confidence < settings.abstain_confidence:
        return make(Status.ABSTAINED, "low_confidence")
    if has_errors(candidate.checks):
        return make(Status.FAILED, "invalid_alt")
    failed = [c for c in candidate.checks if not c.passed]
    notes_only = all(c.severity == "warning" and c.code in NOTE_CODES for c in failed)
    if output.confidence >= _accept_bar(output, settings) and notes_only:
        return make(Status.ACCEPTED, proposal=True, review="optional" if failed else "none")
    return make(Status.NEEDS_REVIEW, proposal=True)


def second_check(
    fix: Fix, element: Element, png: bytes, *, client: VisionClient, settings: Settings
) -> Fix:
    """Ask the job's verifier about an accepted alt text (see the module docstring).

    Only an ACCEPTED, non-decorative figure with alt text is checked: anything else
    already needs a decision or has nothing to compare. Returns the fix with the answer
    recorded and, when it flags, a failed `figure.second_check` note and review
    "optional". Never raises (the verifier fails open).
    """
    verifier = verifier_of(client)
    mode = figure_check_mode(settings)
    figure = fix.figure
    if (
        verifier is None
        or mode == "off"
        or fix.status is not Status.ACCEPTED
        or figure is None
        or figure.decorative
        or not figure.alt
    ):
        return fix
    verdict = verifier.figure_alt(png, alt=figure.alt, caption=element.caption)
    score = figure_score(verdict.probabilities) if verdict.answered else None
    flagged = None if score is None else score < settings.decisions_figure_flag_below
    update: dict[str, Any] = {
        "verifications": [
            *fix.verifications,
            verdict.record(stage="final", mode=mode, flagged=flagged),
        ]
    }
    if flagged is not None and mode == "note":
        check = Check(
            name="figure.second_check",
            passed=not flagged,
            severity="warning",
            code="second_check_mismatch" if flagged else None,
        )
        update["checks"] = [*fix.checks, check]
        if flagged:
            update["review"] = "optional"
    return fix.model_copy(update=update)


def describe(
    element: Element,
    png: bytes,
    *,
    client: VisionClient,
    settings: Settings,
    context_text: str | None = None,
) -> Fix:
    """Alt text for one figure crop (and, with long descriptions on, the long
    description, data table and colour flag). Never raises for model or validation
    problems (they become FAILED/ABSTAINED fixes); raises BudgetExceeded when spend is
    refused."""
    options = run_options.current()
    exam = options.exam_mode
    long = options.long_descriptions and not exam  # values and trends are results
    prompt = load_prompt(EXAM_PROMPT_NAME if exam else LONG_PROMPT_NAME if long else PROMPT_NAME)
    schema: type[FigureOutput] = FigureLongOutput if long else FigureOutput
    tiny = is_tiny(element)
    context = figure_context(element, context_text, labels=long)

    def attempt(model: str, *, escalation: bool = False) -> _Candidate:
        request = VisionRequest(
            step="figure",
            model=model,
            prompt_version=prompt.version,
            instructions=prompt.text,
            images=[png],
            context_text=context,
            max_output_tokens=LONG_MAX_OUTPUT_TOKENS if long else MAX_OUTPUT_TOKENS,
            image_detail="low" if tiny else "high",
        )
        if escalation:
            output, calls = _call_once(client, request, schema)
        else:
            output, calls = _call(client, request, schema, settings)
        return _evaluate(output, calls, element)

    first_model = settings.cheap_model if tiny else settings.main_model
    candidate = attempt(first_model)
    calls = list(candidate.calls)
    if first_model != settings.main_model and _needs_escalation(candidate, settings):
        escalated = attempt(settings.main_model, escalation=True)
        calls += escalated.calls
        candidate = _prefer(escalated, candidate)
    fix = _fix(element, candidate, calls, settings)
    return second_check(fix, element, png, client=client, settings=settings)


# --------------------------------------------------------------------------- notices


def described_with_long_prompt(fix: Fix) -> bool:
    """The fix's proposal came from the long-description prompt (the option was on)."""
    return any(call.prompt_version.startswith(f"{LONG_PROMPT_NAME}-") for call in fix.calls)


def long_description_missing(fix: Fix) -> bool:
    """For the job page's notices (`pipeline.notices`): a chart or diagram described with
    long descriptions on, but without one (notice `long_description_missing`, optional).
    False for anything else, including figures described with the option off."""
    figure = fix.figure
    return (
        figure is not None
        and not figure.decorative
        and figure.figure_type in COMPLEX_FIGURE_TYPES
        and not figure.long_description
        and described_with_long_prompt(fix)
    )
