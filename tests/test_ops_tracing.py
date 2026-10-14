"""observability.tracing: only allowlisted, content-free attributes leave the process.

The Langfuse tests run the real SDK (the optional `tracing` extra) with an in-memory span
exporter in place of the network one, so they check exactly what would be sent.
"""

from __future__ import annotations

import base64
import contextvars
import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from structlog.testing import capture_logs

from readablestem.config import Settings
from readablestem.observability import tracing
from readablestem.observability.tracing import (
    ALLOWED_ATTRIBUTES,
    MASKED,
    LangfuseTracer,
    NoopTracer,
    exportable_key,
    make_tracer,
    mask_payload,
    safe_attributes,
    safe_span_name,
)

SENTINEL = "quokka"
LATEX = rf"\frac{{{SENTINEL}}}{{2}}"


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def enabled(**kw) -> Settings:
    return settings(
        tracing_enabled=True,
        langfuse_public_key=f"pk-lf-test-{uuid.uuid4().hex}",  # one SDK singleton per test
        langfuse_secret_key="sk-lf-test",
        langfuse_host="http://127.0.0.1:9",  # never contacted: the exporter is replaced
        **kw,
    )


# --------------------------------------------------------------------------- masking


def test_safe_attributes_keeps_allowlisted_plain_values_only():
    attrs = {
        "job_id": "3f2a9c",
        "element_id": "p3-e12",
        "model": "gpt-6.1-sol",
        "prompt_version": "equation-1a2b3c4d",
        "input_tokens": 812,
        "cost_usd": 0.0042,
        "is_fallback": False,
        "status": "needs_review",
        # not allowlisted
        "latex": LATEX,
        "text": SENTINEL,
        "filename": "notes.pdf",
        # allowlisted keys with values that are not codes
        "reason": f"the model said {SENTINEL}",
        "outcome": LATEX,
        "latency_ms": float("nan"),
        "page": object(),
    }
    assert safe_attributes(attrs) == {
        "job_id": "3f2a9c",
        "element_id": "p3-e12",
        "model": "gpt-6.1-sol",
        "prompt_version": "equation-1a2b3c4d",
        "input_tokens": 812,
        "cost_usd": 0.0042,
        "is_fallback": False,
        "status": "needs_review",
    }


def test_span_names_must_look_like_codes():
    assert safe_span_name("equation.transcribe") == "equation.transcribe"
    assert safe_span_name(f"equation {SENTINEL}") == "span"
    assert safe_span_name(LATEX) == "span"


def test_mask_hook_filters_metadata_and_masks_everything_else():
    assert mask_payload(data=None) is None
    assert mask_payload(data={"job_id": "j1", "alt": SENTINEL}) == {"job_id": "j1"}
    assert mask_payload(data=LATEX) == MASKED
    assert mask_payload(data=[SENTINEL]) == MASKED


def test_export_allowlist():
    assert exportable_key("langfuse.observation.type")
    assert exportable_key("langfuse.observation.usage_details")
    assert exportable_key("langfuse.observation.metadata.cost_usd")
    for key in (
        "langfuse.observation.input",
        "langfuse.observation.output",
        "langfuse.trace.input",
        "langfuse.observation.status_message",
        "langfuse.observation.metadata.text",
        "user.id",
        "gen_ai.prompt.0.content",
    ):
        assert not exportable_key(key)
    assert {"job_id", "model", "cost_usd", "latency_ms", "page"} <= ALLOWED_ATTRIBUTES


# --------------------------------------------------------------------------- factory


def test_noop_tracer_accepts_attributes_and_updates():
    tracer = NoopTracer()
    with tracer.span("convert", job_id="j1") as span:
        span.set(outcome="ok")
        span.update(status="done")  # the name pipeline.run uses
    tracer.flush()


def test_counts_must_be_numbers_and_models_may_be_listed():
    assert safe_attributes(
        {
            "calls": 2,
            "model_calls": 6,
            "fixes": "3",  # counts are never strings
            "elements": True,
            "model": "gpt-6-luna,gpt-6.1-sol",
        }
    ) == {"calls": 2, "model_calls": 6, "model": "gpt-6-luna,gpt-6.1-sol"}
    assert safe_attributes({"model": f"gpt-6-luna,{SENTINEL} said"}) == {}
    assert safe_attributes({"model": ",".join(["m"] * 9)}) == {}
    assert exportable_key("langfuse.observation.metadata.calls")


def test_tracing_is_off_by_default():
    assert isinstance(make_tracer(settings()), NoopTracer)


