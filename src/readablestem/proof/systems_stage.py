"""Scores every system of a bundle once, for the stages that print them.

The three stages that need the other systems (invented content, the chatbot comparison, the
parser comparison) share this one computation, cached on the stage context: it reads the
bundle's `systems/`, scores each output on its section (`proof.systems`), scores ReadableSTEM's
own HTML from the replay in the same way, and reduces everything to per-section vectors.

A system section that is missing, or whose status is not ok, scores as found nothing; its
gold items stay in every denominator (preregistration section 9).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from readablestem.eval.invented import read as inv_read
from readablestem.eval.invented import score as inv_score
from readablestem.eval.invented import text as inv_text
from readablestem.proof import chatbot, systems, systems_tables
from readablestem.proof.stages import StageContext
from readablestem.proof.systems import SectionScore, SystemRun, load_arms, section_golds

A11Y = "ReadableSTEM"
SEED_KEY = "systems_scored"


@dataclass
class ArmReport:
    name: str
    kind: str  # "chatbot" | "parser"
    expected_runs: int
    scores: list[SectionScore] = field(default_factory=list)
    statuses: dict[str, int] = field(default_factory=dict)
    totals: systems_tables.ArmTotals = field(default_factory=systems_tables.ArmTotals)
    metas: list[dict[str, Any]] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.statuses.get("not_run", 0) == 0


@dataclass
class Scored:
    a11y_vectors: systems_tables.Vectors
    a11y_entries: list[dict[str, Any]] | None
    a11y_content_entries: list[dict[str, Any]] | None
    a11y_totals: systems_tables.ArmTotals
    arms: dict[str, ArmReport]
    vectors: dict[str, systems_tables.Vectors]
    entries: dict[str, list[dict[str, Any]]]


def _expected_runs(arm: str, made: dict[str, int] | None = None) -> int:
    """Runs of an arm: the number the recording says it made, else the arm's own."""
    if made and arm in made:
        return int(made[arm])
    return chatbot.ARM_BY_ID[arm].runs if arm in chatbot.ARM_BY_ID else 1


def _empty_run(arm: str, section: str, run: int, status: str) -> SystemRun:
    meta = {"arm": arm, "section": section, "status": status}
    return SystemRun(arm, section, run, meta, None)


def get_scored(ctx: StageContext) -> Scored:
    cached = ctx.cache.get(SEED_KEY)
    if cached is not None:
        return cached
    order = [s.id for s in ctx.pset.sections]
    golds = section_golds(ctx.records, order)
    references: dict[str, inv_text.Reference] = {}
    gold_mathml: dict[str, list[str]] = {}
    for section in order:
        references[section] = inv_text.Reference.from_pdf(ctx.bundle.pdf_bytes(section))
        gold_mathml[section] = [g["mathml"] for g in golds[section].equations]

    # ReadableSTEM's own HTML (from the replay): invented content and coverage
    entries: list[dict[str, Any]] | None = None
    coverage: dict[str, tuple[float, float]] = {}
    if ctx.arm.html:
        entries = []
        for (repeat, section), html in sorted(ctx.arm.html.items()):
            output = inv_read.read_html(html)
            score = inv_score.score_output(
                references[section], output, gold_equations=gold_mathml[section]
            )
            entries.append({"section": section, "run": f"run{repeat}", "score": score.to_dict()})
            hit, total = systems.coverage_counts(references[section], output)
            old = coverage.get(section, (0.0, 0.0))
            coverage[section] = (old[0] + hit, old[1] + total)
    a11y_vec = systems_tables.a11y_vectors(ctx.arm, ctx.g1, entries, coverage)

    totals = systems_tables.ArmTotals()
    for outcome in ctx.arm.runs("document"):
        for item in outcome.items:
            pages = item.get("pdf_pages", 0)
            cost = item.get("cost_usd", 0.0) + item.get("verifier_cost_usd", 0.0)
            totals.total_cost += cost
            if pages:
                totals.cost_per_page.append(cost / pages)
    for run in ctx.bundle.runs:
        if run["task"] == "document":
            for t in run.get("timing", []):
                if t["pdf_pages"]:
                    totals.seconds_per_page.append(t["latency_ms"] / 1000.0 / t["pdf_pages"])

    arms: dict[str, ArmReport] = {}
    vectors: dict[str, systems_tables.Vectors] = {}
    pooled_entries: dict[str, list[dict[str, Any]]] = {}
    for arm_id, sections in load_arms(ctx.bundle).items():
        report = ArmReport(
            name=arm_id,
            kind="chatbot" if arm_id.startswith("chatbot-") else "parser",
            expected_runs=_expected_runs(arm_id, ctx.bundle.manifest.get("systems_runs")),
        )
        arm_entries: list[dict[str, Any]] = []
        for section in order:
            have = {r.run: r for r in sections.get(section, [])}
            for run_no in range(1, report.expected_runs + 1):
                run = have.get(run_no) or _empty_run(arm_id, section, run_no, "not_run")
                score = systems.score_section(
                    run,
                    golds[section],
                    ctx.bundle.pdf_bytes(section),
                    gold_mathml[section],
                    reference=references[section],
                )
                report.scores.append(score)
                report.metas.append(run.meta)
                report.statuses[run.status] = report.statuses.get(run.status, 0) + 1
                if score.invented is not None:
                    arm_entries.append(
                        {"section": section, "run": f"run{run_no}", "score": score.invented}
                    )
                pages = run.pdf_pages or score.pdf_pages
                cost = run.cost_usd
                report.totals.total_cost += cost
                report.totals.judge_cost += float(run.meta.get("judge_cost_usd") or 0.0)
                if run.status in {"ok", "truncated", "empty"} and pages:
                    report.totals.cost_per_page.append(cost / pages)
                    seconds = run.meta.get("seconds")
                    if seconds is not None:
                        report.totals.seconds_per_page.append(float(seconds) / pages)
                rss = (run.meta.get("measure") or {}).get("peak_rss_mb")
                if rss:
                    report.totals.peak_rss_mb.append(float(rss))
        arms[arm_id] = report
        vectors[arm_id] = systems_tables.system_vectors(report.scores, ctx.g1)
        pooled_entries[arm_id] = arm_entries
    scored = Scored(
        a11y_vectors=a11y_vec,
        a11y_entries=entries,
        a11y_content_entries=entries,
        a11y_totals=totals,
        arms=arms,
        vectors=vectors,
        entries=pooled_entries,
    )
    ctx.cache[SEED_KEY] = scored
    return scored
