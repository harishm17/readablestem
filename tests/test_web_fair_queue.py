"""Round-robin claiming: people take turns, and everything else about the queue is as before.

`Worker.claim` orders by fewest attempts, then the person served least recently, then oldest
(`web/fair_queue.py`). The job page's "documents ahead of yours" replays that order, so
these tests also hold the two together."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from fixtures.web.support import ALICE, BOB, WebEnv, make_pdf
from readablestem.web.db import Blob, Job, User, session_scope, utcnow
from readablestem.web.fair_queue import Queued, queue_order
from readablestem.web.views import queue_ahead
from readablestem.web.worker import MAX_ATTEMPTS

CAROL = "carol@example.org"


@pytest.fixture
def env(tmp_path):
    env = WebEnv(tmp_path)
    with env.running():
        for email in (ALICE, BOB, CAROL):
            env.allow(email)
        yield env


def queue(env, email: str, n: int = 1, *, first_age_s: float = 0.0) -> list[str]:
    """`n` queued jobs of `email`, one second apart, the first one `first_age_s` old."""
    ids = []
    with session_scope(env.ctx.factory) as session:
        user = session.scalar(select(User).where(User.email == email))
        assert user is not None
        start = utcnow() - timedelta(seconds=first_age_s)
        for i in range(n):
            created = start + timedelta(seconds=i)
            job = Job(
                user_id=user.id,
                filename="f.pdf",
                page_count=1,
                retain_until=created + timedelta(hours=24),
                created_at=created,
            )
            session.add(job)
            session.flush()
            session.add(
                Blob(
                    job_id=job.id, kind="upload", media_type="application/pdf", content=make_pdf(1)
                )
            )
            ids.append(job.id)
    return ids


def claims(env, count: int, *, drained: bool = True) -> list[str]:
    worker = env.worker()
    out = []
    for _ in range(count):
        claim = worker.claim()
        assert claim is not None
        out.append(claim.job_id)
    assert not drained or worker.claim() is None
    return out


def test_one_persons_ten_documents_do_not_starve_anothers_one(env):
    many = queue(env, ALICE, 10, first_age_s=120)
    [single] = queue(env, BOB)  # queued after all ten
    order = claims(env, 11)
    assert order.index(single) == 1  # after one of Alice's, not after all ten
    assert [j for j in order if j != single] == many  # her own order is unchanged


def test_people_alternate_while_both_have_work(env):
    a = queue(env, ALICE, 3, first_age_s=300)
    b = queue(env, BOB, 3, first_age_s=100)
    c = queue(env, CAROL, 1)
    assert claims(env, 7) == [a[0], b[0], c[0], a[1], b[1], a[2], b[2]]


def test_someone_served_recently_waits_for_someone_never_served(env):
    [first] = queue(env, ALICE, first_age_s=60)
    assert claims(env, 1) == [first]  # Alice's job starts now (and is no longer queued)
    [later] = queue(env, ALICE)
    [other] = queue(env, BOB)  # newer than Alice's, but Bob has not been served
    assert claims(env, 2) == [other, later]


def test_the_person_served_longest_ago_goes_first(env):
    old, recent = queue(env, ALICE), queue(env, BOB)
    now = utcnow()
    with session_scope(env.ctx.factory) as session:
        for email, ago in ((ALICE, 10), (BOB, 3600)):  # earlier jobs that already ran
            user = session.scalar(select(User).where(User.email == email))
            assert user is not None
            session.add(
                Job(
                    user_id=user.id,
                    filename="done.pdf",
                    page_count=1,
                    status="done",
                    retain_until=now + timedelta(hours=1),
                    started_at=now - timedelta(seconds=ago),
                )
            )
    assert claims(env, 2) == [recent[0], old[0]]


def test_a_single_person_is_served_oldest_first_as_before(env):
    ids = queue(env, ALICE, 5, first_age_s=50)
    assert claims(env, 5) == ids


def test_a_single_person_stays_in_order_after_earlier_work_ran(env):
    ids = queue(env, ALICE, 4, first_age_s=50)
    assert claims(env, 1, drained=False) == ids[:1]
    assert claims(env, 3) == ids[1:]


def test_retried_jobs_still_go_behind_fresh_ones_and_take_turns_among_themselves(env):
    a_retry, b_retry = queue(env, ALICE, first_age_s=500), queue(env, BOB, first_age_s=400)
    a_extra = queue(env, ALICE, first_age_s=390)
    fresh_b = queue(env, BOB, first_age_s=5)
    with session_scope(env.ctx.factory) as session:
        for job_id in (*a_retry, *b_retry, *a_extra):
            job = session.get(Job, job_id)
            assert job is not None
            job.attempts = 1  # requeued after a lost lease, or retried by its owner
    # Fresh work first (Bob's, although Alice's retried jobs are far older), then the
    # retried ones, who take turns: Alice's oldest, Bob's, Alice's second.
    assert claims(env, 4) == [fresh_b[0], a_retry[0], b_retry[0], a_extra[0]]


def test_an_expired_lease_requeues_the_job_and_it_keeps_its_place_in_the_turns(env):
    [lost] = queue(env, ALICE, first_age_s=100)
    [bobs] = queue(env, BOB, first_age_s=50)
    worker = env.worker(lease_s=60)
    now = utcnow()
    claim = worker.claim(now=now)
    assert claim is not None and claim.job_id == lost
    assert worker.requeue_expired(now=now + timedelta(seconds=61)) == (1, 0)
    # attempts = 1 now: behind Bob's fresh one, whatever the turns say.
    assert claims(env, 2) == [bobs, lost]
    assert MAX_ATTEMPTS > 1


def test_job_page_position_matches_what_the_worker_will_do(env):
    a = queue(env, ALICE, 3, first_age_s=300)
    b = queue(env, BOB, 2, first_age_s=100)
    c = queue(env, CAROL, 1)
    with session_scope(env.ctx.factory) as session:
        ahead = {j: queue_ahead(session, session.get(Job, j)) for j in (*a, *b, *c)}  # type: ignore[arg-type]
    expected = [a[0], b[0], c[0], a[1], b[1], a[2]]
    assert [j for j, _ in sorted(ahead.items(), key=lambda kv: kv[1])] == expected
    assert sorted(ahead.values()) == [0, 1, 2, 3, 4, 5]
    assert claims(env, 6) == expected


def test_queue_order_is_a_pure_replay_of_the_claims():
    t0 = utcnow()

    def job(i: str, user: str, attempts: int, seconds: int) -> Queued:
        return Queued(i, user, attempts, t0 + timedelta(seconds=seconds))

    queued = [job("a1", "a", 0, 0), job("a2", "a", 0, 1), job("b1", "b", 0, 2), job("r", "c", 1, 3)]
    served = {"a": t0 - timedelta(seconds=5)}
    ordered = [q.id for q in queue_order(queued, served)]
    assert ordered == ["b1", "a1", "a2", "r"]  # b never served; the retry last
    assert [q.id for q in queue_order([], {})] == []