def test_missing_keys_fall_back_with_a_content_free_warning():
    with capture_logs() as logs:
        tracer = make_tracer(settings(tracing_enabled=True, langfuse_public_key="pk"))
    assert isinstance(tracer, NoopTracer)
    assert logs == [{"event": "tracing_disabled", "reason": "missing_keys", "log_level": "warning"}]


def test_missing_package_falls_back(monkeypatch):
    monkeypatch.setitem(sys.modules, "langfuse", None)  # import langfuse -> ImportError
    with capture_logs() as logs:
        assert isinstance(make_tracer(enabled()), NoopTracer)
    assert logs[0]["reason"] == "package_missing"


def test_client_start_failure_falls_back(monkeypatch):
    def broken(_settings, **_kw):
        raise RuntimeError(f"bad host {SENTINEL}")

    monkeypatch.setattr(tracing, "build_langfuse_client", broken)
    with capture_logs() as logs:
        assert isinstance(make_tracer(enabled()), NoopTracer)
    assert logs[0]["reason"] == "init_failed" and logs[0]["error_type"] == "RuntimeError"
    assert SENTINEL not in json.dumps(logs)


def test_a_broken_tracer_never_breaks_the_traced_code():
    class Broken:
        def start_as_current_observation(self, **_kw):
            raise RuntimeError("exporter down")

        def flush(self):
            raise RuntimeError("exporter down")

    tracer = LangfuseTracer(Broken())  # type: ignore[arg-type]
    with capture_logs() as logs, tracer.span("convert", job_id="j1") as span:
        span.set(outcome="ok")
        ran = True
    tracer.flush()
    assert ran
    assert {e["stage"] for e in logs} == {"start"}


# --------------------------------------------------------------------------- Langfuse


@pytest.fixture
def exported():
    """A LangfuseTracer whose spans land in memory. Yields (tracer, client, read)."""
    pytest.importorskip("langfuse")
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    client = tracing.build_langfuse_client(enabled(), span_exporter=exporter)

    def read():
        client.flush()
        return exporter.get_finished_spans()

    yield LangfuseTracer(client), client, read
    client.shutdown()


def dump(spans) -> str:
    """Everything a span would carry over the wire, as one string."""
    return json.dumps(
        [
            {
                "name": s.name,
                "attributes": dict(s.attributes or {}),
                "events": [[e.name, dict(e.attributes or {})] for e in s.events],
                "status": s.status.description,
            }
            for s in spans
        ],
        default=str,
    )


def test_langfuse_spans_carry_only_allowlisted_attributes(exported):
    tracer, _client, read = exported
    with tracer.span("convert", job_id="3f2a9c", pages=3, text=SENTINEL):
        with tracer.span(
            "equation",
            element_id="p1-e2",
            model="gpt-6-luna",
            prompt_version="equation-1a2b3c4d",
            latex=LATEX,
        ) as span:
            span.set(input_tokens=800, output_tokens=60, cost_usd=0.0001, outcome="ok")
            span.set(alt=SENTINEL, speech=SENTINEL, status=f"{SENTINEL} with spaces")
        with tracer.span(f"figure {SENTINEL}", element_id="p1-e3"):
            pass

    spans = {s.name: s for s in read()}
    assert set(spans) == {"convert", "equation", "span"}
    for span in spans.values():
        assert all(exportable_key(k) for k in span.attributes or {}), span.attributes
        assert not span.events
    generation = dict(spans["equation"].attributes or {})
    assert generation["langfuse.observation.type"] == "generation"
    assert generation["langfuse.observation.model.name"] == "gpt-6-luna"
    assert json.loads(generation["langfuse.observation.usage_details"]) == {
        "input": 800,
        "output": 60,
    }
    assert json.loads(generation["langfuse.observation.cost_details"]) == {"total": 0.0001}
    assert spans["convert"].parent is None
    assert spans["equation"].parent.span_id == spans["convert"].context.span_id
    assert SENTINEL not in dump(spans.values())


def test_exceptions_are_recorded_by_type_without_the_message(exported):
    tracer, _client, read = exported
    with pytest.raises(ValueError), tracer.span("figure", element_id="p1-e3"):
        raise ValueError(f"model returned {LATEX}")
    (span,) = read()
    attrs = dict(span.attributes or {})
    assert attrs["langfuse.observation.level"] == "ERROR"
    assert attrs["langfuse.observation.metadata.error_type"] == "ValueError"
    assert not span.events  # OpenTelemetry's exception event would hold the message
    assert SENTINEL not in dump([span])


