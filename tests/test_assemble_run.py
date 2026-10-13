"""run.convert_pdf with layout and steps replaced by fakes (WP2/WP3 are separate packages).

The fake steps call the real `FakeVisionClient`, so spend reservations, call records and
`BudgetExceeded` behave exactly as they do with the real client.
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import pytest
from latex2mathml.converter import convert
from pydantic import BaseModel

from a11y_stem.config import ConfigError, Settings
from a11y_stem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget
from a11y_stem.core.types import (
    Element,
    EquationFix,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    Status,
    inline_marker,
)
from a11y_stem.models.base import VisionRequest
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.models.pricing import UnknownModelError
from a11y_stem.pipeline import equation_step, figure_step, heading_step, layout, run, table_step
from a11y_stem.pipeline.layout import Layout, LayoutError
from a11y_stem.pipeline.run import CONTEXT_CHARS, PipelineOutput, convert_pdf

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SECRET = "SECRET-DOCUMENT-TEXT"


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


class EquationOut(BaseModel):
    latex: str
    confidence: float


class FigureOut(BaseModel):
    alt: str
    confidence: float


def fake_client(budget: JobBudget | None = None) -> FakeVisionClient:
    return FakeVisionClient(
        defaults={
            "equation": {"latex": r"v = \frac{d}{t}", "confidence": 0.95},
            "figure": {"alt": "A cart on a ramp.", "confidence": 0.9},
        },
        budget=budget,
    )


def el(eid: str, kind: Kind, page: int, order: int, **kw: Any) -> Element:
    return Element(id=eid, kind=kind, page=page, bbox=(72, 72, 172, 92), order=order, **kw)


def sample_layout() -> Layout:
    """Three pages: mixed content, two block equations, and a page with no model work."""
    long_text = "Velocity is a vector. " * 60  # ~1300 characters, to test truncation
    elements = [
        el("p1-e0", Kind.HEADING, 0, 0, text="Motion"),
        el(
            "p1-e1",
            Kind.PARAGRAPH,
            0,
            1,
            text=f"Speed is {inline_marker('p1-e2')} for a trip.",
        ),
        el("p1-e2", Kind.EQUATION, 0, 2, display="inline", parent_id="p1-e1"),
        el("p1-e3", Kind.EQUATION, 0, 3, display="block"),
        el("p1-e4", Kind.FIGURE, 0, 4, caption="Figure 1 A cart."),
        el("p1-e5", Kind.PARAGRAPH, 0, 5, text="The cart rolls down the ramp."),
        el("p2-e0", Kind.PARAGRAPH, 1, 6, text=long_text),
        el("p2-e1", Kind.EQUATION, 1, 7, display="block"),
        el("p2-e2", Kind.EQUATION, 1, 8, display="block"),
        el("p2-e3", Kind.PARAGRAPH, 1, 9, text="End of the worked example."),
        el("p3-e0", Kind.HEADING, 2, 10, text="Summary"),
    ]
    shuffled = elements[::2] + elements[1::2]  # layout order must not matter
    return Layout(elements=shuffled, page_count=3, pages=[0, 1, 2], title="Motion", lang="en")


MODEL_IDS = ["p1-e2", "p1-e3", "p1-e4", "p2-e1", "p2-e2"]  # reading order


@dataclass
class FakePipeline:
    """Installs fake layout/heading/equation/figure functions and records what they saw."""

    layout: Layout = field(default_factory=sample_layout)
    raise_in_step: dict[str, BaseException] = field(default_factory=dict)
    raise_in_crop: set[str] = field(default_factory=set)
    calls_per_step: int = 1
    step_models: tuple[str, ...] = ("gpt-6.1-sol",)  # model per call, cycled
    barrier: threading.Barrier | None = None
    step_delay_s: float = 0.0
    analyze_kwargs: dict[str, Any] = field(default_factory=dict)
    crop_threads: list[str] = field(default_factory=list)
    step_threads: list[str] = field(default_factory=list)
    stepped: list[str] = field(default_factory=list)
    contexts: dict[str, str | None] = field(default_factory=dict)
    active: int = 0
    max_active: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakePipeline:
        monkeypatch.setattr(layout, "analyze", self.analyze)
        monkeypatch.setattr(layout, "crop", self.crop)
        monkeypatch.setattr(heading_step, "heading_fixes", self.heading_fixes)
        monkeypatch.setattr(equation_step, "transcribe", self.transcribe)
        monkeypatch.setattr(figure_step, "describe", self.describe)
        monkeypatch.setattr(equation_step, "PROMPT_VERSION", "equation-declared")
        return self

    def analyze(self, pdf_bytes, *, pages=None, max_pages=None) -> Layout:
        self.analyze_kwargs = {"pages": pages, "max_pages": max_pages}
        return self.layout

    def crop(self, pdf_bytes, element, **kw) -> bytes:
        self.crop_threads.append(threading.current_thread().name)
        if element.id in self.raise_in_crop:
            raise RuntimeError(SECRET)
        return PNG_SIGNATURE + element.id.encode()

    def heading_fixes(self, elements) -> list[Fix]:
        return [
            Fix(
                element_id=e.id,
                kind=Kind.HEADING,
                status=Status.ACCEPTED,
                heading=HeadingFix(level=1 if e.page == 0 else 2, text=e.text or ""),
            )
            for e in elements
            if e.kind is Kind.HEADING
        ]

    def _enter(self, element: Element, context_text: str | None) -> None:
        with self._lock:
            self.stepped.append(element.id)
            self.step_threads.append(threading.current_thread().name)
            self.contexts[element.id] = context_text
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        if self.barrier is not None:
            self.barrier.wait()
        if self.step_delay_s:
            time.sleep(self.step_delay_s)
        if element.id in self.raise_in_step:
            raise self.raise_in_step[element.id]

    def _leave(self) -> None:
        with self._lock:
            self.active -= 1

    def _ask(self, client, step, prompt_version, png, context_text, schema):
        calls, parsed = [], None
        for n in range(self.calls_per_step):  # e.g. cheap model, then main model
            request = VisionRequest(
                step=step,
                model=self.step_models[n % len(self.step_models)],
                prompt_version=prompt_version,
                instructions="fixed instructions",
                images=[png],
                context_text=context_text,
            )
            parsed, call = client.run(request, schema)
            calls.append(call)
        return parsed, calls

    def transcribe(self, element, png, *, client, settings, context_text=None) -> Fix:
        try:
            self._enter(element, context_text)
            parsed, calls = self._ask(
                client, "equation", "equation-used", png, context_text, EquationOut
            )
        finally:
            self._leave()
        display = element.display or "block"
        return Fix(
            element_id=element.id,
            kind=Kind.EQUATION,
            status=Status.ACCEPTED,
            confidence=parsed.confidence,
            equation=EquationFix(
                latex=parsed.latex,
                mathml=convert(parsed.latex, display=display),
                speech="v equals d over t",
                display=display,
            ),
            calls=calls,
        )

    def describe(self, element, png, *, client, settings, context_text=None) -> Fix:
        try:
            self._enter(element, context_text)
            parsed, calls = self._ask(client, "figure", "figure-used", png, context_text, FigureOut)
        finally:
            self._leave()
        return Fix(
            element_id=element.id,
            kind=Kind.FIGURE,
            status=Status.ACCEPTED,
            confidence=parsed.confidence,
            figure=FigureFix(figure_type="diagram", alt=parsed.alt),
            calls=calls,
        )


@pytest.fixture
def fake(monkeypatch) -> FakePipeline:
    return FakePipeline().install(monkeypatch)


def fixes_by_id(output) -> dict[str, Fix]:
    return {f.element_id: f for f in output.result.fixes}


# --------------------------------------------------------------------------- happy path


def test_converts_a_document_end_to_end(fake):
    output = convert_pdf(b"%PDF", settings=settings(), client=fake_client(), pages=[0, 1, 2])
    result = output.result
    assert isinstance(output, PipelineOutput)
    assert fake.analyze_kwargs == {"pages": [0, 1, 2], "max_pages": 30}

    assert [e.order for e in result.elements] == list(range(11))
    assert [f.element_id for f in result.fixes] == [
        "p1-e0", "p1-e2", "p1-e3", "p1-e4", "p2-e1", "p2-e2", "p3-e0",
    ]  # fmt: skip
    assert {f.status for f in result.fixes} == {Status.ACCEPTED}
    assert set(output.crops) == set(MODEL_IDS)

    html = result.html
    assert '<p id="p1-e1">Speed is <math display="inline" id="p1-e2">' in html
    assert '<div class="equation" id="p2-e2"><math display="block">' in html
    assert 'alt="A cart on a ramp."' in html
    assert html.index('id="p1-e0"') < html.index('id="p2-e0"') < html.index('id="p3-e0"')
    assert output.checks and all(c.passed for c in output.checks)

    stats = result.stats
    assert stats.pages == 3
    assert stats.model_calls == 5
    assert stats.by_kind == {"heading": 2, "equation": 4, "figure": 1}
    assert stats.by_status == {"accepted": 7}
    assert stats.cost_usd > 0
    assert stats.cost_usd == pytest.approx(sum(f.cost_usd for f in result.fixes))
    assert [(p.page, p.elements, p.fixes) for p in stats.per_page] == [
        (0, 6, 4),
        (1, 4, 2),
        (2, 1, 1),
    ]
    assert sum(p.cost_usd for p in stats.per_page) == pytest.approx(stats.cost_usd)
    assert stats.latency_ms >= 0 and all(p.latency_ms >= 0 for p in stats.per_page)

    assert result.pipeline_version == run.PIPELINE_VERSION
    assert result.prompt_versions == {
        "equation": "equation-used",
        "figure": "figure-used",
        "table": table_step.PROMPT_VERSION,  # declared: no table in this document
    }
    assert (result.title, result.lang) == ("Motion", "en")


def test_declared_prompt_versions_are_used_when_no_call_was_made(fake):
    fake.layout = Layout(
        elements=[el("p1-e0", Kind.PARAGRAPH, 0, 0, text="x")], page_count=1, pages=[0]
    )
    output = convert_pdf(b"%PDF", settings=settings(), client=fake_client())
    assert output.result.prompt_versions["equation"] == "equation-declared"
    assert output.result.prompt_versions["figure"] == figure_step.PROMPT_VERSION


def test_crops_on_the_calling_thread_and_steps_in_the_pool(fake):
    convert_pdf(b"%PDF", settings=settings(), client=fake_client(), concurrency=3)
    assert fake.crop_threads == [threading.current_thread().name] * len(MODEL_IDS)
    assert fake.step_threads and all(name.startswith("step") for name in fake.step_threads)


def test_steps_run_concurrently(monkeypatch):
    fake = FakePipeline(barrier=threading.Barrier(4, timeout=5)).install(monkeypatch)
    fake.layout = Layout(
        elements=[el(f"p1-e{i}", Kind.EQUATION, 0, i, display="block") for i in range(4)],
        page_count=1,
        pages=[0],
    )
    output = convert_pdf(b"%PDF", settings=settings(), client=fake_client(), concurrency=4)
    # Each step waits until all four are inside it: only possible if they overlap.
    assert {f.status for f in output.result.fixes} == {Status.ACCEPTED}
    assert fake.max_active == 4


def test_concurrency_one_runs_steps_one_at_a_time(monkeypatch):
    fake = FakePipeline(step_delay_s=0.01).install(monkeypatch)
    convert_pdf(b"%PDF", settings=settings(), client=fake_client(), concurrency=1)
    assert fake.max_active == 1
    assert fake.stepped == MODEL_IDS


# --------------------------------------------------------------------------- context


def test_context_text_comes_from_neighbouring_text(fake):
    convert_pdf(b"%PDF", settings=settings(), client=fake_client())
    inline = fake.contexts["p1-e2"]
    assert inline == "Speed is [this equation] for a trip."

    assert fake.contexts["p1-e3"] == (
        "Speed is [equation] for a trip.\n[this equation]\nThe cart rolls down the ramp."
    )
    figure = fake.contexts["p1-e4"]
    assert "[this figure]" in figure and "The cart rolls down the ramp." in figure

    long_before = fake.contexts["p2-e1"]  # after a ~1300-character paragraph
    assert len(long_before) <= CONTEXT_CHARS + 40
    assert long_before.startswith("... ") and "\n[this equation]\n" in long_before

    for text in fake.contexts.values():
        assert text is not None and "⟦" not in text and "⟧" not in text


# --------------------------------------------------------------------------- budget


def budget(max_calls: int = 100, max_usd: float = 100.0, cap: float = 100.0) -> JobBudget:
    return JobBudget(guard=InMemorySpendGuard(cap_usd=cap), max_usd=max_usd, max_calls=max_calls)


def test_budget_exhaustion_stops_the_rest_and_still_assembles(fake):
    output = convert_pdf(
        b"%PDF", settings=settings(), client=fake_client(budget(max_calls=2)), concurrency=1
    )
    fixes = fixes_by_id(output)
    assert [fixes[i].status for i in MODEL_IDS] == [Status.ACCEPTED] * 2 + [Status.FAILED] * 3
    assert {fixes[i].reason for i in MODEL_IDS[2:]} == {"budget_job_calls"}
    assert fake.stepped == MODEL_IDS[:3]  # the 4th and 5th steps never started
    assert output.result.stats.model_calls == 2
    assert set(output.crops) == set(MODEL_IDS)  # originals kept for the flagged items
    html = output.result.html
    assert html.count('data-reason="budget_job_calls"') == 3
    assert all(c.passed for c in output.checks)


def test_budget_stop_cancels_queued_tasks_with_more_workers(monkeypatch):
    fake = FakePipeline(step_delay_s=0.02).install(monkeypatch)
    fake.layout = Layout(
        elements=[el(f"p1-e{i}", Kind.EQUATION, 0, i, display="block") for i in range(20)],
        page_count=1,
        pages=[0],
    )
    output = convert_pdf(
        b"%PDF", settings=settings(), client=fake_client(budget(max_calls=3)), concurrency=2
    )
    statuses = [f.status for f in output.result.fixes]
    assert statuses.count(Status.ACCEPTED) == 3
    assert statuses.count(Status.FAILED) == 17
    assert len(fake.stepped) < 10  # queued work was cancelled, not run and refused


def test_calls_made_before_a_refusal_are_kept(monkeypatch):
    FakePipeline(calls_per_step=2).install(monkeypatch)  # cheap call, then escalation
    output = convert_pdf(
        b"%PDF", settings=settings(), client=fake_client(budget(max_calls=3)), concurrency=1
    )
    first, second = (fixes_by_id(output)[i] for i in MODEL_IDS[:2])
    assert first.status is Status.ACCEPTED and len(first.calls) == 2
    assert second.status is Status.FAILED and second.reason == "budget_job_calls"
    assert len(second.calls) == 1 and second.cost_usd > 0  # billed, so counted
    assert output.result.stats.model_calls == 3


def test_daily_cap_scope_is_reported(fake):
    output = convert_pdf(
        b"%PDF", settings=settings(), client=fake_client(budget(cap=0.0001)), concurrency=1
    )
    assert {fixes_by_id(output)[i].reason for i in MODEL_IDS} == {"budget_daily"}
    assert output.result.stats.model_calls == 0


def test_a_step_reporting_budget_itself_also_stops_the_run(monkeypatch):
    fake = FakePipeline().install(monkeypatch)

    def refusing(element, png, *, client, settings, context_text=None):
        fake.stepped.append(element.id)
        return Fix(
            element_id=element.id, kind=element.kind, status=Status.FAILED, reason="budget_daily"
        )

    monkeypatch.setattr(equation_step, "transcribe", refusing)
    output = convert_pdf(b"%PDF", settings=settings(), client=fake_client(), concurrency=1)
    assert fake.stepped == ["p1-e2"]
    assert {fixes_by_id(output)[i].reason for i in MODEL_IDS} == {"budget_daily"}


# --------------------------------------------------------------------------- isolation


def test_a_failing_step_fails_only_its_element_and_logs_no_content(monkeypatch, caplog):
    FakePipeline(raise_in_step={"p1-e4": RuntimeError(SECRET)}).install(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        output = convert_pdf(b"%PDF", settings=settings(), client=fake_client())
    fixes = fixes_by_id(output)
    assert (fixes["p1-e4"].status, fixes["p1-e4"].reason) == (Status.FAILED, "internal_error")
    assert all(fixes[i].status is Status.ACCEPTED for i in MODEL_IDS if i != "p1-e4")
    assert '<div class="flagged" id="p1-e4" data-status="failed"' in output.result.html
    assert SECRET not in caplog.text
    assert "figure step failed: RuntimeError" in caplog.text


def test_unexpected_budget_error_types_do_not_escape(monkeypatch):
    fake = FakePipeline(raise_in_step={"p1-e3": BudgetExceeded("job_usd", SECRET)})
    fake.install(monkeypatch)
    output = convert_pdf(b"%PDF", settings=settings(), client=fake_client(), concurrency=1)
    fixes = fixes_by_id(output)
    assert fixes["p1-e2"].status is Status.ACCEPTED
    assert {fixes[i].reason for i in MODEL_IDS[1:]} == {"budget_job_usd"}


def test_heading_step_failure_keeps_headings_as_paragraphs(fake, monkeypatch):
    def broken(elements):
        raise ValueError(SECRET)

    monkeypatch.setattr(heading_step, "heading_fixes", broken)
    output = convert_pdf(b"%PDF", settings=settings(), client=fake_client())
    fixes = fixes_by_id(output)
    assert (fixes["p1-e0"].status, fixes["p1-e0"].reason) == (Status.FAILED, "internal_error")
    assert '<p id="p1-e0">Motion</p>' in output.result.html
    assert all(c.passed for c in output.checks)


def test_crop_failure_skips_the_step(monkeypatch):
    fake = FakePipeline(raise_in_crop={"p1-e3"}).install(monkeypatch)
    output = convert_pdf(b"%PDF", settings=settings(), client=fake_client())
    fix = fixes_by_id(output)["p1-e3"]
    assert (fix.status, fix.reason) == (Status.FAILED, "crop_error")
    assert "p1-e3" not in fake.stepped and "p1-e3" not in output.crops
    assert "The original image is not available." in output.result.html


def test_a_fix_for_the_wrong_element_is_rejected(fake, monkeypatch):
    def confused(element, png, *, client, settings, context_text=None):
        return Fix(element_id="other", kind=Kind.EQUATION, status=Status.ACCEPTED)

    monkeypatch.setattr(equation_step, "transcribe", confused)
    output = convert_pdf(b"%PDF", settings=settings(), client=fake_client())
    eq_fixes = [f for f in output.result.fixes if f.kind is Kind.EQUATION]
    assert {(f.status, f.reason) for f in eq_fixes} == {(Status.FAILED, "internal_error")}


# --------------------------------------------------------------------------- progress, tracing


def test_progress_reports_each_page_once(fake):
    seen: list[tuple[int, int]] = []
    convert_pdf(
        b"%PDF",
        settings=settings(),
        client=fake_client(),
        progress=lambda d, t: seen.append((d, t)),
    )
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_progress_counts_pages_without_elements(fake):
    fake.layout = Layout(elements=[], page_count=4, pages=[0, 1, 2, 3])
    seen: list[tuple[int, int]] = []
    output = convert_pdf(
        b"%PDF",
        settings=settings(),
        client=fake_client(),
        progress=lambda d, t: seen.append((d, t)),
    )
    assert seen[-1] == (4, 4) and len(seen) == 4
    assert output.result.stats.pages == 4 and output.result.fixes == []
    assert all(c.passed for c in output.checks)


def test_an_exception_from_progress_aborts_the_run(monkeypatch):
    FakePipeline(step_delay_s=0.01).install(monkeypatch)

    def stop(done, total):
        raise KeyboardInterrupt  # e.g. a cancelled job

    started = time.perf_counter()
    with pytest.raises(KeyboardInterrupt):
        convert_pdf(b"%PDF", settings=settings(), client=fake_client(), progress=stop)
    assert time.perf_counter() - started < 5


def test_layout_errors_propagate(monkeypatch):
    def unreadable(pdf_bytes, *, pages=None, max_pages=None):
        raise LayoutError("encrypted", "PDF is encrypted")

    monkeypatch.setattr(layout, "analyze", unreadable)
    with pytest.raises(LayoutError) as exc:
        convert_pdf(b"%PDF", settings=settings(), client=fake_client())
    assert exc.value.code == "encrypted"


class SetupFailsClient:
    """A client whose setup fails on its first request, as WP1's does without a key
    (`require_openai_key()` runs inside `run`) or with an unpriced model."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.runs = 0
        self._lock = threading.Lock()

    def run(self, request, schema, *, is_fallback=False):
        with self._lock:
            self.runs += 1
        raise self.error


