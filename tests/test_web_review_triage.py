"""Review screen under the "meaning-level only" policy (2026-10-08): what needs a decision
(counted, blocks "done"), optional checks (accepted, folded away, still editable), and one
decision for a group of repeated headings ("Apply to all 3" / "Make all 3 bold text" /
one level for all).

`triage_output()` is the document used here and in test_web_review_triage_browser.py.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from a11y_stem.core import triage
from a11y_stem.core.types import (
    Check,
    ConversionOutput,
    DocumentResult,
    DocumentStats,
    Element,
    EquationFix,
    Fix,
    HeadingFix,
    Kind,
    Status,
)
from a11y_stem.web import review
from a11y_stem.web.db import ReviewEdit, session_scope
from fixtures.web.support import ALICE, BOB, PNG, WebEnv, job_id_from, login, make_pdf, token
from fixtures.web.support import upload as upload_pdf
from test_web_review import cards, install_fake_pipeline

GROUP = "hg-p1-e1"
MEMBERS = ["p1-e1", "p2-e0", "p3-e0"]


def triage_output() -> ConversionOutput:
    """Three pages. Heading p1-e0 (level 2) is certain; "SECRET-LABEL Teacher Support"
    is a weak bold line under it on every page (p1-e1, p2-e0, p3-e0): one group, level 3.
    p2-e1 is a single weak heading (optional note). Equation p1-e2 has a cosmetic style
    note (accepted, optional); equation p2-e2 lost a bold vector (needs a decision);
    equation p3-e1 is clean."""
    layout = [  # (id, kind, page)
        ("p1-e0", Kind.HEADING, 0), ("p1-e1", Kind.HEADING, 0), ("p1-e2", Kind.EQUATION, 0),
        ("p2-e0", Kind.HEADING, 1), ("p2-e1", Kind.HEADING, 1), ("p2-e2", Kind.EQUATION, 1),
        ("p3-e0", Kind.HEADING, 2), ("p3-e1", Kind.EQUATION, 2),
    ]  # fmt: skip
    texts = {
        "p1-e0": "SECRET-HEADING Motion",
        "p2-e1": "SECRET-HEADING Galileo",
        **dict.fromkeys(MEMBERS, "SECRET-LABEL Teacher Support"),
    }
    elements = [
        Element(
            id=eid,
            kind=kind,
            page=page,
            bbox=(0, order, 1, order + 1),
            order=order,
            text=texts.get(eid),
            font_size=10.5 if eid != "p1-e0" else 14.0,
            bold=kind is Kind.HEADING,
            reasons=["larger font"] if eid == "p1-e0" else ["bold body-size line", "bold"],
            display="inline" if kind is Kind.EQUATION else None,
        )
        for order, (eid, kind, page) in enumerate(layout)
    ]

    def eq(latex: str) -> EquationFix:
        return EquationFix(
            latex=latex, mathml=f"<math><mi>{latex}</mi></math>", speech=latex, display="inline"
        )

    def warn(name: str, code: str) -> Check:
        return Check(name=name, passed=False, severity="warning", code=code)

    weak = warn("heading.evidence", "heading.weak_evidence")
    fixes = [
        Fix(element_id="p1-e0", kind=Kind.HEADING, status=Status.ACCEPTED, confidence=0.9,
            heading=HeadingFix(level=2, text=texts["p1-e0"]), review="none"),
        *(
            Fix(element_id=eid, kind=Kind.HEADING, status=Status.NEEDS_REVIEW, confidence=0.6,
                heading=HeadingFix(level=3, text=texts[eid]), checks=[weak], review="required",
                group_key=GROUP, group_size=3)
            for eid in MEMBERS
        ),
        Fix(element_id="p2-e1", kind=Kind.HEADING, status=Status.ACCEPTED, confidence=0.6,
            heading=HeadingFix(level=3, text=texts["p2-e1"]), checks=[weak], review="optional"),
        Fix(element_id="p1-e2", kind=Kind.EQUATION, status=Status.ACCEPTED, confidence=0.97,
            equation=eq("d0"), checks=[warn("equation.text_layer_style", "style_cosmetic")],
            review="optional"),
        Fix(element_id="p2-e2", kind=Kind.EQUATION, status=Status.NEEDS_REVIEW, confidence=0.58,
            equation=eq("w"), checks=[warn("equation.text_layer_style", "style_mismatch")],
            review="required"),
        Fix(element_id="p3-e1", kind=Kind.EQUATION, status=Status.ACCEPTED, confidence=0.99,
            equation=eq("x"), review="none"),
    ]  # fmt: skip
    result = DocumentResult(
        pipeline_version="test",
        title="SECRET-HEADING Triage",
        elements=elements,
        fixes=fixes,
        stats=DocumentStats(pages=3, cost_usd=0.01, latency_ms=5, model_calls=3),
        html="<!doctype html><html lang='en'><head><title>t</title></head><body></body></html>",
    )
    crops = {e.id: PNG for e in elements if e.kind is Kind.EQUATION}
    return ConversionOutput(result=result, crops=crops)


@pytest.fixture
def tri(tmp_path, monkeypatch):
    fake = install_fake_pipeline(monkeypatch)
    env = WebEnv(tmp_path)
    env.converter.output = triage_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.pipeline = fake
        env.job_id = job_id_from(upload_pdf(client, make_pdf(3)))
        env.run_worker()
        yield env


def page_of(env) -> str:
    response = env.browser.get(f"/jobs/{env.job_id}/review")
    assert response.status_code == 200
    return response.text


def post(env, target: str, **fields):
    data = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review")} | fields
    return env.browser.post(f"/jobs/{env.job_id}/review/{target}", data=data)


def stored(env) -> DocumentResult:
    return DocumentResult.model_validate(env.job(env.job_id).result_json)


def section(page: str, section_id: str) -> str:
    start = page.index(f'<section id="{section_id}"')
    ends = [page.find('<section id="', start + 1), page.find("</main>", start)]
    return page[start : min(e for e in ends if e != -1)]


def group_card(page: str) -> str:
    """The group's card, with the cards of its headings folded inside it."""
    start = page.index(f'<li id="group-card-{GROUP}"')
    members_end = page.index("</details>", page.index("group-members", start))
    return page[start : page.index("</article>", members_end)]


