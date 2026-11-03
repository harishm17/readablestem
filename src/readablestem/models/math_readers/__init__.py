"""Reader B: a second reading of a handwritten expression, behind one small protocol.

`MathReader.read(ReadRequest(image_png))` returns LaTeX candidates with the confidences
the reader reported. Adapters: Claude (another model, its own prompt, one high-resolution
crop: as decorrelated from the page read as a model reading can be made) and OpenAI.
`build_readers` makes the ones the settings turn on; both are off by default and nothing
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
    """The readers `settings` turns on, by name (empty with the defaults). Raises
    `ConfigError` for an enabled reader without its key and `UnknownModelError` for one
    whose model has no price. `transport` replaces HTTP for every reader (tests)."""
    readers: dict[str, MathReader] = {}
    if settings.math_reader_claude_enabled:
        readers["claude"] = ClaudeReader(settings, budget, transport=transport)
    if settings.math_reader_openai_enabled:
        readers["openai"] = OpenAIReader(settings, budget, transport=transport)
    return readers
