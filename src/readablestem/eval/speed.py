"""`readablestem eval speed`: the seconds a page takes, by page kind and by stage, and the bar.

Made from measured replays of the corpus (`eval.measure`): every upload converted as the
worker converts a job, under the production profile, with the recorded answers in place of
the provider. A replay waits on no model, so the seconds are the local work: reading the
file, layout, assembling, checking. What the models took when the recording was made is
printed apart, per step.

What the figures are:

- **Seconds a page, by stage.** A stage's seconds of a document (`pipeline.step_facts`)
  divided by its pages, at the median and the 95th percentile over documents.
- **Seconds a page, by page kind.** The conversion does not yet time each page (the page
  trace is a later work item), so a page is given its document's local seconds divided by
  its pages. A kind's figure is therefore the speed of the documents its pages are in. It is
  exact for a document of one kind and blurred for a mixed one, and the report says so.
- **Start-up** is a stage of a job and not of a page: starting the conversion process and
  loading its code takes the same time for 1 page and for 40, so it is printed per job and
  kept out of the seconds a page.
- **CPU and wall seconds** apart: wall is what a person waits, CPU is what a busy machine
  stretches.
- **Memory**: the most the conversion process held and the most the process that called it
  held, each with the upload that reached it.

A replay is run more than once (`--runs`, on other cores when `--cores` names them), and an
upload's seconds are those of its fastest run: a slow run is the machine, a fast one cannot
be. Where two runs of one kind differ by more than the bar's allowance, the report says the
machine was too busy to judge, and the bar judges nothing.

The bar (`judge`): against an earlier record made on the same machine class, no page kind is
slower by more than the allowance (a tenth) at the median or at the 95th percentile.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from readablestem.eval import measure
from readablestem.eval.measure import Converted
from readablestem.ops.step_report import percentile
from readablestem.pipeline.step_facts import START_UP

ALLOWANCE = 0.10


def local_seconds(done: Converted) -> float:
    """The seconds of the document's own stages: the conversion without its start-up."""
    return sum(fact.seconds for fact in done.facts.stages if fact.stage != START_UP)


def fastest(runs: Sequence[Sequence[Converted]]) -> list[Converted]:
    """Each upload's fastest finished run, in the order of the first run."""
    best: dict[str, Converted] = {}
    for run in runs:
        for done in run:
            held = best.get(done.upload)
            if held is None or (not done.error and (bool(held.error) or done.wall_s < held.wall_s)):
                best[done.upload] = done
    return list(best.values())


def _spread(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50": round(percentile(values, 0.50), 4) if values else 0.0,
        "p95": round(percentile(values, 0.95), 4) if values else 0.0,
    }


def _kinds(measured: Sequence[Converted]) -> dict[str, dict[str, Any]]:
    seconds: dict[str, list[float]] = defaultdict(list)
    documents: dict[str, set[str]] = defaultdict(set)
    for done in measured:
        a_page = local_seconds(done) / done.pages
        for kind in done.kinds:
            seconds[kind].append(a_page)
            documents[kind].add(done.upload)
    return {
        kind: {
            "pages": len(seconds[kind]),
            "documents": len(documents[kind]),
            "s_page": _spread(seconds[kind]),
        }
        for kind in measure.KINDS
        if seconds[kind]
    }


def _unsteady(runs: Sequence[Sequence[Converted]], allowance: float) -> list[str]:
    """The page kinds whose median differs between two runs by more than `allowance`."""
    medians: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        finished = [done for done in run if not done.error and done.pages]
        for kind, row in _kinds(finished).items():
            medians[kind].append(row["s_page"]["p50"])
    return sorted(
        kind
        for kind, values in medians.items()
        if len(values) > 1 and min(values) > 0 and max(values) / min(values) - 1 > allowance
    )