def edits(env) -> list[ReviewEdit]:
    with session_scope(env.ctx.factory) as session:
        return list(session.scalars(select(ReviewEdit).order_by(ReviewEdit.created_at)))


# --------------------------------------------------------------------------- rules


def test_decisions_count_a_heading_group_once():
    result = triage_output().result
    assert triage.open_decisions(result.fixes) == 2  # the equation and the group
    assert triage.required_decisions(result.fixes) == 2
    load = triage.review_load(result.fixes)
    assert (load.required_decisions, load.required_fixes, load.optional, load.none) == (2, 4, 2, 2)
    assert dict(load.required_by_kind) == {"equation": 1, "heading": 1}
    assert load.required_by_reason["heading.repeated_label"] == 3
    assert load.optional_by_reason == {"style_cosmetic": 1, "heading.weak_evidence": 1}
    groups = review.group_fixes(result)
    assert [[[f.element_id for f in u] for u in g.units] for g in groups] == [
        [["p2-e2"]],
        [MEMBERS],
    ]
    assert [[f.element_id for f in g.optional] for g in groups] == [["p1-e2"], ["p2-e1"]]


def test_the_next_decision_counts_a_group_once():
    result = triage_output().result
    assert review.next_to_review(result, "p2-e2") == "p1-e1"  # the group, by its first
    assert review.review_anchor(result, "p1-e1") == f"group-card-{GROUP}"
    # One heading of the group decided on its own: the rest of the group comes first.
    assert review.next_to_review(result, "p1-e1") == "p2-e0"
    for fix in result.fixes:
        if fix.group_key == GROUP:
            fix.status = Status.ACCEPTED
    # The whole group decided: the next decision after it.
    assert review.next_to_review(result, "p3-e0") == "p2-e2"
    assert review.review_anchor(result, "p2-e2") == "item-p2-e2"


def test_results_stored_before_tiers_get_them_from_their_status():
    stored_fix = {"element_id": "e", "kind": "equation", "status": "needs_review"}
    assert Fix.model_validate(stored_fix).review == "required"
    tiers = {"failed": "required", "abstained": "required", "accepted": "none"}
    tiers |= {"edited": "none", "rejected": "none"}
    for status, tier in tiers.items():
        assert Fix.model_validate(stored_fix | {"status": status}).review == tier
    fix = Fix.model_validate(stored_fix)
    assert (fix.group_key, fix.group_size, fix.optional_note) == (None, None, False)
    # An explicit tier is kept, and a reviewer's decision never changes it.
    assert Fix.model_validate(stored_fix | {"review": "optional"}).review == "optional"


# --------------------------------------------------------------------------- page


