"""The job page in each state, and its routes: progress, the draft question, the language.

The fake converter's result is changed per test (as in test_web_job_page.py): it has a
heading, a paragraph, two accepted or waiting equations, an abstained equation and a
figure waiting for a decision, so 3 decisions are open until a test makes them.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from html import unescape

import pytest

from a11y_stem.core.types import (
    DocumentResult,
    Element,
    Fix,
    Kind,
    Notice,
    SourceFacts,
    Status,
)
from a11y_stem.web.db import Job, JobStatus, ReviewEdit, session_scope, utcnow
from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, make_pdf, token, upload


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
    assert title(html) == "Converted: SECRET-HEADING Kinematics | a11y-stem"
    status = page_text(section_of_status(html))
    assert "Converted 3 decisions need you before you share it." in status
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
    assert title(html) == "Ready to share: SECRET-HEADING Kinematics | a11y-stem"
    ready = page_text(section(html, "ready-heading"))
    assert "Nothing in this file needed your decision." in ready
    assert f'<a class="button" href="/jobs/{job_id}/download">Download the HTML file</a>' in html
    assert "Before you share" in ready and "Put it in your course" in ready
    assert "Do not paste it into a page editor" in ready
    # As the posting help says: the HTML is the main version, linked where the PDF was.
    assert "link to it where the PDF was linked, so it is the main version" in ready
    assert "next to the PDF" not in ready
    # One link to the posting help (ux-a's Canvas steps), in "Put it in your course";
    # the two questions after a download follow it, hidden until a download.
    main = html[html.index("<main") : html.index("</main>")]  # the footer links it too
    assert main.count('href="/help/posting"') == 1
    assert 'href="/help/posting">How to post it in your course</a>' in section(
        html, "ready-heading"
    )
    assert html.index('id="course-heading"') < html.index('id="feedback"')
    assert "draft" not in html.lower()


def test_after_the_last_decision_the_page_says_who_decided_and_when(web):
    decide_everything(web)
    job_id = convert(web)
    with session_scope(web.ctx.factory) as session:
        session.add(ReviewEdit(job_id=job_id, user_id=web.job(job_id).user_id,
                               element_id="p1-e3", action="accept"))  # fmt: skip
    html = web.browser.get(f"/jobs/{job_id}").text
    # The abstained equation was kept as an image: ready, but said plainly.
    assert title(html) == "Ready: SECRET-HEADING Kinematics | a11y-stem"
    assert "1 equation is shown as an image that screen readers cannot read." in page_text(html)
    today = utcnow().date()
    ready = page_text(section(html, "ready-heading"))
    assert f"You made 3 decisions on {today.day} {today:%B}." in ready


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
        == "Stopped: the AI service did not answer: SECRET-HEADING Kinematics | a11y-stem"
    )
    text = page_text(html)
    assert "did not answer for 4 of 4 equations and figures" in text
    assert "Nothing is wrong with your PDF" in text
    assert "4 decisions need you before you share it." in text
    assert 'id="partial-note"' in html
    assert "All 0 checks" not in text


def test_the_polled_fragment_announces_the_finish_with_a_link_to_the_first_decision(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    web.run_worker()
    fragment = web.browser.get(f"/jobs/{job_id}/status?v=x", headers={"HX-Request": "true"})
    assert f'<a href="/jobs/{job_id}/review">Start the review</a>' in fragment.text


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


def test_a_heading_group_counts_once_and_the_page_says_why(web):
    result = web.converter.output.result
    for i in range(3):
        result.elements.append(
            Element(id=f"p1-e{10 + i}", kind=Kind.HEADING, page=0, bbox=(0, 6, 1, 7),
                    order=10 + i, text="Teacher Support", bold=True)
        )  # fmt: skip
        result.fixes.append(
            Fix(element_id=f"p1-e{10 + i}", kind=Kind.HEADING, status=Status.NEEDS_REVIEW,
                review="required", group_key="hg-p1-e10", group_size=3)
        )  # fmt: skip
    job_id = convert(web)
    results = page_text(section(web.browser.get(f"/jobs/{job_id}").text, "results-heading"))
    assert "Decisions to make 4" in results
    assert "waiting make" not in results  # items are not counted, only decisions


# --------------------------------------------------------------------------- progress


def set_running(web, job_id: str, progress: dict | None) -> None:
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.status = JobStatus.RUNNING
        job.started_at = utcnow()
        job.lease_until = utcnow() + timedelta(minutes=2)
        job.progress_json = progress


def events(**stages) -> dict:
    return {
        "stages": {
            name: {"stage": name, "done": d, "total": t, "counts": c}
            for name, (d, t, c) in stages.items()
        }
    }


def test_a_running_job_shows_its_steps_outside_the_live_region(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    progress = events(
        layout=(1, 1, {"equation": 21, "figure": 7}), equations=(14, 21, {}), figures=(5, 7, {})
    )
    set_running(web, job_id, progress)
    html = web.browser.get(f"/jobs/{job_id}").text
    region = page_text(html[html.index('id="job-status-region"') : html.index('id="job-progress"')])
    assert "Reading the equations and checking each against your PDF's text." in region
    assert "14 of 21" not in region  # counts are not announced per element
    steps = page_text(html[html.index('id="job-progress"') :])
    assert "Found 21 equations and 7 figures (done)" in steps
    assert "Equations: 14 of 21 read and checked against your PDF's text (in progress)" in steps
    assert "Figures: 5 of 7 described (in progress)" in steps
    assert f'hx-get="/jobs/{job_id}/progress?v=' in html
    assert title(html) == "Converting: notes.pdf | a11y-stem"


def test_the_status_fragment_waits_for_the_next_step_and_the_steps_for_any_change(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    set_running(web, job_id, events(layout=(1, 1, {}), equations=(1, 21, {})))
    page = web.browser.get(f"/jobs/{job_id}").text
    status_v = re.search(r'status\?v=([^"]+)"', page).group(1)  # type: ignore[union-attr]
    steps_v = re.search(r'progress\?v=([^"]+)"', page).group(1)  # type: ignore[union-attr]
    set_running(web, job_id, events(layout=(1, 1, {}), equations=(9, 21, {})))
    assert web.browser.get(f"/jobs/{job_id}/status?v={status_v}").status_code == 204
    moved = web.browser.get(f"/jobs/{job_id}/progress?v={steps_v}")
    assert moved.status_code == 200 and "Equations: 9 of 21" in moved.text
    assert "<html" not in moved.text
    set_running(web, job_id, events(layout=(1, 1, {}), equations=(21, 21, {}), assemble=(0, 0, {})))
    assert web.browser.get(f"/jobs/{job_id}/status?v={status_v}").status_code == 200


def test_the_steps_fragment_stops_polling_once_the_job_is_done(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1)))
    web.run_worker()
    fragment = web.browser.get(f"/jobs/{job_id}/progress?v=x").text
    assert 'id="job-progress"' in fragment and "hx-get" not in fragment


def test_waiting_gives_an_estimate_and_counts_your_own_documents(web):
    first = job_id_from(upload(web.browser, make_pdf(2)))
    bob = web.client()
    login(bob, BOB)
    job_id_from(upload(bob, make_pdf(3)))
    mine = job_id_from(upload(web.browser, make_pdf(1)))
    text = page_text(web.browser.get(f"/jobs/{mine}").text)
    # 6 pages at the default pace (no finished job to go by yet): 60 s.
    assert "2 documents ahead of yours (1 is yours), about 1 minute to go." in text
    assert "You can leave this page; it keeps its place." in text
    assert "next in line, less than a minute to go" in page_text(
        web.browser.get(f"/jobs/{first}").text
    )
    bob.close()


def test_the_estimate_follows_recent_conversions(web):
    done = job_id_from(upload(web.browser, make_pdf(1)))
    web.run_worker()
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, done)
        assert job is not None
        job.started_at = utcnow() - timedelta(minutes=10)
        job.finished_at = utcnow()  # 10 minutes for 1 page
    waiting = job_id_from(upload(web.browser, make_pdf(2)))
    assert "about 20 minutes to go" in page_text(web.browser.get(f"/jobs/{waiting}").text)


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


def test_notices_are_listed_per_page_with_their_decisions(web):
    result = web.converter.output.result
    result.elements.append(Element(id="p1-e9", kind=Kind.TABLE, page=0, bbox=(0, 7, 1, 8),
                                   order=9))  # fmt: skip
    result.fixes.append(Fix(element_id="p1-e9", kind=Kind.TABLE, status=Status.FAILED,
                            reason="table_not_rebuilt", review="required"))  # fmt: skip
    footnotes = Notice(code="footnote", page=0, count=2, review="none")
    job_id = convert(web, notices=[Notice(code="lang_detected"), footnotes])
    html = web.browser.get(f"/jobs/{job_id}").text
    not_handled = section(html, "not-handled-heading")
    text = page_text(not_handled)
    assert "Page 1: A footnote is kept where it falls in the text" in text and "(2 times)" in text
    assert "Page 1: A table is kept as an image of the original" in text
    # The link names its decision, so two tables on one page give two different links.
    link = (
        f'<a href="/jobs/{job_id}/review?item=p1-e9">Decide it<span class="visually-hidden">: '
        "Table 1</span></a> (one of your decisions)."
    )
    assert link in not_handled
    worth = section(html, "worth-checking-heading")
    assert "The whole document: The language of the document was detected" in page_text(worth)
    assert '<a href="#language-heading">Check the language</a>' in worth
    # Each sentence is a sentence: a space before the link, after the notice's own.
    assert 'right. <a href="#language-heading">Check the language</a>.' in worth
    assert re.search(
        rf'[.)] <a href="/jobs/{job_id}/review\?item=p1-e9">Decide it<span', not_handled
    )
    # The table's decision is counted once, with the others, not again for its notice.
    assert "Decisions to make 4" in page_text(section(html, "results-heading"))


def test_a_colour_notice_does_not_link_to_a_decision_about_something_else(web):
    # A figure telling things apart by colour alone, whose decision is about its data
    # values: the colour line is not "Decide it" (that decision never mentions colour).
    result = web.converter.output.result
    figure = next(
        f
        for f in result.fixes
        if f.kind is Kind.FIGURE and f.figure is not None and f.review == "required"
    )
    assert figure.figure is not None
    figure.figure.colour_only = True  # the notice follows from the fix
    job_id = convert(web)
    worth = section(web.browser.get(f"/jobs/{job_id}").text, "worth-checking-heading")
    assert "tells things apart by colour alone" in page_text(worth)
    assert "Decide it" not in worth and "You have decided it" not in worth


# --------------------------------------------------------------------------- card


FACTS = SourceFacts(tagged=False, images=1, equations=3, equations_flattened=1,
                    bold_maths_letters=0, links=0)  # fmt: skip


def test_the_card_compares_the_pdf_with_the_html_and_hears_the_difference(web):
    result = web.converter.output.result
    result.elements[3].text = "x2 + 1"
    result.elements[3].reasons = ["superscript"]
    job_id = convert(web, source_facts=FACTS)
    html = web.browser.get(f"/jobs/{job_id}").text
    card = section(html, "changed-heading")
    assert '<th scope="col">Can a screen-reader user …</th>' in card
    text = page_text(card)
    assert "hear equations with their structure?" in text
    assert "In 1 of 3, the raised parts are gone." in text
    assert "Hear the difference" in text
    assert "From your PDF a screen reader gets: “x 2 plus 1”" in text
    assert "In the HTML: “x plus 1”" in text
    assert (
        f'<img src="/jobs/{job_id}/crops/p1-e3.png" alt="The equation as printed in your PDF">'
        in (card)
    )
    assert "%" not in text and "score" not in text.lower()


def test_for_a_tagged_pdf_the_examples_say_text_layer_not_what_a_reader_gets(web):
    result = web.converter.output.result
    result.elements[3].text = "x2 + 1"
    result.elements[3].reasons = ["superscript"]
    job_id = convert(web, source_facts=FACTS.model_copy(update={"tagged": True}))
    text = page_text(section(web.browser.get(f"/jobs/{job_id}").text, "changed-heading"))
    assert "Your PDF's text layer gives: “x 2 plus 1”" in text
    assert "a screen reader may get more from it than its text layer" in text
    assert "From your PDF a screen reader gets" not in text


def test_older_results_without_source_facts_have_no_card_and_still_load(web):
    job_id = convert(web)
    html = web.browser.get(f"/jobs/{job_id}").text
    assert 'id="changed-heading"' not in html
    assert "Language: English." in page_text(html)  # where it came from is not known


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


def test_changing_the_language_keeps_the_date_of_the_last_decision(web):
    decide_everything(web)
    job_id = convert(web)
    with session_scope(web.ctx.factory) as session:
        session.add(ReviewEdit(job_id=job_id, user_id=web.job(job_id).user_id,
                               element_id="p1-e3", action="accept",
                               created_at=utcnow() - timedelta(days=3)))  # fmt: skip
    assert post_language(web, job_id, "de").status_code == 303
    day = (utcnow() - timedelta(days=3)).date()
    assert f"on {day.day} {day:%B %Y}." in (web.job(job_id).html or "")


# --------------------------------------------------------------------------- delete


def test_delete_is_a_quiet_action_at_the_end(web):
    job_id = convert(web)
    html = web.browser.get(f"/jobs/{job_id}").text
    delete = section(html, "delete-heading")
    assert '<button type="submit" class="button-secondary">Delete now</button>' in delete
    assert "button-danger" not in html
    assert html.index('id="delete-heading"') > html.index('id="results-heading"')
