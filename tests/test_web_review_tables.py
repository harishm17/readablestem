"""Tables on the review screens: the table card, its decisions and edits, and the guard
against a stale tab.

Uses fixtures.web.table_doc (a PDF with ruled tables and a result with a table fix in
every review tier) and the real serialiser, assembler and page renderer. Decisions:
1 table p1-e1 (a model proposal a check doubts), 2 table p1-e3 (kept as an image, a grid
read from the PDF to use instead), 3 table p2-e0 (kept as an image, nothing else); an
optional note, table p2-e1; accepted, the wide table p2-e2.

The rules of an edit (headers, cells, the grid read from the PDF) are tested on their
own at the end; axe, reflow and the keyboard in a browser are in
test_web_review_tables_browser.py.
"""

from __future__ import annotations

import logging
import re
from html import unescape

import pytest
from sqlalchemy import select

from a11y_stem.core.types import (
    Check,
    DocumentResult,
    DocumentStats,
    Element,
    Fix,
    Kind,
    Status,
    TableCell,
    TableFix,
    TableGrid,
    grid_problems,
)
from a11y_stem.observability.logging import FIELDS_ATTR
from a11y_stem.pipeline import table_step
from a11y_stem.pipeline.table_html import render_table
from a11y_stem.web import queue
from a11y_stem.web.db import ReviewEdit, session_scope
from a11y_stem.web.messages import CHECK_MESSAGES
from a11y_stem.web.review import (
    NO_TABLE_TO_ACCEPT,
    ReviewInvalid,
    apply_review,
    first_col_is_header,
    first_row_is_header,
    usable_local_grid,
    with_header_col,
    with_header_row,
)
from a11y_stem.web.routes.review import STALE_MESSAGE, STALE_TITLE
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload, with_file
from fixtures.web.table_doc import (
    MATHS_LATEX,
    TABLE_2,
    TABLE_4,
    TABLE_5,
    grid_of,
    table_1_local,
    table_1_proposal,
    table_output,
    table_pdf,
)


@pytest.fixture
def tables(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = table_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, table_pdf()))
        env.run_worker()
        yield env


def screen(env, query: str = "", status: int = 200) -> str:
    response = env.browser.get(f"/jobs/{env.job_id}/review{query}")
    assert response.status_code == status, response.status_code
    return response.text


def listing(env) -> str:
    response = env.browser.get(f"/jobs/{env.job_id}/review/list")
    assert response.status_code == 200
    return response.text


def version(env) -> int:
    return env.job(env.job_id).result_version


def post(env, target: str, *, sent_version: str | None = "current", **fields):
    """A decision as the table card sends it: with the job's current `result_version`,
    an older one (`sent_version="0"`), or none (`sent_version=None`)."""
    data = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review")} | fields
    if sent_version == "current":
        data["version"] = str(version(env))
    elif sent_version is not None:
        data["version"] = sent_version
    return env.browser.post(f"/jobs/{env.job_id}/review/{target}", data=data)


def follow(env, response) -> str:
    assert response.status_code == 303, response.status_code
    return env.browser.get(response.headers["location"]).text


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def section(html: str, start: str, end: str) -> str:
    begin = html.index(start)
    return html[begin : html.index(end, begin)]


def panel(html: str) -> str:
    return section(html, 'id="decision"', 'class="optional-area"')


def stored(env) -> DocumentResult:
    return DocumentResult.model_validate(env.job(env.job_id).result_json)


def fix_of(env, element_id: str) -> Fix:
    fix = stored(env).fix_for(element_id)
    assert fix is not None
    return fix


def grid(env, element_id: str) -> TableGrid:
    fix = fix_of(env, element_id)
    assert fix.table is not None
    return fix.table.grid


def edits(env) -> list[ReviewEdit]:
    with session_scope(env.ctx.factory) as session:
        rows = session.scalars(
            select(ReviewEdit).where(ReviewEdit.job_id == env.job_id).order_by(ReviewEdit.id)
        ).all()
        session.expunge_all()
        return list(rows)


def post_forms(html: str) -> list[str]:
    return re.findall(r'<form method="post".*?</form>', html, re.S)


# --------------------------------------------------------------------------- the queue


def test_tables_are_decisions_in_the_queue_after_figures_and_before_headings(tables):
    decisions = queue.decisions(stored(tables))
    assert [(d.number, d.key) for d in decisions] == [(1, "p1-e1"), (2, "p1-e3"), (3, "p2-e0")]
    page = screen(tables)
    lede = text_of(section(page, '<p class="review-lede">', "</p>"))
    assert lede == (
        "3 things need your decision. Everything else was accepted; optional checks are below."
    )
    # Tables are marked on their page like any other item, and named "Table N".
    marks = re.findall(r'<a id="box-([\w-]+)" class="box[^"]*"', page)
    assert marks == ["p1-e1", "p1-e3"]
    assert "1: Table 1, needs your decision (1 of 3)" in text_of(page)
    css = tables.browser.get(f"/jobs/{tables.job_id}/review/marks.css").text
    assert "#box-p1-e1{" in css and "#box-p2-e2{" in css
    # The optional note is listed with the other optional checks.
    optional = text_of(section(page, '<details class="optional-box"', "</details>"))
    assert "Table 4, page 2" in optional


def test_the_region_image_of_a_table_is_its_crop_from_the_upload(tables):
    response = tables.browser.get(f"/jobs/{tables.job_id}/regions/p1-e1.png")
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")
    assert response.headers["cache-control"] == "private, no-store"


# --------------------------------------------------------------------------- the card


