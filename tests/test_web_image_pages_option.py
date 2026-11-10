"""The upload option "Convert scanned or handwritten pages" (ADR 0007): who is offered it, what the
probe says to it, what a job that asked for it is allowed to spend, and that a job that did not ask
for it never converts such pages, whatever the deployment's flag.
"""

from __future__ import annotations

import pytest

from fixtures.web.support import (
    ALICE,
    WebEnv,
    login,
    make_pdf,
    token,
    upload,
)
from image_page_support import scan_pdf
from readablestem.config import Settings
from readablestem.core.types import ConversionOptions
from readablestem.ops.limits import largest_reservation_usd
from readablestem.web import access, notation, quota
from readablestem.web.db import Job, PendingUpload, User, session_scope
from readablestem.web.pdf_probe import inspect
from readablestem.web.worker import job_caps

OFFER = "Convert scanned or handwritten pages</label>"
IMAGE_REASONS = {"scanned_page", "handwritten_page"}


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def mixed_pdf() -> bytes:
    """A typed page and two scans."""
    return scan_pdf(2, typed_first=True)


@pytest.fixture
def offered(tmp_path):
    env = WebEnv(tmp_path, feature_image_pages=True, max_pages_per_job=5, max_source_pages=5)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


def job_of(response, env: WebEnv) -> Job:
    assert response.status_code == 303, response.text[:300]
    return env.job(response.headers["location"].rsplit("/", 1)[-1])


# ------------------------------------------------------------------------------ the probe


@pytest.mark.parametrize(
    "name",
    ["mixed_course.pdf", "ink_no_layer.pdf", "scan_ocr.pdf", "ios_notes.pdf", "typed_with_ink.pdf"],
)
def test_the_probe_counts_the_pages_layout_will_keep_as_scanned_or_handwritten(name):
    """Handwriting and scans, a hidden OCR or recognition layer or none, typed pages with ink on
    them: the probe's count is the number of pages the conversion would read from an image."""
    from pathlib import Path

    from readablestem.pipeline import layout

    data = (Path(__file__).parent / "fixtures" / "handwriting" / name).read_bytes()
    kept = [r for r in layout.analyze(data).kept_pages.values() if r in IMAGE_REASONS]
    assert inspect(data, max_pages=30, allow_image_pages=True)[1] == len(kept)


# ------------------------------------------------------------------------------ the form


# ------------------------------------------------------------------------------ the upload


def test_a_scan_is_refused_without_the_option_and_the_message_says_how_to_ask(offered):
    response = upload(offered.browser, scan_pdf())
    assert response.status_code == 422
    # With the option offered the refusal says the pages were not asked for, not that the
    # tool cannot convert them, and gives the route.
    assert "has only scanned pages, which are read only when you ask" in response.text
    assert "cannot convert it" not in response.text
    assert "Convert scanned or handwritten pages" in response.text
    assert "upload a PDF exported from the original" in " ".join(response.text.split())
    with session_scope(offered.ctx.factory) as session:
        assert session.query(Job).count() == 0  # refused: nothing was queued or counted


def test_with_the_option_a_scan_becomes_a_job_that_remembers_it(offered):
    job = job_of(upload(offered.browser, scan_pdf(2), image_pages=True), offered)
    assert job.image_pages is True and job.page_count == 2 and job.status == "queued"


def test_a_mixed_pdf_with_the_option_converts_and_without_it_keeps_the_scans_as_images(offered):
    asked = job_of(upload(offered.browser, mixed_pdf(), image_pages=True), offered)
    plain = job_of(upload(offered.browser, mixed_pdf()), offered)
    assert asked.image_pages is True and plain.image_pages is False
    assert asked.page_count == plain.page_count == 3


def test_the_pages_of_a_converted_scan_count_against_the_daily_quota_as_usual(offered):
    with session_scope(offered.ctx.factory) as session:
        user = session.query(User).filter_by(email=ALICE).one()
        before = quota.pages_left(session, user.id, quota=20, day=quota_day())
    upload(offered.browser, scan_pdf(3), image_pages=True)
    with session_scope(offered.ctx.factory) as session:
        user = session.query(User).filter_by(email=ALICE).one()
        after = quota.pages_left(session, user.id, quota=20, day=quota_day())
    assert before - after == 3


def quota_day():
    from readablestem.web.database import utc_today
    from readablestem.web.db import utcnow

    return utc_today(utcnow())


def test_the_page_quota_refuses_a_scan_like_any_other_document(tmp_path):
    env = WebEnv(tmp_path, feature_image_pages=True, per_user_daily_page_quota=2)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        response = upload(client, scan_pdf(3), image_pages=True)
        assert response.status_code == 429
        assert "you can convert" in response.text or "pages" in response.text


# ----------------------------------------------------------------------------- the job caps


