"""Live progress by stage: the events `convert_pdf` sends, and how the worker stores them.

Layout and the steps are fakes (as in test_content_run.py); the worker tests use the web
test app with a converter that sends events like the real one.
"""

from __future__ import annotations

import pymupdf
import pytest
from sqlalchemy import select

from fixtures.web.support import ALICE, WebEnv, job_id_from, login, make_pdf, upload
from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import Element, Fix, Kind, ProgressEvent, Status
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import equation_step, figure_step, layout, run
from readablestem.pipeline.layout import Layout
from readablestem.web import worker as worker_module
from readablestem.web.db import Job, JobStatus, session_scope
from readablestem.web.worker import JobCancelled


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


def test_a_scanned_page_counts_as_done_before_any_model_work(fake_steps, monkeypatch):
    two_pages = document()
    two_pages.elements = [e for e in two_pages.elements if e.page == 0]
    two_pages.kept_pages = {1: "scanned_page"}  # layout keeps page 2 as its image
    monkeypatch.setattr(layout, "analyze", lambda pdf, *, pages=None, max_pages=None: two_pages)
    events = events_of()
    figures = [(e.done, e.total) for e in events if e.stage == "figures"]
    assert figures[0] == (1, 2)  # the scanned page's figure is decided already
    assert figures[-1] == (2, 2)


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
