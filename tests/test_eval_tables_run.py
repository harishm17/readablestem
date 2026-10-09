"""The tables task end to end on synthetic predictions (offline).

Layout's table detection (tables-detect) and the table step (tables-step) are built in
parallel, so these tests stand in for them through the published contracts: `layout.
analyze` returns TABLE elements with a local grid (`Element.table`), `table_step.rebuild`
returns a `Fix` with `Fix.table`. The task must run in local mode (detect + grid, no
model) and with the model step, and report the same format as the other tasks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from a11y_stem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget
from a11y_stem.core.types import (
    Check,
    Element,
    Fix,
    Kind,
    ModelCall,
    Status,
    TableCell,
    TableFix,
    TableGrid,
)
from a11y_stem.eval import tables as T
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.pipeline import layout, table_step
from test_eval_support import base_record, settings, write_gold

SPEEDS_HTML = (
    "<table><tr><th scope='col'>Medium</th><th scope='col'>Speed (m/s)</th></tr>"
    "<tr><td>Air</td><td>343</td></tr><tr><td>Water</td><td>1480</td></tr></table>"
)
UNITS_HTML = (
    "<table><tr><th scope='col'>Quantity</th><th scope='col'>Unit</th></tr>"
    "<tr><td>Length</td><td>metre</td></tr><tr><td>Mass</td><td>kilogram</td></tr></table>"
)
TERMS_HTML = "<table><tr><td>accuracy</td><td>ampere</td><td>constant</td></tr></table>"


def grid(rows: list[list[str]], *, header_rows: int = 1, is_data: bool = True, source="local"):
    cells = [
        TableCell(row=r, col=c, text=text, header="col" if r < header_rows else None)
        for r, row in enumerate(rows)
        for c, text in enumerate(row)
    ]
    return TableGrid(
        rows=len(rows),
        cols=len(rows[0]),
        cells=cells,
        header_rows=header_rows,
        is_data=is_data,
        source=source,
    )


SPEEDS = [["Medium", "Speed (m/s)"], ["Air", "343"], ["Water", "1480"]]
SPEEDS_MISREAD = [["Medium", "Speed (m/s)"], ["Air", "343"], ["Water", "1430"]]
UNITS = [["Quantity", "Unit"], ["Length", "metre"], ["Mass", "kilogram"]]


def table_element(page: int, index: int, local: TableGrid | None) -> Element:
    return Element(
        id=f"p{page + 1}-e{index}",
        kind=Kind.TABLE,
        page=page,
        bbox=(50.0, 100.0 + 50 * index, 400.0, 140.0 + 50 * index),
        order=page * 10 + index,
        table=local,
    )


def pdf_of(title: str, pages: list[str]) -> bytes:
    doc = pymupdf.open()
    for text in pages:
        doc.new_page().insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=10)
    doc.set_metadata({"title": title})
    return doc.tobytes()


# Each document's pages (as text) and the tables "layout" finds in it.
PAGE_TEXT = {
    "speeds": ["Intro text.", "Medium Speed (m/s) Air 343 Water 1480"],
    "units": ["Quantity Unit Length metre Mass kilogram. accuracy ampere constant"],
}


class FakeTableMedia:
    """PDFs without a browser: each OpenStax page in any style prints the same text."""

    def __init__(self) -> None:
        self.printed: list[str] = []
        self.closed = False

    def pdf(self, doc: T.TableDocument) -> bytes:
        self.printed.append(doc.id)
        if doc.pdf_path is not None:
            return doc.pdf_path.read_bytes()
        slug = doc.records[0]["page_slug"]
        return pdf_of(doc.id, PAGE_TEXT[slug])

    def close(self) -> None:
        self.closed = True


def fake_layout(found: dict[str, list[Element]]):
    """`layout.analyze` returning the given elements for a document (by PDF title)."""

    def analyze(pdf_bytes: bytes, *, pages=None, max_pages=None) -> layout.Layout:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            title, count = doc.metadata.get("title", ""), doc.page_count
        key = next((k for k in found if k in title), None)
        return layout.Layout(
            elements=list(found.get(key, []) if key else []),
            page_count=count,
            pages=list(range(count)),
        )

    return analyze


@pytest.fixture
def gold_dir(tmp_path: Path) -> Path:
    records = [
        base_record("speeds", "table", 0, html=SPEEDS_HTML, is_layout=False),
        base_record("units", "table", 0, html=UNITS_HTML, is_layout=False),
        base_record("units", "table", 1, html=TERMS_HTML, is_layout=True),
    ]
    return write_gold(tmp_path, records)


@pytest.fixture
def authored_dir(tmp_path: Path) -> Path:
    root = tmp_path / "authored"
    (root / "doc").mkdir(parents=True)
    (root / "doc" / "doc.pdf").write_bytes(pdf_of("authored-doc", ["Medium Speed (m/s) Air 343"]))
    gold = {"id": "authored/doc/T1", "page": 0, "is_layout": False, "html": SPEEDS_HTML}
    (root / "doc" / "gold.jsonl").write_text(json.dumps(gold) + "\n", encoding="utf-8")
    index = {"documents": [{"id": "doc", "pdf": "doc/doc.pdf", "gold": "doc/gold.jsonl"}]}
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
    return root


@pytest.fixture
def detected(monkeypatch):
    """Layout finds: the speeds table (page 2) with a misread cell in its local grid, the
    units table, the Key Terms grid called a layout table, and one false table."""
    found = {
        "speeds": [table_element(1, 0, grid(SPEEDS_MISREAD))],
        "units": [
            table_element(0, 0, grid(UNITS)),
            table_element(
                0, 1, grid([["accuracy", "ampere", "constant"]], header_rows=0, is_data=False)
            ),
            table_element(0, 2, grid([["Name:", "____"]], header_rows=0)),
        ],
        "authored-doc": [table_element(0, 0, grid(SPEEDS[:2]))],
    }
    monkeypatch.setattr(layout, "analyze", fake_layout(found))
    monkeypatch.setattr(layout, "crop", lambda pdf, element, **_: b"png")
    monkeypatch.setattr(layout, "release", lambda pdf=None: None)
    return found


def config(gold_dir, media, *, mode="local", authored=None, **kw) -> T.TablesConfig:
    return T.TablesConfig(
        settings=settings(),
        media=media,
        date="2026-10-09",
        gold_dir=gold_dir,
        authored_dir=authored,
        mode=mode,
        **kw,
    )


# --------------------------------------------------------------------- local mode


def test_local_mode_scores_the_local_grid_and_every_table_is_a_decision(gold_dir, detected):
    media = FakeTableMedia()
    run = T.run_tables_eval(config(gold_dir, media))
    report = run.report
    assert (report["task"], report["mode"], report["client"]) == ("tables", "local", "none")
    assert report["complete"] and report["n"] == report["n_planned"] == 6  # 2 pages x 3 styles
    assert media.closed and len(media.printed) == 6
    m = report["metrics"]
    assert m["gold_data_tables"] == 6 and m["detection_recall"] == 1.0
    assert m["false_tables"] == 3  # the form-like grid, once per style
    assert m["detection_precision"] == pytest.approx(6 / 9)
    assert m["data_vs_layout"]["layout_kept_out"] == 3  # called layout: kept out
    assert m["text_fidelity"]["wrong_cells"] == 3  # the misread 1480, per style
    # The step with tables off keeps every table as an image: a required decision each,
    # so no wrong cell goes unasked.
    load = m["review_load"]
    assert load["required_decisions"] == load["tables"] + 3 == 12
    assert m["text_fidelity"]["wrong_cells_not_flagged"] == 0
    assert report["cost"]["total_usd"] == 0 and report["cost"]["model_calls"] == 0
    assert m["real_documents"] is None
    assert set(m["by_style"]) == {"grid", "booktabs", "norules"}


def test_gold_pages_come_from_the_print(gold_dir, detected):
    run = T.run_tables_eval(config(gold_dir, FakeTableMedia(), limit=1))
    (row,) = run.items[0]["rows"]
    assert row["page"] == 1 and row["detected"]  # the speeds table is on the second page


def test_authored_documents_join_with_their_own_style(gold_dir, authored_dir, detected):
    run = T.run_tables_eval(config(gold_dir, FakeTableMedia(), authored=authored_dir))
    assert run.report["n"] == 7
    assert run.items[-1]["id"] == "authored/doc" and run.items[-1]["style"] == "authored"
    assert "authored" in run.report["metrics"]["by_style"]
    assert run.report["dataset"]["authored_sha256"]


def test_styles_and_limit_change_the_selection(gold_dir, detected):
    a = T.run_tables_eval(config(gold_dir, FakeTableMedia(), styles=("grid",))).report
    b = T.run_tables_eval(config(gold_dir, FakeTableMedia(), limit=1)).report
    assert a["n"] == 2 and a["dataset"]["styles"] == ["grid"]
    assert a["dataset"]["selection_sha256"] != b["dataset"]["selection_sha256"]


def test_a_document_layout_cannot_read_counts_as_found_nothing(gold_dir, monkeypatch):
    def broken(pdf_bytes, **_):
        raise layout.LayoutError("unreadable", "cannot read")

    monkeypatch.setattr(layout, "analyze", broken)
    report = T.run_tables_eval(config(gold_dir, FakeTableMedia(), styles=("grid",))).report
    assert report["complete"] and report["metrics"]["detection_recall"] == 0.0
    assert report["metrics"]["page_errors"] == {"unreadable": 2}


# --------------------------------------------------------------------- model mode


def call(cost: float = 0.0007) -> ModelCall:
    return ModelCall(
        step="table",
        model="gpt-6-luna",
        prompt_version="table-test",
        input_tokens=900,
        output_tokens=200,
        cost_usd=cost,
        latency_ms=6000,
        outcome="ok",
    )


def model_rebuild(answers: dict[str, Fix]):
    """`table_step.rebuild` stand-in: the given fix per element id; records the options."""
    seen = []

    def rebuild(element, png, *, client, settings, context_text=None):
        from a11y_stem.pipeline import options

        seen.append((element.id, options.current().tables, context_text))
        return answers[element.id].model_copy()

    rebuild.seen = seen  # type: ignore[attr-defined]
    return rebuild


def fix(element_id: str, rows, *, status=Status.ACCEPTED, review="none", checks=()) -> Fix:
    return Fix(
        element_id=element_id,
        kind=Kind.TABLE,
        status=status,
        review=review,
        confidence=0.9,
        table=TableFix(grid=grid(rows, source="model")) if rows is not None else None,
        checks=list(checks),
        calls=[call()],
    )


def test_model_mode_scores_the_proposal_and_counts_cost_and_unflagged_wrong_cells(
    gold_dir, detected, monkeypatch
):
    answers = {
        # speeds: the model repeats the misread, accepted with nothing to review
        "p2-e0": fix("p2-e0", SPEEDS_MISREAD),
        # units: right, accepted; the layout grid kept out; the false table kept as image
        "p1-e0": fix("p1-e0", UNITS),
        "p1-e1": fix("p1-e1", None, status=Status.FAILED, review="required"),
        "p1-e2": fix("p1-e2", None, status=Status.FAILED, review="required"),
    }
    answers["p1-e1"].reason = "table_not_rebuilt"
    answers["p1-e2"].reason = "table_not_rebuilt"
    rebuild = model_rebuild(answers)
    monkeypatch.setattr(table_step, "rebuild", rebuild)
    client = FakeVisionClient()
    report = T.run_tables_eval(
        config(
            gold_dir,
            FakeTableMedia(),
            mode="model",
            client=client,
            client_label="fake",
            styles=("grid",),
        )
    ).report
    assert all(on for _, on, _ in rebuild.seen)  # tables on for the step
    m = report["metrics"]
    assert report["mode"] == "model" and report["complete"]
    assert m["teds"] < 1.0 and m["teds_found"] < 1.0
    assert m["text_fidelity"]["wrong_cells_not_flagged"] == 1  # the gate's zero fails
    load = m["review_load"]
    assert load["required_decisions"] == 2 and load["kept_as_image"] == 2
    assert load["flags"]["wrong"] == 1 and load["flags"]["wrong_required"] == 0
    assert [w["gold_id"] for w in load["wrong_not_required"]] == ["openstax/physics/speeds/tab-000"]
    cost = report["cost"]
    assert cost["model_calls"] == 4 and cost["per_table_usd"] == pytest.approx(0.0007)
    assert cost["seconds_per_table_p50"] == pytest.approx(6.0)
    assert report["prompt_versions"] == {"table": "table-test"}
    assert report["models_used"] == ["gpt-6-luna"]


def test_model_mode_kept_as_image_is_found_but_scores_zero(gold_dir, detected, monkeypatch):
    answers = {
        "p2-e0": fix("p2-e0", None, status=Status.FAILED, review="required"),
        "p1-e0": fix("p1-e0", UNITS),
        "p1-e1": fix("p1-e1", None),
        "p1-e2": fix("p1-e2", None),
    }
    monkeypatch.setattr(table_step, "rebuild", model_rebuild(answers))
    report = T.run_tables_eval(
        config(
            gold_dir, FakeTableMedia(), mode="model", client=FakeVisionClient(), styles=("grid",)
        )
    ).report
    m = report["metrics"]
    assert m["detection_recall"] == 1.0  # found by layout, matched on its local grid
    assert m["teds_found"] == pytest.approx(0.5)  # the image scores 0, units 1


def test_a_budget_stop_reports_a_partial_run(gold_dir, detected, monkeypatch):
    def rebuild(element, png, **_):
        raise BudgetExceeded("job_usd", 0.01)

    monkeypatch.setattr(table_step, "rebuild", rebuild)
    budget = JobBudget(InMemorySpendGuard(cap_usd=0.01), max_usd=0.01, max_calls=10)
    report = T.run_tables_eval(
        config(gold_dir, FakeTableMedia(), mode="model", client=FakeVisionClient(), budget=budget)
    ).report
    assert not report["complete"] and report["stopped_reason"] == "budget_job_usd"
    assert report["n"] == 0 and report["cost"]["budget_spent_usd"] == 0


def test_a_step_that_breaks_is_a_failed_fix_not_a_broken_run(gold_dir, detected, monkeypatch):
    def rebuild(element, png, **_):
        raise RuntimeError("bug")

    monkeypatch.setattr(table_step, "rebuild", rebuild)
    report = T.run_tables_eval(
        config(
            gold_dir, FakeTableMedia(), mode="model", client=FakeVisionClient(), styles=("grid",)
        )
    ).report
    assert report["complete"]
    assert report["metrics"]["review_load"]["required_by_reason"] == {"internal_error": 4}


def test_markdown_summary_has_the_sections(gold_dir, detected):
    report = T.run_tables_eval(config(gold_dir, FakeTableMedia(), styles=("grid",))).report
    text = T.format_markdown(report)
    for heading in (
        "## Eval: tables, local mode",
        "### Headers",
        "### Review load",
        "### By style",
        "### Real documents",
        "Not run",
    ):
        assert heading in text


# ---------------------------------------------------------------- real documents


def test_real_document_boxes_precision_and_full_extent_recall(tmp_path, monkeypatch):
    real = tmp_path / "private"
    real.mkdir()
    (real / "manual.pdf").write_bytes(pdf_of("manual", ["a", "b", "c"]))
    labels = {
        "documents": [
            {
                "file": "manual.pdf",
                "tables": [
                    {"page": 0, "bbox": [50, 100, 400, 300]},  # found whole
                    {"page": 1, "bbox": [50, 100, 400, 500]},  # cut to its header row
                    {"page": 2, "bbox": [50, 100, 400, 300]},  # missed
                ],
            }
        ]
    }
    (real / "labels.json").write_text(json.dumps(labels), encoding="utf-8")

    def element(page, bbox, index):
        e = table_element(page, index, grid([["x"]], header_rows=0))
        return e.model_copy(update={"bbox": bbox})

    found = [
        element(0, (48, 98, 402, 302), 0),
        element(1, (50, 100, 400, 140), 1),  # partial: true table, not full extent
        element(2, (50, 600, 400, 700), 2),  # a figure called a table
    ]
    monkeypatch.setattr(
        layout,
        "analyze",
        lambda pdf, **_: layout.Layout(elements=found, page_count=3, pages=[0, 1, 2]),
    )
    monkeypatch.setattr(layout, "release", lambda pdf=None: None)
    out = T.real_document_metrics(real)
    assert out is not None
    assert (out["predicted_tables"], out["true_tables"], out["labelled_tables"]) == (3, 2, 3)
    assert out["precision"] == pytest.approx(2 / 3)
    assert out["full_extent_recall"] == pytest.approx(1 / 3)
    assert out["any_extent_recall"] == pytest.approx(2 / 3)
    assert "manual" not in json.dumps(out)  # counts only: no file names
    assert T.real_document_metrics(tmp_path) is None  # no labels: not run


def test_the_report_carries_the_real_document_section_when_asked(gold_dir, detected, tmp_path):
    real = tmp_path / "private"
    real.mkdir()
    (real / "labels.json").write_text(json.dumps({"documents": []}), encoding="utf-8")
    report = T.run_tables_eval(
        config(gold_dir, FakeTableMedia(), styles=("grid",), real_dir=real)
    ).report
    assert report["metrics"]["real_documents"]["documents"] == 0


def test_check_codes_on_a_fix_become_its_tier_reasons(gold_dir, detected, monkeypatch):
    failed = Check(name="table.cell_text", passed=False, code="cell_text_mismatch")
    answers = {
        "p2-e0": fix(
            "p2-e0", SPEEDS_MISREAD, status=Status.NEEDS_REVIEW, review="required", checks=[failed]
        ),
        "p1-e0": fix("p1-e0", UNITS),
        "p1-e1": fix("p1-e1", None),
        "p1-e2": fix("p1-e2", None),
    }
    monkeypatch.setattr(table_step, "rebuild", model_rebuild(answers))
    report = T.run_tables_eval(
        config(
            gold_dir, FakeTableMedia(), mode="model", client=FakeVisionClient(), styles=("grid",)
        )
    ).report
    load = report["metrics"]["review_load"]
    assert load["required_by_reason"] == {"cell_text_mismatch": 1}
    assert load["flags"]["flag_precision"] == 1.0 and load["flags"]["flag_recall"] == 1.0
    assert report["metrics"]["text_fidelity"]["wrong_cells_not_flagged"] == 0