def test_the_card_shows_the_image_and_the_table_as_the_student_gets_it(tables):
    body = panel(screen(tables))
    assert "Decision 1 of 3" in body and "Table 1, page 1" in text_of(body)
    assert "Needs your decision" in body
    region = f'src="/jobs/{tables.job_id}/regions/p1-e1.png"'
    assert f'{region} alt="Table 1 in the PDF, outlined, with the lines around it."' in body
    # The proposal, written by the output's own serialiser: the same HTML the student gets.
    proposal = render_table(table_1_proposal(), id_prefix="tp-p1-e1", math_display="inline")
    assert proposal in body
    assert "Proposal, as students would get it" in body
    assert 'role="group" aria-label="Proposed table 1"' in body
    assert '<td colspan="2"><math display="inline">' in body  # the maths cell, as MathML
    # What the checks found, in plain English (web.messages; a code without a sentence
    # gets the neutral one), with the code in the folded details.
    findings = text_of(section(body, "table-findings", "</div>"))
    assert "What the checks found" in findings
    assert "compare the proposal with the original closely" in findings
    assert "table.words: words_differ" in section(body, '<details class="tech">', "</details>")
    # Changing it: a cell from the list, and the two header boxes (both unticked here).
    change = section(body, '<details class="edit-box table-edit">', "</details>")
    assert "<summary>Change the table</summary>" in change
    assert '<option value="r3c1">Row 4, columns 2 to 3: v = d / t</option>' in change
    assert '<label for="cell-pick-p1-e1">Cell to change</label>' in change
    assert re.search(r'id="header-row-p1-e1"[^>]*data-edit-field>', change)  # not checked
    assert re.search(r'id="header-col-p1-e1"[^>]*data-edit-field>', change)
    # The grid read from the PDF differs (it has the header row): offered, folded.
    local = section(body, "<summary>The grid read from the PDF</summary>", "</details>")
    assert render_table(table_1_local(), id_prefix="tl-p1-e1", math_display="inline") in local
    assert 'name="change" value="use_local"' in local and "Use this grid instead" in local
    # Accept and Keep as image (Reject, named for what it does), saying what it keeps.
    assert re.search(r'value="accept" class="button" data-role="accept">Accept<', body)
    assert re.search(r'value="reject" class="button-secondary"[^>]*>Keep as image<', body)
    assert "Keep as image: Students see a picture of the table, marked “not converted”" in body
    # Every form that changes something carries the job's version and the view.
    forms = post_forms(body)
    assert len(forms) == 4  # headers, use this grid, accept, reject
    for form in forms:
        assert '<input type="hidden" name="version" value="0">' in form
        assert 'name="view" value="split"' in form
    assert "Save edit" not in body  # nothing like the equation's LaTeX box


def test_the_controls_come_in_reading_order(tables):
    """Keyboard order is DOM order: the table (its box can be scrolled), what the checks
    found, the changes (folded), then Accept and Reject, then the next decision."""
    body = panel(screen(tables))
    order = [
        'aria-label="Proposed table 1"',
        "What the checks found",
        "<summary>Change the table</summary>",
        'id="cell-pick-p1-e1"',
        'id="header-row-p1-e1"',
        'id="header-col-p1-e1"',
        "Save headers",
        "<summary>The grid read from the PDF</summary>",
        "Use this grid instead",
        'value="accept"',
        'value="reject"',
        "<summary>Details for this item</summary>",
        'rel="next"',
    ]
    positions = [body.index(marker) for marker in order]
    assert positions == sorted(positions)


def test_a_table_kept_as_an_image_offers_the_grid_read_from_the_pdf(tables):
    body = panel(screen(tables, "?item=p1-e3"))
    assert "Decision 2 of 3" in body
    assert "This table is kept as an image of the original" in body  # why, in one sentence
    assert "Grid read from the PDF" in body
    assert render_table(grid_of(TABLE_2), id_prefix="tl-p1-e3", math_display="inline") in body
    assert re.search(r'class="button">Use the grid read from the PDF<', body)
    assert re.search(r'value="reject" class="button-secondary"[^>]*>Keep as image<', body)
    assert "Change the table" not in body  # nothing to change until a grid is chosen
    assert 'value="accept"' not in body
    assert "The image stays in the document, marked “not converted”" in body


def test_a_table_without_a_grid_can_only_keep_its_image(tables):
    body = panel(screen(tables, "?item=p2-e0"))
    assert "Decision 3 of 3" in body
    assert "No rebuilt table: the document shows the image of the table" in body
    assert re.search(r'value="reject" class="button"[^>]*>Keep as image<', body)
    assert "use_local" not in body and 'value="accept"' not in body


def test_an_optional_table_and_an_accepted_one_open_as_items(tables):
    body = panel(screen(tables, "?item=p2-e1"))
    assert "Optional check" in body and "Accepted, with a note" in body
    assert "Rebuilt, as students get it" in body
    assert "It was accepted: nothing to do unless you disagree." in body
    wide = panel(screen(tables, "?item=p2-e2"))
    assert "Accepted automatically" in wide
    # Twelve columns: in its own scroll box (it scrolls there, not the page; see the
    # browser tests).
    assert re.search(r'<div class="table-scroll table-view" tabindex="0" role="group"', wide)
    assert render_table(grid_of(TABLE_5, source="model"), id_prefix="tp-p2-e2") in wide
    # The model's table is the grid read from the PDF here (only who built it differs):
    # nothing else to offer.
    for item in (body, wide):
        assert "The grid read from the PDF" not in item and "use_local" not in item


def test_choosing_a_cell_opens_its_form_with_the_text_in_it(tables):
    body = panel(screen(tables, "?item=p1-e1&cell=r1c1"))
    form = section(body, 'class="decide-form table-cell-form', "</form>")
    assert '<label for="cell-p1-e1">Text in row 2, column 2</label>' in form
    assert re.search(
        r'<textarea id="cell-p1-e1" name="cell"[^>]*autofocus[^>]*>10</textarea>', form
    )
    for name, value in (("change", "cell"), ("row", "1"), ("col", "1"), ("action", "edit")):
        assert f'name="{name}" value="{value}"' in form
    assert '<details class="edit-box table-edit" open>' in body
    assert f'<a href="/jobs/{tables.job_id}/review?item=p1-e1">Cancel</a>' in form
    maths = panel(screen(tables, "?item=p1-e1&cell=r3c1"))
    assert "LaTeX in row 4, columns 2 to 3" in maths
    assert "v = \\frac{d}{t}</textarea>" in maths
    # An unknown cell opens nothing (the list again).
    assert 'id="cell-pick-p1-e1"' in panel(screen(tables, "?item=p1-e1&cell=r9c9"))


# --------------------------------------------------------------------------- decisions


