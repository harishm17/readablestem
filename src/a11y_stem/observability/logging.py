"""Structured JSON logs that cannot carry document content.

Cloud Run reads one JSON object per stdout line; the `severity`, `message` and `time` keys
are the ones Cloud Logging understands. Every record, from our own code or a third-party
library, goes through the same processor chain, and two processors in it exist only to
keep course material out of the logs:

- `privacy_filter` drops any key that names content (`text`, `latex`, `alt`, `email`, ...,
  also inside compound names like `alt_text` or `user_email`), turns values that are not
  plain data into a type marker (so a pydantic `Element` cannot leak through its repr) and
  truncates long strings.
- `exception_codes` replaces a traceback with the exception's type and code locations.
  Exception messages are dropped: a pydantic error on model output, for example, quotes
  the LaTeX it rejected.

Below WARNING only our own loggers (and uvicorn's and alembic's lifecycle lines) are
written: third-party DEBUG and INFO lines print URLs with query strings (the OpenAI SDK's
httpx2 does) or request bodies, so LOG_LEVEL=DEBUG never turns them on. Uvicorn's access
log is disabled for the same reason; `request_logging_middleware` logs the route template.
"""

from __future__ import annotations

import logging
import re
import sys
import time
import traceback
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

    from a11y_stem.config import Settings

# Key words that name document content. A key is dropped when any of its words (split on
# "_", "-", "." and case changes) is one of these.
DENYLIST = frozenset(
    {
        "text",
        "latex",
        "mathml",
        "alt",
        "html",
        "filename",
        "email",
        "content",
        "caption",
        "context",
        "prompt",
        "speech",
    }
)
# Keys that contain a denylisted word but describe content without holding it.
SAFE_KEYS = frozenset({"prompt_version", "prompt_versions"})
MAX_STRING = 200
TRUNCATION_MARK = "...[truncated]"
MAX_DEPTH = 4
MAX_FRAMES = 8

# Loggers allowed to write below WARNING. Everything else must reach WARNING to be shown.
_VERBOSE_ALLOWED = ("a11y_stem", "uvicorn.error", "alembic.runtime.migration")
# Chatty libraries also get their level raised, so they skip formatting messages at all.
_QUIET_LOGGERS = (
    "openai",
    "httpx",
    "httpx2",
    "httpcore",
    "urllib3",
    "langfuse",
    "sqlalchemy.engine",
)
_HANDLER_FLAG = "_a11y_stem_handler"
_SEVERITY = {
    "debug": "DEBUG",
    "info": "INFO",
    "warning": "WARNING",
    "warn": "WARNING",
    "error": "ERROR",
    "exception": "ERROR",
    "critical": "CRITICAL",
    "fatal": "CRITICAL",
}
# Structural keys added by the chain itself; never scrubbed.
_STRUCTURAL = frozenset({"event", "level", "logger", "time", "error_type", "stack"})
_WORD_SPLIT = re.compile(r"[^a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


# --------------------------------------------------------------------------- processors


def is_denied_key(key: str) -> bool:
    """True when a log key names document content (exact word match, any position)."""
    lowered = key.lower()
    if lowered in SAFE_KEYS:
        return False
    words = _WORD_SPLIT.split(_CAMEL.sub("_", key).lower())
    return any(word in DENYLIST for word in words)


def _truncate(value: str) -> str:
    if len(value) <= MAX_STRING:
        return value
    return value[:MAX_STRING] + TRUNCATION_MARK


def _scrub(value: Any, depth: int = 0) -> Any:
    """Plain JSON data only: denied keys dropped, long strings cut, objects reduced to type."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _truncate(value)
    if isinstance(value, Enum):
        return _scrub(value.value, depth)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if depth >= MAX_DEPTH:
        return "<nested>"
    if isinstance(value, Mapping):
        return {str(k): _scrub(v, depth + 1) for k, v in value.items() if not is_denied_key(str(k))}
    if isinstance(value, list | tuple | set | frozenset):
        return [_scrub(item, depth + 1) for item in value]
    # Anything else (models, paths, bytes, exceptions) could render content via repr.
    return f"<{type(value).__name__}>"


def privacy_filter(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """structlog processor: drop content keys, keep plain data, truncate long strings."""
    cleaned: EventDict = {}
    for key, value in event_dict.items():
        if key.startswith("_"):  # structlog/ProcessorFormatter internals (_record, ...)
            cleaned[key] = value
        elif key in _STRUCTURAL or not is_denied_key(key):
            cleaned[key] = _scrub(value)
    return cleaned


def _exc_tuple(exc_info: Any) -> tuple[type[BaseException], BaseException, Any] | None:
    if exc_info is True:
        current = sys.exc_info()
        return None if current[0] is None else cast(Any, current)
    if isinstance(exc_info, BaseException):
        return type(exc_info), exc_info, exc_info.__traceback__
    if isinstance(exc_info, tuple) and len(exc_info) == 3 and exc_info[0] is not None:
        return cast(Any, exc_info)
    return None


def _frame(entry: traceback.FrameSummary) -> str:
    parts = entry.filename.replace("\\", "/").split("/")
    return f"{'/'.join(parts[-3:])}:{entry.lineno}:{entry.name}"


def exception_codes(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Replace `exc_info` with the exception type and code locations, never its message."""
    event_dict.pop("stack_info", None)
    exc = _exc_tuple(event_dict.pop("exc_info", None))
    if exc is None:
        return event_dict
    exc_type, _value, tb = exc
    event_dict["error_type"] = f"{exc_type.__module__}.{exc_type.__qualname__}"
    if tb is not None:
        frames = traceback.extract_tb(tb)[-MAX_FRAMES:]
        event_dict["stack"] = [_frame(f) for f in frames]
    return event_dict


def cloud_severity(_: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """Cloud Logging reads `severity` (DEBUG, INFO, WARNING, ERROR, CRITICAL)."""
    level = str(event_dict.pop("level", method_name)).lower()
    event_dict["severity"] = _SEVERITY.get(level, "DEFAULT")
    return event_dict


def _shared_processors() -> list[Processor]:
    """Run on every record: structlog calls (before hand-off) and stdlib records alike."""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="time"),
        exception_codes,
        privacy_filter,
        cloud_severity,
    ]


