"""Review screen at document scale: grouping and order, stable numbering, where the
reviewer lands after a decision, and bulk accept.

Uses `rich_output()` from test_web_review.py: three pages with every status in every
group, stored in scrambled order.
"""

from __future__ import annotations

import logging
import re

import pytest
from sqlalchemy import select

from a11y_stem.core.types import Check, Fix, Kind, Status
from a11y_stem.web import review
from a11y_stem.web.db import ErrorAggregate, ReviewEdit, session_scope
from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, make_pdf, token
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
    response = env.browser.get(f"/jobs/{env.job_id}/review")
    assert response.status_code == 200
    return response.text


def post(env, target: str, **fields):
    data = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review")} | fields
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


def test_page_lists_groups_in_priority_order(rich):
    page = page_of(rich)
    decided = {"equations": ["p2-e3", "p3-e4"], "figures": ["p3-e5"], "headings": ["p1-e0"]}
    assert cards(page) == [
        *EQUATIONS_PENDING,
        *decided["equations"],
        *FIGURES_PENDING,
        *decided["figures"],
        *HEADINGS_PENDING,
        *decided["headings"],
    ]
    for slug, ids in decided.items():
        folded = group(page, slug).split('<details class="done-group">', 1)[1]
        assert re.findall(r'<article id="fix-([\w-]+)"', folded) == ids


def test_labels_stay_the_same_after_a_decision(rich):
    title = '<h3 id="fix-title-p2-e2">Equation 3 <span class="meta">on page 2</span></h3>'
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
    assert "A letter is bold in the PDF but not in this transcription" in style
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


def test_job_page_links_each_kind_to_its_group(rich):
    page = rich.browser.get(f"/jobs/{rich.job_id}").text
    base = f"/jobs/{rich.job_id}/review"
    assert "7 need your review:" in page
    assert f'<a href="{base}#group-equations">3 equations</a>,' in page
    assert f'<a href="{base}#group-figures">1 figure</a>,' in page
    assert f'<a href="{base}#group-headings">3 headings</a>.' in page
    for slug in ("equations", "figures", "headings"):
        assert f'id="group-{slug}"' in page_of(rich)


# --------------------------------------------------------------------------- landing


def test_after_a_decision_the_reviewer_lands_on_the_next_fix_waiting(rich):
    location = post(rich, "p1-e1", action="accept").headers["location"]
    assert location == f"/jobs/{rich.job_id}/review#item-p2-e1"
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
    assert location == f"/jobs/{rich.job_id}/review#item-p2-e1"  # last one: wraps round
    landing = card(page_of(rich), "p2-e1")
    assert '<a href="#item-p3-e3">Heading 5 on page 3</a>: Rejected.' in landing


def test_landing_item_is_focusable_and_its_notice_comes_first(rich):
    post(rich, "p1-e1", action="accept")
    landing = card(page_of(rich), "p2-e1")
    assert landing.startswith('<li id="item-p2-e1" class="review-item" tabindex="-1">')
    # Tab order from the landing point: the link back, then the card's own controls.
    assert landing.index('href="#item-p1-e1"') < landing.index('id="latex-p2-e1"')


# --------------------------------------------------------------------------- bulk (rules)


def test_bulk_takes_only_what_a_single_accept_could():
    data = rich_output().result
    every = review.bulk_candidates(data, review.BulkRequest(Kind.EQUATION, 50))
    # p2-e1 FAILED, p3-e0 has a failed error-level check, decided ones are not waiting.
    assert sorted(f.element_id for f in every) == ["p1-e1", "p2-e2"]
    at_80 = review.bulk_candidates(data, review.BulkRequest(Kind.EQUATION, 80))
    assert [f.element_id for f in at_80] == ["p1-e1"]
    headings = review.bulk_candidates(data, review.BulkRequest(Kind.HEADING, None))
    assert sorted(f.element_id for f in headings) == ["p1-e3", "p2-e0", "p3-e3"]


