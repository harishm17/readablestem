"""make_vision_client: offline defaults, real client without a key, light package import."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from readablestem.config import MAIN_MODEL, Settings
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.models import (
    VisionRequest,
    make_vision_client,
)

SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]  (no API key)


class EquationOut(BaseModel):
    is_equation: bool
    latex: str
    confidence: float


class FigureOut(BaseModel):
    figure_type: Literal["chart", "diagram", "photo", "illustration", "table_image", "other"]
    decorative: bool
    alt: str
    confidence: float


def budget() -> JobBudget:
    return JobBudget(guard=InMemorySpendGuard(cap_usd=1), max_usd=1, max_calls=10)


def request(step: Literal["equation", "figure"]) -> VisionRequest:
    return VisionRequest(
        step=step,
        model=MAIN_MODEL,
        prompt_version=f"{step}-test",
        instructions="i",
        images=[b"\x89PNG\r\n\x1a\nimg"],
    )


def test_offline_tables_and_long_figures_get_a_placeholder_that_asks_for_a_decision():
    from readablestem.core.types import ConversionOptions, Element, Kind, Status
    from readablestem.pipeline import figure_step, options, table_step

    client = make_vision_client(SETTINGS, budget(), fake=True)
    table = Element(id="p1-e1", kind=Kind.TABLE, page=0, bbox=(0, 0, 100, 50), order=0,
                    text="Time 1 2")  # fmt: skip
    with options.using(ConversionOptions(tables=True, long_descriptions=True)):
        fix = table_step.rebuild(table, b"png", client=client, settings=SETTINGS)
        assert fix.status is Status.NEEDS_REVIEW and fix.table is not None
        assert fix.table.grid.cells[0].text == "Table (offline placeholder)"
        figure = Element(id="p1-e2", kind=Kind.FIGURE, page=0, bbox=(0, 0, 200, 200), order=1)
        described = figure_step.describe(figure, b"png", client=client, settings=SETTINGS)
    assert described.status is Status.NEEDS_REVIEW and described.figure is not None
    assert described.figure.alt == "Figure (offline placeholder)"
