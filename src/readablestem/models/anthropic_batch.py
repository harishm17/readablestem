"""Staged runs on the Message Batches API: the same requests, half the price, later.

A pipeline run asks the model one request at a time, and what it asks next depends on the
last answer (an escalation, a fallback, the figure judge). The Batches API takes a list of
requests, so a run is made in stages instead:

1. run the whole pipeline against the answers file (`eval.answers.CachedClient`); every
   request it has no answer for is collected by a `StagingClient` and answered with a
   placeholder failure that the stage's results are thrown away for;
2. send the collected requests as one batch (or a few), wait, record the answers in the
   file;
3. run the pipeline again. It now gets the first answers for real and reaches the next
   ones (the escalation of an unsure answer, the judge of a description). Stop when a run
   collects nothing: its results are the run's results.

Nothing about a request changes: the prompts, images, schema and effort are what `run`
sends (`AnthropicVisionClient.batch_params`), less the refusal fallback, which the Batches
API does not take. Only the price (every token at `BATCH_DISCOUNT`) and the wait differ, and
a batch's prompt-cache hits are best effort, so a staged run's cache numbers say nothing
about the live path.

A request the placeholder answered is never a real fallback: the router sends its second
attempt only after a failure, so the second attempt is dropped while the first is pending.

Money: each request reserves its worst case at the batch price on the run's `JobBudget` and
settles at the batch price of its usage (a request that errored or expired is not billed).
The `ModelCall` recorded with each answer carries the cost at list price, the number a live
call would have paid, so runs of both kinds compare; the budget holds what was billed.
When the cap cannot hold more requests the stage stops and says how many were left unsent.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from readablestem.core.budget import BudgetExceeded, JobBudget, Reservation
from readablestem.core.types import ModelCall
from readablestem.models.anthropic_client import AnthropicVisionClient
from readablestem.models.base import T, VisionRequest
from readablestem.models.pricing import BATCH_DISCOUNT

log = logging.getLogger(__name__)

API_RETRIES = 6
API_RETRY_FIRST_S = 2.0

MAX_BATCH_BYTES = 200_000_000  # the API takes 256 MB per batch; stay clear of it
MAX_BATCH_REQUESTS = 5_000
POLL_S = 15.0
FAILURES_BEFORE_GIVING_UP = 3


def with_retries[R](
    call: Callable[[], R],
    *,
    idempotent: bool = True,
    sleep: Callable[[float], None] = time.sleep,
) -> R:
    """`call()` again after a dropped connection, a timeout, a rate limit or a server error,
    up to `API_RETRIES` times with a doubling wait. A batch is billed whether or not anyone
    is listening, so losing the connection while polling must not lose the results.
    `idempotent` False (creating a batch): a connection that may have carried the request is
    not tried again, because a second batch would be paid for twice; a rate limit or a
    server error is (nothing was created)."""
    import anthropic

    from readablestem.models.anthropic_client import _may_have_reached_provider

    delay = API_RETRY_FIRST_S
    for attempt in range(API_RETRIES + 1):
        try:
            return call()
        except anthropic.APIConnectionError as exc:
            if attempt == API_RETRIES or not (idempotent or not _may_have_reached_provider(exc)):
                raise
        except anthropic.APIStatusError as exc:
            if attempt == API_RETRIES or not (exc.status_code == 429 or exc.status_code >= 500):
                raise
        log.info("batch API call failed, trying again in %.0f s", delay)
        sleep(delay)
        delay = min(delay * 2, 60.0)
    raise AssertionError("unreachable")


@dataclass
class Pending:
    """One request that has no answer yet."""

    key: str
    request: VisionRequest
    schema: type[BaseModel]
    builder: AnthropicVisionClient  # builds the request's parameters (its settings decide)
    record: Callable[[str, str, Any, ModelCall], None]  # where its answer is kept


class PendingPool:
    """The requests a stage collected, once each, in the order first asked."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Pending] = {}

    def add(self, item: Pending) -> None:
        with self._lock:
            self._items.setdefault(item.key, item)

    def take(self) -> list[Pending]:
        with self._lock:
            items = list(self._items.values())
            self._items.clear()
            return items

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class StagingClient:
    """The `inner` of an answers file during a staged run: collects, never sends."""

    def __init__(self, builder: AnthropicVisionClient, pool: PendingPool) -> None:
        self._builder = builder
        self._pool = pool
        self.verifier = None
        self.owner: Any = None  # the `CachedClient` in front of this one (its `record`)
        self.stages = True  # tells `CachedClient` its answers are placeholders

    def run(
        self, request: VisionRequest, schema: type[T], *, is_fallback: bool = False
    ) -> tuple[T | None, ModelCall]:
        from readablestem.eval.answers import answer_key

        placeholder = ModelCall(
            step=request.step,
            model=request.model,
            prompt_version=request.prompt_version,
            is_fallback=is_fallback,
            outcome="api_error",  # not kept by the answers file
        )
        settings = self._builder._settings
        self._pool.add(
            Pending(
                answer_key(settings, request), request, schema, self._builder, self.owner.record
            )
        )
        return None, placeholder


