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
from pathlib import Path

import pymupdf
import pytest

from readablestem.core.types import Kind, TableCell, TableGrid, grid_problems
from readablestem.pipeline import layout, table_model, tables
from readablestem.pipeline.assemble import render_html
from readablestem.pipeline.layout import LayoutError, analyze, release
from readablestem.pipeline.tables import Proposal, Slot

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


def test_a_table_drawn_with_filled_border_boxes_is_a_table_and_a_chart_stays_a_figure():
    # Word draws cell borders as thin filled rectangles, and a Word page paints them as one
    # drawing: it was read as the bars of a chart (SW37).
    def bordered(page):
        shape = page.new_shape()
        for r in range(5):
            shape.draw_rect(pymupdf.Rect(100, 150 + 22 * r - 0.5, 340, 150 + 22 * r + 0.5))
        for c in range(4):
            shape.draw_rect(pymupdf.Rect(100 + 80 * c - 0.5, 150, 100 + 80 * c + 0.5, 238))
        for r in range(5):  # and a small square at every crossing, which is not a bar
            for c in range(4):
                shape.draw_rect(
                    pymupdf.Rect(
                        100 + 80 * c - 0.25,
                        150 + 22 * r - 0.25,
                        100 + 80 * c + 0.25,
                        150 + 22 * r + 0.25,
                    )
                )
        shape.finish(fill=(0, 0, 0), color=None)
        shape.commit()
        for r in range(4):
            for c in range(3):
                write(page, 106 + 80 * c, 165 + 22 * r, f"R{r}C{c} {r * 3 + c}")

    def chart(page):
        write(page, 72, 100, "Rainfall by month, in millimetres.")
        for k, height in enumerate((40, 90, 65, 120)):
            page.draw_rect(
                pymupdf.Rect(120 + 50 * k, 400 - height, 150 + 50 * k, 400), fill=(0.2, 0.3, 0.8)
            )
        page.draw_line((110, 400), (330, 400), width=0.8)
        page.draw_line((110, 270), (110, 400), width=0.8)

    def scatter(page):
        # Square markers of the same size, none on a rule: still a plot.
        write(page, 72, 100, "Rainfall by month, in millimetres.")
        page.draw_line((110, 400), (330, 400), width=0.8)
        page.draw_line((110, 270), (110, 400), width=0.8)
        for k in range(14):
            x, y = 130 + 14 * k, 380 - 7 * ((k * 5) % 9)
            page.draw_rect(pymupdf.Rect(x, y, x + 0.5, y + 0.5), fill=(0, 0, 0), color=None)

    def kinds(fill):
        return [e.kind for e in analyze(pdf(fill)).elements]

    assert kinds(bordered).count(Kind.TABLE) == 1
    assert Kind.FIGURE not in kinds(bordered)
    assert Kind.FIGURE in kinds(chart) and Kind.TABLE not in kinds(chart)
    assert Kind.TABLE not in kinds(scatter)


def test_an_unruled_aligned_grid_is_kept_on_text_alone():
    def fill(page):
        for r, row in enumerate(PENDULUM):
            for c, text in enumerate(row):
                write(page, 120 + 100 * c + (12 if r and c else 0), 160 + 16 * r, text)

    judgement = tables.judge(proposal((115, 148, 420, 215)), view_of(pdf(fill)))
    assert judgement.verdict == tables.TABLE
    assert judgement.reasons == [tables.REASON_TEXT_GRID]


def loose_numbers(caption: bool, rows: int = 7) -> bytes:
    """Rows of short cells whose left edges wander (a share of a column's cells line up), with a
    "Table 1" line over them or not."""
    offsets = [0, 0, 6, 12, 6, 0, 12]

    def fill(page):
        if caption:
            write(page, 100, 130, "Table 1. Measurements by group")
        for c, head in enumerate(["Group", "Mass", "Length", "Time"]):
            write(page, 100 + 90 * c, 160, head)
        for r, name in enumerate(["Ann", "Bob", "Cy", "Di", "Ed", "Flo", "Gus"][:rows]):
            for c in range(4):
                text = name if c == 0 else f"{r + c}.{r * c + 3}"
                write(page, 100 + 90 * c + (offsets[(r + c) % 7] if c else 0), 176 + 16 * r, text)

    return pdf(fill)


