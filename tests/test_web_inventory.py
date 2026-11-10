"""The Symbols screen (`web.inventory`, `routes/inventory.py`, H11): every symbol read in the
handwritten equations, grouped with the ones that look alike, and changed everywhere at once.

Three handwritten equations on the review document: 1 (inline, waiting) `x + X = p`, 2
(display, waiting) `x + x = \\rho v`, 3 (display, already accepted) `X y = p`. A change is an
ordinary edit of each equation that still waits, as one write: validated per equation, never an
accept, recorded as one row, taken back by one Undo. Equations already decided are listed and
never changed.
"""

from __future__ import annotations

import re

import pytest

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, token, upload
from readablestem.core.types import (
    IMAGE_PAGE_REASON,
    Check,
    DocumentResult,
    EquationFix,
    Status,
)
from readablestem.pipeline import equation_step
from readablestem.web import inventory
from test_web_review_split import section, text_of

WAITING = Check(
    name="image_page.maths_always_checked", passed=False, severity="warning", code="maths_checked"
)
SECOND = Check(
    name="image_page.second_reader", passed=False, severity="warning", code="second_reader_differs"
)
READINGS = {"p1-e2": "x + X = p", "p1-e5": "x + x = \\rho v", "p2-e2": "X y = p"}


def handwritten_output(*, layer: dict[str, str] | None = None):
    """The review document with equations 1 to 3 read from handwriting. `layer`: element id ->
    the text a PDF text layer would have for it (it makes a change there differ from the PDF)."""
    output = review_output()
    result = output.result
    for element in result.elements:
        if element.id not in READINGS:
            continue
        element.reasons = [IMAGE_PAGE_REASON]
        element.text = (layer or {}).get(element.id)
        element.display = "block"
        element.parent_id = None
        fix = next(f for f in result.fixes if f.element_id == element.id)
        latex = READINGS[element.id]
        mathml, _ = equation_step._convert(latex, "block")
        fix.equation = EquationFix(latex=latex, mathml=mathml or "", speech=latex, display="block")
        decided = element.id == "p2-e2"
        fix.status = Status.ACCEPTED if decided else Status.NEEDS_REVIEW
        fix.reviewed = decided
        fix.review = "none" if decided else "required"
        fix.checks = [] if decided else [WAITING, SECOND]
    # the inline equation's paragraph no longer holds a marker for it
    return output


