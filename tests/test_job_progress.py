"""Live progress by stage: the events `convert_pdf` sends, and how the worker stores them.

Layout and the steps are fakes (as in test_content_run.py); the worker tests use the web
test app with a converter that sends events like the real one.
"""

from __future__ import annotations

import threading
from datetime import timedelta

import pymupdf
import pytest
from sqlalchemy import select

from a11y_stem.config import Settings
from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.core.types import Element, Fix, Kind, ProgressEvent, Status
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.pipeline import equation_step, figure_step, layout, run
from a11y_stem.pipeline.layout import Layout
from a11y_stem.web import worker as worker_module
from a11y_stem.web.db import Job, JobStatus, session_scope, utcnow
from a11y_stem.web.worker import JobCancelled
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, make_pdf, upload


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def png() -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), False)
    pixmap.set_rect(pixmap.irect, (30, 30, 30))
    return pixmap.tobytes("png")


def el(eid: str, kind: Kind, order: int, page: int = 0, **kw) -> Element:
    return Element(id=eid, kind=kind, page=page, bbox=(72, 72, 272, 172), order=order, **kw)


def document() -> Layout:
    elements = [
        el("p1-e0", Kind.HEADING, 0, text="Motion", font_size=18, bold=True),
        el("p1-e1", Kind.PARAGRAPH, 1, text="Speed is distance over time."),
        el("p1-e2", Kind.EQUATION, 2, display="block"),
        el("p1-e3", Kind.EQUATION, 3, display="block"),
        el("p1-e4", Kind.FIGURE, 4, caption="Figure 1 A cart."),
        el("p2-e0", Kind.PARAGRAPH, 5, page=1, text="More text."),
        el("p2-e1", Kind.EQUATION, 6, page=1, display="block"),
    ]
    return Layout(elements=elements, page_count=2, pages=[0, 1], title="Motion", lang="en")


def accepted(element, png, *, client, settings, context_text=None) -> Fix:
    return Fix(element_id=element.id, kind=element.kind, status=Status.ABSTAINED,
               reason="low_confidence")  # fmt: skip


@pytest.fixture
def fake_steps(monkeypatch):
    monkeypatch.setattr(layout, "analyze", lambda pdf, *, pages=None, max_pages=None: document())
    monkeypatch.setattr(layout, "crop", lambda pdf, element, **kw: png())
    monkeypatch.setattr(equation_step, "transcribe", accepted)
    monkeypatch.setattr(figure_step, "describe", accepted)


def convert(**kw):
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=1.0), max_usd=1.0, max_calls=100)
    return run.convert_pdf(
        b"%PDF-", settings=settings(), client=FakeVisionClient(budget=budget), **kw
    )


def events_of(**kw) -> list[ProgressEvent]:
    events: list[ProgressEvent] = []
    convert(stage_progress=events.append, **kw)
    return events


def test_stages_come_in_order_with_their_counts(fake_steps):
    events = events_of()
    stages = [e.stage for e in events]
    assert stages[:2] == ["layout", "layout"]
    assert stages[-2:] == ["assemble", "checks"]
    found = events[1]
    assert (found.done, found.total) == (2, 2)  # pages read
    assert found.counts == {"equation": 3, "figure": 1, "heading": 1, "paragraph": 2}
    # Totals first, then one event per element done, each stage up to its total.
    equations = [(e.done, e.total) for e in events if e.stage == "equations"]
    figures = [(e.done, e.total) for e in events if e.stage == "figures"]
    assert equations == [(0, 3), (1, 3), (2, 3), (3, 3)]
    assert figures == [(0, 1), (1, 1)]
    assert "tables" not in stages  # no tables in this document
    assert all(e.model_dump(mode="json") == ProgressEvent(**e.model_dump()).model_dump(mode="json")
               for e in events)  # fmt: skip


def test_events_come_on_the_calling_thread(fake_steps):
    threads: set[str] = set()
    convert(stage_progress=lambda e: threads.add(threading.current_thread().name))
    assert threads == {threading.current_thread().name}


def test_a_scanned_page_counts_as_done_before_any_model_work(fake_steps, monkeypatch):
    scans = {1: (0.0, 0.0, 612.0, 792.0)}
    monkeypatch.setattr(run, "_image_only_pages", lambda pdf, pages, elements: scans)
    two_pages = document()
    two_pages.elements = [e for e in two_pages.elements if e.page == 0]
    monkeypatch.setattr(layout, "analyze", lambda pdf, *, pages=None, max_pages=None: two_pages)
    events = events_of()
    figures = [(e.done, e.total) for e in events if e.stage == "figures"]
    assert figures[0] == (1, 2)  # the scanned page's figure is decided already
    assert figures[-1] == (2, 2)


def test_a_failed_crop_and_a_spent_budget_count_as_done(fake_steps, monkeypatch):
    def crop(pdf, element, **kw):
        if element.id == "p1-e2":
            raise RuntimeError("SECRET-DOC-TEXT")
        return png()

    def spend_it(element, png, *, client, settings, context_text=None):
        from a11y_stem.core.budget import BudgetExceeded

        raise BudgetExceeded("job_usd", "cap")

    monkeypatch.setattr(layout, "crop", crop)
    monkeypatch.setattr(equation_step, "transcribe", spend_it)
    events = events_of(concurrency=1)
    equations = [(e.done, e.total) for e in events if e.stage == "equations"]
    assert equations[-1] == (3, 3)


