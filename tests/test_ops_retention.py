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
from readablestem.core.types import CallFact, NotFinished, StageFact, StepFacts
from readablestem.ops import aggregate, retention
from readablestem.ops.aggregate import aggregate_finished
from readablestem.ops.retention import PurgeStats, delete_jobs, purge_expired
from readablestem.web import db, step_facts

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
        gone = add_job(
            s, user, retain_until=EXPIRED, aggregated=True, blobs=2, edits=[("p1-e1", "edit")]
        )
        add_job(s, user, retain_until=EXPIRED, blobs=1)  # finished, never aggregated
        add_job(s, user, retain_until=EXPIRED, status=db.JobStatus.QUEUED, finished_at=None)
        keep = add_job(s, user, retain_until=KEPT, blobs=1, edits=[("p1-e1", "accept")])
        facts = StepFacts(
            calls=[CallFact(step="describe", page=1)],
            stages=[StageFact(stage="steps", seconds=1)],
            not_finished=[NotFinished(step="figure", reason="cap_reached", done=1, of=3)],
        )
        step_facts.keep(s, gone, facts)
        step_facts.keep(s, keep, facts)

    stats = purge_expired(factory, NOW)

    # The never-aggregated job is counted before it goes, so nothing is lost.
    assert stats == PurgeStats(jobs_deleted=3, blobs_deleted=3, unaggregated_deleted=0)
    assert counts(factory) == (1, 1, 1, 1)  # users are account data, not document data
    with db.session_scope(factory) as s:
        assert step_facts.kept(s, [gone, keep]) == {keep: facts}  # what a job took goes with it
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


def test_problem_reports_go_on_three_clocks_and_with_their_senders_account(factory):
    day = timedelta(days=1)
    with db.session_scope(factory) as s:
        sender, leaving = add_user(s).id, add_user(s, "leaving@example.org").id
        s.add_all(
            [  # (what it is) -> created, closed
                db.Report(from_user_id=sender, item_ref="open-young", created_at=NOW - 10 * day),
                db.Report(from_user_id=sender, item_ref="open-old", created_at=NOW - 181 * day),
                db.Report(
                    from_user_id=sender, item_ref="closed-recent", status="closed",
                    created_at=NOW - 60 * day, closed_at=NOW - 29 * day,
                ),
                db.Report(
                    from_user_id=sender, item_ref="closed-old", status="closed",
                    created_at=NOW - 60 * day, closed_at=NOW - 31 * day,
                ),
                db.Report(from_user_id=leaving, item_ref="leaving", created_at=NOW - day),
            ]
        )  # fmt: skip

    assert purge_expired(factory, NOW).reports_deleted == 2
    with db.session_scope(factory) as s:
        kept = {r.item_ref for r in s.query(db.Report)}
        assert kept == {"open-young", "closed-recent", "leaving"}
        retention.erase_user(s, leaving, NOW)
    with db.session_scope(factory) as s:
        assert {r.item_ref for r in s.query(db.Report)} == {"open-young", "closed-recent"}


# --------------------------------------------------------------------------- the class link
# WS27.2 (web/links.py): a live link keeps its document until the link's last day plus the window
# it goes back to, so it runs out on its own clock; removing the link gives the clock back now.


def _settings():
    from readablestem.config import Settings

    return Settings(_env_file=None)  # type: ignore[call-arg]


def _day_window(settings):
    from readablestem.web import retention as rules

    return rules.window(settings, "day")


def _published_copy(job_id: str) -> db.Blob:
    from readablestem.web import links

    return db.Blob(
        job_id=job_id,
        kind=links.PUBLISHED,
        element_id="v1",
        media_type="text/html",
        content=b"<p>x</p>",
    )


def _make(s, job_id, settings, *, closes_days: int, now=NOW, opens_days: int | None = None):
    """Make a class link on a stored job as the owner's POST does; returns the job and token."""
    from readablestem.web import links

    job = s.get(db.Job, job_id)
    token = links.make(
        s,
        job,
        settings,
        label="",
        opens=None if opens_days is None else (now + timedelta(days=opens_days)).date(),
        closes=(now + timedelta(days=closes_days)).date(),
        now=now,
    )
    return job, token


def test_a_class_link_keeps_the_document_past_its_last_day_on_the_document_own_clock(factory):
    from readablestem.web import links
    from readablestem.web.database import as_utc

    settings = _settings()
    with db.session_scope(factory) as s:
        job_id = add_job(s, add_user(s), retain_until=KEPT)
        job, token = _make(s, job_id, settings, closes_days=10)
        s.add(_published_copy(job_id))
        closing = links.held_until(s, job_id, NOW)
        # The document is kept until the link's last day plus the window it goes back to.
        assert as_utc(job.retain_until) == closing + _day_window(settings)

    # Past its ordinary day, and just after the link closed, the document is not expired and
    # nothing has to release it; only the published copy goes (and was never served: `find`).
    after = closing + timedelta(minutes=30)
    assert purge_expired(factory, NOW + timedelta(days=5)).jobs_deleted == 0
    assert purge_expired(factory, after).jobs_deleted == 0
    with db.session_scope(factory) as s:
        job = s.get(db.Job, job_id)
        assert as_utc(job.retain_until) == closing + _day_window(settings)  # not touched
        assert s.query(db.Blob).filter_by(kind=links.PUBLISHED).count() == 0
        assert links.find(s, links.hash_token(token), closing - timedelta(hours=1)) is not None
        assert links.find(s, links.hash_token(token), after) is None
    # And on its own clock it is purged like any other document.
    end = closing + _day_window(settings) + timedelta(minutes=1)
    assert purge_expired(factory, end).jobs_deleted == 1