def test_accept_moves_on_and_the_document_has_the_table(tables):
    page = follow(tables, post(tables, "p1-e1", action="accept"))
    assert "Decision 2 of 3" in panel(page)
    assert "Table 1: Accepted. 2 decisions left." in page
    fix = fix_of(tables, "p1-e1")
    assert (fix.status, fix.reviewed) == (Status.ACCEPTED, True)
    html = with_file(tables).html
    assert '<div class="table-scroll" id="p1-e1" role="group"' in html
    (edit,) = edits(tables)
    assert edit.action == "accept"
    assert edit.before["table"] == edit.after["table"]  # the proposal, in both snapshots
    assert edit.before["status"] == "needs_review" and edit.after["status"] == "accepted"
    assert version(tables) == 1


def test_reject_keeps_the_image_of_the_table(tables):
    page = follow(tables, post(tables, "p1-e1", action="reject"))
    assert "Table 1: The image of the table is kept in the document, marked “not converted”." in (
        page
    )
    assert fix_of(tables, "p1-e1").status is Status.REJECTED
    html = with_file(tables).html
    flagged = section(html, 'id="p1-e1"', "</div>")
    assert 'alt="Table not converted"' in flagged
    assert "This table is shown as an image of the original" in flagged
    # Decided, it can be opened again: Accept brings the rebuilt table back.
    body = panel(screen(tables, "?item=p1-e1"))
    assert "Decided: Kept as an image by you" in body
    assert "Proposal (the document has the image instead)" in body
    assert 'value="accept"' in body


def test_use_the_grid_read_from_the_pdf_stays_on_the_table(tables):
    response = post(tables, "p1-e3", action="edit", change="use_local")
    assert response.headers["location"] == f"/jobs/{tables.job_id}/review?item=p1-e3"
    page = follow(tables, response)
    notice = section(page, 'id="decision-notice"', "</p>")
    assert (
        "Table 2: The table now uses the grid read from the PDF. Its words are the PDF&#39;s "
        "own, so the checks could not compare them with anything: check it against the "
        "image. 2 decisions left." in notice
    )
    assert "passed the required checks" not in notice
    assert re.search(r'<h2 id="decision-title"[^>]*autofocus', page)
    body = panel(page)
    assert "Decided: Edited by you" in body
    assert "Rebuilt, as students get it" in body
    # The line that said the table is kept as an image no longer applies.
    assert 'class="why"' not in body and "kept as an image" not in body
    # Decided and still open to change; the way on is one link.
    assert "Change the table" in body
    onward = f'href="/jobs/{tables.job_id}/review?item=p2-e0#decision-title">Go on to decision 3<'
    assert onward in body
    fix = fix_of(tables, "p1-e3")
    assert fix.status is Status.EDITED and fix.reviewed
    assert fix.table is not None and fix.table.grid == grid_of(TABLE_2)
    assert fix.reason == "table_not_rebuilt"  # what the pipeline did stays recorded
    html = with_file(tables).html
    assert '<div class="table-scroll" id="p1-e3"' in html and '<th scope="col">Unit</th>' in html


def test_keep_the_image_of_a_table_without_a_grid(tables):
    response = post(tables, "p2-e0", action="reject")
    assert response.headers["location"] == f"/jobs/{tables.job_id}/review?item=p1-e1"
    assert fix_of(tables, "p2-e0").status is Status.REJECTED
    # Every table decided: done.
    post(tables, "p1-e1", action="accept")
    response = post(tables, "p1-e3", action="reject")
    assert response.headers["location"] == f"/jobs/{tables.job_id}/review"
    done = panel(follow(tables, response))
    # Two tables are kept as images: "Ready", with what students get as images.
    assert ">Ready<" in done and "Download the HTML file" in done
    assert "2 tables are shown as an image that screen readers cannot read." in text_of(done)


def test_accept_without_a_rebuilt_table_is_refused(tables):
    response = post(tables, "p1-e3", action="accept")
    assert response.status_code == 422
    assert NO_TABLE_TO_ACCEPT in response.text
    assert fix_of(tables, "p1-e3").status is Status.FAILED
    assert version(tables) == 0


# --------------------------------------------------------------------------- headers


def test_the_first_row_made_a_header_and_back(tables):
    response = post(tables, "p1-e1", action="edit", change="headers", header_row="on")
    assert response.headers["location"] == f"/jobs/{tables.job_id}/review?item=p1-e1"
    page = follow(tables, response)
    assert (
        "Table 1: The first row is a header, and the first column is not a header. Your edit "
        "passed the required checks and was saved." in page
    )
    edited = grid(tables, "p1-e1")
    assert edited.header_rows == 1
    assert {c.header for c in edited.cells if c.row == 0} == {"col"}
    assert {c.header for c in edited.cells if c.row > 0} == {None}
    body = panel(page)
    assert '<thead><tr><th scope="col">Object</th>' in body  # re-rendered
    assert re.search(r'id="header-row-p1-e1"[^>]*checked', body)
    # Both: the first column's body cells become row headers.
    post(tables, "p1-e1", action="edit", change="headers", header_row="on", header_col="on")
    both = grid(tables, "p1-e1")
    assert both.header_cols == 1
    assert {c.text: c.header for c in both.cells if c.col == 0} == {
        "Object": "col", "Cart": "row", "Ball": "row", "Speed": "row",
    }  # fmt: skip
    assert '<th scope="row">Cart</th>' in panel(screen(tables, "?item=p1-e1"))
    assert fix_of(tables, "p1-e1").status is Status.EDITED
    # Neither: back to the proposal's grid, which has no header cell at all, so the
    # decision is open again (a required check fails).
    page = follow(tables, post(tables, "p1-e1", action="edit", change="headers"))
    assert grid(tables, "p1-e1") == table_1_proposal()
    fix = fix_of(tables, "p1-e1")
    assert (fix.status, fix.reviewed) == (Status.NEEDS_REVIEW, False)
    assert "a required check still fails, so the table still waits for your decision" in page
    assert [e.action for e in edits(tables)] == ["edit", "edit", "edit"]
    assert version(tables) == 3


def test_headers_unchanged_are_refused_and_nothing_is_saved(tables):
    response = post(tables, "p1-e1", action="edit", change="headers")
    assert response.status_code == 422
    assert "The headers are already like that." in response.text
    assert 'href="#header-row-p1-e1"' in response.text  # the error summary's link
    assert '<details class="edit-box table-edit" open>' in response.text
    assert fix_of(tables, "p1-e1").status is Status.NEEDS_REVIEW
    assert edits(tables) == [] and version(tables) == 0


