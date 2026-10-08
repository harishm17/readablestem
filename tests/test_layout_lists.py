"""List detection in layout.analyze: bullets, ordered markers, nesting, false lists.

A row that starts a list item becomes a `Kind.LIST_ITEM` element with `list_marker` (as
printed), `list_ordered` and `list_level`, and `text` without the marker. Items found in
the 2026-10-08 smoke test: multiple-choice options "a. ... b. ... c. ... d. ..." had been
merged into one paragraph, and "b. - 40 m/s" had become a display equation.
"""

from __future__ import annotations

import pymupdf
import pytest

from a11y_stem.core.types import Element, Kind, inline_marker
from a11y_stem.pipeline import layout
from a11y_stem.pipeline.layout import analyze, marker_readings, roman_value

BODY = 11.0
PITCH = 15.0


def write(page: pymupdf.Page, x: float, y: float, rows: list[str], **kw) -> float:
    size = kw.pop("fontsize", BODY)
    for row in rows:
        page.insert_text((x, y), row, fontsize=size, **kw)
        y += PITCH * size / BODY
    return y


def one_page(fill) -> bytes:
    doc = pymupdf.open()
    fill(doc.new_page(width=612, height=792))
    return doc.tobytes()


def html_page(html: str) -> bytes:
    return one_page(lambda page: page.insert_htmlbox(pymupdf.Rect(72, 72, 540, 500), html))


def items(elements: list[Element]) -> list[tuple[str | None, bool | None, int | None, str]]:
    return [
        (e.list_marker, e.list_ordered, e.list_level, e.text or "")
        for e in elements
        if e.kind == Kind.LIST_ITEM
    ]


def kinds(elements: list[Element]) -> list[Kind]:
    return [e.kind for e in elements]


# ---------------------------------------------------------------- bullets


@pytest.mark.parametrize("glyph", ["\u2022", "\u25e6", "\u25aa", "\u25b6"])
def test_bullet_glyphs_start_unordered_items_and_the_marker_is_kept_out_of_the_text(glyph):
    html = f"<p>{glyph} Measure the ramp length.<br>{glyph} Time the cart over it.</p>"
    assert items(analyze(html_page(html)).elements) == [
        (glyph, False, 1, "Measure the ramp length."),
        (glyph, False, 1, "Time the cart over it."),
    ]


@pytest.mark.parametrize("glyph", ["\u2013", "\u2217", "-", "*"])
def test_line_start_dashes_and_stars_are_bullets_when_another_line_has_one(glyph):
    html = f"<p>{glyph} Measure the ramp length.<br>{glyph} Time the cart over it.</p>"
    assert [m for m, *_ in items(analyze(html_page(html)).elements)] == [glyph, glyph]


def test_a_sentence_wrapped_onto_a_line_starting_with_a_dash_is_not_a_list():
    def fill(page):
        write(
            page,
            72,
            100,
            [
                "The cart rolls down the ramp and speeds up all the way to the",
                "- slightly dented - stop block at the end of the track, where",
                "it comes to rest before the next trial begins again.",
            ],
        )

    elements = analyze(one_page(fill)).elements
    assert kinds(elements) == [Kind.PARAGRAPH]
    assert "- slightly dented -" in (elements[0].text or "")


def test_a_bullet_drawn_as_a_small_disc_starts_an_item():
    """Chromium draws CSS disc markers as filled paths, not text."""

    def fill(page):
        for i, text in enumerate(["Describe motion in reference frames", "Define displacement"]):
            y = 100 + 16 * i
            page.draw_circle((80, y - 3.5), 1.4, color=None, fill=(0, 0, 0))
            page.insert_text((90, y), text, fontsize=BODY)
        write(page, 72, 160, ["Body text follows the two learning objectives on this page."])

    elements = analyze(one_page(fill)).elements
    assert items(elements) == [
        ("\u2022", False, 1, "Describe motion in reference frames"),
        ("\u2022", False, 1, "Define displacement"),
    ]
    assert elements[-1].kind == Kind.PARAGRAPH
    assert not any(e.kind == Kind.FIGURE for e in elements)


# ---------------------------------------------------------------- ordered markers