def test_threshold_compares_the_percentage_the_page_shows():
    fix = Fix(element_id="x", kind=Kind.EQUATION, status=Status.NEEDS_REVIEW, confidence=0.795)
    assert review.at_or_above(fix, 80)  # shown as 80%, so "at or above 80%" includes it
    fix.confidence = 0.7949
    assert not review.at_or_above(fix, 80)
    fix.confidence = None
    assert not review.at_or_above(fix, 50)


@pytest.mark.parametrize(
    ("slug", "action", "threshold", "error"),
    [
        ("equations", "accept_all", "", "only offered for headings"),
        ("figures", "accept_all", "", "only offered for headings"),
        ("equations", "accept_confident", "85", "Choose a confidence from the list"),
        ("equations", "accept_confident", "", "Choose a confidence from the list"),
        ("equations", "accept_confident", "1e3", "Choose a confidence from the list"),
        ("headings", "delete", "80", "Choose an action"),
    ],
)
def test_parse_bulk_refuses_what_the_page_never_offers(slug, action, threshold, error):
    with pytest.raises(review.ReviewInvalid) as caught:
        review.parse_bulk(slug, action, threshold)
    [field] = caught.value.errors
    assert field.field_id == f"min-confidence-{slug}" and error in field.message


def test_parse_bulk_accepts_the_offered_choices():
    assert review.parse_bulk("headings", "accept_all", "") == review.BulkRequest(Kind.HEADING, None)
    assert review.parse_bulk("figures", "accept_confident", "70%") == review.BulkRequest(
        Kind.FIGURE, 70
    )
    with pytest.raises(LookupError):
        review.parse_bulk("paragraphs", "accept_all", "")


# --------------------------------------------------------------------------- bulk (page)


def test_bulk_forms_show_how_many_each_choice_accepts(rich):
    page = page_of(rich)
    equations = group(page, "equations")
    assert '<option value="90">90% (0 equations)</option>' in equations
    assert '<option value="80" selected>80% (1 equation)</option>' in equations
    assert '<option value="60">60% (2 equations)</option>' in equations
    assert "passed every required check are included (2 equations now)" in equations
    assert "1 of these has a warning: read it on the card first." in equations
    assert "Accept all" not in equations  # only headings get "accept all"
    assert '<label for="min-confidence-equations">' in equations
    headings = group(page, "headings")
    assert "Accept all 3 remaining headings</button>" in headings
    assert "Not included: 1 heading that could not be converted." in headings


def test_accept_all_headings(rich, caplog):
    caplog.set_level(logging.DEBUG)
    before = statuses(rich)
    response = post(rich, "headings", action="accept_all")
    assert response.status_code == 303
    assert response.headers["location"] == f"/jobs/{rich.job_id}/review#group-headings"
    after = statuses(rich)
    for eid in ("p1-e3", "p2-e0", "p3-e3"):
        assert after[eid] == "accepted"
    assert after["p2-e4"] == "abstained"  # never accepted in bulk
    changed = {eid for eid in after if after[eid] != before[eid]}
    assert changed == {"p1-e3", "p2-e0", "p3-e3"}
    assert result(rich).stats.by_status["accepted"] == 5
    # One ReviewEdit per fix, as a single "Accept" would have stored.
    recorded = edits(rich)
    assert sorted(e.element_id for e in recorded) == ["p1-e3", "p2-e0", "p3-e3"]
    assert {e.action for e in recorded} == {"accept"}
    assert {e.before["status"] for e in recorded} == {"needs_review"}
    assert {e.after["status"] for e in recorded} == {"accepted"}
    # The output HTML is re-rendered once for the whole batch.
    assert len(rich.pipeline.rendered) == 1
    assert "p3-e3:accepted" in (rich.job(rich.job_id).html or "")
    # The count is confirmed where the reviewer lands, and at the top.
    page = page_of(rich)
    headings = group(page, "headings")
    assert '<div class="notice" role="status"><p>Accepted 3 headings.</p></div>' in headings
    assert "Accepted 3 headings." in page[: page.index('class="review-toc"')]
    assert "accept_all" not in headings.split("Accepted 3 headings.")[1]  # nothing left
    assert "review_bulk" in caplog.text and "accepted=3" in caplog.text
    assert "SECRET" not in caplog.text