# --------------------------------------------------------------------------- cells


def test_a_cell_changed_is_checked_escaped_and_shown_again(tables):
    typed = "<b>12</b> & up\u200b"  # markup is text; an invisible character is dropped
    response = post(tables, "p1-e1", action="edit", change="cell", row="1", col="1", cell=typed)
    assert response.headers["location"] == f"/jobs/{tables.job_id}/review?item=p1-e1"
    page = follow(tables, response)
    # The cell no longer matches the PDF (10): saved, and the decision stays open.
    assert "Table 1: Row 2, column 2 changed. Saved, but a required check still fails" in page
    assert fix_of(tables, "p1-e1").status is Status.NEEDS_REVIEW
    cell = next(c for c in grid(tables, "p1-e1").cells if (c.row, c.col) == (1, 1))
    assert cell.text == "<b>12</b> & up"
    assert "<td>&lt;b&gt;12&lt;/b&gt; &amp; up</td>" in panel(page)
    assert "&lt;b&gt;12&lt;/b&gt; &amp; up" in with_file(tables).html
    assert "<b>12</b>" not in with_file(tables).html
    # The same text again changes nothing.
    again = post(
        tables, "p1-e1", action="edit", change="cell", row="1", col="1", cell="<b>12</b> & up"
    )
    assert again.status_code == 422
    assert "This is the text the cell has already." in again.text


def test_a_maths_cell_goes_through_the_latex_checks(tables):
    bad = post(
        tables, "p1-e1", action="edit", change="cell", row="3", col="1", cell=r"\notacommand{v}"
    )
    assert bad.status_code == 422
    body = panel(bad.text)
    # The equation edit's own sentences (web.edit_check), not the check codes.
    assert "Your LaTeX was not saved." in body
    assert "is not a command this tool supports" in text_of(body)
    # The cell's form is open again, with what was typed and the error on it.
    form = section(body, 'class="decide-form table-cell-form', "</form>")
    assert r"\notacommand{v}</textarea>" in form and 'aria-invalid="true"' in form
    assert 'id="cell-error-p1-e1"' in form
    assert "Use my edit anyway" not in form  # not a difference from the PDF: no override
    assert grid(tables, "p1-e1") == table_1_proposal() and version(tables) == 0
    good = post(tables, "p1-e1", action="edit", change="cell", row="3", col="1", cell=r"$v = d/t$")
    assert good.status_code == 303
    cell = next(c for c in grid(tables, "p1-e1").cells if (c.row, c.col) == (3, 1))
    assert cell.latex == r"v = d/t"  # cleaned as model output is ($ removed)
    assert cell.mathml is not None and "<mi>d</mi>" in cell.mathml
    assert cell.text == "v = d / t"  # the plain text the serialiser falls back on
    fix = fix_of(tables, "p1-e1")
    own = [c for c in fix.checks if c.name.split(".")[0] in {"latex", "mathml"}]
    assert own and all(c.passed for c in own)  # the LaTeX and MathML checks
    assert MATHS_LATEX not in panel(screen(tables, "?item=p1-e1&cell=r3c1"))


def test_every_change_runs_the_table_checks_again(tables, monkeypatch):
    """A change re-runs the table step's checks on the table as it is now
    (`web.review.table_checks`): a finding stays until the cell is put right."""
    seen: list[tuple[str, int]] = []

    def fake_checks(grid: TableGrid, element: Element) -> list[Check]:
        seen.append((element.id, len(grid.cells)))
        cell = next((c for c in grid.cells if (c.row, c.col) == (1, 1)), None)
        wrong = element.id == "p1-e1" and (cell is None or cell.text != "10")
        code = "text_mismatch" if wrong else None
        return [Check(name="table.cell_text", passed=not wrong, severity="warning", code=code)]

    monkeypatch.setattr(table_step, "table_checks", fake_checks)
    page = follow(tables, post(tables, "p1-e1", action="edit", change="cell", row="1", col="1",
                               cell="11"))  # fmt: skip
    assert "Row 2, column 2 changed." in page
    assert "1 check gives a warning: see the notes on it." in page
    findings = section(panel(page), 'class="explained table-findings"', "</div>")
    # The table step's own sentence for the finding (web/messages.py, tables-step block).
    assert unescape(findings).count(CHECK_MESSAGES[("table.cell_text", "text_mismatch")]) == 1
    # Still wrong after a change of headers.
    post(tables, "p1-e1", action="edit", change="headers", header_row="on")
    assert [c.code for c in fix_of(tables, "p1-e1").failed_checks] == ["text_mismatch"]
    # Put right: the finding goes.
    page = follow(tables, post(tables, "p1-e1", action="edit", change="cell", row="1", col="1",
                               cell="10"))  # fmt: skip
    assert "check gives a warning" not in page
    assert "table-findings" not in panel(page)
    assert fix_of(tables, "p1-e1").failed_checks == []
    # The grid read from the PDF is checked too.
    post(tables, "p1-e3", action="edit", change="use_local")
    assert seen == [("p1-e1", 11), ("p1-e1", 11), ("p1-e1", 11), ("p1-e3", 6)]
    assert [c.passed for c in fix_of(tables, "p1-e3").checks] == [True]


def test_a_cell_that_is_not_there_is_refused_on_the_list(tables):
    response = post(tables, "p1-e1", action="edit", change="cell", row="9", col="0", cell="x")
    assert response.status_code == 422
    assert "That cell is not in the table any more." in response.text
    assert 'href="#cell-pick-p1-e1"' in response.text and 'id="cell-pick-p1-e1"' in response.text
    for row, col in (("²", "0"), ("-1", "0"), ("", "")):
        response = post(tables, "p1-e1", action="edit", change="cell", row=row, col=col, cell="x")
        assert response.status_code == 422


def test_a_cell_emptied_in_the_header_row_stays_a_header(tables):
    post(tables, "p1-e1", action="edit", change="headers", header_row="on")
    post(tables, "p1-e1", action="edit", change="cell", row="0", col="2", cell="")
    empty = next(c for c in grid(tables, "p1-e1").cells if (c.row, c.col) == (0, 2))
    assert empty.text == "" and empty.header == "col"
    post(tables, "p1-e1", action="edit", change="cell", row="0", col="2", cell="Time (s)")
    typed = next(c for c in grid(tables, "p1-e1").cells if (c.row, c.col) == (0, 2))
    assert (typed.text, typed.header) == ("Time (s)", "col")