def test_a_table_line_over_loosely_aligned_columns_makes_it_a_table():
    # A "Table N" line says so, over columns that mostly line up.
    box = (95, 150, 490, 176 + 16 * 7)
    assert tables.judge(proposal(box), view_of(loose_numbers(True))).verdict == tables.TABLE


def labelled_groups(groups: int, rows: int = 4, paragraph: int = 0, ruled: bool = False) -> bytes:
    """Course rows under group labels that run across the first columns (nothing is ruled), and
    the lines of a paragraph under them. `ruled`: a booktabs table under a "Table 4." line, the
    first two groups each under a panel title that runs across every column."""
    panels = ["Panel A: courses that every student of the first year must take and pass"]
    panels += ["Panel B: courses that a student of the second year may choose between freely"]

    def fill(page):
        if ruled:
            write(page, 100, 143, "Table 4. Courses by group and kind")
            page.draw_line((100, 150), (480, 150), width=0.6)
            page.draw_line((100, 165), (480, 165), width=0.6)
        for x, head in ((100, "Code"), (190, "Title"), (420, "Kind")):
            write(page, x, 160, head)
        y = 176
        titles = ["Exercise and Health", "Nutrition in the Lifecycle", "Foundations in Nutrition"]
        for g in range(groups):
            if ruled and g < len(panels):
                write(page, 100, y, panels[g])
                y += 16
            write(page, 100, y, "Foundation Courses")
            y += 16
            for r in range(rows):
                write(page, 100, y, f"BIOL {1400 + r * 10}")
                write(page, 190, y, [*titles, "Sports Psychology"][r])
                write(page, 420, y, ["Core", "Elective"][r % 2])
                y += 16
        for _ in range(paragraph):
            text = "Every course in these groups counts toward the degree once a student has passed"
            write(page, 100, y, text)
            y += 16
        if ruled:
            page.draw_line((100, y - 10), (480, y - 10), width=0.6)

    return pdf(fill)


def nested_list_with_blanks() -> bytes:
    """ "b." and, one level in, "i." to "v." (set flush right) before items that end in a blank
    line to write on; the line over them runs across the strip between the labels and the items."""

    def fill(page):
        write(page, 92.4, 160, "b.", 8.25)
        write(page, 107.6, 160, "Identify the following values:", 8.25)
        items = ["Lowest value for", "Highest value for", "Height of the rectangle:"]
        items += ["Label for x-axis (words):", "Label for y-axis (words):"]
        for r, item in enumerate(items):
            y = 172 + 12 * r
            label = ["i.", "ii.", "iii.", "iv.", "v."][r]
            write(page, 117.6 - pymupdf.get_text_length(label, fontsize=8.25), y, label, 8.25)
            write(page, 125.6, y, f"{item} _______", 8.25)

    return pdf(fill)


