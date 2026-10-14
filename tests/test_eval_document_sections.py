"""The document task converts sections one after the other, or several at once (a live run
waiting on a hosted model); either way the items are the same and in section order."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from a11y_stem.config import Settings
from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
from a11y_stem.eval import pipeline_eval as pe
from a11y_stem.models import OFFLINE_DEFAULTS, FakeVisionClient

LAYOUT = Path(__file__).resolve().parent / "fixtures" / "layout"
PDFS = [
    (LAYOUT / name).read_bytes()
    for name in ("lecture_notes.pdf", "worked_example.pdf", "two_column.pdf")
]


def units(n: int) -> list[list[dict]]:
    return [
        [{"book_slug": "book", "page_slug": f"s{i}", "kind": "paragraph", "id": f"b/s{i}-0"}]
        for i in range(n)
    ]


def config(client, *, workers: int, budget=None) -> pe.EvalConfig:
    return pe.EvalConfig(
        task="document",
        settings=Settings(_env_file=None, fake_models=True),  # type: ignore[call-arg]
        client=client,
        media=None,  # type: ignore[arg-type]
        date="2026-10-14",
        gold_dir=Path("."),
        concurrency=2,
        section_workers=workers,
        budget=budget,
    )


def client(latency: float = 0.0) -> FakeVisionClient:
    class Slow(FakeVisionClient):
        def run(self, request, schema, *, is_fallback=False):
            time.sleep(latency)
            return super().run(request, schema, is_fallback=is_fallback)

    return Slow(defaults=dict(OFFLINE_DEFAULTS))


def without_time(items: list[dict]) -> list[dict]:
    return [{k: v for k, v in item.items() if k != "latency_ms"} for item in items]


def test_sections_converted_together_give_the_same_items_in_the_same_order():
    sections = 6
    pdfs = [PDFS[i % 3] for i in range(sections)]
    serial, serial_stop = pe._eval_document(config(client(), workers=1), units(sections), pdfs)
    together, together_stop = pe._eval_document(config(client(), workers=4), units(sections), pdfs)
    assert serial_stop is None and together_stop is None
    assert [i["id"] for i in together] == [f"book/s{i}" for i in range(sections)]
    assert without_time(together) == without_time(serial)


def test_one_worker_is_the_default_and_means_one_after_the_other():
    assert pe.EvalConfig.__dataclass_fields__["section_workers"].default == 1
    started: list[str] = []
    running = [0]
    most = [0]
    lock = threading.Lock()
    original = pe._document_section

    def watched(cfg, page, pdf):
        with lock:
            running[0] += 1
            most[0] = max(most[0], running[0])
            started.append(page[0]["page_slug"])
        try:
            time.sleep(0.02)
            return original(cfg, page, pdf)
        finally:
            with lock:
                running[0] -= 1

    pe._document_section = watched  # ty: ignore[invalid-assignment]
    try:
        pe._eval_document(config(client(), workers=1), units(3), PDFS)
        assert most[0] == 1 and started == ["s0", "s1", "s2"]
        pe._eval_document(config(client(), workers=3), units(3), PDFS)
        assert most[0] == 3  # all three at once
    finally:
        pe._document_section = original  # ty: ignore[invalid-assignment]


def test_a_budget_stop_ends_the_run_at_the_first_section_it_stopped():
    """Whatever the order they finished in: the items before the stopped section, and its
    reason; the sections after it are not reported."""
    original = pe._document_section

    def stopping(cfg, page, pdf):
        if page[0]["page_slug"] == "s2":
            return None, "budget_job_usd"
        return original(cfg, page, pdf)

    pe._document_section = stopping  # ty: ignore[invalid-assignment]
    try:
        for workers in (1, 4):
            items, stopped = pe._eval_document(
                config(client(), workers=workers), units(5), [PDFS[i % 3] for i in range(5)]
            )
            assert [i["id"] for i in items] == ["book/s0", "book/s1"], workers
            assert stopped == "budget_job_usd"
    finally:
        pe._document_section = original  # ty: ignore[invalid-assignment]


def test_a_cap_that_raises_in_a_section_is_a_stop_too():
    budget = JobBudget(InMemorySpendGuard(cap_usd=0.0), max_usd=0.0, max_calls=10)
    fake = FakeVisionClient(defaults=dict(OFFLINE_DEFAULTS), budget=budget)
    for workers in (1, 3):
        items, stopped = pe._eval_document(
            config(fake, workers=workers, budget=budget), units(3), PDFS
        )
        assert stopped is not None and stopped.startswith("budget_"), workers
        assert items == []


def test_several_sections_at_once_take_less_time_than_one_after_the_other():
    sections = 4
    pdfs = [PDFS[1]] * sections  # the same small page, so the model wait dominates
    slow = 0.15

    def timed(workers: int) -> float:
        started = time.perf_counter()
        pe._eval_document(config(client(slow), workers=workers), units(sections), pdfs)
        return time.perf_counter() - started

    one, four = timed(1), timed(4)
    assert four < 0.75 * one, (one, four)
