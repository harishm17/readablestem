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
