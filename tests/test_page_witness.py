"""The page witness call (`pipeline/page_witness.py`): anchors, dense pages, a refusal, the
budget, and the request it sends, with a stand-in client. Offline: no model is called."""

from __future__ import annotations

import pytest

from readablestem.config import Settings
from readablestem.core.budget import BudgetExceeded, InMemorySpendGuard, JobBudget
from readablestem.core.types import ConversionOptions
from readablestem.models.base import VisionRequest
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import page_witness
from readablestem.pipeline.page_math import PageRow
from readablestem.pipeline.page_witness import PageWitnessOutput


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def row(row_id: str, text: str) -> PageRow:
    return PageRow(row_id, text, tuple(range(len(text))))


def formula(
    anchors: list[tuple[str, int, str]],
    *,
    display: str = "inline",
    where: str = "text",
    latex: str = "x",
) -> dict:
    return {
        "anchors": [{"line": a, "start": s, "quote": q} for a, s, q in anchors],
        "display": display,
        "where": where,
        "latex": latex,
    }


def client_with(*formulas: dict, **kw) -> FakeVisionClient:
    return FakeVisionClient(defaults={"page_witness": {"formulas": list(formulas)}}, **kw)


ROWS = [
    row("L0", "The cell M0 = 47.8 g and the speed v = 5 m/s."),
    row("L1", "a = (v - v0) / t"),
    row("L2", "Figure 1: the force F net on a block"),
]


# ------------------------------------------------------------------------ settings, request


def test_the_pass_is_off_and_opus_with_the_dense_page_limit_by_default():
    s = settings()
    assert s.feature_page_witness is False
    assert s.page_witness_model == "claude-opus-5-5"
    assert s.page_witness_max_output_tokens == 12288
    assert ConversionOptions.from_settings(s).page_witness is False
    assert ConversionOptions.from_settings(settings(feature_page_witness=True)).page_witness


def test_the_request_carries_the_image_and_the_rows_fenced_as_context_not_instructions():
    client = client_with()
    page_witness.run_page(client, settings(), ROWS, b"png")
    (request,) = client.requests
    assert request.step == "page_witness"
    assert request.model == "claude-opus-5-5"
    assert request.images == [b"png"]
    assert request.max_output_tokens == 12288
    assert request.prompt_version == page_witness.PROMPT_VERSION
    assert (request.context_text or "").startswith("L0 | The cell M0 = 47.8 g")
    assert "The cell M0" not in request.instructions  # document text is never in the instructions
    assert "table cells" in request.instructions and "figure labels" in request.instructions


def test_the_model_and_limit_come_from_settings():
    client = client_with()
    s = settings(page_witness_model="claude-sonnet-5-5", page_witness_max_output_tokens=8000)
    page_witness.run_page(client, s, ROWS, b"png")
    assert client.requests[0].model == "claude-sonnet-5-5"
    assert client.requests[0].max_output_tokens == 8000


# -------------------------------------------------------------------------------- anchors


def test_formulas_in_table_cells_captions_and_labels_are_kept_with_where_they_sit():
    client = client_with(
        formula([("L0", 9, "M0 = 47.8 g")], where="table_cell", latex=r"M_0 = 47.8\ \text{g}"),
        formula([("L0", 36, "v = 5 m/s")], latex=r"v = 5\ \text{m/s}"),
        formula([("L2", 22, "F net")], where="caption", latex=r"F_{\text{net}}"),
    )
    page = page_witness.run_page(client, settings(), ROWS, b"png")
    assert page.failed is None
    assert [(f.where, f.span.segments[0].row) for f in page.formulas] == [
        ("table_cell", "L0"),
        ("text", "L0"),
        ("caption", "L2"),
    ]
    assert page.stats.proposed == 3 and page.stats.verified == 3 and page.stats.dropped == 0


def test_a_quote_found_elsewhere_in_its_row_is_moved_and_a_quote_that_holds_nowhere_is_dropped():
    client = client_with(
        formula([("L0", 0, "v = 5 m/s")], latex="v=5"),  # wrong offset: found at 35
        formula([("L0", 0, "v = 6 m/s")], latex="v=6"),  # not in the row
        formula([("L9", 0, "x")], latex="x"),  # not a row
    )
    page = page_witness.run_page(client, settings(), ROWS, b"png")
    assert [f.span.segments[0].start for f in page.formulas] == [35]
    assert (page.stats.relocated, page.stats.dropped) == (1, 2)


