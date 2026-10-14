"""T3-11: table review rough edges.

"Accept all clean tables" in one action, "this grid is not a table", and a maths cell read
by the equation step. Uses fixtures.web.table_doc (a PDF with ruled tables and a table fix in
every review tier). The model is the offline client with an answer set per test.
"""

from __future__ import annotations

import re
from html import unescape

import pytest
from sqlalchemy import select

from a11y_stem.core.types import ConversionOutput, DocumentResult, Status
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.web.db import ReviewEdit, session_scope
from a11y_stem.web.review import (
    NO_CLEAN_TABLES,
    NO_TEXT_GRID,
    ReviewInvalid,
    accept_clean_tables,
    apply_review,
    clean_tables,
    grid_as_text,
    is_clean_table,
)
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload, with_file
from fixtures.web.table_doc import MATHS_LATEX, table_output, table_pdf

CELL_BOX = (182.0, 184.0, 402.0, 202.0)  # row 3, columns 1 to 2 of Table 1


def clean_output() -> ConversionOutput:
    """The table document with two clean tables waiting: p2-e1 and p2-e2 are made proposals
    the model was merely unsure of (no finding of any kind), and the maths cell of Table 1 has
    a place on the page."""
    output = table_output()
    result = output.result
    for eid in ("p2-e1", "p2-e2"):
        fix = result.fix_for(eid)
        assert fix is not None
        fix.status = Status.NEEDS_REVIEW
        fix.review = "required"
        fix.checks = []
    local = next(e for e in result.elements if e.id == "p1-e1").table
    assert local is not None
    for cell in local.cells:
        if (cell.row, cell.col) == (3, 1):
            cell.bbox = CELL_BOX
    return output


@pytest.fixture
def tables(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = clean_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, table_pdf()))
        env.run_worker()
        yield env


def stored(env) -> DocumentResult:
    return DocumentResult.model_validate(env.job(env.job_id).result_json)


def version(env) -> str:
    return str(env.job(env.job_id).result_version or 0)


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def post(env, path: str, **fields):
    page = f"/jobs/{env.job_id}/review"
    data = {"csrf_token": token(env.browser, page), "version": version(env), "view": "split"}
    return env.browser.post(f"/jobs/{env.job_id}{path}", data=data | fields)


def decision_post(env, target: str, **fields):
    return post(env, f"/review/{target}", **fields)


# --------------------------------------------------------------------------- the rule


def test_a_clean_table_waits_with_nothing_found_wrong():
    result = clean_output().result
    elements = {e.id: e for e in result.elements}
    assert [f.element_id for f in clean_tables(result)] == ["p2-e1", "p2-e2"]
    # A finding, a table kept as an image, an accepted table: none is clean.
    for eid in ("p1-e1", "p1-e3", "p2-e0"):
        fix = result.fix_for(eid)
        assert fix is not None and not is_clean_table(fix, elements[eid])
    fix = result.fix_for("p2-e1")
    assert fix is not None
    fix.status = Status.ACCEPTED
    assert not is_clean_table(fix)


def test_a_table_with_a_structural_problem_is_not_clean():
    result = clean_output().result
    fix = result.fix_for("p2-e1")
    assert fix is not None and fix.table is not None
    fix.table.grid.header_rows = fix.table.grid.rows  # headers cover the whole table
    assert not is_clean_table(fix)


def test_accepting_the_clean_tables_accepts_exactly_those():
    result = clean_output().result
    changes = accept_clean_tables(result)
    assert [c.element_id for c in changes] == ["p2-e1", "p2-e2"]
    assert all(c.fix.status is Status.ACCEPTED and c.fix.reviewed for c in changes)
    p1 = result.fix_for("p1-e1")
    assert p1 is not None and p1.status is Status.NEEDS_REVIEW  # it has a finding
    with pytest.raises(ReviewInvalid) as raised:
        accept_clean_tables(result)
    assert raised.value.errors[0].message == NO_CLEAN_TABLES


