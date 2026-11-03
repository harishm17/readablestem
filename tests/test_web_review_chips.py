"""The review card's one-click choices for a doubtful symbol (`web.reading_chips`, H10).

A handwritten display equation (page 2 of the review document, made to look like one) whose
second reading differed in one symbol: `X + y = 2` on the page, `x + y = 2` from the other
reader. The choices are ordinary edits: validated, audited, undoable, never chosen for the
person, and absent when there are no alternatives.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload
from readablestem.core.types import (
    IMAGE_PAGE_REASON,
    Check,
    Element,
    EquationFix,
    Fix,
    Kind,
    ReadingAlternatives,
    Status,
    SymbolAlternative,
)
from readablestem.pipeline import equation_step, reading_alternatives
from readablestem.web import reading_chips
from readablestem.web.db import ReviewEdit, session_scope
from test_web_review_split import panel, screen, section, stored, text_of

ID = "p2-e2"


def handwritten_output():
    output = review_output()
    result = output.result
    element = next(e for e in result.elements if e.id == ID)
    element.reasons = [IMAGE_PAGE_REASON]
    element.text = None  # a picture of handwriting: no text layer to compare with
    latex = "X + y = 2"
    mathml, _ = equation_step._convert(latex, "block")
    fix = next(f for f in result.fixes if f.element_id == ID)
    fix.status, fix.review, fix.confidence = Status.NEEDS_REVIEW, "required", 0.6
    fix.equation = EquationFix(latex=latex, mathml=mathml or "", speech="capital X plus y equals 2",
                               display="block")  # fmt: skip
    fix.checks = [
        Check(name="equation.no_text_layer", passed=False, severity="warning",
              code="no_text_layer"),
        Check(name="image_page.second_reader", passed=False, severity="warning",
              code="second_reader_differs"),
    ]  # fmt: skip
    element.alternatives = reading_alternatives.from_reading(latex, "x + y = 2", "block")
    assert element.alternatives is not None
    return output


@pytest.fixture
def chipped(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = handwritten_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def card(env) -> str:
    return section(screen(env, f"?item={ID}"), 'id="decision"', 'class="optional-area"')


def pick(env, latex: str):
    page = screen(env, f"?item={ID}")
    version = re.search(r'name="version" value="(\d+)"', page).group(1)  # type: ignore[union-attr]
    return env.browser.post(
        f"/jobs/{env.job_id}/review/{ID}",
        data={
            "csrf_token": token(env.browser, f"/jobs/{env.job_id}/review"),
            "view": "split",
            "version": version,
            "action": "edit",
            "origin": "second_reader_choice",
            "latex": latex,
        },
    )


# --------------------------------------------------------------------------- the card


def test_the_card_offers_the_other_reading_as_a_button_named_for_what_it_means(chipped):
    body = card(chipped)
    chips = section(body, '<section class="chips"', "</section>")
    assert text_of(section(chips, "<h3", "</h3>")) == "A second reading differs"
    assert "Nothing is chosen for you." in text_of(chips)
    buttons = re.findall(r"<button[^>]*data-role=\"chip\"[^>]*>.*?</button>", chips, re.S)
    assert len(buttons) == 1
    # The visible words say what the choice is; the hidden ones say which item.
    assert text_of(buttons[0]) == "Use small x in equation 3"
    assert 'name="latex" value="x + y = 2"' in buttons[0]
    assert 'type="submit"' in buttons[0]
    # Where it is, how it is said now, and how the choice would be said aloud.
    assert "Where the proposal above has <code" in chips and "(said “capital X”)" in chips
    assert "Then read aloud roughly as: “x plus y equals 2”" in text_of(chips)
    described = re.search(r'aria-describedby="([^"]+)"', buttons[0]).group(1)  # type: ignore[union-attr]
    assert f'id="{described}"' in chips


def test_nothing_is_chosen_for_the_person(chipped):
    chips = section(card(chipped), '<section class="chips"', "</section>")
    for chosen in ("checked", "selected", "aria-pressed", "aria-checked", 'type="radio"'):
        assert chosen not in chips
    # showing the card changes nothing in the stored result
    fix = stored(chipped).fix_for(ID)
    assert (
        fix is not None and fix.status is Status.NEEDS_REVIEW and fix.equation.latex == "X + y = 2"
    )  # type: ignore[union-attr]


def test_the_choices_are_their_own_form_so_the_edit_box_is_not_sent_with_them(chipped):
    body = card(chipped)
    chips = section(body, '<section class="chips"', "</section>")
    assert 'class="chips-form"' in chips and 'name="action" value="edit"' in chips
    assert chips.count("<form") == 1
    assert body.index('class="chips"') < body.index('class="decide-form')  # choose first


def test_there_are_no_chips_without_alternatives(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = review_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        body = panel(screen(env))
        assert "chips" not in body and "A second reading differs" not in body


# --------------------------------------------------------------------------- choosing


def test_a_choice_is_an_ordinary_edit_checked_audited_and_undoable(chipped):
    response = pick(chipped, "x + y = 2")
    assert response.status_code in (200, 303)
    fix = stored(chipped).fix_for(ID)
    assert fix is not None and fix.status is Status.EDITED and fix.reviewed
    assert fix.equation.latex == "x + y = 2"  # type: ignore[union-attr]
    assert fix.equation.mathml and "<math" in fix.equation.mathml  # type: ignore[union-attr]
    with session_scope(chipped.ctx.factory) as session:
        edits = [e for e in session.scalars(select(ReviewEdit)).all() if e.element_id == ID]
    assert [e.action for e in edits] == ["edit"]
    # the choices are not offered again for a decided item, nor for another reading
    assert "chips" not in card(chipped)
    page = screen(chipped, f"?item={ID}")
    version = re.search(r'name="version" value="(\d+)"', page).group(1)  # type: ignore[union-attr]
    undone = chipped.browser.post(
        f"/jobs/{chipped.job_id}/undo",
        data={
            "csrf_token": token(chipped.browser, f"/jobs/{chipped.job_id}/review"),
            "version": version,
            "view": "split",
        },
    )
    assert undone.status_code in (200, 303)
    back = stored(chipped).fix_for(ID)
    assert back is not None and back.status is Status.NEEDS_REVIEW
    assert back.equation.latex == "X + y = 2"  # type: ignore[union-attr]
    assert 'class="chips"' in card(chipped)  # the choice is open again


def test_a_choice_that_does_not_validate_is_refused_like_a_typed_edit(chipped):
    pick(chipped, r"x + \frac{")
    fix = stored(chipped).fix_for(ID)
    assert fix is not None and fix.status is Status.NEEDS_REVIEW  # nothing was saved


# --------------------------------------------------------------------------- the pieces


def element_and_fix(latex: str, alternatives: ReadingAlternatives | None):
    element = Element(
        id="e", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=0, display="block",
        alternatives=alternatives,
    )  # fmt: skip
    fix = Fix(
        element_id="e", kind=Kind.EQUATION, status=Status.NEEDS_REVIEW, review="required",
        equation=EquationFix(latex=latex, mathml="<math/>", speech="", display="block"),
    )  # fmt: skip
    return element, fix


def test_chips_only_for_the_reading_they_were_made_for_and_only_while_open():
    alt = reading_alternatives.from_reading("X + y = 2", "x + y = 2", "block")
    element, fix = element_and_fix("X + y = 2", alt)
    assert reading_chips.chips_for(element, fix) is not None
    fix.equation.latex = "X + y = 3"  # type: ignore[union-attr]
    assert reading_chips.chips_for(element, fix) is None
    fix.equation.latex = "X + y = 2"  # type: ignore[union-attr]
    fix.status = Status.ACCEPTED
    assert reading_chips.chips_for(element, fix) is None
    assert reading_chips.chips_for(None, fix) is None
    element.alternatives = None
    fix.status = Status.NEEDS_REVIEW
    assert reading_chips.chips_for(element, fix) is None


@pytest.mark.parametrize(
    ("kind", "symbol", "option", "words"),
    [
        ("case", "x", "X", "capital X"),
        ("case", "X", "x", "small x"),
        ("greek_latin", "p", r"\rho", "Greek rho"),
        ("greek_latin", r"\rho", "p", "the letter p"),
        ("digit_letter", "l", "1", "the digit 1"),
        ("digit_letter", "1", "l", "the letter l"),
        ("script", "f", "f^{-1}", "with a raised or lowered part: f to the power minus 1"),
        ("script", "x_2", "x", "without it: x"),
        ("other", "+", r"\times", "times"),
    ],
)
def test_the_words_of_a_choice_say_what_it_means(kind, symbol, option, words):
    alt = SymbolAlternative(start=0, end=len(symbol), symbol=symbol, kind=kind, options=[option])
    assert reading_chips.phrase(alt, option) == words


def test_three_symbols_of_three_options_are_nine_choices_at_most():
    reading = ReadingAlternatives(
        latex="a + b + c",
        symbols=[
            SymbolAlternative(start=i, end=i + 1, symbol=s, options=["x", "y", "z"])
            for i, s in ((0, "a"), (4, "b"), (8, "c"))
        ],
    )
    element, fix = element_and_fix("a + b + c", reading)
    chips = reading_chips.chips_for(element, fix)
    assert chips is not None and chips.count == 9
    assert chips.groups[1].chips[0].latex == "a + x + c"


def test_a_chosen_reading_is_recorded_as_a_choice_and_a_typed_one_is_not(chipped):
    from readablestem.web import record
    from readablestem.web.review import CHOICE_KEY

    pick(chipped, "x + y = 2")
    with session_scope(chipped.ctx.factory) as session:
        [edit] = [e for e in session.scalars(select(ReviewEdit)).all() if e.element_id == ID]
    assert edit.after.get(CHOICE_KEY) is True
    assert record.CHOICE_WORDS.startswith("Edited: chose the reading a second reader gave")


def test_the_mark_is_only_for_a_reading_the_card_offered():
    from readablestem.web.review import CHOICE_KEY, CHOICE_ORIGIN, apply_review

    for latex, marked in (("x + y = 2", True), ("X + y = 3", False)):
        result = handwritten_output().result
        change = apply_review(result, ID, "edit", {"latex": latex, "origin": CHOICE_ORIGIN})
        assert change.after.get(CHOICE_KEY, False) is marked, latex
    # without the form's say-so a matching edit is an ordinary one
    result = handwritten_output().result
    assert CHOICE_KEY not in apply_review(result, ID, "edit", {"latex": "x + y = 2"}).after


def test_a_symbol_that_stands_twice_says_which_one():
    reading = reading_alternatives.from_reading("x + X = 2", "x + x = 2", "block")
    element, fix = element_and_fix("x + X = 2", reading)
    [group] = reading_chips.chips_for(element, fix).groups  # type: ignore[union-attr]
    assert group.symbol == "X" and group.which == ""  # only one capital X
    reading = reading_alternatives.from_reading("x + x = 2", "x + y = 2", "block")
    element, fix = element_and_fix("x + x = 2", reading)
    [group] = reading_chips.chips_for(element, fix).groups  # type: ignore[union-attr]
    assert (group.symbol, group.which) == ("x", "the 2nd ")
    assert group.chips[0].latex == "x + y = 2"
