"""The job page in each state, and its routes: progress, the draft question, the language.

The fake converter's result is changed per test (as in test_web_job_page.py): it has a
heading, a paragraph, two accepted or waiting equations, an abstained equation and a
figure waiting for a decision, so 3 decisions are open until a test makes them.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from html import unescape

import pytest

from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, make_pdf, token, upload
from readablestem.core.types import (
    Check,
    DocumentResult,
    Element,
    EquationFix,
    Fix,
    Kind,
    Notice,
    OmittedPage,
    SourceFacts,
    Status,
)
from readablestem.web import states
from readablestem.web.db import Job, JobStatus, session_scope


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        yield env


def page_text(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def title(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, flags=re.S)
    assert match
    return unescape(" ".join(match.group(1).split()))


def section(html: str, heading_id: str) -> str:
    start = html.index(f'id="{heading_id}"')
    return html[start : html.index("</section>", start)]


def convert(web, **update) -> str:
    result = web.converter.output.result
    for name, value in update.items():
        setattr(result, name, value)
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    assert web.run_worker() == job_id
    return job_id


def decide_everything(web, *, keep_as_image: tuple[str, ...] = ()) -> None:
    """Make every open decision of the fake result before it is converted: accept the
    proposals, fill in nothing (an abstained equation is kept as an image)."""
    for fix in web.converter.output.result.fixes:
        if fix.pending:
            if fix.element_id in keep_as_image or fix.status is Status.ABSTAINED:
                fix.status = Status.REJECTED
            else:
                fix.status = Status.ACCEPTED
            fix.reviewed = True


def stored(web, job_id: str) -> DocumentResult:
    return DocumentResult.model_validate(web.job(job_id).result_json)


# --------------------------------------------------------------------------- headlines


def test_open_decisions_are_the_headline_and_the_review_is_one_click_away(web):
    job_id = convert(web)
    html = web.browser.get(f"/jobs/{job_id}").text
    # The state is said once: decisions open is never "Converted" or "Ready" (F-033).
    label = states.TABLE["needs_decisions"].label
    assert title(html) == f"{label}: SECRET-HEADING Kinematics | ReadableSTEM"
    status = page_text(section_of_status(html))
    assert label in status and "Converted" not in status
    assert "3 decisions need you before you give it to students." in status
    actions = html[
        html.index('id="next-actions"') : html.index("</ul>", html.index("next-actions"))
    ]
    assert f'<a class="button" href="/jobs/{job_id}/review?item=p1-e3">Review 3 decisions</a>' in (
        actions
    )
    assert f'href="/jobs/{job_id}/draft">Download a draft (3 decisions open)</a>' in actions
    assert f'href="/jobs/{job_id}/download"' not in html  # downloading asks first
    assert 'id="ready-heading"' not in html


def section_of_status(html: str) -> str:
    start = html.index('id="job-status"')
    return html[start : html.index("</div>", start)]


def test_nothing_to_decide_is_ready_to_share_with_the_download_first(web):
    decide_everything(web, keep_as_image=())
    result = web.converter.output.result
    # No image left: the abstained equation goes, its element too (an element with no fix
    # is shown as its picture, so the job would be only "Ready", SW7).
    result.fixes = [f for f in result.fixes if f.element_id != "p1-e5"]
    result.elements = [e for e in result.elements if e.id != "p1-e5"]
    for fix in result.fixes:
        fix.reviewed = False  # accepted by the pipeline: nothing needed a decision
    job_id = convert(web)
    html = web.browser.get(f"/jobs/{job_id}").text
    assert title(html) == "Ready to share: SECRET-HEADING Kinematics | ReadableSTEM"
    ready = page_text(section(html, "ready-heading"))
    assert "The tool flagged nothing in this file for review, and nobody has reviewed it." in ready
    assert f'<a class="button" href="/jobs/{job_id}/download">Download the HTML file</a>' in html
    assert "Before you share" in ready and "Where will you post it?" in ready
    assert "Recommended" in ready
    assert "Do not paste it into a page editor" in ready
    # As the posting help says: the HTML is the main version, linked where the PDF was.
    assert "link to it where the PDF was." in ready
    assert "next to the PDF" not in ready
    # One link to the posting help (ux-a's Canvas steps), in the recommended route;
    # the two questions after a download follow it, hidden until a download.
    main = html[html.index("<main") : html.index("</main>")]  # the footer links it too
    assert main.count('href="/help/posting"') == 1
    assert 'href="/help/posting">How to post it in your course</a>' in section(
        html, "ready-heading"
    )
    assert html.index('id="post-heading"') < html.index('id="feedback"')
    assert "draft" not in html.lower()


def test_an_ai_outage_is_not_shown_as_finished(web):
    result = web.converter.output.result
    for fix in result.fixes:
        if fix.kind in (Kind.EQUATION, Kind.FIGURE):
            fix.status, fix.reason, fix.equation, fix.figure = (
                Status.FAILED,
                "model_timeout",
                None,
                None,
            )
    job_id = convert(web)
    html = web.browser.get(f"/jobs/{job_id}").text
    # Decisions are open, so that is the state; the outage is its gap note.
    label = states.TABLE["needs_decisions"].label
    assert title(html) == f"{label}: SECRET-HEADING Kinematics | ReadableSTEM"
    text = page_text(html)
    assert "did not answer for 4 of 4 equations and figures" in text
    assert "Nothing is wrong with your PDF" in text
    assert "4 decisions need you before you give it to students." in text
    assert 'id="partial-note"' in html
    assert "All 0 checks" not in text


def stop(web, job_id: str, code: str, retries: int = 0) -> None:
    """The job as a stopped conversion leaves it: its earlier result still stored, and the
    worker's progress stopped part way through the figures."""
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.status, job.error_code, job.retries = JobStatus.FAILED, code, retries
        job.progress_json = {"stages": {"layout": {"done": 1, "total": 1}, "figures": {}}}