def algorithm_with_comments(caption_below: bool) -> bytes:
    """A frame of numbered steps with a comment column at the right edge, one long step running
    across the strip before it, and the "Table" line of the next table under the frame."""

    def fill(page):
        write(page, 49, 262, "Algorithm 5 Establish the base.", 10)
        page.draw_line((49, 268), (563, 268), width=0.6)
        long_step = "z = Score(d, k); with the scores kept per group and reset after each pass"
        steps = [(0, "Input: Xs[..], Ys[..]", "available inputs")]
        steps += [(0, "Output: Z[..]", "the result")]
        steps += [(0, "function Make(data)", None), (1, "for each d in data do", None)]
        steps += [(2, "for each k in keys do", "see Table X"), (3, long_step, None)]
        steps += [(3, "u = Merge(z, u)", "merged"), (2, "end for", None), (1, "end for", None)]
        steps += [(1, "return z", "done")]
        for i, (level, step, comment) in enumerate(steps):
            y = 282 + 13 * i
            write(page, 54.7 if i < 9 else 50.7, y, f"{i + 1}:", 10)
            write(page, 66 + 15 * level, y, step, 10)
            if comment:
                write(page, 420, y, f"▷ {comment}", 10)
        end = 282 + 13 * len(steps)
        page.draw_line((49, end), (563, end), width=0.6)
        if caption_below:
            write(page, 230, end + 26, "TABLE IX. Results by group", 10)

    return pdf(fill)


@pytest.mark.parametrize(
    ("data", "box", "table"),
    [
        pytest.param(nested_list_with_blanks(), (90, 150, 250, 232), False, id="nested-list"),
        pytest.param(  # the "Table" line under it is the next table's
            algorithm_with_comments(True), (45, 250, 565, 420), False, id="algorithm"
        ),
        pytest.param(  # the paragraph under a table is not rows of it
            labelled_groups(3, 4, paragraph=3), (95, 150, 500, 470), False, id="paragraph"
        ),
        pytest.param(labelled_groups(3, 4), (95, 150, 500, 420), True, id="table-alone"),
        pytest.param(  # long panel titles across it: the rules and the caption still say table
            labelled_groups(3, 4, ruled=True), (98, 148, 482, 462), True, id="ruled-panels"
        ),
    ],
)
def test_a_strip_a_few_lines_cross_does_not_make_a_list_or_an_algorithm_a_table(data, box, table):
    assert (tables.judge(proposal(box), view_of(data)).verdict == tables.TABLE) == table


def test_labelled_lines_with_a_line_at_the_margin_are_not_a_table():
    # "Part B" over some questions crosses the strip after the labels "Q1" ... "Q6": with no
    # rule and no "Table N" line, two such columns are lines of text, not a table.
    questions = ["State the law of inertia.", "Give one example of it from the lab."]
    questions += ["Why does the cart slow down on the flat?", "Draw the forces on the cart."]
    questions += ["Which force does no work on the cart?", "Estimate the friction from your data."]

    def fill(page):
        write(page, 100, 110, "Answer every question below in full sentences, and show the working")
        y = 160
        for i, question in enumerate(questions):
            if i in (0, 3):
                write(page, 100, y, f"Part {'AB'[i // 3]}")
                y += 16
            write(page, 100, y, f"Q{i + 1}")
            write(page, 125, y, question)
            y += 16

    assert Kind.TABLE not in [e.kind for e in analyze(pdf(fill)).elements]


def test_a_table_of_marks_after_names_is_not_a_contents_list():
    # "x" is a roman numeral: marks set in several columns after names, on the first page of a
    # file, looked like page numbers that follow their titles and the table was dropped.
    def fill(page):
        write(page, 72, 100, "Table 3. Continued.")
        write(page, 72, 130, "Species")
        for x, head in ((250, "North"), (310, "South"), (370, "East"), (430, "West")):
            write(page, x, 130, head)
        page.draw_line((72, 118), (480, 118), width=0.6)
        page.draw_line((72, 138), (480, 138), width=0.6)
        for r, name in enumerate(
            ["Alpha bee", "Beta fly", "Gamma ant", "Delta moth", "Epsilon wasp", "Zeta gnat", "Eta"]
        ):
            write(page, 72, 156 + 12 * r, name)
            write(page, [250, 310, 370, 430][(r * 3) % 4] + 3, 156 + 12 * r, "x")
        page.draw_line((72, 156 + 12 * 7), (480, 156 + 12 * 7), width=0.6)

    found = analyze(pdf(fill)).elements
    assert Kind.TABLE in [e.kind for e in found]
    assert not [e for e in found if "contents entry" in e.reasons]


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


