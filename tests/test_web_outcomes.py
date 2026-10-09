"""Jobs that cannot finish normally: spend caps, scanned pages, removed users, no model.

These run the real pipeline (`pipeline.run.convert_pdf`) with the offline model client
wherever the outcome depends on what the pipeline does, because the fake converter of
the other web tests cannot produce a budget stop or a scanned page.
"""

from __future__ import annotations

import copy
import logging
import re
import subprocess
import sys
import threading
from datetime import timedelta
from html import unescape
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import select

from a11y_stem.config import ConfigError, Settings
from a11y_stem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget
from a11y_stem.core.types import DocumentResult, DocumentStats, Element, Kind, PageStats
from a11y_stem.models import OFFLINE_DEFAULTS, make_vision_client
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.models.pricing import estimate_max_cost_usd
from a11y_stem.pipeline.page_kinds import PageKind, nothing_to_convert, page_kinds
from a11y_stem.pipeline.run import convert_pdf
from a11y_stem.web.auth import set_allowed
from a11y_stem.web.database import as_utc, utc_today
from a11y_stem.web.db import Job, JobStatus, SpendDay, User, session_scope, utcnow
from a11y_stem.web.outcome import job_outcome
from a11y_stem.web.pdf_probe import MAX_PAGE_SIDE_PT
from a11y_stem.web.spend import largest_reservation_usd
from a11y_stem.web.worker import (
    JobCancelled,
    Worker,
    default_client_factory,
    error_code_for,
    job_caps,
    result_outcome,
)
from fixtures.web.support import (
    ALICE,
    BOB,
    WebEnv,
    fake_output,
    job_id_from,
    login,
    make_pdf,
    upload,
)

LAYOUT = Path(__file__).parent / "fixtures" / "layout"
CAP = 1.0


def costly_client(settings: Settings, budget: JobBudget) -> FakeVisionClient:
    """Offline answers whose calls really cost something: a main-model call settles at
    about $0.02, so a few of them use up a small remainder of the daily cap."""
    return FakeVisionClient(
        defaults=copy.deepcopy(OFFLINE_DEFAULTS), budget=budget, output_tokens=2000
    )


def offline_client(settings: Settings, budget: JobBudget):
    return make_vision_client(settings, budget, fake=True)


@pytest.fixture
def env(tmp_path):
    env = WebEnv(tmp_path, daily_spend_cap_usd=CAP, max_pages_per_job=30)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


def real_worker(env, client_factory=offline_client, **kwargs) -> Worker:
    return Worker(
        env.settings,
        env.ctx.factory,
        converter=convert_pdf,
        client_factory=client_factory,
        **kwargs,
    )


def leave_under_cap(env, remaining: float) -> None:
    with session_scope(env.ctx.factory) as session:
        day = session.get(SpendDay, utc_today())
        if day is None:
            session.add(SpendDay(day=utc_today(), spent_usd=CAP - remaining, held_usd=0.0))
        else:
            day.spent_usd, day.held_usd = CAP - remaining, 0.0