def test_a_stopped_job_offers_no_decisions_and_nothing_to_keep(web):
    """F-314, F-316: a stopped job's stored result had 3 decisions open; the page does not say
    they need you (there is no review to open) and does not offer to keep the document."""
    job_id = convert(web)
    stop(web, job_id, "timeout")
    html = web.browser.get(f"/jobs/{job_id}").text
    status = page_text(section_of_status(html))
    assert states.TABLE["stopped_service"].label in status
    assert "decisions need you" not in page_text(html)
    assert f'action="/jobs/{job_id}/keep"' not in html
    assert f'action="/jobs/{job_id}/retry"' in html


def test_a_job_with_no_tries_left_is_never_told_to_try_again(web):
    """F-315: with the three tries used, nothing on the page offers "Try again"."""
    job_id = convert(web)
    stop(web, job_id, "timeout", retries=states.MAX_RETRIES_PER_JOB)
    html = web.browser.get(f"/jobs/{job_id}").text
    assert states.TABLE["stopped_no_tries"].label in page_text(section_of_status(html))
    assert "Try again" not in page_text(html)
    assert f'action="/jobs/{job_id}/retry"' not in html


def test_an_unknown_stop_names_its_step_and_can_be_tried_again(web):
    """F-317: a code this version does not know says the step it stopped in, that the cause is
    not known, and offers "Try again" while tries are left."""
    job_id = convert(web)
    stop(web, job_id, "a_code_this_version_does_not_know")
    html = web.browser.get(f"/jobs/{job_id}").text
    status = page_text(section_of_status(html))
    assert states.TABLE["stopped_unknown"].label in status
    assert any(words in status for words in states.STEP_WORDS.values())
    assert states.UNKNOWN_CAUSE in status
    assert f'action="/jobs/{job_id}/retry"' in html


def test_the_state_is_said_once_not_again_as_the_heading_over_the_actions(web):
    """F-415: the status box says the state; the heading over the actions names the task."""
    job_id = convert(web)
    html = web.browser.get(f"/jobs/{job_id}").text
    heading = re.search(r'<h2 id="decisions-heading">([^<]*)</h2>', html)
    said = title(html).split(":")[0]  # the state, as the title and the status box say it
    assert heading and heading.group(1).strip() != said