def test_a_job_that_asked_may_spend_the_image_page_allowance_and_others_the_old_caps():
    s = settings(
        feature_image_pages=True,
        max_usd_per_job=3.0,
        max_usd_per_page=0.30,
        max_model_calls_per_job=400,
    )
    assert job_caps(s, 10) == (3.0, 400)  # unchanged without the option
    assert job_caps(s, 10, image_pages=False) == (3.0, 400)
    assert job_caps(s, 10, image_pages=True)[0] == 3.0  # 10 x $0.25 is less: the old cap stays
    assert job_caps(s, 20, image_pages=True)[0] == pytest.approx(5.0)
    assert job_caps(s, 30, image_pages=True)[0] == pytest.approx(7.5)  # the per-job maximum
    assert job_caps(s, 60, image_pages=True)[0] == pytest.approx(7.5)  # never more
    assert job_caps(s, 20, image_pages=True)[1] == 400  # the call cap is not widened


def test_the_option_changes_no_cap_where_the_feature_is_not_offered():
    s = settings(feature_image_pages=False, max_usd_per_job=3.0)
    assert job_caps(s, 30, image_pages=True) == job_caps(s, 30)


CAPPED = {"feature_image_pages": True, "max_usd_per_job": 2.0, "max_usd_per_page": 0.05}


def attempt_of(env: WebEnv, job: Job, pages: int, pool: str | None, asked: bool):
    claim = type("Claim", (), {"job_id": job.id, "attempt": 1})()
    return env.worker()._start_attempt(claim, 0.0, 0, pages, pool, asked)


def test_an_open_tier_job_has_the_wider_cap_inside_the_open_pool(tmp_path):
    env = WebEnv(
        tmp_path,
        signup_mode="open",
        open_tier_daily_spend_cap_usd=4.0,
        open_tier_daily_page_quota=20,
        **CAPPED,
    )
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        with session_scope(env.ctx.factory) as session:
            user = session.query(User).filter_by(email=ALICE).one()
            user.access_tier = access.OPEN
            user_id = user.id
        job = job_of(upload(client, scan_pdf(3), image_pages=True), env)
        assert job.image_pages is True  # open-tier accounts have the option too
        with session_scope(env.ctx.factory) as session:
            pool = access.spend_pool(access.tier_of(session, user_id))
        assert pool == access.POOL_OPEN
        attempt = attempt_of(env, job, 3, pool, True)
        assert attempt.pool == "open" and attempt.max_usd == pytest.approx(0.75)
        # The pool of the day, not the job's cap, is what bounds an open account's spend:
        # the attempt draws on a guard scoped to it.
        assert attempt.budget.guard is not None


# ------------------------------------------------------------------------ the run options


def test_a_job_converts_scans_only_if_it_asked_whatever_the_flag(offered):
    asked = job_of(upload(offered.browser, scan_pdf(), image_pages=True), offered)
    plain = job_of(upload(offered.browser, make_pdf()), offered)
    options = notation.conversion_options(offered.ctx.factory, offered.settings, asked.id)
    assert options is not None and options.image_pages is True
    options = notation.conversion_options(offered.ctx.factory, offered.settings, plain.id)
    assert options is not None and options.image_pages is False  # the flag only offers it
    # ... and the settings' own flag is never what a web job uses:
    assert ConversionOptions.from_settings(offered.settings).image_pages is True


# ---------------------------------------------------------------------- large files in parts


def test_a_large_scan_keeps_the_choice_for_its_parts(tmp_path):
    env = WebEnv(
        tmp_path,
        feature_image_pages=True,
        max_pages_per_job=2,
        max_source_pages=6,
        max_large_upload_mb=5,
        upload_limit_mb=5,
    )
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        response = upload(client, scan_pdf(4), image_pages=True)
        assert response.status_code == 303 and response.headers["location"].startswith("/uploads/")
        upload_id = response.headers["location"].rsplit("/", 1)[-1]
        with session_scope(env.ctx.factory) as session:
            row = session.get(PendingUpload, upload_id)
            assert row is not None and row.image_pages is True
        page_token = token(client, f"/uploads/{upload_id}")
        sent = client.post(
            f"/uploads/{upload_id}",
            data={"csrf_token": page_token, "parts": "1-2"},
        )
        assert sent.status_code in (200, 303), sent.text[:300]
        with session_scope(env.ctx.factory) as session:
            jobs = session.query(Job).filter(Job.part_group == upload_id).all()
            assert len(jobs) == 1 and jobs[0].image_pages is True


# --------------------------------------------------------------------------- the reservation


def test_offering_the_option_makes_room_for_a_page_read_in_the_largest_reservation():
    off = largest_reservation_usd(settings(feature_image_pages=False, model_provider="anthropic"))
    on = largest_reservation_usd(settings(feature_image_pages=True, model_provider="anthropic"))
    assert on >= off
    cheap = largest_reservation_usd(
        settings(
            feature_image_pages=True,
            model_provider="anthropic",
            image_pages_model="claude-haiku-5-5",
        )
    )
    assert cheap <= on
