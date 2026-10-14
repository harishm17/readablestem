"""The second automated check on Claude (T2-20): a `Verifier` that asks a different Claude
model, with a different prompt, whether an alt text or a LaTeX transcription matches the
image.

Why it exists: the Decisions API check (`models.verifier.DecisionsVerifier`) is OpenAI's, so
with MODEL_PROVIDER=anthropic nothing looked at the first pass's alt text or equations a
second time. This is the same `Verifier` protocol, so the steps, the aggregates, the eval
metrics and the review screen need no change: a verdict is the same content-free
`VerifierVerdict` of answer codes.

What makes it independent of the first pass:

- a different model: `Settings.claude_check_model` (claude-sonnet-5-5), not the cheap or
  the main model that read the page (claude-haiku-5-5 / claude-opus-5-5);
- a different prompt: the first pass is asked to write (alt text, LaTeX) and to say how sure
  it is; this one is asked to audit someone else's text against the image, in a rubric of
  its own (`prompts/check_figure.md`, `check_equation.md`), without seeing the first pass's
  confidence or reasoning;
- a different output: booleans and a verdict code, never a confidence it could echo.

What it does not give: probabilities. Claude returns no calibrated numbers, so the answer
codes are 1.0 and 0.0 ("ok" and "factual_error" for a figure; "match", "cosmetic",
"meaning_changing" for an equation), which the existing score functions read as they read
the Decisions API's (`figure_score` is then 0 or 1, and `decisions_figure_flag_below` and
`decisions_equation_flag_at` still decide "flagged"). A binary answer from a model is a
vote, not a measurement: read the agreement rate, not the number.

Cost and safety, as for every model request in this codebase:

- the request reserves its worst case first, through the job's `JobBudget`, in dollars only
  (`count_calls=False` on its client): a check is held against the job's and the daily caps
  but is not one of the job's model calls, so it cannot use up the calls a later element
  needs (the same rule as the Decisions check, `core.budget`);
- one send, no retries, a short timeout (`claude_check_timeout_s`): the check is optional and
  fail-open, a failure costs the caller only the note it would have added;
- the caption, alt text and LaTeX are untrusted data: they travel only in `context_text`
  (fenced by the client), after `models.verifier.fence_data`;
- implementations never raise: a refused reservation, a provider error, a timeout or a
  refusal comes back as a verdict whose `reason_code` says why there is no answer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from a11y_stem.config import ConfigError, Settings
from a11y_stem.core.budget import BudgetExceeded, JobBudget
from a11y_stem.models.base import VisionClient, VisionRequest
from a11y_stem.models.prompts import load_prompt
from a11y_stem.models.verifier import (
    COSMETIC,
    EQUATION_CHECK,
    FIGURE_CHECK,
    MATCH,
    MEANING_CHANGING,
    P_FACTUAL_ERROR,
    P_OK,
    FakeVerifier,
    Verifier,
    VerifierVerdict,
    fence_data,
)
from a11y_stem.observability.logging import log_fields

if TYPE_CHECKING:
    from a11y_stem.core.types import ModelCall

log = logging.getLogger(__name__)

FIGURE_PROMPT = "check_figure"
EQUATION_PROMPT = "check_equation"
# A verdict is a few short fields; thinking has its own allowance on top (pricing).
MAX_OUTPUT_TOKENS = 256
CAPTION_LIMIT = 600
ALT_LIMIT = 900
LATEX_LIMIT = 1200

FigureProblem = Literal[
    "none",
    "wrong_value",
    "wrong_label",
    "wrong_direction",
    "wrong_object",
    "generic",
    "leaves_out_key_information",
    "repeats_caption",
    "other",
]
EquationDifference = Literal[
    "none",
    "sign",
    "digit",
    "exponent",
    "subscript",
    "greek_letter",
    "symbol",
    "missing_or_extra_term",
    "structure",
    "bold_or_style",
    "other",
]


class FigureAudit(BaseModel):
    """Structured output of the figure audit (strict, no extra keys)."""

    model_config = ConfigDict(extra="forbid")

    conveys_key_information: bool
    contradicts_image: bool
    problem: FigureProblem


class EquationAudit(BaseModel):
    """Structured output of the equation audit (strict, no extra keys)."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["match", "cosmetic", "meaning_changing"]
    difference: EquationDifference


def figure_probabilities(audit: FigureAudit) -> dict[str, float]:
    """The audit as the Decisions check's answer codes (1.0 or 0.0)."""
    return {
        P_OK: 1.0 if audit.conveys_key_information else 0.0,
        P_FACTUAL_ERROR: 1.0 if audit.contradicts_image else 0.0,
    }