def test_a_formula_over_several_rows_has_an_anchor_per_row_and_an_overlap_is_dropped_whole():
    client = client_with(
        formula([("L1", 0, "a = (v - v0) / t")], display="block", latex=r"a=\frac{v-v_0}{t}"),
        formula([("L1", 4, "(v - v0)")], latex="v-v_0"),  # inside the first
        formula([("L0", 9, "M0"), ("L2", 0, "Figure")], display="block", latex="M_0 F"),
    )
    page = page_witness.run_page(client, settings(), ROWS, b"png")
    assert [f.span.display for f in page.formulas] == ["block", "block"]
    assert [len(f.span.segments) for f in page.formulas] == [2, 1]
    assert page.stats.overlapped == 1 and page.stats.dropped == 0


def test_the_latex_is_stored_as_the_other_readings_are():
    client = client_with(formula([("L1", 0, "a = (v - v0) / t")], latex=r"\ce{H2O}"))
    page = page_witness.run_page(client, settings(), ROWS, b"png")
    assert "ce" not in page.formulas[0].latex  # `\ce{...}` is written out


# ---------------------------------------------------------------------- a dense page


def dense(n: int) -> list[PageRow]:
    return [row(f"L{i}", f"{i}: " + "y = m x + b, " * 6) for i in range(n)]


def test_a_page_that_fits_is_one_call_and_a_dense_one_is_read_in_windows_with_the_same_image():
    rows = dense(160)  # about 12,000 characters: two windows of the fence
    windows, left_out = page_witness.windows(rows)
    assert 1 < len(windows) <= page_witness.MAX_WINDOWS and left_out == 0
    assert [r.id for w in windows for r in w] == [r.id for r in rows]  # nothing lost or repeated
    for w in windows:
        _text, ids = page_witness.page_math.context_text(w)
        assert len(ids) == len(w)  # every row of a window fits the fence
    client = client_with()
    page = page_witness.run_page(client, settings(), rows, b"png")
    assert len(client.requests) == len(windows) == page.stats.windows
    assert {r.images[0] for r in client.requests} == {b"png"}
    assert not any("L0 |" in (r.context_text or "") for r in client.requests[1:])


def test_rows_past_the_last_window_are_counted_not_offered():
    rows = dense(600)
    windows, left_out = page_witness.windows(rows)
    assert len(windows) == page_witness.MAX_WINDOWS
    assert left_out == len(rows) - sum(len(w) for w in windows) > 0
    page = page_witness.run_page(client_with(), settings(), rows, b"png")
    assert page.stats.unoffered_rows == left_out


def test_a_formula_in_a_row_no_window_offered_is_dropped():
    rows = dense(600)
    _, left_out = page_witness.windows(rows)
    last = rows[-1]
    client = client_with(formula([(last.id, 3, "y = m x + b")]))
    page = page_witness.run_page(client, settings(), rows, b"png")
    assert left_out > 0 and page.formulas == []
    assert page.stats.dropped == page.stats.proposed  # every window got the answer


def test_an_answer_that_ran_out_is_asked_again_as_two_halves_once():
    rows = dense(20)
    asked: list[list[str]] = []

    def respond(request: VisionRequest):
        ids = [line.split(" | ")[0] for line in (request.context_text or "").splitlines()]
        asked.append(ids)
        if len(ids) == 20:
            return {"formulas": "not valid"}  # the whole page: the answer ran into the limit
        return {"formulas": [formula([(ids[0], 3, "y = m x + b")], latex="y=mx+b")]}

    client = FakeVisionClient(responders={"page_witness": respond})
    page = page_witness.run_page(client, settings(), rows, b"png")
    assert [len(a) for a in asked] == [20, 10, 10]
    assert page.stats.splits == 1 and page.failed is None
    assert [f.span.segments[0].row for f in page.formulas] == ["L0", "L10"]
    # a half that fails again is not split further
    client = FakeVisionClient(responders={"page_witness": lambda r: {"formulas": "bad"}})
    page = page_witness.run_page(client, settings(), rows, b"png")
    assert len(client.requests) == 3 and page.failed == "invalid_output"
    assert page.formulas == []


# ------------------------------------------------------------------------------- refusal