def test_what_a_link_gives_a_document_in_each_case(factory):
    """Pause, a link not open yet, a replacing link, a document kept longer for another reason
    (7 days, 30 for `dev`): the document ends at the last day plus its window, or at the longer
    time it already had."""
    from readablestem.web import links
    from readablestem.web import retention as retention_rules
    from readablestem.web.database import as_utc

    settings = _settings()
    week = retention_rules.window(settings, "week")
    with db.session_scope(factory) as s:
        user = add_user(s)
        # Paused, and a link whose first day is ahead: still kept to the last day plus a day.
        job_id = add_job(s, user, retain_until=KEPT)
        job, _ = _make(s, job_id, settings, closes_days=10, opens_days=5)
        closing = links.held_until(s, job_id, NOW)
        assert links.pause(s, job, paused=True, now=NOW)
        assert as_utc(job.retain_until) == closing + _day_window(settings)
        # A new link replaces the old one: the document follows the new last day.
        _make(s, job_id, settings, closes_days=3)
        replaced = links.held_until(s, job_id, NOW)
        assert replaced < closing
        assert as_utc(job.retain_until) == replaced + _day_window(settings)
        # Already kept longer for another reason: a link never shortens it, and removing the
        # link does not either.
        longer = add_job(s, user, retain_until=NOW + timedelta(days=60))
        job2, _ = _make(s, longer, settings, closes_days=5)
        assert as_utc(job2.retain_until) == NOW + timedelta(days=60)
        assert links.remove(s, job2, settings, NOW + timedelta(days=1))
        assert as_utc(job2.retain_until) == NOW + timedelta(days=60)
        # "Keep for 7 days" while a link is open: the week counts from the link's last day.
        weekly = add_job(s, user, retain_until=KEPT)
        job3, _ = _make(s, weekly, settings, closes_days=10)
        retention_rules.keep_seven_more_days(job3, NOW + timedelta(days=1))
        links.extend(s, job3, settings, NOW + timedelta(days=1))
        assert as_utc(job3.retain_until) == links.held_until(s, weekly, NOW) + week
        # `dev`: its 30 days from upload stand when the link closes sooner; a link that outlasts
        # them keeps it a day past its last day.
        dev = add_job(s, user, retain_until=NOW)
        job4 = s.get(db.Job, dev)
        job4.retention_mode = "dev"
        thirty = as_utc(job4.created_at) + timedelta(days=30)
        job4.retain_until = thirty
        for closes_days, expected in ((2, thirty), (60, None)):
            links.make(
                s,
                job4,
                settings,
                label="",
                opens=None,
                closes=(NOW + timedelta(days=closes_days)).date(),
                now=NOW,
            )
            expected = expected or links.held_until(s, dev, NOW) + _day_window(settings)
            assert as_utc(job4.retain_until) == expected


def test_removing_a_link_goes_back_to_earlier_retention_and_old_rows_go_after_30_days(factory):
    from readablestem.web import links
    from readablestem.web.database import as_utc

    settings = _settings()
    removed = NOW + timedelta(days=1)
    with db.session_scope(factory) as s:
        user = add_user(s)
        job_id = add_job(s, user, retain_until=KEPT)
        job = s.get(db.Job, job_id)
        links.make(
            s,
            job,
            settings,
            label="",
            opens=None,
            closes=(NOW + timedelta(days=20)).date(),
            now=NOW,
        )
        s.add(_published_copy(job_id))
        assert as_utc(job.retain_until) > NOW + timedelta(days=19)
        assert links.remove(s, job, settings, removed)
        assert as_utc(job.retain_until) == removed + _day_window(settings)
        assert s.query(db.Blob).filter_by(kind=links.PUBLISHED).count() == 0
        assert s.query(db.JobLink).count() == 1
    # A removed link's row stays 30 days (so the owner can still read its count), then goes,
    # while the document it belonged to is still there.
    with db.session_scope(factory) as s:
        long_job = add_job(s, user, retain_until=NOW + timedelta(days=90))
        for hash_, closed in (("a", 29), ("b", 31)):
            s.add(
                db.JobLink(
                    job_id=long_job,
                    token_hash=hash_ * 64,
                    closes_at=NOW - timedelta(days=closed),
                    created_at=NOW - timedelta(days=40),
                )
            )
    purge_expired(factory, NOW)
    with db.session_scope(factory) as s:
        assert [r.token_hash[0] for r in s.query(db.JobLink).filter_by(job_id=long_job)] == ["a"]


def test_every_delete_path_takes_the_links_and_the_published_copy(factory):
    with db.session_scope(factory) as s:
        job_id = add_job(s, add_user(s), retain_until=KEPT)
        s.add(_published_copy(job_id))
        s.add(db.JobLink(job_id=job_id, token_hash="g" * 64, closes_at=KEPT, created_at=NOW))
    with db.session_scope(factory) as s:
        assert delete_jobs(s, [job_id], NOW).jobs_deleted == 1
    with db.session_scope(factory) as s:
        assert (s.query(db.JobLink).count(), s.query(db.Blob).count()) == (0, 0)
