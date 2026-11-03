"""A second reader's reading of handwritten display equations, against the page's
(`pipeline.image_page_reconcile`, docs/math-readers.md "Using a reader in the pipeline").

Offline: readers are fakes, or a real `ClaudeReader` over a transport that never sends.
"""

from __future__ import annotations

from typing import Any

import pytest

from image_page_support import block, scan_pdf
from readablestem.config import ConfigError, Settings
from readablestem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget
from readablestem.core.types import (
    IMAGE_PAGE_REASON,
    Check,
    Element,
    EquationFix,
    Fix,
    Kind,
    Status,
)
from readablestem.models.fake import FakeVisionClient
from readablestem.models.math_readers import build_readers
from readablestem.models.math_readers.base import Candidate, ReaderCall, ReadResult
from readablestem.models.math_readers.fake import FakeTransport
from readablestem.pipeline import equation_step, run
from readablestem.pipeline import image_page_reconcile as rec

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


class FakeReader:
    """Answers each read from a script: LaTeX, an Exception to raise, or an outcome code."""

    name = "local"
    model = "fake"
    max_read_s = 1.0

    def __init__(self, *script: str | Exception) -> None:
        self.script = list(script)
        self.reads = 0

    def read(self, request: Any) -> ReadResult:
        self.reads += 1
        item = self.script.pop(0) if self.script else "x"
        if isinstance(item, Exception):
            raise item
        if item in {"timeout", "refused", "invalid_output", "api_error"}:
            return ReadResult([], ReaderCall("local", "fake", outcome=item))  # type: ignore[arg-type]
        return ReadResult([Candidate(item)], ReaderCall("local", "fake", cost_usd=0.001))


def settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {"math_reader_local_enabled": True, "image_pages_second_reader": "local"}
    return Settings(_env_file=None, **{**base, **kw})  # type: ignore[call-arg]


def equation(eid: str, latex: str, *, reasons: tuple[str, ...] = (IMAGE_PAGE_REASON,)):
    mathml, _ = equation_step._convert(latex, "block")
    element = Element(
        id=eid, kind=Kind.EQUATION, page=0, bbox=(0, 0, 100, 20), order=0, display="block",
        reasons=list(reasons),
    )  # fmt: skip
    fix = Fix(
        element_id=eid, kind=Kind.EQUATION, status=Status.NEEDS_REVIEW, confidence=0.6,
        equation=EquationFix(latex=latex, mathml=mathml or "", speech="", display="block"),
        review="required",
        checks=[Check(name="equation.no_text_layer", passed=False, severity="warning",
                      code="no_text_layer")],
    )  # fmt: skip
    return element, fix


def run_one(page_latex: str, *script: str | Exception, **kw: Any):
    element, fix = equation("p1-e0", page_latex, **kw)
    reader = FakeReader(*script)
    summary = rec.reconcile([element], {fix.element_id: fix}, {element.id: PNG}, reader, settings())
    return element, fix, summary, reader


def note(fix: Fix) -> Check | None:
    found = [c for c in fix.checks if c.name == rec.NOTE]
    assert len(found) <= 1
    return found[0] if found else None


# ------------------------------------------------------------------ the three outcomes


def test_agreement_is_recorded_and_the_item_stays_a_required_decision():
    element, fix, summary, _ = run_one(r"x^{2} + y = 3", r"x ^ 2 + y = 3")
    check = note(fix)
    assert check is not None and (check.code, check.passed) == (rec.AGREES, True)
    assert (fix.status, fix.review) == (Status.NEEDS_REVIEW, "required") and fix.pending
    assert element.alternatives is None
    assert (summary.agreed, summary.differed) == (1, 0)


def test_the_same_words_aloud_count_as_agreement():
    _, fix, _, _ = run_one(r"\mathrm{d}x", r"\operatorname{d}x")
    assert note(fix).code == rec.AGREES  # type: ignore[union-attr]


def test_a_difference_in_one_symbol_gives_choices_and_a_note():
    element, fix, summary, _ = run_one(r"X + y = 2", r"x + y = 2")
    check = note(fix)
    assert check is not None and (check.code, check.passed) == (rec.DIFFERS, False)
    alt = element.alternatives
    assert alt is not None and alt.latex == fix.equation.latex  # type: ignore[union-attr]
    [symbol] = alt.symbols
    assert (symbol.symbol, symbol.options, symbol.kind) == ("X", ["x"], "case")
    assert alt.apply(0, 0) == "x + y = 2"
    assert (fix.status, fix.review) == (Status.NEEDS_REVIEW, "required")  # nothing is chosen
    assert fix.equation.latex == "X + y = 2"  # type: ignore[union-attr]
    assert (summary.differed, summary.with_choices) == (1, 1)