# --------------------------------------------------------------------------- the button


def test_the_button_is_offered_when_several_tables_are_clean(tables):
    page = tables.browser.get(f"/jobs/{tables.job_id}/review").text
    assert "Accept all 2 clean tables" in text_of(page)
    assert f"/jobs/{tables.job_id}/tables/accept-clean" in page
    listing = tables.browser.get(f"/jobs/{tables.job_id}/review/list").text
    assert "Accept all 2 clean tables" in text_of(listing)


def test_one_clean_table_has_no_button(tables):
    decision_post(tables, "p2-e1", action="accept")
    page = tables.browser.get(f"/jobs/{tables.job_id}/review").text
    assert "clean tables" not in text_of(page)


def test_one_action_accepts_them_all_and_is_undone_in_one_step(tables):
    before = version(tables)
    response = post(tables, "/tables/accept-clean")
    assert response.status_code == 303
    result = stored(tables)
    for eid in ("p2-e1", "p2-e2"):
        fix = result.fix_for(eid)
        assert fix is not None and fix.status is Status.ACCEPTED and fix.reviewed
    assert version(tables) == str(int(before) + 1)  # one write
    assert tables.job(tables.job_id).html_stale is True
    with session_scope(tables.ctx.factory) as session:
        edits = session.scalars(select(ReviewEdit).order_by(ReviewEdit.created_at)).all()
    assert [(e.element_id, e.action) for e in edits] == [("p2-e1", "accept"), ("p2-e2", "accept")]
    assert len({e.after["write"] for e in edits}) == 1
    page = tables.browser.get(response.headers["location"]).text
    assert "Accepted 2 clean tables." in text_of(page)
    # The table with a finding is still waiting, and so are the tables kept as images.
    assert stored(tables).fix_for("p1-e1").status is Status.NEEDS_REVIEW  # type: ignore[union-attr]
    undone = tables.browser.post(
        f"/jobs/{tables.job_id}/undo",
        data={
            "csrf_token": token(tables.browser, f"/jobs/{tables.job_id}/review"),
            "version": version(tables),
        },
    )
    assert undone.status_code == 303
    result = stored(tables)
    for eid in ("p2-e1", "p2-e2"):
        fix = result.fix_for(eid)
        assert fix is not None and fix.status is Status.NEEDS_REVIEW and not fix.reviewed


def test_with_none_clean_nothing_is_written(tables):
    post(tables, "/tables/accept-clean")
    before = version(tables)
    response = post(tables, "/tables/accept-clean")
    assert response.status_code == 303
    assert version(tables) == before
    page = tables.browser.get(response.headers["location"]).text
    assert NO_CLEAN_TABLES in text_of(page)


def test_the_list_view_accepts_them_too_and_lands_in_the_list(tables):
    response = post(tables, "/tables/accept-clean", view="list")
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/jobs/{tables.job_id}/review/list")
    assert stored(tables).fix_for("p2-e2").status is Status.ACCEPTED  # type: ignore[union-attr]


def test_a_stale_form_accepts_nothing(tables):
    old = version(tables)
    decision_post(tables, "p1-e3", action="reject")
    response = post(tables, "/tables/accept-clean", version=old)
    assert response.status_code == 409
    assert stored(tables).fix_for("p2-e1").status is Status.NEEDS_REVIEW  # type: ignore[union-attr]


# --------------------------------------------------------------------------- not a table


def test_a_grid_that_is_not_a_table_is_written_as_text(tables):
    page = tables.browser.get(f"/jobs/{tables.job_id}/review?item=p1-e3").text
    assert "This grid is not a table" in text_of(page)
    response = decision_post(tables, "p1-e3", action="edit", change="not_a_table")
    assert response.status_code == 303
    fix = stored(tables).fix_for("p1-e3")
    assert fix is not None and fix.table is not None
    assert not fix.table.grid.is_data and fix.status is Status.EDITED and fix.reviewed
    html = with_file(tables).html or ""
    section = html[html.index("Quantity") - 200 : html.index("Energy") + 100]
    assert "<dt>Force</dt><dd>N</dd>" in section and "<table" not in section


