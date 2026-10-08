"""The weekly error-analysis report, from `error_aggregates` only (no document content).

`a11y-stem ops report --days 7` prints, for the last N UTC days: jobs and their
outcomes, pages, cost per page and conversion times; the review load (decisions a
person had to make per page, optional notes per page, by step); per step (equation,
figure, heading) the status and review-tier mix, the most frequent reasons for each tier,
failed checks and reasons, and model call outcomes; the second automated check
(answers, flags and score histogram per check and stage, its spend per page) and the
daily Decisions canary; failed static checks of the output HTML. Each table is plain
text, so it can be pasted into a weekly note as is.
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


TIERS = ("required", "optional", "none")


def _per_page(n: int, pages: int) -> str:
    return f"{n} ({n / pages:.2f} per page)" if pages else str(n)


def _review_load(counts: dict[tuple[str, str], int], pages: int) -> list[tuple[str, str]]:
    """Required decisions (a heading group counts once) and optional notes, per page."""
    decisions = {s: counts.get((s, "decisions:required"), 0) for s in STEPS}
    optional = {s: counts.get((s, "tier:optional"), 0) for s in STEPS}
    if not any(decisions.values()) and not any(optional.values()):
        return []
    rows = [
        ("required decisions", _per_page(sum(decisions.values()), pages)),
        ("optional notes", _per_page(sum(optional.values()), pages)),
    ]
    rows += [(f"  {s}: required", str(decisions[s])) for s in STEPS if decisions[s]]
    rows += [(f"  {s}: optional", str(optional[s])) for s in STEPS if optional[s]]
    return rows


def _second_check(counts: dict[tuple[str, str], int], step: str) -> list[tuple[str, str]]:
    """Answers of the second automated check for one step, per check and stage:
    outcomes, flags and the score histogram (counts only, as aggregated)."""
    rows: list[tuple[str, str]] = []
    found = sorted(
        (c.removeprefix("verify:"), n)
        for (s, c), n in counts.items()
        if s == step and c.startswith("verify:") and c != "verify:cost_microusd"
    )
    rows += [(label, str(n)) for label, n in found]
    micro = counts.get((step, "verify:cost_microusd"), 0)
    if micro:
        rows.append(("cost", f"${micro / 1e6:.4f}"))
    return rows


def _canary(counts: dict[tuple[str, str], int]) -> list[tuple[str, str]]:
    return [
        (c.removeprefix("decisions:"), str(n))
        for (s, c), n in sorted(counts.items())
        if s == "canary" and c.startswith("decisions:")
    ]


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
    lines += _table("Review load (as the pipeline left it)", _review_load(counts, pages))
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
        tiers = [
            (tier, _share(counts.get((step, f"tier:{tier}"), 0), total))
            for tier in TIERS
            if counts.get((step, f"tier:{tier}"))
        ]
        lines += _table(f"{step}: review tiers", tiers)
        for tier in ("required", "optional"):
            lines += _table(f"{step}: why {tier}", _top(counts, step, f"tier:{tier}:"))
        lines += _table(f"{step}: top failed checks", _top(counts, step, "check:"))
        lines += _table(f"{step}: top reasons", _top(counts, step, "reason:"))
        lines += _table(f"{step}: model calls", _top(counts, step, "call:"))
        lines += _table(f"{step}: review actions", _top(counts, step, "review:"))
        if step != "heading":
            lines += _table(f"{step}: second automated check", _second_check(counts, step))
    verify_micro = sum(counts.get((s, "verify:cost_microusd"), 0) for s in STEPS)
    if verify_micro:
        per_page = f" (${verify_micro / 1e6 / pages:.5f} per page)" if pages else ""
        lines += _table(
            "Second automated check: spend", [("total", f"${verify_micro / 1e6:.4f}{per_page}")]
        )
    lines += _table("Decisions canary (days run, drift, failed)", _canary(counts))
    lines += _table("Output HTML: failed static checks", _top(counts, "document", "check:"))
    return "\n".join(lines).rstrip() + "\n"


def weekly_report(session: Session, *, days: int, today: date) -> str:
    since = today - timedelta(days=max(1, days) - 1)
    return render_report(load_counts(session, since), since=since, until=today)
