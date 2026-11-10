"""Upload, My documents, the job page in each state, downloads, retention wording (ux2-lane-a):
the second round of the independent reviews. Each test is one finding: what the page says, in
which order, and what it keeps."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

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
from image_page_support import scan_pdf
from readablestem.web import job_page, outcome
from readablestem.web.db import Job, JobStatus, session_scope


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


def test_the_file_comes_first_and_its_button_follows_the_two_short_groups(web):
    page = web.browser.get("/app").text
    file_at, button_at = page.index('id="pdf"'), page.index("Upload and convert")
    convert, keep = page.index('id="group-convert"'), page.index('id="group-keep"')
    assert file_at < convert < keep < button_at
    # Each group is one line until opened, and says what is set now.
    groups = re.findall(r'<details class="option-group[^"]*"[^>]*>', page)
    assert len(groups) == 2 and not any(" open" in g.split("data-")[0] for g in groups)
    convert_line = state_line(page, "convert-state")
    assert "Figures described normally" in convert_line
    keep_line = state_line(page, "keep-state")
    assert "Deleted 24 hours after you last work on it" in keep_line
    # The checkboxes are still in the page, unticked, for a form that is sent as it is.
    for box in ("exam", "keep", "share"):
        tag = re.search(rf'<input type="checkbox" id="{box}"[^>]*>', page)
        assert tag and " checked" not in tag.group(0)


def test_the_retention_hint_points_to_the_account_default_not_to_ticking_every_time(web):
    page = flat(web.browser.get("/app").text)
    assert "Tick it every time" not in page
    assert "Make this the default in Account" in page


def test_a_saved_default_starts_the_keep_box_ticked_and_the_line_says_seven_days(web):
    token_ = token(web.browser, "/account")
    web.browser.post("/account/keep-default", data={"csrf_token": token_, "keep_default": "on"})
    page = web.browser.get("/app").text
    assert re.search(r'<input type="checkbox" id="keep"[^>]*checked', page)
    keep_line = state_line(page, "keep-state")
    assert keep_line.startswith("Deleted 7 days after you last work on it")


def test_the_scan_box_says_first_what_it_is_for(offered):
    page = flat(offered.browser.get("/app").text)
    first = page.split("Convert scanned or handwritten pages", 1)[1][:260]
    assert "Needed for a PDF that is all scans or handwriting" in first
    assert "scanned pages stay pictures marked “Not converted” unless you check it" in page
    assert "Without it, such pages stay as pictures" not in page  # the old, half-true line


def test_the_main_file_of_a_zip_is_not_offered_without_latex_source_and_is_tucked_away_with_it(
    tmp_path,
):
    off = WebEnv(tmp_path / "off")
    with off.running() as client:
        off.allow(ALICE)
        login(client, ALICE)
        page = client.get("/app").text
        assert "main_file" not in page and "Main file of a zip" not in page
    on = WebEnv(tmp_path / "on", feature_latex_source=True)
    with on.running() as client:
        on.allow(ALICE)
        login(client, ALICE)
        page = client.get("/app").text
        assert 'name="main_file"' in page
        wrapper = page.split('class="more-detail main-file-detail"', 1)[1]
        assert wrapper.index("<summary>") < wrapper.index('name="main_file"')


def refuse_with_choices(web: WebEnv, **extra):
    data = {
        "csrf_token": token(web.browser),
        "exam": "on",
        "share": "on",
        "keep": "on",
        **extra,
    }
    return web.browser.post(
        "/jobs", data=data, files={"pdf": ("notes.txt", b"not a pdf", "text/plain")}
    )


def test_a_refusal_keeps_what_was_ticked_and_puts_the_file_chooser_first_and_focused(web):
    finish(web, filename="earlier.pdf")  # a document already in the list
    response = refuse_with_choices(web)
    assert response.status_code == 415
    page = response.text
    assert re.search(r'<input type="checkbox" id="exam"[^>]*checked', page)
    assert re.search(r'<input type="checkbox" id="share"[^>]*checked', page)
    assert re.search(r'<input type="checkbox" id="keep"[^>]*checked', page)
    # the groups that hold a choice are open, and say so in their line
    for group in ("group-convert", "group-keep"):
        assert re.search(rf'id="{group}"[^>]*\sopen', page)
    assert "Exam or quiz: figures described without answers" in flat(page)
    # the error is next to the file field, not above the whole document list
    assert (
        page.index('id="error-summary-title"')
        < page.index('id="pdf"')
        < page.index('id="jobs-heading"')
    )
    # the summary takes focus (the established contract) and its first link goes to the chooser
    assert re.search(r'<div class="error-summary"[^>]*tabindex="-1" autofocus>', page)
    assert 'href="#pdf"' in page  # the summary links to the chooser


def test_a_refusal_keeps_the_main_file_that_was_typed(tmp_path):
    env = WebEnv(tmp_path, feature_latex_source=True)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        page = refuse_with_choices(env, main_file="chapter2.tex").text
        assert 'name="main_file"' in page and 'value="chapter2.tex"' in page


def test_with_the_option_offered_a_scan_refusal_does_not_say_the_tool_cannot_convert_it(offered):
    response = upload(offered.browser, scan_pdf(), exam=True, keep=True)
    assert response.status_code == 422
    page = flat(response.text)
    assert "cannot convert" not in page
    assert "which are read only when you ask" in page
    assert "upload a PDF exported from the original" in page
    # the summary links to the box to tick, which sits in an open group, other choices kept
    assert 'href="#image_pages"' in response.text
    assert re.search(r'<input type="checkbox" id="exam"[^>]*checked', response.text)
    assert re.search(r'id="group-convert"[^>]*\sopen', response.text)


def test_a_handwriting_refusal_without_the_option_still_says_the_tool_cannot_do_it(web):
    from image_page_support import scan_pdf as scan

    response = upload(web.browser, scan())
    assert "so this tool cannot convert it" in flat(response.text)
    assert "Convert scanned or handwritten pages" not in response.text


def test_source_that_is_not_available_says_what_is_unavailable_and_offers_the_pdf(tmp_path):
    env = WebEnv(tmp_path, feature_latex_source=True)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.ctx.sandbox.set("fail")
        # the form no longer offers it once the probe fails; a stale tab still sends one
        data = b"\\documentclass{article}\\begin{document}x\\end{document}"
        response = client.post(
            "/jobs",
            data={"csrf_token": token(client), "exam": "on"},
            files={"pdf": ("a.tex", data, "text/plain")},
        )
        page = flat(response.text)
        assert response.status_code in {400, 503}
        if response.status_code == 503:
            assert "LaTeX source is not available on this server right now" in page
            assert "Compile it to a PDF and choose that here instead" in page
            assert "cannot read that right now" not in page
            assert re.search(r'<input type="checkbox" id="exam"[^>]*checked', response.text)


# --------------------------------------------------------------------------- My documents


def test_an_empty_my_documents_leads_with_the_upload_form_and_then_the_sample(web):
    page = web.browser.get("/app").text
    assert page.index('id="upload-heading"') < page.index('id="sample-heading"')
    assert "You have not uploaded any documents yet" not in page
    assert 'id="upload-another"' not in page  # nothing to go past


def test_a_populated_my_documents_has_an_upload_action_by_the_heading(web):
    finish(web)
    page = web.browser.get("/app").text
    heading = page.split('<div class="heading-row page-heading">', 1)[1].split("</div>", 1)[0]
    assert "<h1>My documents</h1>" in heading
    assert 'href="#upload-heading">Upload another file</a>' in heading
    assert page.index('id="jobs-heading"') < page.index('id="upload-heading"')


def test_rows_lead_with_the_file_name_so_repeated_titles_do_not_blur_together(web):
    first = finish(web, filename="lecture1.pdf", title="Kinematics")
    second = finish(web, filename="lecture2.pdf", title="Kinematics")
    page = web.browser.get("/app").text
    assert f'<a id="doc-{first}" href="/jobs/{first}">lecture1.pdf</a>' in page
    assert f'<a id="doc-{second}" href="/jobs/{second}">lecture2.pdf</a>' in page
    assert flat(page).count("Titled “Kinematics”") == 2


def test_a_part_is_told_apart_by_its_pages_and_a_chosen_title_leads(web):
    part = finish(web, filename="book.pdf")
    set_job(web, part, part_label="31-59", part_group="g1")
    named = finish(web, filename="week3.pdf")
    set_job(web, named, title_override="My own title")
    page = web.browser.get("/app").text
    assert f'<a id="doc-{part}" href="/jobs/{part}">book.pdf, pages 31-59</a>' in page
    assert f'<a id="doc-{named}" href="/jobs/{named}">My own title</a>' in page
    assert "Titled “My own title”" not in flat(page)


def test_each_row_has_one_next_step_and_delete_not_four_links(web):
    job_id = finish(web)
    page = web.browser.get("/app").text
    row = page.split(f'id="doc-{job_id}"', 1)[1].split("</tr>", 1)[0]
    assert len(re.findall(r"<a ", row)) == 2  # (the document's own link comes before it)
    assert f'id="act-{job_id}"' in row and f'id="del-{job_id}"' in row
    assert "Upload a revised version" not in row


# --------------------------------------------------------------------------- job page: done


def test_the_finished_job_page_has_five_groups_and_links_to_them(web):
    job_id = finish(web)
    page = web.browser.get(f"/jobs/{job_id}").text
    nav = page.split('aria-label="On this page"', 1)[1].split("</nav>", 1)[0]
    for label in (
        "Review and download",
        "For students",
        "Settings and sharing",
        "Record",
        "Delete",
    ):
        assert f">{label}</a>" in nav
    for target in (
        'id="review-and-download"',
        'id="for-students-heading"',
        'id="settings-heading"',
        'id="record-link-heading"',
        'id="delete-heading"',
    ):
        assert target in page
    # in that order
    order = [page.index(t) for t in (
        'id="review-and-download"', 'id="for-students-heading"', 'id="settings-heading"',
        'id="record-link-heading"', 'id="delete-heading"',
    )]  # fmt: skip
    assert order == sorted(order)


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


def test_the_seven_tiles_are_one_line_with_the_counts_on_request(web):
    page = web.browser.get(f"/jobs/{finish(web)}").text
    line = flat(page.split('id="results-line"', 1)[1].split("</p>", 1)[0])
    assert line.lstrip("> ").startswith("Found ") and "decision" not in line
    detail = page.split('id="counts-detail"', 1)[1].split("</details>", 1)[0]
    assert "<summary>Counts in detail</summary>" in detail
    assert "Decisions to make" in flat(detail)
    assert "<details" in page and 'class="summary-list"' in detail


def test_sharing_says_it_is_for_colleagues_and_is_not_how_students_get_it(web):
    job_id = finish(web)
    page = flat(web.browser.get(f"/jobs/{job_id}").text)
    assert "Share with a colleague" in page
    assert "at any time" in page
    assert "Not how students get it" in page
    assert "before you give it to students" in page  # the headline no longer says "share"
    assert "need you before you share it" not in page
    # the job page's panel and the share page both say sharing does not move the deletion date
    line = "Sharing does not extend the deletion date; your own work does."
    assert line in page
    assert line in flat(web.browser.get(f"/jobs/{job_id}/share").text)


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


def test_a_thirty_day_document_says_it_runs_from_upload_and_does_not_move(web):
    job_id = finish(web)
    set_job(web, job_id, retention_mode="dev")
    line = flat(
        web.browser.get(f"/jobs/{job_id}")
        .text.split('id="retention-line"', 1)[1]
        .split("</div>", 1)[0]
    )
    assert "30 days after upload" in line and "does not change that" in line
    assert "starts that time again" not in line


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


def test_ready_asks_where_it_will_be_posted_with_one_recommended_route_and_direct_links(web):
    job_id = ready_job(web)
    page = web.browser.get(f"/jobs/{job_id}").text
    ready = page[page.index('id="ready"') :].split("</section>", 1)[0]
    routes = ready.split('id="post-heading"', 1)[1]
    assert "Where will you post it?" in ready
    assert flat(routes).count("Recommended") == 1
    assert routes.index('id="route-web"') < routes.index('id="route-canvas"')
    assert routes.index('id="route-canvas"') < routes.index('id="route-epub"')
    for link in (
        f'action="/jobs/{job_id}/download/canvas"',
        f'href="/jobs/{job_id}/download/cartridge"',
        f'href="/jobs/{job_id}/download.epub"',
        'href="/help/posting"',
    ):
        assert link in routes
    # the posting steps are said once, and the checks once
    assert flat(ready).count("Do not paste it into a page editor") == 1
    assert flat(ready).count("Listen as a student") >= 1
    assert ready.count('id="before-heading"') == 1


# --------------------------------------------------------------------------- waiting


def test_a_queued_document_says_how_long_it_has_waited_and_that_the_estimate_is_an_estimate(web):
    job_id = job_id_from(upload(web.browser, make_pdf(2)))
    page = web.browser.get(f"/jobs/{job_id}").text
    assert "Waiting for less than a minute." in flat(page)
    assert re.search(r'class="wait-note" data-wait-since="[^"]+" data-wait-slow-after="\d+"', page)
    assert "to go (an estimate)." in flat(page)
    assert '<p class="wait-slow" data-wait-slow hidden>' in page  # not slow yet: no alarm
    note = page.split('class="wait-note"', 1)[1].split('id="job-progress"', 1)[0]
    assert "%" not in flat(note)  # no percentage, no finish time: the tool has neither


def test_a_long_wait_says_so_plainly_and_a_running_one_counts_from_its_start(web):
    queued = job_id_from(upload(web.browser, make_pdf(1)))
    set_job(web, queued, created_at=datetime.now(UTC) - timedelta(hours=2))
    page = web.browser.get(f"/jobs/{queued}").text
    assert "Waiting for 2 hours." in flat(page)
    assert '<p class="wait-slow" data-wait-slow>' in page  # shown: nothing hides it
    slow = page.split('<p class="wait-slow" data-wait-slow>', 1)[1].split("</p>", 1)[0]
    assert "Taking longer than usual." in flat(slow)
    assert 'href="/status"' in slow

    running = job_id_from(upload(web.browser, make_pdf(1)))
    set_job(
        web,
        running,
        status=JobStatus.RUNNING,
        started_at=datetime.now(UTC) - timedelta(minutes=3),
        created_at=datetime.now(UTC) - timedelta(hours=5),
    )
    page = web.browser.get(f"/jobs/{running}").text
    assert "Converting for 3 minutes." in flat(page)  # from the attempt's start, not the upload
    assert '<p class="wait-slow" data-wait-slow hidden>' in page  # three minutes is not slow


def test_a_finished_job_has_no_wait_note(web):
    assert 'class="wait-note"' not in web.browser.get(f"/jobs/{finish(web)}").text


def test_the_elapsed_words_are_the_ones_the_page_script_writes():
    assert job_page.elapsed_words(5) == "less than a minute"
    assert job_page.elapsed_words(60) == "1 minute"
    assert job_page.elapsed_words(61 * 60) == "1 hour 1 minute"
    assert job_page.elapsed_words(3 * 3600) == "3 hours"
    assert job_page.slow_after(None) == job_page.SLOW_FLOOR_S
    assert job_page.slow_after(2000) == 4000  # twice its own estimate when that is longer


def test_a_batch_shares_the_wait_note_while_anything_is_unfinished(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        token_ = token(client)
        response = client.post(
            "/jobs",
            data={"csrf_token": token_},
            files=[
                ("pdf", ("a.pdf", make_pdf(1), "application/pdf")),
                ("pdf", ("b.pdf", make_pdf(1), "application/pdf")),
            ],
        )
        assert response.status_code == 303
        page = client.get(response.headers["location"]).text
        assert "Uploaded less than a minute ago." in flat(page)
        # the same words as the job page (`job_page.elapsed_words`)
        from readablestem.web.routes.batch import _elapsed

        assert _elapsed(0) == job_page.elapsed_words(5) and _elapsed(65) == "1 hour 5 minutes"


# --------------------------------------------------------------------------- stopped


def test_a_stopped_scan_job_is_feature_aware_and_the_message_is_not_repeated():
    plain = outcome.failed_message("handwritten")
    assert plain and "this tool cannot convert it" in plain
    asked = outcome.failed_message("handwritten", image_pages_offered=True)
    assert asked and "cannot convert" not in asked
    assert "such pages are read only when you ask" in asked and outcome.OPT_IN_ROUTE in asked
    # the job outcome carries it, for the status box
    stopped = outcome.job_outcome(status="failed", error_code="scanned", image_pages_offered=True)
    assert stopped and stopped.label == "Stopped" and "checked" in stopped.message
    other = outcome.job_outcome(status="failed", error_code="no_content", image_pages_offered=True)
    assert other and other.message == outcome.FAILED_MESSAGES["no_content"]


def test_a_stopped_job_page_gives_safe_ways_on_when_there_is_no_retry(web):
    output = fake_output()
    output.result.fixes = []
    output.result.elements = []
    web.converter.output = output
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    web.run_worker()
    page = web.browser.get(f"/jobs/{job_id}").text
    todo = flat(page.split('id="failed-heading"', 1)[1].split("</section>", 1)[0])
    assert "What you can do" in flat(page)
    assert "Upload it again" in todo and "Back to My documents" in todo
    assert "Service status" in todo and "Email us about it" in todo
    assert flat(page).count(outcome.FAILED_MESSAGES["no_content"]) == 1


def test_the_unknown_stop_does_not_say_unexpectedly():
    from readablestem.web.views import job_error_message

    message = job_error_message("no-such-code")
    assert "unexpectedly" not in message and "no more specific reason" in message


# --------------------------------------------------------------------------- deleted, expired


def test_a_deleted_documents_page_says_how_retention_works_now_and_offers_to_upload_again(web):
    job_id = finish(web)
    now = datetime.now(UTC)
    set_job(
        web, job_id, created_at=now - timedelta(hours=60), retain_until=now - timedelta(hours=1)
    )
    response = web.browser.get(f"/jobs/{job_id}")
    page = flat(response.text)
    assert response.status_code == 404
    assert "after 59 hours in all" in page
    assert "hours after you last work on them" in page
    assert "Files you downloaded are not affected" in page
    assert "hours after upload" not in page  # not the old, wrong promise
    assert 'class="button" href="/app#upload-heading">Upload the PDF again</a>' in response.text


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


def test_privacy_and_limits_name_the_saved_default_where_they_give_the_default(web):
    privacy = flat(web.browser.get("/privacy").text)
    limits = flat(web.browser.get("/limits").text)
    assert "unless you saved 7 days in Account" in privacy
    assert "can be made the default for new uploads in" in privacy
    assert "as your default in Account" in limits
