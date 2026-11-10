"""The second UX round, lane C: page picking (a parsed preview), sharing, account and deletion,
courses (documents per course, the starter set, adding documents already converted), the named
confirmations, the batch page's next step and elapsed time, and the error pages.

Browser behaviour (the listening player, the page fields, the keyboard walks) is in the
`*_browser.py` files next to these."""

from __future__ import annotations

import re
from html import unescape

import pytest
from sqlalchemy import select

from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, make_pdf, token
from readablestem.web import ranges
from readablestem.web.db import CourseNotation, Job, JobStatus, session_scope
from readablestem.web.routes.batch import _elapsed, _next_step
from readablestem.web.routes.parts import preview_summary


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


# --------------------------------------------------------------------------- the parts preview


def test_the_preview_counts_each_part_and_says_what_is_wrong_with_the_others():
    rows = ranges.preview("1-30\n80-90\n25-40\nabc", total=59, limit=30)
    assert [(r.number, r.count, bool(r.error)) for r in rows] == [
        (1, 30, False),
        (2, 0, True),
        (3, 0, True),
        (4, 0, True),
    ]
    assert rows[0].label == "1-30"
    assert "page 90 is not in this file, which has 59 pages" in rows[1].error
    assert "Page 25 is in part 1 and part 3" in rows[2].error
    assert "“abc” is not a page number or a range" in rows[3].error


def test_the_preview_agrees_with_the_parser_on_what_is_acceptable():
    for text in ("1-30\n31-59", "1-3, 7\n9", "5"):
        good = ranges.parse_parts(text, total=59, limit=30)
        shown = ranges.preview(text, total=59, limit=30)
        assert [p.label for p in good] == [r.label for r in shown]
        assert [len(p.pages) for p in good] == [r.count for r in shown]
    assert ranges.preview("", total=59, limit=30) == []
    assert ranges.preview("1-3", total=9, limit=30, taken={0})[0].error.endswith(
        "page 1 was already converted."
    )


def test_too_many_parts_are_one_more_row_not_a_crash():
    text = "\n".join(str(n) for n in range(1, ranges.MAX_PARTS + 4))
    rows = ranges.preview(text, total=99, limit=30)
    assert len(rows) == ranges.MAX_PARTS + 1 and rows[-1].error.startswith("That is ")


def test_the_summary_says_how_many_parts_and_pages_and_where_today_runs_out():
    rows = ranges.preview("1-30\n31-59", total=59, limit=30)
    assert preview_summary(rows, 100) == "2 parts, 59 pages."
    assert preview_summary(rows, 40) == (
        "2 parts, 59 pages. Only 40 pages are left today: "
        "the first 1 part starts, the rest wait here."
    )
    assert "none starts today" in preview_summary(rows, 10)
    bad = ranges.preview("1-30\nx", total=59, limit=30)
    assert preview_summary(bad, 100).endswith("Fix 1 before converting.")
    assert preview_summary([], 100) == "No parts yet."


# --------------------------------------------------------------------------- the chooser page


@pytest.fixture
def parts_web(tmp_path):
    env = WebEnv(
        tmp_path,
        max_large_upload_mb=5,
        max_source_pages=40,
        per_user_daily_page_quota=40,
    )
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.alice = client
        env.bob = env.client()
        login(env.bob, BOB)
        yield env


def keep_big(env, pages: int = 12) -> str:
    response = env.alice.post(
        "/jobs",
        data={"csrf_token": token(env.alice)},
        files={"pdf": ("chapter2.pdf", make_pdf(pages), "application/pdf")},
    )
    assert response.status_code == 303 and response.headers["location"].startswith("/uploads/")
    return response.headers["location"].rsplit("/", 1)[-1]


def test_the_chooser_leads_with_page_fields_and_a_preview_of_the_parts(parts_web):
    upload_id = keep_big(parts_web)
    page = parts_web.alice.get(f"/uploads/{upload_id}").text
    text = text_of(page)
    assert "3 parts, 12 pages." in text  # the suggestion, parsed
    assert re.findall(r'<span class="parts-pages">([\d-]+)</span>', page) == [
        "1-4",
        "5-8",
        "9-12",
    ]
    # The page fields are for script; the typed form is a disclosure that is open without it.
    assert 'data-parts-builder data-total="12" hidden' in page
    assert "Add part" in page and 'for="part-from">First page' in page
    assert re.search(r"<details[^>]*data-parts-advanced open", page)
    assert "Type the pages instead" in text and 'for="parts"' in page
    assert "/static/parts." in page
    # The daily quota is said once, not again in the hint of the box.
    assert text.count("40 pages left today") == 1