def test_a_numbered_question_beside_a_grid_is_not_a_row_of_it():
    terms = [
        ("Osmosis", "Movement of water"),
        ("Diffusion", "Spreading of particles"),
        ("Active transport", "Movement using energy"),
        ("Mitosis", "Division of a cell"),
    ]

    def rows(page, y0: float) -> None:
        for i, (term, meaning) in enumerate(terms):
            write(page, 82, y0 + 15 * i, f"___ {i + 1}. {term}")
            write(page, 230, y0 + 15 * i, "ABCD"[i] + ". " + meaning)

    def question_below(page):  # the grid, then item 8 with its answer space
        write(page, 64, 100, "7. Match each term with its definition.")
        rows(page, 120)
        write(page, 64, 192, "8. Explain why cells are small.")
        write(page, 500, 192, "(4 points)")
        page.draw_rect(pymupdf.Rect(82, 210, 380, 258), width=0.75)
        write(page, 64, 280, "9. Name the stages of mitosis.")

    def question_above(page):  # item 7 with its points over the grid, then item 8
        write(page, 64, 80, "6. Name the gases in air.")
        write(page, 64, 105, "7. Match each term with its definition.")
        write(page, 500, 105, "(4 points)")
        rows(page, 123)
        write(page, 64, 205, "8. Explain why cells are small.")

    # What stays out of the table, and what the table keeps (the rows alone are too few lines
    # to be a table of their own, but with the question over them taken out they are four).
    for fill, question, kept in (
        (question_below, "Explain", ""),
        (question_above, "Match each", "Osmosis"),
    ):
        for region_tree in (False, True):
            elements = analyze(pdf(fill), region_tree=region_tree).elements
            in_table = " ".join((e.text or "") for e in elements if e.kind == Kind.TABLE)
            assert question not in in_table and kept in in_table
            assert any(e.kind == Kind.LIST_ITEM and question in (e.text or "") for e in elements)


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


def test_without_the_model_file_tables_still_get_a_grid_and_one_warning(caplog):
    def fill(page):
        grid_table(page, PENDULUM)

    data = pdf(fill)
    with caplog.at_level(logging.WARNING, logger="readablestem.pipeline.tables"):
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
    with caplog.at_level(logging.WARNING, logger="readablestem.pipeline.tables"):
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


def test_raised_glyphs_stay_exponents_in_the_cells_and_the_caption():
    # Text layers flatten scripts: 10^5 would read "105" and 10^-18 "10-18". The grid read
    # from the PDF keeps them raised, and so does the caption ("CP^3" is not "CP3").
    def fill(page):
        caption = "Table 1. Orders of magnitude for CP"
        write(page, 100, 140, caption)
        write(page, 100 + pymupdf.get_text_length(caption, fontsize=BODY) + 0.5, 136, "3", size=7.0)
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
    assert table.caption == "Table 1. Orders of magnitude for CP\u00b3"
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 20), False)
    html = render_html(
        title="T", lang="en", elements=[table], fixes=[], crops={table.id: pixmap.tobytes("png")}
    )
    assert "CP\u00b3</figcaption>" in html


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


def test_a_part_that_goes_on_without_the_header_printed_once_is_marked_continued():
    # The header is wider than the figures under it, so the first part's columns are cut
    # further out than the rows that go on, with no header, on the next page.
    xs = (72, 190, 300, 430)

    def rows(page, y, first, count):
        for n in range(first, first + count):
            for x, text in zip(xs, (f"S{n:03d}", "24.32", "54.3", "7.092"), strict=True):
                write(page, x, y, text)
            y += 14

    def first(page):
        write(page, 72, 60, "Intro text before the table, to keep it off the top.")
        write(page, 72, 100, "Table 1. Measured densities")
        for x, text in zip(
            xs, ("Sample", "Mass (g)", "Volume (mL)", "Density (g/mL)"), strict=True
        ):
            write(page, x, 130, text)
        rows(page, 152, 1, 30)

    def second(page):
        rows(page, 90, 31, 40)

    found = [e for e in analyze(pdf(first, second)).elements if e.kind == Kind.TABLE]
    assert [e.page for e in found] == [0, 1]
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
    with caplog.at_level(logging.WARNING, logger="readablestem.pipeline.tables"):
        result = analyze(pdf(captioned_page))
    assert Kind.TABLE not in {e.kind for e in result.elements}
    assert "12.69" in " ".join(e.text or "" for e in result.elements)
    assert [r.getMessage() for r in caplog.records] == [
        "table detection failed on a page: ZeroDivisionError"
    ]


