"""Review screen at document scale: grouping and order, stable numbering, where the
reviewer lands after a decision, and that there is no bulk accept.

Uses `rich_output()` from test_web_review.py: three pages with every status in every
group, stored in scrambled order.
"""

from __future__ import annotations

import logging
import re

import pytest
from sqlalchemy import select

from a11y_stem.core.types import Check, Kind, Status
from a11y_stem.web import review
from a11y_stem.web.db import ErrorAggregate, ReviewEdit, session_scope
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, make_pdf, token
from fixtures.web.support import upload as upload_pdf
from test_web_review import card, cards, edits, install_fake_pipeline, result, rich_output

EQUATIONS_PENDING = ["p1-e1", "p2-e1", "p2-e2", "p3-e0"]
FIGURES_PENDING = ["p1-e2", "p3-e1", "p3-e2"]
HEADINGS_PENDING = ["p1-e3", "p2-e0", "p2-e4", "p3-e3"]


@pytest.fixture
def rich(tmp_path, monkeypatch):
    fake = install_fake_pipeline(monkeypatch)
    env = WebEnv(tmp_path)
    env.converter.output = rich_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.pipeline = fake
        env.job_id = job_id_from(upload_pdf(client, make_pdf(3)))
        env.run_worker()
        yield env


def page_of(env) -> str:
    response = env.browser.get(f"/jobs/{env.job_id}/review/list")
    assert response.status_code == 200
    return response.text


def post(env, target: str, **fields):
    data = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review"), "view": "list"} | fields
    return env.browser.post(f"/jobs/{env.job_id}/review/{target}", data=data)


def statuses(env) -> dict[str, str]:
    return {f.element_id: f.status.value for f in result(env).fixes}


def group(page: str, slug: str) -> str:
    start = page.index(f'<section id="group-{slug}"')
    return page[start : page.index("</section>", start)]


# --------------------------------------------------------------------------- order (rules)


def test_groups_are_equations_figures_headings_with_pending_first_in_page_order():
    groups = review.group_fixes(rich_output().result)
    assert [g.kind for g in groups] == [Kind.EQUATION, Kind.FIGURE, Kind.HEADING]
    ids = [[f.element_id for f in g.pending] for g in groups]
    assert ids == [EQUATIONS_PENDING, FIGURES_PENDING, HEADINGS_PENDING]
    assert [[f.element_id for f in g.done] for g in groups] == [
        ["p2-e3", "p3-e4"],
        ["p3-e5"],
        ["p1-e0"],
    ]
    # FAILED and ABSTAINED sit with NEEDS_REVIEW in reading order, not after it.
    assert [f.status for f in groups[0].pending] == [
        Status.NEEDS_REVIEW,
        Status.FAILED,
        Status.NEEDS_REVIEW,
        Status.NEEDS_REVIEW,
    ]


def test_empty_groups_are_left_out():
    data = rich_output().result
    data.fixes = [f for f in data.fixes if f.kind is not Kind.FIGURE]
    assert [g.slug for g in review.group_fixes(data)] == ["equations", "headings"]


def test_numbers_follow_reading_order_within_each_kind():
    numbers = review.item_numbers(rich_output().result)
    equations = ["p1-e1", "p2-e1", "p2-e2", "p2-e3", "p3-e0", "p3-e4"]
    assert [numbers[e] for e in equations] == [1, 2, 3, 4, 5, 6]
    assert numbers["p1-e2"] == 1 and numbers["p3-e5"] == 4
    assert numbers["p1-e0"] == 1 and numbers["p3-e3"] == 5


def test_next_to_review_goes_forward_then_wraps_round():
    data = rich_output().result
    assert review.next_to_review(data, "p1-e1") == "p2-e1"
    assert review.next_to_review(data, "p3-e0") == "p1-e2"  # last equation -> first figure
    assert review.next_to_review(data, "p3-e3") == "p1-e1"  # last heading -> wraps round
    assert review.next_to_review(data, "p2-e3") == "p3-e0"  # from a decided equation too
    assert review.next_to_review(data, "nope") == "p1-e1"
    for fix in data.fixes:
        fix.status = Status.ACCEPTED
    assert review.next_to_review(data, "p1-e1") is None


