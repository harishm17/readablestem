"""The upload option "Convert scanned or handwritten pages" (ADR 0007): who is offered it, what the
probe says to it, what a job that asked for it is allowed to spend, and that a job that did not ask
for it never converts such pages, whatever the deployment's flag.
"""

from __future__ import annotations

import re

import pymupdf
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
from readablestem.web.pdf_probe import ProbeRejected, inspect, probe
from readablestem.web.uploads import UploadRejected, inspect_pdf_detail
from readablestem.web.worker import job_caps

OFFER = (
    "Convert scanned or handwritten pages "
    "(slower; every converted equation and table needs your check)"
)
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


@pytest.fixture
def not_offered(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


def job_of(response, env: WebEnv) -> Job:
    assert response.status_code == 303, response.text[:300]
    return env.job(response.headers["location"].rsplit("/", 1)[-1])


# ------------------------------------------------------------------------------ the probe


def test_the_probe_counts_scanned_pages_only_when_asked():
    assert inspect(scan_pdf(2), max_pages=5, allow_image_pages=True) == (2, 2)
    assert inspect(mixed_pdf(), max_pages=5, allow_image_pages=True) == (3, 2)
    assert inspect(make_pdf(3), max_pages=5, allow_image_pages=True) == (3, 0)
    assert inspect(mixed_pdf(), max_pages=5) == (3, 0)  # not asked: not counted


def test_a_pdf_of_scans_is_refused_unless_asked_and_a_typed_one_never_is():
    with pytest.raises(ProbeRejected) as refused:
        probe(scan_pdf(), max_pages=5)
    assert refused.value.code == "scanned"
    assert probe(scan_pdf(), max_pages=5, allow_image_pages=True) == 1
    assert probe(make_pdf(2), max_pages=5, allow_image_pages=True) == 2


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


def test_the_isolated_probe_reports_the_count_from_its_child():
    assert inspect_pdf_detail(mixed_pdf(), max_pages=5, allow_image_pages=True) == (3, 2)
    assert inspect_pdf_detail(mixed_pdf(), max_pages=5) == (3, 0)
    with pytest.raises(UploadRejected) as refused:
        inspect_pdf_detail(scan_pdf(), max_pages=5)
    assert refused.value.code == "scanned"


def test_the_probe_child_counts_only_the_pages_page_type_calls_scans():
    from readablestem.pipeline import page_type

    doc = pymupdf.open(stream=mixed_pdf(), filetype="pdf")
    assert page_type.image_page_count(doc) == 2
    doc = pymupdf.open(stream=make_pdf(3), filetype="pdf")
    assert page_type.image_page_count(doc) == 0


# ------------------------------------------------------------------------------ the form


def test_the_option_is_on_the_upload_form_only_where_it_is_offered(offered, not_offered):
    page = offered.browser.get("/app").text
    assert OFFER in page
    assert 'name="image_pages"' in page and 'id="image_pages"' in page
    assert re.search(r'<label for="image_pages">', page)
    assert 'aria-describedby="image-pages-hint"' in page
    assert "does nothing for a PDF that has no scanned or handwritten pages" in " ".join(
        page.split()
    )
    assert "checked" not in re.search(r'<input type="checkbox" id="image_pages"[^>]*>', page).group(
        0
    )
    assert OFFER not in not_offered.browser.get("/app").text


# ------------------------------------------------------------------------------ the upload


def test_a_scan_is_refused_without_the_option_and_the_message_says_how_to_ask(offered):
    response = upload(offered.browser, scan_pdf())
    assert response.status_code == 422
    assert "is scanned, so this tool cannot convert it" in response.text
    assert "Convert scanned or handwritten pages" in response.text
    with session_scope(offered.ctx.factory) as session:
        assert session.query(Job).count() == 0  # refused: nothing was queued or counted


def test_without_the_offer_a_scan_is_refused_as_before_with_no_mention_of_it(not_offered):
    response = upload(not_offered.browser, scan_pdf(), image_pages=True)  # ignored: not offered
    assert response.status_code == 422
    assert "is scanned, so this tool cannot convert it" in response.text
    assert "Convert scanned or handwritten pages" not in response.text


def test_with_the_option_a_scan_becomes_a_job_that_remembers_it(offered):
    job = job_of(upload(offered.browser, scan_pdf(2), image_pages=True), offered)
    assert job.image_pages is True and job.page_count == 2 and job.status == "queued"


def test_the_option_does_nothing_for_a_pdf_without_scanned_pages(offered):
    job = job_of(upload(offered.browser, make_pdf(2), image_pages=True), offered)
    assert job.image_pages is False and job.page_count == 2


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
    s = settings(feature_image_pages=True, max_usd_per_job=3.0, max_usd_per_page=0.30)
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


def test_the_allowance_follows_its_settings():
    s = settings(
        feature_image_pages=True,
        image_pages_usd_per_page=0.40,
        image_pages_max_usd_per_job=5.0,
        max_usd_per_job=3.0,
    )
    assert job_caps(s, 5, image_pages=True)[0] == pytest.approx(2.0)
    assert job_caps(s, 10, image_pages=True)[0] == pytest.approx(4.0)
    assert job_caps(s, 30, image_pages=True)[0] == pytest.approx(5.0)


CAPPED = {"feature_image_pages": True, "max_usd_per_job": 2.0, "max_usd_per_page": 0.05}


def attempt_of(env: WebEnv, job: Job, pages: int, pool: str | None, asked: bool):
    claim = type("Claim", (), {"job_id": job.id, "attempt": 1})()
    return env.worker()._start_attempt(claim, 0.0, 0, pages, pool, asked)


def test_the_worker_starts_a_job_that_asked_with_the_wider_cap(tmp_path):
    env = WebEnv(tmp_path, **CAPPED)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        asked = job_of(upload(client, scan_pdf(4), image_pages=True), env)
        plain = job_of(upload(client, make_pdf(4)), env)
        assert attempt_of(env, asked, 4, None, True).max_usd == pytest.approx(1.0)  # 4 x $0.25
        assert attempt_of(env, plain, 4, None, False).max_usd == pytest.approx(0.2)  # 4 x $0.05
        # What the job is told it may spend is what the budget it gets holds:
        assert attempt_of(env, asked, 4, None, True).budget.max_usd == pytest.approx(1.0)


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


def test_the_worker_hands_the_converter_the_jobs_choice(offered):
    job_of(upload(offered.browser, scan_pdf(), image_pages=True), offered)
    offered.run_worker()
    [call] = offered.converter.calls
    assert call["options"] is not None and call["options"].image_pages is True


def test_without_the_flag_no_job_converts_scans(not_offered):
    job_of(upload(not_offered.browser, make_pdf()), not_offered)
    not_offered.run_worker()
    [call] = not_offered.converter.calls
    assert call["options"] is None or call["options"].image_pages is False


def test_a_revision_form_remembers_the_earlier_choice(offered):
    first = job_of(upload(offered.browser, scan_pdf(), image_pages=True), offered)
    offered.run_worker()
    page = offered.browser.get(f"/app?revises={first.id}").text
    assert re.search(r'<input type="checkbox" id="image_pages"[^>]*\bchecked\b', page)


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


def test_a_pending_upload_made_without_the_option_has_none(tmp_path):
    env = WebEnv(tmp_path, feature_image_pages=True, max_pages_per_job=2, max_source_pages=6)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        response = upload(client, make_pdf(4))
        if response.status_code == 303 and response.headers["location"].startswith("/uploads/"):
            upload_id = response.headers["location"].rsplit("/", 1)[-1]
            with session_scope(env.ctx.factory) as session:
                row = session.get(PendingUpload, upload_id)
                assert row is not None and row.image_pages is False


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