@pytest.mark.parametrize(
    "error", [ConfigError("OPENAI_API_KEY is not set"), UnknownModelError("no price")]
)
def test_configuration_errors_stop_the_run_instead_of_failing_each_element(
    monkeypatch, caplog, error
):
    fake = FakePipeline().install(monkeypatch)
    client = SetupFailsClient(error)
    with caplog.at_level(logging.WARNING), pytest.raises(type(error)):
        convert_pdf(b"%PDF", settings=settings(), client=client, concurrency=1)
    assert client.runs == 1 and len(fake.stepped) == 1  # not once per element
    assert "stopped the run" in caplog.text and str(error) not in caplog.text


def test_configuration_error_with_more_workers_still_stops_early(monkeypatch):
    fake = FakePipeline(step_delay_s=0.02).install(monkeypatch)
    client = SetupFailsClient(ConfigError("OPENAI_API_KEY is not set"))
    with pytest.raises(ConfigError):
        convert_pdf(b"%PDF", settings=settings(), client=client, concurrency=2)
    # Two in flight when the first one failed; nothing started after that.
    assert client.runs <= 2 < len(MODEL_IDS) and len(fake.stepped) <= 2


def install_release(monkeypatch, events: list[tuple[str, Any]]) -> None:
    """A `layout.release` like WP2's (the stub in this package has none) that logs calls."""
    monkeypatch.setattr(layout, "release", lambda pdf: events.append(("release", pdf)), False)


