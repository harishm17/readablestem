"""Upload, My documents, the job page in each state, downloads, retention wording (ux2-lane-a):
the second round of the independent reviews. Each test is one finding: what the page says, in
which order, and what it keeps."""

from __future__ import annotations

import re

import pytest

from fixtures.web.support import (
    ALICE,
    BOB,
    WebEnv,
    fake_output,
    job_id_from,
    login,
    make_pdf,
    token,
    upload,
)
from readablestem.web import outcome
from readablestem.web.db import Job, session_scope


def flat(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def state_line(page: str, state_id: str) -> str:
    """The text of one option group's summary line."""
    chunk = page.split(f'id="{state_id}" data-group-state>', 1)[1].split("</span>", 1)[0]
    return flat(chunk)


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        yield env


@pytest.fixture
def offered(tmp_path):
    env = WebEnv(tmp_path, feature_image_pages=True)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


def finish(env: WebEnv, *, filename: str = "notes.pdf", title: str | None = "Kinematics") -> str:
    output = fake_output()
    output.result.title = title
    env.converter.output = output
    job_id = job_id_from(upload(env.browser, make_pdf(1), filename=filename))
    assert env.run_worker() == job_id
    return job_id


def set_job(env: WebEnv, job_id: str, **columns) -> None:
    with session_scope(env.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        for name, value in columns.items():
            setattr(job, name, value)


# --------------------------------------------------------------------------- upload form


def test_a_saved_default_starts_the_keep_box_ticked_and_the_line_says_seven_days(web):
    token_ = token(web.browser, "/account")
    web.browser.post("/account/keep-default", data={"csrf_token": token_, "keep_default": "on"})
    page = web.browser.get("/app").text
    assert re.search(r'<input type="checkbox" id="keep"[^>]*checked', page)
    keep_line = state_line(page, "keep-state")
    assert keep_line.startswith("Deleted 7 days after you last work on it")


# --------------------------------------------------------------------------- My documents


# --------------------------------------------------------------------------- job page: done


def test_someone_it_was_shared_with_sees_only_the_groups_that_are_theirs(web):
    job_id = finish(web)
    token_ = token(web.browser, f"/jobs/{job_id}/share")
    web.browser.post(
        f"/jobs/{job_id}/share",
        data={"csrf_token": token_, "email": BOB, "role": "view"},
    )
    bob = web.client()
    login(bob, BOB)
    page = bob.get(f"/jobs/{job_id}").text
    nav = page.split('aria-label="On this page"', 1)[1].split("</nav>", 1)[0]
    assert "Review and download" in nav and "Record" in nav
    assert "For students" not in nav and "Settings and sharing" not in nav
    assert "Delete" not in nav and 'id="delete-heading"' not in page


def test_retention_is_explained_once_with_the_real_deletion_time_and_who_moves_it(web):
    job_id = finish(web)
    page = web.browser.get(f"/jobs/{job_id}").text
    assert page.count('id="retention-line"') == 1
    line = flat(page.split('id="retention-line"', 1)[1].split("</div>", 1)[0])
    assert "Deleted automatically on" in line and "24 hours after you last work on it" in line
    assert "Your decisions, edits and downloads restart that time" in line
    assert "sharing does not" in line
    # it sits in the Delete group, where the share page's "Keep it longer" link goes
    assert page.index('id="delete-heading"') < page.index('id="retention-line"')
    assert "after upload" not in line


# --------------------------------------------------------------------------- job page: ready


def ready_job(env: WebEnv) -> str:
    output = fake_output()
    for fix in output.result.fixes:
        if fix.pending:
            fix.status = fix.status.__class__.REJECTED
            fix.reviewed = True
    env.converter.output = output
    job_id = job_id_from(upload(env.browser, make_pdf(1)))
    assert env.run_worker() == job_id
    return job_id


# --------------------------------------------------------------------------- waiting


# --------------------------------------------------------------------------- stopped


def test_a_stopped_scan_job_is_feature_aware_and_the_message_is_not_repeated():
    plain = outcome.failed_message("handwritten")
    assert plain and "this tool cannot convert it" in plain
    asked = outcome.failed_message("handwritten", image_pages_offered=True)
    assert asked and "cannot convert" not in asked
    assert "such pages are read only when you ask" in asked and outcome.OPT_IN_ROUTE in asked
    # the job outcome carries it, for the status box
    stopped = outcome.job_outcome(status="failed", error_code="scanned", image_pages_offered=True)
    assert stopped and stopped.label == "Stopped" and "ticked" in stopped.message
    other = outcome.job_outcome(status="failed", error_code="no_content", image_pages_offered=True)
    assert other and other.message == outcome.FAILED_MESSAGES["no_content"]


# --------------------------------------------------------------------------- deleted, expired


def test_a_document_that_is_not_yours_to_open_gets_neutral_help_that_does_not_say_which_case(web):
    job_id = finish(web)
    bob = web.client()
    login(bob, BOB)
    page = flat(bob.get(f"/jobs/{job_id}").text)
    missing = flat(bob.get("/jobs/" + "0" * 32).text)
    assert "that this account can open" in page and "Check the address and your account" in page
    assert "ask the sender" in page
    # identical for a document that never existed
    assert page == missing or page.replace(job_id, "") == missing


# --------------------------------------------------------------------------- the public text
