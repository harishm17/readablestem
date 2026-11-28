"""The model's page order (`pipeline/page_order.py`, off by default): what becomes of a reply, and
a conversion with the switch on."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from readablestem.config import Settings
from readablestem.core.types import ConversionOptions
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline.page_order import PageOrderOutput, verify
from readablestem.pipeline.run import convert_pdf

ITEMS = [
    {"id": "r0", "kind": "line", "text": "Chapter 2 Motion 41"},
    {"id": "r1", "kind": "line", "text": "A ball dropped from rest falls " * 10},
    {"id": "r2", "kind": "line", "text": "with the acceleration of gravity." * 10},
    {"id": "e0", "kind": "equation", "text": "y = g t^2 / 2"},
]
TWO_COLUMNS = Path(__file__).parent / "fixtures" / "layout" / "two_column.pdf"


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        pytest.param(
            {
                "blocks": [
                    {"kind": "paragraph", "items": [2, 1]},
                    {"kind": "equation", "items": [3]},
                ],
                "furniture": [0],
            },
            {
                "blocks": [
                    {"kind": "paragraph", "items": ["r2", "r1"]},
                    {"kind": "equation", "items": ["e0"]},
                ],
                "furniture": ["r0"],
            },
            id="numbers become the page's own item ids",
        ),
        pytest.param(
            {"blocks": [{"kind": "paragraph", "items": [1]}], "furniture": []},
            {"blocks": [{"kind": "paragraph", "items": ["r1"]}], "furniture": []},
            id="items it does not name are left to the rules",
        ),
        pytest.param(
            {"blocks": [{"kind": "paragraph", "items": [0, 9]}], "furniture": []},
            "unknown_number",
            id="a number that is no item",
        ),
        pytest.param(
            {"blocks": [{"kind": "paragraph", "items": [-1]}], "furniture": []},
            "unknown_number",
            id="a negative number",
        ),
        pytest.param(
            {"blocks": [{"kind": "paragraph", "items": [0]}], "furniture": [1, 2]},
            "too_much_furniture",
            id="paragraphs called furniture",
        ),
        pytest.param(
            {"blocks": [{"kind": "paragraph", "items": [1, 2]}], "furniture": [2]},
            "repeated_number",
            id="a number named twice",
        ),
        pytest.param({"blocks": [], "furniture": []}, "no_numbers", id="none named"),
        pytest.param(
            {"blocks": [{"kind": "paragraph", "items": ["1"]}], "furniture": []},
            "invalid",
            id="a number sent as text",
        ),
        pytest.param(
            {"blocks": [{"kind": "quote", "items": [1]}], "furniture": []},
            "invalid",
            id="a kind that is not one",
        ),
        pytest.param(
            {"blocks": [{"kind": "paragraph", "items": [1]}], "furniture": [], "notes": "x"},
            "invalid",
            id="a key that is not in the schema",
        ),
    ],
)
def test_reply_becomes_an_answer_or_no_answer(reply: dict, expected: dict | str) -> None:
    if expected == "invalid":
        with pytest.raises(ValidationError):
            PageOrderOutput.model_validate(reply)
        return
    answer, reason = verify(PageOrderOutput.model_validate(reply), ITEMS)
    if isinstance(expected, str):
        assert (answer, reason) == (None, expected)
    else:
        assert (answer, reason) == (expected, None)


def _convert(*, on: bool) -> tuple[object, list[str]]:
    asked: list[str] = []

    def reply(request: object) -> dict:
        text = request.context_text  # type: ignore[attr-defined]
        asked.append(text)
        count = len(text.split("\n"))
        # The first item (the heading line) is furniture; every other line is its own block.
        return {
            "blocks": [{"kind": "paragraph", "items": [n]} for n in range(1, count)],
            "furniture": [0],
        }

    settings = Settings(_env_file=None, env="test", feature_layout_model_order=on)  # type: ignore[call-arg]
    output = convert_pdf(
        TWO_COLUMNS.read_bytes(),
        settings=settings,
        client=FakeVisionClient(responders={"page_order": reply}),
        options=ConversionOptions.from_settings(settings),
        concurrency=1,
    )
    return output.result, asked


def test_switch_on_changes_the_page_and_off_asks_nothing() -> None:
    off, asked_off = _convert(on=False)
    on, asked_on = _convert(on=True)

    assert asked_off == [] and off.page_order is None  # type: ignore[attr-defined]
    assert len(asked_on) == 1  # one request for the one page

    texts_off = [e.text or "" for e in off.elements]  # type: ignore[attr-defined]
    texts_on = [e.text or "" for e in on.elements]  # type: ignore[attr-defined]
    assert any(t.startswith("2.4 Free Fall") for t in texts_off)
    assert not any(t.startswith("2.4 Free Fall") for t in texts_on)  # the model left it out
    assert texts_on != texts_off

    summary = on.page_order  # type: ignore[attr-defined]
    assert (summary.pages, summary.applied_pages, summary.failed) == (1, 1, 0)
    assert summary.furniture == 1
    assert summary.calls == 1


def test_a_model_block_of_a_question_and_its_options_stays_a_question_and_its_options() -> None:
    """The model may hold a question and its options in one block; the page keeps each printed
    option as its own item, as it does with the switch off."""
    import pymupdf

    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((72, 100), "1. Which gas do plants take in?", fontsize=11)
    for k, option in enumerate(["a. Oxygen", "b. Carbon dioxide", "c. Nitrogen", "d. Helium"]):
        page.insert_text((90, 120 + 15 * k), option, fontsize=11)
    page.insert_text((72, 200), "2. Name the organelle that makes ATP.", fontsize=11)
    pdf = document.tobytes()

    def reply(request: object) -> dict:
        count = len(request.context_text.split("\n"))  # type: ignore[attr-defined]
        return {"blocks": [{"kind": "paragraph", "items": list(range(count))}], "furniture": []}

    settings = Settings(_env_file=None, env="test", feature_layout_model_order=True)  # type: ignore[call-arg]
    output = convert_pdf(
        pdf,
        settings=settings,
        client=FakeVisionClient(responders={"page_order": reply}),
        options=ConversionOptions.from_settings(settings),
        concurrency=1,
    )
    texts = [e.text or "" for e in output.result.elements]
    assert not any("Which gas" in t and "Oxygen" in t for t in texts)
    assert any(t.strip().endswith("Carbon dioxide") for t in texts)
