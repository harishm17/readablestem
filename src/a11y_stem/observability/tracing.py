"""Tracing (WP6 implements). Off by default; inputs masked when on.

`make_tracer(settings)` returns `NoopTracer` unless TRACING_ENABLED and Langfuse keys are
set. Spans carry ids, model names, token counts, costs, latencies and category codes;
never document text, images, LaTeX or alt text.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, Protocol


class Tracer(Protocol):
    def span(self, name: str, **attrs: Any) -> Any:  # context manager
        ...

    def flush(self) -> None: ...


class NoopTracer:
    @contextmanager
    def span(self, name: str, **attrs: Any) -> Generator[None]:
        yield None

    def flush(self) -> None:
        return None
