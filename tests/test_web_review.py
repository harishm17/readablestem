"""Review screen: ordering, display, accept / edit / reject, validation, re-render.

`pipeline.validate`, `pipeline.speech` and `pipeline.assemble` belong to other work
packages and are monkeypatched with small deterministic fakes here.
"""

from __future__ import annotations

import re
from html import escape

import pytest
from sqlalchemy import func, select

from a11y_stem.core.types import Check, DocumentResult
from a11y_stem.web.db import ReviewEdit, session_scope
from a11y_stem.web.mathml import UnsafeMathML, sanitize_mathml, try_sanitize_mathml
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, make_pdf, token, upload


class FakePipeline:
    def __init__(self) -> None:
        self.rendered: list[list] = []
        self.latex_checked: list[str] = []
        self.alt_checked: list[tuple] = []
        self.render_raises: Exception | None = None

    def check_latex(self, latex: str) -> list[Check]:
        self.latex_checked.append(latex)
        ok = "\\bad" not in latex
        return [
            Check(name="latex.allowed_commands", passed=ok, code=None if ok else "unknown_command")
        ]

    def safe_latex_to_mathml(self, latex: str, display: str = "block") -> str:
        from a11y_stem.pipeline.validate import LatexConversionError

        if "\\fail" in latex:
            raise LatexConversionError("cannot convert")
        return f'<math display="{display}"><mi>{escape(latex)}</mi></math>'

    def check_mathml(self, mathml: str) -> list[Check]:
        return [Check(name="mathml.well_formed", passed=True)]

    def check_alt(self, alt: str, *, decorative: bool, caption: str | None = None) -> list[Check]:
        self.alt_checked.append((alt, decorative, caption))
        ok = not alt.lower().startswith("image of")
        return [Check(name="alt.no_image_of", passed=ok, code=None if ok else "image_of")]

    def mathml_to_speech(self, mathml: str) -> str:
        return "spoken form"

    def render_html(self, *, title, lang, elements, fixes, crops, source_note=None) -> str:
        if self.render_raises is not None:
            raise self.render_raises
        self.rendered.append(fixes)
        statuses = ",".join(f"{f.element_id}:{f.status.value}" for f in fixes)
        return f"<html><body>rendered {statuses} crops={len(crops)}</body></html>"


@pytest.fixture
def web(tmp_path, monkeypatch):
    from a11y_stem.pipeline import assemble, speech, validate

    fake = FakePipeline()
    for name in ("check_latex", "safe_latex_to_mathml", "check_mathml", "check_alt"):
        monkeypatch.setattr(validate, name, getattr(fake, name))
    monkeypatch.setattr(speech, "mathml_to_speech", fake.mathml_to_speech)
    monkeypatch.setattr(assemble, "render_html", fake.render_html)
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.pipeline = fake
        env.job_id = job_id_from(upload(client, make_pdf(1)))
        env.run_worker()
        yield env


def result(env) -> DocumentResult:
    return DocumentResult.model_validate(env.job(env.job_id).result_json)


def edits(env) -> list[ReviewEdit]:
    with session_scope(env.ctx.factory) as session:
        return list(session.scalars(select(ReviewEdit).order_by(ReviewEdit.created_at)))


def post(env, element_id: str, **fields):
    data = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review")} | fields
    return env.browser.post(f"/jobs/{env.job_id}/review/{element_id}", data=data)


# --------------------------------------------------------------------------- page


def test_fixes_needing_review_come_first_then_unconverted_then_the_rest(web):
    page = web.browser.get(f"/jobs/{web.job_id}/review")
    assert page.status_code == 200
    order = re.findall(r'<li id="fix-([\w-]+)"', page.text)
    assert order == ["p1-e3", "p1-e4", "p1-e5", "p1-e0", "p1-e2"]  # paragraphs are not fixes


def test_each_fix_shows_crop_mathml_speech_editor_confidence_and_checks(web):
    page = web.browser.get(f"/jobs/{web.job_id}/review").text
    card = page[page.index('id="fix-p1-e3"') : page.index('id="fix-p1-e4"')]
    assert f'src="/jobs/{web.job_id}/crops/p1-e3.png"' in card
    assert 'alt="The original equation from page 1 of the PDF, as an image."' in card
    assert '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">' in card
    assert "x plus 1" in card
    assert "\\SECRETLATEX{x}</textarea>" in card
    assert '<label for="latex-p1-e3">' in card
    assert "55%" in card and "Needs review" in card
    assert "<code>unknown_command</code>" in card and "(warning)" in card
    for action in ("edit", "accept", "reject"):
        assert f'name="action" value="{action}"' in card

    figure = page[page.index('id="fix-p1-e4"') : page.index('id="fix-p1-e5"')]
    assert 'value="SECRET-ALT a rising line"' in figure
    assert '<label for="alt-p1-e4">' in figure and 'id="decorative-p1-e4"' in figure

    abstained = page[page.index('id="fix-p1-e5"') : page.index('id="fix-p1-e0"')]
    assert 'value="accept"' not in abstained  # nothing to accept
    assert "<code>low_confidence</code>" in abstained


