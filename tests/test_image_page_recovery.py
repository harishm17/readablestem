"""Image pages: writing the reader left out is read from a picture of just that part.

A reader that stops early leaves a stretch of the page unread; the omission check finds it
(`image_page_omission`). The recovery cuts that stretch out and reads it with the same prompt,
so the writing is converted, with its own confidence and decision, instead of only flagged.
"""

from __future__ import annotations

from typing import Any

from image_page_support import block
from readablestem.config import Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import image_page
from readablestem.pipeline.image_page import PageRead, PassResult
from test_image_page_omission import BOX, LINES, blocks_for, page, png_bytes


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def crop_equations(count: int) -> dict[str, Any]:
    """What a reader says about a picture of `count` lines of display equations."""
    return {
        "blocks": [
            block(
                "equation",
                latex=f"y_{i} = {i}",
                x0=100,
                x1=900,
                y0=int(1000 * i / count) + 10,
                y1=int(1000 * (i + 1) / count) - 10,
                confidence="medium",
            )
            for i in range(count)
        ]
    }


def one_line_read() -> PassResult:
    """A page of five lines of writing of which the reader gave only the first."""
    output = blocks_for(LINES[0])
    png = png_bytes(page(LINES))
    return PassResult(reads={0: PageRead(0, output, [], "ok", png)})


def test_a_part_that_gives_nothing_stays_flagged():
    for answer in ({"blocks": []}, None):
        result = one_line_read()
        client = FakeVisionClient(responders={"image_page": lambda r, a=answer: a})
        image_page.recover_omissions(client, settings(), result, concurrency=1)
        read = result.reads[0]
        assert read.recovered == 0 and read.output is not None and len(read.output.blocks) == 1
        built = image_page.build(0, read.output, box=BOX, png=read.png)
        assert built is not None and built.record.omitted == 1


def test_a_budget_refusal_stops_the_recovery_and_keeps_the_page():
    result = one_line_read()
    guard = InMemorySpendGuard(cap_usd=0.0001)
    budget = JobBudget(guard=guard, max_usd=0.0001, max_calls=10)
    client = FakeVisionClient(responders={"image_page": lambda r: crop_equations(3)}, budget=budget)
    image_page.recover_omissions(client, settings(), result, concurrency=1)
    assert result.stopped is not None and result.reads[0].recovered == 0
    assert result.reads[0].output is not None