@pytest.mark.parametrize(
    ("markers", "texts"),
    [
        (["1.", "2.", "3."], ["Measure the ramp.", "Time the cart.", "Plot the data."]),
        (["1)", "2)"], ["Measure the ramp.", "Time the cart."]),
        (["(a)", "(b)", "(c)"], ["find the speed;", "find the time;", "sketch the graph."]),
        (["a.", "b."], ["40 m/s", "4.0 m/s"]),
        (["A.", "B.", "C."], ["Newton", "Joule", "Watt"]),
        (["i.", "ii.", "iii."], ["how far does it fall?", "how long?", "how fast?"]),
        (["(iv)", "(v)"], ["the fourth part", "the fifth part"]),
    ],
)
def test_ordered_markers_with_siblings_start_ordered_items(markers, texts):
    def fill(page):
        write(page, 72, 90, ["Answer each part of the question below in full sentences."])
        for i, (marker, text) in enumerate(zip(markers, texts, strict=True)):
            page.insert_text((84, 120 + PITCH * i), marker, fontsize=BODY)
            page.insert_text((104, 120 + PITCH * i), text, fontsize=BODY)

    assert items(analyze(one_page(fill)).elements) == [
        (marker, True, 1, text) for marker, text in zip(markers, texts, strict=True)
    ]


def test_multiple_choice_options_are_separate_items_not_one_paragraph():
    options = [
        "a. The motion of the ball is independent of the reference frame.",
        "b. The motion of the ball depends on the reference frame.",
        "c. The ball does not move in any reference frame.",
        "d. The ball moves only in the frame of the thrower.",
    ]

    def fill(page):
        write(page, 72, 90, ["How do reference frames affect how you describe the motion?"])
        write(page, 90, 110, options)

    elements = analyze(one_page(fill)).elements
    assert kinds(elements) == [Kind.PARAGRAPH] + [Kind.LIST_ITEM] * 4
    assert items(elements) == [(o[:2], True, 1, o[3:]) for o in options]


def test_a_quantity_option_is_a_list_item_with_inline_text_not_a_display_equation():
    """The smoke test turned "b. - 40 m/s" into a display equation."""

    def fill(page):
        write(page, 72, 90, ["What was the average speed of the baseball over the distance?"])
        write(page, 90, 110, ["a. 40 m/s", "b. - 40 m/s", "c. 0.03 m/s", "d. 8.5 m/s"])

    elements = analyze(one_page(fill)).elements
    assert not any(e.kind == Kind.EQUATION for e in elements)
    assert items(elements)[1] == ("b.", True, 1, "- 40 m/s")


def test_openstax_spaced_full_stop_is_part_of_the_marker():
    def fill(page):
        page.insert_text((60, 100), "3", fontsize=BODY)
        page.insert_text((68, 100), ".", fontsize=BODY)
        page.insert_text(
            (76, 100), "In 1999 the orbiter crashed because of a unit error.", fontsize=BODY
        )

    assert items(analyze(one_page(fill)).elements) == [
        ("3.", True, 1, "In 1999 the orbiter crashed because of a unit error.")
    ]


# ---------------------------------------------------------------- nesting and indents


def test_nested_lists_get_levels_from_the_indent_of_the_item_text():
    html = (
        "<p>Rules for derivatives:</p><ol><li>The power rule.</li><li>The product rule:"
        "<ul><li>for two factors,</li><li>for three factors.</li></ul></li>"
        "<li>The chain rule.</li></ol><p>Body text after the list.</p>"
    )
    elements = analyze(html_page(html)).elements
    assert items(elements) == [
        ("1.", True, 1, "The power rule."),
        ("2.", True, 1, "The product rule:"),
        ("\u2022", False, 2, "for two factors,"),
        ("\u2022", False, 2, "for three factors."),
        ("3.", True, 1, "The chain rule."),
    ]
    assert elements[-1].kind == Kind.PARAGRAPH and elements[-1].text == "Body text after the list."


def test_hanging_indent_lines_continue_their_item():
    def fill(page):
        page.insert_text((72, 100), "1.", fontsize=BODY)
        write(
            page,
            90,
            100,
            [
                "Build a ramp by placing one end of the board on the books and",
                "adjust it until the slope is gentle.",
            ],
        )
        page.insert_text((72, 135), "2.", fontsize=BODY)
        write(page, 90, 135, ["Mark distances along the ramp."])

    assert items(analyze(one_page(fill)).elements) == [
        (
            "1.",
            True,
            1,
            "Build a ramp by placing one end of the board on the books and adjust it until "
            "the slope is gentle.",
        ),
        ("2.", True, 1, "Mark distances along the ramp."),
    ]


