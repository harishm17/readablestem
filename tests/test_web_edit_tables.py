"""T3-11: table review rough edges.

"Accept all clean tables" in one action, "this grid is not a table", and a maths cell read
by the equation step. Uses fixtures.web.table_doc (a PDF with ruled tables and a table fix in
every review tier). The model is the offline client with an answer set per test.
"""

from __future__ import annotations

import re
from html import unescape

import pytest

from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload
from fixtures.web.table_doc import MATHS_LATEX, table_output, table_pdf
from readablestem.core.types import ConversionOutput, DocumentResult, Status
from readablestem.models.fake import FakeVisionClient
from readablestem.web.db import session_scope
from readablestem.web.review import (
    NO_CLEAN_TABLES,
    NO_TEXT_GRID,
    ReviewInvalid,
    accept_clean_tables,
    grid_as_text,
)

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


# --------------------------------------------------------------------------- not a table


def test_without_any_text_to_write_it_is_refused(tables):
    response = decision_post(tables, "p2-e0", action="edit", change="not_a_table")
    assert response.status_code == 422
    assert NO_TEXT_GRID in unescape(response.text)
    fix = stored(tables).fix_for("p2-e0")
    assert fix is not None and fix.status is Status.FAILED


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
    from readablestem.pipeline.validate import safe_latex_to_mathml
    from readablestem.web.db import Job

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


def test_only_a_maths_cell_with_a_place_on_the_page_is_read(tables):
    with_model(tables, {"is_equation": True, "latex": "x", "confidence": 0.99})
    plain = read_post(tables, row="1", col="0")
    assert plain.status_code == 422 and "holds math" in text_of(plain.text)
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