def test_bulk_accept_is_idempotent(rich):
    post(rich, "headings", action="accept_all")
    first = statuses(rich)
    response = post(rich, "headings", action="accept_all")
    assert response.status_code == 303
    assert statuses(rich) == first
    assert len(edits(rich)) == 3
    assert len(rich.pipeline.rendered) == 1  # nothing to re-render
    assert "Nothing changed: no headings were waiting that could be accepted." in group(
        page_of(rich), "headings"
    )


def test_accept_by_confidence(rich):
    assert post(rich, "equations", action="accept_confident", min_confidence="80").status_code
    assert statuses(rich)["p1-e1"] == "accepted"
    assert statuses(rich)["p2-e2"] == "needs_review"  # 65%
    assert "Accepted 1 equation at 80% confidence or higher." in page_of(rich)
    post(rich, "equations", action="accept_confident", min_confidence="60")
    after = statuses(rich)
    assert after["p2-e2"] == "accepted"
    # Never in bulk: a FAILED fix, and a proposal that failed an error-level check even
    # though its confidence (90%) clears every threshold.
    assert after["p2-e1"] == "failed" and after["p3-e0"] == "needs_review"
    assert len(rich.pipeline.rendered) == 2
    post(rich, "equations", action="accept_confident", min_confidence="50")
    assert "Nothing changed: no equations at 50% confidence or higher were waiting" in (
        page_of(rich)
    )
    post(rich, "figures", action="accept_confident", min_confidence="70")
    after = statuses(rich)
    assert after["p1-e2"] == "accepted"  # exactly 70%
    assert (after["p3-e1"], after["p3-e2"]) == ("abstained", "failed")


def test_bulk_never_overrides_a_reviewers_decision(rich):
    post(rich, "p1-e3", action="reject")
    post(rich, "p2-e0", action="edit", level="3")
    post(rich, "headings", action="accept_all")
    after = statuses(rich)
    assert (after["p1-e3"], after["p2-e0"], after["p3-e3"]) == ("rejected", "edited", "accepted")
    assert "Accepted 1 heading." in page_of(rich)


def test_a_bulk_choice_the_page_never_offers_changes_nothing(rich):
    before = statuses(rich)
    response = post(rich, "equations", action="accept_all")
    assert response.status_code == 422
    assert '<a href="#min-confidence-equations">' in response.text  # error summary
    assert 'aria-invalid="true"' in group(response.text, "equations")
    assert statuses(rich) == before and edits(rich) == []
    assert post(rich, "figures", action="accept_confident", min_confidence="5").status_code == 422
    assert statuses(rich) == before and rich.pipeline.rendered == []


def test_bulk_accept_needs_the_csrf_token(rich):
    before = statuses(rich)
    response = rich.browser.post(
        f"/jobs/{rich.job_id}/review/headings",
        data={"csrf_token": "forged", "action": "accept_all"},
    )
    assert response.status_code == 403
    assert statuses(rich) == before and edits(rich) == []


def test_bulk_accept_on_someone_elses_job_is_not_found(rich, caplog):
    caplog.set_level(logging.DEBUG)
    before = statuses(rich)
    rich.allow(BOB)
    bob = rich.client()
    login(bob, BOB)
    for slug, action in [("headings", "accept_all"), ("equations", "accept_all")]:
        # The second form is one the page never offers: still a 404, not a 422 that
        # would confirm the job exists.
        response = bob.post(
            f"/jobs/{rich.job_id}/review/{slug}",
            data={"csrf_token": token(bob), "action": action},
        )
        assert response.status_code == 404
    assert statuses(rich) == before
    assert edits(rich) == [] and rich.pipeline.rendered == []
    assert "review_invalid" not in caplog.text and "review_bulk" not in caplog.text


def test_bulk_accepts_are_counted_when_the_job_is_deleted(rich):
    post(rich, "headings", action="accept_all")
    response = rich.browser.post(
        f"/jobs/{rich.job_id}/delete", data={"csrf_token": token(rich.browser)}
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