def test_the_document_is_released_from_layout_once_cropping_is_done(monkeypatch):
    fake = FakePipeline(step_delay_s=0.02).install(monkeypatch)
    events: list[tuple[str, Any]] = []
    cropped, stepped = fake.crop, fake.transcribe

    def crop(pdf_bytes, element, **kw):
        events.append(("crop", element.id))
        return cropped(pdf_bytes, element, **kw)

    def transcribe(element, png, **kw):
        fix = stepped(element, png, **kw)
        events.append(("step_done", element.id))
        return fix

    monkeypatch.setattr(layout, "crop", crop)
    monkeypatch.setattr(equation_step, "transcribe", transcribe)
    install_release(monkeypatch, events)
    pdf = b"%PDF-1.7 user document"
    convert_pdf(pdf, settings=settings(), client=fake_client(), concurrency=1)

    releases = [i for i, (name, _) in enumerate(events) if name == "release"]
    assert len(releases) == 1 and events[releases[0]][1] is pdf
    last_crop = max(i for i, (name, _) in enumerate(events) if name == "crop")
    last_step = max(i for i, (name, _) in enumerate(events) if name == "step_done")
    assert last_crop < releases[0] < last_step  # not held while waiting on the model


def raise_in_analyze(monkeypatch):
    def unreadable(pdf_bytes, *, pages=None, max_pages=None):
        raise LayoutError("too_many_pages", "too long")

    monkeypatch.setattr(layout, "analyze", unreadable)
    return LayoutError, {}


