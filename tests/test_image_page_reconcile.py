"""A second reader's reading of handwritten display equations, against the page's
(`pipeline.image_page_reconcile`, docs/math-readers.md "Using a reader in the pipeline").

Offline: readers are fakes, or a real `ClaudeReader` over a transport that never sends.
"""

from __future__ import annotations

from typing import Any

from image_page_support import block, scan_pdf
from readablestem.config import ConfigError, Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
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


def note(fix: Fix) -> Check | None:
    found = [c for c in fix.checks if c.name == rec.NOTE]
    assert len(found) <= 1
    return found[0] if found else None


# ------------------------------------------------------------------ the three outcomes


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


# ------------------------------------------------------------------ what it reads


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