# --------------------------------------------------------------------------- split rows


def page_table(rows: list[list[str]], *, header_rows: int = 1, caption=None) -> tables.PageTable:
    cells = [
        TableCell(row=r, col=c, text=t, header="col" if r < header_rows and t else None)
        for r, row in enumerate(rows)
        for c, t in enumerate(row)
    ]
    grid = TableGrid(rows=len(rows), cols=len(rows[0]), cells=cells, header_rows=header_rows)
    return tables.PageTable(
        bbox=(50, 50, 550, 700), grid=grid, caption=caption, lines=set(), paths=set(), reasons=[]
    )


HEAD = ["Length (m)", "Phenomenon", "Mass (kg)", "Phenomenon"]


def texts(table: tables.PageTable) -> list[list[str]]:
    rows: dict[int, list[str]] = {}
    for c in sorted(table.grid.cells, key=lambda c: (c.row, c.col)):
        rows.setdefault(c.row, []).append(c.text)
    return list(rows.values())


def test_a_row_the_page_break_split_is_joined_and_an_empty_rest_dropped():
    # The physics orders-of-magnitude table: its last row breaks across the page, and
    # the next page holds the repeated header, "universe" and "limit)", then the caption.
    before = page_table(
        [HEAD, ["1026", "Distance from Earth to the edges of the known", "1053",
                "Mass of the known universe (current upper"]]
    )  # fmt: skip
    after = page_table([HEAD, ["", "universe", "", "limit)"]], caption="Table 1.3 Values")
    assert tables.join_split_row(before, after)  # nothing left of it: layout drops it
    assert texts(before)[-1] == [
        "1026", "Distance from Earth to the edges of the known universe", "1053",
        "Mass of the known universe (current upper limit)",
    ]  # fmt: skip
    assert before.caption == "Table 1.3 Values"
    assert tables.REASON_ROW_SPLIT in before.reasons
    assert tables.REASON_ROW_CONTINUED in after.reasons


def test_a_split_row_with_more_rows_after_it_keeps_the_rest_of_the_table():
    before = page_table([HEAD, ["1", "A wrapped descrip-", "2", "One more"]])
    after = page_table([HEAD, ["", "tion here", "", "line"], ["3", "Next row", "4", "Last"]])
    assert not tables.join_split_row(before, after)
    assert texts(before)[-1] == ["1", "A wrapped description here", "2", "One more line"]
    assert texts(after) == [HEAD, ["3", "Next row", "4", "Last"]]


@pytest.mark.parametrize(
    "rest",
    [
        ["5", "A new row", "6", "Another"],  # a row of its own
        ["", "Gases at 0 C", "", ""],  # a section row: capitalised, not a continuation
        ["", "", "", ""],  # an empty row
    ],
)
def test_rows_that_are_not_the_end_of_a_split_row_are_left_alone(rest):
    before = page_table([HEAD, ["1", "Diameter of a proton", "2", "Mass of a bacterium"]])
    after = page_table([HEAD, rest, ["7", "x", "8", "y"]])
    old_before, old_after = before.grid, after.grid
    assert not tables.join_split_row(before, after)
    assert (before.grid, after.grid) == (old_before, old_after)
    assert before.reasons == after.reasons == []