def equation_probabilities(audit: EquationAudit) -> dict[str, float]:
    return {
        code: 1.0 if audit.verdict == code else 0.0 for code in (MATCH, COSMETIC, MEANING_CHANGING)
    }


def figure_version() -> str:
    return load_prompt(FIGURE_PROMPT).version


def equation_version() -> str:
    return load_prompt(EQUATION_PROMPT).version


def figure_request(model: str, png: bytes, *, alt: str, caption: str | None) -> VisionRequest:
    """The audit request: the image, then the caption and the alt text as fenced data."""
    prompt = load_prompt(FIGURE_PROMPT)
    text = (
        f"Caption printed with the figure:\n{fence_data(caption, CAPTION_LIMIT) or '(none)'}\n\n"
        f"Alt text to audit:\n{fence_data(alt, ALT_LIMIT)}"
    )
    return VisionRequest(
        step="check",
        model=model,
        prompt_version=prompt.version,
        instructions=prompt.text,
        images=[png],
        context_text=text,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


def equation_request(model: str, png: bytes, *, latex: str) -> VisionRequest:
    prompt = load_prompt(EQUATION_PROMPT)
    return VisionRequest(
        step="check",
        model=model,
        prompt_version=prompt.version,
        instructions=prompt.text,
        images=[png],
        context_text=f"Proposed LaTeX:\n{fence_data(latex, LATEX_LIMIT)}",
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


class ClaudeVerifier:
    """`Verifier` on a Claude model through a `VisionClient` that charges dollars only.

    `client` is that client (built by `make_claude_verifier`; tests give a fake). Never
    raises (fail-open)."""

    def __init__(self, client: VisionClient, model: str) -> None:
        self._client = client
        self.model = model

    def figure_alt(self, png: bytes, *, alt: str, caption: str | None) -> VerifierVerdict:
        return self._ask(
            FIGURE_CHECK,
            figure_version(),
            lambda: figure_request(self.model, png, alt=alt, caption=caption),
            FigureAudit,
            figure_probabilities,
        )

    def equation_match(self, png: bytes, *, latex: str) -> VerifierVerdict:
        return self._ask(
            EQUATION_CHECK,
            equation_version(),
            lambda: equation_request(self.model, png, latex=latex),
            EquationAudit,
            equation_probabilities,
        )

    def _ask(
        self,
        check: str,
        version: str,
        build: Callable[[], VisionRequest],
        schema: type[BaseModel],
        convert: Callable[..., Mapping[str, float]],
    ) -> VerifierVerdict:
        from a11y_stem.models.pricing import UnknownModelError

        try:
            parsed, call = self._client.run(build(), schema)
        except BudgetExceeded:  # nothing was sent
            return VerifierVerdict(check, {}, "budget_exceeded", self.model, version)
        except (ConfigError, UnknownModelError, ValueError) as exc:
            _log_unavailable(check, "skipped", exc)
            return VerifierVerdict(check, {}, "skipped", self.model, version)
        except Exception as exc:  # fail-open: the check never breaks a conversion
            _log_unavailable(check, "api_error", exc)
            return VerifierVerdict(check, {}, "api_error", self.model, version)
        return _verdict(check, version, self.model, parsed, call, convert)


def _verdict(
    check: str,
    version: str,
    model: str,
    parsed: BaseModel | None,
    call: ModelCall,
    convert: Callable[..., Mapping[str, float]],
) -> VerifierVerdict:
    answered = parsed is not None and call.outcome == "ok"
    return VerifierVerdict(
        check,
        dict(convert(parsed)) if answered else {},
        "ok" if answered else (call.outcome if call.outcome != "ok" else "invalid_output"),
        model,
        version,
        call.input_tokens,
        call.cost_usd,
        call.latency_ms,
    )


def _log_unavailable(check: str, reason: str, exc: Exception) -> None:
    log_fields(
        log,
        logging.WARNING,
        "verifier_unavailable",
        check=check,
        reason=reason,
        provider="anthropic",
        error_type=type(exc).__name__,
    )


def make_claude_verifier(settings: Settings, budget: JobBudget, *, fake: bool = False) -> Verifier:
    """The Claude check for one job, charging `budget` in dollars only. `fake=True` gives
    the offline `FakeVerifier` (still charging the budget; it names the Claude check model
    on its verdicts)."""
    model = settings.claude_check_model
    if fake:
        return FakeVerifier(budget=budget, reported_model=model)
    from a11y_stem.models.anthropic_client import AnthropicVisionClient

    own = settings.model_copy(
        update={"model_timeout_s": settings.claude_check_timeout_s, "model_max_retries": 0}
    )
    return ClaudeVerifier(AnthropicVisionClient(own, budget, count_calls=False), model)