def test_page_puts_decisions_first_then_optional_checks_then_the_rest(tri):
    page = page_of(tri)
    assert '<h2 id="needs-decision-title">Needs your decision (2)</h2>' in page
    assert '<h2 id="optional-checks-title">Optional checks (2)</h2>' in page
    assert '<h2 id="decided-title">Accepted or decided (2)</h2>' in page
    # The equation decision, the group card (its headings folded inside), then the
    # optional checks, then the accepted ones.
    assert cards(page) == ["p2-e2", *MEMBERS, "p1-e2", "p2-e1", "p3-e1", "p1-e0"]
    progress = page[page.index('id="review-progress"') : page.index('class="review-toc"')]
    assert "2 decisions are still needed:</strong>" in progress
    assert "1 equation, 1 group of repeated headings." in progress
    assert "2 optional checks can be looked at if you want" in progress


def test_a_heading_group_is_one_card_with_one_decision(tri):
    card = group_card(page_of(tri))
    assert "appears 3 times as a bold line in the body text size." in card
    assert "Keep them as headings at level 3, or make them bold text?" in card
    assert '<span class="meta">3 headings, pages 1, 2 and 3</span>' in card
    assert 'value="accept" class="button">Apply to all 3<span class="visually-hidden">' in card
    assert "headings “SECRET-LABEL Teacher Support” at level 3</span>" in card
    assert "Make all 3 bold text" in card
    assert '<label for="group-level-hg-p1-e1">Or choose one level for all 3</label>' in card
    assert '<option value="3" selected>Level 3</option>' in card
    # Each heading can still be decided on its own, folded inside the card.
    assert '<details class="done-group group-members">' in card  # closed
    assert "<summary>Decide them one by one (3 headings)</summary>" in card
    for eid in MEMBERS:
        assert f'<article id="fix-{eid}"' in card
    assert card.count("SECRET-LABEL Teacher Support") >= 4


def test_optional_checks_are_folded_and_say_what_the_note_is(tri):
    optional = section(page_of(tri), "optional-checks")
    assert '<details class="done-group">' in optional  # closed
    assert "<summary>Show the 2 optional checks</summary>" in optional
    summary = re.search(r'<summary id="fix-title-p1-e2">(.*?)</summary>', optional, re.S)
    assert summary is not None
    assert summary.group(1).startswith("Equation 1, page 1: A letter&#39;s style differs")
    assert "<strong>Note:</strong> A letter&#39;s style differs from the PDF only" in optional
    assert "Optional check (nothing to do unless you disagree):" in optional
    # Still editable: save or reject, no Accept (it is accepted already).
    assert 'action="/jobs/' in optional and 'value="edit"' in optional
    assert 'value="accept"' not in optional


def test_job_page_counts_decisions_and_optional_checks(tri):
    page = tri.browser.get(f"/jobs/{tri.job_id}").text
    base = f"/jobs/{tri.job_id}/review"
    assert "<dt>Need your decision</dt><dd>2</dd>" in page
    assert "<dt>Optional checks (accepted; nothing to do)</dt><dd>2</dd>" in page
    breakdown = page[
        page.index('id="review-breakdown"') : page.index("</p>", page.index("breakdown"))
    ]
    assert "2 need your decision:" in breakdown
    assert f'<a href="{base}#group-equations">1 equation</a>,' in breakdown
    assert f'<a href="{base}#group-headings">1 group of repeated headings</a>.' in breakdown
    assert "Review fixes (2 need your decision)" in page


# --------------------------------------------------------------------------- group actions


def test_apply_to_all_keeps_every_heading_of_the_group(tri):
    response = post(tri, GROUP, action="accept")
    assert response.status_code == 303
    # Nothing in this group is left; the next decision is the equation.
    assert response.headers["location"] == f"/jobs/{tri.job_id}/review#item-p2-e2"
    after = stored(tri)
    for eid in MEMBERS:
        fix = after.fix_for(eid)
        assert fix is not None
        assert (fix.status, fix.reviewed, fix.review) == (Status.ACCEPTED, True, "required")
        assert fix.heading is not None and fix.heading.level == 3
    assert [(e.element_id, e.action) for e in edits(tri)] == [(e, "accept") for e in MEMBERS]
    assert [e.before["status"] for e in edits(tri)] == ["needs_review"] * 3
    page = page_of(tri)
    assert "Kept 3 headings as they are." in page
    assert '<h2 id="needs-decision-title">Needs your decision (1)</h2>' in page
    assert f"group-card-{GROUP}" not in page
    assert tri.job(tri.job_id).html.count(":accepted") >= 5  # re-rendered once


def test_make_all_bold_text_rejects_every_heading_of_the_group(tri):
    assert post(tri, GROUP, action="reject").status_code == 303
    after = stored(tri)
    assert {after.fix_for(e).status for e in MEMBERS} == {Status.REJECTED}  # type: ignore[union-attr]
    assert "Made 3 headings bold text instead of headings." in page_of(tri)