def test_a_list_item_holds_its_inline_equations():
    html = (
        "<p>\u2022 the speed is <i>v</i> = <i>d</i>/<i>t</i> in metres per second;<br>"
        "\u2022 the time is measured in seconds.</p>"
    )
    elements = analyze(html_page(html)).elements
    item = elements[0]
    equation = elements[1]
    assert item.kind == Kind.LIST_ITEM and item.list_marker == "\u2022"
    assert equation.kind == Kind.EQUATION and equation.display == "inline"
    assert equation.parent_id == item.id
    assert (item.text or "").startswith("the speed is " + inline_marker(equation.id))


# ---------------------------------------------------------------- not lists


@pytest.mark.parametrize(
    "line",
    [
        "A ball rolls down the ramp and speeds up as it goes along.",
        "2.5 metres is the height of the classroom ceiling, for comparison.",
        "B. Smith measured the ramp twice and wrote both values down.",
        "I. Newton described motion with three laws in his famous book.",
    ],
)
def test_sentences_that_start_like_markers_without_siblings_are_paragraphs(line):
    def fill(page):
        write(page, 72, 100, ["Some ordinary body text comes first on this page, then a"])
        write(page, 72, 130, [line])

    assert kinds(analyze(one_page(fill)).elements) == [Kind.PARAGRAPH] * 2


def test_a_numbered_section_heading_is_not_a_list_item():
    def fill(page):
        write(page, 72, 100, ["Body text before the section in the normal size of eleven."])
        page.insert_text((72, 140), "2.3 Speed", fontsize=16, fontname="hebo")
        write(page, 72, 170, ["Body text after the heading in the normal size of eleven."])

    elements = analyze(one_page(fill)).elements
    assert kinds(elements) == [Kind.PARAGRAPH, Kind.HEADING, Kind.PARAGRAPH]


# ---------------------------------------------------------------- marker readings


def test_marker_readings_cover_numbers_letters_and_roman_numerals():
    assert marker_readings("12") == (("decimal", 12),)
    assert marker_readings("b") == (("lower-alpha", 2),)
    assert marker_readings("i") == (("lower-alpha", 9), ("lower-roman", 1))
    assert marker_readings("iv") == (("lower-roman", 4),)
    assert marker_readings("C") == (("upper-alpha", 3), ("upper-roman", 100))
    assert marker_readings("iiii") == ()  # not canonical
    assert [roman_value(t) for t in ("xiv", "XL", "ic", "")] == [14, 40, None, None]


def test_list_levels_follow_the_text_indent_and_reset_after_a_paragraph():
    def block(kind: Kind, x: float | None) -> layout._Block:
        marker = layout._Marker("\u2022", False, 1, x) if x is not None else None
        return layout._Block(kind, (0, 0, 1, 1), marker=marker)

    blocks = [
        block(Kind.LIST_ITEM, 90),
        block(Kind.LIST_ITEM, 120),
        block(Kind.EQUATION, None),  # a display equation inside an item does not end the list
        block(Kind.LIST_ITEM, 121),
        block(Kind.LIST_ITEM, 150),
        block(Kind.LIST_ITEM, 90),
        block(Kind.PARAGRAPH, None),
        block(Kind.LIST_ITEM, 120),  # a new list: its first item is level 1 again
    ]
    levels = layout._list_levels(blocks, tolerance=6.0)
    assert [levels.get(id(b)) for b in blocks] == [1, 2, None, 2, 3, 1, None, 1]


def test_list_levels_are_measured_from_the_left_edge_of_the_column():
    def block(x: float, origin: float) -> layout._Block:
        marker = layout._Marker("1.", True, 1, x)
        return layout._Block(Kind.LIST_ITEM, (0, 0, 1, 1), marker=marker, origin=origin)

    left = [block(76, 58), block(98, 58)]  # level 1 and 2 in the left column
    right = [block(362, 322), block(340, 322)]  # the same indents in the right column
    levels = layout._list_levels([*left, *right], tolerance=6.0)
    assert [levels[id(b)] for b in [*left, *right]] == [1, 2, 2, 1]