def raise_in_progress(monkeypatch):
    def progress(done, total):
        raise RuntimeError("job cancelled")

    return RuntimeError, {"progress": progress}


def raise_in_client(monkeypatch):
    return ConfigError, {"client": SetupFailsClient(ConfigError("OPENAI_API_KEY is not set"))}


@pytest.mark.parametrize("failure", [raise_in_analyze, raise_in_progress, raise_in_client])
def test_the_document_is_released_when_the_run_fails(monkeypatch, failure):
    FakePipeline().install(monkeypatch)
    events: list[tuple[str, Any]] = []
    install_release(monkeypatch, events)
    error, kwargs = failure(monkeypatch)
    kwargs.setdefault("client", fake_client())
    with pytest.raises(error):
        convert_pdf(b"%PDF", settings=settings(), **kwargs)
    assert [name for name, _ in events] == ["release"]


def test_a_failing_release_does_not_fail_the_conversion(fake, monkeypatch):
    def release(pdf_bytes):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(layout, "release", release, raising=False)
    output = convert_pdf(b"%PDF", settings=settings(), client=fake_client())
    assert {f.status for f in output.result.fixes} == {Status.ACCEPTED}


class RecordingTracer:
    def __init__(self) -> None:
        self.spans: list[tuple[str, dict[str, Any]]] = []
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def span(self, name: str, **attrs: Any):
        record = (name, dict(attrs))
        with self._lock:
            self.spans.append(record)

        class Span:
            def update(self, **more: Any) -> None:
                record[1].update(more)

        yield Span()

    def flush(self) -> None:
        return None