def test_direct_sdk_misuse_is_still_masked_at_export(exported):
    """If a future caller bypasses LangfuseTracer and passes input/output to the client,
    the mask hook and the export allowlist still keep the content out."""
    _tracer, client, read = exported
    with client.start_as_current_observation(
        name="direct",
        input={"latex": LATEX},
        output=SENTINEL,
        metadata={"job_id": "j1", "alt": SENTINEL},
    ):
        pass
    (span,) = read()
    attrs = dict(span.attributes or {})
    assert "langfuse.observation.input" not in attrs
    assert "langfuse.observation.output" not in attrs
    assert attrs["langfuse.observation.metadata.job_id"] == "j1"
    assert SENTINEL not in dump([span])


def _annotate(span, **attrs) -> None:
    """pipeline.run's `_annotate` (wp/4-assemble): it calls `update`, not `set`."""
    update = getattr(span, "update", None)
    if callable(update):
        update(**attrs)


def test_pipeline_step_spans_become_generations_annotated_after_start(exported):
    """pipeline.run opens a step span with step/element_id/page and only learns the model,
    status and cost when the step returns."""
    tracer, _client, read = exported
    with tracer.span("step", step="equation", element_id="p1-e2", page=0) as span:
        _annotate(
            span,
            status="needs_review",
            reason=None,
            model="gpt-6-luna,gpt-6.1-sol",
            calls=2,
            cost_usd=0.002,
        )
    with tracer.span("headings", count=3) as span:
        _annotate(span, statuses={"accepted": 3})  # a dict: dropped

    spans = {s.name: dict(s.attributes or {}) for s in read()}
    step = spans["step"]
    assert step["langfuse.observation.type"] == "generation"
    assert step["langfuse.observation.model.name"] == "gpt-6-luna,gpt-6.1-sol"
    assert json.loads(step["langfuse.observation.cost_details"]) == {"total": 0.002}
    assert step["langfuse.observation.metadata.status"] == "needs_review"
    assert step["langfuse.observation.metadata.calls"] == 2
    headings = spans["headings"]
    assert headings["langfuse.observation.type"] == "span"
    assert headings["langfuse.observation.metadata.count"] == 3
    assert not any("statuses" in k for k in headings)


def test_step_spans_in_pool_threads_nest_when_run_in_a_copied_context(exported):
    """The documented contract for thread fan-out (pipeline.run follows it)."""
    tracer, _client, read = exported

    def step(i: int) -> None:
        with tracer.span("step", step="figure", element_id=f"p1-e{i}"):
            pass

    with tracer.span("document"), ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contextvars.copy_context().run, step, i) for i in range(3)]
        for future in futures:
            future.result()

    spans = read()
    (document,) = [s for s in spans if s.name == "document"]
    steps = [s for s in spans if s.name == "step"]
    assert len(steps) == 3
    for span in steps:
        assert span.parent is not None and span.parent.span_id == document.context.span_id
        assert span.context.trace_id == document.context.trace_id


def test_direct_sdk_misuse_cannot_upload_media_or_export_status_and_events(exported):
    """Channels the attribute hooks cannot see: Langfuse's media upload (base64 data URIs
    in a payload are uploaded before `mask` runs), the span status description, events
    and the span name."""
    from opentelemetry import trace
    from opentelemetry.trace import StatusCode

    _tracer, client, read = exported
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + SENTINEL.encode() * 8).decode()
    with client.start_as_current_observation(
        name=f"direct {SENTINEL}",
        input={"image": f"data:image/png;base64,{png}"},
        output=f"data:image/png;base64,{png}",
    ) as observation:
        observation.update(level="ERROR", status_message=f"bad LaTeX {LATEX}")
        try:
            raise ValueError(SENTINEL)
        except ValueError as exc:
            trace.get_current_span().record_exception(exc)

    assert os.environ["LANGFUSE_MEDIA_UPLOAD_ENABLED"] == "false"
    # SDK internals, checked so an SDK change that ignores the switch fails loudly here.
    assert client._resources._media_upload_queue.qsize() == 0  # type: ignore[union-attr]
    (span,) = read()
    assert span.name == "span"
    assert span.status.status_code == StatusCode.ERROR
    assert span.status.description is None
    assert not span.events
    assert SENTINEL not in dump([span]) and png[:40] not in dump([span])


def test_make_tracer_builds_a_langfuse_tracer_when_configured(monkeypatch):
    pytest.importorskip("langfuse")
    built = []
    real = tracing.build_langfuse_client

    def offline(settings, **_kw):
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        client = real(settings, span_exporter=InMemorySpanExporter())
        built.append(client)
        return client

    monkeypatch.setattr(tracing, "build_langfuse_client", offline)
    tracer = make_tracer(enabled())
    assert isinstance(tracer, LangfuseTracer)
    for client in built:
        client.shutdown()
