"""The review card of a typed equation the page witness read differently (`web.reading_chips`,
T8): the other reading beside the proposal (rendered, and how it is said), why the item is
asked, one button for the whole of it and chips for the symbols that differ. They are ordinary
edits (checked, audited with their origin, undoable), nothing is chosen for the person, and
nothing shows while `FEATURE_PAGE_WITNESS` is off or after the LaTeX changed.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload
from readablestem.core.types import Check, EquationFix, Fix, Status
from readablestem.pipeline import equation_step
from readablestem.web import reading_chips, record
from readablestem.web.db import ReviewEdit, session_scope
from readablestem.web.review import CHOICE_KEY, WITNESS_KEY, WITNESS_ORIGIN, apply_review
from test_web_review_split import panel, screen, section, stored, text_of

ID = "p2-e2"
PROPOSAL = "x + y = 2"


def witness_output(
    other: str = "x + y = 3", *, source="page_witness", code="witness_mismatch", text=None
):
    """`text`: the PDF's text for the equation; by default the fixture's own is dropped (no
    text layer to compare with), so a choice is checked like any edit and passes."""
    output = review_output()
    next(e for e in output.result.elements if e.id == ID).text = text
    fix = next(f for f in output.result.fixes if f.element_id == ID)
    mathml, _ = equation_step._convert(PROPOSAL, "block")
    fix.status, fix.review, fix.confidence = Status.NEEDS_REVIEW, "required", 0.9
    fix.equation = EquationFix(
        latex=PROPOSAL, mathml=mathml or "", speech="x plus y equals 2", display="block",
        other_latex=other, other_source=source,
    )  # fmt: skip
    fix.checks = [
        Check(name="equation.witness_reading", passed=False, severity="warning", code=code)
    ]
    return output


def open_env(tmp_path, output, **settings):
    """A running site with `output` converted and its review open to ID; returns the env and
    the context to close."""
    env = WebEnv(tmp_path, **settings)
    env.converter.output = output
    running = env.running()
    client = running.__enter__()
    env.allow(ALICE)
    login(client, ALICE)
    env.browser = client
    env.job_id = job_id_from(upload(client, review_pdf()))
    env.run_worker()
    return env, running


@pytest.fixture
def witnessed(tmp_path):
    env, running = open_env(tmp_path, witness_output(), feature_page_witness=True)
    yield env
    running.__exit__(None, None, None)


def case(tmp_path, output, **settings):
    """For a test that needs its own output: `with case(...) as env`."""
    env, running = open_env(tmp_path, output, **settings)

    class Opened:
        def __enter__(self):
            return env

        def __exit__(self, *exc):
            return running.__exit__(*exc)

    return Opened()


def card(env) -> str:
    return section(screen(env, f"?item={ID}"), 'id="decision"', 'class="optional-area"')


def pick(env, latex: str, origin: str = WITNESS_ORIGIN):
    page = screen(env, f"?item={ID}")
    version = re.search(r'name="version" value="(\d+)"', page).group(1)  # type: ignore[union-attr]
    return env.browser.post(
        f"/jobs/{env.job_id}/review/{ID}",
        data={
            "csrf_token": token(env.browser, f"/jobs/{env.job_id}/review"),
            "view": "split",
            "version": version,
            "action": "edit",
            "origin": origin,
            "latex": latex,
        },
    )


def other(body: str) -> str:
    return section(body, '<section class="chips other-reading"', "</section>")


# --------------------------------------------------------------------------- the card


def test_the_card_shows_the_other_reading_why_and_a_button_for_all_of_it(witnessed):
    html = other(card(witnessed))
    assert text_of(section(html, "<h3", "</h3>")) == "The other reading"
    text = text_of(html)
    assert "read this formula differently" in text and "Nothing is chosen for you." in text
    assert "Other reading, read from the whole page" in text
    assert "<math" in html  # rendered, not LaTeX source
    assert "Read aloud roughly as: “x plus y equals 3”" in text
    buttons = re.findall(r'<button[^>]*data-role="other"[^>]*>.*?</button>', html, re.S)
    assert len(buttons) == 1
    assert text_of(buttons[0]) == "Use the other reading for equation 3"
    assert 'name="latex" value="x + y = 3"' in buttons[0]
    described = re.search(r'aria-describedby="([^"]+)"', buttons[0]).group(1)  # type: ignore[union-attr]
    assert f'id="{described}"' in html


def test_the_proposal_and_the_other_reading_are_both_on_the_card(witnessed):
    body = card(witnessed)
    assert 'aria-label="Proposed equation 3"' in body
    assert 'aria-label="Other reading of equation 3"' in body
    assert body.index("Proposed equation 3") < body.index("Other reading of equation 3")
    assert body.index("other-reading") < body.index('class="decide-form')  # choose first


def test_nothing_is_chosen_and_showing_the_card_changes_nothing(witnessed):
    html = other(card(witnessed))
    for chosen in ("checked", "selected", "aria-pressed", "aria-checked", 'type="radio"'):
        assert chosen not in html
    fix = stored(witnessed).fix_for(ID)
    assert fix is not None and fix.status is Status.NEEDS_REVIEW
    assert fix.equation.latex == PROPOSAL  # type: ignore[union-attr]


def test_a_difference_in_one_symbol_is_one_button_not_two_of_the_same(witnessed):
    assert 'data-role="chip"' not in other(card(witnessed))  # 2 -> 3 is the whole reading


def test_symbols_that_differ_are_chips_beside_the_whole_button(tmp_path):
    with case(tmp_path, witness_output("X + y = 3"), feature_page_witness=True) as env:
        html = other(card(env))
        chips = re.findall(r'<button[^>]*data-role="chip"[^>]*>.*?</button>', html, re.S)
        assert [text_of(c) for c in chips] == [
            "Use capital X in equation 3",
            "Use 3 in equation 3",
        ]
        assert 'name="latex" value="X + y = 2"' in chips[0]
        assert "the other reading had:" in text_of(html)
        assert 'name="origin" value="page_witness_choice"' in html
        assert 'name="origin" value="second_reader_choice"' not in html


def test_the_witness_as_proposal_says_the_other_reading_is_the_crops(tmp_path):
    with case(tmp_path, witness_output(source="crop"), feature_page_witness=True) as env:
        text = text_of(other(card(env)))
        assert "Other reading, read from the picture of the formula" in text
        assert "and it is the proposal above" in text


def test_a_boundary_difference_is_said(tmp_path):
    output = witness_output(code="witness_extent_differs")
    with case(tmp_path, output, feature_page_witness=True) as env:
        assert "starts and ends differently" in text_of(other(card(env)))


def test_nothing_shows_while_the_page_witness_is_off(tmp_path):
    with case(tmp_path, witness_output(), feature_page_witness=False) as env:
        body = panel(screen(env, f"?item={ID}"))
        assert "other-reading" not in body and "The other reading" not in body


def test_there_is_no_section_without_another_reading(tmp_path):
    output = witness_output()
    fix = next(f for f in output.result.fixes if f.element_id == ID)
    fix.equation.other_latex = None  # type: ignore[union-attr]
    with case(tmp_path, output, feature_page_witness=True) as env:
        assert "other-reading" not in panel(screen(env, f"?item={ID}"))


# --------------------------------------------------------------------------- choosing


def test_choosing_the_other_reading_is_an_ordinary_audited_undoable_edit(witnessed):
    assert pick(witnessed, "x + y = 3").status_code in (200, 303)
    fix = stored(witnessed).fix_for(ID)
    assert fix is not None and fix.status is Status.EDITED and fix.reviewed
    assert fix.equation.latex == "x + y = 3" and "<math" in fix.equation.mathml  # type: ignore[union-attr]
    assert fix.equation.other_latex is None  # type: ignore[union-attr]
    with session_scope(witnessed.ctx.factory) as session:
        [edit] = [e for e in session.scalars(select(ReviewEdit)).all() if e.element_id == ID]
    assert edit.action == "edit" and edit.after.get(WITNESS_KEY) is True
    assert CHOICE_KEY not in edit.after
    assert record.WITNESS_WORDS == "Edited: chose the other reading of this equation"
    assert "other-reading" not in card(witnessed)  # hidden once decided
    page = screen(witnessed, f"?item={ID}")
    version = re.search(r'name="version" value="(\d+)"', page).group(1)  # type: ignore[union-attr]
    undone = witnessed.browser.post(
        f"/jobs/{witnessed.job_id}/undo",
        data={
            "csrf_token": token(witnessed.browser, f"/jobs/{witnessed.job_id}/review"),
            "version": version,
            "view": "split",
        },
    )
    assert undone.status_code in (200, 303)
    back = stored(witnessed).fix_for(ID)
    assert back is not None and back.status is Status.NEEDS_REVIEW
    assert back.equation.latex == PROPOSAL  # type: ignore[union-attr]
    assert "other-reading" in card(witnessed)  # open again, with its other reading


def test_an_edit_that_changes_the_latex_hides_the_other_reading(witnessed):
    pick(witnessed, "x + y = 5", origin="")  # a typed edit, not a choice
    assert "other-reading" not in card(witnessed)
    with session_scope(witnessed.ctx.factory) as session:
        [edit] = [e for e in session.scalars(select(ReviewEdit)).all() if e.element_id == ID]
    assert WITNESS_KEY not in edit.after


def test_a_choice_the_pdfs_text_contradicts_is_refused_like_a_typed_edit_with_the_same_way_on(
    tmp_path,
):
    with case(tmp_path, witness_output(text=PROPOSAL), feature_page_witness=True) as env:
        refused = pick(env, "x + y = 3")
        assert refused.status_code == 422
        assert "Use my edit anyway" in refused.text  # the usual way on, with the LaTeX in the box
        fix = stored(env).fix_for(ID)
        assert fix is not None and fix.status is Status.NEEDS_REVIEW  # nothing was saved


def test_a_reading_that_does_not_convert_is_not_offered(tmp_path):
    with case(tmp_path, witness_output(r"x + \frac{"), feature_page_witness=True) as env:
        assert "other-reading" not in card(env)
        pick(env, r"x + \frac{")
        fix = stored(env).fix_for(ID)
        assert fix is not None and fix.status is Status.NEEDS_REVIEW  # and it is refused anyway


def test_the_mark_is_only_for_a_reading_the_card_offered():
    for latex, marked in (("x + y = 3", True), ("x + y = 4", False)):
        result = witness_output().result
        change = apply_review(
            result, ID, "edit", {"latex": latex, "origin": WITNESS_ORIGIN}, witness=True
        )
        assert change.after.get(WITNESS_KEY, False) is marked, latex
    result = witness_output().result  # no say-so from the form: an ordinary edit
    change = apply_review(result, ID, "edit", {"latex": "x + y = 3"}, witness=True)
    assert WITNESS_KEY not in change.after
    result = witness_output().result  # the page witness off: nothing was offered
    change = apply_review(result, ID, "edit", {"latex": "x + y = 3", "origin": WITNESS_ORIGIN})
    assert WITNESS_KEY not in change.after


def test_the_other_readings_symbols_are_offered_choices_too():
    fix = next(f for f in witness_output("X + y = 3").result.fixes if f.element_id == ID)
    offered = reading_chips.offered_witness_latex(fix)
    assert offered == {"X + y = 3", "X + y = 2", "x + y = 3"}


# --------------------------------------------------------------------------- the pieces


def fix_with(**equation) -> Fix:
    fix = next(f for f in witness_output().result.fixes if f.element_id == ID)
    fix.equation = fix.equation.model_copy(update=equation)  # type: ignore[union-attr]
    return fix


def test_other_reading_for_is_none_unless_it_is_open_enabled_and_different():
    assert reading_chips.other_reading_for(fix_with()) is not None
    assert reading_chips.other_reading_for(fix_with(), enabled=False) is None
    assert reading_chips.other_reading_for(fix_with(other_latex=PROPOSAL)) is None
    assert reading_chips.other_reading_for(fix_with(other_latex=None)) is None
    decided = fix_with()
    decided.status = Status.ACCEPTED
    assert reading_chips.other_reading_for(decided) is None


def test_a_reading_with_too_many_differences_has_a_button_and_no_chips():
    other = reading_chips.other_reading_for(fix_with(other_latex=r"\frac{a}{b} = \sqrt{c} + d^2"))
    assert other is not None and other.chips is None
