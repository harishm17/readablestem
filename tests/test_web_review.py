"""Review screen: one fix at a time (display, accept / edit / reject, validation,
re-render) and the MathML sanitizer.

Grouping, that there is no bulk accept, and where the reviewer lands after a
decision are in test_web_review_bulk.py; the code -> sentence table in
test_web_review_messages.py; axe in a real browser in test_web_review_browser.py.

`pipeline.validate`, `pipeline.speech` and `pipeline.assemble` belong to other work
packages and are monkeypatched with small deterministic fakes here.
"""

from __future__ import annotations

import re
from html import escape, unescape

import pytest
from sqlalchemy import func, select

from fixtures.web.support import (
    ALICE,
    PNG,
    WebEnv,
    job_id_from,
    login,
    make_pdf,
    token,
    upload,
    with_file,
)
from readablestem.core.types import (
    Check,
    ConversionOutput,
    DocumentResult,
    DocumentStats,
    Element,
    EquationFix,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    Status,
)
from readablestem.web.db import ReviewEdit, session_scope
from readablestem.web.mathml import UnsafeMathML, sanitize_mathml, try_sanitize_mathml


class FakePipeline:
    def __init__(self) -> None:
        self.rendered: list[list] = []
        self.latex_checked: list[str] = []
        self.alt_checked: list[tuple] = []
        self.render_raises: Exception | None = None

    def check_latex(self, latex: str) -> list[Check]:
        self.latex_checked.append(latex)
        ok = "\\bad" not in latex
        warn = "\\warn" not in latex
        return [
            Check(name="latex.allowed_commands", passed=ok, code=None if ok else "unknown_command"),
            Check(
                name="latex.plain_text_arguments",
                passed=warn,
                severity="warning",
                code=None if warn else "markup_in_text",
            ),
        ]

    def safe_latex_to_mathml(self, latex: str, display: str = "block") -> str:
        from readablestem.pipeline.validate import LatexConversionError

        if "\\fail" in latex:
            raise LatexConversionError("cannot convert")
        return f'<math display="{display}"><mi>{escape(latex)}</mi></math>'

    def check_mathml(self, mathml: str) -> list[Check]:
        return [Check(name="mathml.well_formed", passed=True)]

    def check_alt(self, alt: str, *, decorative: bool, caption: str | None = None) -> list[Check]:
        self.alt_checked.append((alt, decorative, caption))
        ok = not alt.lower().startswith("image of")
        return [
            Check(
                name="alt.no_redundant_prefix", passed=ok, code=None if ok else "redundant_prefix"
            )
        ]

    def mathml_to_speech(self, mathml: str) -> str:
        return "spoken form"

    def render_html(
        self,
        *,
        title,
        lang,
        elements,
        fixes,
        crops,
        source_note=None,
        checked_on=None,
        **_options,
    ) -> str:
        if self.render_raises is not None:
            raise self.render_raises
        self.rendered.append(fixes)
        statuses = ",".join(f"{f.element_id}:{f.status.value}" for f in fixes)
        return f"<html><body>rendered {statuses} crops={len(crops)}</body></html>"


def install_fake_pipeline(monkeypatch) -> FakePipeline:
    from readablestem.pipeline import assemble, speech, validate

    fake = FakePipeline()
    for name in ("check_latex", "safe_latex_to_mathml", "check_mathml", "check_alt"):
        monkeypatch.setattr(validate, name, getattr(fake, name))
    monkeypatch.setattr(speech, "mathml_to_speech", fake.mathml_to_speech)
    monkeypatch.setattr(assemble, "render_html", fake.render_html)
    return fake


@pytest.fixture
def web(tmp_path, monkeypatch):
    """The small fake document (fixtures.web.support.fake_result): heading p1-e0 accepted,
    inline equation p1-e2 accepted, equation p1-e3 and figure p1-e4 need review,
    equation p1-e5 abstained."""
    fake = install_fake_pipeline(monkeypatch)
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


def settled(env) -> DocumentResult:
    """The stored result once the review screen has been shown. The fake result has no
    page sizes (like one stored before they existed), and the first view of the review
    screen stores them; a "nothing changed" comparison starts from there."""
    env.browser.get(f"/jobs/{env.job_id}/review")
    return result(env)


def edits(env) -> list[ReviewEdit]:
    with session_scope(env.ctx.factory) as session:
        return list(session.scalars(select(ReviewEdit).order_by(ReviewEdit.created_at)))


