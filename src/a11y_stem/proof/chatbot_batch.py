"""The chatbot recipe on the Message Batches API (Claude arms only): the same requests at
half the price, answered when the batch ends instead of at once.

What is sent is exactly what `proof.chatbot._send` streams: the model, `max_tokens` 64,000
and the one user message built by `build_claude_input` (the PDF, a PNG of every page, the
prompt last), with no system prompt, no `thinking`, no `output_config` and no refusal
fallback. A batch request is not streamed, which changes how the reply travels and nothing
the model is given. The records are the ones `run_job` writes (`write_meta`), with the
batch noted on them.

Money. Before anything is sent, each job's input is counted (the free `count_tokens`
endpoint) and its worst case at the batch price is held against the cap; a job the cap
cannot hold is not run and is listed, as in the live path. A job's `cost_usd` is its usage
at list price, the number a live call would have recorded, so arms recorded either way
compare; `billed_usd` beside it is what the batch charged and what the cap was settled at.
`recorded_cost` therefore over-counts the batch's spend by its discount, never under-counts.

A request the API could not answer (an infrastructure error, an expired or cancelled one) is
sent again in a later batch up to `RETRIES` times, like a live call's retries; any other error is
a crash of the section, as live. A batch holds at most 256 MB, so jobs are sent as several
batches submitted one after the other and waited for together.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a11y_stem.core.budget import BudgetExceeded, InMemorySpendGuard, Reservation
from a11y_stem.models import pricing
from a11y_stem.models.anthropic_batch import with_retries
from a11y_stem.models.pricing import BATCH_DISCOUNT
from a11y_stem.proof.chatbot import (
    MAX_OUTPUT_TOKENS,
    RETRIES,
    ChatArm,
    Job,
    _claude_reply,
    build_claude_input,
    jobs_for,
    page_pngs,
    write_meta,
)

MAX_BATCH_BYTES = 200_000_000
POLL_S = 60.0
TIMEOUT_S = 26 * 3600
# What the API says when it, not the request, failed: worth sending again.
_RETRY_TYPES = frozenset({"api_error", "overloaded_error", "rate_limit_error"})


@dataclass
class _Sent:
    job: Job
    hold: Reservation
    counted: int
    pages: int


def _held_estimate(model: str, counted: int) -> float:
    """The worst case of one job at the batch price: its input and a full reply."""
    price = pricing.price_for(model).for_prompt(counted)
    return BATCH_DISCOUNT * (counted * price.input + MAX_OUTPUT_TOKENS * price.output) / 1e6


def record_arms_batch(
    pdfs: dict[str, Path],
    out_dir: Path,
    *,
    arms: Sequence[ChatArm],
    max_usd: float,
    client: Any,
    runs: int | None = None,
    say: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = POLL_S,
) -> dict[str, Any]:
    """Run every job of `arms` (all on Claude) as Message Batches within one cap. Returns
    what `chatbot.record_arms` does: the spend (billed) and the jobs the cap refused."""
    if {a.provider for a in arms} - {"anthropic"}:
        raise ValueError("the Message Batches API serves the Claude arms only")
    guard = InMemorySpendGuard(cap_usd=max_usd)
    refused: list[str] = []
    for arm in arms:  # in the order of spending: the primary arm first
        todo = [
            job
            for job in jobs_for([arm], pdfs, runs=runs)
            if not (out_dir / arm.id / f"{job.stem}.json").is_file()
        ]
        attempts: dict[str, list[str]] = {}
        for round_ in range(RETRIES + 1):
            if not todo:
                break
            again = _round(todo, out_dir, guard, client, refused, attempts, say, sleep, poll_s)
            if again and round_ == RETRIES:
                for job in again:
                    _crash(job, out_dir, attempts.get(job.stem, []), say)
                again = []
            todo = again
    return {
        "spent_usd": round(guard.spent_usd(), 6),
        "cap_usd": max_usd,
        "refused_by_cap": sorted(refused),
    }


def _round(
    jobs: list[Job],
    out_dir: Path,
    guard: InMemorySpendGuard,
    client: Any,
    refused: list[str],
    attempts: dict[str, list[str]],
    say: Callable[[str], None],
    sleep: Callable[[float], None],
    poll_s: float,
) -> list[Job]:
    """Send `jobs` as batches and write their records. Returns the jobs to send again."""
    submitted: list[tuple[str, dict[str, _Sent], float]] = []
    chunk: dict[str, _Sent] = {}
    requests: list[dict[str, Any]] = []
    size = 0

    def flush() -> None:
        nonlocal chunk, requests, size
        if not requests:
            return
        batch = with_retries(
            lambda: client.messages.batches.create(requests=requests), idempotent=False
        )
        say(f"batch {batch.id}: {len(requests)} requests sent")
        submitted.append((batch.id, chunk, time.monotonic()))
        chunk, requests, size = {}, [], 0

    for job in jobs:
        pngs = page_pngs(job.pdf)
        inp = build_claude_input(job.pdf, job.arm.prompt_text, pngs)
        counted = client.messages.count_tokens(model=job.arm.model, messages=inp).input_tokens
        try:
            hold = guard.reserve(_held_estimate(job.arm.model, counted))
        except BudgetExceeded:
            refused.append(f"{job.arm.id}/{job.stem}")
            continue
        weight = len(json.dumps(inp))
        if requests and size + weight > MAX_BATCH_BYTES:
            flush()
        custom_id = f"j{len(chunk):04d}-{len(submitted):03d}"
        requests.append(
            {
                "custom_id": custom_id,
                "params": {
                    "model": job.arm.model,
                    "max_tokens": MAX_OUTPUT_TOKENS,
                    "messages": inp,
                },
            }
        )
        chunk[custom_id] = _Sent(job, hold, counted, len(pngs))
        size += weight
    flush()

    again: list[Job] = []
    for batch_id, sent, started in submitted:
        _wait(client, batch_id, sleep, poll_s, say)
        seconds = time.monotonic() - started
        seen: set[str] = set()
        try:
            for entry in with_retries(
                lambda bid=batch_id: list(client.messages.batches.results(bid))
            ):
                found = sent.get(entry.custom_id)
                if found is None or entry.custom_id in seen:
                    continue
                seen.add(entry.custom_id)
                retry = _settle(found, entry, out_dir, guard, attempts, seconds, batch_id, say)
                if retry:
                    again.append(found.job)
        finally:
            for cid, found in sent.items():
                if cid not in seen:
                    guard.settle(found.hold, 0.0)  # no answer came back: nothing was billed
                    again.append(found.job)
    return again


def _wait(
    client: Any, batch_id: str, sleep: Callable[[float], None], poll_s: float, say: Any
) -> None:
    started = time.monotonic()
    while True:
        batch = with_retries(lambda: client.messages.batches.retrieve(batch_id))
        if batch.processing_status == "ended":
            counts = batch.request_counts
            say(
                f"batch {batch_id}: ended, {counts.succeeded} succeeded, "
                f"{counts.errored} errored, {counts.expired} expired"
            )
            return
        if time.monotonic() - started > TIMEOUT_S:
            client.messages.batches.cancel(batch_id)
            raise TimeoutError(f"batch {batch_id} did not end in {TIMEOUT_S / 3600:.0f} hours")
        sleep(poll_s)


def _settle(
    sent: _Sent,
    entry: Any,
    out_dir: Path,
    guard: InMemorySpendGuard,
    attempts: dict[str, list[str]],
    seconds: float,
    batch_id: str,
    say: Callable[[str], None],
) -> bool:
    """Settle one result and write its record. True: send the job again."""
    job, result = sent.job, entry.result
    if result.type == "succeeded":
        reply = _claude_reply(result.message, job.arm.model)
        billed = BATCH_DISCOUNT * reply.cost
        guard.settle(sent.hold, billed)
        write_meta(
            job,
            out_dir,
            reply=reply,
            error=None,
            attempts=attempts.get(job.stem, []),
            cost=reply.cost,
            cost_estimated=False,
            seconds=seconds,
            pdf_pages=sent.pages,
            counted=sent.counted,
            say=say,
            extra={"batch": True, "batch_id": batch_id, "billed_usd": round(billed, 6)},
        )
        return False
    guard.settle(sent.hold, 0.0)  # an errored, expired or cancelled request is not billed
    name = _error_name(result)
    attempts.setdefault(job.stem, []).append(name)
    if result.type in ("expired", "canceled") or name in _RETRY_TYPES:
        return True
    _crash(job, out_dir, attempts[job.stem], say, sent=sent, seconds=seconds)
    return False


def _error_name(result: Any) -> str:
    if result.type != "errored":
        return result.type
    error = getattr(result.error, "error", result.error)
    return str(getattr(error, "type", "error"))


def _crash(
    job: Job,
    out_dir: Path,
    attempts: list[str],
    say: Callable[[str], None],
    *,
    sent: _Sent | None = None,
    seconds: float = 0.0,
) -> None:
    """Record a job the API could not answer, as a crash of the section (not asked again)."""
    write_meta(
        job,
        out_dir,
        reply=None,
        error=attempts[-1] if attempts else "error",
        attempts=attempts,
        cost=0.0,
        cost_estimated=False,
        seconds=seconds,
        pdf_pages=sent.pages if sent else 0,
        counted=sent.counted if sent else 0,
        say=say,
        extra={"batch": True},
    )