def test_choices_are_bounded_and_only_convertible_ones_stay():
    # four stretches differ: more than a symbol's doubt, so a note and no choices
    element, fix, _, _ = run_one(r"a + b + c + d + e", r"p + q + r + s + e")
    assert note(fix).code == rec.DIFFERS and element.alternatives is None  # type: ignore[union-attr]
    # one stretch differs but the other reading's LaTeX does not convert in place
    element, fix, _, _ = run_one(r"x + 1", r"x + \frac{")
    assert element.alternatives is None


def test_a_wholly_different_reading_is_a_note_without_choices():
    element, fix, _, _ = run_one(r"x = 1", r"\int_0^1 f(t)\,dt = \sum_{n=1}^{N} a_n")
    assert note(fix).code == rec.DIFFERS and element.alternatives is None  # type: ignore[union-attr]


def test_a_reading_that_does_not_convert_is_no_second_reading():
    element, fix, summary, _ = run_one(r"x = 1", r"\frac{x")
    assert note(fix) is None and element.alternatives is None
    assert summary.unavailable == 1 and summary.differed == 0


@pytest.mark.parametrize("outcome", ["timeout", "refused", "invalid_output", "api_error"])
def test_a_reader_with_no_answer_leaves_the_item_as_it_was(outcome):
    element, fix, summary, _ = run_one(r"x = 1", outcome)
    assert note(fix) is None  # no per-equation line: the document gets one notice
    assert element.alternatives is None and (fix.status, fix.review) == (
        Status.NEEDS_REVIEW,
        "required",
    )
    assert summary.unavailable == 1


def test_a_reader_that_raises_costs_one_expression_its_second_reading():
    a, fa = equation("p1-e0", "x = 1")
    b, fb = equation("p1-e1", "y = 2")
    reader = FakeReader(RuntimeError("boom"), "y = 2")
    summary = rec.reconcile(
        [a, b],
        {fa.element_id: fa, fb.element_id: fb},
        {a.id: PNG, b.id: PNG},
        reader,
        settings(),
    )
    assert (note(fa), note(fb).code) == (None, rec.AGREES)  # type: ignore[union-attr]
    assert (summary.unavailable, summary.agreed, summary.read) == (1, 1, 1)


def test_a_setup_failure_stops_asking_and_leaves_notes():
    a, fa = equation("p1-e0", "x = 1")
    b, fb = equation("p1-e1", "y = 2")
    reader = FakeReader(ConfigError("no model files"))
    rec.reconcile(
        [a, b],
        {fa.element_id: fa, fb.element_id: fb},
        {a.id: PNG, b.id: PNG},
        reader,
        settings(),
    )
    assert reader.reads == 1  # the second is not asked: it would fail the same way
    assert (note(fa), note(fb)) == (None, None)


def test_a_read_is_not_started_unless_its_worst_case_fits_the_time_share():
    a, fa = equation("p1-e0", "x = 1")
    b, fb = equation("p1-e1", "y = 2")
    now = iter([0.0, 1.0, 9.5, 9.5, 9.5])  # the second read could take 1 s: 9.5 + 1 > 10
    reader = FakeReader("x = 1", "y = 2")
    summary = rec.reconcile(
        [a, b],
        {fa.element_id: fa, fb.element_id: fb},
        {a.id: PNG, b.id: PNG},
        reader,
        settings(image_pages_second_reader_time_s=10),
        clock=lambda: next(now),
    )
    assert reader.reads == 1
    assert (note(fa).code, note(fb)) == (rec.AGREES, None)  # type: ignore[union-attr]
    assert (summary.unavailable, summary.read) == (1, 1)
    assert [n.code for n in summary.notices()] == ["second_reader_unavailable"]