def post(env, element_id: str, **fields):
    data = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review"), "view": "list"} | fields
    return env.browser.post(f"/jobs/{env.job_id}/review/{element_id}", data=data)


def review_page(env) -> str:
    response = env.browser.get(f"/jobs/{env.job_id}/review/list")
    assert response.status_code == 200
    return response.text


def cards(page: str) -> list[str]:
    """Element ids of the fix cards, in page order."""
    return re.findall(r'<article id="fix-([\w-]+)"', page)


def card(page: str, element_id: str) -> str:
    """The markup of one fix's list item (its notices, card and form): up to the next
    fix's item or the end of the list."""
    start = page.index(f'<li id="item-{element_id}"')
    ends = [page.find(marker, start + 1) for marker in ('<li id="item-', "</ol>")]
    return page[start : min(e for e in ends if e != -1)]


# --------------------------------------------------------------------------- rich document


def rich_output() -> ConversionOutput:
    """Three pages with every status in every group, listed in scrambled order so the
    review screen's sort is exercised. See test_web_review_bulk.py for the expectations.

    Equations: p1-e1 NR 0.82, p2-e1 FAILED, p2-e2 NR 0.65 (style warning), p2-e3 ACCEPTED,
    p3-e0 NR 0.90 but an error-level check failed, p3-e4 EDITED.
    Figures: p1-e2 NR 0.70 (caption warning), p3-e1 ABSTAINED, p3-e2 FAILED, p3-e5 REJECTED.
    Headings: p1-e0 ACCEPTED, p1-e3 NR 0.60, p2-e0 NR 0.45, p2-e4 ABSTAINED, p3-e3 NR 0.88.
    """
    layout = [  # (id, kind, page)
        ("p1-e0", Kind.HEADING, 0), ("p1-e1", Kind.EQUATION, 0), ("p1-e2", Kind.FIGURE, 0),
        ("p1-e3", Kind.HEADING, 0), ("p2-e0", Kind.HEADING, 1), ("p2-e1", Kind.EQUATION, 1),
        ("p2-e2", Kind.EQUATION, 1), ("p2-e3", Kind.EQUATION, 1), ("p2-e4", Kind.HEADING, 1),
        ("p3-e0", Kind.EQUATION, 2), ("p3-e1", Kind.FIGURE, 2), ("p3-e2", Kind.FIGURE, 2),
        ("p3-e3", Kind.HEADING, 2), ("p3-e4", Kind.EQUATION, 2), ("p3-e5", Kind.FIGURE, 2),
    ]  # fmt: skip
    elements = [
        Element(
            id=eid,
            kind=kind,
            page=page,
            bbox=(0, order, 1, order + 1),
            order=order,
            text=f"SECRET-DOC-TEXT {eid}" if kind is Kind.HEADING else None,
            display="block" if kind is Kind.EQUATION else None,
            caption="Figure SECRET-CAPTION" if kind is Kind.FIGURE else None,
        )
        for order, (eid, kind, page) in enumerate(layout)
    ]

    def eq(latex: str) -> EquationFix:
        return EquationFix(latex=latex, mathml=f"<math><mi>{latex}</mi></math>", speech=latex)

    def warn(name: str, code: str) -> Check:
        return Check(name=name, passed=False, severity="warning", code=code)

    def head(eid: str, level: int) -> HeadingFix:
        return HeadingFix(level=level, text=f"SECRET-HEADING {eid}")

    weak = warn("heading.evidence", "heading.weak_evidence")
    fixes = [
        Fix(element_id="p1-e0", kind=Kind.HEADING, status=Status.ACCEPTED, confidence=0.95,
            heading=head("p1-e0", 1)),
        Fix(element_id="p1-e1", kind=Kind.EQUATION, status=Status.NEEDS_REVIEW, confidence=0.82,
            equation=eq("a")),
        Fix(element_id="p1-e2", kind=Kind.FIGURE, status=Status.NEEDS_REVIEW, confidence=0.70,
            figure=FigureFix(figure_type="chart", alt="SECRET-ALT rising line"),
            checks=[warn("alt.not_caption_copy", "caption_copy")]),
        Fix(element_id="p1-e3", kind=Kind.HEADING, status=Status.NEEDS_REVIEW, confidence=0.60,
            heading=head("p1-e3", 2), checks=[weak]),
        Fix(element_id="p2-e0", kind=Kind.HEADING, status=Status.NEEDS_REVIEW, confidence=0.45,
            heading=head("p2-e0", 2),
            checks=[weak, warn("heading.all_caps", "heading.all_caps")]),
        Fix(element_id="p2-e1", kind=Kind.EQUATION, status=Status.FAILED, confidence=0.7,
            reason="invalid_latex",
            checks=[Check(name="latex.allowed_commands", passed=False, code="unknown_command")]),
        Fix(element_id="p2-e2", kind=Kind.EQUATION, status=Status.NEEDS_REVIEW, confidence=0.65,
            equation=eq("b"), checks=[warn("equation.text_layer_style", "style_mismatch")]),
        Fix(element_id="p2-e3", kind=Kind.EQUATION, status=Status.ACCEPTED, confidence=0.97,
            equation=eq("c")),
        Fix(element_id="p2-e4", kind=Kind.HEADING, status=Status.ABSTAINED,
            reason="heading.empty_text",
            checks=[Check(name="heading.text", passed=False, code="heading.empty_text")]),
        Fix(element_id="p3-e0", kind=Kind.EQUATION, status=Status.NEEDS_REVIEW, confidence=0.90,
            equation=eq("d"),
            checks=[Check(name="mathml.structure", passed=False, code="bad_arity")]),
        Fix(element_id="p3-e1", kind=Kind.FIGURE, status=Status.ABSTAINED, confidence=0.2,
            reason="low_confidence"),
        Fix(element_id="p3-e2", kind=Kind.FIGURE, status=Status.FAILED, reason="model_timeout"),
        Fix(element_id="p3-e3", kind=Kind.HEADING, status=Status.NEEDS_REVIEW, confidence=0.88,
            heading=head("p3-e3", 3), checks=[warn("heading.outline", "heading.level_repaired")]),
        Fix(element_id="p3-e4", kind=Kind.EQUATION, status=Status.EDITED, confidence=0.5,
            equation=eq("e")),
        Fix(element_id="p3-e5", kind=Kind.FIGURE, status=Status.REJECTED, confidence=0.5,
            figure=FigureFix(figure_type="photo", alt="SECRET-ALT photo")),
    ]  # fmt: skip
    fixes.reverse()  # stored order must not matter
    by_status: dict[str, int] = {}
    for fix in fixes:
        by_status[fix.status.value] = by_status.get(fix.status.value, 0) + 1
    result = DocumentResult(
        pipeline_version="test",
        title="SECRET-HEADING Rich",
        elements=elements,
        fixes=fixes,
        stats=DocumentStats(
            pages=3, cost_usd=0.02, latency_ms=5, model_calls=4, by_status=by_status
        ),
        html="<!doctype html><html lang='en'><head><title>t</title></head><body></body></html>",
    )
    crops = {e.id: PNG for e in elements if e.kind in (Kind.EQUATION, Kind.FIGURE)}
    return ConversionOutput(result=result, crops=crops)