def test_an_error_keeps_what_was_typed_and_marks_the_part_that_is_wrong(parts_web):
    upload_id = keep_big(parts_web)
    token_value = token(parts_web.alice, f"/uploads/{upload_id}")
    response = parts_web.alice.post(
        f"/uploads/{upload_id}", data={"csrf_token": token_value, "parts": "1-4\n20-30"}
    )
    assert response.status_code == 400
    page = response.text
    assert "page 30 is not in this file, which has 12 pages" in text_of(page)
    assert 'class="parts-row parts-row-error"' in page
    assert "Fix 1 before converting." in text_of(page)
    assert "data-has-error" in page and "20-30" in page
    # The summary and the field error are linked to the box, which is open.
    assert 'aria-invalid="true"' in page


def test_the_preview_is_a_fragment_for_the_typed_text_and_only_the_owners(parts_web):
    upload_id = keep_big(parts_web)
    url = f"/uploads/{upload_id}/preview"
    hx = {"HX-Request": "true"}
    fragment = parts_web.alice.get(url, params={"parts": "1-2\n3-6"}, headers=hx)
    assert fragment.status_code == 200 and "<html" not in fragment.text
    assert 'id="parts-summary"' in fragment.text and 'hx-swap-oob="innerHTML"' in fragment.text
    assert "2 parts, 6 pages." in text_of(fragment.text)
    assert 'data-remove-part="2"' in fragment.text
    empty = parts_web.alice.get(url, params={"parts": ""}, headers=hx)
    assert "No parts yet." in text_of(empty.text)
    # Someone else's file answers as a missing one (htmx is told to reload).
    other = parts_web.bob.get(url, params={"parts": "1"}, headers=hx)
    assert other.status_code == 404


# --------------------------------------------------------------------------- named confirmations


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path, feature_notation_memory=True, signup_mode="open")
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        yield env


def finished(env, name: str = "notes.pdf", client=None) -> str:
    client = client or env.browser
    response = client.post(
        "/jobs",
        data={"csrf_token": token(client)},
        files={"pdf": (name, make_pdf(1), "application/pdf")},
    )
    job_id = job_id_from(response)
    assert env.run_worker() == job_id
    return job_id


def test_each_confirmation_names_what_it_deletes_and_keeps_a_button_of_the_same_size(web):
    job_id = finished(web)
    page = web.browser.get(f"/jobs/{job_id}/delete?from=app").text
    assert re.search(r'class="button-danger">Delete “[^”]+”</button>', page)
    assert 'class="button-secondary" href="/app">No, keep it</a>' in page
    assert "page-confirm" in page  # the compact footer rule

    response = web.browser.post(
        "/courses", data={"csrf_token": token(web.browser, "/courses"), "name": "Physics 1"}
    )
    course_id = response.headers["location"].rsplit("/", 1)[-1]
    page = web.browser.get(f"/courses/{course_id}/delete").text
    assert ">Delete “Physics 1”</button>" in page and "No, keep it</a>" in page

    page = web.browser.get("/account/delete").text
    assert ">Delete my account</button>" in page and "Yes, delete" not in page


def test_a_long_title_is_cut_in_the_button_and_whole_in_the_heading(web):
    job_id = finished(web)
    long_title = "A very long title " * 8
    with session_scope(web.ctx.factory) as session:
        session.get(Job, job_id).title_override = long_title.strip()
    page = web.browser.get(f"/jobs/{job_id}/delete").text
    button = re.search(r'class="button-danger">(Delete “[^”]*”)</button>', page)
    assert button and len(button.group(1)) < 70 and "…" in button.group(1)
    assert f"<h1>Delete “{long_title.strip()}”?</h1>" in page


# --------------------------------------------------------------------------- account


