"""Image pages: the second reading of lines that hold maths (`image_page_check`).

No model is asked: `FakeVisionClient` answers the crops request with the payloads below.
"""

from __future__ import annotations

from typing import Any

from image_page_support import block, math, png_of, text
from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.core.types import Kind
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import image_page, image_page_check
from readablestem.pipeline.image_page import ImagePageOutput, PageRead, PassResult

BOX = (0.0, 0.0, 612.0, 792.0)


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def sentence(confidence: str = "medium", y0: int = 140, **kw: Any) -> dict[str, Any]:
    """A sentence with a formula, over a bar of ink on the test picture."""
    fields: dict[str, Any] = {
        "confidence": confidence,
        "x0": 100,
        "y0": y0,
        "x1": 900,
        "y1": y0 + 40,
        "runs": [text("Since "), math("x^2 = 4"), text(" we obtain it.")],
    }
    fields.update(kw)
    return block("paragraph", **fields)


def picture(*rows: int) -> bytes:
    """A page picture (600 x 800) with a bar of ink at each row (thousandths of the height)."""
    return png_of([(70, int(r * 0.8) + 2, 540, int(r * 0.8) + 30) for r in rows])


def reading(index: int, *runs: tuple[str, str], confidence: str = "high") -> dict[str, Any]:
    return {
        "index": index,
        "confidence": confidence,
        "runs": [{"kind": k, "value": v} for k, v in runs],
    }


def second_client(*readings: dict[str, Any], **kw: Any) -> FakeVisionClient:
    return FakeVisionClient(
        responders={
            "image_page": lambda request: (
                {"readings": list(readings)}
                if request.prompt_version == image_page_check.PROMPT_VERSION
                else None
            )
        },
        **kw,
    )


def read_of(*blocks: dict[str, Any], rows: tuple[int, ...] = (140,)) -> PassResult:
    output = ImagePageOutput.model_validate({"blocks": list(blocks)})
    return PassResult(reads={0: PageRead(0, output, [], "ok", picture(*rows))})


AGREE = reading(0, ("text", "Since "), ("math", "x^{2}=4"), ("text", " we obtain it."))


# ------------------------------------------------------------------------------ which lines


# -------------------------------------------------------------------------------- verdicts


def run_second(client: FakeVisionClient, *blocks: dict[str, Any]) -> PassResult:
    result = read_of(*blocks)
    image_page.second_readings(client, settings(), result, concurrency=1)
    return result


def test_a_different_symbol_a_different_word_or_a_different_count_is_not_agreement():
    other = reading(0, ("text", "Since "), ("math", "X^{2}=4"), ("text", " we obtain it."))
    words = reading(0, ("text", "Because "), ("math", "x^2=4"), ("text", " we obtain it."))
    two = reading(
        0, ("text", "Since "), ("math", "x^2=4"), ("math", "y"), ("text", " we obtain it.")
    )
    for second in (other, words, two):
        result = run_second(second_client(second), sentence())
        assert not result.reads[0].verdicts[0].agree


def test_a_budget_refusal_stops_the_second_reading_and_keeps_the_pages():
    guard = InMemorySpendGuard(cap_usd=0.0001)
    budget = JobBudget(guard=guard, max_usd=0.0001, max_calls=10)
    result = run_second(second_client(AGREE, budget=budget), sentence())
    assert result.stopped is not None and result.reads[0].verdicts == {}
    assert result.reads[0].output is not None


# ------------------------------------------------------------------------- what build does


def built(verdict: image_page_check.Verdict | None, accept: bool, confidence: str = "medium"):
    output = ImagePageOutput.model_validate({"blocks": [sentence(confidence)]})
    verdicts = {0: verdict} if verdict else None
    page = image_page.build(0, output, box=BOX, png=None, verdicts=verdicts, accept_agreed=accept)
    assert page is not None
    [paragraph] = [e for e in page.elements if e.kind is Kind.PARAGRAPH]
    return page.fixes[paragraph.id]


AGREED = image_page_check.Verdict(agree=True, confidence="high")


def test_nothing_else_is_accepted():
    unsure = image_page_check.Verdict(agree=True, confidence="medium")
    differs = image_page_check.Verdict(agree=False, confidence="high")
    assert built(unsure, accept=True).review == "required"
    differs_fix = built(differs, accept=True)
    assert differs_fix.review == "required"
    assert "second_reading_differs" in {c.code for c in differs_fix.failed_checks}
    assert built(AGREED, accept=True, confidence="low").review == "required"
    assert built(None, accept=True).review == "required"


def test_a_block_with_an_illegible_marker_is_never_accepted():
    output = ImagePageOutput.model_validate(
        {"blocks": [sentence(runs=[text("Since [illegible] "), math("x^2 = 4")])]}
    )
    page = image_page.build(0, output, box=BOX, png=None, verdicts={0: AGREED}, accept_agreed=True)
    assert page is not None
    [paragraph] = [e for e in page.elements if e.kind is Kind.PARAGRAPH]
    assert page.fixes[paragraph.id].review == "required"