# --------------------------------------------------------------------------- page


# --------------------------------------------------------------------------- actions


def test_accept_updates_the_result_records_the_edit_and_rerenders(web):
    response = post(web, "p1-e3", action="accept")
    assert response.status_code == 303
    # Next fix still waiting after equation 2 is the abstained equation 3.
    assert response.headers["location"] == f"/jobs/{web.job_id}/review/list#item-p1-e5"
    after = result(web)
    fix = after.fix_for("p1-e3")
    assert fix is not None and fix.status.value == "accepted"
    assert after.stats.by_status["accepted"] == 3
    [edit] = edits(web)
    assert (edit.action, edit.element_id) == ("accept", "p1-e3")
    assert edit.before["status"] == "needs_review" and edit.after["status"] == "accepted"
    assert web.job(web.job_id).html_stale  # the file is made when somebody asks for it
    job = with_file(web)
    assert not job.html_stale
    assert job.html is not None and "p1-e3:accepted" in job.html
    assert "crops=3" in job.html  # the stored crops are passed to the assembler
    page = review_page(web)
    # The reviewer's decisions come before what was accepted for them.
    assert cards(page) == ["p1-e5", "p1-e4", "p1-e3", "p1-e2", "p1-e0"]
    # The notice sits where the redirect lands, linked back to the fix that changed.
    landing = card(page, "p1-e5")
    assert '<a href="#item-p1-e3">Equation 2 on page 1</a>: Accepted.' in landing
    assert landing.index("Accepted.") < landing.index('<article id="fix-p1-e5"')
    # The changed fix is decided now: the folded list of decided fixes opens to show it.
    changed = card(page, "p1-e3")
    assert '<details class="done-item" open>' in changed
    assert "<strong>Just changed:</strong> Accepted." in changed
    assert '<details class="done-group" open>' in page
    progress = page[page.index('id="review-progress"') : page.index('class="review-toc"')]
    assert "Accepted." in progress
    assert "2 decisions left" in progress