def page_text(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def scanned_pdf(pages: int = 2, *, then_text: bool = False) -> bytes:
    """Pages that are one full-page image and no text (a scan), optionally then a text page."""
    picture = pymupdf.open()
    source = picture.new_page()
    source.insert_text((72, 72), "Scanned lecture notes", fontsize=24)
    png = source.get_pixmap(dpi=50).tobytes("png")
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_image(page.rect, stream=png)
    if then_text:
        doc.new_page().insert_text((72, 72), "Kinematics: velocity is the rate of change.")
    data = doc.tobytes()
    doc.close()
    picture.close()
    return data


def blank_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def huge_page_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((20, 40), "Big page")
    page.set_mediabox(pymupdf.Rect(0, 0, 18000, 18000))
    data = doc.tobytes()
    doc.close()
    return data


# --------------------------------------------------------------------------- budget stops


def test_a_job_the_daily_cap_stops_part_way_is_not_shown_as_a_normal_success(env):
    """The audit's at-cap scenario: before, this ended "Finished. The accessible HTML is
    ready." with error_code None and no hint that most of it was not converted."""
    job_id = job_id_from(upload(env.browser, (LAYOUT / "lecture_notes.pdf").read_bytes()))
    leave_under_cap(env, 0.05)  # more than one call's worth, so the job is claimed
    assert real_worker(env, costly_client).run_once() == job_id

    job = env.job(job_id)
    assert job.status == "done" and job.error_code == "budget_daily"
    assert job.result_json is not None and job.result_json["stopped_by"] == "budget_daily"
    failed = [f for f in job.result_json["fixes"] if f.get("reason") == "budget_daily"]
    model_fixes = [f for f in job.result_json["fixes"] if f["kind"] in {"equation", "figure"}]
    assert failed

    text = page_text(env.browser.get(f"/jobs/{job_id}").text)
    assert "Stopped early" in text
    assert f"{len(failed)} of {len(model_fixes)} equations and figures were not converted" in text
    assert "after 00:00 UTC" in text
    assert "The accessible HTML is ready" not in text
    assert "Stopped early" in page_text(env.browser.get("/app").text)
    # The partial result is still there to use.
    assert env.browser.get(f"/jobs/{job_id}/download").status_code == 200


def test_a_per_job_cap_stop_says_to_split_the_document(env):
    job_id = job_id_from(upload(env.browser, (LAYOUT / "lecture_notes.pdf").read_bytes()))
    tight = env.settings.model_copy(update={"max_usd_per_page": 0.02})  # 2 pages: $0.04
    worker = Worker(tight, env.ctx.factory, converter=convert_pdf, client_factory=costly_client)
    assert worker.run_once() == job_id
    job = env.job(job_id)
    assert job.status == "done" and job.error_code == "budget_job_usd"
    assert job.cost_usd <= 0.04 + 1e-9
    text = page_text(env.browser.get(f"/jobs/{job_id}").text)
    assert "Stopped early" in text and "Split the PDF into smaller parts" in text


def test_the_worker_claims_nothing_while_the_daily_budget_is_spent(env, caplog):
    caplog.set_level(logging.INFO)
    job_id = job_id_from(upload(env.browser, make_pdf(1)))
    headroom = largest_reservation_usd(env.settings)
    leave_under_cap(env, headroom / 2)
    worker = real_worker(env)
    assert worker.budget_closed()
    assert worker.run_once() is None
    assert worker.run_once() is None
    assert env.job(job_id).status == "queued"
    paused = [r for r in caplog.records if r.getMessage().startswith("claims_paused")]
    assert len(paused) == 1  # logged once per pause, not on every poll

    text = page_text(env.browser.get(f"/jobs/{job_id}").text)
    assert "Waiting" in text and "Today's conversion budget has been used up" in text
    status = env.browser.get(f"/jobs/{job_id}/status")
    assert "used up" in status.text and 'hx-get="/jobs/' in status.text

    leave_under_cap(env, CAP)  # a new day
    assert worker.run_once() == job_id
    assert env.job(job_id).status == "done"
    assert any(r.getMessage().startswith("claims_resumed") for r in caplog.records)


def test_uploads_close_when_less_than_the_largest_single_call_is_left(env):
    """The old threshold was one cheap call ($0.001); an equation's first reservation on
    the main model is about $0.031, so uploads were accepted that could convert nothing."""
    cheap_call = estimate_max_cost_usd(
        env.settings.cheap_model, n_images=1, text_chars=0, max_output_tokens=1024
    )
    largest = largest_reservation_usd(env.settings)
    assert largest > 0.03 > cheap_call
    leave_under_cap(env, 0.0015)  # one cheap call fits; no equation does
    response = upload(env.browser, make_pdf(1))
    assert response.status_code == 503
    assert "Conversions are paused" in env.browser.get("/app").text


def test_largest_reservation_matches_the_biggest_step_request():
    settings = Settings(_env_file=None)
    from a11y_stem.pipeline import equation_step

    biggest = estimate_max_cost_usd(
        settings.main_model,
        n_images=1,
        text_chars=600,
        max_output_tokens=equation_step.MAX_OUTPUT_TOKENS,
    )
    assert largest_reservation_usd(settings) >= biggest


# --------------------------------------------------------------------------- caps per job


def test_job_caps_scale_with_pages_up_to_the_per_job_caps():
    settings = Settings(_env_file=None)
    assert job_caps(settings, 1) == (settings.max_usd_per_page, settings.max_model_calls_per_page)
    assert job_caps(settings, 30) == (settings.max_usd_per_job, settings.max_model_calls_per_job)
    assert job_caps(settings, 0) == job_caps(settings, settings.max_pages_per_job)


def test_a_dense_one_page_pdf_cannot_spend_a_whole_job_allowance(env):
    """One page of many small figures (the audit's 96-figure page) used to reach the $2
    per-job cap; now a one-page job stops at the per-page allowance."""
    doc = pymupdf.open()
    page = doc.new_page()
    picture = pymupdf.open()
    tile = picture.new_page(width=60, height=60)
    tile.draw_rect(tile.rect, color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
    png = tile.get_pixmap(dpi=144).tobytes("png")
    for row in range(6):
        for col in range(5):
            x, y = 50 + col * 100, 60 + row * 110
            page.insert_image(pymupdf.Rect(x, y, x + 70, y + 70), stream=png)
            page.insert_text((x, y + 90), f"Figure {row * 5 + col + 1}", fontsize=8)
    data = doc.tobytes()
    job_id = job_id_from(upload(env.browser, data))
    tight = env.settings.model_copy(update={"max_usd_per_page": 0.05})
    worker = Worker(tight, env.ctx.factory, converter=convert_pdf, client_factory=costly_client)
    assert worker.run_once() == job_id
    job = env.job(job_id)
    assert job.cost_usd <= 0.05 + 1e-9
    assert job.error_code == "budget_job_usd"


def test_the_job_cap_holds_when_an_attempt_dies_between_heartbeats(env):
    """The audit's requeue scenario: attempt 1 settles calls and dies without a beat; the
    retry used to get the full cap again because Job.cost_usd lagged behind."""
    job_id = job_id_from(upload(env.browser, make_pdf(1)))
    max_usd, _ = job_caps(env.settings, 1)
    call = 0.05
    clock = {"now": utcnow()}
    first = env.worker(lease_s=60, clock=lambda: clock["now"])
    claim = first.claim()
    assert claim is not None
    attempt = first._start_attempt(claim, 0.0, 0, 1)
    for _ in range(3):  # settled, but no heartbeat or progress callback afterwards
        attempt.budget.settle(attempt.budget.reserve(call), call)
    held = attempt.budget.reserve(call)  # in flight when the instance is killed
    assert env.job(job_id).cost_usd == pytest.approx(4 * call)  # current, without a beat

    clock["now"] += timedelta(minutes=5)
    second = env.worker(lease_s=60, clock=lambda: clock["now"])
    assert second.requeue_expired() == (1, 0)
    claim_b = second.claim()
    assert claim_b is not None
    job = env.job(job_id)
    retry = second._start_attempt(claim_b, job.cost_usd, job.model_calls, 1)
    spent = 0.0
    with pytest.raises(BudgetExceeded) as refused:
        while True:
            retry.budget.settle(retry.budget.reserve(call), call)
            spent += call
    assert refused.value.scope == "job_usd"
    # The dead attempt's in-flight hold still counts (erring on the side of less spend).
    assert 4 * call + spent <= max_usd + 1e-9
    assert env.job(job_id).cost_usd <= max_usd + 1e-9
    # A late settle from the dead attempt adjusts the job's total, never past the cap.
    attempt.budget.settle(held, 0.0)
    assert env.job(job_id).cost_usd == pytest.approx(3 * call + spent)


def test_a_refused_daily_reservation_is_taken_off_the_job_again(env):
    job_id = job_id_from(upload(env.browser, make_pdf(1)))
    worker = env.worker()
    claim = worker.claim()
    assert claim is not None
    attempt = worker._start_attempt(claim, 0.0, 0, 1)
    leave_under_cap(env, 0.01)
    with pytest.raises(BudgetExceeded) as refused:
        attempt.budget.reserve(0.02)
    assert refused.value.scope == "daily"
    job = env.job(job_id)
    assert job.cost_usd == 0.0 and job.model_calls == 0


# --------------------------------------------------------------------------- shutdown


def test_stopping_the_worker_cancels_the_running_job_and_ends_its_lease(env):
    job_id = job_id_from(upload(env.browser, make_pdf(1)))
    started, release = threading.Event(), threading.Event()
    seen: dict = {}

    def converter(pdf_bytes, /, *, settings, client, progress=None):
        started.set()
        release.wait(5)
        try:
            client.budget.reserve(0.001)
        except JobCancelled:
            seen["refused"] = True
        return fake_output()

    worker = env.worker(lease_s=60)
    worker.converter = converter
    runner = threading.Thread(target=worker.run_once)
    runner.start()
    assert started.wait(5)
    worker.stop(timeout=1)  # what the app's shutdown does (no loop thread to join here)
    lease = as_utc(env.job(job_id).lease_until)
    assert lease is not None and lease <= utcnow()  # ended now, not in 60 s
    release.set()
    runner.join(10)
    assert seen == {"refused": True}  # no model call after shutdown began
    assert env.job(job_id).status == "running"  # left for the next worker, which ...
    assert env.worker().requeue_expired() == (1, 0)  # ... requeues it at once


# --------------------------------------------------------------------------- scanned pages


@pytest.mark.parametrize(
    ("data", "code", "message"),
    [
        (scanned_pdf(2), "scanned", "scanned page images with no text"),
        (blank_pdf(), "no_content", "Every page of notes.pdf is blank"),
        (huge_page_pdf(), "page_too_large", "too large to convert"),
    ],
    ids=["scanned", "blank", "huge-page"],
)
def test_pdfs_with_nothing_to_convert_are_refused_at_upload(env, data, code, message):
    response = upload(env.browser, data)
    assert response.status_code == 422
    assert message in response.text
    with session_scope(env.ctx.factory) as session:
        assert session.scalar(select(Job.id)) is None  # nothing queued, no quota spent


def test_scanned_pages_in_a_mixed_pdf_are_kept_flagged_and_reported(env):
    job_id = job_id_from(upload(env.browser, scanned_pdf(2, then_text=True)))
    assert real_worker(env).run_once() == job_id
    job = env.job(job_id)
    assert job.status == "done" and job.error_code == "scanned_pages"
    assert job.result_json is not None and job.result_json["image_only_pages"] == [0, 1]
    scans = [f for f in job.result_json["fixes"] if f.get("reason") == "scanned_page"]
    assert [f["element_id"] for f in scans] == ["p1-e0", "p2-e0"]
    assert job.html is not None and job.html.count('data-reason="scanned_page"') == 2
    for element_id in ("p1-e0", "p2-e0"):  # the page images are kept, not sent to a model
        crop = env.browser.get(f"/jobs/{job_id}/crops/{element_id}.png")
        assert crop.status_code == 200 and crop.content.startswith(b"\x89PNG")
    assert job.model_calls == 0

    text = page_text(env.browser.get(f"/jobs/{job_id}").text)
    assert "Pages 1 and 2 are scanned images with no text" in text
    assert "run text recognition (OCR)" in text


def test_a_pipeline_result_with_nothing_in_it_fails_instead_of_finishing_empty(env):
    def nothing(pdf_bytes, /, *, settings, client, progress=None):
        output = fake_output()
        output.result.elements, output.result.fixes = [], []
        return output

    job_id = job_id_from(upload(env.browser, make_pdf(1)))
    worker = env.worker()
    worker.converter = nothing
    assert worker.run_once() == job_id
    job = env.job(job_id)
    assert job.status == "failed" and job.error_code == "no_content" and job.html is None
    assert "no text, equations or figures" in page_text(env.browser.get(f"/jobs/{job_id}").text)


def _result(pages: list[int], image_only: list[int], elements: int = 1, **extra) -> DocumentResult:
    return DocumentResult(
        pipeline_version="t",
        elements=[
            Element(id=f"p1-e{i}", kind=Kind.FIGURE, page=0, bbox=(0, 0, 1, 1), order=i)
            for i in range(elements)
        ],
        stats=DocumentStats(
            pages=len(pages),
            cost_usd=0,
            latency_ms=0,
            model_calls=0,
            per_page=[
                PageStats(page=p, elements=0, fixes=0, cost_usd=0, latency_ms=0) for p in pages
            ],
        ),
        image_only_pages=image_only,
        **extra,
    )


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (_result([0, 1], [0, 1]), (JobStatus.FAILED, "scanned")),
        (_result([0], [], elements=0), (JobStatus.FAILED, "no_content")),
        (_result([0, 1], [0]), (JobStatus.DONE, "scanned_pages")),
        (_result([0, 1], [0], stopped_by="budget_daily"), (JobStatus.DONE, "budget_daily")),
        (_result([0], [], stopped_by="budget_cancelled"), (JobStatus.DONE, None)),
        (_result([0], []), (JobStatus.DONE, None)),
    ],
    ids=["all-scanned", "empty", "some-scanned", "budget-first", "not-a-budget-stop", "normal"],
)
def test_result_outcome(result, expected):
    assert result_outcome(result) == expected