def test_tracer_spans_are_content_free(fake):
    tracer = RecordingTracer()
    convert_pdf(b"%PDF", settings=settings(), client=fake_client(), tracer=tracer)
    names = [name for name, _ in tracer.spans]
    assert names.count("document") == 1 and names.count("page") == 3
    assert names.count("step") == len(MODEL_IDS) and "headings" in names and "assemble" in names
    step = next(attrs for name, attrs in tracer.spans if name == "step")
    assert {"step", "element_id", "page", "status", "model", "cost_usd", "calls"} <= set(step)
    document = next(attrs for name, attrs in tracer.spans if name == "document")
    assert document["model_calls"] == 5 and "reason" not in document  # no budget stop
    everything = repr(tracer.spans)
    for element in sample_layout().elements:
        for text in (element.text, element.caption):
            if text:
                assert text not in everything
    assert "A cart on a ramp." not in everything and "frac" not in everything


class SetTracer:
    """Shaped like WP6's tracer: the span handle has only `set(**attrs)`."""

    def __init__(self) -> None:
        self.spans: list[tuple[str, dict[str, Any], dict[str, Any]]] = []  # name, open, all
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def span(self, name: str, **attrs: Any):
        record = (name, dict(attrs), dict(attrs))
        with self._lock:
            self.spans.append(record)

        class Handle:
            def set(self, **more: Any) -> None:
                record[2].update(more)

        yield Handle()

    def flush(self) -> None:
        return None

    def named(self, name: str) -> list[dict[str, Any]]:
        return [attrs for span, _, attrs in self.spans if span == name]