def test_the_share_never_outlasts_what_the_jobs_own_limit_leaves():
    s = settings(image_pages_second_reader_time_s=120)
    assert rec.share_s(s, None, 50) == 120
    # a 100 s job keeps 20% for everything after: 100 * 0.8 - 30 elapsed = 50
    assert rec.share_s(s, 100, 30) == pytest.approx(50)
    assert rec.share_s(s, 100, 95) == 0  # nothing left: no read is started
    reader = FakeReader()
    a, fa = equation("p1-e0", "x = 1")
    rec.reconcile([a], {fa.element_id: fa}, {a.id: PNG}, reader, s, share_s=0.0)
    assert reader.reads == 0


def test_no_unavailable_note_on_each_equation_but_one_notice_for_the_document():
    a, fa = equation("p1-e0", "x = 1")
    b, fb = equation("p1-e1", "y = 2")
    reader = FakeReader("timeout", "refused")
    summary = rec.reconcile(
        [a, b],
        {fa.element_id: fa, fb.element_id: fb},
        {a.id: PNG, b.id: PNG},
        reader,
        settings(),
    )
    [notice] = summary.notices()
    assert (notice.code, notice.count, notice.page) == ("second_reader_unavailable", 2, None)
    record = summary.record()
    assert (record.considered, record.read, record.unavailable) == (2, 0, 2)


# ------------------------------------------------------------------ what it reads


def test_only_handwritten_display_equations_with_a_crop_and_a_reading_are_read():
    inline, fi = equation("p1-e0", "x = 1")
    inline.display = "inline"
    typed, ft = equation("p1-e1", "y = 2", reasons=())
    nocrop, fn = equation("p1-e2", "z = 3")
    failed, ff = equation("p1-e3", "w = 4")
    ff.status = Status.FAILED
    ff.reason = "invalid_latex"
    ff.equation = None
    badcrop, fb = equation("p1-e4", "u = 5")
    fb.checks.append(
        Check(name="image_page.single_reading", passed=False, code="single_reading_crop_failed")
    )
    good, fg = equation("p1-e5", "v = 6")
    elements = [inline, typed, nocrop, failed, badcrop, good]
    fixes = {f.element_id: f for f in (fi, ft, fn, ff, fb, fg)}
    crops = {e.id: PNG for e in elements if e is not nocrop}
    reader = FakeReader()
    summary = rec.reconcile(elements, fixes, crops, reader, settings())
    assert reader.reads == 1 and summary.considered == 1
    assert all(note(f) is None for f in (fi, ft, fn, ff, fb))


def test_a_rerun_replaces_the_note_and_does_not_stack_them():
    element, fix, _, _ = run_one("x = 1", "y = 2")
    rec.reconcile(
        [element], {fix.element_id: fix}, {element.id: PNG}, FakeReader("x = 1"), settings()
    )
    assert [c.code for c in fix.checks if c.name == rec.NOTE] == [rec.AGREES]


def test_no_reader_chosen_means_nothing_is_built_or_read():
    off = Settings(_env_file=None)  # type: ignore[call-arg]
    assert rec.default_readers(off) == {}
    claude = Settings(  # type: ignore[call-arg]
        _env_file=None,
        math_reader_claude_enabled=True,
        anthropic_api_key="k",
        image_pages_second_reader="claude",
    )
    assert rec.default_readers(claude) == {}  # the child has no key and no network
    with pytest.raises(ValueError, match="MATH_READER_CLAUDE_ENABLED"):
        Settings(_env_file=None, image_pages_second_reader="claude")  # type: ignore[call-arg]


# ------------------------------------------------------------------ through the run

PAGE = {
    "blocks": [
        block("equation", latex="v = \\frac{d}{t}", label="(1)", y0=200, y1=260),
        block("equation", latex="E = mc^{2}", label="(2)", y0=300, y1=360),
    ]
}
CROP_READING = {"is_equation": True, "latex": "v = \\frac{d}{t}", "confidence": 1.0}


def convert(readers=None, **config: Any):
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        feature_image_pages=True,
        image_pages_recover_omissions=False,
        anthropic_api_key="k",
        **config,
    )
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=5.0), max_usd=5.0, max_calls=100)
    fake = FakeVisionClient(
        responders={"image_page": lambda r: PAGE, "equation": lambda r: CROP_READING},
        budget=budget,
    )
    return run.convert_pdf(scan_pdf(), settings=s, client=fake, readers=readers), budget


def test_off_by_default_changes_nothing_in_a_run():
    result, _ = convert()
    result = result.result
    assert all(e.alternatives is None for e in result.elements)
    assert not [c for f in result.fixes for c in f.checks if c.name == rec.NOTE]


