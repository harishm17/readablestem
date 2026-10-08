"""ops.aggregate: category counts, idempotence, privacy of the aggregate table (SQLite)."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from a11y_stem.config import CHEAP_MODEL, MAIN_MODEL
from a11y_stem.core.types import Fix, Kind, ModelCall, Status
from a11y_stem.ops import aggregate
from a11y_stem.ops.aggregate import aggregate_finished, code, fix_counts, job_counts, utc_day
from a11y_stem.web import db
from fixtures.ops.seed import (
    EXPECTED_DONE_COUNTS,
    FINISHED,
    NOW,
    SENTENCE,
    SENTINEL,
    add_job,
    add_user,
    aggregate_rows,
    sentinel_result,
)


@pytest.fixture
def factory(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path}/ops.db")
    db.create_all(engine)
    yield db.make_sessionmaker(engine)
    engine.dispose()


def test_code_keeps_category_codes_and_rejects_text():
    assert code("unknown_command") == "unknown_command"
    assert code("gpt-6.1-sol") == "gpt-6.1-sol"
    assert code("LaTeX.Allowed_Commands") == "latex.allowed_commands"
    for text in (SENTENCE, r"\frac{a}{b}", "a@example.org", "", "x" * 49, None, 3):
        assert code(text) == "other"


def test_utc_day_converts_offsets_and_treats_naive_as_utc():
    fallback = datetime(2000, 1, 1, tzinfo=UTC)
    late_in_chicago = datetime(2026, 10, 6, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert utc_day(late_in_chicago, fallback) == date(2026, 10, 7)
    assert utc_day(datetime(2026, 10, 6, 23, 30), fallback) == date(2026, 10, 6)
    assert utc_day(None, fallback) == date(2000, 1, 1)


def test_fallback_and_escalation_are_told_apart():
    def fix(*calls: ModelCall) -> Fix:
        return Fix(element_id="e", kind=Kind.EQUATION, status=Status.ACCEPTED, calls=list(calls))

    def c(model: str, *, fallback: bool = False) -> ModelCall:
        return ModelCall(step="equation", model=model, prompt_version="v", is_fallback=fallback)

    escalated = fix_counts(fix(c(CHEAP_MODEL), c(MAIN_MODEL)))
    assert escalated[("equation", "call:escalated")] == 1
    assert escalated[("equation", "call:fallback")] == 0
    fell_back = fix_counts(fix(c(CHEAP_MODEL), c(MAIN_MODEL, fallback=True)))
    assert fell_back[("equation", "call:fallback")] == 1
    assert fell_back[("equation", "call:escalated")] == 0
    assert fix_counts(fix(c(MAIN_MODEL)))[("equation", "call:escalated")] == 0


def test_job_counts_match_the_documented_categories():
    counts = job_counts("done", None, 2, sentinel_result().model_dump(mode="json"))
    assert dict(counts) == EXPECTED_DONE_COUNTS


def test_invalid_result_is_counted_not_quoted():
    counts = job_counts("done", None, 1, {"fixes": SENTINEL})
    assert counts[("job", "aggregate:invalid_result")] == 1
    assert counts[("job", "status:done")] == 1


def test_aggregates_finished_jobs_by_completion_day(factory):
    with db.session_scope(factory) as s:
        user = add_user(s)
        add_job(s, user, result=sentinel_result())
        add_job(
            s,
            user,
            status=db.JobStatus.FAILED,
            error_code="encrypted",
            page_count=0,
            finished_at=FINISHED + timedelta(days=1),
        )
        add_job(s, user, status=db.JobStatus.RUNNING, finished_at=None)  # not finished
        add_job(s, user, status=db.JobStatus.QUEUED, finished_at=None)

    assert aggregate_finished(factory, NOW) == 2

    with db.session_scope(factory) as s:
        rows = s.query(db.ErrorAggregate).all()
        by_day: dict[date, Counter] = {}
        for row in rows:
            by_day.setdefault(row.day, Counter())[(row.step, row.category)] += row.count
        assert dict(by_day[date(2026, 10, 6)]) == EXPECTED_DONE_COUNTS
        assert dict(by_day[date(2026, 10, 7)]) == {
            ("job", "status:failed"): 1,
            ("job", "error:encrypted"): 1,
        }
        statuses = {j.status: j.aggregated for j in s.query(db.Job).all()}
        assert statuses == {"done": True, "failed": True, "running": False, "queued": False}


def test_aggregation_is_idempotent_and_accumulates(factory):
    with db.session_scope(factory) as s:
        user = add_user(s)
        add_job(s, user, result=sentinel_result())
    assert aggregate_finished(factory, NOW) == 1
    assert aggregate_finished(factory, NOW) == 0  # already counted
    with db.session_scope(factory) as s:
        assert aggregate_rows(s) == EXPECTED_DONE_COUNTS
        add_job(s, add_user(s, "second@example.org"), result=sentinel_result())
    assert aggregate_finished(factory, NOW) == 1  # same day: rows are upserted, not duplicated
    with db.session_scope(factory) as s:
        assert aggregate_rows(s) == {k: 2 * v for k, v in EXPECTED_DONE_COUNTS.items()}
        assert s.query(db.ErrorAggregate).count() == len(EXPECTED_DONE_COUNTS)


def test_no_document_text_reaches_the_aggregate_table(factory):
    with db.session_scope(factory) as s:
        add_job(s, add_user(s), result=sentinel_result(), error_code=SENTENCE)
    aggregate_finished(factory, NOW)
    with db.session_scope(factory) as s:
        rows = s.query(db.ErrorAggregate).all()
        assert rows
        for row in rows:
            assert SENTINEL not in f"{row.step} {row.category}".lower()
            assert len(row.step) <= 32 and len(row.category) <= 64
        assert ("job", "error:other") in aggregate_rows(s)


def test_large_backlog_is_processed_in_batches(factory, monkeypatch):
    monkeypatch.setattr(aggregate, "BATCH_SIZE", 2)
    with db.session_scope(factory) as s:
        user = add_user(s)
        for _ in range(5):
            add_job(s, user, page_count=1)
    assert aggregate_finished(factory, NOW) == 5
    with db.session_scope(factory) as s:
        assert aggregate_rows(s) == {("job", "status:done"): 5, ("job", "pages"): 5}


def test_a_failed_batch_leaves_jobs_unaggregated(factory, monkeypatch):
    with db.session_scope(factory) as s:
        add_job(s, add_user(s))

    def broken(*_args, **_kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(aggregate, "upsert_counts", broken)
    with pytest.raises(RuntimeError):
        aggregate_finished(factory, NOW)
    with db.session_scope(factory) as s:
        assert s.query(db.Job).one().aggregated is False  # rolled back with the counts
        assert s.query(db.ErrorAggregate).count() == 0
