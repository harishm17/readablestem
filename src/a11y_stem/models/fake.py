"""Deterministic offline `VisionClient` for tests, CI and local runs without a key.

Responses are chosen by, in order: an exact entry for (step, sha256 of the first image),
then a per-step responder function, then a per-step default. Every call is recorded (with
the request) so tests can assert what would have been sent. Costs are computed from fake
token counts so budget code is exercised.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import ValidationError

from a11y_stem.core.budget import JobBudget
from a11y_stem.core.types import ModelCall, Outcome
from a11y_stem.models.base import T, VisionRequest
from a11y_stem.models.pricing import cost_usd, estimate_max_cost_usd
from a11y_stem.models.verifier import Verifier


def image_key(png: bytes) -> str:
    return hashlib.sha256(png).hexdigest()


Responder = Callable[[VisionRequest], dict | None]


@dataclass
class FakeVisionClient:
    by_image: dict[tuple[str, str], dict | None] = field(default_factory=dict)
    responders: dict[str, Responder] = field(default_factory=dict)
    defaults: dict[str, dict | None] = field(default_factory=dict)
    # Force an outcome for a model (e.g. {"gpt-6.1-sol": "api_error"}) to test fallback.
    fail_models: dict[str, Outcome] = field(default_factory=dict)
    budget: JobBudget | None = None
    input_tokens: int = 800
    output_tokens: int = 60
    requests: list[VisionRequest] = field(default_factory=list)
    # The job's second automated check, as on the real client (`verifier.verifier_of`).
    verifier: Verifier | None = None

    def run(
        self, request: VisionRequest, schema: type[T], *, is_fallback: bool = False
    ) -> tuple[T | None, ModelCall]:
        reservation = None
        if self.budget is not None:
            estimate = estimate_max_cost_usd(
                request.model,
                n_images=len(request.images),
                text_chars=len(request.context_text or ""),
                max_output_tokens=request.max_output_tokens,
            )
            reservation = self.budget.reserve(estimate)  # may raise BudgetExceeded
        self.requests.append(request)
        call = ModelCall(
            step=request.step,
            model=request.model,
            prompt_version=request.prompt_version,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cost_usd=cost_usd(request.model, self.input_tokens, 0, self.output_tokens),
            latency_ms=1,
            is_fallback=is_fallback,
        )
        if self.budget is not None and reservation is not None:
            self.budget.settle(reservation, call.cost_usd)
        if request.model in self.fail_models:
            call.outcome = self.fail_models[request.model]
            return None, call
        payload = self._payload(request)
        if payload is None:
            call.outcome = "refused"
            return None, call
        try:
            return schema.model_validate(payload), call
        except ValidationError:
            call.outcome = "invalid_output"
            return None, call

    def _payload(self, request: VisionRequest) -> dict | None:
        if request.images:
            key = (request.step, image_key(request.images[0]))
            if key in self.by_image:
                return self.by_image[key]
        if request.step in self.responders:
            return self.responders[request.step](request)
        return self.defaults.get(request.step)
