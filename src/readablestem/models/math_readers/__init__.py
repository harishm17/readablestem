"""Reader B: a second reading of a handwritten expression, behind one small protocol.

`MathReader.read(ReadRequest(image_png))` returns LaTeX candidates with the confidences
the reader reported. Adapters: Claude (another model, its own prompt, one high-resolution
crop: as decorrelated from the page read as a model reading can be made), OpenAI, and a
local one (TexTeller on CPU: no key, no network, nothing leaves the process).
`build_readers` makes the ones the settings turn on; all are off by default and nothing
in the pipeline calls them yet. See `docs/math-readers.md`.
"""

from __future__ import annotations

from readablestem.config import Settings
from readablestem.core.budget import JobBudget
from readablestem.models.math_readers.base import (
    Candidate,
    MathReader,
    ReaderCall,
    ReadRequest,
    ReadResult,
)
from readablestem.models.math_readers.claude_reader import ClaudeReader
from readablestem.models.math_readers.engine import ReaderBudget
from readablestem.models.math_readers.local_reader import LocalReader
from readablestem.models.math_readers.openai_vision import OpenAIReader
from readablestem.models.math_readers.transport import Transport

__all__ = [
    "Candidate",
    "MathReader",
    "ReadRequest",
    "ReadResult",
    "ReaderCall",
    "build_readers",
]


def build_readers(
    settings: Settings, budget: JobBudget, *, transport: Transport | None = None
) -> dict[str, MathReader]:
    """The readers `settings` turns on, by name (empty with the defaults), sharing a dollar
    sub-cap inside the job's budget; a reader raises `BudgetExceeded("reader_usd")` once the
    sub-cap is spent, which the caller treats as no second reading. Raises
    `ConfigError` for an enabled reader without its key and `UnknownModelError` for one
    whose model has no price. `transport` replaces HTTP for every reader (tests)."""
    readers: dict[str, MathReader] = {}
    # One sub-cap shared by the readers of this job (`ReaderBudget`).
    capped = ReaderBudget(budget, settings.math_reader_max_usd_per_job)
    if settings.math_reader_claude_enabled:
        readers["claude"] = ClaudeReader(settings, capped, transport=transport)
    if settings.math_reader_openai_enabled:
        readers["openai"] = OpenAIReader(settings, capped, transport=transport)
    if settings.math_reader_local_enabled:
        # No key, no network and no dollars: it holds nothing under the budget.
        readers["local"] = LocalReader.from_settings(settings)
    return readers
