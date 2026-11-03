"""Review at scale: accept many at once, filter the decision list, report an item, and leave
a page out from its card (`web.bulk_accept`, `routes/bulk.py`, `routes/review.py`).

The document is `fixtures.web.review_doc` with equations and figures added in every state
the bulk actions must tell apart: an undecided equation or figure that failed nothing (accepted
in bulk), and ones with a failed check, a flag from the second check, an error-level check, a
decorative mark, an empty alt text, and a decision already made (left alone).
"""

from __future__ import annotations

import re
from html import unescape

import pytest
from sqlalchemy import select

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, token, upload
from readablestem.core.types import (
    Check,
    ConversionOutput,
    DocumentResult,
    Element,
    EquationFix,
    FigureFix,
    Fix,
    Kind,
    Status,
    Verification,
)
from readablestem.web import bulk_accept, report_item
from readablestem.web.db import ReviewEdit, session_scope
from readablestem.web.review import BULK_KEY, WRITE_KEY, ReviewInvalid

BOX = (50.0, 600.0, 200.0, 620.0)
EQUATION_CANDIDATES = ["p1-e2", "p2-e7"]
FIGURE_CANDIDATES = ["p1-e6", "p2-e12"]  # p2-e12: its second check ran and passed


def _equation(eid: str, page: int, order: int, **fix) -> tuple[Element, Fix]:
    element = Element(id=eid, kind=Kind.EQUATION, page=page, bbox=BOX, order=order, display="block")
    equation = EquationFix(latex="x", mathml="<math><mi>x</mi></math>", speech="x")
    return element, Fix(
        element_id=eid, kind=Kind.EQUATION, equation=equation, review="required", **fix
    )


def _figure(eid: str, page: int, order: int, figure: FigureFix, **fix) -> tuple[Element, Fix]:
    element = Element(id=eid, kind=Kind.FIGURE, page=page, bbox=BOX, order=order)
    return element, Fix(element_id=eid, kind=Kind.FIGURE, figure=figure, review="required", **fix)


def scaled_output() -> ConversionOutput:
    output = review_output()
    result = output.result
    # p1-e2 (equation) and p1-e6 (figure) wait with nothing failed once its warning is gone.
    for fix in result.fixes:
        if fix.element_id == "p1-e2":
            fix.checks = [Check(name="latex.allowed_commands", passed=True)]
    flagged = Verification(check="equation_match", flagged=True)
    chart = FigureFix(figure_type="chart", alt="A line that rises.")
    needs = Status.NEEDS_REVIEW
    added = [
        _equation("p2-e7", 1, 70, status=needs, confidence=0.6),
        _equation("p2-e8", 1, 71, status=needs, verifications=[flagged]),
        _equation(
            "p2-e9",
            1,
            72,
            status=needs,
            checks=[Check(name="mathml.structure", passed=False, code="bad_arity")],
        ),
        _equation("p2-e10", 1, 73, status=Status.EDITED),
        _equation(
            "p2-e11",
            1,
            74,
            status=needs,
            checks=[Check(name="equation.text_layer_style", passed=False, severity="warning")],
        ),
        _figure(
            "p2-e12",
            1,
            75,
            chart,
            status=needs,
            verifications=[Verification(check="figure_alt", flagged=False)],
        ),
        _figure(
            "p2-e13", 1, 76, FigureFix(figure_type="photo", alt="", decorative=True), status=needs
        ),
        _figure("p2-e14", 1, 77, FigureFix(figure_type="photo", alt="  "), status=needs),
        _figure(
            "p2-e15",
            1,
            78,
            chart,
            status=needs,
            checks=[Check(name="alt.length", passed=False, severity="warning", code="alt_long")],
        ),
        _figure("p2-e16", 1, 79, chart, status=Status.ACCEPTED),
        # The second check gave no answer (it failed open): not "every check passed".
        _equation(
            "p2-e17", 1, 80, status=needs, verifications=[Verification(check="equation_match")]
        ),
    ]
    for element, fix in added:
        result.elements.append(element)
        result.fixes.append(fix)
    return output