def test_the_retention_setting_comes_before_the_limits_and_names_what_it_saves(web):
    page = web.browser.get("/account").text
    assert page.index('id="account-keep-heading"') < page.index('id="account-limits"')
    assert ">Save how long documents are kept</button>" in page
    assert re.search(r"New documents are kept\s+<strong>\d+ hours</strong>", page)
    web.browser.post(
        "/account/keep-default",
        data={"csrf_token": token(web.browser, "/account"), "keep_default": "on"},
    )
    assert re.search(
        r"New documents are kept\s+<strong>7 days</strong>", web.browser.get("/account").text
    )


def test_the_deletion_confirmation_leads_with_exports_and_folds_the_long_wording_away(web):
    ready = finished(web)
    with session_scope(web.ctx.factory) as session:
        from readablestem.core.types import Status

        job = session.get(Job, ready)
        for fix in (job.result_json or {}).get("fixes", []):
            fix["status"], fix["review"], fix["reason"] = Status.ACCEPTED.value, "none", None
        job.result_json = {**(job.result_json or {})}
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(job, "result_json")
    page = web.browser.get("/account/delete").text
    text = text_of(page)
    assert page.index("Download what you want to keep first") < page.index("What is deleted")
    assert page.index("What is deleted") < page.index("Not reached by deleting")
    assert page.index("Not reached by deleting") < page.index(">Delete my account</button>")
    assert 'href="/app/download"' in page and 'href="/app"' in page
    # A short list, with the policy one link away.
    assert 'href="/privacy">privacy notice</a>' in page
    assert "Backups keep a copy for about a week." in text
    assert len(text.split()) < 400


def test_the_export_links_are_left_out_when_there_is_nothing_to_download(web):
    page = web.browser.get("/account/delete").text
    assert "Download what you want to keep first" not in page


# --------------------------------------------------------------------------- sharing


def finished_share_page(env) -> tuple[str, str]:
    job_id = finished(env)
    return job_id, env.browser.get(f"/jobs/{job_id}/share").text


def test_sharing_leads_with_the_steps_and_keeps_the_exact_address_rule_in_help(web):
    _job_id, page = finished_share_page(web)
    steps = text_of(page[page.index('class="share-steps"') :].split("</p>")[0])
    assert steps.endswith(
        "Add a person, copy the link, send it yourself. Nothing is emailed from here."
    )
    assert page.index('class="share-steps"') < page.index('id="add-heading"')
    # The long wording is in the help disclosure, after the controls.
    assert page.index('id="add-heading"') < page.index('class="share-help"')
    help_text = text_of(page[page.index('class="share-help"') :])
    assert "an alias does not match" in help_text
    # The expiry sits by the Share button and by Copy link.
    assert 'class="hint share-expiry-note"' in page and 'id="share-expiry"' in page
    assert "Also makes review decisions" in page


def test_an_error_summary_sits_directly_above_the_form_it_is_about(web):
    job_id = finished(web)
    response = web.browser.post(
        f"/jobs/{job_id}/share",
        data={
            "csrf_token": token(web.browser, f"/jobs/{job_id}/share"),
            "email": "nope",
            "role": "view",
        },
    )
    page = response.text
    summary = page.index('class="error-summary"')
    form = page.index('id="add-heading"')
    between = text_of(page[summary:form])
    assert summary < form and len(between) < 400  # no paragraphs of policy in between
    assert "Give a named person access" not in page[:form]


# --------------------------------------------------------------------------- courses


def new_course(env, name: str = "Physics 1") -> str:
    response = env.browser.post(
        "/courses", data={"csrf_token": token(env.browser, "/courses"), "name": name}
    )
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[-1]


def post(env, path: str, page: str, **fields):
    return env.browser.post(path, data={"csrf_token": token(env.browser, page)} | fields)


def test_the_course_list_shows_documents_and_a_configure_notation_action(web):
    course_id = new_course(web)
    web.browser.post(
        "/jobs",
        data={"csrf_token": token(web.browser), "course": course_id},
        files={"pdf": ("a.pdf", make_pdf(1), "application/pdf")},
    )
    page = web.browser.get("/courses").text
    assert '<th scope="col">Documents</th>' in page
    row = page[page.index('class="course-table"') :]
    assert re.search(r'data-label="Documents">1</td>', row)
    assert f'href="/courses/{course_id}#entries-heading">Set up notation' in row
    post(
        web,
        f"/courses/{course_id}/entries",
        f"/courses/{course_id}",
        symbol="bold d",
        meaning="vector",
        note="",
    )
    assert "Edit notation" in web.browser.get("/courses").text