def test_outcome_messages_for_every_new_code_say_what_to_do():
    for code in ("scanned", "no_content", "model_not_configured", "access_removed"):
        outcome = job_outcome(status="failed", error_code=code)
        assert outcome is not None and outcome.label == "Stopped" and outcome.message
    for code in ("budget_daily", "budget_job_usd", "budget_job_calls", "scanned_pages"):
        outcome = job_outcome(status="done", error_code=code)
        assert outcome is not None and outcome.partial
    assert job_outcome(status="done", error_code=None) is None
    assert job_outcome(status="queued", error_code=None) is None
    waiting = job_outcome(status="queued", error_code=None, budget_closed=True)
    assert waiting is not None and waiting.code == "waiting_budget"


# --------------------------------------------------------------------------- allowlist


def test_removing_someone_stops_their_queued_jobs_and_spending(env):
    env.allow(BOB)
    other = env.client()
    login(other, BOB)
    queued = job_id_from(upload(other, make_pdf(1)))
    with session_scope(env.ctx.factory) as session:
        set_allowed(session, BOB, allowed=False)
    job = env.job(queued)
    assert job.status == "failed" and job.error_code == "access_removed"
    assert env.run_worker() is None  # nothing of theirs is claimed


def test_a_running_job_stops_spending_once_its_owner_is_removed(env):
    job_id = job_id_from(upload(env.browser, make_pdf(1)))
    seen: dict = {}

    def converter(pdf_bytes, /, *, settings, client, progress=None):
        client.budget.reserve(0.001)
        with session_scope(env.ctx.factory) as session:
            set_allowed(session, ALICE, allowed=False)
        try:
            client.budget.reserve(0.001)
        except JobCancelled:
            seen["refused"] = True
        return fake_output()

    worker = env.worker()
    worker.converter = converter
    assert worker.run_once() == job_id
    assert seen == {"refused": True}
    assert env.job(job_id).error_code == "access_removed"