# WP6 keeps numbers, booleans and strings matching this; it silently drops anything else.
WP6_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,79}$")


def wp6_keeps(value: Any) -> bool:
    return isinstance(value, bool | int | float) or (
        isinstance(value, str) and WP6_CODE.match(value) is not None
    )


def test_span_results_reach_a_set_style_handle_as_scalars(fake):
    tracer = SetTracer()
    output = convert_pdf(b"%PDF", settings=settings(), client=fake_client(), tracer=tracer)
    steps = tracer.named("step")
    assert len(steps) == len(MODEL_IDS)
    for step in steps:
        assert step["status"] == "accepted" and step["model"] == "gpt-6.1-sol"
        assert step["cost_usd"] > 0 and step["calls"] == 1 and "latency_ms" in step
    (document,) = tracer.named("document")
    assert document["pages"] == 3 and document["model_calls"] == 5
    assert document["cost_usd"] == output.result.stats.cost_usd
    assert document["fixes_accepted"] == 7 and document["checks_failed"] == 0
    (headings,) = tracer.named("headings")
    assert headings["fixes_accepted"] == 2
    for _, _, attrs in tracer.spans:
        assert all(wp6_keeps(v) for v in attrs.values()), attrs


def test_each_model_request_gets_a_span_opened_with_its_model(fake):
    fake.calls_per_step, fake.step_models = 2, ("gpt-6-luna", "gpt-6.1-sol")
    tracer = SetTracer()
    convert_pdf(b"%PDF", settings=settings(), client=fake_client(), tracer=tracer)
    calls = [(opened, attrs) for name, opened, attrs in tracer.spans if name == "model_call"]
    assert len(calls) == 2 * len(MODEL_IDS)
    # `model` at open time is what makes a WP6 span a Langfuse generation (cost views).
    assert sorted({opened["model"] for opened, _ in calls}) == ["gpt-6-luna", "gpt-6.1-sol"]
    for opened, attrs in calls:
        assert opened["step"] in ("equation", "figure") and opened["is_fallback"] is False
        assert attrs["outcome"] == "ok" and attrs["cost_usd"] > 0 and "input_tokens" in attrs
    for step in tracer.named("step"):  # two models: no joined "a,b" string on the step
        assert "model" not in step and step["calls"] == 2