# --------------------------------------------------------------------------- counts


def test_the_counts_add_up_and_match_the_review_screen(web):
    job_id = convert(web)
    html = web.browser.get(f"/jobs/{job_id}").text
    results = page_text(section(html, "results-heading"))
    # Decisions are the one unit (T1-11): no count of items waiting, accepted or decided.
    assert "Of the 5 items" not in results and "were accepted for you" not in results
    assert "1 equation is shown as an image that screen readers cannot read." in results
    review = page_text(web.browser.get(f"/jobs/{job_id}/review").text)
    assert "3" in review and "Decisions to make 3" in results


# --------------------------------------------------------------------------- what was checked

# A page stored before the statement existed: no render meta, so the app makes it again from
# the result (`routes.jobs.current_html`) and every surface reads the same result.
OLD_PAGE = "<!doctype html><html lang='en'><head><title>T</title></head><body></body></html>"


def clean(result: DocumentResult) -> None:
    """The fake result with every part converted and nothing flagged (a clean typed page)."""
    result.elements = [e for e in result.elements if e.id != "p1-e5"]
    result.fixes = [f for f in result.fixes if f.element_id != "p1-e5"]
    for fix in result.fixes:
        fix.status, fix.reviewed, fix.checks = Status.ACCEPTED, False, []
    result.html = OLD_PAGE


def equations(result: DocumentResult, n: int, **fix) -> None:
    """`n` block equations in place of the fake result's parts; with `fix`, each has a
    converted proposal with those fields."""
    result.elements = [result.elements[0]] + [
        Element(id=f"p1-q{i}", kind=Kind.EQUATION, page=0, bbox=(0, i, 1, i + 1),
                order=i + 1, display="block")
        for i in range(n)
    ]  # fmt: skip
    result.fixes = [result.fixes[0]]
    if fix:
        result.fixes += [
            Fix(element_id=f"p1-q{i}", kind=Kind.EQUATION,
                equation=EquationFix(latex="x", mathml="<math><mi>x</mi></math>",
                                     speech="x", display="block"), **fix)
            for i in range(n)
        ]  # fmt: skip
    result.html = OLD_PAGE


def no_mathml(result: DocumentResult) -> None:
    equations(result, 61)  # found, but no proposal was written for any


def pages_left_out(result: DocumentResult) -> None:
    clean(result)
    result.stats.pages = 8
    result.omitted = [OmittedPage(page=p) for p in range(1, 8)]


def accepted_anyway(result: DocumentResult) -> None:
    failing = Check(name="mathml.parses", passed=False, severity="error", code="bad_mathml")
    equations(result, 34, status=Status.ACCEPTED, reviewed=True, checks=[failing])


def optional_unopened(result: DocumentResult) -> None:
    equations(result, 26, status=Status.ACCEPTED, review="optional")


def statement_of(web, job_id: str) -> dict[str, object]:
    """What each surface says about the job: the student page (its notice and its note),
    the job page, the EPUB's accessibility metadata and the record's CSV."""
    student = web.browser.get(f"/jobs/{job_id}/view").text
    note = student[student.index('<footer id="about-conversion"') :]
    notice = re.search(r'<section id="checked-notice".*?</section>', student, flags=re.S)
    job = web.browser.get(f"/jobs/{job_id}").text
    epub = zipfile.ZipFile(io.BytesIO(web.browser.get(f"/jobs/{job_id}/download.epub").content))
    package = unescape(epub.read("EPUB/package.opf").decode())
    rows = csv.reader(io.StringIO(web.browser.get(f"/jobs/{job_id}/record.csv").text))
    return {
        "note": page_text(note[: note.index("</footer>")]),
        "notice": page_text(notice.group(0)) if notice else "",
        "state": title(job).split(":")[0],
        "job": page_text(job),
        "summary": re.search(r'accessibilitySummary">([^<]*)<', package).group(1),
        "sufficient": "accessModeSufficient" in package,
        "csv": {r[1]: r[7] for r in rows if r and r[0] == "summary"},
    }


