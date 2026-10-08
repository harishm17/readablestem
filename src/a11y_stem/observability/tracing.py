"""Tracing. Off by default; when on, spans carry numbers and category codes only.

`make_tracer(settings)` returns `NoopTracer` unless TRACING_ENABLED is true and both
Langfuse keys are set; it also falls back to `NoopTracer` (with a content-free warning)
when the optional `langfuse` package is missing or the client cannot start.

Masking is an allowlist, applied three times so that one mistake is not a leak:

1. `LangfuseTracer.span(name, **attrs)` keeps only `ALLOWED_ATTRIBUTES` whose values are
   numbers, booleans or short code-like strings (no spaces, no backslashes), and never
   passes inputs or outputs. Unsafe span names become "span".
2. The Langfuse `mask` hook filters metadata the same way and replaces any other payload
   (input, output) with "[masked]".
3. The export-stage `mask_otel_spans` hook deletes every OpenTelemetry attribute that is
   not structural Langfuse data or an allowlisted metadata key.

The client gets its own OpenTelemetry `TracerProvider` and exports only spans it created
itself, so a library instrumented elsewhere (an OpenAI auto-instrumentor, say) cannot send
prompts through it. Exceptions are recorded as level ERROR plus the exception type; they
never reach OpenTelemetry's own exception recording, which would copy the message.
"""

from __future__ import annotations

import math
import re
from collections.abc import Generator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Any, Protocol

from a11y_stem.observability.logging import get_logger

if TYPE_CHECKING:
    from langfuse import Langfuse
    from langfuse.types import MaskOtelSpansParams, MaskOtelSpansResult
    from opentelemetry.sdk.trace.export import SpanExporter

    from a11y_stem.config import Settings

ALLOWED_ATTRIBUTES = frozenset(
    {
        # ids (opaque hex or "p3-e12"; never filenames or emails)
        "job_id",
        "element_id",
        "request_id",
        # what ran
        "step",
        "kind",
        "model",
        "prompt_version",
        "pipeline_version",
        "attempt",
        "is_fallback",
        # how it went (category codes)
        "status",
        "outcome",
        "reason",
        "error_type",
        "confidence",
        # cost and size
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cost_usd",
        "latency_ms",
        "page",
        "pages",
    }
)
# A code-like string: no spaces, no LaTeX, short. Model names, prompt versions, hex ids,
# element ids and category codes all match; sentences and formulas do not.
_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,79}$")
_SPAN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,63}$")
MASKED = "[masked]"

AttributeValue = str | bool | int | float


class SpanHandle(Protocol):
    def set(self, **attrs: Any) -> None:
        """Add (allowlisted) attributes after the span started, e.g. tokens and outcome."""
        ...


class Tracer(Protocol):
    def span(self, name: str, **attrs: Any) -> Any:  # context manager yielding a SpanHandle
        ...

    def flush(self) -> None: ...


# --------------------------------------------------------------------------- masking


def safe_attributes(attrs: Mapping[str, Any]) -> dict[str, AttributeValue]:
    """Allowlisted keys with plain values only. Everything else is dropped silently."""
    return {
        key: value for key, value in attrs.items() if key in ALLOWED_ATTRIBUTES and _plain(value)
    }


