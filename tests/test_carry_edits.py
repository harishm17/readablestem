"""T3-1, T3-2 with T3-5 and T3-16: the edits that no fix covers carry over to a new
conversion only onto an item that is unchanged.

The rules of `web/carry_edits.py` on the review fixtures' result: the earlier version gets
real edits (`web.page_edits`, the same functions the routes call), the new one is the
pipeline's output as it was, or with the item changed. What carries: words and headings
(matched by what the item said before the edit), pictures and equations added by hand
(same place, same look, nothing found there now), pages left out (same look), and the
description of an image (with its decision, onto the same picture). What never does: any of
those onto an item whose words, picture or page changed.
"""

from __future__ import annotations

from fixtures.web.review_doc import review_output
from readablestem.core.types import (
    DocumentResult,
    EquationFix,
    Fix,
    Kind,
    Status,
)
from readablestem.web import carry, carry_edits, page_edits, review

PARAGRAPH = "p1-e1"  # "A rigid body turning with angular velocity ⟦p1-e2⟧ moves a point ..."
PLAIN = "p1-e4"  # "Discuss vectors and scalars with the class."
SECOND_PAGE = "p2-e1"  # "Average speed is distance over time."
EQUATION = "p2-e2"
ALT = "A graph of a straight line rising from the origin"
SAME = lambda page, box: True  # noqa: E731
CHANGED = lambda page, box: False  # noqa: E731


def fresh() -> tuple[DocumentResult, dict[str, bytes]]:
    output = review_output()
    return output.result, output.crops


def rows_of(*changes: page_edits.StructureChange) -> list[carry_edits.EditRow]:
    return [carry_edits.EditRow(c.element_id, c.action, c.before, c.after) for c in changes]


def element(result: DocumentResult, element_id: str):
    return next(e for e in result.elements if e.id == element_id)


def carry_all(old, old_crops, new, new_crops, rows, unchanged=SAME):
    """What the worker does, in its order: edits, then decisions, then pages left out."""
    view = carry_edits.with_omitted_back(old)
    done = carry_edits.carry_structure(view, old_crops, new, new_crops, rows, unchanged)
    new_crops.update(done.crops)
    report, changes = carry.carry_decisions(view, old_crops, new, new_crops)
    done.extend(carry_edits.carry_omitted(view, new, unchanged))
    return done, report, changes


# --------------------------------------------------------------------------- the words


def test_the_words_of_an_unchanged_paragraph_are_carried():
    old, old_crops = fresh()
    change = page_edits.edit_text(old, PLAIN, "Discuss vectors and scalars with your class.")
    new, new_crops = fresh()
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(change))
    assert done.items == [(carry_edits.TEXT, PLAIN)] and done.dropped == 0
    assert element(new, PLAIN).text == "Discuss vectors and scalars with your class."


def test_two_edits_in_a_row_are_one_carried_edit_with_the_last_words():
    old, old_crops = fresh()
    first = page_edits.edit_text(old, PLAIN, "Discuss vectors with the class.")
    second = page_edits.edit_text(old, PLAIN, "Talk about vectors with the class.")
    new, new_crops = fresh()
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(first, second))
    assert done.items == [(carry_edits.TEXT, PLAIN)]
    assert element(new, PLAIN).text == "Talk about vectors with the class."


def test_an_edit_that_was_undone_is_not_carried():
    old, old_crops = fresh()
    change = page_edits.edit_text(old, PLAIN, "Something else entirely.")
    undone = page_edits.undo(old, PLAIN, change.before[review.STRUCTURE_KEY])
    new, new_crops = fresh()
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(change, undone))
    assert done.items == [] and done.dropped == 0
    assert element(new, PLAIN).text == "Discuss vectors and scalars with the class."


def test_words_are_never_put_on_a_paragraph_whose_words_changed():
    old, old_crops = fresh()
    change = page_edits.edit_text(old, PLAIN, "Discuss vectors and scalars with your class.")
    new, new_crops = fresh()
    new.elements = [
        e.model_copy(update={"text": "Discuss vectors, scalars and tensors."})
        if e.id == PLAIN
        else e
        for e in new.elements
    ]
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(change))
    assert done.items == [] and done.dropped == 1
    assert element(new, PLAIN).text == "Discuss vectors, scalars and tensors."