def test_budget_stop_reason_is_on_the_document_span(fake):
    tracer = SetTracer()
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=100), max_usd=100, max_calls=2)
    convert_pdf(
        b"%PDF", settings=settings(), client=fake_client(budget), tracer=tracer, concurrency=1
    )
    (document,) = tracer.named("document")
    assert document["reason"] == "budget_job_calls" and document["fixes_failed"] == 3


class FailingTracer:
    """A tracer whose spans fail to open, or fail when they close (e.g. a flush error)."""

    def __init__(self, *, on_enter: bool) -> None:
        self.on_enter = on_enter
        self.closed_with: list[type[BaseException] | None] = []

    @contextlib.contextmanager
    def span(self, name, **attrs):
        if self.on_enter:
            raise RuntimeError("tracing backend down")
        try:
            yield None
        except BaseException as exc:
            self.closed_with.append(type(exc))
            raise RuntimeError("flush failed on close") from None
        self.closed_with.append(None)
        raise RuntimeError("flush failed on close")

    def flush(self) -> None:
        return None


@pytest.mark.parametrize("on_enter", [True, False])
def test_a_broken_tracer_never_stops_a_conversion(fake, on_enter):
    tracer = FailingTracer(on_enter=on_enter)
    output = convert_pdf(b"%PDF", settings=settings(), client=fake_client(), tracer=tracer)
    assert {f.status for f in output.result.fixes} == {Status.ACCEPTED}
    assert output.result.html