def test_an_unknown_change_is_refused(tables):
    for change in ("", "rows", "delete"):
        response = post(tables, "p1-e1", action="edit", change=change)
        assert response.status_code == 422
        assert "Choose what to change." in response.text
    assert version(tables) == 0


def test_no_document_text_reaches_the_logs(tables, caplog):
    caplog.set_level(logging.DEBUG)
    post(tables, "p1-e1", action="edit", change="cell", row="1", col="1", cell="SECRET-CELL 12")
    post(tables, "p1-e1", action="edit", change="cell", row="3", col="1", cell=r"\SECRETLATEX")
    post(tables, "p1-e1", action="edit", change="headers", header_row="on")
    assert "SECRET" not in caplog.text
    events = [r for r in caplog.records if r.getMessage() == "review"]
    assert [getattr(r, FIELDS_ATTR)["change"] for r in events] == ["cell", "headers"]


# --------------------------------------------------------------------------- stale tabs


def test_a_form_from_before_another_tab_changed_the_table_is_refused(tables):
    """Tab A shows table 1; tab B makes the first row a header; tab A then changes a cell
    by its place in the grid it showed. Refused with the review screens' one stale-tab
    guard (409, `web.review.is_stale`): nothing saved, the page says why, shows the table
    as it is now with the cell's form open again on what was typed, and offers a reload."""
    assert (
        post(tables, "p1-e1", action="edit", change="headers", header_row="on").status_code == 303
    )
    assert version(tables) == 1
    before = stored(tables)
    response = post(
        tables, "p1-e1", sent_version="0", action="edit", change="cell", row="1", col="1",
        cell="12",
    )  # fmt: skip
    assert response.status_code == 409
    page = unescape(response.text)
    assert STALE_TITLE in page and STALE_MESSAGE in page
    assert "Reload this page</a>" in page
    body = panel(response.text)
    assert '<input type="hidden" name="version" value="1">' in body  # the page is current
    assert '<thead><tr><th scope="col">Object</th>' in body
    assert 'value="12"' in body or ">12</textarea>" in body  # what was typed is kept
    assert stored(tables) == before and version(tables) == 1
    assert len(edits(tables)) == 1
    # Accept and Keep as image from the old page are refused the same way.
    for action in ("accept", "reject"):
        assert post(tables, "p1-e1", sent_version="0", action=action).status_code == 409
    assert fix_of(tables, "p1-e1").status is Status.EDITED


def test_every_review_write_moves_the_version_on(tables):
    assert version(tables) == 0
    post(tables, "p2-e0", action="reject")
    assert version(tables) == 1
    # A form without the field (an older page, a script) is not checked, but still counts.
    post(tables, "p1-e3", sent_version=None, action="edit", change="use_local")
    assert version(tables) == 2
    # Accept: a write moves it on; one refused (nothing to accept) does not.
    assert post(tables, "p1-e1", action="accept").status_code == 303
    assert fix_of(tables, "p1-e1").status is Status.ACCEPTED and version(tables) == 3
    assert post(tables, "p2-e0", action="accept").status_code == 422
    assert version(tables) == 3


def test_the_stale_guard_on_the_list_view(tables):
    post(tables, "p2-e0", action="reject")
    response = post(tables, "p1-e3", sent_version="0", action="edit", change="use_local",
                    view="list")  # fmt: skip
    assert response.status_code == 409
    assert STALE_MESSAGE in unescape(response.text)
    assert "Table 2 is now: " in unescape(response.text)
    assert fix_of(tables, "p1-e3").status is Status.FAILED


# --------------------------------------------------------------------------- list view


def test_the_list_view_lists_tables_like_other_kinds(tables):
    page = listing(tables)
    progress = text_of(section(page, 'id="review-progress"', "</div>"))
    assert "3 decisions are still needed: 3 tables." in progress
    group = section(page, 'id="group-tables"', 'id="optional-checks"')
    assert 'Tables <span class="group-count">(3 decisions)</span>' in group
    first = section(group, 'id="item-p1-e1"', "</article>")
    assert f'src="/jobs/{tables.job_id}/crops/p1-e1.png"' in first
    assert render_table(table_1_proposal(), id_prefix="tp-p1-e1", math_display="inline") in first
    assert re.search(r'value="accept" class="button">Accept<', first)
    assert f'href="/jobs/{tables.job_id}/review?item=p1-e1">open it on the review screen</a>' in (
        first
    )
    second = section(group, 'id="item-p1-e3"', "</article>")
    assert "Use the grid read from the PDF" in second and "Keep as image" in second
    assert "Why it was not converted:" in second
    third = section(group, 'id="item-p2-e0"', "</article>")
    assert "Keep as image" in third and "use_local" not in third
    for form in post_forms(group):
        assert 'name="view" value="list"' in form
    # A decision there comes back to the list's next table.
    response = post(tables, "p1-e3", action="edit", change="use_local", view="list")
    assert response.headers["location"] == f"/jobs/{tables.job_id}/review/list#item-p2-e0"
    assert "Table 2 on page 1" in follow(tables, response)
    # Converted now: the reason it was not is no longer shown.
    decided = section(listing(tables), 'id="item-p1-e3"', "</article>")
    assert "Why it was not converted:" not in decided and "Proposed table" in decided


# --------------------------------------------------------------------------- older results


def test_a_result_stored_before_tables_still_loads_and_shows(tables):
    """A table fix with no proposal and no grid from layout (the 0.4.0 table step as it is
    today), and fields older results lack, load and show (decision 3 is one)."""
    data = tables.job(tables.job_id).result_json
    for element in data["elements"]:
        element.pop("table", None)
    for fix in data["fixes"]:
        fix.pop("table", None)
    result = DocumentResult.model_validate(data)
    assert all(e.table is None for e in result.elements)
    assert usable_local_grid(result.elements[1]) is None


# --------------------------------------------------------------------------- the rules


def plain(rows: list[list[str]], **kw) -> TableGrid:
    cells = [
        TableCell(row=r, col=c, text=t) for r, row in enumerate(rows) for c, t in enumerate(row)
    ]
    return TableGrid(rows=len(rows), cols=len(rows[0]), cells=cells, **kw)