def test_when_nothing_is_left_the_reviewer_lands_on_the_progress_summary(web):
    post(web, "p1-e3", action="accept")
    post(web, "p1-e5", action="reject")
    response = post(web, "p1-e4", action="reject", acknowledge="on")
    assert response.headers["location"] == f"/jobs/{web.job_id}/review/list#review-progress"
    page = review_page(web)
    progress = page[page.index('id="review-progress"') : page.index('class="review-toc"')]
    # "Reject" is named for what it does, and so is its notice.
    assert '<a href="#item-p1-e4">Figure 1 on page 1</a>: Kept without a description' in progress
    assert "Nothing left to decide." in progress


def test_an_equation_edit_that_fails_a_check_is_not_saved(web):
    before = settled(web)
    response = post(web, "p1-e3", action="edit", latex="\\bad{y}")
    assert response.status_code == 422
    assert "<title>Error: All items as a list" in response.text
    assert '<a href="#latex-p1-e3">' in response.text  # error summary links to the field
    # Explained in words that name the problem; the code is for the Details, not here.
    assert (
        "Your LaTeX was not saved. \\bad is not a command this tool supports: check for a "
        "typo, or write it another way." in unescape(response.text)
    )
    assert "(latex.allowed_commands: unknown_command)" not in response.text
    assert "\\bad{y}</textarea>" in response.text  # the reviewer's input is kept
    assert 'aria-invalid="true"' in response.text
    assert result(web) == before and edits(web) == []
    assert web.pipeline.rendered == []


def test_empty_latex_is_refused(web):
    response = post(web, "p1-e5", action="edit", latex="   ")
    assert response.status_code == 422
    assert "Enter the LaTeX" in response.text


def test_figure_needs_alt_text_or_decorative(web):
    response = post(web, "p1-e4", action="edit", alt="")
    assert response.status_code == 422
    assert "Enter alt text, or mark the figure as decorative." in response.text


def test_a_proposal_that_failed_a_required_check_cannot_be_accepted_as_is(web):
    from readablestem.web.db import Job

    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, web.job_id)
        assert job is not None and job.result_json is not None
        data = DocumentResult.model_validate(job.result_json)
        fix = data.fix_for("p1-e4")
        assert fix is not None
        fix.checks = [Check(name="alt.length", passed=False, severity="error", code="too_long")]
        job.result_json = data.model_dump(mode="json", exclude={"html"})
    figure = card(review_page(web), "p1-e4")
    assert 'value="accept"' not in figure
    assert "<strong>Must be fixed:</strong> The alt text is long: aim for 150" in figure
    response = post(web, "p1-e4", action="accept")
    assert response.status_code == 422
    assert "failed a required check" in response.text
    assert post(web, "p1-e4", action="edit", alt="A rising line").status_code == 303


def test_unknown_action_or_element(web):
    assert post(web, "p1-e3", action="delete-everything").status_code == 422
    assert post(web, "p9-e9", action="accept").status_code == 404
    assert post(web, "p1-e1", action="accept").status_code == 404  # a paragraph: no fix
    assert post(web, "bad id!", action="accept").status_code == 404


