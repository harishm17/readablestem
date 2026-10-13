"""Stage: a11y-stem against the chatbot recipe (preregistration sections 6.2, 7 and 8).

Family A of the claims (section 8.3): a11y-stem against the Purdue-prompt chatbot on
gpt-6.1-sol, five comparisons, each judged at the Bonferroni level (the 99% interval of the
paired cluster bootstrap for k = 5). The expert-prompt arm and the gpt-6-luna arm, and every
single-system number, are descriptive.

The tables here are also used by the parser comparison (`proof.parsers`).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

import numpy as np

from a11y_stem.proof import chatbot, stages, systems_stage, systems_tables
from a11y_stem.proof.bundle import BundleWriter
from a11y_stem.proof.goldinfo import SectionFacts
from a11y_stem.proof.report import NA, Section, StageResult, Table, num
from a11y_stem.proof.stages import RecordContext, StageContext
from a11y_stem.proof.systems import SectionScore
from a11y_stem.proof.systems_stage import A11Y, Scored
from a11y_stem.proof.systems_tables import FAMILY_A, METRICS

TITLE = "Comparison with the chatbot recipe"
PRIMARY_ARM = "chatbot-sol-purdue"

EQUATION_KEYS = ["e2e", "e2e_clean", "unflagged", "matched_exact", "recall", "precision"]
HEADING_KEYS = ["h_f1", "h_precision", "h_recall", "h_level"]
FIGURE_KEYS = ["fig_pass", "fig_factual", "fig_nodesc"]
INVENTED_KEYS = ["inv_sentence", "inv_number", "inv_heading", "inv_eq_aligned", "inv_eq_unaligned"]


def _columns(scored: Scored, names: Sequence[str]) -> list[tuple[str, systems_tables.Vectors]]:
    return [(A11Y, scored.a11y_vectors), *[(n, scored.vectors[n]) for n in names]]


def per_run_table(title: str, scored: Scored, names: Sequence[str], g1: frozenset[str]) -> Table:
    """Each run's own point estimate (the run is not a resampling unit, but its variation
    is published)."""
    rows = []
    for name in names:
        report = scored.arms[name]
        by_run: dict[int, list[SectionScore]] = defaultdict(list)
        for s in report.scores:
            by_run[s.run].append(s)
        for run, items in sorted(by_run.items()):
            vec = systems_tables.system_vectors(items, g1)
            row = [f"{name} run {run}"]
            for key in ("e2e", "unflagged", "h_f1", "inv_sentence", "inv_number"):
                spec = METRICS[key]
                row.append(num(systems_tables.value_of(spec, vec), spec.digits))
            rows.append(row)
    return Table(
        title,
        [
            "Run",
            "end_to_end_exact_lenient",
            "unflagged_wrong_per_100",
            "heading F1",
            "invented sentences",
            "invented numbers",
        ],
        rows,
    )


def book_table(
    title: str, key: str, facts: Sequence[SectionFacts], columns: Sequence[tuple[str, dict]]
) -> Table:
    spec = METRICS[key]
    books = list(dict.fromkeys(f.id.split("/")[0] for f in facts))
    titles = {f.id.split("/")[0]: f.book_title for f in facts}
    rows = []
    for book in books:
        row = [titles.get(book, book)]
        for _, vectors in columns:
            per = {s: v for s, v in vectors.get(key, {}).items() if s.split("/")[0] == book}
            value = stats_point(per, spec)
            row.append(num(value, spec.digits))
        rows.append(row)
    return Table(
        title,
        ["Book (point estimates; three sections each)", *[n for n, _ in columns]],
        rows,
    )


def stats_point(per: dict, spec: systems_tables.MetricSpec) -> float | None:
    if not per:
        return None
    total = np.sum([list(v) for v in per.values()], axis=0)
    value = spec.statistic(total)
    return None if value is None else value * spec.scale


def section_rows(
    title: str, key: str, facts: Sequence[SectionFacts], columns: Sequence[tuple[str, dict]]
) -> Table:
    spec = METRICS[key]
    rows = []
    for fact in facts:
        row = [fact.id, num(fact.equations)]
        for _, vectors in columns:
            vec = vectors.get(key, {}).get(fact.id)
            value = spec.statistic(np.asarray(vec)) if vec else None
            row.append(num(None if value is None else value * spec.scale, spec.digits))
        rows.append(row)
    return Table(title, ["Section", "Gold equations", *[n for n, _ in columns]], rows)


def status_rows(
    scored: Scored, names: Sequence[str], n_sections: int
) -> dict[str, tuple[dict[str, int], int]]:
    """system -> (outputs by status, outputs expected)."""
    out: dict[str, tuple[dict[str, int], int]] = {}
    for name in names:
        out[name] = (dict(scored.arms[name].statuses), n_sections * scored.arms[name].expected_runs)
    return out


def cost_totals(scored: Scored, names: Sequence[str]) -> dict[str, systems_tables.ArmTotals]:
    out = {A11Y: scored.a11y_totals}
    for name in names:
        out[name] = scored.arms[name].totals
    return out


def arm_block(
    ctx: StageContext,
    scored: Scored,
    names: Sequence[str],
    *,
    title_prefix: str,
    keys_extra: Sequence[str] = (),
) -> list:
    cols = _columns(scored, names)
    n_boot = ctx.n_boot
    blocks: list = [
        systems_tables.metrics_table(
            f"{title_prefix}: equations (preregistration 7.1)",
            [*EQUATION_KEYS, "e2e_free"],
            cols,
            n_boot,
            note="`value [cluster 95% interval by section]`. A system that asks no questions "
            "has every wrong equation unflagged. G1 equations (operators packed into OpenStax "
            "text) can fail exact match when the answer is right. The allowlist-off row "
            "converts a system's TeX without a11y-stem's LaTeX allowlist (descriptive; "
            "a11y-stem's own allowlist is a product rule, so it has no such row).",
        ),
        systems_tables.metrics_table(
            f"{title_prefix}: headings (7.2)",
            HEADING_KEYS,
            cols,
            n_boot,
            note="Levels are compared relative to each side's top level in the section. "
            "A system that writes an `h1` document title the PDF does not have gets a false "
            "positive for it.",
        ),
        systems_tables.metrics_table(
            f"{title_prefix}: figures (7.3)",
            FIGURE_KEYS,
            cols,
            n_boot,
            note="Over the figures with an OpenStax alt text. Agreement with a model judge "
            "(gpt-6.1-sol) that sees the image, the candidate, the OpenStax alt and the "
            "caption, never which system wrote the candidate; not accuracy, and not "
            "calibrated against human labels in this run. A figure with no description is a "
            "fail. Judge cost is reported apart.",
        ),
        systems_tables.metrics_table(
            f"{title_prefix}: invented content (7.5; the units are in the invented-content "
            "section)",
            INVENTED_KEYS,
            cols,
            n_boot,
            note="All text, including text a11y-stem wrote itself. A section a system failed "
            "on has no units, so it adds nothing here; the raw rates include paraphrase the "
            "rule counts as unsupported, and the audit that measures that has not been done.",
        ),
        systems_tables.metrics_table(f"{title_prefix}: coverage (7.6)", ["coverage"], cols, n_boot),
        systems_tables.counts_table(
            f"{title_prefix}: counts behind the rates",
            [*EQUATION_KEYS, "e2e_free", *HEADING_KEYS, *FIGURE_KEYS, *INVENTED_KEYS, "coverage"],
            cols,
        ),
        systems_tables.wilson_table(
            f"{title_prefix}: Wilson 95% intervals (items treated as independent; narrower than "
            "the cluster intervals)",
            [*EQUATION_KEYS, "e2e_free", "h_precision", "h_recall", *FIGURE_KEYS, *INVENTED_KEYS],
            cols,
        ),
    ]
    return blocks


class ChatbotStage:
    name = "chatbot_comparison"
    title = TITLE

    def record(self, ctx: RecordContext, writer: BundleWriter) -> None:
        ctx.say(
            "chatbot: recorded by `a11y-stem proof --add-systems chatbot` "
            "(its own spend cap); nothing recorded here"
        )

    def run(self, ctx: StageContext) -> StageResult:
        scored = systems_stage.get_scored(ctx)
        names = [a.id for a in chatbot.ARMS if a.id in scored.arms]
        if not names:
            return StageResult(
                "not_wired",
                [Section(TITLE, ["**Not computed**: this bundle holds no chatbot outputs."])],
                [f"{TITLE}: no chatbot outputs in the bundle"],
            )
        n_boot = ctx.n_boot
        facts = ctx.facts
        blocks: list = []
        intro = [
            "The chatbot recipe is what an instructor does today: upload the PDF to a general "
            "model and type one sentence. Prompt of the primary arm, from Purdue Innovative "
            'Learning ("Accessibility in Mathematics"), verbatim, and quoted as the input '
            "it is, not as a claim this project makes:",
            "<!-- quote -->\n> " + chatbot.PROMPTS["purdue"] + "\n<!-- /quote -->",
            "Each arm sends the PDF as a file and a 150 dpi image of every page, then the "
            "prompt; no system instructions, no tools, `store=False`, default reasoning "
            "effort, at most 64,000 output tokens (`proof.chatbot`). The HTML in the reply is "
            "scored as it comes: a reply that was cut off or empty is a failure of the "
            "section, not asked again. Equations are read from the reply's MathJax TeX or "
            "MathML, converted the way a11y-stem converts its own model's LaTeX, and aligned "
            "to the gold equations by the document task's code, so the same scorer reads "
            "both systems.",
            "Arms: `chatbot-sol-purdue` is the primary arm (gpt-6.1-sol, two runs); "
            "`chatbot-sol-expert` is the careful prompt the earlier research wrote (one run) "
            "so that the comparison is not only against the weakest prompt an instructor "
            "might copy; `chatbot-luna-purdue` is the same Purdue prompt on the cheaper "
            "gpt-6-luna (two runs). The last two are descriptive.",
        ]
        missing = [a.id for a in chatbot.ARMS if a.id not in scored.arms]
        if missing:
            intro.append(
                "**Not run in this recording:** "
                + ", ".join(f"`{m}`" for m in missing)
                + " (see the status table)."
            )
        blocks += intro
        if PRIMARY_ARM in scored.arms:
            k = len(FAMILY_A)
            comparisons = [
                systems_tables.compare(
                    key,
                    label,
                    scored.a11y_vectors,
                    scored.vectors[PRIMARY_ARM],
                    a_name=A11Y,
                    other=PRIMARY_ARM,
                    k=k,
                    n_boot=n_boot,
                )
                for key, label in FAMILY_A
            ]
            blocks.append(
                systems_tables.claims_table(
                    "Family A: a11y-stem against the Purdue-prompt chatbot on gpt-6.1-sol",
                    comparisons,
                    a_name=A11Y,
                    k=k,
                    note="Paired cluster bootstrap by section: the same resampled sections "
                    'for both systems, B = 10,000, seed 0. "X is better" is written only '
                    "when the 99% interval (Bonferroni, k = 5) of the difference excludes 0; "
                    'otherwise "no difference this set can detect". With 15 sections '
                    "a percentile bootstrap is approximate and tends to be a little narrow. "
                    "The chatbot's two runs are pooled; a11y-stem's document task also has "
                    "two runs, and its headings task one. Every number here is a rate over "
                    "all items, and a section that failed counts as a failure.",
                )
            )
        blocks += arm_block(ctx, scored, names, title_prefix="All chatbot arms")
        blocks.append(per_run_table("Each run on its own", scored, names, ctx.g1))
        cols = _columns(scored, names)
        blocks.append(section_rows("End-to-end exact match by section", "e2e", facts, cols))
        blocks.append(book_table("End-to-end exact match by book", "e2e", facts, cols))
        blocks.append(book_table("Heading F1 by book", "h_f1", facts, cols))
        blocks.append(
            systems_tables.cost_time_table(
                "Cost and time per PDF page",
                cost_totals(scored, names),
                note="Chatbot: list-price cost of each call from its usage, and the call's "
                "wall clock, per PDF page of the section. a11y-stem: pipeline plus second "
                "check per page, and the document task's wall clock at concurrency 4 on the "
                "recording machine. Judge cost is not included for any system.",
            )
        )
        blocks.append(
            systems_tables.failure_table(
                "Sections that did not convert", status_rows(scored, names, len(facts))
            )
        )
        blocks.append(
            "**Not measured in this recording:** whether a wrong equation changes the meaning "
            "(the blind labels of preregistration 7.8 are the author's, and none exist yet), "
            "and the agreement of the figure judge with human labels."
        )
        return StageResult("ok", [Section(TITLE, blocks)])


stages.register(ChatbotStage())
_ = NA