def test_an_empty_course_page_offers_an_editable_starter_set_and_adds_only_the_ticked(web):
    course_id = new_course(web)
    page = web.browser.get(f"/courses/{course_id}").text
    assert "Start from common symbols" in page and page.count('class="starter-row"') == 6
    assert 'value="bold v"' in page and 'value="bold A"' in page
    assert re.search(r'<option value="matrix" selected>A matrix</option>', page)
    path = f"/courses/{course_id}"
    # Tick the first row (changed to "bold w") and the fifth; nothing else is added.
    symbols = ["bold w", "bold a", "bold F", "bold r", "bold A", "bold B"]
    meanings = ["vector", "vector", "vector", "vector", "matrix", "matrix"]
    response = web.browser.post(
        f"{path}/starter",
        data={
            "csrf_token": token(web.browser, path),
            "symbol": symbols,
            "meaning": meanings,
            "pick": ["0", "4"],
        },
    )
    assert response.status_code == 303 and response.headers["location"] == path
    with session_scope(web.ctx.factory) as session:
        added = sorted(
            (e.symbol, e.meaning, e.source) for e in session.scalars(select(CourseNotation))
        )
    assert added == [("bold A", "matrix", "user"), ("bold w", "vector", "user")]
    # Once the course has symbols the starter set is not offered again.
    assert "Start from common symbols" not in web.browser.get(path).text


def test_nothing_is_added_from_the_starter_set_unasked_or_unreadable(web):
    course_id = new_course(web)
    path = f"/courses/{course_id}"
    none = web.browser.post(
        f"{path}/starter",
        data={"csrf_token": token(web.browser, path), "symbol": ["bold v"], "meaning": ["vector"]},
    )
    assert none.status_code == 303
    assert web.browser.get(path).text.count("Check the symbols to add first") == 1
    with session_scope(web.ctx.factory) as session:
        assert session.scalar(select(CourseNotation)) is None
    odd = web.browser.post(
        f"{path}/starter",
        data={
            "csrf_token": token(web.browser, path),
            "symbol": ["???"],
            "meaning": ["vector"],
            "pick": ["0"],
        },
    )
    assert odd.status_code == 303
    assert "1 could not be added" in text_of(web.browser.get(path).text)


def test_documents_already_converted_can_be_added_to_a_course_and_the_page_says_what_that_does(web):
    course_id = new_course(web)
    kept = finished(web, "kept.pdf")
    other = new_course(web, "Other")
    inside = web.browser.post(
        "/jobs",
        data={"csrf_token": token(web.browser), "course": other},
        files={"pdf": ("inside.pdf", make_pdf(1), "application/pdf")},
    )
    assert web.run_worker() == job_id_from(inside)
    path = f"/courses/{course_id}"
    page = web.browser.get(path).text
    assert "Add converted documents (1)" in page
    assert (
        f'id="attach-{kept}"' in page
        and job_id_from(inside) not in page.split("attach-documents")[1]
    )
    text = text_of(page)
    assert "It is not converted again" in text and "upload it again for this course" in text

    response = web.browser.post(
        f"{path}/documents",
        data={
            "csrf_token": token(web.browser, path),
            "job": [kept, job_id_from(inside), "0" * 32],
        },
    )
    assert response.status_code == 303
    assert web.job(kept).course_id == course_id
    assert web.job(job_id_from(inside)).course_id == other  # already in a course: left alone
    assert "Added 1 document to Physics 1. They were not converted again." in text_of(
        web.browser.get(path).text
    )
    assert "Add converted documents" not in web.browser.get(path).text


def test_only_ones_own_finished_documents_can_be_added(web):
    course_id = new_course(web)
    bob = web.client()
    login(bob, BOB)
    theirs = finished(web, "bobs.pdf", client=bob)
    queued = web.browser.post(
        "/jobs",
        data={"csrf_token": token(web.browser)},
        files={"pdf": ("queued.pdf", make_pdf(1), "application/pdf")},
    )
    path = f"/courses/{course_id}"
    web.browser.post(
        f"{path}/documents",
        data={"csrf_token": token(web.browser, path), "job": [theirs, job_id_from(queued)]},
    )
    assert web.job(theirs).course_id is None
    assert web.job(job_id_from(queued)).course_id is None
    assert web.job(job_id_from(queued)).status == JobStatus.QUEUED
    # Someone else's course is a 404.
    assert bob.post(
        f"{path}/documents", data={"csrf_token": token(bob), "job": [theirs]}
    ).status_code in {403, 404}