def test_a_proposal_can_be_called_not_a_table_and_the_local_grid_is_preferred(tables):
    response = decision_post(tables, "p1-e1", action="edit", change="not_a_table")
    assert response.status_code == 303
    fix = stored(tables).fix_for("p1-e1")
    assert fix is not None and fix.table is not None
    assert not fix.table.grid.is_data and fix.table.grid.source == "local"
    assert fix.status is Status.EDITED


def test_without_any_text_to_write_it_is_refused(tables):
    response = decision_post(tables, "p2-e0", action="edit", change="not_a_table")
    assert response.status_code == 422
    assert NO_TEXT_GRID in unescape(response.text)
    fix = stored(tables).fix_for("p2-e0")
    assert fix is not None and fix.status is Status.FAILED


def test_it_can_be_made_a_table_again_and_the_change_undone(tables):
    decision_post(tables, "p1-e3", action="edit", change="not_a_table")
    page = tables.browser.get(f"/jobs/{tables.job_id}/review?item=p1-e3").text
    assert "Use this grid instead" in text_of(page) and "This grid is not a table" not in text_of(
        page
    )
    done = decision_post(tables, "p1-e3", action="edit", change="use_local")
    assert done.status_code == 303
    assert stored(tables).fix_for("p1-e3").table.grid.is_data  # type: ignore[union-attr]
    undone = tables.browser.post(
        f"/jobs/{tables.job_id}/undo",
        data={
            "csrf_token": token(tables.browser, f"/jobs/{tables.job_id}/review"),
            "version": version(tables),
        },
    )
    assert undone.status_code == 303
    assert not stored(tables).fix_for("p1-e3").table.grid.is_data  # type: ignore[union-attr]


def test_the_list_view_has_the_button_too(tables):
    page = tables.browser.get(f"/jobs/{tables.job_id}/review/list").text
    assert "This grid is not a table" in text_of(page)
    response = decision_post(tables, "p1-e3", view="list", action="edit", change="not_a_table")
    assert response.status_code == 303


def test_grid_as_text_never_writes_flattened_maths():
    result = clean_output().result
    fix = result.fix_for("p1-e3")
    element = next(e for e in result.elements if e.id == "p1-e3")
    assert fix is not None and grid_as_text(fix, element) is not None
    assert grid_as_text(fix, None) is None  # no local grid and no proposal


# --------------------------------------------------------------------------- a maths cell


def with_model(env, answer):
    env.clients = []

    def factory(settings, budget):
        client = FakeVisionClient(budget=budget, defaults={"equation": answer} if answer else {})
        env.clients.append(client)
        return client

    env.ctx.client_factory = factory


def set_cell_latex(env, latex: str) -> None:
    """Give the maths cell of Table 1 other LaTeX than the PDF says, so a read is a change."""
    from a11y_stem.pipeline.validate import safe_latex_to_mathml
    from a11y_stem.web.db import Job

    result = stored(env)
    fix = result.fix_for("p1-e1")
    assert fix is not None and fix.table is not None
    for cell in fix.table.grid.cells:
        if cell.latex is not None:
            cell.latex, cell.mathml = latex, safe_latex_to_mathml(latex, "inline")
    with session_scope(env.ctx.factory) as session:
        session.get(Job, env.job_id).result_json = result.model_dump(mode="json", exclude={"html"})


def read_post(env, row="3", col="1", **fields):
    return post(env, "/tables/p1-e1/read-cell", row=row, col=col, **fields)


