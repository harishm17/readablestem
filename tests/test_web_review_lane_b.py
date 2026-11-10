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

import pytest

from fixtures.web.review_doc import GROUP, review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload
from readablestem.core.types import DocumentResult
from readablestem.web.review import FIGURE_KEEP_ACK


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


# ------------------------------------------------------------------------------ bulk


# ------------------------------------------------------------------------- list view


# ----------------------------------------------------------------------- table checks