# --------------------------------------------------------------------------- setup


def _is_own_or_serious(record: logging.LogRecord) -> bool:
    """Handler filter: below WARNING, only allowlisted loggers get through."""
    if record.levelno >= logging.WARNING:
        return True
    name = record.name
    return any(name == prefix or name.startswith(prefix + ".") for prefix in _VERBOSE_ALLOWED)


class _StdoutHandler(logging.StreamHandler):
    """Writes to whatever `sys.stdout` is at emit time.

    A handler created during an import would otherwise keep the stream that was current
    then (pytest's capture, for example) and fail once that stream is closed.
    """

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property  # type: ignore[override]
    def stream(self) -> Any:
        return sys.stdout

    @stream.setter
    def stream(self, _value: Any) -> None:
        return None


def configure_logging(settings: Settings | None = None) -> None:
    """JSON logs to stdout at `settings.log_level`. Safe to call more than once.

    One root handler renders both structlog and stdlib records, so uvicorn, SQLAlchemy and
    Langfuse warnings get the same severity mapping and privacy filter as our own lines.
    """
    level_name = (settings.log_level if settings is not None else "INFO").upper()
    level = logging.getLevelNamesMapping().get(level_name, logging.INFO)
    shared = _shared_processors()

    handler = _StdoutHandler()
    setattr(handler, _HANDLER_FLAG, True)
    handler.addFilter(_is_own_or_serious)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.EventRenamer("message"),
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, _HANDLER_FLAG, False)]:
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level)

    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))
    # Uvicorn's own handlers would bypass the chain; let its records reach the root handler.
    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """A structlog logger that always runs the privacy chain (configures defaults once)."""
    if not structlog.is_configured():
        configure_logging()
    return structlog.stdlib.get_logger(name)


# --------------------------------------------------------------------------- HTTP

_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_QUIET_ROUTES = frozenset({"/healthz"})


def route_template(scope: Mapping[str, Any]) -> str:
    """The matched route's template ("/jobs/{job_id}"), never the concrete path or query."""
    route = scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    return template if isinstance(template, str) and template else "unmatched"


async def request_logging_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """One log line per request: method, route template, status, latency_ms.

    Add with `app.middleware("http")(request_logging_middleware)`. No query string, no
    headers, no path parameters: a URL can carry a filename or an email address.
    """
    start = time.perf_counter()
    status = 500  # if the app raises, the server error middleware answers 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route = route_template(request.scope)
        method = request.method if request.method in _METHODS else "OTHER"
        latency_ms = round((time.perf_counter() - start) * 1000)
        log = get_logger("a11y_stem.http")
        emit = log.debug if route in _QUIET_ROUTES else log.info
        emit("request", method=method, route=route, status=status, latency_ms=latency_ms)