def result_with(grid: TableGrid, local: TableGrid | None = None) -> DocumentResult:
    element = Element(id="p1-e0", kind=Kind.TABLE, page=0, bbox=(0, 0, 10, 10), order=0,
                      table=local)  # fmt: skip
    fix = Fix(element_id="p1-e0", kind=Kind.TABLE, status=Status.NEEDS_REVIEW,
              table=TableFix(grid=grid), review="required")  # fmt: skip
    return DocumentResult(pipeline_version="test", title="t", elements=[element], fixes=[fix],
                          stats=DocumentStats(pages=1, cost_usd=0, latency_ms=0, model_calls=0),
                          html="")  # fmt: skip


def test_header_row_and_column_toggles():
    g = plain([["", "Mass", "Speed"], ["Cart", "2", "3"], ["Ball", "1", "4"]])
    assert not first_row_is_header(g) and not first_col_is_header(g)
    rows = with_header_row(g, True)
    assert rows.header_rows == 1 and [c.header for c in rows.cells[:3]] == ["col"] * 3
    both = with_header_col(rows, True)
    assert both.header_cols == 1
    assert [c.header for c in both.cells if c.col == 0] == ["col", "row", "row"]
    assert grid_problems(both) == []
    assert with_header_row(both, True) is both  # already so: unchanged
    off = with_header_col(with_header_row(both, False), False)
    assert off == g
    assert g.cells[0].header is None  # the originals are never changed


def test_a_first_column_marked_without_its_count_is_a_header_column():
    g = plain([["Cart", "2"], ["Ball", "1"]])
    for cell in g.cells:
        if cell.col == 0:
            cell.header = "row"
    assert g.header_cols == 0 and first_col_is_header(g)
    off = with_header_col(g, False)
    assert {c.header for c in off.cells} == {None}


def test_a_section_row_stays_a_header_when_the_first_column_is_not():
    cells = [
        TableCell(row=0, col=0, text="Item", header="col"),
        TableCell(row=0, col=1, text="Cost", header="col"),
        TableCell(row=1, col=0, col_span=2, text="Fruit", header="row"),
        TableCell(row=2, col=0, text="Apple", header="row"),
        TableCell(row=2, col=1, text="1"),
    ]
    g = TableGrid(rows=3, cols=2, cells=cells, header_rows=1, header_cols=1)
    off = with_header_col(g, False)
    assert {c.text: c.header for c in off.cells}["Fruit"] == "row"
    assert {c.text: c.header for c in off.cells}["Apple"] is None


@pytest.mark.parametrize(
    ("rows", "spans", "form", "problem"),
    [
        ([["Only", "row"]], {}, {"header_row": "on"}, "headers_cover_table"),
        ([["One"], ["column"]], {}, {"header_col": "on"}, "headers_cover_table"),
        ([["Tall", "a"], ["", "b"]], {(0, 0): 2}, {"header_row": "on"}, "header_span_into_body"),
    ],
)
def test_headers_that_would_break_the_grid_are_refused(rows, spans, form, problem):
    g = plain(rows)
    cells = [c for c in g.cells if not any(c.row == r + 1 and c.col == col for r, col in spans)]
    for cell in cells:
        if (cell.row, cell.col) in spans:
            cell.row_span = spans[(cell.row, cell.col)]
    g = g.model_copy(update={"cells": cells})
    assert grid_problems(g) == []
    result = result_with(g)
    with pytest.raises(ReviewInvalid) as caught:
        apply_review(result, "p1-e0", "edit", {"change": "headers", **form})
    (error,) = caught.value.errors
    assert error.field_id == "header-row-p1-e0"
    assert error.message.startswith("The headers were not saved.")
    assert f"({problem})" in error.message
    fix = result.fix_for("p1-e0")
    assert fix is not None and fix.status is Status.NEEDS_REVIEW and fix.table is not None
    assert fix.table.grid == g


def test_text_typed_into_an_empty_header_slot_makes_it_a_header():
    """A model may leave the corner of the header row an empty data cell (valid); text
    typed there would be data in the header row, so it becomes a header."""
    cells = [
        TableCell(row=0, col=0, text=""),
        TableCell(row=0, col=1, text="Mass", header="col"),
        TableCell(row=1, col=0, text="", header=None),
        TableCell(row=1, col=1, text="2"),
    ]
    g = TableGrid(rows=2, cols=2, cells=cells, header_rows=1, header_cols=1)
    assert grid_problems(g) == []
    result = result_with(g)
    apply_review(
        result, "p1-e0", "edit", {"change": "cell", "row": "0", "col": "0", "cell": "Item"}
    )
    apply_review(
        result, "p1-e0", "edit", {"change": "cell", "row": "1", "col": "0", "cell": "Cart"}
    )
    fix = result.fix_for("p1-e0")
    assert fix is not None and fix.table is not None
    assert {c.text: c.header for c in fix.table.grid.cells} == {
        "Item": "col", "Mass": "col", "Cart": "row", "2": None,
    }  # fmt: skip
    assert grid_problems(fix.table.grid) == []


def test_a_problem_the_proposal_already_had_does_not_block_a_cell():
    g = plain([["a", "b"], ["c", "d"]])
    g = g.model_copy(update={"cells": g.cells[:3]})  # a hole where "d" was
    assert grid_problems(g) == ["grid_hole"]
    result = result_with(g)
    change = apply_review(result, "p1-e0", "edit",
                          {"change": "cell", "row": "0", "col": "0", "cell": "A"})  # fmt: skip
    assert change.note == "Row 1, column 1 changed."
    fix = result.fix_for("p1-e0")
    assert fix is not None and fix.table is not None and fix.table.grid.cells[0].text == "A"


def test_the_grid_read_from_the_pdf_is_only_used_when_it_has_no_problem():
    broken = plain([["a", "b"], ["c", "d"]])
    broken = broken.model_copy(update={"cells": broken.cells[:3]})
    result = result_with(plain([["x"], ["y"]]), local=broken)
    with pytest.raises(ReviewInvalid):
        apply_review(result, "p1-e0", "edit", {"change": "use_local"})
    good = plain([["a", "b"], ["c", "d"]], header_rows=1)
    good = with_header_row(good.model_copy(update={"header_rows": 0}), True)
    result = result_with(plain([["x"], ["y"]]), local=good)
    apply_review(result, "p1-e0", "edit", {"change": "use_local"})
    fix = result.fix_for("p1-e0")
    assert fix is not None and fix.table is not None and fix.table.grid == good
    assert fix.table.grid is not good  # a copy: the element's grid stays as layout made it