def test_one_level_for_all_edits_every_heading_of_the_group(tri):
    assert post(tri, GROUP, action="edit", level="2").status_code == 303
    after = stored(tri)
    for eid in MEMBERS:
        fix = after.fix_for(eid)
        assert fix is not None and fix.status is Status.EDITED
        assert fix.heading is not None and fix.heading.level == 2
    assert "Set 3 headings to level 2." in page_of(tri)


def test_a_level_that_skips_is_refused_for_the_whole_group(tri):
    response = post(tri, GROUP, action="edit", level="5")
    assert response.status_code == 422
    page = response.text
    assert "Level 5 would skip a level after the heading before one of these" in page
    assert '<a href="#group-level-hg-p1-e1">' in page  # error summary links to the select
    assert 'aria-invalid="true"' in group_card(page)
    assert '<option value="5" selected>Level 5</option>' in group_card(page)
    assert {stored(tri).fix_for(e).status for e in MEMBERS} == {Status.NEEDS_REVIEW}  # type: ignore[union-attr]
    assert edits(tri) == []
    assert post(tri, GROUP, action="edit", level="x").status_code == 422


def test_deciding_one_heading_of_a_group_leaves_the_rest_as_a_group(tri):
    response = post(tri, "p2-e0", action="reject")
    assert response.headers["location"] == f"/jobs/{tri.job_id}/review#group-card-{GROUP}"
    page = page_of(tri)
    card = group_card(page)
    # Working through them one by one: their cards stay unfolded, with a link back.
    assert '<details class="done-group group-members" open>' in card
    assert "Heading 3 on page 2: Rejected." in card
    assert "appears 3 times as a bold line in the body text size (2 still waiting)." in card
    assert "Apply to all 2" in card and "Make all 2 bold text" in card
    assert '<h2 id="needs-decision-title">Needs your decision (2)</h2>' in page
    assert post(tri, GROUP, action="accept").status_code == 303
    after = stored(tri)
    assert [after.fix_for(e).status for e in MEMBERS] == [  # type: ignore[union-attr]
        Status.ACCEPTED,
        Status.REJECTED,
        Status.ACCEPTED,
    ]


def test_a_decision_lands_on_the_group_card(tri):
    response = post(tri, "p2-e2", action="accept")
    assert response.headers["location"] == f"/jobs/{tri.job_id}/review#group-card-{GROUP}"
    card = group_card(page_of(tri))
    assert "Equation 2 on page 2: Accepted." in card


def test_repeating_a_group_decision_changes_nothing(tri):
    assert post(tri, GROUP, action="accept").status_code == 303
    before = len(edits(tri))
    response = post(tri, GROUP, action="reject")
    assert response.status_code == 303
    assert len(edits(tri)) == before
    assert "Nothing changed: none of these headings were waiting" in page_of(tri)


def test_unknown_groups_and_other_peoples_jobs_are_not_found(tri):
    assert post(tri, "hg-p9-e9", action="accept").status_code == 404
    assert post(tri, GROUP, action="bogus").status_code == 422
    other = tri.browser
    other.post("/logout", data={"csrf_token": token(other, "/app")})
    tri.allow(BOB)
    login(other, BOB)
    data = {"csrf_token": token(other, "/app"), "action": "accept"}
    assert other.post(f"/jobs/{tri.job_id}/review/{GROUP}", data=data).status_code == 404


def test_bulk_accept_still_takes_group_members(tri):
    assert post(tri, "headings", action="accept_all").status_code == 303
    assert {stored(tri).fix_for(e).status for e in MEMBERS} == {Status.ACCEPTED}  # type: ignore[union-attr]


def test_an_optional_check_can_still_be_edited(tri):
    assert post(tri, "p1-e2", action="edit", latex="d_0").status_code == 303
    fix = stored(tri).fix_for("p1-e2")
    assert fix is not None and fix.status is Status.EDITED and fix.review == "optional"
    page = page_of(tri)
    assert '<h2 id="optional-checks-title">Optional checks (1)</h2>' in page
    assert '<h2 id="needs-decision-title">Needs your decision (2)</h2>' in page  # unchanged


def test_an_error_on_one_heading_of_a_group_unfolds_the_group(tri):
    response = post(tri, "p3-e0", action="edit", level="6")
    assert response.status_code == 422
    card = group_card(response.text)
    assert '<details class="done-group group-members" open>' in card
    assert 'id="level-error-p3-e0"' in card
    assert '<a href="#level-p3-e0">' in response.text  # the error summary reaches it