def test_a_run_with_a_fake_reader_adds_notes_and_choices_and_asks_about_every_equation():
    reader = FakeReader(r"u = \frac{d}{t}", r"v = \frac{d}{t}")
    result, _ = convert(
        {"local": reader}, math_reader_local_enabled=True, image_pages_second_reader="local"
    )
    result = result.result
    equations = [e for e in result.elements if e.kind is Kind.EQUATION]
    assert len(equations) == 2 and reader.reads == 2
    codes = {e.id: note(result.fix_for(e.id)) for e in equations}  # type: ignore[arg-type]
    assert sorted(c.code for c in codes.values() if c) == [rec.AGREES, rec.DIFFERS]
    with_choices = [e for e in equations if e.alternatives is not None]
    assert len(with_choices) == 1
    assert with_choices[0].alternatives.symbols[0].options == ["u"]  # type: ignore[union-attr]
    for e in equations:  # handwritten maths is always asked, agreed or not
        fix = result.fix_for(e.id)
        assert fix is not None and fix.review == "required" and fix.pending
    summary = result.image_pages.second_reader  # type: ignore[union-attr]
    assert (summary.reader, summary.considered, summary.read) == ("local", 2, 2)
    assert (summary.agreed, summary.differed, summary.with_choices) == (1, 1, 1)
    # and it survives storage
    from readablestem.core.types import DocumentResult

    again = DocumentResult.model_validate(result.model_dump(mode="json"))
    assert [e.alternatives for e in again.elements] == [e.alternatives for e in result.elements]


def test_the_readers_own_sub_cap_stop_is_a_notice_and_never_stops_the_job():
    """`run._budget_scope` stops the whole job on a failed fix whose reason starts `budget_`:
    a reader's own spend limit must only ever be a note on a fix that stands."""
    s = Settings(  # type: ignore[call-arg]
        _env_file=None,
        anthropic_api_key="k",
        math_reader_claude_enabled=True,
        math_reader_max_usd_per_job=0.00001,  # below any one read's worst case
        image_pages_second_reader="claude",
    )
    job = JobBudget(guard=InMemorySpendGuard(cap_usd=5.0), max_usd=5.0, max_calls=100)
    transport = FakeTransport([])  # nothing may be sent
    readers = build_readers(s, job, transport=transport)
    result, _ = convert(
        readers,
        math_reader_claude_enabled=True,
        math_reader_max_usd_per_job=0.00001,
        image_pages_second_reader="claude",
    )
    result = result.result
    assert transport.sent == []
    equations = [e for e in result.elements if e.kind is Kind.EQUATION]
    assert len(equations) == 2
    for e in equations:
        fix = result.fix_for(e.id)
        assert fix is not None
        assert fix.status is not Status.FAILED and run._budget_scope(fix) is None
        assert note(fix) is None
        assert fix.review == "required" and fix.pending
    # one notice for the document says how many equations have only the first reading
    [notice] = [n for n in result.notices if n.code == rec.BUDGET]
    assert (notice.count, notice.page, notice.element_id) == (2, None, None)
    summary = result.image_pages.second_reader  # type: ignore[union-attr]
    assert (summary.reader, summary.considered, summary.over_budget, summary.read) == (
        "claude", 2, 2, 0,
    )  # fmt: skip
    # every other step still ran: the equations were read from their crops, and nothing
    # in the document carries a budget reason
    assert all((f.reason or "").startswith("budget_") is False for f in result.fixes)
    assert all(f.equation is not None for f in result.fixes if f.kind is Kind.EQUATION)


def test_a_job_scope_stop_inside_a_reader_is_a_notice_too():
    class Refused(FakeReader):
        def read(self, request: Any) -> ReadResult:
            self.reads += 1
            raise BudgetExceeded("job_usd", "the job's own cap")

    reader = Refused()
    result, _ = convert(
        {"local": reader}, math_reader_local_enabled=True, image_pages_second_reader="local"
    )
    result = result.result
    assert reader.reads == 1  # not asked again
    for e in (e for e in result.elements if e.kind is Kind.EQUATION):
        fix = result.fix_for(e.id)
        assert fix is not None and run._budget_scope(fix) is None
        assert note(fix) is None
    assert [n.code for n in result.notices if n.code.startswith("second_reader")] == [
        rec.UNAVAILABLE
    ]
