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

The two hooks only see attributes and payloads, so the client is also built to close the
other ways out, for code that calls the Langfuse SDK directly: its private
`TracerProvider` hands exporters a copy of each span without the status description,
events and link attributes (and with a code-like name), and Langfuse's media upload,
which sends base64 data URIs found in a payload before the `mask` hook runs, is off.

The client gets its own OpenTelemetry `TracerProvider` and exports only spans it created
itself, so a library instrumented elsewhere (an OpenAI auto-instrumentor, say) cannot send
prompts through it. Exceptions are recorded as level ERROR plus the exception type; they
never reach OpenTelemetry's own exception recording, which would copy the message.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Generator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Any, Protocol

from readablestem.observability.logging import get_logger

if TYPE_CHECKING:
    from langfuse import Langfuse
    from langfuse.types import MaskOtelSpansParams, MaskOtelSpansResult
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SpanExporter

    from readablestem.config import Settings

# Read by the Langfuse SDK when a client starts (langfuse/_client/environment_variables.py).
_MEDIA_UPLOAD_ENV = "LANGFUSE_MEDIA_UPLOAD_ENABLED"

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
# Counts: numbers only, never strings.
COUNT_ATTRIBUTES = frozenset({"calls", "model_calls", "elements", "tasks", "fixes", "count"})
# Steps that call a model. A span opened with one of these as `step` (or with `model=`)
# becomes a Langfuse generation, so the model, tokens and cost set on it later land in
# Langfuse's cost views. The type is fixed when the span starts, so it is decided then.
GENERATION_STEPS = frozenset({"equation", "figure", "judge"})
# A code-like string: no spaces, no LaTeX, short. Model names, prompt versions, hex ids,
# element ids and category codes all match; sentences and formulas do not.
_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,79}$")
_SPAN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,63}$")
_MAX_MODELS = 4  # `model` may list the models a step used: "gpt-6-luna,gpt-6.1-sol"
MASKED = "[masked]"

AttributeValue = str | bool | int | float


class SpanHandle(Protocol):
    def set(self, **attrs: Any) -> None:
        """Add (allowlisted) attributes after the span started, e.g. tokens and outcome."""
        ...

    def update(self, **attrs: Any) -> None:
        """Same as `set` (the name the pipeline uses)."""
        ...


class Tracer(Protocol):
    """Spans nest through `contextvars`. Work fanned out to a thread pool nests under the
    caller's span only if each task runs in a copy of the caller's context:
    `pool.submit(contextvars.copy_context().run, work)` (one copy per task); otherwise
    each task's span starts a trace of its own.
    """

    def span(self, name: str, **attrs: Any) -> Any:  # context manager yielding a SpanHandle
        ...

    def flush(self) -> None: ...


# --------------------------------------------------------------------------- masking


def safe_attributes(attrs: Mapping[str, Any]) -> dict[str, AttributeValue]:
    """Allowlisted keys with plain values only. Everything else is dropped silently."""
    return {key: value for key, value in attrs.items() if _allowed(key, value)}


def _allowed(key: str, value: Any) -> bool:
    if key in COUNT_ATTRIBUTES:
        return _number(value)
    if key == "model" and isinstance(value, str) and "," in value:
        models = value.split(",")
        return len(models) <= _MAX_MODELS and all(_plain(m) for m in models)
    return key in ALLOWED_ATTRIBUTES and _plain(value)


def _number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _plain(value: Any) -> bool:
    """Numbers, booleans and code-like strings; never free text or objects."""
    if isinstance(value, bool) or _number(value):
        return True
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
        name = key.removeprefix(_METADATA_PREFIX)
        return name in ALLOWED_ATTRIBUTES or name in COUNT_ATTRIBUTES
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

    update = set


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

    # `pipeline.run` annotates spans with `span.update(...)`.
    update = set

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

    A span opened with `model=`, or with `step=` a model step ("equation", "figure",
    "judge"), becomes a Langfuse "generation", so the model, token counts and `cost_usd`
    set on it, even after it started, show up in Langfuse's cost views; other spans are
    plain spans.
    """

    def __init__(self, client: Langfuse) -> None:
        self._client = client

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Generator[SpanHandle]:
        safe = safe_attributes(attrs)
        is_generation = "model" in safe or safe.get("step") in GENERATION_STEPS
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


def _content_free_tracer_provider() -> TracerProvider:
    """A private TracerProvider whose span processors only ever see content-free spans.

    `mask_otel_spans` can change attributes only, but an OpenTelemetry span carries more:
    a status description (`update(status_message=...)` sets it), events
    (`record_exception` copies the exception message into one), link attributes and its
    name. Every processor added to this provider (Langfuse adds its exporting one) gets a
    copy of each finished span without the description, events and link attributes, and
    with "span" for a name that is not code-like. Uses public OpenTelemetry API only.
    """
    from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
    from opentelemetry.trace import Link, Status

    def scrub(span: ReadableSpan) -> ReadableSpan:
        return ReadableSpan(
            name=safe_span_name(span.name),
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes=span.attributes,
            events=(),
            links=tuple(Link(link.context) for link in span.links),
            kind=span.kind,
            status=Status(span.status.status_code),
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )

    class ScrubbingProcessor(SpanProcessor):
        def __init__(self, inner: SpanProcessor) -> None:
            self._inner = inner

        def on_start(self, span: Any, parent_context: Any = None) -> None:
            self._inner.on_start(span, parent_context=parent_context)

        def _on_ending(self, span: Any) -> None:
            ending = getattr(self._inner, "_on_ending", None)
            if callable(ending):
                ending(span)

        def on_end(self, span: ReadableSpan) -> None:
            self._inner.on_end(scrub(span))

        def shutdown(self) -> None:
            self._inner.shutdown()

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return self._inner.force_flush(timeout_millis)

    class ContentFreeTracerProvider(TracerProvider):
        def add_span_processor(self, span_processor: SpanProcessor) -> None:
            super().add_span_processor(ScrubbingProcessor(span_processor))

    return ContentFreeTracerProvider()


def build_langfuse_client(
    settings: Settings, *, span_exporter: SpanExporter | None = None
) -> Langfuse:
    """A masked Langfuse client with a private TracerProvider. Raises ImportError if the
    `tracing` extra is not installed. `span_exporter` replaces the network exporter (tests).

    Media upload is switched off. Langfuse otherwise finds base64 data URIs (a page crop,
    say) in input, output and metadata and uploads them straight to its media storage,
    before the `mask` hook runs and outside the span export that `mask_otel_spans` sees.
    The SDK reads the switch from the environment only, when the client starts.
    """
    os.environ[_MEDIA_UPLOAD_ENV] = "false"
    from langfuse import Langfuse
    from langfuse.span_filter import is_langfuse_span

    secret = settings.langfuse_secret_key
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=secret.get_secret_value() if secret is not None else None,
        base_url=settings.langfuse_host or None,
        environment=settings.env,
        mask=mask_payload,
        mask_otel_spans=mask_otel_spans,
        should_export_span=is_langfuse_span,
        tracer_provider=_content_free_tracer_provider(),
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