def report(runs: Sequence[Sequence[Converted]], *, allowance: float = ALLOWANCE) -> dict[str, Any]:
    """The figures of one or more measured replays of the same uploads, as plain data: the
    `metrics` of a speed record (`measure.record`), which `render` prints and `judge`
    compares."""
    every = fastest(runs)
    measured = [done for done in every if not done.error and done.pages]
    pages = sum(done.pages for done in measured)

    stage_s: dict[str, list[float]] = defaultdict(list)  # seconds a page, per document
    stage_total: dict[str, float] = defaultdict(float)
    order: list[str] = []
    call_s: dict[str, list[float]] = defaultdict(list)
    rows: list[dict[str, Any]] = []
    for done in measured:
        for fact in done.facts.stages:
            if fact.stage not in order:
                order.append(fact.stage)
            if fact.stage != START_UP:
                stage_s[fact.stage].append(fact.seconds / done.pages)
                stage_total[fact.stage] += fact.seconds
        for call in done.facts.calls:
            call_s[call.step].append(call.seconds)
        start = next((f.seconds for f in done.facts.stages if f.stage == START_UP), 0.0)
        rows.append(
            {
                "upload": done.upload,
                "pages": done.pages,
                "wall_s": done.wall_s,
                "start_up_s": start,
                "local_s": round(local_seconds(done), 4),
                "cpu_s": done.cpu_s,
                "caller_cpu_s": done.own_cpu_s,
                # What the requests took when they were recorded. Several are sent at once,
                # so this is more than a person waited for them.
                "recorded_model_s": round(sum(c.seconds for c in done.facts.calls), 2),
                "peak_mb": done.peak_mb,
                "caller_peak_mb": done.own_peak_mb,
            }
        )
    local = sum(stage_total.values())
    biggest = max(measured, key=lambda done: done.peak_mb, default=None)
    caller = max(measured, key=lambda done: done.own_peak_mb, default=None)
    return {
        "runs": len(runs),
        "uploads": {
            "measured": len(measured),
            "not_measured": sorted(done.upload for done in every if done.error),
        },
        "pages": pages,
        "s_page": {
            "wall": _spread(
                [
                    (d.wall_s - r["start_up_s"]) / d.pages
                    for d, r in zip(measured, rows, strict=True)
                ]
            ),
            "cpu": _spread([done.cpu_s / done.pages for done in measured]),
        },
        "kinds": _kinds(measured),
        "stages": {
            stage: {
                "documents": len(stage_s[stage]),
                "s_page": _spread(stage_s[stage]),
                # The stage's seconds of the whole run over every page of it.
                "s_page_mean": round(stage_total[stage] / pages, 4) if pages else 0.0,
                "share": round(stage_total[stage] / local, 4) if local else 0.0,
            }
            for stage in order
            if stage != START_UP
        },
        "start_up_s": _spread([row["start_up_s"] for row in rows]),
        "totals": {
            "wall_s": round(sum(row["wall_s"] for row in rows), 2),
            "local_s": round(local, 2),
            "cpu_s": round(sum(row["cpu_s"] for row in rows), 2),
            "caller_cpu_s": round(sum(row["caller_cpu_s"] for row in rows), 2),
            "recorded_model_s": round(sum(row["recorded_model_s"] for row in rows), 2),
        },
        # How long a check sent as a batch takes to come back. No batch answer is in the
        # recording, so there is nothing to read it from yet.
        "background_check_s": None,
        "model_calls": {
            step: {"calls": len(values), "s": _spread(values)}
            for step, values in sorted(call_s.items())
        },
        "memory": {
            "conversion_peak_mb": biggest.peak_mb if biggest else 0.0,
            "conversion_peak_upload": biggest.upload if biggest else "",
            "caller_peak_mb": caller.own_peak_mb if caller else 0.0,
            "caller_peak_upload": caller.upload if caller else "",
        },
        "too_busy_to_judge": _unsteady(runs, allowance),
        "documents": rows,
    }


@dataclass(frozen=True)
class Verdict:
    """What the bar says of a report against an earlier one."""

    judged: bool  # False: the two cannot be compared, and `why` says so
    why: str = ""
    slower: list[str] = field(default_factory=list)  # one line per figure over the allowance

    @property
    def met(self) -> bool:
        return self.judged and not self.slower


def judge(now: dict[str, Any], before: dict[str, Any], *, allowance: float = ALLOWANCE) -> Verdict:
    """The bar, on two speed records: no page kind of `now` is slower than in `before` by
    more than `allowance`, at the median or at the 95th percentile. Only two records from
    the same machine class are compared, and only when the machine was steady while `now`
    was measured."""
    here, there = now.get("machine"), before.get("machine")
    if not here or here != there:
        return Verdict(False, f"measured on another machine class: {there} then, {here} now")
    busy = now["metrics"].get("too_busy_to_judge")
    if busy:
        return Verdict(
            False,
            "the machine was too busy to judge: two runs of the same code differ by more "
            "than the allowance on " + ", ".join(busy),
        )
    slower = []
    for kind, row in now["metrics"]["kinds"].items():
        earlier = before["metrics"]["kinds"].get(kind)
        if earlier is None:
            continue
        for point in ("p50", "p95"):
            was, is_now = earlier["s_page"][point], row["s_page"][point]
            if was > 0 and is_now > was * (1 + allowance):
                slower.append(
                    f"{kind}: {is_now:.3f} s a page at {point}, {was:.3f} before "
                    f"({is_now / was - 1:+.0%}, allowed {allowance:+.0%})"
                )
    return Verdict(True, slower=slower)