# --------------------------------------------------------------------------- order (page)


def test_page_lists_decisions_by_kind_then_the_decided_ones(rich):
    page = page_of(rich)
    # The reviewer's own decisions (edited, rejected), then what was accepted for them;
    # each by kind (equations, figures, headings).
    mine, accepted = ["p3-e4", "p3-e5"], ["p2-e3", "p1-e0"]
    expected = [*EQUATIONS_PENDING, *FIGURES_PENDING, *HEADINGS_PENDING, *mine, *accepted]
    assert cards(page) == expected
    for slug, ids in (
        ("equations", EQUATIONS_PENDING),
        ("figures", FIGURES_PENDING),
        ("headings", HEADINGS_PENDING),
    ):
        assert re.findall(r'<article id="fix-([\w-]+)"', group(page, slug)) == ids
    yours = page[page.index('id="decided"') : page.index('id="accepted"')]
    assert re.findall(r'<article id="fix-([\w-]+)"', yours) == mine
    folded = page[page.index('id="accepted"') :].split('<details class="done-group">', 1)[1]
    assert re.findall(r'<article id="fix-([\w-]+)"', folded) == accepted


def test_labels_stay_the_same_after_a_decision(rich):
    title = '<h4 id="fix-title-p2-e2">Equation 3 <span class="meta">on page 2</span></h4>'
    assert title in page_of(rich)
    assert post(rich, "p1-e1", action="accept").status_code == 303
    assert title in page_of(rich)  # not renumbered by the move of equation 1


def test_confidence_is_a_word_and_a_number(rich):
    page = page_of(rich)
    assert "<dd>Medium (82%)</dd>" in card(page, "p1-e1")
    assert "<dd>Low (45%)</dd>" in card(page, "p2-e0")
    assert "<dd>High (90%)</dd>" in card(page, "p3-e0")
    assert "<dd>Not given</dd>" in card(page, "p3-e2")


def test_checks_and_reasons_are_explained_not_coded(rich):
    page = page_of(rich)
    style = card(page, "p2-e2")
    assert "a bold letter, a vector, is plain here" in style
    assert "<code>equation.text_layer_style: style_mismatch</code>" in style
    heading = card(page, "p1-e3")
    assert "Only weak signs say this is a heading" in heading
    assert "Code: <code>heading.evidence: heading.weak_evidence</code>" in heading
    timeout = card(page, "p3-e2")
    assert "The model did not answer in time." in timeout
    assert "<code>model_timeout</code>" in timeout
    blocked = card(page, "p3-e0")
    assert "<strong>Must be fixed:</strong> A fraction, root, subscript" in blocked
    assert 'value="accept"' not in blocked


def test_job_page_links_each_kind_to_its_first_decision(rich):
    page = rich.browser.get(f"/jobs/{rich.job_id}").text
    base = f"/jobs/{rich.job_id}/review"
    # Every fix waiting counts, not converted ones too: each needs a decision.
    assert "11 need your decision:" in page
    assert f'<a href="{base}?item=p1-e1">4 equations</a>,' in page
    assert f'<a href="{base}?item=p1-e2">3 figures</a>,' in page
    assert f'<a href="{base}?item=p1-e3">4 headings</a>.' in page
    for slug in ("equations", "figures", "headings"):
        assert f'id="group-{slug}"' in page_of(rich)


# --------------------------------------------------------------------------- landing