@pytest.mark.parametrize(
    ("make", "expected"),
    [
        pytest.param(
            no_mathml,
            {
                "state": "Ready",
                "job": "61 equations are shown as images that screen readers cannot read.",
                "note": "61 parts could not be converted and are shown as pictures",
                "notice": "Part 1 not converted: equation, page 1",
                "summary": "0 of 61 equations are MathML.",
                "csv": {"Shown as pictures of the original": "61"},
            },
            id="0-of-61-equations-in-mathml",
        ),
        pytest.param(
            pages_left_out,
            {
                "state": "Ready",
                "job": "7 of 8 pages were left out.",
                "note": "Pages 2, 3, 4, 5, 6, 7 and 8 (7 of 8) of the original PDF were left out",
                "notice": "Which pages were left out",
                "summary": "were left out of this file",
                "csv": {"Every part the tool found was converted.": "No"},
            },
            id="7-of-8-pages-left-out",
        ),
        pytest.param(
            accepted_anyway,
            {
                "state": "Ready to share",
                "job": "You reviewed 34 of 34 flagged parts. 34 parts kept with a check failing.",
                "note": "Your instructor reviewed 34 of 34 parts the tool flagged. 34 parts were "
                "kept as the tool wrote them without a check passing.",
                "notice": "",
                "summary": "34 parts were kept as the tool wrote them without a check passing.",
                "csv": {"Accepted by a person with a check failing": "34"},
            },
            id="34-of-34-accepted-anyway",
        ),
        pytest.param(
            optional_unopened,
            {
                "state": "Ready to share",
                "job": "Nobody has reviewed this file yet. 26 optional notes not opened.",
                "note": "This version has not been reviewed by a person.",
                "notice": "This version has not been reviewed by a person.",
                "summary": "This version has not been reviewed by a person.",
                "csv": {"Optional notes never opened": "26"},
            },
            id="26-optional-never-opened",
        ),
        pytest.param(
            clean,
            {
                "state": "Ready to share",
                "job": "Every part the tool found was converted.",
                "note": "Its equations are written so that screen readers can read them aloud.",
                "notice": "This version has not been reviewed by a person.",
                "summary": "2 of 2 equations are MathML.",
                "csv": {"Every part the tool found was converted.": "Yes"},
            },
            id="clean-typed-control",
        ),
    ],
)
def test_every_surface_says_what_was_converted_and_reviewed(web, make, expected):
    """SW7 (F-034, F-110): the student page, its opening notice, the job's state, the job
    page, the EPUB's accessibility summary and the record say one statement, from one model,
    and none says more than was done: no "Ready to share" or "Everything was converted"
    while a part is a picture or a page is left out, no "reviewed" for notes nobody opened,
    and an "Accept anyway" said apart."""
    make(web.converter.output.result)
    said = statement_of(web, convert(web))
    assert said["state"] == expected["state"]
    assert expected["job"] in said["job"]
    assert expected["note"] in said["note"]
    if expected["notice"]:
        assert expected["notice"] in said["notice"]
    else:
        assert said["notice"] == ""  # reviewed, nothing a picture or left out
    assert expected["summary"] in said["summary"]
    # Text alone is enough only when nothing is a picture or left out.
    assert said["sufficient"] == (expected["state"] == "Ready to share")
    for label, value in expected["csv"].items():
        assert said["csv"][label] == value
    for words in ("Everything was converted", "checked the parts", "Your instructor checked"):
        assert words not in said["job"] + said["note"] + said["summary"]


# --------------------------------------------------------------------------- progress


def events(**stages) -> dict:
    return {
        "stages": {
            name: {"stage": name, "done": d, "total": t, "counts": c}
            for name, (d, t, c) in stages.items()
        }
    }


# --------------------------------------------------------------------------- draft


