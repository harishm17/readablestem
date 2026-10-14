"""The `ConversionOptions` of the conversion in progress, for any step to read.

`run.convert_pdf` puts its options in force for the whole run (`using`), and every step
reads them with `current()`. They travel in a context variable rather than as a step
argument so that the step signatures, which tests and the eval harness swap in, stay
as they are: `run` already copies the context into each pool thread (for the tracer),
so a step on a pool thread sees the same options as the thread that started the run.

Outside a conversion (a step called on its own, the eval calling a step directly)
`current()` is the defaults: every feature off, no notation.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from readablestem.core.types import ConversionOptions

_CURRENT: ContextVar[ConversionOptions | None] = ContextVar("conversion_options", default=None)


def current() -> ConversionOptions:
    """The options in force (fresh defaults outside a conversion)."""
    return _CURRENT.get() or ConversionOptions()


@contextmanager
def using(options: ConversionOptions) -> Generator[ConversionOptions]:
    """Put `options` in force on this thread (and in contexts copied from it) for the
    block, then restore what was there before."""
    token = _CURRENT.set(options)
    try:
        yield options
    finally:
        _CURRENT.reset(token)
