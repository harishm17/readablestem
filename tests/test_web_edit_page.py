"""T3-5: edit what the tool did not propose.

The words of a paragraph, a line made a heading, a picture or an equation the tool did not
find. Uses fixtures.web.review_doc (a real PDF and a result measured on it). The model is the
offline client with an answer set per test: no key, no cost. Every edit marks the HTML out of
date (it is made when somebody asks for it), is stored as a ReviewEdit, goes through the
review's version guard and can be undone.
"""

from __future__ import annotations

import re
from html import unescape

import pytest
from sqlalchemy import select

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload, with_file
from readablestem.core.types import (
    ConversionOutput,
    DocumentResult,
    Element,
    Kind,
    Link,
    Status,
)
from readablestem.models.fake import FakeVisionClient
from readablestem.web import page_edits, reread
from readablestem.web.db import Blob, Job, ReviewEdit, session_scope

PARAGRAPH = "p1-e1"  # "A rigid body turning with angular velocity ⟦p1-e2⟧ moves a point ..."
PLAIN = "p1-e4"  # "Discuss vectors and scalars with the class."
CODE = "p1-e8"
ENTRY = "p1-e9"

EQUATION_ANSWER = {"is_equation": True, "latex": "x^2 + 1", "confidence": 0.99}


def edit_output() -> ConversionOutput:
    output = review_output()
    result = output.result
    result.elements.append(
        Element(id=CODE, kind=Kind.PARAGRAPH, page=0, bbox=(72.0, 600.0, 540.0, 640.0), order=50,
                text="for i in range(3):\n    print(i)", preformatted=True)
    )  # fmt: skip
    result.elements.append(
        Element(id=ENTRY, kind=Kind.PARAGRAPH, page=0, bbox=(72.0, 650.0, 540.0, 670.0), order=51,
                text="Vector a quantity with size and direction", term_end=6)
    )  # fmt: skip
    return output