def test_a_table_kept_as_an_image_cannot_be_accepted_or_changed():
    result = result_with(plain([["x"]]))
    fix = result.fix_for("p1-e0")
    assert fix is not None
    fix.table, fix.status, fix.reason = None, Status.FAILED, "table_not_rebuilt"
    with pytest.raises(ReviewInvalid) as caught:
        apply_review(result, "p1-e0", "accept", {})
    assert caught.value.errors[0].message == NO_TABLE_TO_ACCEPT
    for change in ("cell", "headers"):
        with pytest.raises(ReviewInvalid):
            apply_review(result, "p1-e0", "edit", {"change": change})
    change = apply_review(result, "p1-e0", "reject", {})
    assert change.fix.status is Status.REJECTED


def test_the_card_does_not_say_a_problem_twice():
    """A structural problem the table step already reported under its broader code
    (`table.grid` grid_invalid, `table.headers` header_problem) is not listed again in
    the card's own sentences; one the step did not report still is."""
    from a11y_stem.web.table_card import table_card

    g = plain([["a", "b"], ["c", "d"]])
    g = g.model_copy(update={"cells": g.cells[:3]})  # a hole where "d" was
    result = result_with(g)
    fix = result.fix_for("p1-e0")
    assert fix is not None
    assert table_card(fix, result.elements[0]).problems == [
        "There is a gap in the table where no cell is."
    ]
    fix.checks = [Check(name="table.grid", passed=False, severity="warning", code="grid_invalid")]
    assert table_card(fix, result.elements[0]).problems == []
    fix.checks = [
        Check(name="table.headers", passed=False, severity="warning", code="header_problem")
    ]
    assert table_card(fix, result.elements[0]).problems == [
        "There is a gap in the table where no cell is."
    ]


def test_a_cell_put_right_leaves_the_table_open_while_its_headers_are_still_wrong():
    """One cell edit must not settle a table whose required finding remains (app review:
    "Aluminium" changed while a header problem stood): the change is saved, the decision
    stays open, and Accept (or Keep as image) decides it."""
    pdf = plain([["Material", "Solid", "Liquid"], ["Aluminium", "900", "1180"]], header_rows=1)
    pdf = with_header_row(pdf.model_copy(update={"header_rows": 0}), True)
    misread = [c.model_copy(update={"text": "960"}) if c.text == "900" else c for c in pdf.cells]
    # The header row's last cell is a data cell: a header problem.
    broken = [c.model_copy(update={"header": None}) if c.text == "Liquid" else c for c in misread]
    proposal = pdf.model_copy(update={"cells": broken, "source": "model"})
    result = result_with(proposal, pdf)
    form = {"change": "cell", "row": "1", "col": "1", "cell": "900"}
    change = apply_review(result, "p1-e0", "edit", form)
    fix = change.fix
    assert {c.code for c in fix.failed_checks} == {"header_problem"}
    assert (fix.status, fix.reviewed, fix.pending) == (Status.NEEDS_REVIEW, False, True)
    assert result.stats.by_status == {"needs_review": 1}
    # Accept is still there: the reviewer decides.
    assert apply_review(result, "p1-e0", "accept", {}).fix.status is Status.ACCEPTED


def test_a_cell_typed_unlike_the_pdf_leaves_the_table_open():
    pdf = with_header_row(plain([["Group", "Count"], ["Students", "12"]]), True)
    result = result_with(pdf.model_copy(update={"source": "model"}), pdf)
    change = apply_review(
        result, "p1-e0", "edit", {"change": "cell", "row": "1", "col": "0", "cell": "Seven"}
    )
    assert change.fix.status is Status.NEEDS_REVIEW
    assert {c.code for c in change.fix.failed_checks} == {"text_mismatch"}
    # Put back as printed: every check passes, and the edit decides it.
    back = {"change": "cell", "row": "1", "col": "0", "cell": "Students"}
    assert apply_review(result, "p1-e0", "edit", back).fix.status is Status.EDITED


def test_the_card_makes_a_grid_with_flattened_maths_a_second_choice():
    """The grid read from the PDF gives K = 1/2 mv^2 flat: "Use the grid read from the
    PDF" is not the main button, and its flattened cell is edited as LaTeX, with the
    PDF's text shown."""
    from a11y_stem.web.table_card import table_card

    flat = "K = ¹2mv²"
    local = with_header_row(plain([["Quantity", "Definition"], ["Kinetic energy", flat]]), True)
    result = result_with(local, local)
    fix = result.fix_for("p1-e0")
    assert fix is not None
    fix.table, fix.status, fix.reason = None, Status.FAILED, "table_not_rebuilt"
    card = table_card(fix, result.elements[0])
    assert card.local is not None and card.local_flat
    apply_review(result, "p1-e0", "edit", {"change": "use_local"})
    card = table_card(fix, result.elements[0])
    cell = next(c for c in card.cells if c.ref == "r1c1")
    assert (cell.maths, cell.flat, cell.value) == (True, flat, "")
    assert next(c for c in card.cells if c.ref == "r1c0").flat == ""


def test_a_table_decided_outside_the_queue_is_listed_under_your_decisions(tables):
    # The optional-note table p2-e1 (accepted with a note): the reviewer keeps the image.
    assert post(tables, "p2-e1", action="reject").status_code == 303
    page = screen(tables)
    start = page.find('id="decided-summary"')
    assert start >= 0, "no Your decisions block"
    decided = page[start : page.find("</details>", start)]
    assert "p2-e1" in decided


