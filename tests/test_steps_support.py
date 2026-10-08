"""Shared helpers for the equation and figure step tests (no tests in this module).

`models.router.call_with_fallback` belongs to WP1 and is still a stub on this branch, so
the step tests swap in `fallback_router`: a minimal implementation of the same contract
(try the request's model; on a retryable outcome try `settings.fallback_for[model]` once;
BudgetExceeded propagates because the client raises it).
"""

from __future__ import annotations

import dataclasses

import pytest

from a11y_stem.config import Settings
from a11y_stem.core.types import Element, Kind, ModelCall
from a11y_stem.models import router
from a11y_stem.models.base import T, VisionClient, VisionRequest


def make_settings(**kw) -> Settings:
    """Settings that ignore any local .env (thresholds: accept 0.85, abstain 0.4)."""
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def fallback_router(
    client: VisionClient, request: VisionRequest, schema: type[T], *, settings: Settings
) -> tuple[T | None, list[ModelCall]]:
    parsed, call = client.run(request, schema)
    calls = [call]
    if parsed is None and call.outcome in router.RETRYABLE:
        fallback = dataclasses.replace(request, model=settings.fallback_for[request.model])
        parsed, call = client.run(fallback, schema, is_fallback=True)
        call.attempt = 2
        calls.append(call)
    return parsed, calls


def use_fallback_router(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(router, "call_with_fallback", fallback_router)


def equation_element(
    *, display: str | None = "block", text: str | None = None, element_id: str = "p1-e3"
) -> Element:
    return Element(
        id=element_id,
        kind=Kind.EQUATION,
        page=0,
        bbox=(72.0, 100.0, 300.0, 130.0),
        order=3,
        text=text,
        display=display,  # type: ignore[arg-type]
    )


def figure_element(
    *,
    bbox: tuple[float, float, float, float] = (72.0, 200.0, 400.0, 420.0),
    caption: str | None = None,
    element_id: str = "p1-e5",
) -> Element:
    return Element(id=element_id, kind=Kind.FIGURE, page=0, bbox=bbox, order=5, caption=caption)