@pytest.fixture
def scaled(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = scaled_output()
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def stored(env) -> DocumentResult:
    return DocumentResult.model_validate(env.job(env.job_id).result_json)


def statuses(env) -> dict[str, str]:
    return {f.element_id: f.status.value for f in stored(env).fixes}


def edits(env) -> list[ReviewEdit]:
    with session_scope(env.ctx.factory) as session:
        return list(session.scalars(select(ReviewEdit).order_by(ReviewEdit.created_at)))


def version(env) -> str:
    return str(env.job(env.job_id).result_version or 0)


def confirm_page(env, slug: str, view: str = "list"):
    return env.browser.get(f"/jobs/{env.job_id}/accept/{slug}?view={view}")


def accept(env, slug: str, **fields):
    form = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review"), "view": "list"}
    form |= {"version": version(env)} | fields
    return env.browser.post(f"/jobs/{env.job_id}/accept/{slug}", data=form, follow_redirects=False)


# --------------------------------------------------------------------------- the rules


def test_only_undecided_items_with_nothing_failed_are_candidates():
    result = scaled_output().result
    assert [f.element_id for f in bulk_accept.candidates(result, "equations")] == (
        EQUATION_CANDIDATES
    )
    assert [f.element_id for f in bulk_accept.candidates(result, "figures")] == FIGURE_CANDIDATES
    offers = {o.slug: o.count for o in bulk_accept.offers(result)}
    assert offers == {"equations": 2, "figures": 2}


def test_a_heading_group_or_kept_page_is_never_a_candidate():
    result = scaled_output().result
    kinds = {
        f.kind for slug in ("equations", "figures") for f in bulk_accept.candidates(result, slug)
    }
    assert kinds == {Kind.EQUATION, Kind.FIGURE}
    assert all(f.group_key is None for f in bulk_accept.candidates(result, "figures"))


def test_accept_all_marks_each_change_as_bulk_and_refuses_when_there_is_none():
    result = scaled_output().result
    changes = bulk_accept.accept_all(result, "equations")
    assert [c.element_id for c in changes] == EQUATION_CANDIDATES
    assert all(c.after[BULK_KEY] == "equations" and c.action == "accept" for c in changes)
    assert all(result.fix_for(e).reviewed for e in EQUATION_CANDIDATES)
    with pytest.raises(ReviewInvalid) as refused:
        bulk_accept.accept_all(result, "equations")
    assert "No equation is waiting" in refused.value.errors[0].message


# --------------------------------------------------------------------------- the pages


def test_the_confirm_page_gives_the_count_and_lists_exactly_what_would_be_accepted(scaled):
    response = confirm_page(scaled, "equations")
    assert response.status_code == 200
    page = response.text
    assert "<h1>Accept all remaining equations whose checks passed</h1>" in page
    assert "2 equations are waiting" in " ".join(page.split())
    listed = re.findall(r'<li id="bulk-([\w-]+)"', page)
    assert listed == EQUATION_CANDIDATES
    assert 'value="list"' in page and 'name="version"' in page and 'name="csrf_token"' in page
    figures = confirm_page(scaled, "figures").text
    assert re.findall(r'<li id="bulk-([\w-]+)"', figures) == FIGURE_CANDIDATES
    assert "A car on a road between a house and a school." in unescape(figures)


def test_the_list_and_the_review_screen_offer_the_actions_with_counts(scaled):
    listing = scaled.browser.get(f"/jobs/{scaled.job_id}/review/list").text
    assert f'href="/jobs/{scaled.job_id}/accept/equations?view=list"' in listing
    assert "Accept all remaining equations whose checks passed (2)" in listing
    assert "Accept all figures that have generated alt text (2)" in listing
    split = scaled.browser.get(f"/jobs/{scaled.job_id}/review").text
    assert f'href="/jobs/{scaled.job_id}/accept/figures?view=split"' in split


def test_no_offer_when_nothing_qualifies(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = review_output()  # its one waiting equation has a warning
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        listing = client.get(f"/jobs/{env.job_id}/review/list").text
        assert "bulk-offers" in listing  # the figure p1-e6 qualifies
        assert "Accept all remaining equations" not in listing
        assert "Nothing is waiting" not in confirm_page(env, "equations").text
        assert "No equation is waiting" in confirm_page(env, "equations").text


# --------------------------------------------------------------------------- the decision


def test_accepting_changes_only_the_listed_items_in_one_write_marked_bulk(scaled):
    before = statuses(scaled)
    start = int(version(scaled))
    response = accept(scaled, "equations")
    assert response.status_code == 303
    after = statuses(scaled)
    for eid in EQUATION_CANDIDATES:
        assert before[eid] == "needs_review" and after[eid] == "accepted"
    assert {k: v for k, v in after.items() if k not in EQUATION_CANDIDATES} == {
        k: v for k, v in before.items() if k not in EQUATION_CANDIDATES
    }
    assert int(version(scaled)) == start + 1  # one write
    recorded = edits(scaled)
    assert [e.element_id for e in recorded] == EQUATION_CANDIDATES
    assert all(
        e.after[BULK_KEY] == "equations" and e.after[WRITE_KEY] == start + 1 for e in recorded
    )
    assert all(e.action == "accept" and e.before["status"] == "needs_review" for e in recorded)
    fixes = {f.element_id: f for f in stored(scaled).fixes}
    assert all(fixes[e].reviewed for e in EQUATION_CANDIDATES)


def test_figures_are_accepted_the_same_way_and_the_notice_says_how_many(scaled):
    response = accept(scaled, "figures")
    assert response.status_code == 303
    after = statuses(scaled)
    assert [after[e] for e in FIGURE_CANDIDATES] == ["accepted", "accepted"]
    # Decorative, empty, failed-check and decided figures are as they were.
    assert after["p2-e13"] == after["p2-e14"] == after["p2-e15"] == "needs_review"
    page = scaled.browser.get(response.headers["location"].split("#")[0]).text
    assert "Accepted 2 figures with generated alt text." in unescape(page)


def test_undo_takes_the_whole_action_back_as_one_step(scaled):
    original = statuses(scaled)
    accept(scaled, "equations")
    listing = scaled.browser.get(f"/jobs/{scaled.job_id}/review/list").text
    assert 'data-role="undo"' in listing  # offered on the page that follows
    undone = scaled.browser.post(
        f"/jobs/{scaled.job_id}/undo",
        data={
            "csrf_token": token(scaled.browser, f"/jobs/{scaled.job_id}/review"),
            "version": version(scaled),
            "view": "list",
        },
    )
    assert undone.status_code in (200, 303)
    assert statuses(scaled) == original
    undo_edits = [e for e in edits(scaled) if e.action == "undo"]
    assert sorted(e.element_id for e in undo_edits) == EQUATION_CANDIDATES
    assert not any(f.reviewed for f in stored(scaled).fixes if f.element_id in EQUATION_CANDIDATES)


def test_the_review_record_says_which_bulk_action_each_decision_was_part_of(scaled):
    accept(scaled, "equations")
    accept(scaled, "figures")
    page = " ".join(unescape(scaled.browser.get(f"/jobs/{scaled.job_id}/record").text).split())
    assert "together with the other equations whose checks passed, in one action" in page
    assert "together with the other figures with generated alt text, in one action" in page
    assert "other clean tables" not in page


def test_the_form_needs_the_csrf_token(scaled):
    before = statuses(scaled)
    response = scaled.browser.post(
        f"/jobs/{scaled.job_id}/accept/equations",
        data={"csrf_token": "forged", "version": version(scaled)},
    )
    assert response.status_code == 403
    assert statuses(scaled) == before and edits(scaled) == []


def test_another_person_cannot_see_or_accept_them(scaled):
    bob = scaled.client()
    login(bob, BOB)
    path = f"/jobs/{scaled.job_id}/accept/equations"
    assert bob.get(path).status_code == 404
    post = bob.post(path, data={"csrf_token": token(bob, "/app"), "version": version(scaled)})
    assert post.status_code == 404
    assert edits(scaled) == []
    anonymous = scaled.client()
    assert anonymous.get(path, follow_redirects=False).status_code in (303, 401, 404)


def test_a_page_drawn_before_the_last_change_is_refused_and_nothing_is_accepted(scaled):
    stale = version(scaled)
    first = scaled.browser.post(
        f"/jobs/{scaled.job_id}/review/p1-e6",
        data={
            "csrf_token": token(scaled.browser, f"/jobs/{scaled.job_id}/review"),
            "view": "list",
            "action": "accept",
            "version": stale,
        },
    )
    assert first.status_code == 303
    before = statuses(scaled)
    refused = accept(scaled, "equations", version=stale)
    assert refused.status_code == 409
    assert statuses(scaled) == before
    assert [e.element_id for e in edits(scaled)] == ["p1-e6"]


def test_an_unknown_kind_is_not_found(scaled):
    assert confirm_page(scaled, "headings").status_code == 404
    assert accept(scaled, "headings").status_code == 404
    assert accept(scaled, "tables").status_code == 404


def test_with_nothing_to_accept_nothing_is_written(scaled):
    accept(scaled, "equations")
    start = version(scaled)
    again = accept(scaled, "equations")
    assert again.status_code == 303
    assert version(scaled) == start
    page = scaled.browser.get(again.headers["location"]).text
    assert "No equation is waiting with every check passed." in unescape(page)


def test_from_the_review_screen_it_goes_on_to_the_next_decision(scaled):
    response = accept(scaled, "equations", view="split")
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/jobs/{scaled.job_id}/review")
    assert "/review/list" not in response.headers["location"]


# --------------------------------------------------------------------------- the filter


def needs_decision(page: str) -> str:
    start = page.index('<section id="needs-decision"')
    return page[start : page.index("</section>", page.index('id="optional-checks"'))]


def cards_in(section: str) -> list[str]:
    return re.findall(r'<article id="(?:fix-|group-card-)?([\w-]+)"', section)


def test_filter_by_kind_shows_only_that_kind_and_says_how_many(scaled):
    page = scaled.browser.get(f"/jobs/{scaled.job_id}/review/list?kind=figures").text
    section = needs_decision(page)
    assert 'id="group-figures"' in section and 'id="group-equations"' not in section
    assert 'id="group-headings"' not in section
    result = re.search(r'<p id="filter-result"[^>]*>([^<]*)</p>', page)
    assert result and result.group(1).startswith("Showing 5 of ")
    assert "figures" in result.group(1)
    assert 'role="status"' in result.group(0) and 'tabindex="-1"' in result.group(0)
    assert '<option value="figures" selected>' in page


def test_filter_by_page_keeps_decisions_on_that_page_and_heading_groups_spanning_it(scaled):
    page = scaled.browser.get(f"/jobs/{scaled.job_id}/review/list?page=2").text
    section = needs_decision(page)
    assert "fix-p1-e2" not in section and "fix-p2-e7" in section
    assert "hg-p1-e3" in section  # the repeated heading is also on page 2
    only_one = scaled.browser.get(f"/jobs/{scaled.job_id}/review/list?page=1").text
    assert "fix-p2-e7" not in needs_decision(only_one)
    assert 'value="1"' in only_one


def test_both_filters_together_and_a_way_back(scaled):
    page = scaled.browser.get(f"/jobs/{scaled.job_id}/review/list?kind=equations&page=2").text
    section = needs_decision(page)
    assert "fix-p2-e7" in section and "fix-p1-e2" not in section and "fix-p1-e6" not in section
    assert f'href="/jobs/{scaled.job_id}/review/list#needs-decision"' in page  # show all


def test_nothing_matching_says_so_and_bad_values_are_ignored(scaled):
    none = scaled.browser.get(f"/jobs/{scaled.job_id}/review/list?kind=headings&page=1").text
    assert "No decision matches" not in none or "headings" in none
    ignored = scaled.browser.get(f"/jobs/{scaled.job_id}/review/list?kind=nonsense&page=99").text
    assert 'id="filter-result"' not in ignored  # shown whole
    letters = scaled.browser.get(f"/jobs/{scaled.job_id}/review/list?page=abc").text
    assert 'id="filter-result"' not in letters


def test_the_filter_is_a_labelled_get_form(scaled):
    page = scaled.browser.get(f"/jobs/{scaled.job_id}/review/list").text
    form = page[
        page.index('<form method="get"') : page.index("</form>", page.index('<form method="get"'))
    ]
    assert 'action="/jobs/' in form and 'aria-labelledby="filter-title"' in form
    assert 'for="filter-kind"' in form and 'for="filter-page"' in form
    assert 'type="number"' in form and 'min="1"' in form and 'max="2"' in form


# --------------------------------------------------------------------------- report this item


def test_the_link_names_the_job_and_the_item_and_nothing_from_the_document(tmp_path):
    env = WebEnv(tmp_path, contact_email="help@example.org")
    env.converter.output = scaled_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        listing = client.get(f"/jobs/{env.job_id}/review/list").text
        link = re.search(r'<a href="(mailto:[^"]+)"[^>]*data-role="report-item"', listing)
        assert link, "a report link on a card"
        target = unescape(link.group(1))
        assert target.startswith("mailto:help@example.org?subject=")
        assert env.job_id in target and "Item%3A%20p" in target
        for secret in ("Teacher Support", "omega", "A car on a road"):
            assert secret not in target
        split = client.get(f"/jobs/{env.job_id}/review?item=p1-e2").text
        assert 'data-role="report-item"' in split
        assert "Item%3A%20p1-e2" in unescape(split)


def test_item_url_uses_the_feedback_url_when_there_is_one_and_nothing_without():
    class S:
        feedback_url = "https://forms.example.org/f?x=1"
        contact_email = "help@example.org"

    url = report_item.item_url(S, "abc123", "p2-e3")  # type: ignore[arg-type]
    assert url == "https://forms.example.org/f?x=1&job=abc123&item=p2-e3"
    S.feedback_url = None  # type: ignore[assignment]
    mail = report_item.item_url(S, "abc123", "p2-e3")  # type: ignore[arg-type]
    assert mail and mail.startswith("mailto:help@example.org?") and "abc123" in mail
    S.contact_email = None  # type: ignore[assignment]
    assert report_item.item_url(S, "abc123", "p2-e3") is None  # type: ignore[arg-type]


def test_no_link_without_a_feedback_path(tmp_path):
    env = WebEnv(tmp_path, contact_email=None, feedback_url=None)
    env.converter.output = scaled_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        assert "report-item" not in client.get(f"/jobs/{job_id}/review/list").text
        assert "report-item" not in client.get(f"/jobs/{job_id}/review?item=p1-e2").text


# --------------------------------------------------------------------------- leave a page out


def kept_page_output() -> ConversionOutput:
    output = review_output()
    result = output.result
    result.elements.append(
        Element(id="p2-e9", kind=Kind.FIGURE, page=1, bbox=(0.0, 0.0, 612.0, 792.0), order=99)
    )
    result.fixes.append(
        Fix(
            element_id="p2-e9",
            kind=Kind.FIGURE,
            status=Status.FAILED,
            reason="scanned_page",
            review="required",
            group_key="pg-p2-e9",
            group_size=1,
        )
    )
    output.crops["p2-e9"] = output.crops["p1-e6"]
    return output


@pytest.fixture
def kept(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = kept_page_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def test_a_scanned_page_card_has_a_leave_this_page_out_button_on_both_screens(kept):
    for url in (f"/jobs/{kept.job_id}/review?item=pg-p2-e9", f"/jobs/{kept.job_id}/review/list"):
        page = kept.browser.get(url).text
        assert "Leave this page out of the HTML" in page, url
        assert f'action="/jobs/{kept.job_id}/pages/2/omit"' in page
        assert 'data-role="omit-page-card"' in page and "leave-hint-pg-p2-e9" in page


def test_the_button_leaves_the_page_out_goes_on_and_undo_puts_it_back(kept):
    page = kept.browser.get(f"/jobs/{kept.job_id}/review?item=pg-p2-e9").text
    form = re.search(r'action="/jobs/[\w]+/pages/2/omit".*?</form>', page, re.S)
    assert form
    fields = dict(re.findall(r'name="(\w+)" value="([^"]*)"', form.group(0)))
    assert fields["after"] == "review" and fields["view"] == "split"
    response = kept.browser.post(
        f"/jobs/{kept.job_id}/pages/2/omit", data=fields, follow_redirects=False
    )
    assert response.status_code == 303
    assert "/pages/" not in response.headers["location"]
    assert "page-tools" not in response.headers["location"]  # on to a decision, not the tools
    after = stored(kept)
    assert after.omitted_pages == [1]
    notice = kept.browser.get(response.headers["location"]).text
    assert "Page 2" in unescape(notice) and 'data-role="undo"' in notice
    undone = kept.browser.post(
        f"/jobs/{kept.job_id}/undo",
        data={
            "csrf_token": fields["csrf_token"],
            "version": version(kept),
            "view": "split",
        },
    )
    assert undone.status_code in (200, 303)
    assert stored(kept).omitted_pages == []


def test_handwriting_on_a_typed_page_has_no_leave_out_button(tmp_path):
    output = kept_page_output()
    fix = next(f for f in output.result.fixes if f.element_id == "p2-e9")
    fix.reason = "handwriting_on_page"
    env = WebEnv(tmp_path)
    env.converter.output = output
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        page = client.get(f"/jobs/{job_id}/review?item=pg-p2-e9").text
        assert "omit-page-card" not in page and "leave-hint" not in page


# --------------------------------------------------------------------------- review fixes


def test_the_preview_shows_the_original_crop_beside_each_proposal(scaled):
    page = confirm_page(scaled, "equations").text
    assert f"/jobs/{scaled.job_id}/crops/p1-e2.png" in page
    assert "Original in the PDF" in page
    figures = confirm_page(scaled, "figures").text
    assert f"/jobs/{scaled.job_id}/crops/p1-e6.png" in figures


def test_a_blank_or_missing_version_is_refused_and_nothing_is_accepted(scaled):
    before = statuses(scaled)
    blank = accept(scaled, "equations", version="")
    assert blank.status_code == 409
    token_ = token(scaled.browser, f"/jobs/{scaled.job_id}/review")
    missing = scaled.browser.post(
        f"/jobs/{scaled.job_id}/accept/equations", data={"csrf_token": token_, "view": "list"}
    )
    assert missing.status_code == 409
    assert statuses(scaled) == before and edits(scaled) == []


def test_an_answer_that_failed_open_is_not_a_passed_check():
    result = scaled_output().result
    ids = [f.element_id for f in bulk_accept.candidates(result, "equations")]
    assert "p2-e17" not in ids and "p2-e8" not in ids  # no answer, and flagged
    assert "p2-e12" in [f.element_id for f in bulk_accept.candidates(result, "figures")]


def test_a_decision_in_a_filtered_list_comes_back_to_the_filtered_list(scaled):
    page = scaled.browser.get(f"/jobs/{scaled.job_id}/review/list?kind=equations&page=2").text
    assert 'name="flt" value="kind=equations&amp;page=2"' in page
    response = scaled.browser.post(
        f"/jobs/{scaled.job_id}/review/p2-e7",
        data={
            "csrf_token": token(scaled.browser, f"/jobs/{scaled.job_id}/review"),
            "view": "list",
            "action": "accept",
            "version": version(scaled),
            "flt": "kind=equations&page=2&evil=1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/review/list?kind=equations&page=2#" in response.headers["location"]
    assert "evil" not in response.headers["location"]
    plain = scaled.browser.post(
        f"/jobs/{scaled.job_id}/review/p2-e8",
        data={
            "csrf_token": token(scaled.browser, f"/jobs/{scaled.job_id}/review"),
            "view": "list",
            "action": "reject",
            "version": version(scaled),
        },
        follow_redirects=False,
    )
    assert "?" not in plain.headers["location"].split("#")[0]


def test_leave_out_says_how_many_decisions_go_with_the_page(kept):
    page = unescape(kept.browser.get(f"/jobs/{kept.job_id}/review?item=pg-p2-e9").text)
    assert "Any decisions still waiting on it are dropped with it." in page
    found = re.search(r"(\d+) decisions? waiting on it (?:goes|go) with it\.", page)
    assert found and int(found.group(1)) >= 1


def test_demo_hits_that_are_counted_are_revalidated_and_others_cached(tmp_path, monkeypatch):
    from readablestem.web import analytics

    async def nothing(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(analytics, "capture", nothing)
    with WebEnv(tmp_path, analytics_enabled=True).running() as client:
        counted = client.get("/demo?hm_id=abcdefgh1234")
        assert counted.headers["cache-control"] == "public, no-cache"
        again = client.get(
            "/demo?hm_id=abcdefgh1234", headers={"If-None-Match": counted.headers["etag"]}
        )
        assert again.status_code == 304
        assert client.get("/demo").headers["cache-control"] == "public, max-age=3600"