def test_tables_are_named_by_their_printed_numbers_when_every_one_has_its_own():
    """tables.pdf: a missed table shifted the ordinals, so the card titled "Table 3" showed
    the caption "Table 4". Tables carry their printed numbers when each has a "Table N"
    caption of its own; otherwise all keep their ordinals, so no two names clash."""
    from a11y_stem.web.review import item_labels

    def result(captions: list[str | None]) -> DocumentResult:
        elements = [
            Element(id=f"p1-e{i}", kind=Kind.TABLE, page=0, bbox=(0, 0, 1, 1), order=i,
                    caption=caption)
            for i, caption in enumerate(captions)
        ]  # fmt: skip
        fixes = [
            Fix(element_id=e.id, kind=Kind.TABLE, status=Status.FAILED, reason="table_not_rebuilt")
            for e in elements
        ]
        return DocumentResult(pipeline_version="test", elements=elements, fixes=fixes,
                              stats=DocumentStats(pages=1, cost_usd=0, latency_ms=0,
                                                  model_calls=0))  # fmt: skip

    printed = item_labels(result(["Table 2: Speeds.", "Table 4: Specific heat by phase."]))
    assert printed == {"p1-e0": "Table 2", "p1-e1": "Table 4"}
    assert item_labels(result(["Table 2.1 A.", None])) == {"p1-e0": "Table 1", "p1-e1": "Table 2"}
    assert item_labels(result(["Table 1 A.", "Table 1 (continued)."])) == {
        "p1-e0": "Table 1",
        "p1-e1": "Table 2",
    }


# --------------------------------------------------------------------------- written as text


def text_output():
    """Table p2-e1 judged not a table of data by the model (table_step: its words in
    reading order, the note `written_as_text`), as the step now stores it."""
    output = table_output()
    fix = output.result.fix_for("p2-e1")
    assert fix is not None
    fix.table = TableFix(grid=grid_of(TABLE_4).model_copy(update={"is_data": False}))
    fix.checks = [
        Check(name="table.kind", passed=False, severity="warning", code="written_as_text")
    ]
    return output


@pytest.fixture
def as_text(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = text_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, table_pdf()))
        env.run_worker()
        yield env


def test_a_table_written_as_text_is_shown_as_students_get_it(as_text):
    page = screen(as_text, "?item=p2-e1")
    assert "As students get it: text, not a table" in page
    assert "students get its text in reading order" in text_of(page)
    assert "Change the table" not in page  # no cells or headers to change in text
    assert "Use this grid instead" in page  # the way back to a table
    assert queue.first_open(queue.decisions(stored(as_text))) is not None  # others wait
    assert "p2-e1" not in {d.key for d in queue.decisions(stored(as_text))}  # not a decision
    assert post(as_text, "p2-e1", action="accept").status_code == 303  # re-renders
    html = as_text.browser.get(f"/jobs/{as_text.job_id}/download").text
    assert '<div id="p2-e1"><p>Table 4 Constants</p><dl><dt>Constant</dt>' in html
    response = post(as_text, "p2-e1", action="edit", change="cell", row="1", col="1", cell="9.8")
    assert response.status_code == 422
    assert "written as text, not as a table" in text_of(response.text)
    back = post(as_text, "p2-e1", action="edit", change="use_local")
    assert back.status_code == 303
    fix = stored(as_text).fix_for("p2-e1")
    assert fix is not None and fix.table is not None and fix.table.grid.is_data


def test_the_list_view_offers_the_grid_read_from_the_pdf_for_a_table_written_as_text(as_text):
    card = section(listing(as_text), 'id="fix-p2-e1"', "</article>")
    assert 'name="change" value="use_local"' in card  # the way back to a table, here too
    assert "Use the grid read from the PDF" in text_of(card)
    assert "To change its headers or a cell" not in text_of(card)  # text has none
    back = post(as_text, "p2-e1", action="edit", change="use_local", view="list")
    assert back.status_code == 303
    fix = stored(as_text).fix_for("p2-e1")
    assert fix is not None and fix.table is not None and fix.table.grid.is_data


def numbers_output():
    """Table 5 (numbers) judged not a table of data by the model: the step proposes the
    grid read from the PDF instead, a decision (`numbers_not_a_table`)."""
    output = table_output()
    fix = output.result.fix_for("p2-e2")
    assert fix is not None
    fix.status, fix.review = Status.NEEDS_REVIEW, "required"
    fix.table = TableFix(grid=grid_of(TABLE_5))
    fix.checks = [
        Check(name="table.kind", passed=False, severity="warning", code="numbers_not_a_table")
    ]
    return output


def test_numbers_the_model_calls_not_a_table_are_a_decision_before_the_finish_line(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = numbers_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, table_pdf()))
        env.run_worker()
        assert "p2-e2" in {d.key for d in queue.decisions(stored(env)) if d.open}
        page = text_of(screen(env, "?item=p2-e2"))
        assert "but its cells hold numbers" in page
        assert post(env, "p2-e2", action="accept").status_code == 303
        html = env.browser.get(f"/jobs/{env.job_id}/download").text
        assert "<table" in section(html, 'id="p2-e2"', "</table>")


def test_a_maths_cell_unlike_the_pdf_needs_use_my_edit_anyway(tables):
    """The same checks as an equation's edit, against the PDF's text in that cell
    ("v = d / t"): x for d changes what a student reads, so it is not used without the
    reviewer's explicit choice, which is recorded."""
    typed = r"v = \frac{x}{t}"
    refused = post(tables, "p1-e1", action="edit", change="cell", row="3", col="1", cell=typed)
    assert refused.status_code == 422
    body = panel(refused.text)
    assert "differs from the PDF where it changes the meaning" in text_of(body)
    form = section(body, 'class="decide-form table-cell-form', "</form>")
    assert '<button type="submit" name="action" value="edit_anyway"' in form
    assert grid(tables, "p1-e1") == table_1_proposal() and version(tables) == 0
    used = post(tables, "p1-e1", action="edit_anyway", change="cell", row="3", col="1", cell=typed)
    assert used.status_code == 303
    cell = next(c for c in grid(tables, "p1-e1").cells if (c.row, c.col) == (3, 1))
    assert cell.latex == typed
    fix = fix_of(tables, "p1-e1")
    assert "symbol_mismatch" in {c.code for c in fix.failed_checks}  # kept on the fix
    assert fix.status is Status.NEEDS_REVIEW  # a table is decided by Accept
    with session_scope(tables.ctx.factory) as session:
        edits = session.scalars(select(ReviewEdit).where(ReviewEdit.job_id == tables.job_id))
        assert any((e.after or {}).get("anyway") for e in edits)