def test_an_inline_equation_stays_where_it_is_and_must_be_the_same_picture():
    typed = "A rigid body turning with angular velocity [equation 1] moves a point r quickly."
    old, old_crops = fresh()
    change = page_edits.edit_text(old, PARAGRAPH, typed)
    new, new_crops = fresh()
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(change))
    assert done.items == [(carry_edits.TEXT, PARAGRAPH)]
    assert element(new, PARAGRAPH).text == (
        "A rigid body turning with angular velocity ⟦p1-e2⟧ moves a point r quickly."
    )
    # The equation in it is another picture now: the words are left alone.
    again, again_crops = fresh()
    again_crops["p1-e2"] = again_crops["p1-e2"] + b"\x01"
    done, _, _ = carry_all(old, old_crops, again, again_crops, rows_of(change))
    assert done.items == [] and done.dropped == 1
    assert "quickly" not in (element(again, PARAGRAPH).text or "")


def test_a_text_that_appears_a_different_number_of_times_is_not_guessed():
    old, old_crops = fresh()
    change = page_edits.edit_text(old, PLAIN, "Discuss vectors and scalars with your class.")
    new, new_crops = fresh()
    twin = element(new, PLAIN).model_copy(update={"id": "p2-e9", "page": 1, "order": 99})
    new.elements.append(twin)  # the same words twice now: which one was edited?
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(change))
    assert done.items == [] and done.dropped == 1
    assert element(new, PLAIN).text == element(new, "p2-e9").text


def test_the_nearest_of_two_identical_paragraphs_gets_the_edit_when_the_counts_agree():
    old, old_crops = fresh()
    twin = element(old, PLAIN).model_copy(update={"id": "p2-e9", "page": 1, "order": 99})
    old.elements.append(twin)
    change = page_edits.edit_text(old, "p2-e9", "Discuss vectors and scalars with your class.")
    new, new_crops = fresh()
    new.elements.append(
        element(new, PLAIN).model_copy(update={"id": "p2-e9", "page": 1, "order": 99})
    )
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(change))
    assert done.items == [(carry_edits.TEXT, "p2-e9")]
    assert element(new, "p2-e9").text.endswith("your class.")
    assert element(new, PLAIN).text.endswith("the class.")


# --------------------------------------------------------------------------- headings


def test_a_paragraph_made_a_heading_is_one_again_with_its_level():
    old, old_crops = fresh()
    change = page_edits.make_heading(old, SECOND_PAGE, "2")
    new, new_crops = fresh()
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(change))
    assert done.items == [(carry_edits.HEADING, SECOND_PAGE)]
    assert element(new, SECOND_PAGE).kind is Kind.HEADING
    fix = new.fix_for(SECOND_PAGE)
    assert fix is not None and fix.heading is not None and fix.heading.level == 2
    assert fix.decided_by_reviewer


def test_a_heading_made_and_reworded_carries_both_onto_the_unchanged_paragraph():
    old, old_crops = fresh()
    made = page_edits.make_heading(old, SECOND_PAGE, "2")
    reworded = page_edits.edit_text(old, SECOND_PAGE, "Average speed")
    new, new_crops = fresh()
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(made, reworded))
    assert done.items == [(carry_edits.HEADING, SECOND_PAGE)]
    assert element(new, SECOND_PAGE).text == "Average speed"
    fix = new.fix_for(SECOND_PAGE)
    assert fix is not None and fix.heading is not None and fix.heading.text == "Average speed"


def test_no_heading_is_made_of_a_paragraph_that_changed():
    old, old_crops = fresh()
    change = page_edits.make_heading(old, SECOND_PAGE, "2")
    new, new_crops = fresh()
    new.elements = [
        e.model_copy(update={"text": "Average speed is distance divided by time."})
        if e.id == SECOND_PAGE
        else e
        for e in new.elements
    ]
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(change))
    assert done.items == [] and done.dropped == 1
    assert element(new, SECOND_PAGE).kind is Kind.PARAGRAPH