def test_after_a_decision_the_reviewer_lands_on_the_next_fix_waiting(rich):
    location = post(rich, "p1-e1", action="accept").headers["location"]
    assert location == f"/jobs/{rich.job_id}/review/list#item-p2-e1"
    page = page_of(rich)
    landing = card(page, "p2-e1")
    # The notice is where the redirect lands, before the card, linked to the fix it is
    # about; that fix is decided now and unfolded so the link works.
    assert '<a href="#item-p1-e1">Equation 1 on page 1</a>: Accepted.' in landing
    assert landing.index("Accepted.") < landing.index('<article id="fix-p2-e1"')
    assert '<details class="done-item" open>' in card(page, "p1-e1")
    assert '<details class="done-item">' in card(page, "p2-e3")  # untouched: still folded
    assert "Accepted." not in card(page_of(rich), "p2-e1")  # notices are one-time

    location = post(rich, "p3-e3", action="reject").headers["location"]
    assert location == f"/jobs/{rich.job_id}/review/list#item-p2-e1"  # last one: wraps round
    landing = card(page_of(rich), "p2-e1")
    assert '<a href="#item-p3-e3">Heading 5 on page 3</a>: Made plain text, not a heading.' in (
        landing
    )


def test_landing_item_is_focusable_and_its_notice_comes_first(rich):
    post(rich, "p1-e1", action="accept")
    landing = card(page_of(rich), "p2-e1")
    assert landing.startswith('<li id="item-p2-e1" class="review-item" tabindex="-1">')
    # Tab order from the landing point: the link back, then the card's own controls.
    assert landing.index('href="#item-p1-e1"') < landing.index('id="latex-p2-e1"')


# --------------------------------------------------------------------------- no bulk accept


def test_the_list_view_offers_no_bulk_accept(rich):
    """Every decision the pipeline asks for is meaning-level (review tiers, "meaning-level
    only"): none is accepted in bulk without being opened, by confidence or "accept all"
    (removed 2026-10-09)."""
    page = page_of(rich)
    for gone in ("bulk-form", "min_confidence", "accept_confident", "accept_all", "Accept all"):
        assert gone not in page
    assert not hasattr(review, "apply_bulk_accept")


@pytest.mark.parametrize(
    ("slug", "fields"),
    [
        ("headings", {"action": "accept_all"}),
        ("equations", {"action": "accept_confident", "min_confidence": "50"}),
        ("figures", {"action": "accept"}),
    ],
)
def test_a_bulk_post_is_not_found_and_changes_nothing(rich, slug, fields, caplog):
    caplog.set_level(logging.DEBUG)
    before = statuses(rich)
    response = post(rich, slug, **fields)
    assert response.status_code == 404
    assert statuses(rich) == before and edits(rich) == []
    assert rich.pipeline.rendered == []
    assert "review_bulk" not in caplog.text


def test_a_bulk_post_needs_the_csrf_token_first(rich):
    response = rich.browser.post(
        f"/jobs/{rich.job_id}/review/headings",
        data={"csrf_token": "forged", "action": "accept_all"},
    )
    assert response.status_code == 403


def test_accepts_are_counted_when_the_job_is_deleted(rich):
    for eid in ("p1-e3", "p2-e0", "p3-e3"):
        assert post(rich, eid, action="accept").status_code == 303
    response = rich.browser.post(
        f"/jobs/{rich.job_id}/delete",
        data={"csrf_token": token(rich.browser), "confirm": "yes"},
    )
    assert response.status_code == 303
    with session_scope(rich.ctx.factory) as session:
        rows = session.scalars(
            select(ErrorAggregate).where(ErrorAggregate.category == "review:accept")
        ).all()
        assert {(r.step, r.count) for r in rows} == {("heading", 3)}
        assert session.scalars(select(ReviewEdit)).all() == []  # deleted with the job


def test_a_check_code_shared_by_two_checks_gets_the_right_sentence(rich):
    empty_latex = Check(name="latex.not_empty", passed=False, code="empty")
    empty_alt = Check(name="alt.not_empty", passed=False, code="empty")
    assert "transcription is empty" in review.describe_failed([empty_latex])
    assert "alt text is empty" in review.describe_failed([empty_alt])
    assert review.describe_failed([empty_alt]).endswith("(alt.not_empty: empty)")