def test_a_tracer_cannot_swallow_a_real_error(monkeypatch):
    def unreadable(pdf_bytes, *, pages=None, max_pages=None):
        raise LayoutError("unreadable", "bad PDF")

    monkeypatch.setattr(layout, "analyze", unreadable)
    tracer = FailingTracer(on_enter=False)
    with pytest.raises(LayoutError):
        convert_pdf(b"%PDF", settings=settings(), client=fake_client(), tracer=tracer)
    assert tracer.closed_with == [LayoutError]


def test_inline_equation_alone_in_its_paragraph_gets_the_neighbours(fake):
    fake.layout = Layout(
        elements=[
            el("p1-e0", Kind.PARAGRAPH, 0, 0, text="The kinetic energy is"),
            el("p1-e1", Kind.PARAGRAPH, 0, 1, text=inline_marker("p1-e2")),
            el("p1-e2", Kind.EQUATION, 0, 2, display="inline", parent_id="p1-e1"),
            el("p1-e3", Kind.PARAGRAPH, 0, 3, text="where m is the mass ⟦a, b⟧."),
        ],
        page_count=1,
        pages=[0],
    )
    convert_pdf(b"%PDF", settings=settings(), client=fake_client())
    assert fake.contexts["p1-e2"] == (
        "The kinetic energy is\n[this equation]\nwhere m is the mass ⟦a, b⟧."
    )  # the parent (only a marker) is skipped; interval brackets are not markers


def test_list_items_are_context_for_a_display_equation_between_them():
    """A worked solution as a numbered list: the equation inside step 1 is introduced by
    step 1 and followed by step 2, not by whatever paragraph came before the list."""
    elements = [
        el("p1-e0", Kind.PARAGRAPH, 0, 0, text="Unrelated paragraph before the list."),
        el("p1-e1", Kind.LIST_ITEM, 0, 1, text="Solution: the average velocity is",
           list_marker="1.", list_ordered=True, list_level=1),
        el("p1-e2", Kind.EQUATION, 0, 2, display="block"),
        el("p1-e3", Kind.LIST_ITEM, 0, 3, text="Discussion: the answer is positive.",
           list_marker="2.", list_ordered=True, list_level=1),
        el("p1-e4", Kind.PARAGRAPH, 0, 4, text="Unrelated paragraph after the list."),
    ]  # fmt: skip
    assert run.context_texts(elements)["p1-e2"] == (
        "Solution: the average velocity is\n[this equation]\nDiscussion: the answer is positive."
    )


def test_an_inline_equation_in_a_list_item_gets_its_item_around_the_marker():
    elements = [
        el("p1-e0", Kind.LIST_ITEM, 0, 0, text=f"Speed is {inline_marker('p1-e1')} here.",
           list_marker="a.", list_ordered=True, list_level=1),
        el("p1-e1", Kind.EQUATION, 0, 1, display="inline", parent_id="p1-e0"),
    ]  # fmt: skip
    assert run.context_texts(elements)["p1-e1"] == "Speed is [this equation] here."
