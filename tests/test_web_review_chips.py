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
from test_web_review_split import screen, section, stored

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
