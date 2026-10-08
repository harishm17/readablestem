"""ops.retention: purge cascade, counting before deletion (purge and "delete now"), review
counts, cron ordering (SQLite)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine

from a11y_stem.ops import aggregate, retention
from a11y_stem.ops.aggregate import aggregate_finished
from a11y_stem.ops.retention import PurgeStats, aggregate_and_purge, delete_jobs, purge_expired
from a11y_stem.web import db
from fixtures.ops.seed import (
    EXPECTED_DONE_COUNTS,
    NOW,
    SENTINEL,
    add_job,
    add_user,
    aggregate_rows,
    review,
    sentinel_result,
)

EXPIRED = NOW - timedelta(minutes=1)
KEPT = NOW + timedelta(hours=1)


@pytest.fixture
def factory(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path}/ops.db")
    db.create_all(engine)
    yield db.make_sessionmaker(engine)
    engine.dispose()


def counts(factory) -> tuple[int, int, int, int]:
    with db.session_scope(factory) as s:
        return (
            s.query(db.Job).count(),
            s.query(db.Blob).count(),
            s.query(db.ReviewEdit).count(),
            s.query(db.User).count(),
        )


def test_purge_deletes_expired_jobs_with_everything_derived(factory):
    with db.session_scope(factory) as s:
        user = add_user(s)
        add_job(s, user, retain_until=EXPIRED, aggregated=True, blobs=2, edits=[("p1-e1", "edit")])
        add_job(s, user, retain_until=EXPIRED, blobs=1)  # finished, never aggregated
        add_job(s, user, retain_until=EXPIRED, status=db.JobStatus.QUEUED, finished_at=None)
        keep = add_job(s, user, retain_until=KEPT, blobs=1, edits=[("p1-e1", "accept")])

    stats = purge_expired(factory, NOW)

    # The never-aggregated job is counted before it goes, so nothing is lost.
    assert stats == PurgeStats(jobs_deleted=3, blobs_deleted=3, unaggregated_deleted=0)
    assert counts(factory) == (1, 1, 1, 1)  # users are account data, not document data
    with db.session_scope(factory) as s:
        assert s.query(db.Job).one().id == keep
        rows = aggregate_rows(s)
    assert rows[("job", "status:done")] == 1  # the queued job is not a finished one
    assert rows[("unknown", "review:edit")] == 1
    assert purge_expired(factory, NOW) == PurgeStats(0, 0, 0)


def test_purge_counts_unaggregated_jobs_before_deleting_them(factory):
    with db.session_scope(factory) as s:
        user = add_user(s)
        add_job(s, user, retain_until=EXPIRED, result=sentinel_result())
        add_job(s, user, retain_until=EXPIRED, status=db.JobStatus.FAILED, error_code="encrypted")
    stats = purge_expired(factory, NOW)
    assert stats.jobs_deleted == 2 and stats.unaggregated_deleted == 0
    expected = dict(EXPECTED_DONE_COUNTS)
    expected[("job", "status:failed")] = 1
    expected[("job", "error:encrypted")] = 1
    expected[("job", "pages")] += 2
    with db.session_scope(factory) as s:
        assert aggregate_rows(s) == expected


def test_purge_deletes_even_when_counting_fails_and_says_so(factory, monkeypatch, capsys):
    with db.session_scope(factory) as s:
        user = add_user(s)
        add_job(s, user, retain_until=EXPIRED, result=sentinel_result(), blobs=1)
        add_job(s, user, retain_until=EXPIRED, status=db.JobStatus.FAILED, error_code="encrypted")

    def broken(*_args, **_kwargs):
        raise RuntimeError(f"aggregation bug near {SENTINEL}")

    monkeypatch.setattr(aggregate, "job_counts", broken)
    stats = purge_expired(factory, NOW)
    assert stats == PurgeStats(jobs_deleted=2, blobs_deleted=1, unaggregated_deleted=2)
    assert counts(factory)[:3] == (0, 0, 0)
    with db.session_scope(factory) as s:
        assert s.query(db.ErrorAggregate).count() == 0  # rolled back with its savepoint
    out = capsys.readouterr().out
    assert "aggregate_on_delete_failed" in out and SENTINEL not in out


def test_delete_now_counts_the_job_and_its_reviews(factory):
    """The web app's "delete now": a user reviews, downloads and deletes within the hour,
    before the cron has aggregated the job. Its counts and review actions must survive."""
    with db.session_scope(factory) as s:
        user = add_user(s)
        job_id = add_job(s, user, result=sentinel_result(), retain_until=KEPT, blobs=2)
        review(s, job_id, "p1-e1", "accept")  # needs_review -> accepted in the result
        running = add_job(s, user, status=db.JobStatus.RUNNING, finished_at=None)

    with db.session_scope(factory) as s:
        stats = delete_jobs(s, [job_id, "no-such-job"], NOW)
    assert stats == PurgeStats(jobs_deleted=1, blobs_deleted=2, unaggregated_deleted=0)
    with db.session_scope(factory) as s:
        assert delete_jobs(s, [running], NOW) == PurgeStats(1, 0, 0)  # nothing to count
        assert delete_jobs(s, [], NOW) == PurgeStats(0, 0, 0)

    expected = dict(EXPECTED_DONE_COUNTS)
    expected[("equation", "review:accept")] = 1
    with db.session_scope(factory) as s:
        assert aggregate_rows(s) == expected  # p1-e1 still counted as needs_review
        assert s.query(db.Job).count() == 0
    assert aggregate_finished(factory, NOW) == 0  # nothing left to count twice


def test_delete_now_after_aggregation_counts_only_the_reviews(factory):
    with db.session_scope(factory) as s:
        job_id = add_job(s, add_user(s), result=sentinel_result())
    assert aggregate_finished(factory, NOW) == 1
    with db.session_scope(factory) as s:
        review(s, job_id, "p2-e1", "reject")
    with db.session_scope(factory) as s:
        delete_jobs(s, [job_id], NOW)
    expected = dict(EXPECTED_DONE_COUNTS)
    expected[("figure", "review:reject")] = 1
    with db.session_scope(factory) as s:
        assert aggregate_rows(s) == expected


def test_boundary_is_strictly_before_now(factory):
    with db.session_scope(factory) as s:
        add_job(s, add_user(s), retain_until=NOW)
    assert purge_expired(factory, NOW).jobs_deleted == 0
    assert purge_expired(factory, NOW + timedelta(microseconds=1)).jobs_deleted == 1


def test_children_are_deleted_even_without_foreign_key_enforcement(tmp_path):
    """A bare SQLite engine has no PRAGMA foreign_keys=ON, so no cascade: the purge must
    delete crops and edits itself."""
    engine = create_engine(f"sqlite:///{tmp_path}/nofk.db")
    db.create_all(engine)
    factory = db.make_sessionmaker(engine)
    with db.session_scope(factory) as s:
        add_job(s, add_user(s), retain_until=EXPIRED, blobs=3, edits=[("p1-e1", "edit")])
    assert purge_expired(factory, NOW).blobs_deleted == 3
    assert counts(factory) == (0, 0, 0, 1)
    engine.dispose()


def test_review_actions_are_counted_once_at_deletion(factory):
    with db.session_scope(factory) as s:
        add_job(
            s,
            add_user(s),
            retain_until=EXPIRED,
            aggregated=True,
            result=sentinel_result(),
            edits=[("p1-e1", "edit"), ("p1-e1", "reject"), ("p2-e1", "accept"), ("gone", "edit")],
        )
    purge_expired(factory, NOW)
    with db.session_scope(factory) as s:
        rows = s.query(db.ErrorAggregate).all()
        assert {r.day for r in rows} == {date(2026, 10, 6)}  # the job's completion day
        assert aggregate_rows(s) == {
            ("equation", "review:edit"): 1,
            ("equation", "review:reject"): 1,
            ("figure", "review:accept"): 1,
            ("unknown", "review:edit"): 1,
        }
        assert all(SENTINEL not in r.category for r in rows)


def test_review_count_failure_never_blocks_deletion(factory, monkeypatch, capsys):
    with db.session_scope(factory) as s:
        add_job(s, add_user(s), retain_until=EXPIRED, edits=[("p1-e1", "edit")])

    def broken(*_args, **_kwargs):
        raise RuntimeError(f"insert failed for {SENTINEL}")

    monkeypatch.setattr(retention, "upsert_counts", broken)
    assert purge_expired(factory, NOW).jobs_deleted == 1
    assert counts(factory)[:3] == (0, 0, 0)
    out = capsys.readouterr().out
    assert "review_count_failed" in out and "RuntimeError" in out
    assert SENTINEL not in out


def test_large_backlog_is_purged_in_batches(factory, monkeypatch):
    monkeypatch.setattr(retention, "BATCH_SIZE", 2)
    with db.session_scope(factory) as s:
        user = add_user(s)
        for _ in range(5):
            add_job(s, user, retain_until=EXPIRED, aggregated=True, blobs=1)
    assert purge_expired(factory, NOW) == PurgeStats(5, 5, 0)


def test_cron_aggregates_before_purging(factory):
    with db.session_scope(factory) as s:
        add_job(s, add_user(s), retain_until=EXPIRED, result=sentinel_result(), blobs=1)
    aggregated, stats = aggregate_and_purge(factory, NOW)
    assert aggregated == 1
    assert stats == PurgeStats(jobs_deleted=1, blobs_deleted=1, unaggregated_deleted=0)
    with db.session_scope(factory) as s:
        assert aggregate_rows(s) == EXPECTED_DONE_COUNTS  # counted, then deleted


def test_cron_still_purges_when_aggregation_fails(factory, monkeypatch, capsys):
    with db.session_scope(factory) as s:
        add_job(s, add_user(s), retain_until=EXPIRED, blobs=1)

    def broken(*_args, **_kwargs):
        raise RuntimeError("aggregation bug")

    monkeypatch.setattr(aggregate, "job_counts", broken)  # the cron and the purge both fail
    aggregated, stats = aggregate_and_purge(factory, NOW)
    assert aggregated is None
    assert stats == PurgeStats(jobs_deleted=1, blobs_deleted=1, unaggregated_deleted=1)
    assert "aggregate_failed" in capsys.readouterr().out
