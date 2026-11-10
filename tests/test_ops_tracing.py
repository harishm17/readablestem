"""observability.tracing: only allowlisted, content-free attributes leave the process.

The Langfuse tests run the real SDK (the optional `tracing` extra) with an in-memory span
exporter in place of the network one, so they check exactly what would be sent.
"""

from __future__ import annotations

import base64
import json
import os
import uuid

import pytest
from structlog.testing import capture_logs

from readablestem.config import Settings
from readablestem.observability import tracing
from readablestem.observability.tracing import (
    MASKED,
    LangfuseTracer,
    NoopTracer,
    exportable_key,
    make_tracer,
    mask_payload,
    safe_attributes,
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


def test_mask_hook_filters_metadata_and_masks_everything_else():
    assert mask_payload(data=None) is None
    assert mask_payload(data={"job_id": "j1", "alt": SENTINEL}) == {"job_id": "j1"}
    assert mask_payload(data=LATEX) == MASKED
    assert mask_payload(data=[SENTINEL]) == MASKED


# --------------------------------------------------------------------------- factory


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
