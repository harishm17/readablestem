"""T3-1: a stopped or partly converted document is tried again without paying twice.

The real pipeline runs here, with the offline model client made to fail on demand: a run
that stops half way keeps its finished steps, "Try again" asks the model only for the rest,
spends only on that, counts nothing against the daily pages, keeps the decisions already
made, and every limit still holds. Also the status line about the AI service.
"""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from fixtures.web.support import (
    ALICE,
    BOB,
    csrf_from,
    job_id_from,
    login,
    make_settings,
    upload,
)
from readablestem.config import ConfigError
from readablestem.core.types import (
    ConversionOptions,
    DocumentResult,
    Element,
    Fix,
    Kind,
    ModelCall,
    Status,
)
from readablestem.models import make_vision_client
from readablestem.models.fake import image_key
from readablestem.pipeline import run as pipeline_run
from readablestem.web import provider_health, retry
from readablestem.web.app import create_app
from readablestem.web.auth import set_allowed
from readablestem.web.db import Job, QuotaDay, StepRecord, session_scope
from readablestem.web.worker import Worker

PDF = Path(__file__).parent / "fixtures" / "layout" / "worked_example.pdf"


class Flaky:
    """The offline client, except that it can stop answering: for every request
    (`down`), for the pictures in `down_for`, or by raising a configuration error after
    `fatal_after` requests (a run that dies half way)."""

    def __init__(self, inner, state: dict) -> None:
        self._inner = inner
        self._state = state

    def run(self, request, schema, **kwargs):
        state = self._state
        state["asked"].append(request.step)
        asked_now = len(state["asked"])
        if state["fatal_after"] is not None and asked_now > state["fatal_after"]:
            raise ConfigError("the model is not set up")
        down = state["down"] or (
            bool(request.images) and image_key(request.images[0]) in state["down_for"]
        )
        if down:
            call = ModelCall(
                step=request.step,
                model=request.model,
                prompt_version=request.prompt_version,
                outcome="api_error",
                latency_ms=1,
            )
            return None, call
        return self._inner.run(request, schema, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class Site:
    def __init__(self, tmp_path: Path, **settings) -> None:
        self.state: dict = {"down": False, "down_for": set(), "fatal_after": None, "asked": []}
        values = {"max_pages_per_job": 30, "model_concurrency": 1} | settings
        self.settings = make_settings(tmp_path, **values)
        self.app = create_app(self.settings, client_factory=self.make_client)
        self.ctx = self.app.state.ctx

    def make_client(self, settings, budget):
        return Flaky(make_vision_client(settings, budget, fake=True), self.state)

    def run_worker(self) -> str | None:
        worker = Worker(
            self.settings,
            self.ctx.factory,
            converter=self.ctx.converter,
            client_factory=self.make_client,
        )
        return worker.run_once()

    def job(self, job_id: str) -> Job:
        with session_scope(self.ctx.factory) as session:
            job = session.get(Job, job_id)
            assert job is not None
            session.expunge(job)
            return job

    def result(self, job_id: str) -> DocumentResult:
        return DocumentResult.model_validate(self.job(job_id).result_json)

    def records(self, job_id: str) -> int:
        with session_scope(self.ctx.factory) as session:
            return int(
                session.scalar(
                    select(func.count()).select_from(StepRecord).where(StepRecord.job_id == job_id)
                )
                or 0
            )

    def pages_used(self) -> int:
        with session_scope(self.ctx.factory) as session:
            return int(session.scalar(select(func.coalesce(func.sum(QuotaDay.pages), 0))) or 0)

    def post_retry(self, client: TestClient, job_id: str):
        page = f"/jobs/{job_id}"
        return client.post(f"{page}/retry", data={"csrf_token": csrf_from(client.get(page).text)})


@pytest.fixture
def site(tmp_path):
    site = Site(tmp_path)
    with TestClient(site.app, follow_redirects=False) as browser:
        with session_scope(site.ctx.factory) as session:
            set_allowed(session, ALICE, allowed=True)
            set_allowed(session, BOB, allowed=True)
        login(browser, ALICE)
        site.browser = browser
        site.bob = TestClient(site.app, follow_redirects=False)
        login(site.bob, BOB)
        yield site
        site.bob.close()


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def model_fixes(result: DocumentResult) -> list[Fix]:
    return [f for f in result.fixes if f.kind in (Kind.EQUATION, Kind.FIGURE)]


def baseline(site: Site) -> tuple[DocumentResult, int, float]:
    """A clean run of the same file on the same site: its result, the model requests it
    made and what it cost."""
    site.state["asked"] = []
    job_id = job_id_from(upload(site.browser, PDF.read_bytes(), filename="clean.pdf"))
    assert site.run_worker() == job_id
    return site.result(job_id), len(site.state["asked"]), site.job(job_id).cost_usd


def fixes_json(result: DocumentResult) -> list[dict]:
    return [f.model_dump(mode="json") for f in result.fixes]


# --------------------------------------------------------------------------- the pipeline


def test_a_cache_gives_back_finished_steps_and_the_model_is_not_asked_again(site):
    """convert_pdf with a cache: the second run of the same file makes no request at all
    and returns the same fixes."""
    from readablestem.config import Settings

    kept: dict[str, tuple[str, Fix]] = {}

    class Cache:
        hits = 0

        def lookup(self, element: Element, key: str):
            found = kept.get(element.id)
            if found is None or found[0] != key:
                return None
            Cache.hits += 1
            return found[1]

        def store(self, element: Element, key: str, fix: Fix) -> None:
            kept[element.id] = (key, fix)

    settings = site.settings
    assert isinstance(settings, Settings)
    from readablestem.core.budget import InMemorySpendGuard, JobBudget

    def client():
        budget = JobBudget(InMemorySpendGuard(cap_usd=5.0), max_usd=5.0, max_calls=500)
        return Flaky(make_vision_client(settings, budget, fake=True), site.state)

    site.state["asked"] = []
    first = pipeline_run.convert_pdf(
        PDF.read_bytes(), settings=settings, client=client(), concurrency=1, step_cache=Cache()
    )
    asked_first = len(site.state["asked"])
    assert asked_first > 0 and kept and Cache.hits == 0
    site.state["asked"] = []
    second = pipeline_run.convert_pdf(
        PDF.read_bytes(), settings=settings, client=client(), concurrency=1, step_cache=Cache()
    )
    assert site.state["asked"] == [] and Cache.hits == len(kept)
    assert fixes_json(second.result) == fixes_json(first.result)


def test_a_key_changes_with_the_picture_the_text_the_options_and_the_models(site):
    element = Element(id="p1-e0", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=0)
    base = ConversionOptions()
    key = pipeline_run.step_key(element, b"png", "words", base, site.settings)
    same = pipeline_run.step_key(element, b"png", "words", base, site.settings)
    assert key == same
    assert key != pipeline_run.step_key(element, b"png2", "words", base, site.settings)
    assert key != pipeline_run.step_key(element, b"png", "other words", base, site.settings)
    assert key != pipeline_run.step_key(
        element, b"png", "words", ConversionOptions(exam_mode=True), site.settings
    )
    other = site.settings.model_copy(update={"main_model": "some-other-model"})
    assert key != pipeline_run.step_key(element, b"png", "words", base, other)
    moved = element.model_copy(update={"text": "x = 1"})
    assert key != pipeline_run.step_key(moved, b"png", "words", base, site.settings)
    # The id and the reading-order number are not what the answer depends on.
    renumbered = element.model_copy(update={"id": "p9-e9", "order": 99})
    assert key == pipeline_run.step_key(renumbered, b"png", "words", base, site.settings)


def test_only_answers_are_reusable_not_failures_a_new_try_could_change():
    def fix(status: Status, reason: str | None = None) -> Fix:
        return Fix(element_id="p1-e0", kind=Kind.EQUATION, status=status, reason=reason)

    assert pipeline_run.reusable(fix(Status.ACCEPTED))
    assert pipeline_run.reusable(fix(Status.NEEDS_REVIEW))
    assert pipeline_run.reusable(fix(Status.ABSTAINED, "low_confidence"))
    assert pipeline_run.reusable(fix(Status.FAILED, "invalid_table"))
    for reason in ("model_api_error", "model_timeout", "model_invalid_output", "budget_daily",
                   "budget_job_usd", "internal_error", "crop_error"):  # fmt: skip
        assert not pipeline_run.reusable(fix(Status.FAILED, reason)), reason


# --------------------------------------------------------------------------- a run that dies


def test_a_run_that_dies_half_way_keeps_its_finished_steps_and_pays_only_for_the_rest(site):
    clean, clean_requests, clean_cost = baseline(site)
    total = len(model_fixes(clean))
    assert total >= 3 and clean_requests >= total
    used_before = site.pages_used()

    site.state["asked"] = []
    site.state["fatal_after"] = 2  # the third request finds the model not set up
    job_id = job_id_from(upload(site.browser, PDF.read_bytes(), filename="worked.pdf"))
    assert site.run_worker() == job_id
    job = site.job(job_id)
    assert (job.status, job.error_code) == ("failed", "model_not_configured")
    kept = site.records(job_id)
    assert 0 < kept < total
    cost_after_first_try = job.cost_usd
    with session_scope(site.ctx.factory) as session:
        kept_usd = sum(
            Fix.model_validate(fix_json).cost_usd
            for fix_json in session.scalars(
                select(StepRecord.fix_json).where(StepRecord.job_id == job_id)
            )
        )
    used_after_upload = site.pages_used()
    assert used_after_upload > used_before  # the upload used the pages

    # The page offers "Try again" and says what it will and will not do.
    page = text_of(site.browser.get(f"/jobs/{job_id}").text)
    assert "Try again" in page and "Nothing is counted against your daily pages" in page

    site.state.update(fatal_after=None, asked=[])
    response = site.post_retry(site.browser, job_id)
    assert response.status_code == 303 and response.headers["location"] == f"/jobs/{job_id}"
    queued = site.job(job_id)
    assert (queued.status, queued.retries, queued.error_code) == ("queued", 1, None)
    assert site.run_worker() == job_id

    again = site.job(job_id)
    assert (again.status, again.error_code) == ("done", None)
    # Only what was not finished was asked; the pages were not counted again.
    assert 0 < len(site.state["asked"]) < clean_requests
    assert site.pages_used() == used_after_upload
    # The same document as a clean run makes. The money: what the first try spent (a step
    # cut off after its first request is paid for twice, nothing else is), plus a clean
    # run's cost less the steps that were given back, which are not paid for again.
    assert fixes_json(site.result(job_id))[: len(clean.fixes)] == fixes_json(clean)
    assert again.cost_usd == pytest.approx(cost_after_first_try + clean_cost - kept_usd)
    assert again.cost_usd < cost_after_first_try + clean_cost  # not both runs in full
    assert again.model_calls > 0
    # A finished job that nothing can improve keeps no records.
    assert site.records(job_id) == 0


def test_the_steps_kept_are_deleted_with_the_job(site):
    site.state["fatal_after"] = 2
    job_id = job_id_from(upload(site.browser, PDF.read_bytes()))
    site.run_worker()
    assert site.records(job_id) > 0
    token = csrf_from(site.browser.get(f"/jobs/{job_id}").text)
    site.browser.post(f"/jobs/{job_id}/delete", data={"csrf_token": token, "confirm": "yes"})
    assert site.records(job_id) == 0


# --------------------------------------------------------------------------- the AI service is down


def test_a_conversion_during_an_outage_is_not_a_dead_end(site):
    clean, _, clean_cost = baseline(site)
    site.state["down"] = True
    site.state["asked"] = []
    job_id = job_id_from(upload(site.browser, PDF.read_bytes(), filename="outage.pdf"))
    assert site.run_worker() == job_id
    stopped = site.job(job_id)
    assert (stopped.status, stopped.error_code) == ("done", None)
    failed = [f for f in model_fixes(site.result(job_id)) if f.status is Status.FAILED]
    assert failed and all(f.reason == "model_api_error" for f in failed)

    # The job page says what happened and offers the way on; the status page says the
    # service is not answering.
    page = text_of(site.browser.get(f"/jobs/{job_id}").text)
    assert "Stopped: the AI service did not answer" in page
    assert "Convert the missing parts" in page
    assert "Nothing is wrong with your PDF" in page
    assert "The AI service is not answering right now" in page
    status = text_of(site.browser.get("/status").text)
    assert "AI service Not answering since" in status
    # The person decides one of the items that was not converted: keep the original.
    target = failed[0].element_id
    review = f"/jobs/{job_id}/review"
    decided = site.browser.post(
        f"{review}/{target}",
        data={
            "csrf_token": csrf_from(site.browser.get(review).text),
            "view": "list",
            "action": "reject",
        },
    )
    assert decided.status_code in {200, 303}

    # The service is back: convert the missing parts. Nothing is asked twice.
    site.state["down"] = False
    site.state["asked"] = []
    assert site.post_retry(site.browser, job_id).status_code == 303
    assert site.run_worker() == job_id
    result = site.result(job_id)
    assert not [f for f in model_fixes(result) if f.reason == "model_api_error"]
    # Their decision survived the second conversion.
    mine = result.fix_for(target)
    assert mine is not None and mine.status is Status.REJECTED and mine.reviewed
    assert site.job(job_id).retries == 1
    # The failed requests cost nothing and the second run paid for each item once.
    assert site.job(job_id).cost_usd == pytest.approx(clean_cost)
    # The outage note is gone, and so is the status line (one answered request ends it).
    page = text_of(site.browser.get(f"/jobs/{job_id}").text)
    assert "AI service did not answer" not in page
    assert "AI service Not answering" not in text_of(site.browser.get("/status").text)
    assert fixes_json(result)[: len(clean.fixes)] != []  # a real document


def test_trying_again_asks_only_for_the_items_the_service_missed(site):
    """Three of the four items fail at first: the one that was converted is kept."""
    clean, clean_requests, _ = baseline(site)
    # Find the pictures of the items as the model is sent them: the crop a conversion cuts
    # first (an unconverted equation's stored crop is another, sharper one: T3-15).
    from readablestem.pipeline import layout

    pdf = PDF.read_bytes()
    by_id = {e.id: e for e in clean.elements}
    try:
        crops = {
            f.element_id: image_key(layout.crop(pdf, by_id[f.element_id]))
            for f in model_fixes(clean)
        }
    finally:
        layout.release(pdf)
    ids = sorted(f.element_id for f in model_fixes(clean))
    missed = ids[1:]
    site.state["down_for"] = {crops[i] for i in missed if i in crops}
    assert len(site.state["down_for"]) == len(missed)
    site.state["asked"] = []
    job_id = job_id_from(upload(site.browser, PDF.read_bytes(), filename="part.pdf"))
    site.run_worker()
    assert site.records(job_id) == 1  # the one that was converted
    assert "Convert the missing parts" in text_of(site.browser.get(f"/jobs/{job_id}").text)

    site.state["down_for"] = set()
    site.state["asked"] = []
    site.post_retry(site.browser, job_id)
    site.run_worker()
    # Fewer requests than a whole conversion: the converted item was not asked again.
    assert 0 < len(site.state["asked"]) < clean_requests
    assert fixes_json(site.result(job_id))[: len(clean.fixes)] == fixes_json(clean)


# --------------------------------------------------------------------------- the limits


def test_trying_again_is_the_owners_only_and_needs_the_token(site):
    site.state["fatal_after"] = 1
    job_id = job_id_from(upload(site.browser, PDF.read_bytes()))
    site.run_worker()
    assert site.job(job_id).status == "failed"
    # Someone else gets the same 404 as for an id that does not exist.
    token = csrf_from(site.bob.get("/app").text)
    theirs = site.bob.post(f"/jobs/{job_id}/retry", data={"csrf_token": token})
    unknown = site.bob.post(f"/jobs/{'0' * 32}/retry", data={"csrf_token": token})
    assert theirs.status_code == unknown.status_code == 404
    # No token, a wrong one: refused, and the job is as it was.
    for fields in ({}, {"csrf_token": "wrong"}):
        refused = site.browser.post(f"/jobs/{job_id}/retry", data=fields)
        assert refused.status_code == 403
    assert site.job(job_id).status == "failed" and site.job(job_id).retries == 0


def test_a_job_that_is_not_stopped_cannot_be_tried_again(site):
    clean_id = job_id_from(upload(site.browser, PDF.read_bytes()))
    site.run_worker()
    assert site.job(clean_id).status == "done"
    response = site.post_retry(site.browser, clean_id)
    assert response.status_code == 409 and "Nothing to try again" in response.text
    # Waiting is not stopped either.
    waiting = job_id_from(upload(site.browser, PDF.read_bytes(), filename="b.pdf"))
    assert site.post_retry(site.browser, waiting).status_code == 409
    assert site.job(waiting).retries == 0


def test_a_file_this_tool_cannot_convert_is_not_offered_a_retry(site):
    job_id = job_id_from(upload(site.browser, PDF.read_bytes()))
    site.run_worker()
    with session_scope(site.ctx.factory) as session:
        job = session.get(Job, job_id)
        job.status, job.error_code, job.result_json = "failed", "handwritten", None
    page = text_of(site.browser.get(f"/jobs/{job_id}").text)
    assert "Try again" not in page
    assert site.post_retry(site.browser, job_id).status_code == 409


def test_a_job_can_be_tried_again_only_a_few_times(site):
    site.state["fatal_after"] = 1
    job_id = job_id_from(upload(site.browser, PDF.read_bytes()))
    for n in range(retry.MAX_RETRIES_PER_JOB):
        site.run_worker()
        assert site.job(job_id).status == "failed", n
        assert site.post_retry(site.browser, job_id).status_code == 303
        assert site.job(job_id).retries == n + 1
    site.run_worker()
    assert site.job(job_id).status == "failed"
    refused = site.post_retry(site.browser, job_id)
    assert refused.status_code == 409 and "No more tries" in refused.text
    page = text_of(site.browser.get(f"/jobs/{job_id}").text)
    assert (
        "already tried this document 3 times" in page
        and "Try again"
        not in page.split("already tried")[0].split("Why the conversion stopped")[-1]
    )


def test_a_person_with_a_full_queue_cannot_add_to_it_by_trying_again(site):
    from readablestem.web.routes.jobs import MAX_ACTIVE_JOBS_PER_USER

    site.state["fatal_after"] = 1
    stopped = job_id_from(upload(site.browser, PDF.read_bytes()))
    site.run_worker()
    site.state["fatal_after"] = None
    for n in range(MAX_ACTIVE_JOBS_PER_USER):
        upload(site.browser, PDF.read_bytes(), filename=f"w{n}.pdf")
    refused = site.post_retry(site.browser, stopped)
    assert refused.status_code == 429 and "Too many documents in progress" in refused.text
    assert site.job(stopped).status == "failed" and site.job(stopped).retries == 0


def test_trying_again_is_rate_limited_per_person(tmp_path):
    site = Site(tmp_path, rate_limits_enabled=True)
    with TestClient(site.app, follow_redirects=False) as browser:
        with session_scope(site.ctx.factory) as session:
            set_allowed(session, ALICE, allowed=True)
        login(browser, ALICE)
        job_id = job_id_from(upload(browser, PDF.read_bytes()))
        token = csrf_from(browser.get(f"/jobs/{job_id}").text)
        rule = site.ctx.limiter.rules["retry_user"]
        answers = [
            browser.post(f"/jobs/{job_id}/retry", data={"csrf_token": token}).status_code
            for _ in range(rule.limit + 1)
        ]
        assert answers[-1] == 429 and 429 not in answers[: rule.limit]


def test_the_daily_budget_still_holds_a_retried_job_in_the_queue(site):
    """Trying again does not skip the spend cap: a queued job waits while it is used up."""
    site.state["fatal_after"] = 1
    job_id = job_id_from(upload(site.browser, PDF.read_bytes()))
    site.run_worker()
    site.state["fatal_after"] = None
    site.post_retry(site.browser, job_id)
    cap = site.ctx.guard.cap_usd
    assert cap
    from readablestem.web.database import utc_today
    from readablestem.web.db import SpendDay

    with session_scope(site.ctx.factory) as session:
        session.merge(SpendDay(day=utc_today(), spent_usd=cap, held_usd=0.0))
    assert site.run_worker() is None  # nothing claimed
    assert site.job(job_id).status == "queued"
    page = text_of(site.browser.get(f"/jobs/{job_id}").text)
    assert "Today's conversion budget has been used up" in page


def test_interrupted_attempts_are_counted_from_the_last_try_again(site):
    """A job interrupted three times is failed; after "Try again" it gets three more."""
    from datetime import timedelta

    from readablestem.web.db import utcnow

    job_id = job_id_from(upload(site.browser, PDF.read_bytes()))
    with session_scope(site.ctx.factory) as session:
        job = session.get(Job, job_id)
        job.status, job.attempts, job.attempts_at_retry, job.retries = "running", 4, 3, 1
        job.lease_until = utcnow() - timedelta(minutes=5)
    worker = Worker(
        site.settings,
        site.ctx.factory,
        converter=site.ctx.converter,
        client_factory=site.make_client,
    )
    assert worker.requeue_expired() == (1, 0)  # one attempt since the try-again: requeued
    with session_scope(site.ctx.factory) as session:
        job = session.get(Job, job_id)
        job.status, job.attempts = "running", 6
        job.lease_until = utcnow() - timedelta(minutes=5)
    assert worker.requeue_expired() == (0, 1)  # three since: failed as before


# --------------------------------------------------------------------------- provider health


def test_the_service_counts_as_not_answering_after_a_run_of_failures_and_recovers():
    now = [1000.0]
    health = provider_health.ProviderHealth(clock=lambda: now[0])
    for _ in range(provider_health.MIN_CALLS - 1):
        health.record("api_error")
    assert not health.is_down() and health.down_since() is None
    health.record("timeout")
    assert health.is_down() and health.down_since() is not None
    health.record("budget_exceeded")  # says nothing about the service
    assert health.is_down()
    health.record("ok")  # one answer ends it
    assert not health.is_down()
    for _ in range(provider_health.MIN_CALLS):
        health.record("api_error")
    assert health.is_down()
    now[0] += provider_health.WINDOW_S + 1  # nothing seen for ten minutes: no claim
    assert not health.is_down()
    health.reset()


def test_the_status_page_says_the_service_answers_when_it_does(site):
    page = text_of(site.browser.get("/status").text)
    assert "AI service No problems seen in the last 10 minutes" in page
