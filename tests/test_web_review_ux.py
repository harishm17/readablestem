"""Review screen fixes from the UX review (docs/web.md "Review screen"): the stale-tab guard
(MF12), edits checked like proposals (MF3), Undo and plain names for the decisions (MF14),
notes that name their item (MF15), where the speech differs (BW12), what a screen reader
gets from the PDF, and getting back to a decision after the session ended (MF19).

Uses the real-shaped document of fixtures.web.review_doc: decision 1 is the bold omega read
plain (equation p1-e2), 2 the figure p1-e6, 3 the "Teacher Support" heading group; an
optional note on equation p1-e5. The browser side (axe, reflow, focus, no JavaScript,
the live check and the kept draft) is in test_web_review_ux_browser.py.
"""

# Bold and double-struck maths letters and the multiplication sign are the point here.
# ruff: noqa: RUF001

from __future__ import annotations

import re
from html import unescape

import pytest
from sqlalchemy import select

from a11y_stem.core.types import DocumentResult
from a11y_stem.web.db import Job, ReviewEdit, session_scope
from a11y_stem.web.review import bump_result_version, is_stale
from fixtures.web.review_doc import GROUP, review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload


@pytest.fixture
def ux(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = review_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def screen(env, query: str = "", status: int = 200) -> str:
    response = env.browser.get(f"/jobs/{env.job_id}/review{query}")
    assert response.status_code == status, response.status_code
    return response.text


def post(env, target: str, **fields):
    data = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review")} | fields
    return env.browser.post(f"/jobs/{env.job_id}/review/{target}", data=data)


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def section(html: str, start: str, end: str) -> str:
    begin = html.index(start)
    return html[begin : html.index(end, begin)]


def panel(html: str) -> str:
    return section(html, 'id="decision"', 'class="optional-area"')


def stored(env) -> DocumentResult:
    return DocumentResult.model_validate(env.job(env.job_id).result_json)


def version_of(env) -> int:
    with session_scope(env.ctx.factory) as session:
        job = session.get(Job, env.job_id)
        assert job is not None
        return job.result_version


def edits(env) -> list[ReviewEdit]:
    with session_scope(env.ctx.factory) as session:
        rows = session.scalars(
            select(ReviewEdit).order_by(ReviewEdit.created_at, ReviewEdit.id)
        ).all()
        session.expunge_all()
        return list(rows)


# --------------------------------------------------------------------------- MF12 stale tab


def test_is_stale():
    assert not is_stale(3, "3")
    assert not is_stale(0, "0") and not is_stale(None, "0")
    assert not is_stale(3, "")  # a page from before the guard: not refused
    for sent in ("2", "4", "x", "-1", "3.0", "²", "1" * 12):
        assert is_stale(3, sent), sent


def test_bump_result_version():
    job = Job(filename="x.pdf", result_version=None)
    assert bump_result_version(job) == 1
    assert bump_result_version(job) == 2


def test_every_form_carries_the_version_its_page_was_drawn_at(ux):
    page = screen(ux)
    forms = re.findall(
        r"<form method=\"post\" action=\"/jobs/[^\"]+/review/[^\"]+\".*?</form>", page, re.S
    )
    assert forms
    for form in forms:
        assert '<input type="hidden" name="version" value="0">' in form
    listing = ux.browser.get(f"/jobs/{ux.job_id}/review/list").text
    assert listing.count('name="version" value="0"') == listing.count('name="view" value="list"')


def test_each_review_write_counts_itself_once(ux):
    assert post(ux, "p1-e2", action="accept", version="0").status_code == 303
    assert version_of(ux) == 1
    # A group decision is one write, however many headings it changes.
    assert post(ux, GROUP, action="reject", version="1").status_code == 303
    assert version_of(ux) == 2
    assert [e.after["write"] for e in edits(ux)] == [1, 2, 2, 2]
    # The next page's forms carry the new number.
    assert 'name="version" value="2"' in screen(ux)
    # A write that changes nothing (the group again, nothing left) is not counted.
    assert post(ux, GROUP, action="reject", version="2").status_code == 303
    assert version_of(ux) == 2


def test_a_write_from_an_out_of_date_tab_is_refused_and_says_so(ux):
    # Tab A accepts equation 1; tab B, drawn before, then tries to keep it as an image.
    assert post(ux, "p1-e2", action="accept", version="0").status_code == 303
    response = post(ux, "p1-e2", action="reject", version="0")
    assert response.status_code == 409
    fix = stored(ux).fix_for("p1-e2")
    assert fix.status.value == "accepted"  # tab A's decision stands
    assert version_of(ux) == 1 and len(edits(ux)) == 1
    page = response.text
    box = section(page, '<div class="error-summary stale-notice"', "</div>")
    assert 'role="alert"' in box and "autofocus" in box
    assert "Your choice was not saved" in box
    assert "changed in another tab or window after this page was opened" in box
    assert "Equation 1 is now: accepted by you." in text_of(box)
    assert f'<a href="/jobs/{ux.job_id}/review?item=p1-e2">Reload this page</a>' in box
    # The page shows the decision as it is now, with forms that carry the new version.
    assert "Decided: Accepted by you" in panel(page)
    assert 'name="version" value="1"' in page


def test_a_refused_stale_edit_keeps_what_was_typed(ux):
    assert post(ux, "p1-e6", action="accept", version="0").status_code == 303
    response = post(ux, "p1-e2", action="edit", latex=r"\boldsymbol{\omega}", version="0")
    assert response.status_code == 409
    assert r"\boldsymbol{\omega}</textarea>" in panel(response.text)
    assert stored(ux).fix_for("p1-e2").status.value == "needs_review"
    # Choosing again from the refreshed page works.
    assert (
        post(ux, "p1-e2", action="edit", latex=r"\boldsymbol{\omega}", version="1").status_code
        == 303
    )


def test_stale_group_and_list_view_writes_are_refused_too(ux):
    assert post(ux, "p1-e2", action="accept", version="0").status_code == 303
    group = post(ux, GROUP, action="reject", version="0")
    assert group.status_code == 409
    assert "Your choice was not saved" in group.text
    assert {f.status.value for f in stored(ux).fixes if f.group_key == GROUP} == {"needs_review"}
    listed = post(ux, "p1-e6", action="accept", version="0", view="list")
    assert listed.status_code == 409
    assert "<h1>All items as a list</h1>" in listed.text
    assert f'href="/jobs/{ux.job_id}/review/list#item-p1-e6">Reload this page</a>' in listed.text
    bulk = post(ux, "headings", action="accept_all", version="0", view="list")
    assert bulk.status_code == 409
    assert stored(ux).fix_for("p1-e6").status.value == "needs_review"


def test_a_form_without_a_version_is_still_applied(ux):
    """Pages opened before the guard existed (and scripts) send no version."""
    assert post(ux, "p1-e2", action="accept").status_code == 303
    assert post(ux, "p1-e6", action="accept").status_code == 303
    assert version_of(ux) == 2


# --------------------------------------------------------------------------- MF3 edits


MEANING = (
    "Your LaTeX differs from the PDF where it changes the meaning: The ω is bold in the PDF "
    "(a vector) but plain in your edit. Correct it, or choose Use my edit anyway if you are "
    "sure."
)


def test_an_edit_that_changes_the_meaning_is_not_used_without_asking(ux):
    # A plain omega again (written another way), where the PDF has a bold one.
    response = post(ux, "p1-e2", action="edit", latex=r"{\omega}", version="0")
    assert response.status_code == 422
    body = panel(response.text)
    assert MEANING in text_of(body)
    assert '<a href="#latex-p1-e2">' in response.text  # the error summary
    assert re.search(r'value="edit_anyway"[^>]*>Use my edit anyway<', body)
    assert "{\\omega}</textarea>" in body
    assert stored(ux).fix_for("p1-e2").status.value == "needs_review"
    assert edits(ux) == [] and version_of(ux) == 0


def test_bold_omega_typed_as_words_is_caught(ux):
    response = post(ux, "p1-e2", action="edit", latex=r"bold \omega", version="0")
    assert response.status_code == 422
    assert "Your edit has b, d, l and o, which the PDF does not." in text_of(panel(response.text))


def test_use_my_edit_anyway_saves_it_and_records_the_choice(ux):
    response = post(ux, "p1-e2", action="edit_anyway", latex=r"{\omega}", version="0")
    assert response.status_code == 303
    fix = stored(ux).fix_for("p1-e2")
    assert fix.status.value == "edited" and fix.equation.latex == r"{\omega}"
    # The failed check stays on the fix: the record shows what the reviewer overrode.
    assert any(c.code == "style_mismatch" and not c.passed for c in fix.checks)
    (edit,) = edits(ux)
    assert edit.action == "edit" and edit.after["anyway"] is True
    page = ux.browser.get(response.headers["location"]).text
    assert "Your edit was saved as you wrote it, although it differs from the PDF." in page


def test_a_correct_edit_passes_the_same_checks_as_a_proposal(ux):
    response = post(ux, "p1-e2", action="edit", latex=r"\boldsymbol{\omega}", version="0")
    assert response.status_code == 303
    fix = stored(ux).fix_for("p1-e2")
    names = {c.name: c.passed for c in fix.checks}
    assert names["equation.text_layer"] and names["equation.text_layer_style"]
    assert names["latex.converts"] and names["speech.generated"]
    assert fix.equation.speech == "bold omega"
    assert "anyway" not in edits(ux)[0].after


def test_the_one_click_fix_is_offered_only_when_save_edit_takes_it(ux, monkeypatch):
    from a11y_stem.web import compare, edit_check

    result = stored(ux)
    element = next(e for e in result.elements if e.id == "p1-e2")
    fix = result.fix_for("p1-e2")
    found = compare.compare_equation(element, fix)
    suggestion = compare.suggest_fix(element, fix, found)
    assert suggestion is not None and suggestion.latex == r"\boldsymbol{\omega}"
    real = edit_check.check_equation_edit

    def differs(*args):
        checked = real(*args)
        checked.meaning = ["The PDF has 8 where your edit has 3."]
        return checked

    monkeypatch.setattr(edit_check, "check_equation_edit", differs)
    assert compare.suggest_fix(element, fix, found) is None


def test_saving_an_unchanged_edit_changes_nothing(ux):
    response = post(ux, "p1-e2", action="edit", latex="  \\omega ", version="0")
    assert response.status_code == 422
    assert (
        "Nothing changed. Choose Accept to keep it as it is, or change the LaTeX first."
        in text_of(panel(response.text))
    )
    alt = "A car on a road between a house and a school."
    response = post(ux, "p1-e6", action="edit", alt=alt, version="0")
    assert response.status_code == 422
    assert "Nothing changed." in text_of(panel(response.text))
    assert edits(ux) == [] and version_of(ux) == 0


def test_saving_a_rejected_figures_text_unchanged_puts_it_back(ux):
    assert post(ux, "p1-e6", action="reject", version="0").status_code == 303
    alt = "A car on a road between a house and a school."
    assert post(ux, "p1-e6", action="edit", alt=alt, version="1").status_code == 303
    assert stored(ux).fix_for("p1-e6").status.value == "edited"


@pytest.mark.parametrize(
    ("latex", "sentence"),
    [
        (r"\frac{a}{b", "It has one more { than }: add the missing }."),
        (r"\frac{a}}{b}", "It has one more } than {: add the missing {."),
        (r"\omegaa + \foo", r"\omegaa and \foo are not commands this tool supports"),
        (r"\input{x}", r"\input could read files or change settings, so it is never allowed."),
        (r"\begin{tikzpicture}x\end{tikzpicture}", "The tikzpicture environment is not supported"),
    ],
)
def test_latex_that_cannot_be_used_is_explained_by_naming_the_problem(latex, sentence):
    from a11y_stem.pipeline.validate import check_latex
    from a11y_stem.web.edit_check import latex_problems

    failed = [c for c in check_latex(latex) if not c.passed]
    said = " ".join(latex_problems(latex, failed))
    assert said.startswith(sentence) or f" {sentence}" in said, said
    assert "latex." not in said  # no check codes


def test_the_edit_checks_are_the_equation_steps_own():
    """`web.edit_check.evaluate` runs the step's evaluation without a model answer: the
    same checks, codes and MathML as `equation_step._evaluate` on the same LaTeX."""
    from a11y_stem.core.types import Element, Kind
    from a11y_stem.pipeline import equation_step
    from a11y_stem.web.edit_check import evaluate

    cases = [
        (r"\omega", "𝛚", "inline"),
        (r"\boldsymbol{\omega}", "𝛚", "inline"),
        (r"e^{\lambda t}", "e−λt", "block"),
        (r"v = d / t", "v = d / t", "block"),
        (r"x^2 + 3", "x2 + 8", "block"),
        (r"\mathbf{d}_0 = 0", "\U0001d41d\U0001d7ce = 0", "block"),
        (r"\frac{a}{b", "a b", "block"),
        (r"a + b", None, "block"),
    ]
    for latex, text, display in cases:
        element = Element(
            id="p1-e1", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=0, text=text,
            display=display,
        )  # fmt: skip
        output = equation_step.EquationOutput(is_equation=True, latex=latex, confidence=1.0)
        step = equation_step._evaluate(output, [], element, display)
        mathml, speech, checks = evaluate(step.latex, display, element)
        assert mathml == step.mathml, latex
        assert speech == step.speech, latex
        assert [(c.name, c.passed, c.code) for c in checks] == [
            (c.name, c.passed, c.code) for c in step.checks
        ], latex


# --------------------------------------------------------------------------- live check


def live(env, latex: str, target: str = "p1-e2"):
    data = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review"), "latex": latex}
    return env.browser.post(f"/jobs/{env.job_id}/review/{target}/check", data=data)


def test_the_live_check_says_what_save_edit_would(ux):
    response = live(ux, r"{\omega}")
    assert response.status_code == 200
    html = response.text
    assert "<!doctype" not in html.lower()  # a fragment, not a page
    assert "Differs from the PDF:" in html
    assert "The ω is bold in the PDF (a vector) but plain in your edit." in html
    assert '<p id="live-status-p1-e2" hx-swap-oob="innerHTML">' in html
    assert "Your LaTeX differs from the PDF where it changes the meaning." in html
    assert '<math xmlns="http://www.w3.org/1998/Math/MathML"' in html  # the preview
    good = live(ux, r"\boldsymbol{\omega}").text
    assert "Your LaTeX can be saved: it matches the PDF&#39;s own text." in good
    assert "Read aloud roughly as:</span> “bold omega”" in good
    broken = live(ux, r"\frac{a}{b").text
    assert "Cannot be used yet:" in broken and "It has one more { than }" in broken
    assert "Your LaTeX cannot be saved yet. The problem is listed below." in broken
    # Unchanged: nothing to say, and the status emptied.
    same = live(ux, r"\omega").text
    assert "<math" not in same
    assert '<p id="live-status-p1-e2" hx-swap-oob="innerHTML"></p>' in same
    assert stored(ux).fix_for("p1-e2").status.value == "needs_review"  # nothing saved
    assert edits(ux) == []


def test_the_live_check_needs_the_owner_a_token_and_an_equation(ux):
    assert live(ux, "x", target="p1-e6").status_code == 404  # a figure
    assert live(ux, "x", target="p9-e9").status_code == 404
    forged = ux.browser.post(
        f"/jobs/{ux.job_id}/review/p1-e2/check", data={"csrf_token": "forged", "latex": "x"}
    )
    assert forged.status_code == 403
    # Signed out in another tab: the session's token still matches, and the answer is
    # empty, so the page the reviewer is typing in stays as it is.
    csrf = token(ux.browser, f"/jobs/{ux.job_id}/review")
    with session_scope(ux.ctx.factory) as session:
        job = session.get(Job, ux.job_id)
        assert job is not None
        job.user.session_version += 1
    gone = ux.browser.post(
        f"/jobs/{ux.job_id}/review/p1-e2/check", data={"csrf_token": csrf, "latex": "x"}
    )
    assert gone.status_code == 204 and gone.text == ""


def test_the_live_check_logs_no_content(ux, caplog, capsys):
    import logging

    caplog.set_level(logging.DEBUG)
    live(ux, r"\SECRETLATEX{x} + \omega")
    assert "SECRETLATEX" not in caplog.text
    assert "SECRETLATEX" not in capsys.readouterr().out


# --------------------------------------------------------------------------- MF14


def undo(env, version: str, **extra):
    data = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review"), "version": version}
    return env.browser.post(f"/jobs/{env.job_id}/undo", data=data | extra)


def test_reject_is_named_for_what_it_does(ux):
    equation = panel(screen(ux))
    assert re.search(r'data-role="reject"[^>]*>Keep as image<span', equation)
    assert (
        "Keep as image: Students see a picture of the original; screen readers say only "
        "“Equation not converted”." in text_of(equation)
    )
    heading = panel(screen(ux, "?item=p1-e0"))  # a bold heading, accepted
    assert re.search(r'data-role="reject"[^>]*>Make bold text<span', heading)
    assert ">Reject<" not in equation + heading
    response = post(ux, "p1-e2", action="reject", version="0")
    page = ux.browser.get(response.headers["location"]).text
    assert (
        "Equation 1: Kept as an image of the original. Screen readers will say “Equation not "
        "converted”. 2 decisions left." in text_of(page)
    )
    assert "Decided: Kept as an image by you" in panel(screen(ux, "?item=p1-e2"))


def test_undo_puts_the_last_decision_back(ux):
    response = post(ux, "p1-e2", action="edit", latex=r"\boldsymbol{\omega}", version="0")
    page = ux.browser.get(response.headers["location"]).text
    notice = section(page, '<div class="decision-notice"', "</div>")
    assert f'action="/jobs/{ux.job_id}/undo"' in notice
    assert 'name="version" value="1"' in notice
    assert '>Undo<span class="visually-hidden"> this decision</span>' in notice
    response = undo(ux, "1")
    assert response.status_code == 303
    assert response.headers["location"] == f"/jobs/{ux.job_id}/review?item=p1-e2"
    fix = stored(ux).fix_for("p1-e2")
    assert fix.status.value == "needs_review" and fix.equation.latex == r"\omega"
    assert not fix.reviewed
    assert [c.code for c in fix.checks if not c.passed] == ["style_mismatch"]  # as before
    assert version_of(ux) == 2
    assert [e.action for e in edits(ux)] == ["edit", "undo"]
    back = ux.browser.get(response.headers["location"]).text
    assert "Equation 1: Undone. It is waiting for your decision again. 3 decisions left." in (
        text_of(back)
    )
    heading = re.search(r'<h2 id="decision-title"[^>]*>', back).group(0)
    assert "autofocus" in heading  # focus is back on the decision
    assert "/undo" not in section(back, '<div class="decision-notice"', "</div>")  # no undo of undo
    # The output is the one from before the decision, re-rendered.
    assert "bold omega" not in (ux.job(ux.job_id).html or "")


def test_undo_a_group_decision_and_a_stale_or_empty_undo(ux):
    assert post(ux, GROUP, action="reject", version="0").status_code == 303
    assert undo(ux, "0").status_code == 303  # a page from before that decision: refused
    assert {f.status.value for f in stored(ux).fixes if f.group_key == GROUP} == {"rejected"}
    page = screen(ux)
    assert "Nothing was undone: this document changed after this page was opened." in page
    response = undo(ux, "1")
    assert response.headers["location"] == f"/jobs/{ux.job_id}/review?item={GROUP}"
    assert {f.status.value for f in stored(ux).fixes if f.group_key == GROUP} == {"needs_review"}
    back = ux.browser.get(response.headers["location"]).text
    assert "Repeated heading “Teacher Support”: Undone. They are waiting for your decision" in (
        text_of(back)
    )
    # Undoing again would undo the undo: not offered, and refused.
    undo(ux, "2")
    assert {f.status.value for f in stored(ux).fixes if f.group_key == GROUP} == {"needs_review"}
    assert "There is nothing to undo here." in screen(ux)
    assert undo(ux, "").status_code == 303 and version_of(ux) == 2  # no version: refused


def test_undo_in_the_list_view(ux):
    response = post(ux, "p1-e6", action="accept", version="0", view="list")
    page = ux.browser.get(response.headers["location"]).text
    assert "Undo the last decision" in page
    response = undo(ux, "1", view="list")
    assert response.headers["location"] == f"/jobs/{ux.job_id}/review/list#item-p1-e6"
    assert stored(ux).fix_for("p1-e6").status.value == "needs_review"


def test_your_decisions_are_listed_apart_from_the_automatic_ones(ux):
    assert "Your decisions" not in screen(ux)  # nothing decided yet
    post(ux, "p1-e2", action="edit", latex=r"\boldsymbol{\omega}", version="0")
    post(ux, GROUP, action="reject", version="1")
    page = screen(ux)
    box = section(page, '<details class="optional-box decided-box">', "</details>")
    assert '<summary id="decided-summary">Your decisions (2)</summary>' in box
    lines = [text_of(li) for li in re.findall(r"<li>.*?</li>", box, re.S)]
    assert lines == [
        "Equation 1, page 1 ω Edited by you.",  # bold by its mathvariant
        "Repeated heading, 3 times “Teacher Support” All 3 were made bold text.",
    ]
    assert f'href="/jobs/{ux.job_id}/review?item={GROUP}#decision-title"' in box
    assert 'aria-labelledby="optional-summary decided-summary"' in page


def test_a_heading_group_of_two_appears_twice(tmp_path):
    output = review_output()
    for fix in output.result.fixes:
        if fix.element_id == "p2-e0":
            fix.group_key = fix.group_size = None
        elif fix.group_key == GROUP:
            fix.group_size = 2
    env = WebEnv(tmp_path)
    env.converter.output = output
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        body = panel(screen(env, f"?item={GROUP}"))
        assert "“Teacher Support” appears twice as a bold line in the body text size." in body


def test_the_skip_link_goes_straight_to_the_decision(ux):
    page = screen(ux)
    links = re.findall(r'<a class="skip-link" href="([^"]+)">([^<]+)</a>', page)
    assert links == [("#main", "Skip to main content"), ("#decision-title", "Skip to the decision")]
    assert 'id="decision-title" tabindex="-1"' in page


# --------------------------------------------------------------------------- BW12, the text layer


def spoken(env, element_id: str, speech: str) -> None:
    """Give a stored proposal the reading the pipeline would (the fixture stores its
    LaTeX there)."""
    with session_scope(env.ctx.factory) as session:
        job = session.get(Job, env.job_id)
        assert job is not None and job.result_json is not None
        data = dict(job.result_json)
        data["fixes"] = [
            fix | {"equation": fix["equation"] | {"speech": speech}}
            if fix["element_id"] == element_id
            else fix
            for fix in data["fixes"]
        ]
        job.result_json = data


def test_the_card_says_what_a_screen_reader_gets_from_the_pdf(ux):
    spoken(ux, "p1-e2", "omega")
    body = panel(screen(ux))
    # The proposal's reading, and the PDF's text with its reading; the symbol that
    # differs is marked in both.
    assert (
        '<span class="speech-label">Read aloud roughly as:</span> “<mark class="said-diff">'
        "omega</mark>”" in body
    )
    assert (
        '<span class="speech-label">From your PDF a screen reader gets:</span> “<span '
        'class="text-layer">𝛚</span>”, roughly “<mark class="said-diff">bold omega</mark>”' in body
    )
    assert "Marked: the words where the two readings differ." in body
    # Nothing differs in an accepted equation: no reading of the PDF, nothing marked.
    other = panel(screen(ux, "?item=p2-e2"))
    assert 'From your PDF a screen reader gets:</span> “<span class="text-layer">v = d / t' in (
        other
    )
    assert "said-diff" not in other and "roughly “" not in other.split("screen reader gets")[1]


def test_an_equation_without_text_in_the_pdf_says_so(tmp_path):
    output = review_output()
    for element in output.result.elements:
        if element.id == "p1-e2":
            element.text = None
    env = WebEnv(tmp_path)
    env.converter.output = output
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        body = panel(screen(env))
        assert (
            "From your PDF a screen reader gets nothing here: the PDF has no text for this "
            "equation." in text_of(body)
        )
        listing = env.browser.get(f"/jobs/{env.job_id}/review/list").text
        assert "the PDF has no text for this equation." in listing


@pytest.mark.parametrize(
    ("said", "text_layer", "marked", "pdf_marked"),
    [
        ("omega", "𝛚", ["omega"], ["bold omega"]),
        ("a equals omega times bold r", "a = 𝛚 × 𝐫", ["omega"], ["bold omega"]),
        ("e to the power lambda t end power", "e−λt", [], ["minus"]),
        ("x squared plus 3", "x2 + 8", ["3"], ["8"]),
        ("capital R to the power n", "ℝn", ["capital R"], ["double-struck capital R"]),
    ],
)
def test_the_readings_mark_only_the_symbols_that_differ(said, text_layer, marked, pdf_marked):
    from a11y_stem.web.speech_diff import spoken_diff

    diff = spoken_diff(said, text_layer)
    assert diff is not None
    assert [s.text for s in diff.said if s.differs] == marked
    assert [s.text for s in diff.pdf_said if s.differs] == pdf_marked


@pytest.mark.parametrize(
    ("said", "text_layer"),
    [
        ("x squared plus y squared equals r squared", "x2 + y2 = r2"),  # flat superscripts
        ("sine theta", "sin θ"),  # a function name
        ("v equals d divided by t", "v = d / t"),
        ("the fraction a plus b over c end fraction", "a + b c"),  # structure words
        ("double-struck capital R to the power n", "Rn"),  # a style the PDF cannot carry
        ("omega", ""),
        ("", "𝛚"),
    ],
)
def test_structure_and_unseen_styles_are_never_marked(said, text_layer):
    from a11y_stem.web.speech_diff import spoken_diff

    assert spoken_diff(said, text_layer) is None


# --------------------------------------------------------------------------- MF19 session end

JOB = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize(
    ("path", "kept"),
    [
        ("/app", True),
        ("/admin", True),
        (f"/jobs/{JOB}", True),
        (f"/jobs/{JOB}/review", True),
        (f"/jobs/{JOB}/review?item=p1-e2&page=2&accepted=1", True),
        (f"/jobs/{JOB}/review/list", True),
        (f"/jobs/{JOB}/view", True),
        ("//elsewhere.example/app", False),
        ("/\\elsewhere.example", False),
        ("https://elsewhere.example/app", False),
        ("/app?next=//elsewhere.example", False),
        (f"/jobs/{JOB}/review?item=%3Cscript%3E", False),
        (f"/jobs/{JOB}/pages/1.png", False),  # an image asked for in the background
        (f"/jobs/{JOB}/review/marks.css", False),
        (f"/jobs/{JOB}/download", False),
        ("/jobs/not-a-job/review", False),
        ("/login", False),
        ("", False),
        (None, False),
    ],
)
def test_only_pages_of_this_app_are_come_back_to(path, kept):
    from a11y_stem.web.return_to import safe_return

    assert safe_return(path) == (path if kept else None)


def sign_out_elsewhere(env) -> None:
    """Sign out on another device: that ends every session of the account."""
    other = env.client()
    login(other, ALICE)
    assert other.post("/logout", data={"csrf_token": token(other)}).status_code == 303


def test_signing_in_comes_back_to_the_page_asked_for(ux):
    other = ux.client()  # another browser, signed out
    deep = f"/jobs/{ux.job_id}/review?item=p1-e6"
    response = other.get(deep)
    assert (response.status_code, response.headers["location"]) == (303, "/login")
    assert login(other, ALICE).headers["location"] == deep
    # Once: signing in again later goes to My documents.
    assert login(other, ALICE).headers["location"] == "/app"
    # Nothing outside the app's pages is remembered.
    fresh = ux.client()
    assert fresh.get(f"/jobs/{ux.job_id}/pages/1.png").headers["location"] == "/login"
    assert login(fresh, ALICE).headers["location"] == "/app"


def test_a_decision_after_the_session_ended_comes_back_after_sign_in(ux):
    csrf = token(ux.browser, f"/jobs/{ux.job_id}/review")
    sign_out_elsewhere(ux)
    fields = {"action": "edit", "latex": r"\boldsymbol{\omega}", "version": "0"}
    response = ux.browser.post(
        f"/jobs/{ux.job_id}/review/p1-e2", data={"csrf_token": csrf} | fields
    )
    assert response.status_code == 401
    page = response.text
    assert "<h1>Please sign in again</h1>" in page
    assert "Your session ended, so this was not saved." in page
    assert "Sign in again to come back to the same decision." in page
    assert 'href="/dev-login"' in page  # the sign-in choices are on the page itself
    # app.js shows this line when this tab kept a draft of that form.
    assert f'<p class="hint" data-draft-for="/jobs/{ux.job_id}/review/p1-e2" hidden>' in page
    assert "boldsymbol" not in page  # what was typed is never sent back by the server
    assert stored(ux).fix_for("p1-e2").status.value == "needs_review"
    response = login(ux.browser, ALICE)
    assert response.headers["location"] == f"/jobs/{ux.job_id}/review?item=p1-e2"
    assert "Decision 1 of 3" in screen(ux, "?item=p1-e2")


def test_a_decision_after_the_cookie_expired_says_sign_in_again(ux):
    """An expired cookie takes the CSRF token with it: the same page as above, not "This
    form has expired"; Undo comes back to the review screen."""
    other = ux.client()
    response = other.post(
        f"/jobs/{ux.job_id}/review/p1-e2", data={"csrf_token": "from-before", "action": "accept"}
    )
    assert response.status_code == 403
    assert "Please sign in again" in response.text
    assert login(other, ALICE).headers["location"] == f"/jobs/{ux.job_id}/review?item=p1-e2"
    expired = ux.client()
    expired.post(f"/jobs/{ux.job_id}/undo", data={"csrf_token": "from-before", "version": "1"})
    assert login(expired, ALICE).headers["location"] == f"/jobs/{ux.job_id}/review"
    # Other forms keep the plain message (signing in itself, say).
    assert "This form has expired" in ux.client().post("/login", data={}).text


def test_a_form_from_before_signing_in_again_links_back_to_its_decision(ux):
    csrf = token(ux.browser, f"/jobs/{ux.job_id}/review")
    login(ux.browser, ALICE)  # signed in again (in another tab): a new token
    response = ux.browser.post(
        f"/jobs/{ux.job_id}/review/p1-e2", data={"csrf_token": csrf, "action": "accept"}
    )
    assert response.status_code == 403
    assert "This form has expired" in response.text
    assert "Go back to the decision and choose again." in response.text
    assert f'<a href="/jobs/{ux.job_id}/review?item=p1-e2">Back to the decision</a>' in (
        response.text
    )
    assert stored(ux).fix_for("p1-e2").status.value == "needs_review"


def test_the_list_view_names_the_saved_item_so_its_draft_is_forgotten(ux):
    response = post(ux, "p1-e6", action="accept", version="0", view="list")
    page = ux.browser.get(response.headers["location"]).text
    assert f'data-saved="/jobs/{ux.job_id}/review/p1-e6"' in page
    assert page.count("data-edit-field") >= 3  # the fields whose drafts app.js keeps
