"""The review experience (ux2/lane-b): the figure's Save and accept, Save and decide later and the
acknowledged "keep without a description"; a repeated heading decided one by one without leaving
the review screen; the bulk page's next step; the list view's first decision first; the table
card's specific findings; what the Symbols screen says before a replacement is chosen.

Same document as test_web_review_split.py (fixtures.web.review_doc): decision 1 the bold omega
read plain (equation p1-e2), 2 the figure p1-e6, 3 the "Teacher Support" heading group.
"""

from __future__ import annotations

import re
from html import unescape
from typing import get_args

import pytest

from fixtures.web.review_doc import GROUP, review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload
from readablestem.core.types import Check, DocumentResult, FigureType
from readablestem.web import messages
from readablestem.web.review import FIGURE_KEEP_ACK
from readablestem.web.routes.review import ALT_GUIDANCE


@pytest.fixture
def env(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = review_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def screen(env, query: str = "") -> str:
    response = env.browser.get(f"/jobs/{env.job_id}/review{query}")
    assert response.status_code == 200, response.status_code
    return response.text


def post(env, target: str, **fields):
    data = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review"), "view": "split"}
    return env.browser.post(f"/jobs/{env.job_id}/review/{target}", data=data | fields)


def fix_of(env, element_id: str):
    result = DocumentResult.model_validate(env.job(env.job_id).result_json)
    fix = result.fix_for(element_id)
    assert fix is not None
    return fix


ALT = "A car moves from a house to a school along a straight road."


# ----------------------------------------------------------------------------- figures


def test_alt_guidance_covers_every_kind_of_figure():
    assert set(ALT_GUIDANCE) == set(get_args(FigureType))
    for words in ALT_GUIDANCE.values():
        assert words.endswith(".") and len(words) < 160


def test_keeping_a_figure_without_a_description_needs_the_acknowledgement(env):
    refused = post(env, "p1-e6", action="reject")
    assert refused.status_code == 422
    assert FIGURE_KEEP_ACK in text_of(refused.text)
    assert 'id="acknowledge-error-p1-e6"' in refused.text
    assert 'href="#acknowledge-p1-e6"' in refused.text  # the error summary reaches the box
    assert fix_of(env, "p1-e6").status.value == "needs_review"
    done = post(env, "p1-e6", action="reject", acknowledge="on")
    assert done.status_code == 303
    assert fix_of(env, "p1-e6").status.value == "rejected"


def test_save_and_accept_settles_the_figure_and_says_so(env):
    assert post(env, "p1-e6", action="edit", alt=ALT).status_code == 303
    fix = fix_of(env, "p1-e6")
    assert fix.status.value == "edited" and fix.figure is not None and fix.figure.alt == ALT
    assert "Saved and accepted." in text_of(screen(env))


def test_save_decide_later_keeps_the_text_and_leaves_the_figure_waiting(env):
    response = post(env, "p1-e6", action="save", alt=ALT)
    assert response.status_code == 303
    assert response.headers["location"].endswith("?item=p1-e6")  # stays on the figure
    fix = fix_of(env, "p1-e6")
    assert fix.status.value == "needs_review" and fix.figure is not None
    assert fix.figure.alt == ALT and not fix.reviewed
    page = screen(env, "?item=p1-e6")
    assert "Saved, not accepted yet." in text_of(page)
    # The saved text is now the proposal: Accept keeps it as the reviewer's accepted text.
    assert post(env, "p1-e6", action="accept", alt=ALT).status_code == 303
    assert fix_of(env, "p1-e6").status.value == "accepted"
    assert fix_of(env, "p1-e6").figure.alt == ALT  # type: ignore[union-attr]


def test_save_decide_later_is_for_a_figure_only(env):
    response = post(env, "p1-e2", action="save", latex="x")
    assert response.status_code == 422 and "Choose an action." in text_of(response.text)
    assert fix_of(env, "p1-e2").status.value == "needs_review"


def test_the_figure_card_names_what_each_choice_does_and_guides_the_alt_text(env):
    body = screen(env, "?item=p1-e6")
    note = re.search(r'id="figure-note-p1-e6">([^<]+)<', body)
    assert note is not None
    for words in ("Accept as written", "Save and accept", "Save, decide later"):
        assert words in note.group(1) or words in body
    assert "how its labelled parts connect" in body  # a diagram's guidance, not a general one
    # the list view uses the same markup
    listing = env.browser.get(f"/jobs/{env.job_id}/review/list").text
    assert 'data-role="save-later"' in listing and 'id="acknowledge-p1-e6"' in listing


# ----------------------------------------------------------------- repeated headings


def test_a_repeated_heading_can_be_decided_one_by_one_on_the_review_screen(env):
    body = screen(env, f"?item={GROUP}")
    assert 'id="group-members"' in body
    assert len(re.findall(rf'name="stay" value="{GROUP}"', body)) == 3
    assert "Decide them one by one in the list view" not in body
    first = post(env, "p1-e7", action="reject", stay=GROUP)
    assert first.status_code == 303
    assert first.headers["location"].endswith(f"?item={GROUP}#group-members")
    assert fix_of(env, "p1-e7").status.value == "rejected"
    assert fix_of(env, "p2-e0").status.value != "rejected"
    # the group's own card now asks about the two that still wait
    assert "Decide them one by one (2)" in text_of(screen(env, f"?item={GROUP}"))


def test_a_stay_that_is_not_the_headings_group_is_ignored(env):
    response = post(env, "p1-e7", action="reject", stay="hg-not-its-group")
    assert response.status_code == 303
    assert "#group-members" not in response.headers["location"]


# ------------------------------------------------------------------------------ bulk


def test_bulk_page_with_nothing_to_accept_names_the_next_step(env):
    page = env.browser.get(f"/jobs/{env.job_id}/accept/equations?view=split").text
    text = text_of(page)
    assert "No equation is waiting with every check passed." in text
    assert "still waiting, each held back by a failed check or a flag" in text
    assert re.search(r'href="/jobs/\w+/review\?item=p1-e\d">Review the first equation<', page)


# ------------------------------------------------------------------------- list view


def test_the_list_leads_with_the_first_decision_and_folds_its_tools(env):
    page = env.browser.get(f"/jobs/{env.job_id}/review/list").text
    assert '<details class="list-tools" id="list-tools">' in page  # closed
    assert page.index('id="needs-decision-title"') < page.index('id="list-tools"')
    assert page.index('id="list-tools"') < page.index('class="review-toc"')
    assert page.index('class="review-toc"') < page.index('id="group-equations"')
    # the document (file and page count) is named at the top, before the decisions
    assert re.search(r'<p class="lede">[^<]+ · \d+ pages?</p>', page)
    assert page.index('<p class="lede">') < page.index('id="needs-decision-title"')
    assert '<article id="fix-p1-e2"' in page  # the first decision is there to read, not folded
    # every item keeps its own anchor (deep links)
    assert 'id="item-p1-e2"' in page and 'id="item-p1-e6"' in page
    filtered = env.browser.get(f"/jobs/{env.job_id}/review/list?kind=equations").text
    assert '<details class="list-tools" id="list-tools" open>' in filtered


# ----------------------------------------------------------------------- table checks


def test_a_table_check_without_a_sentence_is_named_by_the_part_of_the_table():
    explained = messages.explain_check(Check(name="table.cell_text", passed=False, code="new_code"))
    assert not explained.known
    assert explained.text == (
        "A check on the words and numbers of this table did not pass, so compare them with the image."
    )
    other = messages.explain_check(Check(name="table.something_new", passed=False, code="x"))
    assert "of this table" in other.text and other.text != messages.UNKNOWN_CHECK_MESSAGE
    # a check that is not about a table keeps the neutral sentence
    plain = messages.explain_check(Check(name="latex.something_new", passed=False, code="x"))
    assert plain.text == messages.UNKNOWN_CHECK_MESSAGE
