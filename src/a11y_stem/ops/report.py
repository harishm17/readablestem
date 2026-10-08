"""The weekly error-analysis report, from `error_aggregates` only (no document content).

`a11y-stem ops report --days 7` prints, for the last N UTC days: jobs and their
outcomes, pages, cost per page and conversion times; per step (equation, figure,
heading) the status mix, the most frequent failed checks and reasons, and model call
outcomes; failed static checks of the output HTML. Each table is plain text, so it can be
pasted into a weekly note as is.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from a11y_stem.web.db import ErrorAggregate

STEPS = ("equation", "figure", "heading")
TOP = 10


def load_counts(session: Session, since: date) -> dict[tuple[str, str], int]:
    """(step, category) -> summed count over days >= `since`."""
    rows = session.execute(
        select(ErrorAggregate.step, ErrorAggregate.category, func.sum(ErrorAggregate.count))
        .where(ErrorAggregate.day >= since)
        .group_by(ErrorAggregate.step, ErrorAggregate.category)
    ).all()
    return {(step, category): int(total or 0) for step, category, total in rows}


def _table(title: str, rows: list[tuple[str, str]]) -> list[str]:
    if not rows:
        return [f"{title}: none", ""]
    width = max(len(label) for label, _ in rows)
    return [title, *(f"  {label:<{width}}  {value}" for label, value in rows), ""]


def _share(n: int, total: int) -> str:
    return f"{n} ({100 * n / total:.0f}%)" if total else str(n)


def _top(counts: dict[tuple[str, str], int], step: str, prefix: str) -> list[tuple[str, str]]:
    found = [
        (c.removeprefix(prefix), n)
        for (s, c), n in counts.items()
        if s == step and c.startswith(prefix)
    ]
    found.sort(key=lambda item: (-item[1], item[0]))
    return [(label, str(n)) for label, n in found[:TOP]]


def render_report(counts: dict[tuple[str, str], int], *, since: date, until: date) -> str:
    """The report text for already-loaded counts (see `load_counts`)."""
    job = {c: n for (s, c), n in counts.items() if s == "job"}
    done, failed = job.get("status:done", 0), job.get("status:failed", 0)
    pages, micro = job.get("pages", 0), job.get("cost_microusd", 0)
    lines = [f"a11y-stem error analysis, {since.isoformat()} to {until.isoformat()} (UTC)", ""]
    summary = [
        ("jobs finished", str(done + failed)),
        ("done", _share(done, done + failed)),
        ("failed", _share(failed, done + failed)),
        ("pages converted", str(pages)),
        ("model cost", f"${micro / 1e6:.4f}"),
        ("cost per page", f"${micro / 1e6 / pages:.4f}" if pages else "n/a"),
    ]
    lines += _table("Jobs", summary)
    outcomes = [
        (c.removeprefix("error:"), str(n)) for c, n in sorted(job.items()) if c.startswith("error:")
    ]
    lines += _table("Job error and outcome codes", outcomes)
    latency = [
        (c.removeprefix("latency:"), str(n))
        for c, n in sorted(job.items())
        if c.startswith("latency:")
    ]
    lines += _table("Conversion time (done jobs)", latency)
    for step in STEPS:
        statuses = {
            c.removeprefix("status:"): n
            for (s, c), n in counts.items()
            if s == step and c.startswith("status:")
        }
        total = sum(statuses.values())
        if not total:
            continue
        mix = [
            (status, _share(n, total))
            for status, n in sorted(statuses.items(), key=lambda i: -i[1])
        ]
        lines += _table(f"{step}: {total} fixes by status", mix)
        lines += _table(f"{step}: top failed checks", _top(counts, step, "check:"))
        lines += _table(f"{step}: top reasons", _top(counts, step, "reason:"))
        lines += _table(f"{step}: model calls", _top(counts, step, "call:"))
        lines += _table(f"{step}: review actions", _top(counts, step, "review:"))
    lines += _table("Output HTML: failed static checks", _top(counts, "document", "check:"))
    return "\n".join(lines).rstrip() + "\n"


def weekly_report(session: Session, *, days: int, today: date) -> str:
    since = today - timedelta(days=max(1, days) - 1)
    return render_report(load_counts(session, since), since=since, until=today)
