"""ops.retention: purge cascade, counting before deletion (purge and "delete now"), review
counts, cron ordering (SQLite)."""

from __future__ import annotations

from datetime import timedelta

import pytest

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
from readablestem.ops import aggregate, retention
from readablestem.ops.aggregate import aggregate_finished
from readablestem.ops.retention import PurgeStats, delete_jobs, purge_expired
from readablestem.web import db

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