def test_free_text_in_the_action_field_never_reaches_the_logs(web, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    response = post(web, "p1-e3", action="accept\nSECRET-DOC-TEXT fake log line")
    assert response.status_code == 422
    [record] = [r for r in caplog.records if r.getMessage() == "review_invalid"]
    assert record.a11y_fields["action"] == "unknown"
    assert "SECRET" not in caplog.text


def test_a_failed_render_loses_no_decision_and_shows_no_content(web):
    """The click stores the decision; making the file is done when it is asked for, so an
    assembler bug fails that request (500, no document text) and the next one that works
    makes the file with the decision in it."""
    web.pipeline.render_raises = RuntimeError("SECRET-DOC-TEXT assembler bug")
    response = post(web, "p1-e3", action="accept")
    assert response.status_code == 303
    assert result(web).fix_for("p1-e3").status.value == "accepted"  # type: ignore[union-attr]
    with session_scope(web.ctx.factory) as session:
        assert session.scalar(select(func.count()).select_from(ReviewEdit)) == 1
    assert web.job(web.job_id).html_stale
    failed = web.browser.get(f"/jobs/{web.job_id}/download")
    assert failed.status_code == 500 and "SECRET" not in failed.text
    assert web.job(web.job_id).html_stale  # nothing was half-written
    web.pipeline.render_raises = None
    assert "p1-e3:accepted" in (with_file(web).html or "")


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


# --------------------------------------------------------------------------- cleaning


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("$v_f = v_0 + at$", "v_f = v_0 + at"),  # dollars a reviewer pasted in
        ("\\[ x^2 \\]", "x^2"),
        ("50% of g", "50\\% of g"),  # a bare % would end the line as a comment
    ],
)
def test_reviewer_latex_is_cleaned_like_model_output(web, typed, stored):
    assert post(web, "p1-e3", action="edit", latex=typed).status_code == 303
    assert web.pipeline.latex_checked == [stored]  # validated as it is stored
    fix = result(web).fix_for("p1-e3")
    assert fix is not None and fix.equation is not None and fix.equation.latex == stored


def test_reviewer_alt_text_is_cleaned_like_model_output(web):
    """Bidi controls and zero-width characters are invisible but change what a screen
    reader says; check_alt refuses them uncleaned, so the reviewer's text is cleaned
    first and what is stored is exactly what was checked."""
    typed = "A line\u202e rising\u200b steadily\u2066"
    assert post(web, "p1-e4", action="edit", alt=typed).status_code == 303
    assert web.pipeline.alt_checked == [
        ("A line rising steadily", False, "Figure 1 SECRET-CAPTION")
    ]
    fix = result(web).fix_for("p1-e4")
    assert fix is not None and fix.figure is not None
    assert fix.figure.alt == "A line rising steadily"


# --------------------------------------------------------------------------- heading levels


@pytest.mark.parametrize("level", ["\u00b2", "\u0663", "0", "7", "1 ", "+2", "", "\uff11"])
def test_heading_level_accepts_only_the_digits_1_to_6(web, level):
    """ "\u00b2".isdigit() is True and int("\u00b2") raises: that was a 500."""
    response = post(web, "p1-e0", action="edit", level=level)
    assert response.status_code == 422
    assert "Choose a heading level from 1 to 6." in response.text


def outline_output() -> ConversionOutput:
    """h1 Motion, h2 Speed, h3 Units, then a paragraph: a clean outline."""
    output = rich_output()
    headings = [("p1-e0", 1), ("p1-e1", 2), ("p1-e2", 3)]
    output.result.elements = [
        Element(id=eid, kind=Kind.HEADING, page=0, bbox=(0, i, 1, i + 1), order=i,
                text=f"SECRET-HEADING {eid}")
        for i, (eid, _) in enumerate(headings)
    ] + [Element(id="p1-e3", kind=Kind.PARAGRAPH, page=0, bbox=(0, 3, 1, 4), order=3,
                 text="SECRET-DOC-TEXT")]  # fmt: skip
    output.result.fixes = [
        Fix(element_id=eid, kind=Kind.HEADING, status=Status.NEEDS_REVIEW, confidence=0.6,
            heading=HeadingFix(level=level, text=f"SECRET-HEADING {eid}"))
        for eid, level in headings
    ]  # fmt: skip
    output.crops = {}
    return output


@pytest.fixture
def outline(tmp_path, monkeypatch):
    fake = install_fake_pipeline(monkeypatch)
    env = WebEnv(tmp_path)
    env.converter.output = outline_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.pipeline = fake
        env.job_id = job_id_from(upload(client, make_pdf(1)))
        env.run_worker()
        yield env


def levels(env) -> dict[str, int]:
    return {f.element_id: f.heading.level for f in result(env).fixes if f.heading}


def test_a_level_the_outline_repair_would_change_is_refused(outline):
    """The output never skips a level: h1, h2, then h4 would be written as h3. Storing
    4 and showing 3 would make the review screen disagree with the document."""
    response = post(outline, "p1-e2", action="edit", level="4")
    assert response.status_code == 422
    assert (
        "Level 4 would skip a level after the heading before it, so the document would "
        "show it as level 3. Choose a level from 1 to 3, or change the heading before it "
        "first."
    ) in response.text
    assert '<a href="#level-p1-e2">' in response.text
    assert levels(outline) == {"p1-e0": 1, "p1-e1": 2, "p1-e2": 3}
    assert edits(outline) == [] and outline.pipeline.rendered == []
    for level, written in (("2", 2), ("1", 1)):  # siblings and parents always fit
        assert post(outline, "p1-e2", action="edit", level=level).status_code == 303
        assert levels(outline)["p1-e2"] == written


