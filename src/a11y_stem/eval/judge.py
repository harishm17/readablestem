"""LLM judge for figure alt text: candidate vs the OpenStax reference alt and caption.

Why a judge: alt text has no exact-match metric. Two good descriptions of one graph can
share few words, so string overlap (BLEU, ROUGE) mostly measures phrasing. The judge
answers two narrow questions instead (does the candidate carry the key information; does
it state anything false) plus a 1-5 usefulness score. `eval calibrate` measures how far
those answers can be trusted against Harish's own labels before any number is published.

Design choices:
- Text only. The judge sees the candidate, the reference alt and the caption, not the
  image: the reference is the ground truth, and leaving the image out keeps each call
  cheap and keeps the judge from writing its own description to compare against.
- Blind. Nothing in the request says which system wrote the candidate (the bias probes
  in `calibration.py` even feed it the reference itself as a candidate).
- Main model (`settings.main_model`, gpt-6.1-sol) with no fallback: a judge that silently
  switched models would change the measuring instrument mid-run. A failed call is
  recorded and the item is left unjudged.
- All three texts are untrusted data and travel in `VisionRequest.context_text` (fenced
  by the client); the instructions are the fixed prompt `models/prompts/judge_figure.md`.

Pass rule: `conveys_key_information and not factual_error`. The score is reported
separately (mean score) and used by the length-bias probe.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from a11y_stem.config import Settings
from a11y_stem.core.types import ModelCall
from a11y_stem.models.base import VisionClient, VisionRequest
from a11y_stem.models.prompts import load_prompt

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


def _field(name: str, text: str | None) -> str:
    """One tagged field. Our tag names are removed from the text so a candidate cannot
    close its own field and pose as the reference."""
    cleaned = text or ""
    for tag in MAX_CHARS:
        cleaned = cleaned.replace(f"<{tag}>", "").replace(f"</{tag}>", "")
    cleaned = cleaned.strip()
    limit = MAX_CHARS[name]
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
    candidate: str | None, reference: str, caption: str | None, *, settings: Settings
) -> VisionRequest:
    prompt = load_prompt(JUDGE_PROMPT)
    return VisionRequest(
        step="judge",
        model=settings.main_model,
        prompt_version=prompt.version,
        instructions=prompt.text,
        images=[],
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
) -> Judgement:
    """Grade one candidate. Raises BudgetExceeded (from the client) when spend is refused."""
    request = judge_request(candidate, reference, caption, settings=settings)
    verdict, call = client.run(request, JudgeVerdict)
    return Judgement(verdict=verdict, call=call)


def judge_prompt_version() -> str:
    return load_prompt(JUDGE_PROMPT).version