def test_jobs_of_users_no_longer_allowed_are_not_claimed(env):
    job_id = job_id_from(upload(env.browser, make_pdf(1)))
    with session_scope(env.ctx.factory) as session:  # removed behind the app's back
        user = session.scalar(select(User).where(User.email == ALICE))
        assert user is not None
        user.is_allowed = False
    assert env.run_worker() is None
    assert env.job(job_id).status == "queued"


# --------------------------------------------------------------------------- model access


def test_fake_models_answer_offline_and_are_refused_in_production(tmp_path):
    settings = Settings(_env_file=None, fake_models=True)
    budget = JobBudget(guard=InMemorySpendGuard(1.0), max_usd=1, max_calls=10)
    assert isinstance(default_client_factory(settings, budget), FakeVisionClient)
    prod = Settings(
        _env_file=None,
        env="prod",
        fake_models=True,
        daily_spend_cap_usd=1,
        per_user_daily_page_quota=1,
        session_secret="s" * 40,
        google_client_id="id",
        google_client_secret="secret",
        cron_token="t",
        openai_api_key="k",
        contact_email="c@example.org",
        database_url="postgresql+psycopg://x@localhost/db",
        base_url="https://example.org",
    )
    with pytest.raises(ConfigError, match="FAKE_MODELS"):
        prod.check_production()