def _s(value: float) -> str:
    return f"{value:.3f}"


def _table(head: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    widths = [max(len(str(row[i])) for row in (head, *rows)) for i in range(len(head))]
    return [
        "  ".join(str(cell).ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in (head, *rows)
    ]


def render(record: dict[str, Any], verdict: Verdict | None = None) -> str:
    """A speed record as the lines `readablestem eval speed` prints."""
    made = record["metrics"]
    machine = record.get("machine") or {}
    uploads = made["uploads"]
    lines = [
        f"speed of a page: {uploads['measured']} uploads, {made['pages']} pages, "
        f"fastest of {made['runs']} run(s) of each; no model is waited for",
        *measure.header(record),
        f"Machine: {machine.get('cpu')}, {machine.get('cores')} cores.",
    ]
    if uploads["not_measured"]:
        lines.append("not measured: " + ", ".join(uploads["not_measured"]))
    if made["too_busy_to_judge"]:
        lines.append(
            "the machine was too busy to judge: runs of the same code differ by more than "
            "the allowance on " + ", ".join(made["too_busy_to_judge"])
        )
    lines += [
        "",
        f"seconds a page, start-up apart: wall {_s(made['s_page']['wall']['p50'])} at the "
        f"median and {_s(made['s_page']['wall']['p95'])} at p95; CPU "
        f"{_s(made['s_page']['cpu']['p50'])} and {_s(made['s_page']['cpu']['p95'])} "
        "(CPU includes start-up)",
        f"start-up of a job: {_s(made['start_up_s']['p50'])} s at the median, "
        f"{_s(made['start_up_s']['p95'])} at p95",
        f"the whole run: {made['totals']['wall_s']:.0f} s of wall time, "
        f"{made['totals']['local_s']:.0f} of them after start-up; CPU "
        f"{made['totals']['cpu_s']:.0f} s in the conversion processes and "
        f"{made['totals']['caller_cpu_s']:.0f} s in the processes that called them",
        "",
        "by page kind (a page has its document's seconds a page):",
    ]
    lines += _table(
        ("kind", "pages", "documents", "median", "p95"),
        [
            (
                kind,
                row["pages"],
                row["documents"],
                _s(row["s_page"]["p50"]),
                _s(row["s_page"]["p95"]),
            )
            for kind, row in made["kinds"].items()
        ],
    )
    lines += ["", "by stage (seconds a page of a document that has the stage):"]
    lines += _table(
        ("stage", "documents", "median", "p95", "mean over every page", "share of local time"),
        [
            (
                stage,
                row["documents"],
                _s(row["s_page"]["p50"]),
                _s(row["s_page"]["p95"]),
                _s(row["s_page_mean"]),
                f"{row['share']:.0%}",
            )
            for stage, row in made["stages"].items()
        ],
    )
    lines += [
        "",
        "what the models took when the answers were recorded (seconds a request; "
        f"{made['totals']['recorded_model_s']:.0f} s in all, several sent at once):",
    ]
    lines += _table(
        ("step", "requests", "median", "p95"),
        [
            (step, row["calls"], f"{row['s']['p50']:.2f}", f"{row['s']['p95']:.2f}")
            for step, row in made["model_calls"].items()
        ],
    )
    memory = made["memory"]
    lines += [
        "how long a background check takes to come back: not measured (no batch answer is "
        "in the recording)",
        "",
        f"most memory: the conversion process {memory['conversion_peak_mb']:.0f} MB "
        f"({memory['conversion_peak_upload']}); the process that called it "
        f"{memory['caller_peak_mb']:.0f} MB ({memory['caller_peak_upload']})",
    ]
    if verdict is not None:
        lines.append("")
        if not verdict.judged:
            lines.append(f"bar: not judged ({verdict.why})")
        elif verdict.met:
            lines.append("bar: met (no page kind slower than the earlier record allows)")
        else:
            lines += ["bar: NOT met", *(f"  {line}" for line in verdict.slower)]
    return "\n".join(lines)