def _plain(value: Any) -> bool:
    """Numbers, booleans and code-like strings; never free text or objects."""
    if isinstance(value, bool | int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return isinstance(value, str) and _CODE.match(value) is not None


def safe_span_name(name: str) -> str:
    return name if _SPAN_NAME.match(name) else "span"


def mask_payload(*, data: Any, **_: Any) -> Any:
    """Langfuse `mask` hook: runs on input, output and metadata before they are recorded."""
    if data is None:
        return None
    if isinstance(data, Mapping):
        return safe_attributes(data)
    return MASKED


_STRUCTURAL_KEYS = frozenset(
    {
        "langfuse.observation.type",
        "langfuse.observation.level",
        "langfuse.observation.model.name",
        "langfuse.observation.usage_details",
        "langfuse.observation.cost_details",
        "langfuse.environment",
        "langfuse.release",
        "langfuse.version",
        "langfuse.internal.as_root",
        "langfuse.internal.is_app_root",
    }
)
_METADATA_PREFIX = "langfuse.observation.metadata."


def exportable_key(key: str) -> bool:
    """True for OpenTelemetry attributes allowed to leave the process."""
    if key in _STRUCTURAL_KEYS:
        return True
    if key.startswith(_METADATA_PREFIX):
        return key.removeprefix(_METADATA_PREFIX) in ALLOWED_ATTRIBUTES
    return False


def mask_otel_spans(*, params: MaskOtelSpansParams) -> MaskOtelSpansResult:
    """Langfuse export-stage hook: delete every attribute that is not allowlisted."""
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches = {}
    for identifier, span in params.spans.items():
        doomed = tuple(key for key in span.attributes if not exportable_key(key))
        if doomed:
            patches[identifier] = OtelSpanPatch(delete_attributes=doomed)
    return MaskOtelSpansResult(span_patches=patches)


# --------------------------------------------------------------------------- tracers


class _NoopSpan:
    def set(self, **attrs: Any) -> None:
        return None


class NoopTracer:
    @contextmanager
    def span(self, name: str, **attrs: Any) -> Generator[SpanHandle]:
        yield _NoopSpan()

    def flush(self) -> None:
        return None


class _LangfuseSpan:
    """Wraps a Langfuse observation; every update goes through `safe_attributes`."""

    def __init__(self, observation: Any, *, is_generation: bool) -> None:
        self._observation = observation
        self._is_generation = is_generation
        self._metadata: dict[str, AttributeValue] = {}

    def set(self, **attrs: Any) -> None:
        safe = safe_attributes(attrs)
        if not safe:
            return
        self._metadata.update(safe)
        update: dict[str, Any] = {"metadata": dict(self._metadata)}
        if self._is_generation:
            update.update(_generation_fields(self._metadata))
        if safe.get("status") == "failed" or "error_type" in safe:
            update["level"] = "ERROR"
        try:
            self._observation.update(**update)
        except Exception as exc:  # tracing must never break a conversion
            _log_failure("update", exc)

    def fail(self, exc: BaseException) -> None:
        self.set(error_type=type(exc).__name__)


def _log_failure(stage: str, exc: Exception) -> None:
    get_logger(__name__).warning("trace_failed", stage=stage, error_type=type(exc).__name__)


def _generation_fields(meta: Mapping[str, AttributeValue]) -> dict[str, Any]:
    """Model name, token usage and cost in the fields Langfuse uses for its cost views."""
    fields: dict[str, Any] = {}
    if isinstance(meta.get("model"), str):
        fields["model"] = meta["model"]
    usage = {
        name: int(meta[key])
        for name, key in (
            ("input", "input_tokens"),
            ("output", "output_tokens"),
            ("input_cached_tokens", "cached_input_tokens"),
            ("output_reasoning_tokens", "reasoning_tokens"),
        )
        if isinstance(meta.get(key), int | float)
    }
    if usage:
        fields["usage_details"] = usage
    cost = meta.get("cost_usd")
    if isinstance(cost, int | float) and not isinstance(cost, bool):
        fields["cost_details"] = {"total": float(cost)}
    return fields


class LangfuseTracer:
    """`Tracer` backed by a Langfuse v4 client (OpenTelemetry under the hood).

    A span opened with `model=` becomes a Langfuse "generation", so token counts and
    `cost_usd` show up in Langfuse's cost views; other spans are plain spans.
    """

    def __init__(self, client: Langfuse) -> None:
        self._client = client

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Generator[SpanHandle]:
        safe = safe_attributes(attrs)
        is_generation = "model" in safe
        manager: AbstractContextManager[Any] | None = None
        try:
            if is_generation:
                manager = self._client.start_as_current_observation(
                    name=safe_span_name(name), as_type="generation"
                )
            else:
                manager = self._client.start_as_current_observation(
                    name=safe_span_name(name), as_type="span"
                )
            observation = manager.__enter__()
        except Exception as exc:
            _log_failure("start", exc)
            manager = None
        if manager is None:
            yield _NoopSpan()
            return
        handle = _LangfuseSpan(observation, is_generation=is_generation)
        handle.set(**safe)
        try:
            yield handle
        except BaseException as exc:
            handle.fail(exc)
            # Close without the exception so OpenTelemetry does not record its message.
            self._close(manager)
            raise
        self._close(manager)

    @staticmethod
    def _close(manager: AbstractContextManager[Any]) -> None:
        try:
            manager.__exit__(None, None, None)
        except Exception as exc:
            _log_failure("end", exc)

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception as exc:
            _log_failure("flush", exc)

    def shutdown(self) -> None:
        self._client.shutdown()


# --------------------------------------------------------------------------- factory


def build_langfuse_client(
    settings: Settings, *, span_exporter: SpanExporter | None = None
) -> Langfuse:
    """A masked Langfuse client with a private TracerProvider. Raises ImportError if the
    `tracing` extra is not installed. `span_exporter` replaces the network exporter (tests).
    """
    from langfuse import Langfuse
    from langfuse.span_filter import is_langfuse_span
    from opentelemetry.sdk.trace import TracerProvider

    secret = settings.langfuse_secret_key
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=secret.get_secret_value() if secret is not None else None,
        base_url=settings.langfuse_host or None,
        environment=settings.env,
        mask=mask_payload,
        mask_otel_spans=mask_otel_spans,
        should_export_span=is_langfuse_span,
        tracer_provider=TracerProvider(),
        span_exporter=span_exporter,
    )


def make_tracer(settings: Settings) -> Tracer:
    """`LangfuseTracer` when tracing is enabled and configured, else `NoopTracer`."""
    if not settings.tracing_enabled:
        return NoopTracer()
    log = get_logger(__name__)
    secret = settings.langfuse_secret_key
    if not settings.langfuse_public_key or secret is None or not secret.get_secret_value():
        log.warning("tracing_disabled", reason="missing_keys")
        return NoopTracer()
    try:
        client = build_langfuse_client(settings)
    except ImportError:
        log.warning("tracing_disabled", reason="package_missing")
        return NoopTracer()
    except Exception as exc:  # a tracing problem must never stop the app
        log.warning("tracing_disabled", reason="init_failed", error_type=type(exc).__name__)
        return NoopTracer()
    return LangfuseTracer(client)