def test_a_missing_model_key_fails_with_its_own_code_and_message(env, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert error_code_for(ConfigError("no key")) == "model_not_configured"
    job_id = job_id_from(upload(env.browser, (LAYOUT / "worked_example.pdf").read_bytes()))
    worker = Worker(
        env.settings, env.ctx.factory, converter=convert_pdf, client_factory=default_client_factory
    )
    assert worker.run_once() == job_id
    job = env.job(job_id)
    assert job.status == "failed" and job.error_code == "model_not_configured"
    text = page_text(env.browser.get(f"/jobs/{job_id}").text)
    assert "Conversions are not set up on this server yet" in text


def test_page_size_limit_is_generous_for_real_documents():
    assert MAX_PAGE_SIDE_PT > 3370  # an A0 poster's long side


# --------------------------------------------------------------------------- page kinds


def test_page_kinds_tell_scans_from_text_figures_and_blank_pages():
    data = scanned_pdf(1, then_text=True)
    doc = pymupdf.open(stream=data, filetype="pdf")
    figure = doc.new_page()
    figure.draw_circle((200, 200), 50)
    doc.new_page()
    doc[0].set_rotation(90)  # image boxes are in unrotated coordinates
    kinds = page_kinds(doc)
    assert kinds == {
        0: PageKind.IMAGE_ONLY,
        1: PageKind.TEXT,
        2: PageKind.GRAPHICS,
        3: PageKind.BLANK,
    }
    assert nothing_to_convert({0: PageKind.IMAGE_ONLY, 1: PageKind.BLANK}) == "scanned"
    assert nothing_to_convert({0: PageKind.BLANK}) == "no_content"
    assert nothing_to_convert({0: PageKind.IMAGE_ONLY, 1: PageKind.GRAPHICS}) is None
    doc.close()


def test_the_upload_probe_child_caps_its_own_address_space():
    code = (
        "import resource\n"
        "from a11y_stem.web.pdf_probe import limit_memory\n"
        "limit_memory(512 * 1024 * 1024)\n"
        "print(resource.getrlimit(resource.RLIMIT_AS)[0])\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert int(out.stdout) == 512 * 1024 * 1024


def test_headlines_count_tables_with_equations_and_figures():
    """tables.pdf: 13 equations and 5 tables. An outage headline used to say '13 of 18
    equations and figures', and a budget stop '13 of 13' with 3 tables also stopped."""

    def fix(kind: str, reason: str | None = None) -> dict:
        return {"kind": kind, "status": "failed" if reason else "accepted", "reason": reason}

    outage = {
        "fixes": [fix("equation", "model_timeout")] * 13
        + [fix("table", "model_api_error")] * 3
        + [fix("table")] * 2
    }
    message = job_outcome(status="done", error_code=None, result_json=outage)
    assert message is not None
    assert "did not answer for 16 of 18 equations and tables" in message.message
    budget = {
        "fixes": [fix("equation", "budget_job_usd")] * 13
        + [fix("table", "budget_job_usd")] * 3
        + [fix("figure")]
    }
    message = job_outcome(status="done", error_code="budget_job_usd", result_json=budget)
    assert message is not None
    assert "16 of 17 equations, figures and tables were not converted" in message.message
