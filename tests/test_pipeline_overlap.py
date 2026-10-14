"""Work that needs no model answer is done while the models are answering (T3-18): the text
pass and the heading decisions run once the steps are at work, and the table models are
loaded when the worker starts instead of inside the first job."""

from __future__ import annotations

import threading
from pathlib import Path

from fixtures.web.support import WebEnv, fake_client_factory, make_settings
from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import Kind
from readablestem.models import OFFLINE_DEFAULTS, FakeVisionClient
from readablestem.pipeline import run, tables, text_pass
from readablestem.web.worker import Worker

PDF = Path(__file__).parent / "fixtures" / "layout" / "lecture_notes.pdf"


class Watching(FakeVisionClient):
    """Says when its first request arrives."""

    started: threading.Event

    def run(self, request, schema, *, is_fallback=False):
        self.started.set()
        return super().run(request, schema, is_fallback=is_fallback)


def settings() -> Settings:
    return Settings(_env_file=None, fake_models=True)  # type: ignore[call-arg]


def test_the_text_pass_runs_after_the_first_model_request_has_been_sent(monkeypatch):
    budget = JobBudget(InMemorySpendGuard(100.0), max_usd=100.0, max_calls=1000)
    client = Watching(defaults=dict(OFFLINE_DEFAULTS), budget=budget)
    client.started = threading.Event()
    seen: dict[str, bool] = {}
    real_apply = text_pass.apply

    def apply(*args, **kwargs):
        # Not waiting for the model would take the whole timeout and leave this False.
        seen["model_already_at_work"] = client.started.wait(timeout=10)
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(text_pass, "apply", apply)
    output = run.convert_pdf(PDF.read_bytes(), settings=settings(), client=client, concurrency=2)
    assert seen == {"model_already_at_work": True}
    # and the document is complete: every kind of fix is there, headings included
    kinds = {f.kind for f in output.result.fixes}
    assert Kind.EQUATION in kinds and Kind.HEADING in kinds
    assert output.result.lang_source in {"pdf", "detected", "default"}  # the text pass ran


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


def test_the_worker_loads_the_table_models_at_start_in_production_only(tmp_path, monkeypatch):
    loaded = threading.Event()
    monkeypatch.setattr(tables, "preload", loaded.set)

    def started(**overrides) -> bool:
        loaded.clear()
        env = WebEnv(tmp_path)
        settings = make_settings(tmp_path, **overrides)
        worker = Worker(
            settings,
            env.ctx.factory,
            converter=env.converter,
            client_factory=fake_client_factory,
        )
        worker.start()
        try:
            return loaded.wait(timeout=2)
        finally:
            worker.stop()

    assert started(env="prod", preload_table_models=True) is True
    assert started(env="prod", preload_table_models=False) is False
    assert started(env="test") is False  # tests and local runs do not pay for it


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