def test_a_level_the_outline_would_now_change_is_not_carried_and_nothing_is_left_half_done():
    old, old_crops = fresh()
    change = page_edits.make_heading(old, SECOND_PAGE, "2")
    new, new_crops = fresh()
    # A level 4 under a level 1 would be repaired to 2: `make_heading` refuses it.
    fix = old.fix_for(SECOND_PAGE)
    assert fix is not None and fix.heading is not None
    fix.heading.level = 4
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(change))
    assert done.items == [] and done.dropped == 1
    assert element(new, SECOND_PAGE).kind is Kind.PARAGRAPH
    assert new.fix_for(SECOND_PAGE) is None


# --------------------------------------------------------------------------- added items

BOX = (320.0, 600.0, 520.0, 700.0)  # empty on page 1 of the review document


def test_a_picture_added_by_hand_comes_back_where_the_page_is_unchanged():
    old, old_crops = fresh()
    change = page_edits.add_figure(old, 0, BOX, ALT, b"\x89PNG-added")
    old_crops.update(change.crops)
    new, new_crops = fresh()
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(change))
    assert [kind for kind, _ in done.items] == [carry_edits.PICTURE]
    added = done.items[0][1]
    assert added in {e.id for e in new.elements} and new_crops[added] == b"\x89PNG-added"
    fix = new.fix_for(added)
    assert fix is not None and fix.figure is not None and fix.figure.alt == ALT
    assert fix.decided_by_reviewer


def test_a_picture_is_not_added_back_onto_a_page_that_looks_different():
    old, old_crops = fresh()
    change = page_edits.add_figure(old, 0, BOX, ALT, b"\x89PNG-added")
    old_crops.update(change.crops)
    new, new_crops = fresh()
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(change), CHANGED)
    assert done.items == [] and done.dropped == 1
    assert not [e for e in new.elements if e.id.startswith("p1-x")]


def test_a_picture_is_not_added_twice_when_the_tool_finds_it_this_time():
    old, old_crops = fresh()
    figure_box = element(old, "p1-e6").bbox
    # The tool had not found the figure; the reviewer drew it in by hand.
    old.elements = [e for e in old.elements if e.id != "p1-e6"]
    old.fixes = [f for f in old.fixes if f.element_id != "p1-e6"]
    change = page_edits.add_figure(old, 0, figure_box, ALT, b"\x89PNG-added")
    old_crops.update(change.crops)
    new, new_crops = fresh()  # this time the tool finds the figure itself
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(change))
    assert done.items == [] and done.dropped == 1
    assert [e.id for e in new.elements if e.kind is Kind.FIGURE] == ["p1-e6"]


def test_something_the_tool_found_before_too_does_not_stop_a_picture_overlapping_it():
    old, old_crops = fresh()
    figure_box = element(old, "p1-e6").bbox  # drawn over the tool's own figure, in both
    change = page_edits.add_figure(old, 0, figure_box, ALT, b"\x89PNG-added")
    old_crops.update(change.crops)
    new, new_crops = fresh()
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(change))
    assert [kind for kind, _ in done.items] == [carry_edits.PICTURE]


def test_an_equation_marked_by_hand_keeps_what_was_decided_about_it():
    old, old_crops = fresh()
    read = Fix(
        element_id="tmp",
        kind=Kind.EQUATION,
        status=Status.NEEDS_REVIEW,
        confidence=0.9,
        equation=EquationFix(latex="x^2", mathml="<math><msup><mi>x</mi><mn>2</mn></msup></math>",
                             speech="x squared", display="block"),
    )  # fmt: skip
    change = page_edits.add_equation(old, 0, BOX, "x2", b"\x89PNG-eq", read)
    old_crops.update(change.crops)
    added = change.element_id
    fix = old.fix_for(added)
    assert fix is not None
    fix.status, fix.reviewed = Status.ACCEPTED, True  # the reviewer accepted what was read
    new, new_crops = fresh()
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(change))
    assert [kind for kind, _ in done.items] == [carry_edits.EQUATION]
    carried = new.fix_for(done.items[0][1])
    assert carried is not None and carried.status is Status.ACCEPTED and carried.reviewed
    assert carried.equation is not None and carried.equation.latex == "x^2"
    assert element(new, done.items[0][1]).text == "x2"