def test_the_course_page_keeps_notation_documents_and_settings_apart(web):
    course_id = new_course(web)
    page = web.browser.get(f"/courses/{course_id}").text
    positions = [
        page.index(f'id="{i}"')
        for i in ("entries-heading", "course-documents-heading", "settings-heading")
    ]
    assert positions == sorted(positions)
    assert "Rename or delete" in page


def test_the_course_list_has_a_first_course_example_when_empty(web):
    page = web.browser.get("/courses").text
    assert "You have no courses yet." in page and "Add your first course" in page
    assert "common symbols to start from" in page


# --------------------------------------------------------------------------- the batch page


def test_elapsed_time_is_said_in_words():
    assert _elapsed(0) == "less than a minute"
    assert _elapsed(1) == "1 minute" and _elapsed(59) == "59 minutes"
    assert _elapsed(60) == "1 hour" and _elapsed(125) == "2 hours 5 minutes"


def test_the_next_step_follows_the_state_of_the_batch():
    wait = _next_step([{"state": "waiting", "id": "a"}, {"state": "ready", "id": "b"}])
    assert "Nothing to do yet" in wait["text"] and "href" not in wait
    decide = _next_step([{"state": "decide", "id": "a"}, {"state": "decide", "id": "b"}])
    assert decide["href"] == "/jobs/a/review" and decide["label"] == "Review the first one"
    assert "2 documents need your decisions" in decide["text"]
    assert "stopped" in _next_step([{"state": "stopped", "id": "a"}])["text"]
    assert "Every document is ready" in _next_step([{"state": "ready", "id": "a"}])["text"]
    assert _next_step([]) == {}


# --------------------------------------------------------------------------- error pages


def test_the_fixed_error_pages_carry_the_wordmark_status_and_support_links(tmp_path):
    from readablestem.web.app import _static_error_page

    for name, status in (("500.html", 500), ("503.html", 503)):
        page = _static_error_page(name, status, "help@example.org").body.decode()
        assert 'class="brand"' in page and "ReadableSTEM</a>" in page
        assert 'href="/status"' in page and 'href="/app"' in page and 'href="/"' in page
        assert 'href="mailto:help@example.org">Email for help</a>' in page
        assert "<!--support-->" not in page and "{{" not in page and "{#" not in page
        assert '<link rel="stylesheet" href="/static/app.css">' in page
    bare = _static_error_page("500.html", 500, None).body.decode()
    assert "Email for help" not in bare and "<!--support-->" not in bare
    hostile = _static_error_page("503.html", 503, '"><script>x</script>').body.decode()
    assert "<script>x" not in hostile


def test_a_404_says_what_to_try_for_the_kind_of_page(web):
    mine = web.browser.get(f"/jobs/{'0' * 32}")
    assert mine.status_code == 404
    text = text_of(mine.text)
    assert "A deleted document, or one not shared with you, looks like this too." in text
    assert 'class="button" href="/app"' in mine.text
    course = web.browser.get(f"/courses/{'0' * 32}")
    assert "A deleted course looks like this too." in text_of(course.text)
    assert 'class="button" href="/courses"' in course.text
    other = web.browser.get("/no-such-page")
    assert "looks like this too" not in other.text


def test_a_404_for_someone_elses_job_matches_one_for_a_made_up_id(web):
    bob = web.client()
    login(bob, BOB)
    theirs = finished(web, "bobs.pdf", client=bob)
    strangers = web.browser.get(f"/jobs/{theirs}")
    invented = web.browser.get(f"/jobs/{'1' * 32}")
    assert strangers.status_code == invented.status_code == 404
    assert text_of(strangers.text) == text_of(invented.text)


def test_a_server_error_page_has_the_status_and_support_links(web):
    from starlette.exceptions import HTTPException

    def fail():
        raise HTTPException(status_code=502, detail="x")

    web.app.add_api_route("/fail502", fail)
    page = web.browser.get("/fail502").text
    assert 'href="/status">service status</a>' in page and "mailto:" in page
