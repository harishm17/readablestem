"""Review screen fixes from the UX review (docs/web.md "Review screen"): the stale-tab guard
(MF12), edits checked like proposals (MF3), Undo and plain names for the decisions (MF14),
notes that name their item (MF15), where the speech differs (BW12), what a screen reader
gets from the PDF, and getting back to a decision after the session ended (MF19).

Uses the real-shaped document of fixtures.web.review_doc: decision 1 is the bold omega read
plain (equation p1-e2), 2 the figure p1-e6, 3 the "Teacher Support" heading group; an
optional note on equation p1-e5. The browser side (axe, reflow, focus, no JavaScript,
the live check and the kept draft) is in test_web_review_ux_browser.py.
"""

from __future__ import annotations

import re
from html import unescape

import pytest
from sqlalchemy import select

from a11y_stem.core.types import DocumentResult
from a11y_stem.web.db import Job, ReviewEdit, session_scope
from a11y_stem.web.review import bump_result_version, is_stale
from fixtures.web.review_doc import GROUP, review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload


@pytest.fixture
def ux(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = review_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def screen(env, query: str = "", status: int = 200) -> str:
    response = env.browser.get(f"/jobs/{env.job_id}/review{query}")
    assert response.status_code == status, response.status_code
    return response.text


def post(env, target: str, **fields):
    data = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review")} | fields
    return env.browser.post(f"/jobs/{env.job_id}/review/{target}", data=data)


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def section(html: str, start: str, end: str) -> str:
    begin = html.index(start)
    return html[begin : html.index(end, begin)]


def panel(html: str) -> str:
    return section(html, 'id="decision"', 'class="optional-area"')


def stored(env) -> DocumentResult:
    return DocumentResult.model_validate(env.job(env.job_id).result_json)


def version_of(env) -> int:
    with session_scope(env.ctx.factory) as session:
        job = session.get(Job, env.job_id)
        assert job is not None
        return job.result_version


def edits(env) -> list[ReviewEdit]:
    with session_scope(env.ctx.factory) as session:
        rows = session.scalars(
            select(ReviewEdit).order_by(ReviewEdit.created_at, ReviewEdit.id)
        ).all()
        session.expunge_all()
        return list(rows)


# --------------------------------------------------------------------------- MF12 stale tab


def test_is_stale():
    assert not is_stale(3, "3")
    assert not is_stale(0, "0") and not is_stale(None, "0")
    assert not is_stale(3, "")  # a page from before the guard: not refused
    for sent in ("2", "4", "x", "-1", "3.0", "²", "1" * 12):
        assert is_stale(3, sent), sent


def test_bump_result_version():
    job = Job(filename="x.pdf", result_version=None)
    assert bump_result_version(job) == 1
    assert bump_result_version(job) == 2


def test_every_form_carries_the_version_its_page_was_drawn_at(ux):
    page = screen(ux)
    forms = re.findall(
        r"<form method=\"post\" action=\"/jobs/[^\"]+/review/[^\"]+\".*?</form>", page, re.S
    )
    assert forms
    for form in forms:
        assert '<input type="hidden" name="version" value="0">' in form
    listing = ux.browser.get(f"/jobs/{ux.job_id}/review/list").text
    assert listing.count('name="version" value="0"') == listing.count('name="view" value="list"')


def test_each_review_write_counts_itself_once(ux):
    assert post(ux, "p1-e2", action="accept", version="0").status_code == 303
    assert version_of(ux) == 1
    # A group decision is one write, however many headings it changes.
    assert post(ux, GROUP, action="reject", version="1").status_code == 303
    assert version_of(ux) == 2
    assert [e.after["write"] for e in edits(ux)] == [1, 2, 2, 2]
    # The next page's forms carry the new number.
    assert 'name="version" value="2"' in screen(ux)
    # A write that changes nothing (the group again, nothing left) is not counted.
    assert post(ux, GROUP, action="reject", version="2").status_code == 303
    assert version_of(ux) == 2


def test_a_write_from_an_out_of_date_tab_is_refused_and_says_so(ux):
    # Tab A accepts equation 1; tab B, drawn before, then tries to keep it as an image.
    assert post(ux, "p1-e2", action="accept", version="0").status_code == 303
    response = post(ux, "p1-e2", action="reject", version="0")
    assert response.status_code == 409
    fix = stored(ux).fix_for("p1-e2")
    assert fix.status.value == "accepted"  # tab A's decision stands
    assert version_of(ux) == 1 and len(edits(ux)) == 1
    page = response.text
    box = section(page, '<div class="error-summary stale-notice"', "</div>")
    assert 'role="alert"' in box and "autofocus" in box
    assert "Your choice was not saved" in box
    assert "changed in another tab or window after this page was opened" in box
    assert "Equation 1 is now: accepted by you." in text_of(box)
    assert f'<a href="/jobs/{ux.job_id}/review?item=p1-e2">Reload this page</a>' in box
    # The page shows the decision as it is now, with forms that carry the new version.
    assert "Decided: Accepted by you" in panel(page)
    assert 'name="version" value="1"' in page


def test_a_refused_stale_edit_keeps_what_was_typed(ux):
    assert post(ux, "p1-e6", action="accept", version="0").status_code == 303
    response = post(ux, "p1-e2", action="edit", latex=r"\boldsymbol{\omega}", version="0")
    assert response.status_code == 409
    assert r"\boldsymbol{\omega}</textarea>" in panel(response.text)
    assert stored(ux).fix_for("p1-e2").status.value == "needs_review"
    # Choosing again from the refreshed page works.
    assert (
        post(ux, "p1-e2", action="edit", latex=r"\boldsymbol{\omega}", version="1").status_code
        == 303
    )


def test_stale_group_and_list_view_writes_are_refused_too(ux):
    assert post(ux, "p1-e2", action="accept", version="0").status_code == 303
    group = post(ux, GROUP, action="reject", version="0")
    assert group.status_code == 409
    assert "Your choice was not saved" in group.text
    assert {f.status.value for f in stored(ux).fixes if f.group_key == GROUP} == {"needs_review"}
    listed = post(ux, "p1-e6", action="accept", version="0", view="list")
    assert listed.status_code == 409
    assert "<h1>All items as a list</h1>" in listed.text
    assert f'href="/jobs/{ux.job_id}/review/list#item-p1-e6">Reload this page</a>' in listed.text
    bulk = post(ux, "headings", action="accept_all", version="0", view="list")
    assert bulk.status_code == 409
    assert stored(ux).fix_for("p1-e6").status.value == "needs_review"


def test_a_form_without_a_version_is_still_applied(ux):
    """Pages opened before the guard existed (and scripts) send no version."""
    assert post(ux, "p1-e2", action="accept").status_code == 303
    assert post(ux, "p1-e6", action="accept").status_code == 303
    assert version_of(ux) == 2
