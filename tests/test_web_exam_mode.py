"""Pipeline hooks in the web app (tier 1 pipeline items on the web side).

- "Exam or quiz: describe figures without giving answers" on the upload form: stored on the
  job (`Job.exam_mode`) and passed to the conversion as `ConversionOptions.exam_mode`
  (`web.notation.conversion_options`), with or without a course's notation.
- The reviewer's sentence for the note `equation.single_reading` / `single_reading`.
- What the review screen says screen readers hear for an equation kept as an image.
"""

from __future__ import annotations

import re
from html import unescape

import pytest

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, make_pdf, token, upload
from readablestem.core.types import Check, ConversionOptions
from readablestem.pipeline.equation_step import SINGLE_READING_CODE
from readablestem.web import notation
from readablestem.web.messages import CHECK_MESSAGES, explain_check


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


@pytest.fixture
def with_notation(tmp_path):
    env = WebEnv(tmp_path, feature_notation_memory=True)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


# --------------------------------------------------------------------------- the box


def test_the_upload_form_has_the_exam_box_unticked_and_described(web):
    page = web.browser.get("/app").text
    box = re.search(r'<input type="checkbox" id="exam" name="exam"[^>]*>', page)
    assert box is not None
    assert "checked" not in box.group(0)
    assert 'aria-describedby="exam-hint"' in box.group(0)
    assert '<label for="exam">Exam or quiz: describe figures without giving answers</label>' in page
    assert 'id="exam-hint"' in page


def test_a_ticked_box_is_stored_and_the_conversion_runs_in_exam_mode(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1), exam=True))
    assert web.job(job_id).exam_mode is True
    web.run_worker()
    options = web.converter.calls[-1]["options"]
    assert isinstance(options, ConversionOptions)
    assert options.exam_mode is True
    assert options.notation == []  # no course: the exam choice alone
    # The deployment's flags still apply (from_settings), as for a course's options.
    assert options.tables == web.settings.feature_tables


def test_an_unticked_box_leaves_the_run_as_it_was(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    assert web.job(job_id).exam_mode is False
    web.run_worker()
    assert web.converter.calls[-1]["options"] is None


def test_exam_mode_comes_with_the_courses_notation(with_notation):
    client = with_notation.browser
    made = client.post(
        "/courses", data={"csrf_token": token(client, "/courses"), "name": "Physics 1"}
    )
    course_id = made.headers["location"].rsplit("/", 1)[-1]
    client.post(
        f"/courses/{course_id}/entries",
        data={
            "csrf_token": token(client, f"/courses/{course_id}"),
            "symbol": "bold d",
            "meaning": "vector",
            "note": "",
        },
    )
    fields = {"csrf_token": token(client), "course": course_id, "exam": "on"}
    files = {"pdf": ("notes.pdf", make_pdf(1), "application/pdf")}
    assert client.post("/jobs", data=fields, files=files).status_code == 303
    with_notation.run_worker()
    options = with_notation.converter.calls[-1]["options"]
    assert options.exam_mode is True and len(options.notation) == 1
    # The same course without the box: notation only.
    del fields["exam"]
    assert client.post("/jobs", data=fields, files=files).status_code == 303
    with_notation.run_worker()
    options = with_notation.converter.calls[-1]["options"]
    assert options.exam_mode is False and len(options.notation) == 1


def test_conversion_options_for_a_missing_job_is_none(web):
    assert notation.conversion_options(web.ctx.factory, web.settings, "0" * 32) is None


def test_only_the_value_on_is_a_yes(web):
    fields = {"csrf_token": token(web.browser), "exam": "yes"}
    files = {"pdf": ("notes.pdf", make_pdf(1), "application/pdf")}
    job_id = job_id_from(web.browser.post("/jobs", data=fields, files=files))
    assert web.job(job_id).exam_mode is False


# --------------------------------------------------------------------------- the note


def test_the_single_reading_note_has_a_sentence_for_the_reviewer():
    check = Check(
        name="equation.single_reading", passed=False, severity="warning", code=SINGLE_READING_CODE
    )
    explained = explain_check(check)
    assert explained.known
    assert explained.code == "equation.single_reading: single_reading"
    assert explained.text == CHECK_MESSAGES[("equation.single_reading", "single_reading")]
    assert "read this equation once" in explained.text


def test_the_review_screen_shows_the_single_reading_note_on_an_accepted_equation(tmp_path):
    output = review_output()
    for fix in output.result.fixes:
        if fix.element_id == "p1-e5":  # an accepted equation with an optional note
            fix.checks = [
                Check(
                    name="equation.single_reading",
                    passed=False,
                    severity="warning",
                    code=SINGLE_READING_CODE,
                )
            ]
    env = WebEnv(tmp_path)
    env.converter.output = output
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        page = client.get(f"/jobs/{job_id}/review/list").text
    text = text_of(page)
    assert "Note: " + CHECK_MESSAGES[("equation.single_reading", "single_reading")] in text


# --------------------------------------------------------------------------- the reject line


def test_keep_as_image_says_screen_readers_hear_the_pdf_text_marked_unchecked(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = review_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        page = client.get(f"/jobs/{job_id}/review").text
    text = text_of(page)
    assert (
        "Keep as image: Students see a picture of the original; screen readers hear the PDF "
        "text of the equation, marked as not checked." in text
    )
    assert "say only" not in text
