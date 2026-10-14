"""Content-free logging helpers for the web app and worker.

Exception messages can carry document text (a parser quoting the line it choked on, a
validator echoing LaTeX), so they are never logged. What is logged about an exception is
its type and where it happened (file:line:function of the innermost frames), which is
enough to find the bug without seeing the document.

Every line is structured (`observability.logging.log_fields`): the message is the event
name alone and the fields are top-level JSON keys, so Logs Explorer can match
`jsonPayload.message="job_failed"` exactly and filter on `jsonPayload.error_code`.
"""

from __future__ import annotations

import logging

from readablestem.observability.logging import exception_fields, log_fields

Field = str | int | float | bool | None | list[str]


def log_event(
    logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Field
) -> None:
    """One log line: an event name plus ids, counts and category codes only.

    Callers must only pass content-free values (ids, numbers, codes); never filenames,
    emails or document text.
    """
    log_fields(logger, level, event, stacklevel=2, **fields)


def log_exception(logger: logging.Logger, event: str, exc: BaseException, **fields: Field) -> None:
    """`log_event` at ERROR plus `error_type` and `stack` (code locations, innermost last;
    never the message)."""
    log_fields(logger, logging.ERROR, event, stacklevel=2, **fields, **exception_fields(exc))
