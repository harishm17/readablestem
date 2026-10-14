"""Drive an eval in stages on the Message Batches API (`models.anthropic_batch`).

`drive(run_once, ...)` runs `run_once` (a whole eval pass against the answers file), sends
the requests that pass could not answer as batches, and runs it again, until a pass asks
for nothing new. The last pass is the run: every answer in it is a recorded one.
"""

from __future__ import annotations

from collections.abc import Callable

from readablestem.models.anthropic_batch import BatchRunner, PendingPool

MAX_STAGES = 8


class StageError(RuntimeError):
    """The staged run could not finish (the cap, or requests that keep failing)."""


def drive[R](
    run_once: Callable[[], R],
    pool: PendingPool,
    runner: BatchRunner,
    *,
    say: Callable[[str], None] = print,
) -> R:
    for stage in range(1, MAX_STAGES + 1):
        result = run_once()
        pending = pool.take()
        if not pending:
            say(f"staged run: finished after {stage} pass(es)")
            return result
        say(f"staged run, pass {stage}: {len(pending)} requests to send")
        sent = runner.run(pending)
        say(
            f"  answered {sent.answered}, failed {sent.failed}, billed ${sent.billed_usd:.4f} "
            "at the batch price"
        )
        if sent.unsent:
            raise StageError(
                f"the spend cap could not hold {sent.unsent} requests; the answers so far are "
                "in the answers file, so a rerun with a larger --max-usd goes on from there"
            )
    raise StageError(f"requests were still being asked after {MAX_STAGES} passes")
