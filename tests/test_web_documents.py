"""My documents as a to-do list, times in the reader's zone, the deleted-document page,
upload messages that name the file, the budget-spent page and the posting help (UX
review BW5, BW9, MF10, MF11, MF16, MF20, MF21, MF22, MF24)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from a11y_stem.core.types import Status
from a11y_stem.ops.retention import purge_expired
from a11y_stem.web.app import make_templates
from a11y_stem.web.database import utc_today
from a11y_stem.web.db import Job, SpendDay, session_scope
from a11y_stem.web.pdf_probe import ProbeRejected, probe
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

LAYOUT = Path(__file__).resolve().parent / "fixtures" / "layout"


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        yield env


def text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def finish(env, *, filename: str = "notes.pdf", title: str | None = "Kinematics", decided=False):
    """Upload and convert one document; `decided`: every decision already made."""
    output = fake_output()
    output.result.title = title
    if decided:
        for fix in output.result.fixes:
            if fix.pending:
                fix.status = Status.REJECTED
                fix.reviewed = True
    env.converter.output = output
    job_id = job_id_from(upload(env.browser, make_pdf(1), filename=filename))
    assert env.run_worker() == job_id
    return job_id


def set_created(env, job_id: str, when: datetime) -> None:
    with session_scope(env.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.created_at = when


# --------------------------------------------------------------------------- the list


def test_my_documents_is_a_to_do_list(web):
    now = datetime.now(UTC)
    older = finish(web, filename="week1.pdf", title="Week 1: Vectors")
    newer = finish(web, filename="week2.pdf", title="Week 2: Motion")
    ready = finish(web, filename="ps.pdf", title="Problem Set 2", decided=True)
    waiting = job_id_from(upload(web.browser, make_pdf(2), filename="slides.pdf"))
    set_created(web, older, now - timedelta(hours=3))
    set_created(web, newer, now - timedelta(hours=2))
    page = web.browser.get("/app").text
    flat = text(page)

    # Needs decisions first (oldest first), then ready, then the rest; the list comes
    # before the upload form.
    order = [page.index(f'id="doc-{job}"') for job in (older, newer, ready, waiting)]
    assert order == sorted(order)
    assert page.index('id="jobs-heading"') < page.index('id="upload-heading"')
    assert "Needs your decisions (2)" in flat and "Ready to download (1)" in flat
    assert (
        "2 documents need your decisions (6 decisions in all) · 1 ready to download · "
        "1 waiting." in flat
    )
    assert f'<a class="button" id="review-next" href="/jobs/{older}/review">Review next' in page
    assert "3 decisions to make" in flat and "Ready to download" in flat
    assert f'id="act-{older}" href="/jobs/{older}/review">Review' in page
    assert f'id="act-{ready}" href="/jobs/{ready}/download">Download' in page

    # By title, with the file name, pages and upload time under it.
    assert f'<a id="doc-{older}" href="/jobs/{older}">Week 1: Vectors</a>' in page
    assert "week1.pdf · 1 page · uploaded" in flat
    assert f'<a id="doc-{waiting}" href="/jobs/{waiting}">slides.pdf</a>' in page  # no title yet
    assert "slides.pdf · 2 pages" not in flat  # the file name is not said twice
    # Partial results, failures and badges say it in words.
    for badge in re.findall(r'<span class="status-badge status-(\w+)">([^<]+)</span>', page):
        assert badge[1].strip(), badge
    assert "<title>My documents (3 of 4 finished) | a11y-stem</title>" in page


def test_a_document_without_a_title_is_shown_by_its_file_name(web):
    job_id = finish(web, filename="handout.pdf", title="Converted document")
    page = web.browser.get("/app").text
    assert f'<a id="doc-{job_id}" href="/jobs/{job_id}">handout.pdf</a>' in page


def test_the_list_refreshes_only_when_something_changed(web):
    waiting = job_id_from(upload(web.browser, make_pdf(1)))
    page = web.browser.get("/app").text
    match = re.search(r'hx-get="/app/documents\?v=([0-9a-f]+)" hx-trigger="every 5s"', page)
    assert match, "the list polls while a document is waiting"
    version = match.group(1)
    assert "refresh the list" in page  # without JavaScript, a link does it
    assert web.browser.get(f"/app/documents?v={version}").status_code == 204
    assert web.run_worker() == waiting
    changed = web.browser.get(f"/app/documents?v={version}", headers={"HX-Request": "true"})
    assert changed.status_code == 200
    assert changed.text.startswith("<title>My documents | a11y-stem</title>")
    assert '<div id="documents" class="documents">' in changed.text  # finished: no polling
    assert "<html" not in changed.text
    assert "hx-get" not in web.browser.get("/app").text


def test_the_list_is_the_owners_only(web):
    finish(web, filename="mine.pdf", title="Mine")
    bob = web.client()
    login(bob, BOB)
    assert "Mine" not in bob.get("/app").text and "Mine" not in bob.get("/app/documents").text
    assert web.client().get("/app/documents").status_code == 303  # signed out: sign in


def test_a_partial_result_says_so_in_the_list(web):
    job_id = finish(web, decided=True)
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.error_code = "scanned_pages"
    page = web.browser.get("/app").text
    assert (
        '<span class="status-badge status-partial">Finished, with pages not converted</span>'
        in page
    )


# --------------------------------------------------------------------------- delete


def test_the_lists_delete_is_a_link_to_the_confirmation(web):
    job_id = finish(web)
    page = web.browser.get("/app").text
    assert f'<a id="del-{job_id}" href="/jobs/{job_id}/delete?from=app">Delete' in page
    confirm = web.browser.get(f"/jobs/{job_id}/delete?from=app").text
    flat = text(confirm)
    assert "Delete Kinematics?" in flat and "notes.pdf, uploaded" in flat
    assert '<a href="/app">No, keep it</a>' in confirm  # back where it came from
    assert "Files you have already downloaded are not affected." in flat


def test_the_confirmation_counts_the_decisions_that_go_with_it(web, monkeypatch):
    from a11y_stem.pipeline import assemble

    monkeypatch.setattr(assemble, "render_html", lambda **kw: "<html>re-rendered</html>")
    job_id = finish(web)
    web.browser.post(
        f"/jobs/{job_id}/review/p1-e3", data={"csrf_token": token(web.browser), "action": "reject"}
    )
    assert "You made 1 decision on it." in text(web.browser.get(f"/jobs/{job_id}/delete").text)


# --------------------------------------------------------------------------- deleted


def test_an_expired_documents_link_says_it_was_deleted_as_promised(web):
    job_id = finish(web)
    now = datetime.now(UTC)
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.created_at = now - timedelta(hours=25)
        job.retain_until = now - timedelta(hours=1)  # kept 24 hours, purge not run yet
    for path in ("", "/review", "/download"):
        response = web.browser.get(f"/jobs/{job_id}{path}")
        assert response.status_code == 404
        flat = text(response.text)
        assert "This document has been deleted" in flat
        assert "deleted automatically 24 hours after upload, as promised" in flat
        assert "Files you downloaded are not affected." in flat
        assert "not yours to see" not in flat


def test_after_the_purge_its_owner_still_learns_it_was_deleted(web):
    job_id = finish(web)
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.retain_until = datetime.now(UTC) - timedelta(minutes=1)
    assert purge_expired(web.ctx.factory).jobs_deleted == 1
    flat = text(web.browser.get(f"/jobs/{job_id}").text)
    assert "This document has been deleted" in flat
    assert "deleted automatically 24 hours after you last worked on them (7 days if" in flat
    # Anyone else learns nothing, not even that the id existed.
    bob = web.client()
    login(bob, BOB)
    other = bob.get(f"/jobs/{job_id}")
    assert other.status_code == 404
    assert "This document has been deleted" not in text(other.text)
    assert "There is no document at this address." in text(other.text)


def test_someone_elses_live_document_is_a_plain_not_found(web):
    job_id = finish(web)
    bob = web.client()
    login(bob, BOB)
    flat = text(bob.get(f"/jobs/{job_id}").text)
    assert "There is no document at this address." in flat
    assert "has been deleted" not in flat and "not yours to see" not in flat


# --------------------------------------------------------------------------- uploads


def truncated_pdf() -> bytes:
    """A PDF cut short, as by a download that did not finish: MuPDF repairs it and finds
    no pages (the reviewers' 15 truncated files were all called "no pages")."""
    real = (LAYOUT / "lecture_notes.pdf").read_bytes()
    return real[: len(real) // 10]


def test_a_damaged_pdf_is_not_called_empty():
    with pytest.raises(ProbeRejected) as refused:
        probe(truncated_pdf(), max_pages=50)
    assert refused.value.code == "damaged"


def test_upload_messages_name_the_file_and_the_numbers(web):
    response = upload(web.browser, truncated_pdf(), filename="lecture4.pdf")
    assert response.status_code == 422
    assert "lecture4.pdf looks damaged or incomplete" in response.text
    assert "save a fresh copy" in response.text
    response = upload(web.browser, make_pdf(7), filename="chapter2.pdf")
    assert "chapter2.pdf has 7 pages; the limit is 5 per upload." in text(response.text)
    response = upload(web.browser, b"PK zip", filename="slides.pptx")
    assert "slides.pptx is not a PDF." in response.text


def test_the_upload_form_carries_what_the_browser_needs_to_check_first(web):
    page = web.browser.get("/app").text
    assert 'data-max-bytes="1048576" data-max-mb="1"' in page
    assert '<div id="pdf-check" aria-live="polite"></div>' in page
    assert '<p id="upload-status" class="upload-status" role="status"></p>' in page
    # The opt-in says what ticking it lets the developer do.
    assert "may look at the results to find and fix mistakes in the tool" in text(page)


def test_when_the_budget_is_spent_the_form_says_when_uploads_open_again(web):
    with session_scope(web.ctx.factory) as session:
        session.add(SpendDay(day=utc_today(), spent_usd=5.0, held_usd=0.0))
    page = web.browser.get("/app").text
    assert "Conversions are paused for today." in text(page)
    assert 'action="/jobs"' not in page  # no form that would send a file to be refused
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    assert (
        f'Uploads open again at <time class="local-time" datetime="{tomorrow}T00:00:00Z">' in page
    )
    status = web.browser.get("/status").text
    assert (
        f'They start again at <time class="local-time" datetime="{tomorrow}T00:00:00Z">' in status
    )


# --------------------------------------------------------------------------- times


def test_times_are_time_elements_in_utc_until_a_script_localises_them():
    env = make_templates().env
    rendered = env.from_string("{{ t|utc }} {{ t|utc_text }} [{{ none|utc }}]").render(
        t=datetime(2026, 10, 9, 23, 32, tzinfo=UTC)
    )
    assert rendered == (
        '<time class="local-time" datetime="2026-10-09T23:32:00Z">9 Oct 2026, 23:32 UTC</time>'
        " 9 Oct 2026, 23:32 UTC []"
    )


def test_the_documents_list_shows_deletion_times(web):
    job_id = finish(web)
    page = web.browser.get("/app").text
    with session_scope(web.ctx.factory) as session:
        retain = session.scalar(select(Job.retain_until).where(Job.id == job_id))
    assert retain is not None
    stamp = retain.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    assert f'data-label="Deleted automatically"><time class="local-time" datetime="{stamp}"' in page


# --------------------------------------------------------------------------- public pages


def test_the_posting_help_says_upload_and_link_never_paste(web):
    page = web.client().get("/help/posting")  # public: no sign-in
    assert page.status_code == 200
    flat = text(page.text)
    assert "Do not paste the file into a Canvas page" in flat
    assert "open Files and upload the .html file" in flat
    assert "Post the HTML as the main course material" in flat
    assert "keep the PDF as a print copy" in flat
    assert "28 CFR 35.202" in flat and "guidance, not legal advice" in flat
    assert "https://www.ada.gov/resources/2024-03-08-web-rule/" in page.text
    assert 'aria-current="page">Posting in your course</a>' in page.text


def test_the_landing_page_says_why_now_how_long_and_how_accurate(web):
    flat = text(web.client().get("/").text)
    assert "public universities" in flat
    assert "a 16-page textbook section needed 3 decisions" in flat
    assert "23 of 30 equations" in flat and "It makes mistakes" in flat
    assert "How to post it in your course" in flat
    assert "confidence score" not in flat


def test_the_privacy_notice_mentions_usage_events(web):
    flat = text(web.client().get("/privacy").text)
    assert "No cookies or scripts are used for this." in flat
    assert "deleted after 400 days" in flat