def test_a_list_that_goes_on_in_the_next_column_keeps_its_levels():
    """Levels came from the absolute x of the item text, so every item after a column
    break went one level deeper and nested inside the last item of the first column."""

    def item(page, x: float, y: float, marker: str, lines: list[str]) -> float:
        page.insert_text((x, y), marker, fontsize=BODY)
        return write(page, x + (14 if marker[0].isdigit() else 18), y, lines) + 4

    def fill(page):
        y = write(
            page,
            58,
            80,
            [
                "The checklist below continues into the",
                "right-hand column of this handout, as a",
                "long numbered list with nested parts.",
            ],
        )
        y = item(page, 62, y + 6, "1.", ["Measure the length of the ramp with", "the tape."])
        y = item(page, 62, y, "2.", ["Time the cart over the full ramp:"])
        y = item(page, 80, y, "(a)", ["once with the stopwatch held in hand,"])
        item(page, 80, y, "(b)", ["once with the light gate at the end."])
        y = item(page, 344, 80, "(c)", ["once more with the ramp raised,"])
        y = item(page, 344, y, "(d)", ["and once with the ramp lowered."])
        y = item(page, 326, y, "3.", ["Average the times for each setting of", "the ramp."])
        y = item(page, 326, y, "4.", ["Compare the plot with the prediction."])
        write(
            page,
            322,
            y + 6,
            [
                "The plot should show the time falling as",
                "the ramp gets steeper, and the slope of",
                "the line gives the acceleration.",
            ],
        )

    found = items(analyze(one_page(fill)).elements)
    assert [(m, lv) for m, _, lv, _ in found] == [
        ("1.", 1), ("2.", 1), ("(a)", 2), ("(b)", 2), ("(c)", 2), ("(d)", 2), ("3.", 1), ("4.", 1),
    ]  # fmt: skip


# ---------------------------------------------------------------- not lists (review round 2)


@pytest.mark.parametrize(
    "lines",
    [
        [
            "The ratio of the heavier mass to the lighter one is exactly",
            "2. The heavier ball therefore falls with the same acceleration as the",
            "lighter one, because the force on it is also twice as large as before.",
        ],
        [
            "The answer to the first question is given in section",
            "3. A full derivation is left to the reader as an exercise for this week.",
            "Next we turn to the energy of the system and how it changes over time.",
        ],
    ],
)
def test_a_wrapped_line_that_starts_with_a_number_and_a_capital_is_not_an_item(lines):
    """A number before a capital needs no sibling, but not on the next line of a
    paragraph: it cut the sentence and moved "2." into a marker (rendered <ol start=2>)."""
    elements = analyze(one_page(lambda page: write(page, 72, 100, lines))).elements
    assert kinds(elements) == [Kind.PARAGRAPH]
    assert lines[1] in (elements[0].text or "")


def test_a_lone_numbered_item_after_a_colon_is_still_an_item():
    def fill(page):
        write(page, 72, 100, ["Follow these steps to calibrate the light gate:"])
        write(page, 72, 115, ["1. Measure the distance between the two gates."])
        write(page, 72, 145, ["Then repeat the whole procedure for the other cart."])

    elements = analyze(one_page(fill)).elements
    assert kinds(elements) == [Kind.PARAGRAPH, Kind.LIST_ITEM, Kind.PARAGRAPH]
    assert items(elements) == [("1.", True, 1, "Measure the distance between the two gates.")]


@pytest.mark.parametrize("dash", ["-", "\u2013", "\u2014"])
def test_a_spaced_minus_before_a_number_is_not_a_dash_bullet(dash):
    """Two lines starting "- 40 m/s" and "- 20 m/s" matched the sibling rule: the sign
    went into the marker and the values were read out as positive."""
    html = (
        "<p>The velocities of the two carts after the collision are:<br>"
        f"{dash} 40 m/s<br>{dash} 20 m/s<br>so both carts move to the left after they collide.</p>"
    )
    elements = analyze(html_page(html)).elements
    assert not any(e.kind == Kind.LIST_ITEM for e in elements)
    text = " ".join(e.text or "" for e in elements)
    assert f"{dash} 40 m/s" in text and f"{dash} 20 m/s" in text


def test_a_dash_bullet_before_words_is_still_a_bullet():
    html = "<p>\u2013 forty metres per second<br>\u2013 twenty metres per second</p>"
    assert [m for m, *_ in items(analyze(html_page(html)).elements)] == ["\u2013", "\u2013"]