# Every decision path must agree with the output: a level the reviewer accepts is the
# level the document shows, and the review screen shows the level the document writes.
# These run the real assembler (no fixture here patches it).


def heading_doc(*spec: int | None) -> DocumentResult:
    """Headings p1-e0, p1-e1, ... waiting for review at the levels in `spec`; None is a
    heading the reviewer rejected (written as a paragraph)."""
    elements = [
        Element(id=f"p1-e{i}", kind=Kind.HEADING, page=0, bbox=(0, i, 1, i + 1), order=i,
                text=f"Heading {i}")
        for i in range(len(spec))
    ]  # fmt: skip
    fixes = [
        Fix(element_id=f"p1-e{i}", kind=Kind.HEADING, confidence=0.9,
            status=Status.REJECTED if level is None else Status.NEEDS_REVIEW,
            heading=HeadingFix(level=level or 2, text=f"Heading {i}"))
        for i, level in enumerate(spec)
    ]  # fmt: skip
    return DocumentResult(
        pipeline_version="t",
        elements=elements,
        fixes=fixes,
        stats=DocumentStats(pages=1, cost_usd=0, latency_ms=0, model_calls=0),
    )


def written_levels(data: DocumentResult) -> dict[str, int]:
    """element_id -> N of the <hN> the real assembler writes for it, without the move down
    that gives a file with several top-level headings one title (T3-13, `title_shift`): the
    review screen speaks of the outline repair's levels."""
    from unittest.mock import patch

    from readablestem.pipeline.assemble import render_html

    with patch("readablestem.pipeline.assemble.title_shift", return_value=0):
        html = render_html(title="t", lang="en", elements=data.elements, fixes=data.fixes, crops={})
    return {eid: int(n) for n, eid in re.findall(r'<h([1-6]) id="(p1-e\d+)"', html)}


def test_rejecting_and_restoring_a_heading_moves_its_children_and_back():
    from readablestem.web.review import apply_review

    data = heading_doc(1, 2, 3, 3, 2)
    assert apply_review(data, "p1-e1", "reject", {}).moved == ["p1-e2", "p1-e3"]
    assert written_levels(data) == {"p1-e0": 1, "p1-e2": 2, "p1-e3": 2, "p1-e4": 2}
    assert apply_review(data, "p1-e1", "accept", {}).moved == ["p1-e2", "p1-e3"]
    assert written_levels(data) == {"p1-e0": 1, "p1-e1": 2, "p1-e2": 3, "p1-e3": 3, "p1-e4": 2}
    assert apply_review(data, "p1-e4", "reject", {}).moved == []  # nothing after it


def test_moved_headings_are_named_then_counted():
    from readablestem.web.routes.review import _moved_message

    numbers = {f"p1-e{i}": i + 1 for i in range(9)}
    assert _moved_message([], numbers) == ""
    assert _moved_message(["p1-e2"], numbers) == " Heading 3 is now shown at a different level."
    assert _moved_message(["p1-e2", "p1-e3", "p1-e5"], numbers) == (
        " Headings 3, 4 and 6 are now shown at a different level."
    )
    many = [f"p1-e{i}" for i in range(1, 8)]
    assert _moved_message(many, numbers) == " 7 later headings are now shown at a different level."


# --------------------------------------------------------------------------- after a decision


def test_a_reviewers_accept_is_marked_as_the_reviewers(web):
    assert post(web, "p1-e3", action="accept").status_code == 303
    fixes = {f.element_id: f for f in result(web).fixes}
    assert fixes["p1-e3"].status is Status.ACCEPTED and fixes["p1-e3"].reviewed
    assert fixes["p1-e2"].status is Status.ACCEPTED and not fixes["p1-e2"].reviewed
    assert post(web, "p1-e4", action="reject", acknowledge="on").status_code == 303
    assert result(web).fix_for("p1-e4").reviewed  # type: ignore[union-attr]
    decided = card(review_page(web), "p1-e3")
    assert "Accepted by you" in decided
