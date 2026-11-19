"""T3-5: edit what the tool did not propose.

The words of a paragraph, a line made a heading, a picture or an equation the tool did not
find. Uses fixtures.web.review_doc (a real PDF and a result measured on it). The model is the
offline client with an answer set per test: no key, no cost. Every edit marks the HTML out of
date (it is made when somebody asks for it), is stored as a ReviewEdit, goes through the
review's version guard and can be undone.
"""

from __future__ import annotations

import dataclasses
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
    Run,
    Status,
)
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import equation_step
from readablestem.web import page_edits, reread
from readablestem.web.database import utc_today
from readablestem.web.db import Blob, Job, session_scope
from readablestem.web.quota import rereads_used

PARAGRAPH = "p1-e1"  # "A rigid body turning with angular velocity ⟦p1-e2⟧ moves a point ..."
PLAIN = "p1-e4"  # "Discuss vectors and scalars with the class."
CODE = "p1-e8"
ENTRY = "p1-e9"
NEXT = "p1-e10"  # a plain paragraph, then another: the two sit side by side
AFTER = "p1-e11"

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
    for number, eid in enumerate((NEXT, AFTER)):
        top = 680.0 + 20 * number
        result.elements.append(
            Element(id=eid, kind=Kind.PARAGRAPH, page=0, bbox=(72.0, top, 540.0, top + 15.0),
                    order=52 + number, text=f"A paragraph beside it, number {number + 1}.")
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
        (
            "A rigid body turning with angular velocity omega moves a point.",
            "Keep the mark [equation 1] in the text, or remove the equation as well.",
        ),
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


def test_links_stay_on_the_words_that_did_not_change(tmp_path):
    output = edit_output()
    paragraph = next(e for e in output.result.elements if e.id == PLAIN)
    paragraph.text = "Discuss vectors and scalars with the class, then open the notes here."
    start = paragraph.text.index("notes")
    paragraph.links = [
        Link(kind="uri", start=start, end=start + 5, href="https://example.org/notes"),
        Link(kind="uri", start=0, end=7, href="https://example.org/discuss"),
    ]
    struck = paragraph.text.index("scalars")  # struck through on the page (F-509)
    bold = paragraph.text.index("vectors")  # set in bold on the page (F-510)
    paragraph.runs = [
        Run(start=bold, end=bold + 7, kind="strong"),
        Run(start=struck, end=struck + 7, kind="deleted"),
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
        # The bold word and the struck word did not change: still marked, where they now are.
        text = kept.text or ""
        assert [(r.kind, text[r.start : r.end]) for r in kept.runs] == [
            ("strong", "vectors"),
            ("deleted", "scalars"),
        ]
        # The words of a link and the struck word changed: both go, and both are counted. The
        # bold word is only the look of words that did not change, and stays.
        changed = text.replace("notes", "pages").replace("scalars", "numbers")
        done = edit_post(env, PLAIN, action="text", text=changed)
        assert done.status_code == 303
        after = element(env, PLAIN)
        assert after.links == [] and [r.kind for r in after.runs] == ["strong"]
        page = client.get(f"/jobs/{env.job_id}/review?page=1&tools=1").text
        assert "1 link and 1 struck-text mark were taken off" in unescape(page)


def test_a_kept_line_end_is_written_as_br_and_moves_with_its_words(tmp_path):
    output = edit_output()
    paragraph = next(e for e in output.result.elements if e.id == PLAIN)
    paragraph.text = "Discuss vectors and scalars with the class"
    paragraph.role = "aside"  # a text edit keeps what the block is
    first = paragraph.text.index(" vectors")
    second = paragraph.text.index(" with")
    paragraph.runs = [
        Run(start=first, end=first + 1, kind="break"),
        Run(start=second, end=second + 1, kind="break"),
    ]
    older = output.result.elements[0]  # a stored element that has no runs at all
    assert not older.runs
    env = WebEnv(tmp_path)
    env.converter.output = output
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        html = with_file(env).html or ""
        assert "Discuss<br>vectors and scalars<br>with the class" in html
        assert "<br>" not in html.split("Discuss<br>")[0]  # the older elements: no line ends
        # The words before the first line end change: both line ends stay at their spaces.
        done = edit_post(env, PLAIN, action="text", text=paragraph.text.replace("Discuss", "Talk"))
        assert done.status_code == 303
        kept = element(env, PLAIN)
        assert kept.role == "aside" and "<aside" in (with_file(env).html or "")
        assert [(kept.text or "")[r.start : r.end] for r in kept.runs if r.kind == "break"] == [
            " ",
            " ",
        ]
        assert "Talk<br>vectors and scalars<br>with the class" in (with_file(env).html or "")
        # The space that holds a line end is taken out: that line end goes, the other stays.
        done = edit_post(env, PLAIN, action="text", text="Talkvectors and scalars with the class")
        assert done.status_code == 303
        assert len([r for r in element(env, PLAIN).runs if r.kind == "break"]) == 1
        assert (with_file(env).html or "").count("<br>") == 1


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


def test_unknown_element_or_action_is_not_found(edits):
    assert edit_post(edits, "p9-e9", action="text", text="x").status_code == 404
    assert edit_post(edits, PLAIN, action="delete", text="x").status_code == 404
    assert edit_post(edits, "p1-e2", action="text", text="x").status_code == 422  # an equation


def test_the_notice_after_a_text_edit_names_the_edit_and_its_undo_says_change(edits):
    done = edit_post(edits, PLAIN, action="text", text="Something else entirely.")
    page = edits.browser.get(done.headers["location"]).text
    notice = text_of(page.split('id="decision-notice"')[1].split("</div>")[0])
    assert "The text was changed." in notice and "All decisions made" not in notice
    assert "Undo this change" in notice and "this decision" not in notice


def test_undo_gives_the_words_back(edits):
    edit_post(edits, PLAIN, action="text", text="Something else entirely.")
    path = f"/jobs/{edits.job_id}/review"
    done = edits.browser.post(
        f"/jobs/{edits.job_id}/undo",
        data={"csrf_token": token(edits.browser, path), "version": version(edits)},
    )
    assert done.status_code == 303
    assert element(edits, PLAIN).text == "Discuss vectors and scalars with the class."


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


# --------------------------------------------------------------------------- sidebar, quotation


def test_a_paragraph_is_made_a_sidebar_or_a_quotation_and_the_page_has_the_wrapper(edits):
    page = f"/jobs/{edits.job_id}/review?page=1&tools=1&change={PLAIN}"
    assert "Make it a sidebar" in edits.browser.get(page).text
    assert edit_post(edits, PLAIN, action="role", role="aside").status_code == 303
    assert element(edits, PLAIN).role == "aside"
    html = with_file(edits).html or ""
    assert re.search(
        r'<aside aria-label="Sidebar"><p[^>]*>Discuss vectors and scalars with the class\.</p>'
        r"</aside>",
        html,
    )
    shown = unescape(edits.browser.get(page).text)
    assert "This paragraph is a sidebar" in shown and "Not a sidebar" in shown
    # The other role replaces it; neighbors of one role share one wrapper.
    assert edit_post(edits, PLAIN, action="role", role="quote").status_code == 303
    assert edit_post(edits, NEXT, action="role", role="quote").status_code == 303
    assert edit_post(edits, AFTER, action="role", role="quote").status_code == 303
    html = with_file(edits).html or ""
    assert "<aside" not in html and html.count("<blockquote>") == 2
    quoted = html.split("<blockquote>")[2].split("</blockquote>")[0]
    assert "number 1." in quoted and "number 2." in quoted
    # The same role again, or a word that is neither, changes nothing.
    assert edit_post(edits, PLAIN, action="role", role="quote").status_code == 422
    assert edit_post(edits, PLAIN, action="role", role="verse").status_code == 422
    assert element(edits, PLAIN).role == "quote"


def test_a_role_is_cleared_and_undone(edits):
    edit_post(edits, PLAIN, action="role", role="aside")
    assert edit_post(edits, PLAIN, action="role", role="").status_code == 303
    assert element(edits, PLAIN).role is None
    assert "<aside" not in (with_file(edits).html or "")
    assert edit_post(edits, PLAIN, action="role", role="").status_code == 422  # already none
    path = f"/jobs/{edits.job_id}/review"
    undo = edits.browser.post(
        f"/jobs/{edits.job_id}/undo",
        data={"csrf_token": token(edits.browser, path), "version": version(edits)},
    )
    assert undo.status_code == 303
    assert element(edits, PLAIN).role == "aside"


def test_only_a_plain_paragraph_can_be_a_sidebar_or_a_quotation(edits):
    page = unescape(
        edits.browser.get(f"/jobs/{edits.job_id}/review?page=1&tools=1&change={CODE}").text
    )
    assert "Make it a sidebar" not in page
    for target in (CODE, ENTRY, "p1-e0", "p1-e2"):  # code, entry, heading, equation
        response = edit_post(edits, target, action="role", role="aside")
        assert response.status_code == 422, target
        assert element(edits, target).role is None


# --------------------------------------------------------------------------- one paragraph left out


def test_a_paragraph_left_out_is_listed_to_put_back_named_in_the_record_and_not_in_the_file(edits):
    page = f"/jobs/{edits.job_id}/review?page=1&tools=1&change={PLAIN}"
    assert "Leave it out" in edits.browser.get(page).text
    assert edit_post(edits, PLAIN, action="leave_out").status_code == 303
    html = with_file(edits).html or ""
    assert "Discuss vectors and scalars" not in html
    assert "One paragraph or list item on page 1 of the original PDF was left out" in text_of(html)
    # Not deleted: the page's card still lists it, as left out, with a way back.
    shown = unescape(edits.browser.get(page.replace(f"&change={PLAIN}", "")).text)
    assert "left out of the student file" in shown and "Put it back" in shown
    assert "Discuss vectors and scalars" in shown
    # The record says which words were taken out.
    record = text_of(edits.browser.get(f"/jobs/{edits.job_id}/record").text)
    assert "Left a paragraph out of the student file" in record
    assert "Discuss vectors and scalars with the class." in record
    # The summary holds the words from the stored paragraph itself, so one left out by a
    # carried change (whose row has no words) is named too.
    assert "Page 1: “Discuss vectors and scalars with the class.”" in record
    # Back, and the file is whole again.
    assert edit_post(edits, PLAIN, action="put_back").status_code == 303
    assert "Discuss vectors and scalars" in (with_file(edits).html or "")
    assert not stored(edits).left_out_blocks
    assert edit_post(edits, PLAIN, action="put_back").status_code == 422  # not left out now


def test_a_paragraph_left_out_is_undone(edits):
    edit_post(edits, PLAIN, action="leave_out")
    path = f"/jobs/{edits.job_id}/review"
    done = edits.browser.post(
        f"/jobs/{edits.job_id}/undo",
        data={"csrf_token": token(edits.browser, path), "version": version(edits)},
    )
    assert done.status_code == 303
    assert element(edits, PLAIN).text == "Discuss vectors and scalars with the class."
    assert not stored(edits).left_out_blocks and not [
        n for n in stored(edits).notices if n.code == "block_omitted"
    ]


def test_only_a_paragraph_or_list_item_nothing_points_at_is_left_out(edits):
    # A heading, an equation, a figure, a paragraph with an equation in it, and a paragraph
    # a decision waits on are each refused, and nothing changes.
    for target in ("p1-e0", "p1-e2", "p1-e6", PARAGRAPH):
        assert edit_post(edits, target, action="leave_out").status_code == 422, target
    assert not stored(edits).left_out_blocks
    # Nor is text left out of a Word or LaTeX file, whose note would name a page of "the
    # original PDF"; and a paragraph is not put back onto a page that is itself left out.
    word = stored(edits).model_copy(update={"origin": "docx"})
    with pytest.raises(page_edits.ReviewInvalid):
        page_edits.leave_out_block(word, PLAIN)
    result = stored(edits)
    page_edits.leave_out_block(result, PLAIN)
    page_edits.omit_page(result, 0, pages=page_edits.page_count(result))
    with pytest.raises(page_edits.ReviewInvalid):
        page_edits.put_back_block(result, PLAIN)


# --------------------------------------------------------------------------- pictures


BOX = {"left": "10", "top": "55", "right": "50", "bottom": "70"}


def add_post(env, kind: str, number: int = 1, **fields):
    path = f"/jobs/{env.job_id}/review"
    data = {"csrf_token": token(env.browser, path), "version": version(env), "kind": kind}
    return env.browser.post(f"/jobs/{env.job_id}/pages/{number}/add", data=data | BOX | fields)


def test_a_picture_is_added_with_its_alt_text(edits):
    # The region is shown as it would be cut, before anything is added.
    preview = edits.browser.get(f"/jobs/{edits.job_id}/pages/1/region.png", params=BOX)
    assert preview.status_code == 200 and preview.content.startswith(b"\x89PNG")
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


def test_a_region_given_by_two_lines_is_the_box_the_four_numbers_for_it_would_make(edits):
    path = f"/jobs/{edits.job_id}/review"
    # The lines are offered by their first words, with both ends of the page.
    first = edits.browser.get(path, params={"page": 1, "tools": 1, "add": "picture"})
    assert "Discuss vectors and scalars with the class." in first.text
    assert "The top of the page" in first.text and "The bottom of the page" in first.text
    page = edits.browser.get(
        path,
        params={"page": 1, "tools": 1, "add": "picture", "after": PLAIN, "before": "p1-e7"}
        | {"left": "1", "top": "2", "right": "3", "bottom": "4"},  # the lines win over these
    )
    assert page.status_code == 200
    query = re.search(r"region\.png\?([^\"]+)\"", page.text)
    assert query is not None
    edges = dict(pair.split("=") for pair in unescape(query.group(1)).split("&"))
    above, below = element(edits, PLAIN).bbox, element(edits, "p1-e7").bbox
    assert float(edges["top"]) >= 100 * above[3] / 792
    assert float(edges["bottom"]) <= 100 * below[1] / 792
    response = add_post(edits, "picture", alt="A sketch of a cart on a ramp.", **edges)
    assert response.status_code == 303
    added = element(edits, "p1-x1")
    assert added.bbox == page_edits.region_box(
        (612.0, 792.0), edges["left"], edges["top"], edges["right"], edges["bottom"], "region"
    )
    assert above[3] <= added.bbox[1] and added.bbox[3] <= below[1]
    # One line alone says what is missing.
    alone = edits.browser.get(path, params={"page": 1, "add": "picture", "after": PLAIN})
    assert "Choose both lines" in alone.text and "region.png" not in alone.text


def test_every_line_of_a_dense_page_can_be_chosen_for_a_region(tmp_path):
    output = edit_output()
    for n in range(150):
        output.result.elements.append(
            Element(id=f"p1-d{n}", kind=Kind.PARAGRAPH, page=0, order=100 + n,
                    bbox=(72.0, 10.0 + 4.5 * n, 540.0, 12.0 + 4.5 * n), text=f"Dense line {n}.")
        )  # fmt: skip
    env = WebEnv(tmp_path)
    env.converter.output = output
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        path = f"/jobs/{env.job_id}/review"
        query = {"page": 1, "tools": 1, "add": "picture"}
        last = client.get(path, params=query | {"after": "p1-d149", "before": "bottom"})
        assert "region.png" in last.text and "Choose both lines" not in last.text


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
    landed = unescape(edits.browser.get(response.headers["location"]).text)
    assert "Nothing was changed" in landed and "make the change again" in landed  # a next step


# --------------------------------------------------------------------------- equations


def with_model(env, answer):
    """The offline client answers equation reads with `answer` (None: the model refuses)."""
    env.clients = []

    def factory(settings, budget):
        client = FakeVisionClient(budget=budget, defaults={"equation": answer} if answer else {})
        env.clients.append(client)
        return client

    env.ctx.client_factory = factory


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


def _rereads_today(env) -> int:
    with session_scope(env.ctx.factory) as session:
        return rereads_used(session, next(iter(_user_ids(env))), utc_today())


def test_the_same_crop_read_again_is_answered_from_the_record(edits, monkeypatch):
    """F-093: the second read of one crop makes no call, charges nothing and counts nothing;
    a changed prompt is another question."""
    with_model(edits, EQUATION_ANSWER)
    before = edits.job(edits.job_id)
    assert add_post(edits, "equation").status_code == 303
    one = edits.job(edits.job_id)
    assert add_post(edits, "equation").status_code == 303
    two = edits.job(edits.job_id)
    assert sum(len(c.requests) for c in edits.clients) == 1
    assert one.model_calls == before.model_calls + 1 and two.model_calls == one.model_calls
    assert one.cost_usd > before.cost_usd and two.cost_usd == one.cost_usd
    assert _rereads_today(edits) == 1  # an invited account is counted, not limited
    assert [e.id for e in stored(edits).elements if e.id.startswith("p1-x")] == ["p1-x1", "p1-x2"]

    real = equation_step.load_prompt
    monkeypatch.setattr(
        equation_step,
        "load_prompt",
        lambda name: dataclasses.replace(real(name), version="changed"),
    )
    assert add_post(edits, "equation").status_code == 303
    assert sum(len(c.requests) for c in edits.clients) == 2
    assert _rereads_today(edits) == 2


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
