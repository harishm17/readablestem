"""Work that needs no model answer is done while the models are answering (T3-18): the text
pass and the heading decisions run once the steps are at work, and the table models are
loaded when the worker starts instead of inside the first job."""

from __future__ import annotations

from pathlib import Path

from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.models import OFFLINE_DEFAULTS, FakeVisionClient
from readablestem.pipeline import run, tables

PDF = Path(__file__).parent / "fixtures" / "layout" / "lecture_notes.pdf"


def settings() -> Settings:
    return Settings(_env_file=None, fake_models=True)  # type: ignore[call-arg]


def test_the_result_is_the_same_whichever_comes_first(monkeypatch):
    """The text pass adds links and languages to the elements and changes nothing a step
    reads, so running it after the steps are queued gives the document it always did."""
    budget = JobBudget(InMemorySpendGuard(100.0), max_usd=100.0, max_calls=1000)
    ordered = run.convert_pdf(
        PDF.read_bytes(),
        settings=settings(),
        client=FakeVisionClient(defaults=dict(OFFLINE_DEFAULTS), budget=budget),
        concurrency=1,
    )
    # the text pass first, as it was: run it inside the layout step's place
    original = run._process

    def text_first(run_, elements, page_list, cached, while_waiting=None):
        if while_waiting is not None:
            while_waiting()
        return original(run_, elements, page_list, cached, None)

    monkeypatch.setattr(run, "_process", text_first)
    budget2 = JobBudget(InMemorySpendGuard(100.0), max_usd=100.0, max_calls=1000)
    first = run.convert_pdf(
        PDF.read_bytes(),
        settings=settings(),
        client=FakeVisionClient(defaults=dict(OFFLINE_DEFAULTS), budget=budget2),
        concurrency=1,
    )

    def same(a, b):
        keep = {"stats", "html_checks"}
        return a.model_dump(mode="json", exclude=keep) == b.model_dump(mode="json", exclude=keep)

    assert same(ordered.result, first.result)
    assert ordered.result.html == first.result.html


def test_preload_loads_both_models_under_the_pdf_lock_and_never_raises(monkeypatch):
    from readablestem.pipeline import layout

    calls: list[str] = []
    monkeypatch.setattr(tables, "load_detector", lambda: calls.append("detector") or True)
    monkeypatch.setattr(tables, "_structurer", lambda: calls.append("structurer"))
    tables.preload()
    assert calls == ["detector", "structurer"]

    def broken() -> bool:
        raise RuntimeError("no wheel")

    monkeypatch.setattr(tables, "load_detector", broken)
    tables.preload()  # a model that cannot be loaded is found out by the first page that needs it
    # the lock is free again afterwards
    assert layout._LOCK.acquire(blocking=False)
    layout._LOCK.release()