# --------------------------------------------------------------------------- pages left out


def test_a_page_left_out_is_left_out_again_when_it_looks_the_same():
    old, old_crops = fresh()
    change = page_edits.omit_page(old, 1, pages=3)
    new, new_crops = fresh()
    new.stats.pages = 3
    done, _, _ = carry_all(old, old_crops, new, new_crops, rows_of(change))
    assert done.items == [(carry_edits.PAGE_LEFT_OUT, "page-2")]
    assert new.omitted_pages == [1]
    assert not [e for e in new.elements if e.page == 1]


def test_a_page_that_looks_different_is_not_left_out():
    old, old_crops = fresh()
    page_edits.omit_page(old, 1, pages=3)
    new, new_crops = fresh()
    new.stats.pages = 3
    done, _, _ = carry_all(old, old_crops, new, new_crops, [], CHANGED)
    assert done.items == [] and done.dropped == 1
    assert new.omitted_pages == [] and [e for e in new.elements if e.page == 1]


def test_what_was_decided_on_a_page_before_it_was_left_out_is_there_when_it_comes_back():
    old, old_crops = fresh()
    fix = old.fix_for(EQUATION)
    assert fix is not None
    fix.status, fix.reviewed = Status.REJECTED, True  # kept as the original
    page_edits.omit_page(old, 1, pages=3)
    assert old.fix_for(EQUATION) is None  # held back with its page
    new, new_crops = fresh()
    new.stats.pages = 3
    _, report, _ = carry_all(old, old_crops, new, new_crops, [])
    assert new.omitted_pages == [1] and EQUATION in report.carried
    page_edits.restore_page(new, 1)
    back = new.fix_for(EQUATION)
    assert back is not None and back.status is Status.REJECTED and back.reviewed


# --------------------------------------------------------------------------- described images


def describe(result: DocumentResult, element_id: str, words: str) -> None:
    fix = result.fix_for(element_id)
    assert fix is not None
    fix.status, fix.reviewed = Status.REJECTED, True  # "keep it as an image" comes first
    review.describe_in_words(result, fix, {"description": words})


def test_a_description_is_carried_with_its_decision_onto_the_same_picture():
    old, old_crops = fresh()
    describe(old, EQUATION, "The speed v equals the distance d divided by the time t")
    new, new_crops = fresh()
    _, report, changes = carry_all(old, old_crops, new, new_crops, [])
    assert EQUATION in report.carried
    fix = new.fix_for(EQUATION)
    assert (
        fix is not None
        and fix.described == "The speed v equals the distance d divided by the time t"
    )
    assert fix.status is Status.REJECTED and fix.reviewed
    assert changes and changes[0].after["described"] == fix.described


def test_a_description_is_never_put_on_a_picture_that_changed():
    old, old_crops = fresh()
    describe(old, EQUATION, "The speed v equals the distance d divided by the time t")
    new, new_crops = fresh()
    new_crops[EQUATION] = new_crops[EQUATION] + b"\x01"
    _, report, _ = carry_all(old, old_crops, new, new_crops, [])
    assert EQUATION not in report.carried
    fix = new.fix_for(EQUATION)
    assert fix is not None and fix.described is None and fix.status is not Status.REJECTED


# --------------------------------------------------------------------------- the report


def test_the_report_counts_the_edits_and_holds_only_ids():
    report = carry.CarryReport(edits=["p1-e4", "page-2"], edits_dropped=3)
    again = carry.CarryReport.from_json(report.to_json())
    assert again.edits == ["p1-e4", "page-2"] and again.edits_dropped == 3
    assert carry.CarryReport.from_json({"edits": [1, "p1-e4"], "edits_dropped": -2}).edits == [
        "p1-e4"
    ]
    old, _ = fresh()
    summary = carry.summary(report, old)
    assert (summary.edits, summary.edits_dropped, summary.any) == (2, 3, True)
