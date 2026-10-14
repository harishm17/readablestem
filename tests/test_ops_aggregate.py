"""ops.aggregate: category counts, idempotence, privacy of the aggregate table (SQLite)."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from fixtures.ops.seed import (
    EXPECTED_DONE_COUNTS,
    FINISHED,
    NOW,
    SENTENCE,
    SENTINEL,
    add_job,
    add_user,
    aggregate_rows,
    review,
    sentinel_result,
)
from readablestem.config import CHEAP_MODEL, MAIN_MODEL
from readablestem.core.types import Check, Fix, HeadingFix, Kind, ModelCall, Status
from readablestem.ops import aggregate
from readablestem.ops.aggregate import (
    Reviewed,
    aggregate_finished,
    code,
    fix_counts,
    job_counts,
    utc_day,
)
from readablestem.web import db


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


def test_reviews_before_aggregation_do_not_change_the_pipeline_counts(factory):
    """Reviewers act minutes after a job finishes and the cron is hourly. A review
    rewrites the stored result (status; an edit replaces the checks), so aggregation
    must count the fixes as the pipeline left them."""
    with db.session_scope(factory) as s:
        job_id = add_job(s, add_user(s), result=sentinel_result())
        review(s, job_id, "p1-e1", "accept", at=NOW)  # needs_review -> accepted
        review(s, job_id, "p1-e1", "reject", at=NOW + timedelta(minutes=1))
        review(
            s,
            job_id,
            "p2-e3",  # needs_review, its failed check replaced by the reviewer's
            "edit",
            checks=[Check(name="latex.length", passed=False, code="too_long")],
            at=NOW + timedelta(minutes=2),
        )
        review(s, job_id, "p2-e1", "reject", at=NOW)  # accepted -> rejected

    assert aggregate_finished(factory, NOW + timedelta(hours=1)) == 1

    expected = dict(EXPECTED_DONE_COUNTS)
    del expected[("equation", "check:latex.allowed_commands:other")]  # p2-e3's, replaced
    expected[("equation", "aggregate:checks_edited")] = 1
    del expected[("equation", "tier:required:other")]  # its reason was that check
    expected[("equation", "tier:required:checks_edited")] = 1
    with db.session_scope(factory) as s:
        rows = aggregate_rows(s)
    assert rows == expected
    assert rows[("equation", "status:needs_review")] == 2
    assert not any(":edited" in c or ":rejected" in c or "too_long" in c for _, c in rows)


def test_an_edited_heading_keeps_its_pipeline_checks_in_the_counts():
    """A heading edit only changes the level (web/review.py), so its failed checks are the
    pipeline's and are counted, not lost as "aggregate:checks_edited"."""
    heading = Fix(
        element_id="p1-e0",
        kind=Kind.HEADING,
        status=Status.EDITED,
        heading=HeadingFix(level=2, text="x"),
        checks=[
            Check(name="heading.level_repaired", passed=False, severity="warning", code="skip")
        ],
    )
    reviewed = Reviewed(status={"p1-e0": "needs_review"}, edited={"p1-e0"})
    counts = fix_counts(heading, reviewed)
    assert counts[("heading", "check:heading.level_repaired:skip")] == 1
    assert counts[("heading", "aggregate:checks_edited")] == 0
    assert counts[("heading", "status:needs_review")] == 1


def test_checks_recorded_in_the_before_snapshot_are_counted_instead_of_lost():
    equation = Fix(
        element_id="p1-e1",
        kind=Kind.EQUATION,
        status=Status.EDITED,
        checks=[Check(name="latex.length", passed=False, code="too_long")],  # the reviewer's
    )
    original = [Check(name="latex.allowed_commands", passed=False, code="unknown_command")]
    reviewed = Reviewed(
        status={"p1-e1": "needs_review"}, edited={"p1-e1"}, checks={"p1-e1": original}
    )
    counts = fix_counts(equation, reviewed)
    assert counts[("equation", "check:latex.allowed_commands:unknown_command")] == 1
    assert counts[("equation", "check:latex.length:too_long")] == 0
    assert counts[("equation", "aggregate:checks_edited")] == 0