def test_a_draft_download_asks_first_and_says_what_students_get(web):
    job_id = convert(web)
    page = web.browser.get(f"/jobs/{job_id}/draft")
    assert page.status_code == 200
    text = page_text(page.text)
    assert "3 decisions are still open." in text
    assert "For 1 equation and 1 figure, students will get the tool's suggestion as it is." in (
        text
    )  # the abstained equation is the image
    assert "1 equation is an image marked “Not converted”." in text
    assert (
        f'<a class="button" href="/jobs/{job_id}/review?item=p1-e3">Review 3 decisions first'
        in (page.text)
    )


def test_with_no_decision_open_the_draft_page_goes_to_the_finish_line(web):
    decide_everything(web)
    job_id = convert(web)
    response = web.browser.get(f"/jobs/{job_id}/draft")
    assert response.status_code == 303 and response.headers["location"].endswith("#ready")


def test_the_draft_page_is_the_owners_only(web):
    job_id = convert(web)
    bob = web.client()
    login(bob, BOB)
    assert bob.get(f"/jobs/{job_id}/draft").status_code == 404
    bob.close()


# --------------------------------------------------------------------------- notices


# --------------------------------------------------------------------------- card


FACTS = SourceFacts(tagged=False, images=1, equations=3, equations_flattened=1,
                    bold_maths_letters=0, links=0)  # fmt: skip


def test_the_card_never_logs_document_text(web, caplog):
    caplog.set_level(logging.DEBUG)
    result = web.converter.output.result
    result.elements[3].text = "SECRET-DOC-TEXT x2"
    result.elements[3].reasons = ["superscript"]
    job_id = convert(web, source_facts=FACTS)
    page = web.browser.get(f"/jobs/{job_id}").text
    assert "SECRET minus DOC minus TEXT x 2" in page  # shown to the owner, as read out
    assert "SECRET" not in caplog.text


# --------------------------------------------------------------------------- language


def post_language(web, job_id: str, lang: str, client=None):
    client = client or web.browser
    return client.post(f"/jobs/{job_id}/language", data={"csrf_token": token(client), "lang": lang})


def test_the_language_can_be_confirmed_or_changed(web, caplog):
    caplog.set_level(logging.INFO)
    job_id = convert(web, lang_source="detected", notices=[Notice(code="lang_detected")])
    html = web.browser.get(f"/jobs/{job_id}").text
    language = section(html, "language-heading")
    assert "Language: English (detected; your PDF did not say)." in page_text(language)
    assert '<label for="document-language">Language of this document</label>' in language
    assert "Confirm this language" in language
    before = web.job(job_id).result_version
    response = post_language(web, job_id, "fr")
    assert response.status_code == 303
    # No fragment: the confirmation at the top takes focus (a fragment kept it off).
    assert response.headers["location"] == f"/jobs/{job_id}"
    after = stored(web, job_id)
    assert (after.lang, after.lang_source, after.notices) == ("fr", "user", [])
    job = web.job(job_id)
    assert '<html lang="fr">' in (job.html or "")  # the file was made again
    assert job.result_version == before + 1
    page = web.browser.get(f"/jobs/{job_id}").text
    assert "The language is now French." in page
    assert "Language: French (you chose it)." in page_text(page)
    assert 'id="worth-checking-heading"' not in page  # the notice is answered
    assert "SECRET" not in caplog.text


def test_only_a_listed_language_is_taken(web):
    job_id = convert(web)
    response = post_language(web, job_id, "javascript:alert(1)")
    assert response.status_code == 400
    assert stored(web, job_id).lang == "en"


def test_the_language_is_the_owners_to_change(web):
    job_id = convert(web)
    bob = web.client()
    login(bob, BOB)
    assert post_language(web, job_id, "fr", client=bob).status_code == 404
    bob.close()
    assert stored(web, job_id).lang == "en"


def test_the_language_of_an_unfinished_job_cannot_be_changed(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    assert post_language(web, job_id, "fr").status_code == 409


# --------------------------------------------------------------------------- delete
