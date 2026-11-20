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

from fixtures.web.support import ALICE, WebEnv, job_id_from, login, make_pdf, upload
from readablestem.core.types import ConversionOptions


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


def test_a_language_chosen_at_upload_is_the_documents_language(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1), language="es"))
    assert web.job(job_id).language == "es"
    web.run_worker()
    options = web.converter.calls[-1]["options"]
    assert options.language == "es"
    # Anything the page does not offer is "Detect it".
    other = job_id_from(upload(web.browser, make_pdf(1), language="xx"))
    assert web.job(other).language is None
    assert 'name="language"' in web.browser.get("/app").text


def test_the_choice_is_said_wherever_the_document_is_handled_and_never_to_the_student(web):
    exam = job_id_from(upload(web.browser, make_pdf(1), filename="midterm.pdf", exam=True))
    plain = job_id_from(upload(web.browser, make_pdf(1), filename="notes.pdf"))
    web.run_worker()
    web.run_worker()

    def words(path: str) -> str:
        response = web.browser.get(path)
        assert response.status_code == 200, path
        return text_of(response.text)

    on, off = "figure descriptions do not give answers", "Not an exam or quiz"
    assert on in words(f"/jobs/{exam}") and off not in words(f"/jobs/{exam}")
    assert off in words(f"/jobs/{plain}") and on not in words(f"/jobs/{plain}")
    # The owner can change it: the revised upload asks for the file again, box ready.
    assert f"/app?revises={exam}" in web.browser.get(f"/jobs/{exam}").text
    assert (
        'id="exam" name="exam" value="on" checked' in web.browser.get(f"/app?revises={exam}").text
    )
    assert on in words(f"/jobs/{exam}/review")
    assert on not in words(f"/jobs/{plain}/review")
    assert on in words(f"/jobs/{exam}/record") and off in words(f"/jobs/{plain}/record")
    assert on in web.browser.get(f"/jobs/{exam}/record.csv").text
    assert (
        web.browser.get("/app/documents", headers={"HX-Request": "true"}).text.count(
            "Exam or quiz</span>"
        )
        == 1
    )  # midterm only
    student = web.browser.get(f"/jobs/{exam}/download", follow_redirects=True)
    assert student.status_code == 200 and "<main" in student.text
    assert "exam or quiz" not in student.text.casefold()


def test_a_document_named_like_an_exam_but_not_converted_as_one_is_said_to_its_owner(web):
    named = job_id_from(upload(web.browser, make_pdf(1), filename="Midterm2_Fall.pdf"))
    ticked = job_id_from(upload(web.browser, make_pdf(1), filename="midterm.pdf", exam=True))
    final = job_id_from(upload(web.browser, make_pdf(1), filename="Lecture_Notes_FINAL.pdf"))
    for _ in range(3):
        web.run_worker()
    hint = "says “midterm”"

    page = web.browser.get(f"/jobs/{named}").text
    assert hint in page and f"/app?revises={named}" in page
    assert hint not in web.browser.get(f"/jobs/{ticked}").text  # chosen on purpose
    assert hint not in web.browser.get(f"/jobs/{final}").text  # "final" alone is a draft label
    assert web.job(named).exam_mode is False  # a hint only: the choice is still off


def test_a_source_document_says_nothing_of_the_exam_choice(web):
    """A Word or LaTeX source is read from its own file: the choice does not apply, so neither
    its job page nor its record says "Not an exam or quiz"."""
    from fixtures.web.review_doc import review_output, review_pdf

    pdf = job_id_from(upload(web.browser, review_pdf()))
    web.run_worker()
    asked = "Not an exam or quiz"
    assert asked in web.browser.get(f"/jobs/{pdf}").text  # a PDF says it
    output = review_output()
    output.result.origin = "latex"
    web.converter.output = output
    source = job_id_from(upload(web.browser, review_pdf()))
    web.run_worker()
    assert web.job(source).status == "done"
    for path in (f"/jobs/{source}", f"/jobs/{source}/record"):
        page = web.browser.get(path)
        assert page.status_code == 200 and asked not in page.text, path


# --------------------------------------------------------------------------- the note


# --------------------------------------------------------------------------- the reject line