# --------------------------------------------------------------------------- actions


def test_accept_updates_the_result_records_the_edit_and_rerenders(web):
    response = post(web, "p1-e3", action="accept")
    assert response.status_code == 303
    # It needed review, so it moves down the list: go back to the progress note, which
    # sits right above the next fix to review.
    assert response.headers["location"] == f"/jobs/{web.job_id}/review#review-progress"
    after = result(web)
    fix = after.fix_for("p1-e3")
    assert fix is not None and fix.status.value == "accepted"
    assert after.stats.by_status["accepted"] == 3
    [edit] = edits(web)
    assert (edit.action, edit.element_id) == ("accept", "p1-e3")
    assert edit.before["status"] == "needs_review" and edit.after["status"] == "accepted"
    job = web.job(web.job_id)
    assert job.html is not None and "p1-e3:accepted" in job.html
    assert "crops=3" in job.html  # the stored crops are passed to the assembler
    page = web.browser.get(f"/jobs/{web.job_id}/review").text
    progress = page[page.index('id="review-progress"') : page.index('class="fix-list"')]
    assert "Equation on page 1: Accepted." in progress
    assert "2 fixes still need your" in progress
    order = re.findall(r'<li id="fix-([\w-]+)"', page)
    assert order == ["p1-e4", "p1-e5", "p1-e0", "p1-e2", "p1-e3"]
    card = page[page.index('id="fix-p1-e3"') :]
    assert "Accepted." in card  # also shown inside the card that changed


def test_changing_an_already_reviewed_fix_returns_to_that_fix(web):
    post(web, "p1-e3", action="accept")
    response = post(web, "p1-e3", action="reject")
    assert response.headers["location"] == f"/jobs/{web.job_id}/review#fix-p1-e3"


def test_saving_an_equation_edit_validates_and_stores_it(web):
    response = post(web, "p1-e3", action="edit", latex="  x^2 + 1  ")
    assert response.status_code == 303
    assert web.pipeline.latex_checked == ["x^2 + 1"]
    fix = result(web).fix_for("p1-e3")
    assert fix is not None and fix.status.value == "edited" and fix.equation is not None
    assert fix.equation.latex == "x^2 + 1"
    assert fix.equation.mathml == '<math display="block"><mi>x^2 + 1</mi></math>'
    assert fix.equation.speech == "spoken form" and fix.equation.display == "block"
    assert [c.name for c in fix.checks] == ["latex.allowed_commands", "mathml.well_formed"]
    assert edits(web)[0].after["equation"]["latex"] == "x^2 + 1"


def test_an_equation_edit_that_fails_a_check_is_not_saved(web):
    before = result(web)
    response = post(web, "p1-e3", action="edit", latex="\\bad{y}")
    assert response.status_code == 422
    assert "<title>Error: Review fixes" in response.text
    assert '<a href="#latex-p1-e3">' in response.text  # error summary links to the field
    assert "latex.allowed_commands (unknown_command)" in response.text
    assert "\\bad{y}</textarea>" in response.text  # the reviewer's input is kept
    assert 'aria-invalid="true"' in response.text
    assert result(web) == before and edits(web) == []
    assert web.pipeline.rendered == []


def test_latex_that_cannot_become_mathml_is_not_saved(web):
    response = post(web, "p1-e3", action="edit", latex="\\fail")
    assert response.status_code == 422
    assert "could not be converted to MathML" in response.text
    assert edits(web) == []


def test_empty_latex_is_refused(web):
    response = post(web, "p1-e5", action="edit", latex="   ")
    assert response.status_code == 422
    assert "Enter the LaTeX" in response.text


def test_an_abstained_equation_can_be_fixed_by_editing(web):
    assert post(web, "p1-e5", action="edit", latex="E = mc^2").status_code == 303
    fix = result(web).fix_for("p1-e5")
    assert fix is not None and fix.status.value == "edited"
    assert fix.equation is not None and fix.equation.display == "block"