@pytest.fixture
def symbols(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = handwritten_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def stored(env) -> DocumentResult:
    return DocumentResult.model_validate(env.job(env.job_id).result_json)


def latex_of(env, element_id: str) -> str:
    fix = stored(env).fix_for(element_id)
    assert fix is not None and fix.equation is not None
    return fix.equation.latex


def page(env, query: str = "?view=split") -> str:
    response = env.browser.get(f"/jobs/{env.job_id}/symbols{query}")
    assert response.status_code == 200, response.status_code
    return response.text


def version_of(html: str) -> str:
    return re.search(r'name="version" value="(\d+)"', html).group(1)  # type: ignore[union-attr]


def change(env, symbol: str, to: str, *, leave: tuple[str, ...] = (), version: str | None = None):
    html = page(env)
    data: dict[str, object] = {
        "csrf_token": token(env.browser, f"/jobs/{env.job_id}/review"),
        "version": version if version is not None else version_of(html),
        "view": "split",
        "symbol": symbol,
        "to": to,
        "exclude": list(leave),
    }
    return followed(env, env.browser.post(f"/jobs/{env.job_id}/symbols", data=data))


def followed(env, response):
    """The page a redirect leads to (the test client does not follow them)."""
    if response.status_code == 303:
        return env.browser.get(response.headers["location"])
    return response


def form_of(html: str, symbol_id: str) -> str:
    return section(html, f'<h3 id="{symbol_id}-title"', "</form>")


# --------------------------------------------------------------------------- finding symbols


def test_symbols_are_found_by_token_and_prose_is_left_alone():
    found = [(s.key, s.nth) for s in inventory.symbol_spans(r"\text{rate} x + \mathbf{x} = \rho_1")]
    assert found == [("x", 1), ("x", 2), ("\\rho", 1), ("1", 1)]
    # never inside a command: `\max` is no m, a and x
    assert [s.key for s in inventory.symbol_spans(r"\max(a, b)")] == ["a", "b"]
    # an array's name and column spec are not symbols
    assert [s.key for s in inventory.symbol_spans(r"\begin{array}{cc} a \end{array}")] == ["a"]


def test_a_command_before_a_letter_gets_a_space_and_only_the_chosen_places_change():
    assert inventory.relettered("p v", "p", "\\rho", {1}) == "\\rho  v".replace("  ", " ")
    assert inventory.relettered("pv", "p", "\\rho", {1}) == "\\rho v"
    assert inventory.relettered("x + x", "x", "X", {2}) == "x + X"
    assert inventory.relettered("x_x", "x", "X", {1, 2}) == "X_X"


# --------------------------------------------------------------------------- the screen


def test_the_screen_lists_each_symbol_with_its_count_grouped_by_look_alike(symbols):
    html = page(symbols)
    assert text_of(section(html, "<h1", "</h1>")) == "Symbols in the handwritten equations"
    intro = text_of(section(html, 'class="symbols-intro"', "</div>"))
    assert "6 symbols, 10 places, in 3 equations" in intro
    x_form = text_of(form_of(html, "sym-x"))
    assert "Small x" in x_form and "Read 3 times, in 2 equations." in x_form
    capital = text_of(form_of(html, "sym-X"))
    assert "Capital X" in capital and "Read 2 times, in 2 equations." in capital
    # 1 of the capital X is in the equation already accepted, and is not changed
    assert "1 in decided equations stay." in capital
    # x, X, p and rho are in families with more than one symbol read: those come first
    headings = [text_of(h) for h in re.findall(r"<h2[^>]*>.*?</h2>", html, re.S)]
    assert headings[0].startswith("Looks alike: x X \u00d7") and "small x, capital X" in headings[0]
    assert text_of(section(html, 'id="sym-p-title"', "</h3>")).endswith("p Small p")
    assert "Greek rho" in text_of(section(html, 'id="sym-_5crho-title"', "</h3>"))
    # v and y are in no other family member's company here: they follow
    assert html.index('id="sym-y-title"') > html.index('id="sym-p-title"')


def test_only_the_owner_sees_the_screen(symbols):
    symbols.allow(BOB)
    other = symbols.client()
    login(other, BOB)
    assert other.get(f"/jobs/{symbols.job_id}/symbols").status_code == 404
    refused = other.post(
        f"/jobs/{symbols.job_id}/symbols",
        data={"csrf_token": token(other), "symbol": "x", "to": "X", "version": "0"},
    )
    assert refused.status_code == 404
    assert latex_of(symbols, "p1-e5") == "x + x = \\rho v"


# --------------------------------------------------------------------------- changing one


def test_a_change_edits_every_waiting_equation_and_accepts_none(symbols):
    response = change(symbols, "x", "X")
    assert response.status_code == 200  # followed the redirect to the screen
    assert latex_of(symbols, "p1-e2") == "X + X = p"
    assert latex_of(symbols, "p1-e5") == "X + X = \\rho v"
    assert latex_of(symbols, "p2-e2") == "X y = p"  # decided: untouched
    result = stored(symbols)
    for eid in ("p1-e2", "p1-e5"):
        fix = result.fix_for(eid)
        assert fix is not None
        # still a decision of its own: waiting, required, not reviewed
        assert fix.status is Status.NEEDS_REVIEW and fix.review == "required" and not fix.reviewed
        assert fix.pending and not fix.decided_by_reviewer
        # what made it a decision stays beside the checks of the new LaTeX
        names = {c.name for c in fix.checks if not c.passed}
        assert {"image_page.maths_always_checked", "image_page.second_reader"} <= names
        assert fix.equation is not None and "<mi>X</mi>" in fix.equation.mathml
    accepted = result.fix_for("p2-e2")
    assert accepted is not None and accepted.status is Status.ACCEPTED
    notice = text_of(section(response.text, 'class="decision-notice symbols-notice"', "</form>"))
    assert (
        "Changed small x to capital X in 2 equations. Each is still waiting for your decision. "
        "Undo takes all of it back." in notice
    )


def test_everything_left_out_changes_nothing_and_says_so(symbols):
    written = symbols.job(symbols.job_id).result_version
    response = change(symbols, "x", "X", leave=("p1-e2:1", "p1-e5:1", "p1-e5:2"))
    assert response.status_code == 422
    assert "Nothing to change: no place is checked, or every one is already decided." in text_of(
        response.text
    )
    assert latex_of(symbols, "p1-e5") == "x + x = \\rho v"
    assert symbols.job(symbols.job_id).result_version == written  # no write was made


def test_an_equation_that_fails_its_check_is_left_and_the_others_are_changed(tmp_path):
    env = WebEnv(tmp_path)
    # equation 1 has a text layer that says a small x: a capital X would differ from the PDF
    env.converter.output = handwritten_output(layer={"p1-e2": "x + X = p"})
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        response = change(env, "x", "X")
        assert latex_of(env, "p1-e2") == "x + X = p"
        assert latex_of(env, "p1-e5") == "X + X = \\rho v"
        notice = text_of(section(response.text, "symbols-notice", "</form>"))
        assert "in 1 equation." in notice
        assert "1 equation was left as before: the change did not pass the checks there." in notice


def test_one_undo_takes_the_whole_batch_back_and_the_screen_says_so(symbols):
    after = change(symbols, "x", "X")
    assert 'data-role="undo"' in after.text
    posted = symbols.browser.post(
        f"/jobs/{symbols.job_id}/undo",
        data={
            "csrf_token": token(symbols.browser, f"/jobs/{symbols.job_id}/review"),
            "view": "symbols",
            "version": version_of(after.text),
        },
    )
    assert posted.status_code == 303
    assert posted.headers["location"] == f"/jobs/{symbols.job_id}/symbols?view=split"
    undo = followed(symbols, posted)
    assert "Undone. They are waiting for your decision again." in text_of(undo.text)
    assert 'data-role="undo"' not in undo.text  # no Undo under an Undo
    assert latex_of(symbols, "p1-e2") == "x + X = p"
    assert latex_of(symbols, "p1-e5") == "x + x = \\rho v"
    result = stored(symbols)
    for eid in ("p1-e2", "p1-e5"):
        fix = result.fix_for(eid)
        assert fix is not None and fix.status is Status.NEEDS_REVIEW and not fix.reviewed


def test_the_record_shows_the_batch_as_one_row(symbols):
    change(symbols, "x", "X")
    body = text_of(symbols.browser.get(f"/jobs/{symbols.job_id}/record").text)
    assert body.count("Changed small x to capital X in 2 equations at once") == 1
    assert "Each equation stayed waiting for a decision." in body
    csv = symbols.browser.get(f"/jobs/{symbols.job_id}/record.csv").text
    assert csv.count("Changed small x to capital X in 2 equations at once") == 1


# --------------------------------------------------------------------------- refused


@pytest.mark.parametrize(
    ("symbol", "to"),
    [("x", "x"), ("x", "\\alpha"), ("x", "\\frac{1}{2}"), ("q", "g"), ("y", "Y\\x")],
)
def test_only_another_symbol_of_the_family_is_accepted(symbols, symbol, to):
    response = change(symbols, symbol, to)
    assert response.status_code == 422
    assert latex_of(symbols, "p1-e5") == "x + x = \\rho v"
    assert latex_of(symbols, "p1-e2") == "x + X = p"


def test_a_page_from_before_another_change_is_refused(symbols):
    old = version_of(page(symbols))
    change(symbols, "x", "X")  # the document changes
    response = change(symbols, "X", "x", version=old)
    assert response.status_code == 409
    assert "This page is out of date" in text_of(response.text)
    assert latex_of(symbols, "p1-e5") == "X + X = \\rho v"  # nothing from the old page applied


def test_a_post_without_the_csrf_token_is_refused(symbols):
    response = symbols.browser.post(
        f"/jobs/{symbols.job_id}/symbols", data={"symbol": "x", "to": "X", "version": "0"}
    )
    assert response.status_code == 403
    assert latex_of(symbols, "p1-e5") == "x + x = \\rho v"


def test_malformed_exclusions_are_ignored():
    assert inventory.excluded_places(["p1-e2:1", "junk", ":3", "p1-e2:x", "p1-e5:99999"]) == {
        ("p1-e2", 1)
    }


@pytest.mark.parametrize("job_id", ["//evil.example", "x/../../evil", "A" * 32, ""])
def test_symbols_url_refuses_a_bad_job_id(job_id: str) -> None:
    from readablestem.web.deps import PageError
    from readablestem.web.routes.review import symbols_url

    with pytest.raises(PageError):
        symbols_url(job_id, "split")


def choose(env, symbol: str, to: str, *, include: tuple[str, ...], version: str | None = None):
    """The screen's own form: the places that stay ticked are posted, `places=chosen`."""
    html = page(env)
    data: dict[str, object] = {
        "csrf_token": token(env.browser, f"/jobs/{env.job_id}/review"),
        "version": version if version is not None else version_of(html),
        "view": "split",
        "symbol": symbol,
        "to": to,
        "places": "chosen",
        "include": list(include),
    }
    return followed(env, env.browser.post(f"/jobs/{env.job_id}/symbols", data=data))
