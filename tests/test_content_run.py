"""run.convert_pdf and the content features: options in force for every step (pool
threads too), TABLE elements through the table step, and the new result fields.

Layout and the equation and figure steps are fakes; the table step and the assembler
are the real ones.
"""

from __future__ import annotations

import threading

import pymupdf
import pytest

from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import (
    ConversionOptions,
    Element,
    FigureFix,
    Fix,
    Kind,
    NotationEntry,
    Status,
)
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import equation_step, figure_step, layout, options, run, table_step
from readablestem.pipeline.assemble_tables import TABLE_NOT_CONVERTED_ALT
from readablestem.pipeline.layout import Layout


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def png() -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), False)
    pixmap.set_rect(pixmap.irect, (30, 30, 30))
    return pixmap.tobytes("png")


def el(eid: str, kind: Kind, order: int, **kw) -> Element:
    return Element(id=eid, kind=kind, page=0, bbox=(72, 72, 272, 172), order=order, **kw)


def document() -> Layout:
    elements = [
        el("p1-e0", Kind.PARAGRAPH, 0, text="Measured speeds are below."),
        el("p1-e1", Kind.TABLE, 1, caption="Table 1 Speeds."),
        el("p1-e2", Kind.PARAGRAPH, 2, text="The speed doubles."),
        el("p1-e3", Kind.EQUATION, 3, display="block"),
        el("p1-e4", Kind.FIGURE, 4, caption="Figure 1 A cart."),
    ]
    return Layout(elements=elements, page_count=1, pages=[0], title="Speeds", lang="en")


class Recorder:
    """Fake steps that note the options in force and the thread they ran on."""

    def __init__(self) -> None:
        self.seen: dict[str, ConversionOptions] = {}
        self.threads: dict[str, str] = {}
        self.contexts: dict[str, str | None] = {}
        self._lock = threading.Lock()

    def note(self, element: Element, context_text: str | None) -> None:
        with self._lock:
            self.seen[element.id] = options.current()
            self.threads[element.id] = threading.current_thread().name
            self.contexts[element.id] = context_text

    def transcribe(self, element, png, *, client, settings, context_text=None) -> Fix:
        self.note(element, context_text)
        return Fix(element_id=element.id, kind=element.kind, status=Status.ABSTAINED,
                   reason="low_confidence")  # fmt: skip

    def describe(self, element, png, *, client, settings, context_text=None) -> Fix:
        self.note(element, context_text)
        return Fix(
            element_id=element.id,
            kind=element.kind,
            status=Status.ACCEPTED,
            confidence=0.9,
            figure=FigureFix(figure_type="diagram", alt="A cart on a ramp."),
        )

    def rebuild(self, element, png, *, client, settings, context_text=None) -> Fix:
        self.note(element, context_text)
        return REAL_REBUILD(element, png, client=client, settings=settings, context_text=None)


REAL_REBUILD = table_step.rebuild


@pytest.fixture
def recorder(monkeypatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(layout, "analyze", lambda pdf, *, pages=None, max_pages=None: document())
    monkeypatch.setattr(layout, "crop", lambda pdf, element, **kw: png())
    monkeypatch.setattr(equation_step, "transcribe", rec.transcribe)
    monkeypatch.setattr(figure_step, "describe", rec.describe)
    monkeypatch.setattr(table_step, "rebuild", rec.rebuild)
    return rec


def convert(s: Settings | None = None, **kw):
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=1.0), max_usd=1.0, max_calls=100)
    return run.convert_pdf(
        b"%PDF-", settings=s or settings(), client=FakeVisionClient(budget=budget), **kw
    )


def test_options_default_to_the_settings_flags_in_every_step(recorder):
    convert(settings(feature_tables=True, feature_colour=True))
    assert set(recorder.seen) == {"p1-e1", "p1-e3", "p1-e4"}
    for seen in recorder.seen.values():
        assert seen == ConversionOptions(tables=True, colour=True)
    assert all(name.startswith("step") for name in recorder.threads.values())  # pool threads


def test_options_given_by_the_caller_reach_every_step(recorder):
    given = ConversionOptions(
        notation=[NotationEntry(symbol="bold d", canonical="\U0001d41d", meaning="vector")],
        notation_memory=True,
    )
    convert(settings(feature_tables=True), options=given)
    assert all(seen == given for seen in recorder.seen.values())
    assert options.current() == ConversionOptions()  # restored once the run is over


def test_options_are_restored_when_the_run_fails(recorder, monkeypatch):
    def boom(pdf, *, pages=None, max_pages=None):
        assert options.current().tables
        raise layout.LayoutError("unreadable", "cannot read")

    monkeypatch.setattr(layout, "analyze", boom)
    with pytest.raises(layout.LayoutError):
        convert(options=ConversionOptions(tables=True))
    assert options.current() == ConversionOptions()


def test_a_table_goes_to_the_table_step_and_is_kept_as_an_image(recorder):
    output = convert()
    result = output.result
    fix = result.fix_for("p1-e1")
    assert fix is not None and fix.kind is Kind.TABLE
    assert (fix.status, fix.reason, fix.review) == (Status.FAILED, "table_not_rebuilt", "required")
    assert fix.calls == []  # decided before any model work
    assert "p1-e1" in output.crops
    assert f'alt="{TABLE_NOT_CONVERTED_ALT}"' in result.html
    assert 'data-reason="table_not_rebuilt"' in result.html
    assert "<figcaption>Table 1 Speeds.</figcaption>" in result.html
    assert result.stats.by_kind["table"] == 1
    # The table and the abstained equation; the accepted figure needs nothing.
    assert result.stats.required_decisions == 2
    assert {f.element_id for f in result.fixes if f.review == "required"} == {"p1-e1", "p1-e3"}


def test_every_kind_that_asks_for_a_decision_is_on_the_review_screen():
    """A required decision the review screen cannot show would keep a job from ever
    being done (the job page counts every pending fix)."""
    from readablestem.web.review import GROUP_KINDS

    asking = {Kind.EQUATION, Kind.FIGURE, Kind.HEADING, Kind.TABLE}
    assert asking <= set(GROUP_KINDS)