def test_failed_html_checks_cost_and_latency_are_counted_for_done_jobs():
    result = sentinel_result()
    result.html_checks = [
        Check(name="heading.order", passed=False, code="skipped_level"),
        Check(name="img.alt", passed=True),
    ]
    result.stats.latency_ms = 130_000
    counts = job_counts("done", "budget_daily", 2, result.model_dump(mode="json"))
    assert counts[("document", "check:heading.order:skipped_level")] == 1
    assert counts[("job", "latency:lt300s")] == 1
    assert counts[("job", "cost_microusd")] == 6000
    assert counts[("job", "error:budget_daily")] == 1
    failed = job_counts("failed", "timeout", 5, None)
    assert ("job", "pages") not in failed  # pages converted, not pages uploaded


def test_review_snapshots_without_a_status_are_ignored(factory):
    with db.session_scope(factory) as s:
        job_id = add_job(s, add_user(s), result=sentinel_result(), edits=[("p1-e1", "accept")])
    aggregate_finished(factory, NOW)
    with db.session_scope(factory) as s:
        assert aggregate_rows(s) == EXPECTED_DONE_COUNTS
        assert s.get(db.Job, job_id).aggregated


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


def test_review_tiers_are_counted_and_a_heading_group_is_one_decision():
    from test_web_review_triage import triage_output

    counts = job_counts("done", None, 3, triage_output().result.model_dump(mode="json"))
    assert counts[("heading", "decisions:required")] == 1  # three headings, one decision
    assert counts[("equation", "decisions:required")] == 1
    assert counts[("heading", "tier:required")] == 3
    assert counts[("heading", "tier:required:heading.repeated_label")] == 3
    assert counts[("heading", "tier:optional")] == 1
    assert counts[("heading", "tier:optional:heading.weak_evidence")] == 1
    assert counts[("heading", "tier:none")] == 1
    assert counts[("equation", "tier:optional:style_cosmetic")] == 1
    assert counts[("equation", "tier:required:style_mismatch")] == 1
    assert counts[("equation", "tier:none")] == 1


def test_tiers_of_results_stored_before_tiers_come_from_the_pipeline_status():
    """A stored fix without `review`: its tier is what its pipeline status implied, even
    after a reviewer accepted it (the `before` snapshot of its first ReviewEdit)."""
    stored = sentinel_result().model_dump(mode="json")
    for fix in stored["fixes"]:
        del fix["review"], fix["group_key"], fix["group_size"]
        if fix["element_id"] == "p1-e1":
            fix["status"] = "accepted"  # a reviewer accepted it
    reviewed = Reviewed(status={"p1-e1": "needs_review"})
    counts = job_counts("done", None, 2, stored, reviewed)
    assert counts[("equation", "tier:required")] == 3
    assert counts[("equation", "decisions:required")] == 3
    assert counts[("figure", "tier:none")] == 1


def test_the_weekly_report_counts_table_decisions_and_every_reviewable_kind():
    from readablestem.core.triage import REVIEWABLE_KINDS
    from readablestem.core.types import DocumentResult, DocumentStats, Element
    from readablestem.ops import export, report

    assert set(report.STEPS) == {k.value for k in REVIEWABLE_KINDS}
    assert set(export.PROPOSAL_KEYS) == {k.value for k in REVIEWABLE_KINDS}
    elements = [
        Element(id="p1-e1", kind=Kind.TABLE, page=0, bbox=(0, 0, 100, 100), order=0),
        Element(id="p1-e2", kind=Kind.EQUATION, page=0, bbox=(0, 0, 10, 10), order=1),
    ]
    fixes = [
        Fix(element_id="p1-e1", kind=Kind.TABLE, status=Status.FAILED,
            reason="table_not_rebuilt", review="required"),
        Fix(element_id="p1-e2", kind=Kind.EQUATION, status=Status.NEEDS_REVIEW,
            review="required"),
    ]  # fmt: skip
    result = DocumentResult(
        pipeline_version="0.4.0", elements=elements, fixes=fixes,
        stats=DocumentStats(pages=1, cost_usd=0.0, latency_ms=1, model_calls=0),
    )  # fmt: skip
    counts = job_counts("done", None, 1, result.model_dump(mode="json"))
    text = report.render_report(counts, since=date(2026, 10, 1), until=date(2026, 10, 7))
    load = text[text.index("Review load") :].split("\n\n", 1)[0]
    assert "required decisions    2" in load
    assert "table: required     1" in load and "equation: required  1" in load
