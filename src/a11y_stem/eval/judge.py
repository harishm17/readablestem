"""LLM judge for figure alt text: candidate vs the OpenStax reference alt and caption.

Why a judge: alt text has no exact-match metric. Two good descriptions of one graph can
share few words, so string overlap (BLEU, ROUGE) mostly measures phrasing. The judge
answers two narrow questions instead (does the candidate carry the key information; does
it state anything false) plus a 1-5 usefulness score. `eval calibrate` measures how far
those answers can be trusted against Harish's own labels before any number is published.

Design choices:
- The image, when given. The figures eval passes the figure's PNG (`image=`), and the
  prompt then makes the image the ground truth: OpenStax alt texts vary from 39 to 980
  characters, and some leave out what a figure shows. Without an image the reference is
  the ground truth (the text-only mode). The two modes are different instruments: the
  gate refuses to compare reports graded by different judge prompts, and the bias
  probes record which mode they ran in (`judge_mode`).
- Blind. Nothing in the request says which system wrote the candidate (the bias probes
  in `calibration.py` even feed it the reference itself as a candidate).
- Main model (`settings.main_model`, gpt-6.1-sol) with no fallback: a judge that silently
  switched models would change the measuring instrument mid-run. A failed call is
  recorded and the item is left unjudged.
- Which provider and model judge is a choice of the run (`make_judge`, the
  `--judge-provider` / `--judge-model` options): by default the run's own provider, and
  then the run's main model on OpenAI, but always `claude-opus-5-5` on Anthropic, so the
  instrument stays the same while the run's main model changes. The judge has its own
  client (same budget) and, on Anthropic, the server-side refusal fallback is off for it.
  The prompt is the same for both providers; the report records the judge's provider and
  model.
- All three texts are untrusted data and travel in `VisionRequest.context_text` (fenced
  by the client); the instructions are the fixed prompt `models/prompts/judge_figure.md`.

Pass rule: `conveys_key_information and not factual_error`. The score is reported
separately (mean score) and used by the length-bias probe.

Long descriptions and chart data tables (FEATURE_LONG_DESCRIPTIONS) have a rubric of
their own, `judge_long` (prompt `judge_long_description.md`, always with the image):
accurate, complete, no invented values, and the table's values within tolerance. It is
not calibrated against human labels yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from a11y_stem.config import ANTHROPIC_MAIN_MODEL, Provider, Settings, with_provider
from a11y_stem.core.types import ModelCall, TableGrid
from a11y_stem.models.base import VisionClient, VisionRequest
from a11y_stem.models.prompts import load_prompt

if TYPE_CHECKING:
    from a11y_stem.core.budget import JobBudget

JUDGE_PROMPT = "judge_figure"
# Per-field caps keep the whole data block under `fence_untrusted`'s default 2000-character
# limit, so the client never cuts it blindly. Sized from the sample gold set: OpenStax alt
# text runs up to ~1000 characters (2 of 13 figures exceed 800), a pipeline candidate is
# short alt text, and the caption is secondary context. A cut field ends in " [cut]".
MAX_CHARS = {"candidate": 900, "reference": 800, "caption": 200}
MAX_OUTPUT_TOKENS = 1024  # reasoning tokens count as output on the main model


class JudgeVerdict(BaseModel):
    """Structured output schema for the judge."""

    model_config = ConfigDict(extra="forbid")

    conveys_key_information: bool
    factual_error: bool
    score: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0.0, le=1.0)

    @property
    def passed(self) -> bool:
        return self.conveys_key_information and not self.factual_error


@dataclass(frozen=True)
class Judgement:
    """One judge call: the verdict (None when the call failed) and its content-free record."""

    verdict: JudgeVerdict | None
    call: ModelCall

    @property
    def passed(self) -> bool | None:
        return None if self.verdict is None else self.verdict.passed


def _field(name: str, text: str | None, limits: dict[str, int] = MAX_CHARS) -> str:
    """One tagged field. Our tag names are removed from the text so a candidate cannot
    close its own field and pose as the reference. Removal repeats until nothing
    changes: one pass would turn "</cand</candidate>idate>" back into a closing tag."""
    cleaned = text or ""
    previous = None
    while cleaned != previous:
        previous = cleaned
        for tag in limits:
            cleaned = cleaned.replace(f"<{tag}>", "").replace(f"</{tag}>", "")
    cleaned = cleaned.strip()
    limit = limits[name]
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + " [cut]"
    return f"<{name}>\n{cleaned}\n</{name}>"


def judge_context(candidate: str | None, reference: str, caption: str | None) -> str:
    """The data block the judge grades. Every text is capped and stripped of our tags."""
    return "\n".join(
        [
            _field("candidate", candidate),
            _field("reference", reference),
            _field("caption", caption),
        ]
    )


def judge_request(
    candidate: str | None,
    reference: str,
    caption: str | None,
    *,
    settings: Settings,
    image: bytes | None = None,
) -> VisionRequest:
    """The judge sees the figure when `image` is given (PNG): the first real eval run
    showed a text-only judge marking correct details absent from the reference, and a
    case where the reference itself was wrong, as factual errors."""
    prompt = load_prompt(JUDGE_PROMPT)
    return VisionRequest(
        step="judge",
        model=settings.main_model,
        prompt_version=prompt.version,
        instructions=prompt.text,
        images=[image] if image else [],
        context_text=judge_context(candidate, reference, caption),
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


def judge_alt(
    candidate: str | None,
    reference: str,
    caption: str | None,
    *,
    client: VisionClient,
    settings: Settings,
    image: bytes | None = None,
) -> Judgement:
    """Grade one candidate. Raises BudgetExceeded (from the client) when spend is refused."""
    request = judge_request(candidate, reference, caption, settings=settings, image=image)
    verdict, call = client.run(request, JudgeVerdict)
    return Judgement(verdict=verdict, call=call)


def judge_prompt_version() -> str:
    return load_prompt(JUDGE_PROMPT).version


# ===================================================================== long descriptions

# A separate rubric (prompt `judge_long_description.md`) for what the figure step writes
# with long descriptions on: the long description (accurate, complete, no invented
# values) and the chart data table (every value within tolerance of the figure). Same
# instrument rules as above: the image is the ground truth, the main model with no
# fallback, every text fenced as data, blind. Pass rule: `accurate and complete and not
# invented_values`; a table passes when `table_values_ok`.
LONG_JUDGE_PROMPT = "judge_long_description"
# Caps that keep the whole block under the fence's 2,000 characters (the figure prompt
# asks for at most 1,200 characters; a cut field ends in " [cut]" and the rubric says
# not to count a cut end as missing). The table is the chart's values, one row a line.
LONG_MAX_CHARS = {"long_description": 1000, "data_table": 400, "reference": 250, "caption": 150}
LONG_MAX_OUTPUT_TOKENS = 1536


class LongJudgeVerdict(BaseModel):
    """Structured output schema for the long-description judge."""

    model_config = ConfigDict(extra="forbid")

    accurate: bool
    complete: bool
    invented_values: bool
    score: int = Field(ge=1, le=5)
    table_values_ok: bool | None  # None: there was no data table
    table_wrong_values: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)

    @property
    def passed(self) -> bool:
        return self.accurate and self.complete and not self.invented_values


@dataclass(frozen=True)
class LongJudgement:
    verdict: LongJudgeVerdict | None
    call: ModelCall


def table_text(grid: TableGrid | None) -> str:
    """A data table as plain rows for the judge: cells separated by " | ", the header
    row first. Empty when there is no table."""
    if grid is None:
        return ""
    rows: list[list[str]] = [[""] * grid.cols for _ in range(grid.rows)]
    for cell in grid.cells:
        if cell.row < grid.rows and cell.col < grid.cols:
            rows[cell.row][cell.col] = " ".join(cell.text.split())
    return "\n".join(" | ".join(row) for row in rows)


def long_judge_context(
    long_description: str | None, table: str, reference: str, caption: str | None
) -> str:
    """The data block the long-description judge grades, each text capped and stripped
    of the tags of both judges."""
    limits = MAX_CHARS | LONG_MAX_CHARS  # every tag stripped; this judge's caps
    return "\n".join(
        [
            _field("long_description", long_description, limits),
            _field("data_table", table, limits),
            _field("reference", reference, limits),
            _field("caption", caption, limits),
        ]
    )


def long_judge_request(
    long_description: str | None,
    table: TableGrid | None,
    reference: str,
    caption: str | None,
    *,
    settings: Settings,
    image: bytes,
) -> VisionRequest:
    prompt = load_prompt(LONG_JUDGE_PROMPT)
    return VisionRequest(
        step="judge",
        model=settings.main_model,
        prompt_version=prompt.version,
        instructions=prompt.text,
        images=[image],
        context_text=long_judge_context(long_description, table_text(table), reference, caption),
        max_output_tokens=LONG_MAX_OUTPUT_TOKENS,
    )


def judge_long(
    long_description: str | None,
    table: TableGrid | None,
    reference: str,
    caption: str | None,
    *,
    client: VisionClient,
    settings: Settings,
    image: bytes,
) -> LongJudgement:
    """Grade one long description and data table against the figure image. Raises
    BudgetExceeded (from the client) when spend is refused."""
    request = long_judge_request(
        long_description, table, reference, caption, settings=settings, image=image
    )
    verdict, call = client.run(request, LongJudgeVerdict)
    return LongJudgement(verdict=verdict, call=call)


def long_judge_prompt_version() -> str:
    return load_prompt(LONG_JUDGE_PROMPT).version


# ===================================================================== who judges


@dataclass(frozen=True)
class JudgeSetup:
    """The instrument of a run: the client that sends judge calls and the settings whose
    `main_model` they name. `provider` and `model` are what reports record."""

    client: VisionClient
    settings: Settings

    @property
    def provider(self) -> str:
        return self.settings.model_provider

    @property
    def model(self) -> str:
        return self.settings.main_model

    def describe(self) -> dict[str, str]:
        return {"provider": self.provider, "model": self.model}


def judge_settings(
    settings: Settings, provider: Provider | None = None, model: str | None = None
) -> Settings:
    """The settings the judge runs with. `provider` None: the run's. Model None: the run's
    main model on OpenAI (unchanged), `claude-opus-5-5` on Anthropic (not the run's main
    model, which may be a Sonnet being compared). On Anthropic the refusal fallback is
    off, so the judge never changes model inside a call. Raises pydantic's
    `ValidationError` for a Claude model without a price."""
    chosen = provider or settings.model_provider
    if chosen == "anthropic":
        base = with_provider(settings, "anthropic", main_model=model or ANTHROPIC_MAIN_MODEL)
        return base.model_copy(update={"anthropic_refusal_fallback": False})
    if chosen == settings.model_provider and model is None:
        return settings
    return with_provider(settings, chosen, main_model=model)


def make_judge(
    settings: Settings,
    budget: JobBudget,
    *,
    run_client: VisionClient | None,
    provider: Provider | None = None,
    model: str | None = None,
    fake: bool = False,
) -> JudgeSetup:
    """The judge for a run. Reuses the run's client when the judge settings are the run's
    (OpenAI, nothing chosen); otherwise builds the judge's own client on the same
    `budget`, so one cap covers the run and its judge. `run_client` None: there is no run
    (the invented-content judge), so the judge always gets its own. `fake` gives an
    offline `FakeVisionClient`. Raises `ConfigError` when the judge provider's key is
    missing, now and not on the first call."""
    chosen = judge_settings(settings, provider, model)
    if fake:
        from a11y_stem.models.fake import FakeVisionClient

        return JudgeSetup(run_client or FakeVisionClient(budget=budget), chosen)
    if run_client is not None and chosen == settings:
        return JudgeSetup(run_client, chosen)
    from a11y_stem.models import make_live_client

    return JudgeSetup(make_live_client(chosen, budget, second_check=False), chosen)