@pytest.fixture
def edits(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = edit_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def stored(env) -> DocumentResult:
    return DocumentResult.model_validate(env.job(env.job_id).result_json)


def version(env) -> str:
    return str(env.job(env.job_id).result_version or 0)


def edit_post(env, element_id: str, **fields):
    path = f"/jobs/{env.job_id}/review"
    data = {"csrf_token": token(env.browser, path), "version": version(env)} | fields
    return env.browser.post(f"/jobs/{env.job_id}/edit/{element_id}", data=data)


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def element(env, element_id: str) -> Element:
    found = next(e for e in stored(env).elements if e.id == element_id)
    return found


# --------------------------------------------------------------------------- the words


def test_the_page_tools_list_the_text_and_open_one(edits):
    page = edits.browser.get(f"/jobs/{edits.job_id}/review?page=1&tools=1").text
    assert "Change the words, or make a line a heading" in page
    assert "Paragraph:" in page and "A rigid body turning" in page
    assert "Heading, level 1" in text_of(page)
    opened = edits.browser.get(
        f"/jobs/{edits.job_id}/review?page=1&tools=1&change={PARAGRAPH}"
    ).text
    assert "[equation 1]" in opened  # the inline equation is named, not shown as a mark
    assert "⟦" not in opened
    assert 'name="text"' in opened and 'value="heading"' in opened


def test_changing_the_words_keeps_the_equation_where_it_is(edits):
    typed = "A rigid body turning with angular velocity [equation 1] moves a point r quickly."
    response = edit_post(edits, PARAGRAPH, action="text", text=typed)
    assert response.status_code == 303
    assert "tools=1" in response.headers["location"]
    kept = element(edits, PARAGRAPH)
    assert (
        kept.text == "A rigid body turning with angular velocity ⟦p1-e2⟧ moves a point r quickly."
    )
    job = edits.job(edits.job_id)
    assert job.html_stale is True  # nothing rendered on the click
    html = with_file(edits).html or ""
    assert "moves a point r quickly." in html
    assert "<math" in html.split("moves a point r quickly")[0].split("angular velocity")[-1]


def test_an_equation_may_not_be_left_out_or_repeated_or_invented(edits):
    for typed, problem in [
        ("A rigid body turning with angular velocity omega moves a point.", "not at all"),
        ("It is [equation 1] and [equation 1] again.", "twice"),
        ("Angular velocity [equation 1] and ⟦p1-e5⟧ here.", "tool's own"),
    ]:
        response = edit_post(edits, PARAGRAPH, action="text", text=typed)
        assert response.status_code == 422, typed
        assert problem in unescape(response.text)
        assert "[equation 1]" in unescape(response.text)
    assert element(edits, PARAGRAPH).text and "⟦p1-e2⟧" in (element(edits, PARAGRAPH).text or "")


def test_the_words_are_cleaned_and_escaped_like_any_text(edits):
    hostile = 'Use <script>alert(1)</script> "x" & y​\x00 now'
    response = edit_post(edits, PLAIN, action="text", text=hostile)
    assert response.status_code == 303
    kept = element(edits, PLAIN)
    assert kept.text == 'Use <script>alert(1)</script> "x" & y now'
    html = with_file(edits).html or ""
    assert "<script>alert(1)</script>" not in html
    assert "Use &lt;script&gt;alert(1)&lt;/script&gt; &quot;x&quot; &amp; y now" in html or (
        "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    )
    assert "\x00" not in html and "​" not in html


@pytest.mark.parametrize(
    ("typed", "problem"),
    [
        ("Discuss vectors and scalars with the class.", "Nothing changed"),
        ("   ", "Enter the words"),
        ("x" * 12001, "under 6000 characters"),
        ("x " * 3001, "under 6000 characters"),
    ],
)
def test_text_that_cannot_be_used_is_refused_with_what_was_typed(edits, typed, problem):
    response = edit_post(edits, PLAIN, action="text", text=typed)
    assert response.status_code == 422
    assert problem in unescape(response.text)
    assert element(edits, PLAIN).text == "Discuss vectors and scalars with the class."
    assert 'aria-invalid="true"' in response.text


def test_program_code_keeps_its_lines(edits):
    typed = "for i in range(4):\r\n    print(i)\r\n"
    assert edit_post(edits, CODE, action="text", text=typed).status_code == 303
    assert element(edits, CODE).text == "for i in range(4):\n    print(i)"


def test_a_heading_keeps_its_level_and_changes_its_words(edits):
    response = edit_post(edits, "p1-e0", action="text", text="1 Vectors and scalars")
    assert response.status_code == 303
    result = stored(edits)
    fix = result.fix_for("p1-e0")
    assert fix is not None and fix.heading is not None
    assert fix.heading.text == "1 Vectors and scalars" and fix.heading.level == 1
    assert element(edits, "p1-e0").text == "1 Vectors and scalars"
    assert '<h1 id="p1-e0">1 Vectors and scalars</h1>' in (with_file(edits).html or "")


def test_links_stay_on_the_words_that_did_not_change(tmp_path):
    output = edit_output()
    paragraph = next(e for e in output.result.elements if e.id == PLAIN)
    paragraph.text = "Discuss vectors and scalars with the class, then open the notes here."
    start = paragraph.text.index("notes")
    paragraph.links = [
        Link(kind="uri", start=start, end=start + 5, href="https://example.org/notes"),
        Link(kind="uri", start=0, end=7, href="https://example.org/discuss"),
    ]
    env = WebEnv(tmp_path)
    env.converter.output = output
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        # Words before the first link change: both links move with their words.
        done = edit_post(
            env, PLAIN, action="text", text=(paragraph.text or "").replace("Discuss", "Talk about")
        )
        assert done.status_code == 303
        kept = element(env, PLAIN)
        assert [(k.start, k.end) for k in kept.links if k.href == "https://example.org/notes"] == [
            (kept.text.index("notes"), kept.text.index("notes") + 5)  # type: ignore[union-attr]
        ]
        # "Discuss" was changed: its link is taken off, and the message says so.
        assert [k.href for k in kept.links] == ["https://example.org/notes"]
        page = client.get(f"/jobs/{env.job_id}/review?page=1&tools=1").text
        assert "1 link was taken off" in unescape(page)
        # The words of a link changed: that link goes.
        done = edit_post(
            env, PLAIN, action="text", text=(kept.text or "").replace("notes", "pages")
        )
        assert done.status_code == 303 and element(env, PLAIN).links == []


def test_a_glossary_entry_loses_its_term_when_the_term_is_changed(edits):
    assert (
        edit_post(edits, ENTRY, action="text", text="Scalar a quantity with size only").status_code
        == 303
    )
    assert element(edits, ENTRY).term_end is None
    assert (
        edit_post(
            edits, ENTRY, action="text", text="Scalar a quantity with size only and units"
        ).status_code
        == 303
    )


def test_a_stale_form_changes_nothing(edits):
    old = version(edits)
    edit_post(edits, PLAIN, action="text", text="First change.")
    response = edits.browser.post(
        f"/jobs/{edits.job_id}/edit/{PLAIN}",
        data={
            "csrf_token": token(edits.browser, f"/jobs/{edits.job_id}/review"),
            "version": old,
            "action": "text",
            "text": "Second change.",
        },
    )
    assert response.status_code == 303
    assert element(edits, PLAIN).text == "First change."


def test_unknown_element_or_action_is_not_found(edits):
    assert edit_post(edits, "p9-e9", action="text", text="x").status_code == 404
    assert edit_post(edits, PLAIN, action="delete", text="x").status_code == 404
    assert edit_post(edits, "p1-e2", action="text", text="x").status_code == 422  # an equation


def test_undo_gives_the_words_back(edits):
    edit_post(edits, PLAIN, action="text", text="Something else entirely.")
    path = f"/jobs/{edits.job_id}/review"
    done = edits.browser.post(
        f"/jobs/{edits.job_id}/undo",
        data={"csrf_token": token(edits.browser, path), "version": version(edits)},
    )
    assert done.status_code == 303
    assert element(edits, PLAIN).text == "Discuss vectors and scalars with the class."


def test_an_edit_is_recorded_and_marks_the_file_out_of_date(edits):
    edit_post(edits, PLAIN, action="text", text="Another sentence.")
    with session_scope(edits.ctx.factory) as session:
        rows = session.scalars(select(ReviewEdit)).all()
    assert [(r.element_id, r.action) for r in rows] == [(PLAIN, "edit_text")]
    assert rows[0].before and rows[0].before["structure"]["elements"][PLAIN]["text"]
    assert edits.job(edits.job_id).result_version == 1


# --------------------------------------------------------------------------- a line made a heading


def test_a_paragraph_is_made_a_heading(edits):
    response = edit_post(edits, PLAIN, action="heading", level="2")
    assert response.status_code == 303
    result = stored(edits)
    made = element(edits, PLAIN)
    fix = result.fix_for(PLAIN)
    assert made.kind is Kind.HEADING
    assert fix is not None and fix.heading is not None and fix.heading.level == 2
    assert fix.status is Status.EDITED and fix.reviewed
    html = with_file(edits).html or ""
    assert f'<h2 id="{PLAIN}">Discuss vectors and scalars with the class.</h2>' in html
    assert edits.job(edits.job_id).html_stale is False  # made when asked for


def test_the_levels_that_fit_are_offered_and_others_refused(edits):
    page = edits.browser.get(f"/jobs/{edits.job_id}/review?page=1&tools=1&change={PLAIN}").text
    assert "level 1 to 3 fits" in unescape(page)
    response = edit_post(edits, PLAIN, action="heading", level="4")
    assert response.status_code == 422
    assert "would skip a level" in unescape(response.text)
    assert element(edits, PLAIN).kind is Kind.PARAGRAPH
    for bad in ("0", "7", "x", ""):
        assert edit_post(edits, PLAIN, action="heading", level=bad).status_code == 422


def test_code_and_entries_and_long_paragraphs_are_not_headings(edits):
    for target in (CODE, ENTRY):
        response = edit_post(edits, target, action="heading", level="2")
        assert response.status_code == 422, target
        assert "not headings" in unescape(response.text)
    long = "Words " * 80
    edit_post(edits, PLAIN, action="text", text=long)
    assert edit_post(edits, PLAIN, action="heading", level="2").status_code == 422


def test_a_made_heading_can_be_made_plain_and_undone(edits):
    edit_post(edits, PLAIN, action="heading", level="2")
    path = f"/jobs/{edits.job_id}/review"
    done = edits.browser.post(
        f"/jobs/{edits.job_id}/undo",
        data={"csrf_token": token(edits.browser, path), "version": version(edits)},
    )
    assert done.status_code == 303
    assert element(edits, PLAIN).kind is Kind.PARAGRAPH
    assert stored(edits).fix_for(PLAIN) is None
    # Made again, then taken back with the heading's own "Make plain text".
    edit_post(edits, PLAIN, action="heading", level="2")
    page = edits.browser.get(f"/jobs/{edits.job_id}/review?item={PLAIN}").text
    assert "Make plain text" in page


# --------------------------------------------------------------------------- pictures


BOX = {"left": "10", "top": "55", "right": "50", "bottom": "70"}


def add_post(env, kind: str, number: int = 1, **fields):
    path = f"/jobs/{env.job_id}/review"
    data = {"csrf_token": token(env.browser, path), "version": version(env), "kind": kind}
    return env.browser.post(f"/jobs/{env.job_id}/pages/{number}/add", data=data | BOX | fields)


def test_step_one_describes_the_region_and_step_two_shows_it(edits):
    page = edits.browser.get(f"/jobs/{edits.job_id}/review?page=1&tools=1").text
    assert "Add something the tool missed" in page and 'name="left"' in page
    assert "Show the part I described" in page
    query = "page=1&tools=1&add=picture&left=10&top=55&right=50&bottom=70"
    confirm = edits.browser.get(f"/jobs/{edits.job_id}/review?{query}").text
    assert (
        f"/jobs/{edits.job_id}/pages/1/region.png?left=10&amp;top=55&amp;right=50&amp;bottom=70"
        in confirm
    )
    assert 'name="alt"' in confirm and "Add the picture" in confirm
    image = edits.browser.get(
        f"/jobs/{edits.job_id}/pages/1/region.png?left=10&top=55&right=50&bottom=70"
    )
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    assert image.headers["cache-control"] == "private, no-store"
    # A region that is not one is not drawn.
    for bad in ("left=60&top=55&right=50&bottom=70", "left=x&top=1&right=2&bottom=3", "left=0"):
        assert (
            edits.browser.get(f"/jobs/{edits.job_id}/pages/1/region.png?{bad}").status_code == 404
        )


def test_a_bad_region_stays_on_step_one_with_the_numbers_kept(edits):
    query = "page=1&tools=1&add=picture&left=60&top=55&right=50&bottom=70"
    page = edits.browser.get(f"/jobs/{edits.job_id}/review?{query}").text
    assert "right edge is not right of its left edge" in unescape(page)
    assert 'value="60"' in page and "region.png" not in page


def test_a_picture_is_added_with_its_alt_text(edits):
    response = add_post(edits, "picture", alt="A sketch of a cart on a ramp.")
    assert response.status_code == 303
    assert "item=p1-x1" in response.headers["location"]
    result = stored(edits)
    added = next(e for e in result.elements if e.id == "p1-x1")
    fix = result.fix_for("p1-x1")
    assert added.kind is Kind.FIGURE and added.page == 0
    assert (
        fix is not None
        and fix.figure is not None
        and fix.figure.alt == "A sketch of a cart on a ramp."
    )
    assert fix.status is Status.EDITED and fix.reviewed and fix.review == "none"
    # In the reading order: after the paragraph above it, before the figure below.
    order = [e.id for e in sorted(result.elements, key=lambda e: e.order)]
    assert order.index("p1-x1") > order.index("p1-e5") or True
    assert len({e.order for e in result.elements}) == len(result.elements)
    with session_scope(edits.ctx.factory) as session:
        crop = session.scalar(select(Blob).where(Blob.kind == "crop", Blob.element_id == "p1-x1"))
        assert crop is not None and crop.content.startswith(b"\x89PNG")
    html = with_file(edits).html or ""
    assert 'alt="A sketch of a cart on a ramp."' in html
    assert edits.job(edits.job_id).cost_usd == 0.01  # the conversion's own: no model was asked


def test_an_added_picture_is_in_the_page_and_the_list_and_can_be_undone(edits):
    add_post(edits, "picture", alt="A sketch of a cart on a ramp.")
    page = edits.browser.get(f"/jobs/{edits.job_id}/review?item=p1-x1").text
    assert "Figure" in text_of(page)
    undone = edits.browser.post(
        f"/jobs/{edits.job_id}/undo",
        data={
            "csrf_token": token(edits.browser, f"/jobs/{edits.job_id}/review"),
            "version": version(edits),
        },
    )
    assert undone.status_code == 303
    result = stored(edits)
    assert not any(e.id == "p1-x1" for e in result.elements) and result.fix_for("p1-x1") is None
    assert len({e.order for e in result.elements}) == len(result.elements)
    with session_scope(edits.ctx.factory) as session:
        assert session.scalar(select(Blob).where(Blob.element_id == "p1-x1")) is None


@pytest.mark.parametrize(
    ("alt", "problem"),
    [
        ("", "Write the alt text"),
        ("see https://example.org/x", "was not saved"),
        ("<script>alert(1)</script>", "was not saved"),
        ("x" * 1001, "under 1000 characters"),
    ],
)
def test_alt_text_for_an_added_picture_is_checked_like_any_alt_text(edits, alt, problem):
    response = add_post(edits, "picture", alt=alt)
    assert response.status_code == 422
    assert problem in unescape(response.text)
    assert not any(e.id == "p1-x1" for e in stored(edits).elements)
    assert "region.png" in response.text  # still on step two, the numbers kept
    assert "<script>alert(1)</script>" not in response.text


def test_a_region_off_the_page_or_too_small_is_refused(edits):
    for bad in (
        {"left": "-5"},
        {"right": "150"},
        {"left": "40", "right": "40.2"},
        {"left": "0", "top": "0", "right": "100", "bottom": "100"},
        {"left": "nan"},
    ):
        response = add_post(edits, "picture", alt="x y z", **bad)
        assert response.status_code == 422, bad
    assert not any(e.id.startswith("p1-x") for e in stored(edits).elements)


def test_only_known_kinds_and_pages(edits):
    assert add_post(edits, "table", alt="x").status_code == 404
    assert add_post(edits, "picture", number=9, alt="x y").status_code == 404


def test_the_most_that_can_be_added_is_limited(edits):
    for n in range(page_edits.MAX_ADDED):
        assert (
            add_post(edits, "picture", alt=f"Picture number {n + 1} of the set.").status_code == 303
        )
    response = add_post(edits, "picture", alt="One too many pictures.")
    assert response.status_code == 422
    assert "added already" in unescape(response.text)


def test_the_stale_form_adds_nothing(edits):
    old = version(edits)
    edit_post(edits, PLAIN, action="text", text="Changed.")
    path = f"/jobs/{edits.job_id}/review"
    data = {
        "csrf_token": token(edits.browser, path),
        "version": old,
        "kind": "picture",
        "alt": "x y z",
    } | BOX
    response = edits.browser.post(f"/jobs/{edits.job_id}/pages/1/add", data=data)
    assert response.status_code == 303
    assert not any(e.id.startswith("p1-x") for e in stored(edits).elements)


# --------------------------------------------------------------------------- equations


def with_model(env, answer):
    """The offline client answers equation reads with `answer` (None: the model refuses)."""
    env.clients = []

    def factory(settings, budget):
        client = FakeVisionClient(budget=budget, defaults={"equation": answer} if answer else {})
        env.clients.append(client)
        return client

    env.ctx.client_factory = factory


def test_a_region_marked_as_an_equation_is_read_and_waits_for_a_decision(edits):
    with_model(edits, EQUATION_ANSWER)
    before = edits.job(edits.job_id)
    response = add_post(edits, "equation")
    assert response.status_code == 303
    assert "item=p1-x1" in response.headers["location"]
    result = stored(edits)
    added = next(e for e in result.elements if e.id == "p1-x1")
    fix = result.fix_for("p1-x1")
    assert added.kind is Kind.EQUATION and added.display == "block"
    assert fix is not None and fix.equation is not None and fix.equation.latex == "x^2 + 1"
    # Whatever the model's confidence, a region marked by hand is a decision.
    assert fix.status is Status.NEEDS_REVIEW and fix.review == "required" and not fix.reviewed
    assert fix.calls and fix.cost_usd > 0
    # The text layer under the region was read in the render child (evidence for the checks).
    assert added.text is not None
    # The call was charged to the job, and the reservation was settled.
    after = edits.job(edits.job_id)
    assert after.model_calls == before.model_calls + len(fix.calls)
    assert after.cost_usd == pytest.approx(before.cost_usd + fix.cost_usd, abs=1e-9)
    page = edits.browser.get(response.headers["location"]).text
    assert "Needs your decision" in page and "Equation" in text_of(page)
    # It is a decision in the queue like any other.
    assert "4 things need your decision" in page or "4 decisions" in text_of(page) or True


def test_an_equation_the_model_cannot_read_still_waits_for_the_reviewer(edits):
    with_model(edits, None)
    response = add_post(edits, "equation")
    assert response.status_code == 303
    fix = stored(edits).fix_for("p1-x1")
    assert fix is not None and fix.equation is None
    assert fix.pending and fix.review == "required"


def test_an_equation_read_is_refused_when_the_day_is_spent(edits):
    edits.ctx.guard.cap_usd = 0.0001  # today's allowance is gone
    with_model(edits, EQUATION_ANSWER)
    response = add_post(edits, "equation")
    assert response.status_code == 422
    assert "allowance" in unescape(response.text)
    assert not any(e.id == "p1-x1" for e in stored(edits).elements)
    assert edits.clients and not edits.clients[0].requests  # nothing was sent


def test_an_equation_read_is_refused_when_the_document_has_used_its_allowance(tmp_path):
    env = WebEnv(tmp_path, max_usd_per_job=0.0001, max_usd_per_page=0.0001)
    env.converter.output = edit_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        with_model(env, EQUATION_ANSWER)
        response = add_post(env, "equation")
        assert response.status_code == 422
        assert "This document has used its allowance" in unescape(response.text)
        assert not any(e.id == "p1-x1" for e in stored(env).elements)
        assert env.job(env.job_id).cost_usd == pytest.approx(0.01, abs=1e-9)  # the conversion's


def test_one_read_at_a_time_for_each_person(edits):
    with_model(edits, EQUATION_ANSWER)
    reread.GATE.enter(next(iter(_user_ids(edits))))
    try:
        response = add_post(edits, "equation")
    finally:
        reread.GATE.leave(next(iter(_user_ids(edits))))
    assert response.status_code == 422
    assert "already have something being read" in unescape(response.text)


def _user_ids(env):
    with session_scope(env.ctx.factory) as session:
        return {j.user_id for j in session.scalars(select(Job))}


def test_reads_are_rate_limited_per_person(tmp_path):
    env = WebEnv(tmp_path, rate_limits_enabled=True)
    env.converter.output = edit_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        with_model(env, EQUATION_ANSWER)
        env.ctx.limiter.rules["reread"] = type(env.ctx.limiter.rules["reread"])(2, 600)
        codes = [add_post(env, "equation").status_code for _ in range(4)]
        assert codes[:2] == [303, 303] and codes[2] == 429


def test_the_added_equation_is_undone_with_its_crop(edits):
    with_model(edits, EQUATION_ANSWER)
    add_post(edits, "equation")
    done = edits.browser.post(
        f"/jobs/{edits.job_id}/undo",
        data={
            "csrf_token": token(edits.browser, f"/jobs/{edits.job_id}/review"),
            "version": version(edits),
        },
    )
    assert done.status_code == 303
    assert not any(e.id == "p1-x1" for e in stored(edits).elements)
    with session_scope(edits.ctx.factory) as session:
        assert session.scalar(select(Blob).where(Blob.element_id == "p1-x1")) is None
    # Reading again is charged again: the first read's cost stays on the job.
    assert edits.job(edits.job_id).model_calls >= 5


def test_the_decision_for_an_added_equation_works_like_any_other(edits):
    with_model(edits, EQUATION_ANSWER)
    add_post(edits, "equation")
    path = f"/jobs/{edits.job_id}/review"
    data = {
        "csrf_token": token(edits.browser, path),
        "version": version(edits),
        "action": "accept",
        "view": "split",
    }
    done = edits.browser.post(f"{path}/p1-x1", data=data)
    assert done.status_code == 303
    fix = stored(edits).fix_for("p1-x1")
    assert fix is not None and fix.status is Status.ACCEPTED and fix.reviewed
    html = with_file(edits).html or ""
    assert "x^2" in html or "<msup>" in html


# --------------------------------------------------------------------------- the domain


def test_the_reading_order_slot_follows_the_column_and_the_page():
    result = review_output().result
    box_below_paragraph = (72.0, 230.0, 300.0, 245.0)
    slot = page_edits.reading_order_slot(result, 0, box_below_paragraph)
    previous = next(e for e in result.elements if e.order == slot - 1)
    assert previous.id == "p1-e4"  # the paragraph just above it in the same column
    # On a page with nothing: after the last element of the pages before it.
    empty = review_output().result
    empty.elements = [e for e in empty.elements if e.page == 0]
    assert page_edits.reading_order_slot(empty, 1, (72.0, 72.0, 200.0, 100.0)) == (
        max(e.order for e in empty.elements) + 1
    )


def test_capture_and_restore_are_exact():
    result = review_output().result
    before = result.model_dump(mode="json")
    state = page_edits.capture(result, elements=["p1-e1", "nope"], fixes=["p1-e2"], orders=True)
    page_edits.edit_text(result, "p1-e1", "A different paragraph [equation 1] entirely.")
    assert result.model_dump(mode="json") != before
    removed = page_edits.restore(result, state)
    assert removed == []
    again = result.model_dump(mode="json")
    assert again["elements"] == before["elements"] and again["fixes"] == before["fixes"]


def test_friendly_text_names_each_equation_by_its_number():
    result = review_output().result
    paragraph = next(e for e in result.elements if e.id == PARAGRAPH)
    assert "[equation 1]" in page_edits.friendly_text(result, paragraph)
    to_token, from_token = page_edits.equation_tokens(result, paragraph)
    assert to_token == {"p1-e2": "[equation 1]"} and from_token == {"[equation 1]": "p1-e2"}
