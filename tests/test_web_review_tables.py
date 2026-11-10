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

from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload, with_file
from fixtures.web.table_doc import (
    MATHS_LATEX,
    TABLE_4,
    grid_of,
    table_1_proposal,
    table_output,
    table_pdf,
)
from readablestem.core.types import (
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
from readablestem.observability.logging import FIELDS_ATTR
from readablestem.pipeline import table_step
from readablestem.web import queue
from readablestem.web.db import ReviewEdit, session_scope
from readablestem.web.messages import CHECK_MESSAGES
from readablestem.web.review import (
    NO_TABLE_TO_ACCEPT,
    ReviewInvalid,
    apply_review,
    first_col_is_header,
    with_header_col,
    with_header_row,
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


# --------------------------------------------------------------------------- the queue


# --------------------------------------------------------------------------- the card


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


def test_no_document_text_reaches_the_logs(tables, caplog):
    caplog.set_level(logging.DEBUG)
    post(tables, "p1-e1", action="edit", change="cell", row="1", col="1", cell="SECRET-CELL 12")
    post(tables, "p1-e1", action="edit", change="cell", row="3", col="1", cell=r"\SECRETLATEX")
    post(tables, "p1-e1", action="edit", change="headers", header_row="on")
    assert "SECRET" not in caplog.text
    events = [r for r in caplog.records if r.getMessage() == "review"]
    assert [getattr(r, FIELDS_ATTR)["change"] for r in events] == ["cell", "headers"]


# --------------------------------------------------------------------------- stale tabs


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


# --------------------------------------------------------------------------- list view


# --------------------------------------------------------------------------- older results


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


def test_a_first_column_marked_without_its_count_is_a_header_column():
    g = plain([["Cart", "2"], ["Ball", "1"]])
    for cell in g.cells:
        if cell.col == 0:
            cell.header = "row"
    assert g.header_cols == 0 and first_col_is_header(g)
    off = with_header_col(g, False)
    assert {c.header for c in off.cells} == {None}


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


def test_the_card_makes_a_grid_with_flattened_maths_a_second_choice():
    """The grid read from the PDF gives K = 1/2 mv^2 flat: "Use the grid read from the
    PDF" is not the main button, and its flattened cell is edited as LaTeX, with the
    PDF's text shown."""
    from readablestem.web.table_card import table_card

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


def test_tables_are_named_by_their_printed_numbers_when_every_one_has_its_own():
    """tables.pdf: a missed table shifted the ordinals, so the card titled "Table 3" showed
    the caption "Table 4". Tables carry their printed numbers when each has a "Table N"
    caption of its own; otherwise all keep their ordinals, so no two names clash."""
    from readablestem.web.review import item_labels

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
    # ... and the finding says so with that one correction, not a link to cells there are not.
    assert 'data-role="finding-grid"' in page and 'data-role="finding-edit"' not in page
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
