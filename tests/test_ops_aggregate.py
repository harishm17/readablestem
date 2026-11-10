"""ops.aggregate: category counts, idempotence, privacy of the aggregate table (SQLite)."""

from __future__ import annotations

import pytest

from fixtures.ops.seed import (
    EXPECTED_DONE_COUNTS,
    NOW,
    SENTENCE,
    SENTINEL,
    add_job,
    add_user,
    aggregate_rows,
    sentinel_result,
)
from readablestem.ops.aggregate import (
    aggregate_finished,
    code,
    job_counts,
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


def test_invalid_result_is_counted_not_quoted():
    counts = job_counts("done", None, 1, {"fixes": SENTINEL})
    assert counts[("job", "aggregate:invalid_result")] == 1
    assert counts[("job", "status:done")] == 1


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