@dataclass
class BatchResult:
    sent: int = 0
    answered: int = 0
    failed: int = 0
    unsent: int = 0
    billed_usd: float = 0.0
    batches: int = 0


class BatchRunner:
    """Sends collected requests as Message Batches and records their answers."""

    def __init__(
        self,
        budget: JobBudget,
        *,
        say: Callable[[str], None] = print,
        poll_s: float = POLL_S,
        timeout_s: float = 6 * 3600,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._budget = budget
        self._say = say
        self._poll_s = poll_s
        self._timeout_s = timeout_s
        self._sleep = sleep
        self._failures: dict[str, int] = {}

    def run(self, pending: list[Pending]) -> BatchResult:
        """Send `pending` and call each request's `record(key, step, parsed, call)` for its
        answer when it is worth keeping. A request the API failed is left for the next
        stage until it has failed `FAILURES_BEFORE_GIVING_UP` times, then its failure is
        recorded as the answer."""
        result = BatchResult()
        queue = list(pending)
        while queue:
            chunk, held, queue = self._fill(queue)
            if not chunk:
                result.unsent = len(queue)
                break
            self._send_chunk(chunk, held, result)
            if queue:
                self._say(f"batch: {len(queue)} more requests wait for the first to settle")
        return result

    def _fill(self, queue: list[Pending]) -> tuple[list[Pending], list[Reservation], list[Pending]]:
        chunk: list[Pending] = []
        held: list[Reservation] = []
        size = 0
        for index, item in enumerate(queue):
            params = item.builder.batch_params(item.request, item.schema)
            bytes_ = len(json.dumps(params))
            if chunk and (size + bytes_ > MAX_BATCH_BYTES or len(chunk) >= MAX_BATCH_REQUESTS):
                return chunk, held, queue[index:]
            try:
                held.append(self._budget.reserve(item.builder.batch_estimate_usd(item.request)))
            except BudgetExceeded:
                return chunk, held, queue[index:]
            chunk.append(item)
            size += bytes_
        return chunk, held, []

    def _send_chunk(
        self,
        chunk: list[Pending],
        held: list[Reservation],
        result: BatchResult,
    ) -> None:
        by_id = {
            f"r{index:05d}": (item, hold)
            for index, (item, hold) in enumerate(zip(chunk, held, strict=True))
        }
        sdk = chunk[0].builder.sdk_client()
        settled: set[str] = set()
        try:
            requests: Any = [
                {"custom_id": cid, "params": item.builder.batch_params(item.request, item.schema)}
                for cid, (item, _) in by_id.items()
            ]
            batch_id = with_retries(
                lambda: sdk.messages.batches.create(requests=requests), idempotent=False
            ).id
            result.batches += 1
            result.sent += len(requests)
            self._say(f"batch {batch_id}: {len(requests)} requests sent")
            self._wait(sdk, batch_id)
            for entry in with_retries(lambda: list(sdk.messages.batches.results(batch_id))):
                found = by_id.get(entry.custom_id)
                if found is None or entry.custom_id in settled:
                    continue
                item, hold = found
                settled.add(entry.custom_id)
                self._settle_entry(item, hold, entry, result)
        finally:
            for cid, (_, hold) in by_id.items():
                if cid not in settled:
                    self._budget.settle(hold, 0.0)  # never answered: nothing was billed

    def _wait(self, sdk: Any, batch_id: str) -> None:
        started = time.monotonic()
        while True:
            batch = with_retries(lambda: sdk.messages.batches.retrieve(batch_id))
            if batch.processing_status == "ended":
                counts = batch.request_counts
                self._say(
                    f"batch {batch_id}: ended, {counts.succeeded} succeeded, "
                    f"{counts.errored} errored, {counts.expired} expired"
                )
                return
            if time.monotonic() - started > self._timeout_s:
                sdk.messages.batches.cancel(batch_id)
                raise TimeoutError(f"batch {batch_id} did not end in {self._timeout_s:.0f} s")
            self._sleep(self._poll_s)

    def _settle_entry(
        self,
        item: Pending,
        hold: Reservation,
        entry: Any,
        result: BatchResult,
    ) -> None:
        outcome = entry.result
        if outcome.type == "succeeded":
            parsed, call = item.builder.read_batch_message(
                item.request, item.schema, outcome.message
            )
            billed = BATCH_DISCOUNT * call.cost_usd
            self._budget.settle(hold, billed)
            result.billed_usd += billed
            result.answered += 1
            item.record(item.key, item.request.step, parsed, call)
            return
        self._budget.settle(hold, 0.0)
        result.failed += 1
        count = self._failures.get(item.key, 0) + 1
        self._failures[item.key] = count
        log.info("batch request %s: %s (failure %d)", item.request.step, outcome.type, count)
        if count >= FAILURES_BEFORE_GIVING_UP:
            call = ModelCall(
                step=item.request.step,
                model=item.request.model,
                prompt_version=item.request.prompt_version,
                outcome="api_error" if outcome.type != "expired" else "timeout",
            )
            item.record(item.key, item.request.step, None, call)