def test_a_refusal_is_recorded_not_retried_and_leaves_the_page_without_formulas():
    client = FakeVisionClient(defaults={"page_witness": None})
    page = page_witness.run_page(client, settings(), ROWS, b"png")
    assert page.failed == "refused" and page.formulas == []
    assert page.stats.refused == 1 and len(client.requests) == 1


def test_a_failed_model_call_is_a_failed_page_with_its_outcome():
    client = FakeVisionClient(fail_models={"claude-opus-5-5": "api_error"})
    page = page_witness.run_page(client, settings(), ROWS, b"png")
    assert page.failed == "api_error"


# -------------------------------------------------------------------------------- budget


def test_the_call_is_reserved_against_the_job_budget_at_the_models_worst_case():
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=10.0), max_usd=10.0, max_calls=10)
    client = client_with(budget=budget)
    page_witness.run_page(client, settings(), ROWS, b"png")
    assert budget.calls == 1 and budget.spent_usd > 0


def test_a_cap_too_small_for_one_call_stops_the_page_and_the_pass_keeping_earlier_pages():
    pages = {n: ([row("L0", f"page {n}: x = {n} + 1")], lambda: b"png") for n in range(3)}

    class StopsOnSecond(FakeVisionClient):
        def run(self, request, schema, *, is_fallback=False):
            if "page 1" in (request.context_text or ""):
                raise BudgetExceeded("job_usd", "cap")
            return super().run(request, schema, is_fallback=is_fallback)

    client = StopsOnSecond(defaults={"page_witness": {"formulas": []}})
    result = page_witness.run(client, settings(), pages, concurrency=1)
    assert result.stopped is not None and result.stopped.scope == "job_usd"
    assert result.plan.pages[0].failed is None
    assert result.plan.pages[1].failed == "budget_exceeded"

    tight = JobBudget(guard=InMemorySpendGuard(cap_usd=0.001), max_usd=0.001, max_calls=10)
    page = page_witness.run_page(client_with(budget=tight), settings(), ROWS, b"png")
    assert page.stopped is not None and page.failed == "budget_exceeded"


def test_a_window_answered_before_the_cap_keeps_its_formulas():
    rows = dense(160)

    class StopsOnSecond(FakeVisionClient):
        def run(self, request, schema, *, is_fallback=False):
            if "L0 |" not in (request.context_text or ""):
                raise BudgetExceeded("job_usd", "cap")
            return super().run(request, schema, is_fallback=is_fallback)

    ok = {"formulas": [formula([("L0", 3, "y = m x + b")], latex="y=mx+b")]}
    page = page_witness.run_page(
        StopsOnSecond(defaults={"page_witness": ok}), settings(), rows, b"png"
    )
    assert page.failed is None and page.stopped is not None and len(page.formulas) == 1


# ---------------------------------------------------------------------- the pass, pages


def test_a_page_with_too_little_text_or_no_image_is_skipped_and_the_rest_are_asked():
    client = client_with()
    pages = {
        0: ([row("L0", "x")], lambda: b"png"),
        1: ([row("L0", "x = 5 and more")], lambda: b"png"),
        2: ([row("L0", "y = 6 and more")], lambda: (_ for _ in ()).throw(ValueError("render"))),
        3: ([row("L0", "z = 7 and more")], lambda: None),
    }
    result = page_witness.run(client, settings(), pages)
    assert sorted(result.plan.pages) == [1] and len(client.requests) == 1
    assert len(result.calls) == 1


def test_only_pages_with_maths_are_maths_pages():
    class Char:
        def __init__(self, math: bool):
            self.math = math

    class Obj:
        def __init__(self, *flags: bool):
            self.chars = [Char(f) for f in flags]

    prose = {"L0": Obj(False, False, False, False)}
    glyphs = {"L0": Obj(True, True), "L1": Obj(True, False)}
    assert not page_witness.is_maths_page(prose)
    assert page_witness.is_maths_page(glyphs)
    assert page_witness.is_maths_page(prose, equations=1)


def test_the_output_schema_refuses_extra_keys_and_unknown_places():
    with pytest.raises(ValueError):
        PageWitnessOutput.model_validate({"formulas": [], "extra": 1})
    bad = formula([("L0", 0, "x")], where="margin")
    with pytest.raises(ValueError):
        PageWitnessOutput.model_validate({"formulas": [bad]})
