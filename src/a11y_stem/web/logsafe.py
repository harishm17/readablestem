"""Content-free logging helpers for the web app and worker.

Exception messages can carry document text (a parser quoting the line it choked on, a
validator echoing LaTeX), so they are never logged. What is logged about an exception is
its type and where it happened (file:line:function of the innermost frames), which is
enough to find the bug without seeing the document.
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

Field = str | int | float | bool | None


def exception_summary(exc: BaseException, *, frames: int = 6) -> dict[str, str]:
    """Type and code locations of an exception; never its message or locals."""
    stack = traceback.extract_tb(exc.__traceback__)[-frames:]
    where = " < ".join(f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in reversed(stack))
    return {"exc_type": type(exc).__name__, "where": where}


def log_event(
    logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Field
) -> None:
    """One log line: an event name plus ids, counts and category codes only.

    Callers must only pass content-free values (ids, numbers, codes); never filenames,
    emails or document text.
    """
    text = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.log(level, "%s %s", event, text, extra={"event": event, "fields": fields})


def log_exception(logger: logging.Logger, event: str, exc: BaseException, **fields: Field) -> None:
    log_event(logger, event, logging.ERROR, **fields, **exception_summary(exc))
