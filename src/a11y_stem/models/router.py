"""Model fallback: when one model fails, try the other one once.

The two models cover each other (`Settings.fallback_for`), so an outage, a refusal or a
malformed answer on one model does not cost the user a fix. Exactly one fallback attempt:
resending after transient errors is the client's job (`settings.model_max_retries`, each
resend reserved and charged on its own), and a chain of fallbacks would multiply spend on
inputs that no model can handle.

Every attempt is returned as a `ModelCall`, numbered in order (`attempt` 1, then 2 for the
fallback, which also has `is_fallback=True`), so cost and outcome reporting sees the full
picture. One `ModelCall` can cover several sends to the same model (the client's retries);
its cost is what all of them were charged.

Budget: `BudgetExceeded` from the first reservation propagates unchanged (nothing was
sent). If the first call went out and the fallback's reservation is refused, the router
raises `FallbackBudgetExceeded` instead. It is a `BudgetExceeded` with the same `scope`,
and it carries the calls already made (and paid for) in `.calls`. Callers catching
`BudgetExceeded` can recover them with `calls_made(exc)` and keep the cost on the `Fix`.
"""

from __future__ import annotations

import dataclasses
import logging

from a11y_stem.config import Settings
from a11y_stem.core.budget import BudgetExceeded
from a11y_stem.core.types import ModelCall
from a11y_stem.models.base import T, VisionClient, VisionRequest

log = logging.getLogger(__name__)

RETRYABLE = {"api_error", "timeout", "invalid_output", "refused"}


class FallbackBudgetExceeded(BudgetExceeded):
    """The fallback call's reservation was refused after earlier calls were made.

    `calls` holds those earlier calls (with their real cost) so they are not lost.
    """

    def __init__(self, cause: BudgetExceeded, calls: list[ModelCall]) -> None:
        super().__init__(cause.scope, str(cause))
        self.calls = list(calls)


def calls_made(exc: BudgetExceeded) -> list[ModelCall]:
    """Calls already made when `exc` was raised: empty unless the fallback was refused."""
    return list(exc.calls) if isinstance(exc, FallbackBudgetExceeded) else []


def call_with_fallback(
    client: VisionClient, request: VisionRequest, schema: type[T], *, settings: Settings
) -> tuple[T | None, list[ModelCall]]:
    """Try request.model; on a RETRYABLE outcome try settings.fallback_for[model] once.

    Returns the parsed result (or None) and every attempt's ModelCall. BudgetExceeded from
    the first reservation propagates unchanged; if the fallback reservation is refused, a
    `FallbackBudgetExceeded` (a BudgetExceeded) carrying the first call is raised instead.
    A model with no configured fallback gets one attempt.
    """
    parsed, first = client.run(request, schema)
    calls = [_numbered(first, 1)]
    fallback_model = settings.fallback_for.get(request.model)
    if first.outcome not in RETRYABLE or fallback_model is None:
        return parsed, calls

    log.info(
        "model fallback: step=%s from=%s to=%s outcome=%s",
        request.step,
        request.model,
        fallback_model,
        first.outcome,
    )
    fallback_request = dataclasses.replace(request, model=fallback_model)
    try:
        parsed, second = client.run(fallback_request, schema, is_fallback=True)
    except BudgetExceeded as exc:
        raise FallbackBudgetExceeded(exc, calls) from exc
    calls.append(_numbered(second, 2))
    return parsed, calls


def _numbered(call: ModelCall, attempt: int) -> ModelCall:
    return call if call.attempt == attempt else call.model_copy(update={"attempt": attempt})
