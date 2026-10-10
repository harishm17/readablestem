"""pipeline.tables: the detector's thread pin and quiet stdout, arbitration of proposed
boxes, the structure choice, filling cells with the PDF's words, and how layout claims
the tables it keeps (TABLE elements, captions, reading order, continued tables, rotated
pages, the probe in front of the detector).

Pages are generated with PyMuPDF. Arbitration is tested on proposals made here (no
detector); the layout tests run the real detector. The SLANet-plus model file is hidden
from this module (`no_model`) so results do not depend on whether a developer fetched
it; the tests that need it skip without it.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

import pymupdf
import pytest

from a11y_stem.core.types import Kind, TableCell, TableGrid, grid_problems
from a11y_stem.pipeline import layout, table_model, tables
from a11y_stem.pipeline.layout import LayoutError, analyze, release
from a11y_stem.pipeline.tables import Proposal, Slot

BODY = 11.0
FIXTURES = Path(__file__).parent / "fixtures" / "layout"
MODEL_PRESENT = table_model.model_path().is_file()


@pytest.fixture(autouse=True)
def no_model(tmp_path, monkeypatch):
    """No SLANet-plus file: grids come from the drawn rules or PyMuPDF (as in CI)."""
    monkeypatch.setenv(table_model.MODEL_DIR_ENV, str(tmp_path / "models"))
    tables.reset_models()
    release()
    yield
    tables.reset_models()
    release()


def pdf(*fills, width: float = 612, height: float = 792) -> bytes:
    doc = pymupdf.open()
    for fill in fills:
        fill(doc.new_page(width=width, height=height))
    return doc.tobytes()


def write(page: pymupdf.Page, x: float, y: float, text: str, size: float = BODY) -> None:
    page.insert_text((x, y), text, fontsize=size)


def grid_table(page: pymupdf.Page, rows: list[list[str]], *, x0=100.0, y0=150.0, widths=None,
               height=20.0, blank_rows: int = 0) -> tuple[float, float, float, float]:  # fmt: skip
    """A table ruled on every side of every cell, in display coordinates (on a page with
    /Rotate, drawn turned so that it reads upright); returns its box."""
    widths = widths or [90.0] * len(rows[0])
    turn = page.derotation_matrix
    xs = [x0]
    for w in widths:
        xs.append(xs[-1] + w)
    n = len(rows) + blank_rows
    ys = [y0 + i * height for i in range(n + 1)]
    for y in ys:
        page.draw_line(pymupdf.Point(xs[0], y) * turn, pymupdf.Point(xs[-1], y) * turn, width=0.6)
    for x in xs:
        page.draw_line(pymupdf.Point(x, ys[0]) * turn, pymupdf.Point(x, ys[-1]) * turn, width=0.6)
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            point = pymupdf.Point(xs[c] + 5, ys[r] + 14) * turn
            page.insert_text(point, text, fontsize=BODY, rotate=page.rotation)
    return xs[0], ys[0], xs[-1], ys[-1]


def view_of(data: bytes, number: int = 0) -> tables._PageView:
    doc = pymupdf.open(stream=data, filetype="pdf")
    page = doc[number]
    return tables._PageView.of(page, layout._page_rows(page), layout._page_paths(page))


def proposal(box) -> Proposal:
    return Proposal(tuple(float(v) for v in box), [])  # ty: ignore[invalid-argument-type]


PENDULUM = [
    ["Length", "Time", "Period"],
    ["0.20", "8.98", "0.898"],
    ["0.40", "12.69", "1.269"],
    ["0.60", "15.55", "1.555"],
]
PROSE = (
    "A cart rolls down a ramp and the photogate records the time it takes to pass.",
    "The table below lists what each group measured during the first session.",
)


# ---------------------------------------------------------------- the detector


def test_cpu_allowance_follows_affinity_and_the_cgroup_quota(tmp_path, monkeypatch):
    monkeypatch.setattr(tables.os, "sched_getaffinity", lambda pid: {0, 1, 2, 3})
    cpu_max = tmp_path / "cpu.max"
    monkeypatch.setattr(tables, "_CGROUP_V2", cpu_max)
    monkeypatch.setattr(tables, "_CGROUP_V1", (tmp_path / "quota", tmp_path / "period"))
    assert tables.cpu_allowance() == 4  # no cgroup files
    cpu_max.write_text("150000 100000\n")  # Docker --cpus=1.5
    assert tables.cpu_allowance() == 2
    cpu_max.write_text("max 100000\n")
    assert tables.cpu_allowance() == 4
    cpu_max.write_text("50000 100000\n")
    assert tables.cpu_allowance() == 1
    cpu_max.unlink()
    (tmp_path / "quota").write_text("100000")
    (tmp_path / "period").write_text("100000")
    assert tables.cpu_allowance() == 1
    (tmp_path / "quota").write_text("-1")
    assert tables.cpu_allowance() == 4


def test_the_layout_models_run_with_the_pinned_thread_settings():
    assert tables.load_detector()
    assert tables.LAYOUT_SESSIONS, "pymupdf.layout created no onnxruntime session"
    for session in tables.LAYOUT_SESSIONS:
        assert session.intra_op_threads == tables.cpu_allowance(), session
        assert session.inter_op_threads == 1, session
        assert session.spinning == "0", session


def test_other_onnxruntime_sessions_are_left_alone(tmp_path):
    import onnxruntime as ort

    tables.load_detector()
    assert getattr(ort.InferenceSession, "_a11y_pinned", False)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 3
    with pytest.raises(Exception):  # noqa: B017  (no model file: onnxruntime's own error)
        ort.InferenceSession(str(tmp_path / "other.onnx"), sess_options=options)
    assert options.intra_op_num_threads == 3


def test_what_pymupdf_prints_during_detection_is_dropped(capfd):
    other: list[str] = []
    with tables._quiet_stdout() as captured:
        print("unknown keyword: 'JaneDoeGrades'")
        pymupdf.message("table finder: 'Jane Doe 87%'")
        thread = threading.Thread(target=lambda: other.append(str(sys.stdout.write("x\n"))))
        thread.start()
        thread.join()
    out = capfd.readouterr().out
    assert "Jane" not in out and len(captured) == 2
    assert captured == ["", ""]  # counted, never kept
    assert out == "x\n"  # another thread still writes to the real stdout
    assert not isinstance(sys.stdout, tables._ThreadStdout)


def test_proposals_are_in_display_space_on_a_rotated_page():
    def upright(page):
        grid_table(page, PENDULUM)

    def turned(page):
        page.set_rotation(90)
        grid_table(page, PENDULUM)

    boxes = []
    for fill in (upright, turned):
        doc = pymupdf.open(stream=pdf(fill), filetype="pdf")
        found = tables.propose(doc[0])
        assert len(found) == 1
        boxes.append(found[0].bbox)
    assert boxes[1] == pytest.approx(boxes[0], abs=1.0)  # same place on the screen


# ---------------------------------------------------------------- arbitration


def test_a_ruled_numeric_table_is_kept():
    box = None

    def fill(page):
        nonlocal box
        box = grid_table(page, PENDULUM)

    view = view_of(pdf(fill))
    judgement = tables.judge(proposal(box), view)
    assert judgement.verdict == tables.TABLE
    assert tables.REASON_RULED in judgement.reasons
    assert judgement.columns == 3 and judgement.column_rules == 4


def test_an_unruled_aligned_grid_is_kept_on_text_alone():
    def fill(page):
        for r, row in enumerate(PENDULUM):
            for c, text in enumerate(row):
                write(page, 120 + 100 * c + (12 if r and c else 0), 160 + 16 * r, text)

    judgement = tables.judge(proposal((115, 148, 420, 215)), view_of(pdf(fill)))
    assert judgement.verdict == tables.TABLE
    assert judgement.reasons == [tables.REASON_TEXT_GRID]


def test_a_table_of_contents_with_dot_leaders_is_dropped():
    def fill(page):
        for i, title in enumerate(["Motion", "Forces", "Energy", "Momentum", "Waves"]):
            write(page, 100, 160 + 16 * i, f"{i + 1}  {title} " + ". " * 30)
            write(page, 480, 160 + 16 * i, str(3 + 12 * i))

    judgement = tables.judge(proposal((95, 148, 500, 230)), view_of(pdf(fill)))
    assert judgement.verdict == "toc"


def test_bullet_columns_are_a_list_not_a_table():
    def fill(page):
        for i in range(4):
            write(page, 100, 160 + 16 * i, f"• Step {i + 1} of the setup")
            write(page, 330, 160 + 16 * i, f"• Check {i + 1} before you start")

    judgement = tables.judge(proposal((95, 148, 520, 215)), view_of(pdf(fill)))
    assert judgement.verdict == "list"


def test_a_diagram_with_short_labels_is_not_a_table():
    def fill(page):
        page.draw_circle((200, 250), 60, width=1)
        page.draw_circle((340, 250), 40, width=1)
        page.draw_line((200, 250), (340, 250), width=1)
        for x, label in ((195, "A"), (335, "B"), (265, "r"), (190, "O"), (330, "C")):
            write(page, x, 240 if label in "AB" else 270, label)

    judgement = tables.judge(proposal((140, 190, 380, 310)), view_of(pdf(fill)))
    assert judgement.verdict in {"labels", "figure"}


def test_a_drawing_with_some_text_in_it_stays_a_figure():
    def fill(page):
        for i in range(4):
            page.draw_circle((150 + 70 * i, 200), 25, width=1)
            write(page, 135 + 70 * i, 250, f"Mass {i + 1}")
            write(page, 135 + 70 * i, 265, f"{2 * i + 1} kg")

    judgement = tables.judge(proposal((120, 170, 420, 275)), view_of(pdf(fill)))
    assert judgement.verdict == "figure"


def test_prose_in_a_box_has_no_grid():
    def fill(page):
        for i, line in enumerate(PROSE):
            write(page, 100, 160 + 15 * i, line)

    judgement = tables.judge(proposal((95, 148, 520, 190)), view_of(pdf(fill)))
    assert judgement.verdict == "no_grid"


def test_a_blank_worksheet_cut_to_its_header_row_is_extended_along_its_rules():
    box = None

    def fill(page):
        nonlocal box
        write(page, 100, 120, "Record your measurements in the table below.")
        box = grid_table(page, [["Trial", "Length (m)", "Time (s)"]], blank_rows=5)
        write(page, 100, 300, "Then work out the period of each trial from its time.")

    view = view_of(pdf(fill))
    assert box is not None
    header_only = (box[0], box[1], box[2], box[1] + 20)
    judgement = tables.judge(proposal(header_only), view)
    assert judgement.verdict == tables.TABLE
    assert tables.REASON_EXTENDED in judgement.reasons
    assert judgement.bbox == pytest.approx(box, abs=1.0)


def test_extension_stops_before_running_text_and_captions():
    box = None

    def fill(page):
        nonlocal box
        box = grid_table(page, [["Trial", "Length (m)", "Time (s)"], ["1", "0.20", "8.98"]])
        # Column rules run on below the table through a paragraph (a page border).
        for x in (box[0], box[2]):
            page.draw_line((x, box[3]), (x, box[3] + 60), width=0.6)
        write(page, 105, box[3] + 20, "The values agree within one per cent of each other, as")
        write(page, 105, box[3] + 34, "expected from the length of the string and the time.")

    view = view_of(pdf(fill))
    judgement = tables.judge(proposal(box), view)
    assert judgement.verdict == tables.TABLE
    assert box is not None and judgement.bbox[3] == pytest.approx(box[3], abs=2.0)


# ---------------------------------------------------------------- structure and fill


def words(*items: tuple[float, float, float, float, str, int]) -> list[tables._Word]:
    return [tables._Word(*item) for item in items]


def two_by_two() -> list[Slot]:
    return [
        Slot(0, 0, bbox=(0, 0, 50, 20)),
        Slot(0, 1, bbox=(50, 0, 100, 20)),
        Slot(1, 0, bbox=(0, 20, 50, 40)),
        Slot(1, 1, bbox=(50, 20, 100, 40)),
    ]


def test_fill_puts_each_word_in_the_cell_it_lies_in():
    grid = tables.fill(
        two_by_two(),
        words(
            (5, 5, 20, 15, "Time", 0),
            (55, 5, 80, 15, "Speed", 0),
            (5, 25, 15, 35, "1", 1),
            (55, 25, 70, 35, "v0", 1),  # a subscript glyph stays in its word, and cell
            (72, 25, 90, 35, "m/s", 1),
        ),
        header_rows=1,
    )
    assert grid is not None and not grid_problems(grid)
    assert [c.text for c in sorted(grid.cells, key=lambda c: (c.row, c.col))] == [
        "Time", "Speed", "1", "v0 m/s"
    ]  # fmt: skip
    assert grid.header_rows == 1 and grid.source == "local"
    assert [c.header for c in grid.cells if c.row == 0] == ["col", "col"]


def test_fill_rejoins_a_word_hyphenated_across_lines_in_a_cell():
    grid = tables.fill(
        [Slot(0, 0, bbox=(0, 0, 100, 40))],
        words((5, 5, 60, 15, "accelera-", 0), (5, 20, 40, 30, "tion", 1)),
    )
    assert grid is not None and grid.cells[0].text == "acceleration"


def test_fill_makes_holes_into_empty_cells_and_refuses_overlaps():
    spanning = [Slot(0, 0, col_span=2, bbox=(0, 0, 100, 20)), Slot(1, 0, bbox=(0, 20, 50, 40))]
    grid = tables.fill(spanning, [])
    assert grid is not None and not grid_problems(grid)
    assert (grid.rows, grid.cols, len(grid.cells)) == (2, 2, 3)
    overlapping = [Slot(0, 0, col_span=2, bbox=(0, 0, 100, 20)), Slot(0, 1, bbox=(50, 0, 100, 20))]
    assert tables.fill(overlapping, []) is None
    assert tables.fill([Slot(0, 70, bbox=(0, 0, 1, 1))], []) is None  # past MAX_TABLE_COLS


def test_a_label_and_value_table_gets_no_header_row():
    grid = tables.fill(
        two_by_two(),
        words((5, 5, 20, 15, "Mass", 0), (55, 5, 80, 15, "2 kg", 0)),
        header_rows=1,
    )
    assert grid is not None and grid.header_rows == 0
    assert all(c.header is None for c in grid.cells)


def test_misfit_prefers_the_grid_that_keeps_columns_apart():
    def fill(page):
        for r, row in enumerate(PENDULUM):
            for c, text in enumerate(row):
                write(page, 120 + 100 * c, 160 + 16 * r, text)

    view = view_of(pdf(fill))
    text = tables._text_grid(view, (115, 148, 420, 215), [])
    rows = [(148 + 16 * r, 164 + 16 * r) for r in range(4)]
    right = [Slot(r, c, bbox=(115 + 100 * c, y0, 215 + 100 * c, y1))
             for r, (y0, y1) in enumerate(rows) for c in range(3)]  # fmt: skip
    merged = [Slot(r, c, bbox=(115 + 100 * c, y0, 215 + 200 * c, y1))
              for r, (y0, y1) in enumerate(rows) for c in range(2)]  # fmt: skip
    assert tables.misfit(right, text) == 0
    assert tables.misfit(merged, text) == 4  # one extra cell-on-a-line per row


def test_the_drawn_grid_gives_spans_where_a_rule_is_missing():
    def fill(page):
        xs, ys = (100, 200, 300, 400), (150, 170, 190, 210)
        for y in ys:
            page.draw_line((100, y), (400, y), width=0.6)
        page.draw_line((100, 150), (100, 210), width=0.6)
        page.draw_line((400, 150), (400, 210), width=0.6)
        page.draw_line((200, 150), (200, 210), width=0.6)
        page.draw_line((300, 170), (300, 210), width=0.6)  # not in the header row
        write(page, 105, 164, "Trial")
        write(page, 230, 164, "Measured time (s)")
        assert xs and ys

    view = view_of(pdf(fill))
    slots = tables._ruled_slots(view, (100, 150, 400, 210), BODY)
    assert slots is not None
    spans = {(s.row, s.col): (s.row_span, s.col_span) for s in slots}
    assert spans[(0, 1)] == (1, 2) and (0, 2) not in spans
    assert len(slots) == 8


def test_a_two_column_grid_of_prose_is_a_layout_grid():
    def grid(texts: list[list[str]]) -> TableGrid:
        cells = [
            TableCell(row=r, col=c, text=t)
            for r, row in enumerate(texts)
            for c, t in enumerate(row)
        ]
        return TableGrid(rows=len(texts), cols=len(texts[0]), cells=cells)

    terms = [["Velocity", PROSE[0]], ["Momentum", PROSE[1]]]
    assert tables.is_layout_grid(grid(terms))
    assert tables.is_layout_grid(grid([["Only one column"], ["of text"]]))
    assert not tables.is_layout_grid(grid([["Mass", "2 kg"], ["Speed", "3 m/s"]]))
    assert not tables.is_layout_grid(grid([["1", PROSE[0]], ["2", PROSE[1]]]))
    assert not tables.is_layout_grid(grid([[*r, "9.8"] for r in terms]))


KEY_TERMS = [
    ["displacement", "distance", "kinematics", "magnitude"],
    ["position", "reference frame", "scalar", "vector"],
]


def test_a_ruled_grid_of_key_terms_stays_text_unless_it_has_a_table_caption():
    # OpenStax's ruled "Key Terms" box: claimed as a table, its words became an image and a
    # required decision (flags off) or "not a data table" (flags on).
    def terms(page):
        write(page, 72, 100, PROSE[0])
        grid_table(page, KEY_TERMS, y0=130)
        write(page, 72, 200, PROSE[1])

    result = analyze(pdf(terms))
    assert [e.kind for e in result.elements if e.kind == Kind.TABLE] == []
    # Its terms are a list, in reading order: not run together in one paragraph.
    items = [e.text for e in result.elements if e.kind == Kind.LIST_ITEM]
    assert items == [term for row in KEY_TERMS for term in row]

    def captioned(page):
        write(page, 100, 120, "Table 1. Terms of motion")
        grid_table(page, KEY_TERMS, y0=130)

    tables_found = [e for e in analyze(pdf(captioned)).elements if e.kind == Kind.TABLE]
    assert [e.caption for e in tables_found] == ["Table 1. Terms of motion"]


TERMS = [
    ["accuracy", "ampere", "constant", "conversion factor"],
    ["derived units", "kilogram", "method of adding percents", ""],
]


def test_a_term_grid_is_written_as_a_list_with_no_table_and_no_decision():
    # The trailing empty cell (the terms ran out) and a four-word term no longer make it
    # a data table: OpenStax prints these in about half of the table gold's pages.
    def terms(page):
        write(page, 72, 100, PROSE[0])
        grid_table(page, TERMS, x0=72, y0=130, widths=[125.0] * 4)
        write(page, 72, 200, PROSE[1])

    result = analyze(pdf(terms))
    assert [e for e in result.elements if e.kind == Kind.TABLE] == []
    items = [e.text for e in result.elements if e.kind == Kind.LIST_ITEM]
    assert items == [term for row in TERMS for term in row if term]
    order = [e.kind for e in result.elements]
    first = order.index(Kind.LIST_ITEM)
    assert order[first - 1] == Kind.PARAGRAPH and order[first + len(items)] == Kind.PARAGRAPH


def test_what_a_term_grid_is():
    def grid(texts: list[list[str]]) -> TableGrid:
        cells = [
            TableCell(row=r, col=c, text=t)
            for r, row in enumerate(texts)
            for c, t in enumerate(row)
        ]
        return TableGrid(rows=len(texts), cols=len(texts[0]), cells=cells)

    assert tables.is_term_grid(grid(TERMS))
    assert tables.is_term_grid(grid(KEY_TERMS))
    assert tables.is_term_grid(grid([["W\u207b boson", "pion", "graviton"]]))  # a sign, no digit
    # Data: a digit, a symbol or unit column, a category repeated, two columns (label |
    # value), a gap inside the grid, a whole empty row, too many rows, a long cell.
    assert not tables.is_term_grid(grid([["Trial", "Length", "Time"], ["first", "0.20 m", "x"]]))
    assert not tables.is_term_grid(grid([["Element", "Symbol", "State"], ["Hydrogen", "H", "gas"]]))
    assert not tables.is_term_grid(grid([["Type", "Image", "Sign"], ["convex", "real", "real"]]))
    assert not tables.is_term_grid(grid([["Quantity", "Unit"], ["Length", "metre"]]))
    assert not tables.is_term_grid(grid([["alpha", "", "gamma"], ["delta", "omega", "sigma"]]))
    assert not tables.is_term_grid(grid([["alpha", "beta", "gamma"], ["", "", ""]]))
    assert not tables.is_term_grid(grid([[f"term{n} one", "beta", "gamma"] for n in "abcdefg"]))
    long = "a definition of several words"
    assert not tables.is_term_grid(grid([["alpha", "beta", long]]))


def test_a_term_grid_with_a_table_caption_is_data():
    def captioned(page):
        write(page, 72, 120, "Table 4. Terms used in this lab")
        grid_table(page, TERMS, x0=72, y0=130, widths=[125.0] * 4)

    found = [e for e in analyze(pdf(captioned)).elements if e.kind == Kind.TABLE]
    assert len(found) == 1 and found[0].table is not None and found[0].table.is_data


def test_a_word_grid_has_no_digit_and_short_cells():
    def grid(texts: list[list[str]]) -> TableGrid:
        cells = [
            TableCell(row=r, col=c, text=t)
            for r, row in enumerate(texts)
            for c, t in enumerate(row)
        ]
        return TableGrid(rows=len(texts), cols=len(texts[0]), cells=cells)

    assert tables.is_word_grid(grid(KEY_TERMS))
    assert not tables.is_word_grid(grid([["Trial", "Length"], ["first", "0.20 m"]]))
    assert not tables.is_word_grid(grid([["Term", "Meaning"], ["speed", "how fast it goes"]]))
    assert not tables.is_word_grid(grid([["a", "b"]] * 7))  # more rows than a box of terms
    assert not tables.is_word_grid(grid([["Trial", "Length"], ["", ""]]))  # a blank worksheet


def test_without_the_model_file_tables_still_get_a_grid_and_one_warning(caplog):
    def fill(page):
        grid_table(page, PENDULUM)

    data = pdf(fill)
    with caplog.at_level(logging.WARNING, logger="a11y_stem.pipeline.tables"):
        first = analyze(data)
        release()
        analyze(data)
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == [
        "table structure model not found; tables use PyMuPDF's grid "
        "(fetch it with: make table-model)"
    ]
    table = next(e for e in first.elements if e.kind == Kind.TABLE)
    assert table.table is not None and (table.table.rows, table.table.cols) == (4, 3)
    assert tables.REASON_SLANET not in table.reasons


def test_a_model_file_with_the_wrong_checksum_is_not_loaded(caplog):
    path = table_model.model_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not the model")
    with caplog.at_level(logging.WARNING, logger="a11y_stem.pipeline.tables"):
        assert tables._structurer() is None
    assert any("wrong checksum" in r.getMessage() for r in caplog.records)


@pytest.mark.skipif(not MODEL_PRESENT, reason="SLANet-plus model not fetched (make table-model)")
def test_slanet_reads_rows_columns_and_a_spanning_header(monkeypatch):
    monkeypatch.delenv(table_model.MODEL_DIR_ENV)
    tables.reset_models()

    def fill(page):
        grid_table(page, [["Trial", "Length", "Time"], *PENDULUM[1:]])

    doc = pymupdf.open(stream=pdf(fill), filetype="pdf")
    crop = tables._crop(doc[0], (100, 150, 370, 230))
    assert crop is not None
    found = tables.structure(crop[0])
    assert found is not None
    assert max(s.row + s.row_span for s in found.slots) == 4
    assert max(s.col + s.col_span for s in found.slots) == 3


# ---------------------------------------------------------------- layout claims tables


def captioned_page(page: pymupdf.Page) -> None:
    write(page, 72, 100, PROSE[0])
    write(page, 72, 115, PROSE[1])
    write(page, 100, 140, "Table 1. Pendulum measurements")
    grid_table(page, [["Length", "Ten swings", "Period"], *PENDULUM[1:]], y0=150)
    write(page, 72, 260, "The values agree within one per cent.")
    grid_table(page, [["Trial", "Length (m)", "Time (s)"]], y0=300, blank_rows=4)
    write(page, 100, 415, "Table 2: Record your data here.")
    write(page, 72, 450, "Then work out the period of each trial.")


def test_raised_glyphs_stay_exponents_in_the_cells():
    # Text layers flatten scripts: 10^5 would read "105" and 10^-18 "10-18". The grid read
    # from the PDF keeps them raised.
    def fill(page):
        write(page, 100, 140, "Table 1. Orders of magnitude")
        grid_table(
            page, [["Quantity", "Value", "Unit"], ["Pressure", "", "Pa"], ["Quark", "", "m"]]
        )
        for y, base, script in ((184, "1.01\u00d710", "5"), (204, "10", "-18")):
            write(page, 195, y, base)
            width = pymupdf.get_text_length(base, fontsize=BODY)
            write(page, 195 + width + 0.5, y - 4.5, script, size=7.0)

    table = next(e for e in analyze(pdf(fill)).elements if e.kind == Kind.TABLE)
    assert table.table is not None
    texts = {(c.row, c.col): c.text for c in table.table.cells}
    assert (texts[(1, 1)], texts[(2, 1)]) == ("1.01\u00d710\u2075", "10\u207b\u00b9\u2078")
    assert texts[(1, 2)] == "Pa"


def test_layout_emits_table_elements_in_reading_order_with_their_captions():
    result = analyze(pdf(captioned_page))
    kinds = [e.kind for e in result.elements]
    assert kinds == [Kind.PARAGRAPH, Kind.TABLE, Kind.PARAGRAPH, Kind.TABLE, Kind.PARAGRAPH]
    first, second = (e for e in result.elements if e.kind == Kind.TABLE)
    assert first.caption == "Table 1. Pendulum measurements"  # above
    assert second.caption == "Table 2: Record your data here."  # below
    assert first.table is not None and first.table.caption == first.caption
    assert first.table.source == "local" and first.table.header_rows == 1
    assert [c.text for c in first.table.cells if c.row == 0] == ["Length", "Ten swings", "Period"]
    assert second.table is not None and (second.table.rows, second.table.cols) == (5, 3)
    assert all(c.is_empty for c in second.table.cells if c.row > 0)
    texts = " ".join(e.text or "" for e in result.elements if e.kind == Kind.PARAGRAPH)
    assert "Table 1" not in texts and "Table 2" not in texts and "12.69" not in texts


def test_a_blank_worksheet_and_unruled_numbers_become_tables_not_loose_cells():
    def fill(page):
        write(page, 72, 100, PROSE[0])
        grid_table(page, [["Trial", "Length (m)", "Time (s)"]], y0=130, blank_rows=4)
        write(page, 72, 250, PROSE[1])
        for r, row in enumerate(PENDULUM[1:]):  # numbers in aligned columns, no rules
            for c, text in enumerate(row):
                write(page, 150 + 90 * c, 280 + 16 * r, text)

    elements = analyze(pdf(fill)).elements
    assert [e.kind for e in elements] == [Kind.PARAGRAPH, Kind.TABLE, Kind.PARAGRAPH, Kind.TABLE]
    blank, numbers = (e for e in elements if e.kind == Kind.TABLE)
    assert blank.table is not None and (blank.table.rows, blank.table.cols) == (5, 3)
    assert numbers.table is not None and (numbers.table.rows, numbers.table.cols) == (3, 3)


def test_header_cells_are_not_headings_and_rules_are_not_figures():
    result = analyze(pdf(captioned_page))
    assert not [e for e in result.elements if e.kind in (Kind.HEADING, Kind.FIGURE, Kind.EQUATION)]


def test_a_table_at_the_top_of_the_next_page_with_the_same_columns_is_marked_continued():
    def first(page):
        write(page, 72, 100, PROSE[0])
        grid_table(page, PENDULUM, y0=600)

    def second(page):
        grid_table(page, [["0.80", "17.94", "1.794"], ["1.00", "20.06", "2.006"]], y0=72)
        write(page, 72, 140, PROSE[1])

    result = analyze(pdf(first, second))
    found = [e for e in result.elements if e.kind == Kind.TABLE]
    assert [e.page for e in found] == [0, 1]
    assert tables.REASON_CONTINUED not in found[0].reasons
    assert tables.REASON_CONTINUED in found[1].reasons


def test_a_table_on_a_rotated_page_is_found_in_display_space():
    def fill(page):
        page.set_rotation(90)  # a landscape page: 792 x 612 on the screen
        write_turned(page, 72, 100, PROSE[0])
        grid_table(page, PENDULUM, y0=150)

    result = analyze(pdf(fill))
    assert result.page_sizes == [(792.0, 612.0)]
    table = next(e for e in result.elements if e.kind == Kind.TABLE)
    assert table.table is not None and (table.table.rows, table.table.cols) == (4, 3)
    assert "12.69" in {c.text for c in table.table.cells}
    assert table.bbox == pytest.approx((100, 150, 370, 230), abs=3.0)


def write_turned(page: pymupdf.Page, x: float, y: float, text: str) -> None:
    point = pymupdf.Point(x, y) * page.derotation_matrix
    page.insert_text(point, text, fontsize=BODY, rotate=page.rotation)


def test_detection_only_runs_on_pages_the_probe_passed(monkeypatch):
    calls: list[int] = []
    real = tables.propose
    monkeypatch.setattr(tables, "propose", lambda page: calls.append(page.number) or real(page))
    data = (FIXTURES / "damaged_render_abort.pdf").read_bytes()
    with pytest.raises(LayoutError):
        analyze(data)
    assert calls == []


def test_table_detection_logs_no_document_text(caplog):
    with caplog.at_level(logging.DEBUG):
        analyze(pdf(captioned_page))
    logged = " ".join(r.getMessage() for r in caplog.records)
    for text in ("Pendulum", "12.69", "Length", "Record your data"):
        assert text not in logged


def test_a_failure_in_table_detection_leaves_the_page_as_it_was(monkeypatch, caplog):
    def broken(proposal, view):
        raise ZeroDivisionError

    monkeypatch.setattr(tables, "judge", broken)
    with caplog.at_level(logging.WARNING, logger="a11y_stem.pipeline.tables"):
        result = analyze(pdf(captioned_page))
    assert Kind.TABLE not in {e.kind for e in result.elements}
    assert "12.69" in " ".join(e.text or "" for e in result.elements)
    assert [r.getMessage() for r in caplog.records] == [
        "table detection failed on a page: ZeroDivisionError"
    ]