def test_figure_alt_text_edit_is_checked_against_the_caption(web):
    response = post(web, "p1-e4", action="edit", alt="A line   rising\nsteadily")
    assert response.status_code == 303
    assert web.pipeline.alt_checked == [
        ("A line rising steadily", False, "Figure 1 SECRET-CAPTION")
    ]
    fix = result(web).fix_for("p1-e4")
    assert fix is not None and fix.status.value == "edited" and fix.figure is not None
    assert fix.figure.alt == "A line rising steadily" and fix.figure.figure_type == "chart"


def test_figure_alt_text_failing_a_check_is_not_saved(web):
    response = post(web, "p1-e4", action="edit", alt="Image of a graph")
    assert response.status_code == 422
    assert '<a href="#alt-p1-e4">' in response.text
    assert 'value="Image of a graph"' in response.text
    assert edits(web) == []


def test_figure_can_be_marked_decorative(web):
    assert post(web, "p1-e4", action="edit", alt="ignored", decorative="on").status_code == 303
    fix = result(web).fix_for("p1-e4")
    assert fix is not None and fix.figure is not None
    assert fix.figure.decorative is True and fix.figure.alt == ""
    assert web.pipeline.alt_checked[-1] == ("", True, "Figure 1 SECRET-CAPTION")


def test_figure_needs_alt_text_or_decorative(web):
    response = post(web, "p1-e4", action="edit", alt="")
    assert response.status_code == 422
    assert "Enter alt text, or mark the figure as decorative." in response.text


def test_reject_keeps_the_original(web):
    assert post(web, "p1-e4", action="reject").status_code == 303
    fix = result(web).fix_for("p1-e4")
    assert fix is not None and fix.status.value == "rejected"
    assert "p1-e4:rejected" in (web.job(web.job_id).html or "")


def test_heading_level_can_be_changed(web):
    assert post(web, "p1-e0", action="edit", level="2").status_code == 303
    fix = result(web).fix_for("p1-e0")
    assert fix is not None and fix.heading is not None and fix.heading.level == 2
    assert post(web, "p1-e0", action="edit", level="9").status_code == 422


def test_nothing_to_accept_on_an_abstained_fix(web):
    response = post(web, "p1-e5", action="accept")
    assert response.status_code == 422
    assert '<a href="#actions-p1-e5">' in response.text


def test_unknown_action_or_element(web):
    assert post(web, "p1-e3", action="delete-everything").status_code == 422
    assert post(web, "p9-e9", action="accept").status_code == 404
    assert post(web, "p1-e1", action="accept").status_code == 404  # a paragraph: no fix
    assert post(web, "bad id!", action="accept").status_code == 404


def test_a_failed_rerender_changes_nothing(web):
    before = result(web)
    web.pipeline.render_raises = RuntimeError("SECRET-DOC-TEXT assembler bug")
    response = post(web, "p1-e3", action="accept")
    assert response.status_code == 500
    assert "SECRET" not in response.text
    assert result(web) == before
    with session_scope(web.ctx.factory) as session:
        assert session.scalar(select(func.count()).select_from(ReviewEdit)) == 0


# --------------------------------------------------------------------------- MathML


def test_sanitizer_rebuilds_mathml_from_an_allowlist():
    dirty = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block" onclick="x()" '
        'href="javascript:alert(1)"><mi style="color:red" mathvariant="bold">a&lt;b</mi>'
        '<semantics><mn>1</mn><annotation-xml encoding="text/html"><b>x</b></annotation-xml>'
        '<annotation encoding="application/x-tex">\\frac{1}{2}</annotation></semantics></math>'
    )
    clean = str(sanitize_mathml(dirty))
    assert clean == (
        '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">'
        '<mi mathvariant="bold">a&lt;b</mi><semantics><mn>1</mn>'
        '<annotation encoding="application/x-tex">\\frac{1}{2}</annotation></semantics></math>'
    )


@pytest.mark.parametrize(
    "bad",
    [
        "<math><script>alert(1)</script></math>",
        "<math><mi>x</mi><foreignObject/></math>",
        "<svg><mi>x</mi></svg>",
        "<math><mi>unclosed</math>",
        '<!DOCTYPE m [<!ENTITY a "aaaa">]><math><mi>&a;</mi></math>',
        "",
    ],
)
def test_sanitizer_refuses_anything_unexpected(bad):
    with pytest.raises(UnsafeMathML):
        sanitize_mathml(bad)
    assert try_sanitize_mathml(bad) is None