def test_the_cell_form_offers_to_read_a_maths_cell(tables):
    page = tables.browser.get(f"/jobs/{tables.job_id}/review?item=p1-e1&cell=r3c1").text
    assert "Read this cell with the model" in text_of(page)
    plain = tables.browser.get(f"/jobs/{tables.job_id}/review?item=p1-e1&cell=r1c0").text
    assert "Read this cell with the model" not in text_of(plain)


def test_a_maths_cell_is_read_by_the_equation_step_and_checked_like_an_edit(tables):
    set_cell_latex(tables, "v = d")
    with_model(tables, {"is_equation": True, "latex": MATHS_LATEX, "confidence": 0.99})
    before = tables.job(tables.job_id)
    response = read_post(tables)
    assert response.status_code == 303, response.text[:300]
    fix = stored(tables).fix_for("p1-e1")
    assert fix is not None and fix.table is not None
    cell = next(c for c in fix.table.grid.cells if (c.row, c.col) == (3, 1))
    assert cell.latex == MATHS_LATEX and cell.mathml
    # Charged to the job like any model call, and the crop it read is the cell's.
    after = tables.job(tables.job_id)
    assert after.model_calls > before.model_calls and after.cost_usd > before.cost_usd
    request = tables.clients[0].requests[0]
    assert (
        request.step == "equation" and request.images and request.images[0].startswith(b"\x89PNG")
    )
    page = tables.browser.get(response.headers["location"]).text
    assert "The tool read this cell." in text_of(page)


def test_what_is_read_that_differs_from_the_pdf_is_shown_not_saved(tables):
    set_cell_latex(tables, MATHS_LATEX)
    with_model(tables, {"is_equation": True, "latex": r"v = d + t", "confidence": 0.99})
    response = read_post(tables)
    assert response.status_code == 422
    text = text_of(response.text)
    assert "differs from the PDF" in text or "Use my edit anyway" in text
    assert "v = d + t" in response.text  # in the cell's box, for the reviewer to correct
    fix = stored(tables).fix_for("p1-e1")
    assert fix is not None and fix.table is not None
    assert next(c for c in fix.table.grid.cells if (c.row, c.col) == (3, 1)).latex == MATHS_LATEX


def test_a_cell_the_model_cannot_read_is_left_for_the_reviewer(tables):
    with_model(tables, None)
    response = read_post(tables)
    assert response.status_code == 422
    assert "could not read this cell as maths" in text_of(response.text)


def test_only_a_maths_cell_with_a_place_on_the_page_is_read(tables):
    with_model(tables, {"is_equation": True, "latex": "x", "confidence": 0.99})
    plain = read_post(tables, row="1", col="0")
    assert plain.status_code == 422 and "holds maths" in text_of(plain.text)
    missing = read_post(tables, row="9", col="9")
    assert missing.status_code == 422 and "not in the table any more" in text_of(missing.text)
    junk = read_post(tables, row="x", col="1")
    assert junk.status_code == 422
    assert not tables.clients or not tables.clients[0].requests  # nothing was sent


def test_a_stale_form_reads_nothing(tables):
    with_model(tables, {"is_equation": True, "latex": "x", "confidence": 0.99})
    old = version(tables)
    decision_post(tables, "p1-e3", action="reject")
    response = read_post(tables, version=old)
    assert response.status_code == 409
    assert not tables.clients or not tables.clients[0].requests


def test_reading_a_cell_is_refused_when_the_document_has_used_its_allowance(tables):
    set_cell_latex(tables, "v = d")
    with_model(tables, {"is_equation": True, "latex": MATHS_LATEX, "confidence": 0.99})
    tables.ctx.guard.cap_usd = 0.0001
    response = read_post(tables)
    assert response.status_code == 422
    assert "allowance" in text_of(response.text)
    assert not tables.clients[0].requests


def test_apply_review_accepts_the_new_change_name():
    result = clean_output().result
    change = apply_review(result, "p1-e3", "edit", {"change": "not_a_table"})
    assert change.note and change.fix.status is Status.EDITED
