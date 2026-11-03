"""One small contract for "reader B": a second reading of one handwritten expression, made
to differ from the page pass's reading so the two can be compared.

A reader gets one expression crop (PNG) and returns LaTeX candidates, best first, each with
the confidence the reader reported (`None` when it reports none). It never raises for
provider errors, refusals, timeouts or unreadable output: those come back as an empty
result whose `ReaderCall.outcome` says why (`core.types.Outcome` codes). It raises
`core.budget.BudgetExceeded` when the spend reservation is refused, before anything is
sent, and `ValueError` for a request that could only fail at the provider.

Confidences are what the model reports about itself. They are not calibrated and not
comparable across readers: a ranking signal, never shown to a reader of the output as a
probability.

Used by `pipeline.image_page_reconcile` and nowhere else. Settings: `Settings.math_reader_*`;
the factory is `models.math_readers.build_readers`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Protocol

from readablestem.core.types import Outcome

PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
MAX_IMAGE_BYTES: Final = 4 * 1024 * 1024
MAX_LATEX_CHARS: Final = 2000
MAX_CANDIDATES: Final = 5

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass(frozen=True)
class ReadRequest:
    """What to read: one expression, as a PNG crop. Cut it tight and render it large (a
    high-resolution crop of the one expression, no page around it)."""

    image_png: bytes

    def __post_init__(self) -> None:
        if not self.image_png.startswith(PNG_SIGNATURE):
            raise ValueError("image is not PNG data")
        if len(self.image_png) > MAX_IMAGE_BYTES:
            raise ValueError("image is too large")


@dataclass(frozen=True)
class Candidate:
    latex: str
    confidence: float | None = None


@dataclass(frozen=True)
class ReaderCall:
    """One logical read (every send it took). Content-free: codes, counts and dollars."""

    reader: str
    model: str
    outcome: Outcome = "ok"
    cost_usd: float = 0.0
    latency_ms: int = 0
    sends: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ReadResult:
    candidates: list[Candidate] = field(default_factory=list)
    call: ReaderCall = field(default_factory=lambda: ReaderCall("", ""))


class MathReader(Protocol):
    """A reader B. `name` is stable ("claude", "openai"); `model` is the model id."""

    name: str
    model: str

    def read(self, request: ReadRequest) -> ReadResult: ...


def clean_latex(raw: object) -> str | None:
    """A candidate string made safe to carry: delimiters and control characters removed,
    bounded. None when nothing is left or it is not text."""
    if not isinstance(raw, str):
        return None
    text = _CONTROL.sub("", raw).strip()
    for left, right in (("\\(", "\\)"), ("\\[", "\\]"), ("$$", "$$"), ("$", "$")):
        if len(text) >= len(left) + len(right) and text.startswith(left) and text.endswith(right):
            text = text[len(left) : len(text) - len(right)].strip()
            break
    if not text or len(text) > MAX_LATEX_CHARS:
        return None
    return text


def clamp_confidence(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    value = float(raw)
    if value != value:  # NaN
        return None
    return min(1.0, max(0.0, value))


def unique(candidates: Sequence[Candidate]) -> list[Candidate]:
    """In order, one per distinct LaTeX string, at most `MAX_CANDIDATES`."""
    seen: set[str] = set()
    out: list[Candidate] = []
    for candidate in candidates:
        if candidate.latex not in seen:
            seen.add(candidate.latex)
            out.append(candidate)
    return out[:MAX_CANDIDATES]