def test_an_exception_from_the_receiver_stops_the_run(fake_steps):
    def stop(event: ProgressEvent) -> None:
        if event.stage == "equations" and event.done == 1:
            raise JobCancelled()

    with pytest.raises(JobCancelled):
        convert(stage_progress=stop)


# --------------------------------------------------------------------------- the worker


class StageConverter:
    """A fake `convert_pdf` that sends the events the real one sends (see above) and
    can stop part way, to look at the job row while it runs."""

    def __init__(self, env: WebEnv, events: list[ProgressEvent], pause_after: int | None = None):
        self.env = env
        self.events = events
        self.pause_after = pause_after
        self.seen: list[dict | None] = []

    def __call__(self, pdf, /, *, settings, client, progress=None, options=None,
                 stage_progress=None):  # fmt: skip
        assert stage_progress is not None
        for number, event in enumerate(self.events, start=1):
            stage_progress(event)
            if number == self.pause_after:
                self.seen.append(self.progress())
        return self.env.converter.output.model_copy(deep=True)

    def progress(self) -> dict | None:
        with session_scope(self.env.ctx.factory) as session:
            job = session.scalars(select(Job).where(Job.status == JobStatus.RUNNING)).first()
            return None if job is None else job.progress_json


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


def run_with(web, converter, **kwargs) -> str:
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    worker = worker_module.Worker(
        web.settings, web.ctx.factory, converter=converter,
        client_factory=lambda s, b: FakeVisionClient(budget=b), **kwargs,
    )  # fmt: skip
    assert worker.run_once() == job_id
    return job_id


EVENTS = [
    ProgressEvent(stage="layout"),
    ProgressEvent(stage="layout", done=1, total=1, counts={"equation": 3, "figure": 1}),
    ProgressEvent(stage="equations", done=0, total=3),
    ProgressEvent(stage="figures", done=0, total=1),
    ProgressEvent(stage="equations", done=1, total=3),
    ProgressEvent(stage="figures", done=1, total=1),
    ProgressEvent(stage="equations", done=2, total=3),
]


def test_the_worker_stores_the_latest_event_of_every_stage(web, monkeypatch):
    monkeypatch.setattr(worker_module, "PROGRESS_WRITE_S", 0.0)  # store every event
    converter = StageConverter(web, EVENTS, pause_after=len(EVENTS))
    run_with(web, converter)
    stored = converter.seen[0]
    assert stored == {
        "stages": {
            "layout": {"stage": "layout", "done": 1, "total": 1,
                       "counts": {"equation": 3, "figure": 1}},
            "equations": {"stage": "equations", "done": 2, "total": 3, "counts": {}},
            "figures": {"stage": "figures", "done": 1, "total": 1, "counts": {}},
        },
    }  # fmt: skip


def test_writes_are_throttled_but_new_and_finished_stages_are_stored_at_once(web, monkeypatch):
    monkeypatch.setattr(worker_module, "PROGRESS_WRITE_S", 3600.0)  # nothing on the clock
    converter = StageConverter(web, EVENTS, pause_after=len(EVENTS))
    run_with(web, converter)
    stages = converter.seen[0]["stages"]
    # "figures" 1 of 1 finished a stage and was stored at once, with the latest of the
    # others ("equations" 1 of 3); "equations" 2 of 3 waits for the clock.
    assert stages["figures"]["done"] == 1
    assert stages["equations"]["done"] == 1


def test_progress_is_cleared_when_a_job_is_requeued(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.status = JobStatus.RUNNING
        job.attempts = 1
        job.lease_until = utcnow() - timedelta(minutes=1)
        job.progress_json = {"stages": {"layout": {"stage": "layout", "done": 1, "total": 1}}}
    requeued, _ = web.worker().requeue_expired()
    assert requeued == 1
    assert web.job(job_id).progress_json is None


def test_a_cancelled_job_stops_at_its_next_event(web):
    """Deleting the job while it converts: the next event raises, the run stops."""
    deleted: list[bool] = []

    class Deleting(StageConverter):
        def __call__(self, pdf, /, **kw):
            stage_progress = kw["stage_progress"]
            stage_progress(EVENTS[0])
            with session_scope(web.ctx.factory) as session:
                session.query(Job).delete()
            try:
                for event in EVENTS[1:]:
                    stage_progress(event)
            except JobCancelled:
                deleted.append(True)
                raise
            return web.converter.output.model_copy(deep=True)

    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    worker = worker_module.Worker(
        web.settings, web.ctx.factory, converter=Deleting(web, EVENTS),
        client_factory=lambda s, b: FakeVisionClient(budget=b),
    )  # fmt: skip
    assert worker.run_once() == job_id
    assert deleted == [True]


def test_a_failing_progress_write_never_fails_the_conversion(web, monkeypatch, caplog):
    def broken(self, attempt, value):
        raise RuntimeError("database gone")

    monkeypatch.setattr(worker_module.Worker, "_write_progress", broken)
    job_id = run_with(web, StageConverter(web, EVENTS))
    assert web.job(job_id).status == JobStatus.DONE
