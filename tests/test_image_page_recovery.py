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
from readablestem.core.types import IMAGE_PAGE_OMISSION_REASON, Kind
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import image_page
from readablestem.pipeline.image_page import ImagePageOutput, PageRead, PassResult
from test_image_page_omission import BOX, LINES, W, H, blocks_for, page, png_bytes


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


def test_the_left_out_lines_are_read_from_their_own_picture_and_joined_in_reading_order():
    result = one_line_read()
    client = FakeVisionClient(responders={"image_page": lambda r: crop_equations(4)})
    image_page.recover_omissions(client, settings(), result, concurrency=1)
    read = result.reads[0]
    assert len(client.requests) == 1 and len(read.calls) == 1
    assert read.recovered == 4
    assert read.output is not None and len(read.output.blocks) == 5
    ys = [b.y0 for b in read.output.blocks]
    assert ys == sorted(ys)  # the original first line, then the recovered ones below it
    # the recovered boxes are on the page, not in the picture of the part
    assert all(b.y0 >= read.output.blocks[0].y1 - 20 for b in read.output.blocks[1:])
    assert all(0 <= b.x0 < b.x1 <= 1000 and 0 <= b.y0 < b.y1 <= 1000 for b in read.output.blocks)
    built = image_page.build(0, read.output, box=BOX, png=read.png)
    assert built is not None
    assert not [e for e in built.elements if IMAGE_PAGE_OMISSION_REASON in e.reasons]
    assert [e.kind for e in built.elements if e.display == "block"] == [Kind.EQUATION] * 5


def test_a_page_read_completely_costs_no_request():
    output = blocks_for(*LINES)
    result = PassResult(reads={0: PageRead(0, output, [], "ok", png_bytes(page(LINES)))})
    client = FakeVisionClient(responders={"image_page": lambda r: crop_equations(2)})
    image_page.recover_omissions(client, settings(), result, concurrency=1)
    assert client.requests == [] and result.reads[0].recovered == 0


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


def test_a_page_without_a_picture_or_with_an_unplaced_block_is_left_alone():
    output = ImagePageOutput.model_validate(
        {"blocks": [block("paragraph", x0=0, y0=0, x1=0, y1=0, runs=[])]}
    )
    client = FakeVisionClient(responders={"image_page": lambda r: crop_equations(2)})
    result = PassResult(
        reads={
            0: PageRead(0, blocks_for(LINES[0]), [], "ok", None),
            1: PageRead(1, output, [], "ok", png_bytes(page(LINES))),
        }
    )
    image_page.recover_omissions(client, settings(), result, concurrency=1)
    assert client.requests == [] and W and H


def test_the_picture_sent_is_the_part_of_the_page_with_a_margin():
    result = one_line_read()
    client = FakeVisionClient(responders={"image_page": lambda r: crop_equations(2)})
    image_page.recover_omissions(client, settings(), result, concurrency=1)
    from io import BytesIO

    from PIL import Image

    sent = Image.open(BytesIO(client.requests[0].images[0]))
    assert sent.height < 0.9 * H and sent.width < W  # a part, not the page
    assert client.requests[0].prompt_version == image_page.PROMPT_VERSION
