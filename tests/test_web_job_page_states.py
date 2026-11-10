"""The job page in each state, and its routes: progress, the draft question, the language.

The fake converter's result is changed per test (as in test_web_job_page.py): it has a
heading, a paragraph, two accepted or waiting equations, an abstained equation and a
figure waiting for a decision, so 3 decisions are open until a test makes them.
"""

from __future__ import annotations

import logging
import re
from html import unescape

import pytest

from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, make_pdf, token, upload
from readablestem.core.types import (
    DocumentResult,
    Kind,
    Notice,
    SourceFacts,
    Status,
)


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
    assert title(html) == "Converted: SECRET-HEADING Kinematics | ReadableSTEM"
    status = page_text(section_of_status(html))
    assert "Converted 3 decisions need you before you give it to students." in status
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
    result.fixes = [f for f in result.fixes if f.element_id != "p1-e5"]  # no image left
    for fix in result.fixes:
        fix.reviewed = False  # accepted by the pipeline: nothing needed a decision
    job_id = convert(web)
    html = web.browser.get(f"/jobs/{job_id}").text
    assert title(html) == "Ready to share: SECRET-HEADING Kinematics | ReadableSTEM"
    ready = page_text(section(html, "ready-heading"))
    assert "Nothing in this file needed your decision." in ready
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
    assert (
        title(html)
        == "Stopped: the AI service did not answer: SECRET-HEADING Kinematics | ReadableSTEM"
    )
    text = page_text(html)
    assert "did not answer for 4 of 4 equations and figures" in text
    assert "Nothing is wrong with your PDF" in text
    assert "4 decisions need you before you give it to students." in text
    assert 'id="partial-note"' in html
    assert "All 0 checks" not in text


# --------------------------------------------------------------------------- counts


def test_the_counts_add_up_and_match_the_review_screen(web):
    job_id = convert(web)
    html = web.browser.get(f"/jobs/{job_id}").text
    results = page_text(section(html, "results-heading"))
    # Decisions are the one unit (T1-11): no count of items waiting, accepted or decided.
    assert "Of the 5 items" not in results and "were accepted for you" not in results
    assert "1 equation is shown as an image of the original." in results, results
    review = page_text(web.browser.get(f"/jobs/{job_id}/review").text)
    assert "3" in review and "Decisions to make 3" in results


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
    assert f'<a href="/jobs/{job_id}/download">Download the draft anyway</a>' in page.text


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
