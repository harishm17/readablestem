"""Image pages: a second reading of lines that hold handwritten maths (docs/eval-handwriting.md).

The page reader is rarely sure of a handwritten line (85% of the blocks of the handwriting
held-out set were `medium` or `low`), and a block's `medium` says little: 62% of the formulas in
blocks it called `medium` were right. A line cut out of the page and read alone is a
different, independent look at the same ink: its own confidence separated the formulas it was
sure of (88% right) from the rest (48% right), and where it and the page reading disagreed only
14% of the page readings were right.

This module cuts out each `medium` block that holds a formula, asks the model for the line's
words and formulas, several lines to a request (`CROPS_PER_REQUEST`), and compares the answer
with the page reading: the formulas by their symbols (`page_math.mathml_stream`, so notation such as
`\\bar` and `\\overline` does not count as a difference) and the words by their letters. The
verdict (`Verdict`) goes to `image_page.build`, which records it as a check on the block's fix
and, only when `Settings.image_pages_accept_agreed` is on, lets a block whose two readings agree,
each sure of itself, be an optional note instead of a required decision.

Fail-safe: a refused, invalid or over-budget answer is no verdict and the block stays as the page
reader left it. A second reading adds a cost (one request a few lines, `$0.015` a handwriting
page) and never a decision.
"""

from __future__ import annotations

import io
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from readablestem.config import Settings
from readablestem.core.budget import BudgetExceeded
from readablestem.core.types import ModelCall
from readablestem.models.base import VisionClient, VisionRequest
from readablestem.models.prompts import load_prompt
from readablestem.pipeline import equation_step, page_math

log = logging.getLogger(__name__)

PROMPT_NAME = "image_page_crops"
PROMPT_VERSION = load_prompt(PROMPT_NAME).version
CROPS_PER_REQUEST = 8
MAX_CROPS_PER_PAGE = 40
MAX_OUTPUT_TOKENS = 3000
DEFAULT_EFFORT = "low"
# A cut-out is the block's box grown by this many pixels on each side (the ink trimming of
# `image_page.Ink.region` has already happened), and never smaller than this many pixels tall.
CROP_MARGIN_PX = 14
MIN_CROP_HEIGHT_PX = 48

Confidence = Literal["high", "medium", "low"]


class CropRun(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    kind: Literal["text", "math"]
    value: str = Field(description="The words as written, or the formula as LaTeX.")


class CropReading(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    index: int = Field(description="The picture's number, counting from 0.")
    confidence: Confidence
    runs: list[CropRun] = Field(description="The line's words and formulas in reading order.")


class CropReadings(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    readings: list[CropReading]


@dataclass(frozen=True)
class Verdict:
    """What the second reading of one block says about the page reading of it."""

    agree: bool  # every formula and every word is the same in both
    confidence: Confidence  # the second reading's own
    latex: tuple[str, ...] = ()  # the second reading's formulas, kept for the record


@dataclass
class Crop:
    block: int  # index in `ImagePageOutput.blocks`
    png: bytes
    formulas: list[str]  # the page reading's formulas
    words: str  # the page reading's words


@dataclass
class CheckResult:
    verdicts: dict[int, Verdict] = field(default_factory=dict)
    calls: list[ModelCall] = field(default_factory=list)
    stopped: BudgetExceeded | None = None


def build_request(settings: Settings, pngs: Sequence[bytes]) -> VisionRequest:
    prompt = load_prompt(PROMPT_NAME)
    return VisionRequest(
        step="image_page",
        model=settings.image_pages_model or settings.main_model,
        prompt_version=prompt.version,
        instructions=prompt.text,
        images=list(pngs),
        max_output_tokens=MAX_OUTPUT_TOKENS,
        image_detail="high",
        effort=settings.anthropic_effort_image_pages or DEFAULT_EFFORT,
        timeout_s=max(settings.model_timeout_s, settings.image_pages_timeout_s),
    )


# ------------------------------------------------------------------------ comparison


def _letters(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _stream(latex: str) -> tuple[str, ...] | None:
    cleaned = equation_step.strip_trailing_punctuation(equation_step.clean_latex(latex))
    if not cleaned.strip():
        return None
    mathml, _ = equation_step._convert(cleaned, "inline")
    stream = page_math.mathml_stream(mathml) if mathml else None
    return tuple(stream) if stream else None


def compare(crop: Crop, reading: CropReading) -> Verdict:
    """Do the two readings of a block say the same? Formulas by their symbols, in order and
    in the same number; words by their letters. A formula that does not convert is no
    agreement."""
    formulas = [r.value for r in reading.runs if r.kind == "math" and r.value.strip()]
    words = "".join(r.value for r in reading.runs if r.kind == "text")
    agree = len(formulas) == len(crop.formulas) and _letters(words) == _letters(crop.words)
    if agree:
        for ours, theirs in zip(crop.formulas, formulas, strict=True):
            a, b = _stream(ours), _stream(theirs)
            if a is None or b is None or a != b:
                agree = False
                break
    return Verdict(agree=agree, confidence=reading.confidence, latex=tuple(formulas))


# ----------------------------------------------------------------------------- the pass


def cut(png: bytes, box: tuple[int, int, int, int], margin: int = CROP_MARGIN_PX) -> bytes | None:
    """The picture of `box` (pixels of the page picture `png`) with a margin of `margin`
    pixels, as PNG; None when it cannot be made."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(png)) as image:
            image = image.convert("RGB")
            x0, y0, x1, y1 = box
            region = (
                max(0, x0 - margin),
                max(0, y0 - margin),
                min(image.width, x1 + margin),
                min(image.height, y1 + margin),
            )
            if region[2] - region[0] < 2 or region[3] - region[1] < 2:
                return None
            piece = image.crop(region)
            if piece.height < MIN_CROP_HEIGHT_PX:  # a very thin line: pad it with paper
                padded = Image.new("RGB", (piece.width, MIN_CROP_HEIGHT_PX), (255, 255, 255))
                padded.paste(piece, (0, (MIN_CROP_HEIGHT_PX - piece.height) // 2))
                piece = padded
            out = io.BytesIO()
            piece.save(out, format="PNG")
            return out.getvalue()
    except Exception as exc:  # a picture that cannot be cut only costs the check
        log.warning("image page crop failed: %s", type(exc).__name__)
        return None


def check(client: VisionClient, settings: Settings, crops: Sequence[Crop]) -> CheckResult:
    """Ask for the second reading of `crops`, `CROPS_PER_REQUEST` to a request. Never raises
    for a bad answer; a budget refusal stops the pass (the verdicts so far stay)."""
    result = CheckResult()
    for start in range(0, min(len(crops), MAX_CROPS_PER_PAGE), CROPS_PER_REQUEST):
        chunk = list(crops[start : min(start + CROPS_PER_REQUEST, MAX_CROPS_PER_PAGE)])
        try:
            parsed, call = client.run(build_request(settings, [c.png for c in chunk]), CropReadings)
        except BudgetExceeded as exc:
            result.stopped = exc
            break
        except Exception as exc:  # the second reading is never worth the page
            log.warning("image page second reading failed: %s", type(exc).__name__)
            continue
        result.calls.append(call)
        if parsed is None:
            continue
        seen: set[int] = set()
        for reading in parsed.readings:
            if not 0 <= reading.index < len(chunk) or reading.index in seen:
                continue
            seen.add(reading.index)
            crop = chunk[reading.index]
            result.verdicts[crop.block] = compare(crop, reading)
    return result
