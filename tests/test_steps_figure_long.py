"""Long descriptions, chart data tables and the colour-only flag (FEATURE_LONG_DESCRIPTIONS)
in the figure step, with FakeVisionClient: the option off sends exactly what it did
before; on, the long prompt, the figure's labels as fenced context, and the review tiers
of docs/design/content-features.md (unsure description: a note; values read from the
image: a required decision; colour only: no change)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from a11y_stem.config import MAIN_MODEL
from a11y_stem.core.types import ConversionOptions, Element, Fix, Kind, Status, grid_problems
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.models.prompts import load_prompt
from a11y_stem.pipeline import figure_details, figure_step, options
from a11y_stem.pipeline.figure_step import (
    LONG_MAX_OUTPUT_TOKENS,
    LONG_PROMPT_VERSION,
    MAX_OUTPUT_TOKENS,
    PROMPT_VERSION,
    FigureLongOutput,
    FigureOutput,
    describe,
    long_description_missing,
)
from test_steps_support import make_settings, use_fallback_router

PNG = b"\x89PNG figure crop"
SETTINGS = make_settings()
ALT = "Line graph of position versus time: position rises steadily, then levels off."
LONG = (
    "Line graph of the position of a car against time.\n"
    "The horizontal axis is time in seconds, from 0 to 8. The vertical axis is position "
    "in metres, from 0 to 2400.\n"
    "Position rises in a straight line from 400 m at 0 s to 2000 m at 6.40 s."
)
HEADER = ["Time (s)", "Position (m)"]
ROWS = [["0", "400"], ["0.50", "525"], ["6.40", "2000"]]
MINUS, EN, THIN, SUP3, ZWSP = chr(0x2212), chr(0x2013), chr(0x2009), chr(0xB3), chr(0x200B)
# The labels layout found in the figure region (Element.text of a figure).
LABELS = "Position, d (m) 2400 2000 1600 1200 800 525 0 1 2 3 4 5 6 7 8 0.50 6.40 d0 = 400"


@pytest.fixture(autouse=True)
def _router(monkeypatch):
    use_fallback_router(monkeypatch)


@pytest.fixture
def long_on():
    with options.using(ConversionOptions(long_descriptions=True)):
        yield


def element(text: str | None = LABELS, caption: str | None = "Figure 2.3 A car.") -> Element:
    return Element(
        id="p1-e5",
        kind=Kind.FIGURE,
        page=0,
        bbox=(72.0, 200.0, 400.0, 420.0),
        order=5,
        text=text,
        caption=caption,
    )


def short(**kw) -> dict:
    return {"figure_type": "chart", "decorative": False, "alt": ALT, "confidence": 0.93, **kw}


def long(*, table=True, **kw) -> dict:
    answer = {
        **short(),
        "long_description": LONG,
        "long_description_unsure": False,
        "data_table": {"header": HEADER, "rows": ROWS} if table else None,
        "values_printed": True,
        "colour_only": False,
    }
    answer.update(kw)
    return answer


def run(answer: dict, el: Element | None = None) -> tuple[Fix, FakeVisionClient]:
    client = FakeVisionClient(defaults={"figure": answer})
    fix = describe(el or element(), PNG, client=client, settings=SETTINGS, context_text="Near.")
    return fix, client


def codes(fix: Fix) -> set[str | None]:
    return {c.code for c in fix.failed_checks}


# ---------------------------------------------------------------------- option off


def test_off_sends_the_short_prompt_exactly_as_before():
    fix, client = run(short())
    (request,) = client.requests
    assert request.prompt_version == PROMPT_VERSION
    assert request.instructions == load_prompt("figure").text
    assert request.max_output_tokens == MAX_OUTPUT_TOKENS
    # The figure's own labels are not sent: the context is what it was.
    assert request.context_text == "Caption: Figure 2.3 A car.\n\nNearby text: Near."
    assert fix.status is Status.ACCEPTED and fix.review == "none"
    assert fix.figure is not None
    assert fix.figure.long_description is None and fix.figure.data_table is None
    assert fix.figure.data_values_from_text is None and fix.figure.colour_only is False
    assert [c.name for c in fix.checks if c.name.startswith("figure.")] == []


def test_off_refuses_the_long_fields():
    # FigureOutput forbids extra keys, so the long schema is never mixed in by accident.
    fix, _ = run(long())
    assert fix.status is Status.FAILED and fix.reason == "model_invalid_output"


def test_off_outside_a_run_uses_the_defaults():
    assert options.current().long_descriptions is False


# ---------------------------------------------------------------------- option on


def test_on_sends_the_long_prompt_with_the_labels_fenced(long_on):
    _, client = run(long())
    (request,) = client.requests
    assert request.prompt_version == LONG_PROMPT_VERSION != PROMPT_VERSION
    assert request.instructions == load_prompt("figure_long").text
    assert request.max_output_tokens == LONG_MAX_OUTPUT_TOKENS
    assert request.model == MAIN_MODEL
    assert request.context_text is not None
    assert request.context_text.endswith(f"Labels in the figure: {LABELS}")
    assert LABELS not in request.instructions  # document text only travels as data


def test_long_labels_are_cut(long_on):
    _, client = run(long(), element(text="7 " * 1000))
    context = client.requests[0].context_text or ""
    labels = context.split("Labels in the figure: ", 1)[1]
    assert len(labels) == figure_step.MAX_LABEL_CHARS


def test_a_long_description_is_stored_cleaned_and_needs_nothing(long_on):
    messy = f"  Line graph of the position of a car{ZWSP} against time.  \n\n" + LONG
    fix, _ = run(long(long_description=messy, data_table=None))
    assert fix.status is Status.ACCEPTED and fix.review == "none"
    assert fix.figure is not None and fix.figure.long_description is not None
    lines = fix.figure.long_description.split("\n")
    assert lines[0] == "Line graph of the position of a car against time."
    assert "" not in lines
    assert all(c.passed for c in fix.checks)
    assert "figure.long_description" in {c.name for c in fix.checks}


def test_an_unsure_long_description_is_an_optional_note(long_on):
    fix, _ = run(long(long_description_unsure=True, data_table=None))
    assert fix.status is Status.ACCEPTED and fix.review == "optional"
    assert codes(fix) == {"long_description_unsure"}
    assert fix.optional_note


@pytest.mark.parametrize(
    "text",
    [
        "Too short.",
        "<p>Line graph of position against time, rising in a straight line to 2000 m.</p>",
        "See https://example.org/figure for a description of this graph and its values.",
        "Image " * 20,
        "Line graph. " * 300,
        "\n".join(["Line graph of position against time."] * 13),
    ],
)
def test_an_unusable_long_description_is_dropped_without_a_decision(long_on, text):
    fix, _ = run(long(long_description=text, data_table=None))
    assert fix.figure is not None and fix.figure.long_description is None
    assert fix.status is Status.ACCEPTED and fix.review == "none"
    assert long_description_missing(fix)  # the job page's notice, not a decision


def test_values_printed_in_the_figure_need_no_decision(long_on):
    fix, _ = run(long())
    assert fix.figure is not None and fix.figure.data_table is not None
    assert fix.figure.data_values_from_text is True
    assert fix.status is Status.ACCEPTED and fix.review == "none"
    check = next(c for c in fix.checks if c.name == "figure.data_values")
    assert check.passed and check.severity == "warning"


def test_values_read_from_the_image_are_an_optional_extra_not_a_decision(long_on):
    # Additive extras never add a required decision: the table is kept on the fix,
    # offered on the review screen, and not written until a reviewer adds it.
    from a11y_stem.pipeline import figure_details

    fix, _ = run(long(), element(text=None))  # a raster chart: no PDF text in the region
    assert fix.figure is not None and fix.figure.data_values_from_text is False
    assert fix.status is Status.ACCEPTED and fix.review == "optional"
    assert codes(fix) == {"values_from_image"}
    assert fix.confidence == pytest.approx(0.93)  # the confidence is the model's, unchanged
    assert fix.figure.data_table is not None and not fix.figure.data_table_added
    assert figure_details.data_table_offered(fix.figure)
    assert not figure_details.data_table_shown(fix.figure)


def test_one_value_missing_from_the_text_is_enough(long_on):
    fix, _ = run(long(data_table={"header": HEADER, "rows": [*ROWS, ["8", "2400.5"]]}))
    assert fix.figure is not None and fix.figure.data_values_from_text is False
    assert fix.status is Status.ACCEPTED and fix.review == "optional"


def test_values_the_model_estimated_are_read_from_the_image(long_on):
    # Every number appears in the labels (tick values), but the model says it placed
    # them by eye: they are still read from the image.
    fix, _ = run(long(values_printed=False))
    assert fix.figure is not None and fix.figure.data_values_from_text is False
    assert fix.status is Status.ACCEPTED and codes(fix) == {"values_from_image"}


def test_a_doubtful_alt_text_is_still_a_decision_with_a_table_offered(long_on):
    # The table never adds a decision, and never takes one away either.
    fix, _ = run(long(confidence=0.6), element(text=None))
    assert fix.status is Status.NEEDS_REVIEW and fix.review == "required"
    assert codes(fix) == {"values_from_image"}


@pytest.mark.parametrize(
    ("figure_type", "table"),
    [
        ("diagram", {"header": HEADER, "rows": ROWS}),
        ("chart", {"header": HEADER, "rows": [["0", "400"], ["1"]]}),  # ragged
        ("chart", {"header": HEADER, "rows": []}),
        ("chart", {"header": ["Planet", "Colour"], "rows": [["Mars", "red"]]}),  # no number
        ("chart", {"header": HEADER, "rows": [["1", "2"]] * 61}),  # too long to be read
    ],
)
def test_a_table_that_is_not_a_charts_values_is_dropped(long_on, figure_type, table):
    fix, _ = run(long(figure_type=figure_type, data_table=table))
    assert fix.figure is not None and fix.figure.data_table is None
    assert fix.figure.data_values_from_text is None
    assert "figure.data_values" not in {c.name for c in fix.checks}
    assert fix.status is Status.ACCEPTED


def test_colour_only_changes_nothing_but_the_flag(long_on):
    fix, _ = run(long(colour_only=True))
    assert fix.figure is not None and fix.figure.colour_only is True
    assert fix.status is Status.ACCEPTED and fix.review == "none"


def test_a_decorative_answer_carries_none_of_it(long_on):
    answer = long(figure_type="photo", decorative=True, alt="", confidence=0.99, colour_only=True)
    fix, _ = run(answer, element(caption=None))
    assert fix.figure is not None and fix.figure.decorative
    assert fix.figure.long_description is None and fix.figure.data_table is None
    assert fix.figure.colour_only is False


def test_low_confidence_still_abstains(long_on):
    fix, _ = run(long(confidence=0.2))
    assert fix.status is Status.ABSTAINED and fix.figure is None


def test_the_long_schema_extends_the_short_one():
    assert set(FigureOutput.model_fields) < set(FigureLongOutput.model_fields)
    assert FigureLongOutput.model_config.get("extra") == "forbid"


# ---------------------------------------------------------------------- the notice helper


def test_long_description_missing_only_for_charts_and_diagrams_described_with_it_on(long_on):
    chart, _ = run(long(long_description=""))
    photo, _ = run(long(figure_type="photo", long_description="", data_table=None))
    described, _ = run(long())
    assert long_description_missing(chart)
    assert not long_description_missing(photo)
    assert not long_description_missing(described)


def test_long_description_missing_is_never_raised_with_the_option_off():
    fix, _ = run(short())
    assert not long_description_missing(fix)


# ---------------------------------------------------------------------- numbers and grids


@pytest.mark.parametrize(
    ("text", "numbers"),
    [
        (f"{MINUS}0.50 and -0.5", ["-0.5", "-0.5"]),
        (f"1,000 m and 1000 m and 1{THIN}000 m", ["1000", "1000", "1000"]),
        (".5 s", ["0.5"]),
        (f"from 1{EN}3 s", ["1", "3"]),  # a range, not minus three
        (f"x {MINUS} 3", ["3"]),  # a minus between terms is not a sign
        (f"y = {MINUS}3", ["-3"]),
        (f"10{SUP3} and v2 and CO2", ["10", "3"]),  # an exponent is a number; indices not
        (f"({MINUS}2, 4.0)", ["-2", "4"]),
        ("-0", ["0"]),
        ("", []),
    ],
)
def test_numbers_are_read_the_same_way_from_tables_and_text(text, numbers):
    assert figure_details.numbers_in(text) == [Decimal(n) for n in numbers]


def test_an_exponent_is_compared_too():
    # Exponents printed as superscripts are numbers of their own: a table that writes them
    # with a caret matches the figure, and one with another exponent does not.
    sup = {"2": "\u00b2", "3": "\u00b3", "4": "\u2074"}
    printed = f"Earth 5.97\u00d710{sup['2']}{sup['4']} Mars 6.42\u00d710{sup['2']}{sup['3']}"
    header = ["Planet", "Mass (kg)"]
    right = figure_details.data_grid(header, [["Earth", "5.97 x 10^24"], ["Mars", "6.42 x 10^23"]])
    wrong = figure_details.data_grid(header, [["Earth", "5.97 x 10^24"], ["Mars", "6.42 x 10^25"]])
    assert right is not None and wrong is not None
    assert figure_details.values_in_text(right, printed)
    assert not figure_details.values_in_text(wrong, printed)


def test_values_in_text_needs_every_number():
    grid = figure_details.data_grid(HEADER, ROWS)
    assert grid is not None
    assert figure_details.values_in_text(grid, LABELS)
    assert not figure_details.values_in_text(grid, "0 400 525 6.40 2000")  # 0.50 missing
    assert not figure_details.values_in_text(grid, None)


def test_a_data_grid_is_a_valid_table_with_headers():
    grid = figure_details.data_grid(HEADER, ROWS)
    assert grid is not None and grid_problems(grid) == []
    assert (grid.rows, grid.cols, grid.header_rows, grid.header_cols) == (4, 2, 1, 1)
    assert grid.source == "model" and grid.caption is None and grid.is_data
    headers = {(c.row, c.col): c.header for c in grid.cells}
    assert headers[(0, 0)] == headers[(0, 1)] == "col"
    assert headers[(1, 0)] == "row" and headers[(1, 1)] is None
    single = figure_details.data_grid(["Speed (m/s)"], [["3"], ["4"]])
    assert single is not None and single.header_cols == 0 and grid_problems(single) == []


def test_figure_labels_come_from_the_caption():
    assert figure_details.figure_label("Figure 2.3 A car.") == "Figure 2.3"
    assert figure_details.figure_label("Fig. 4: Speeds") == "Figure 4"
    assert figure_details.figure_label(f"FIGURE 1{EN}2 Data") == f"Figure 1{EN}2"
    assert figure_details.figure_label("A car on a road.") is None
    assert figure_details.figure_label(None) is None
