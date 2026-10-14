"""Stage: ReadableSTEM against the chatbot recipe (preregistration sections 6.2, 7 and 8).

Family A of the claims (section 8.3): ReadableSTEM against the Purdue-prompt chatbot on
gpt-6.1-sol, five comparisons, each judged at the Bonferroni level (the 99% interval of the
paired cluster bootstrap for k = 5). The expert-prompt arm and the gpt-6-luna arm, and every
single-system number, are descriptive.

The tables here are also used by the parser comparison (`proof.parsers`).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

import numpy as np

from readablestem.proof import chatbot, stages, systems_stage, systems_tables
from readablestem.proof.bundle import BundleWriter
from readablestem.proof.goldinfo import SectionFacts
from readablestem.proof.report import NA, Section, StageResult, Table, num
from readablestem.proof.stages import RecordContext, StageContext
from readablestem.proof.systems import SectionScore
from readablestem.proof.systems_stage import A11Y, Scored
from readablestem.proof.systems_tables import FAMILY_A, METRICS

TITLE = "Comparison with the chatbot recipe"
PRIMARY_ARM = "chatbot-sol-purdue"  # the OpenAI recording's; `primary_arm` finds the recording's


def primary_arm(arms) -> str | None:
    """The primary chatbot arm among `arms` (ids), whichever provider it ran on."""
    return next((a for a in chatbot.PRIMARY_IDS if a in arms), None)


def arms_in(scored: Scored) -> list[chatbot.ChatArm]:
    """The chatbot arms the bundle holds, in the order of spending."""
    every = (*chatbot.ARMS, *chatbot.CLAUDE_ARMS)
    return [a for a in every if a.id in scored.arms]


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
    out = {f"{A11Y} (document task)": scored.a11y_totals}
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
            "converts a system's TeX without ReadableSTEM's LaTeX allowlist (descriptive; "
            "ReadableSTEM's own allowlist is a product rule, so it has no such row).",
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
            f"({ctx.settings.main_model}) that sees the image, the candidate, the OpenStax alt "
            "and the caption, never which system wrote the candidate; not accuracy, and not "
            "calibrated against human labels in this run. A figure with no description is a "
            "fail. Judge cost is reported apart.",
        ),
        systems_tables.metrics_table(
            f"{title_prefix}: invented content (7.5; the units are in the invented-content "
            "section)",
            INVENTED_KEYS,
            cols,
            n_boot,
            note="All text, including text ReadableSTEM wrote itself. A section a system failed "
            "on has no units, so it adds nothing here; the raw rates include paraphrase the "
            "rule counts as unsupported, and the audit that measures that has not been done. "
            "A rate of exactly 0 has a zero-width bootstrap interval (every resample is 0): "
            "the Wilson upper bound in the last table is the bound to read.",
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


_WORDS = {1: "one", 2: "two", 3: "three"}


def _count(n: int) -> str:
    return _WORDS.get(n, str(n))


def family(scored: Scored) -> tuple[chatbot.ChatArm, ...]:
    """The arms that belong with the arms the recording holds: Claude's when it holds any
    of those, else the OpenAI ones."""
    claude = any(a.id in scored.arms for a in chatbot.CLAUDE_ARMS)
    return chatbot.CLAUDE_ARMS if claude else chatbot.ARMS


def send_text(scored: Scored) -> str:
    """How the arms send the PDF (the same for both providers up to the API's own words)."""
    if family(scored) is chatbot.CLAUDE_ARMS:
        return (
            "Each arm sends the PDF as a document block and a 150 dpi image of every page, "
            "then the prompt; no system prompt, no tools, the model's default reasoning "
            "effort, at most 64,000 output tokens, streamed (`proof.chatbot`)."
        )
    return (
        "Each arm sends the PDF as a file and a 150 dpi image of every page, then the "
        "prompt; no system instructions, no tools, `store=False`, default reasoning "
        "effort, at most 64,000 output tokens (`proof.chatbot`)."
    )


def arms_text(ctx: StageContext, scored: Scored) -> str:
    if family(scored) is chatbot.CLAUDE_ARMS:
        made = {a.id: scored.arms[a.id].expected_runs for a in arms_in(scored)}
        parts = []
        for arm in arms_in(scored):
            runs = made[arm.id]
            what = (
                "the primary arm"
                if arm.role == "primary"
                else "the same Purdue prompt on the cheaper model"
            )
            parts.append(
                f"`{arm.id}` is {what} ({arm.model}, {_count(runs)} run{'s' if runs > 1 else ''})"
            )
        return (
            "Arms: "
            + "; ".join(parts)
            + ". The cheaper arm is descriptive. The registration's expert-prompt arm is not "
            "run on Claude (Addendum 6)."
        )
    return (
        "Arms: `chatbot-sol-purdue` is the primary arm (gpt-6.1-sol, two runs); "
        "`chatbot-sol-expert` is a longer, more careful prompt written for this project "
        "(one run; the text is `src/readablestem/proof/chat_prompts.json`) "
        "so that the comparison is not only against the weakest prompt an instructor "
        "might copy; `chatbot-luna-purdue` is the same Purdue prompt on the cheaper "
        "gpt-6-luna (two runs). The last two are descriptive."
    )


def pooled_note(ctx: StageContext, scored: Scored, primary: str) -> str:
    runs = scored.arms[primary].expected_runs
    own = ctx.pset.repeats.get("document", 1)
    chat = f"The chatbot's {_count(runs)} run{'s are' if runs > 1 else ' is'} pooled; "
    if runs == own:
        doc = f"ReadableSTEM's document task also has {_count(own)} run{'s' if own > 1 else ''}"
    else:
        doc = f"ReadableSTEM's document task has {_count(own)} run{'s' if own > 1 else ''}"
    return chat + doc + ", and its headings task one. "


class ChatbotStage:
    name = "chatbot_comparison"
    title = TITLE

    def record(self, ctx: RecordContext, writer: BundleWriter) -> None:
        ctx.say(
            "chatbot: recorded by `readablestem proof --add-systems chatbot` "
            "(its own spend cap); nothing recorded here"
        )

    def run(self, ctx: StageContext) -> StageResult:
        scored = systems_stage.get_scored(ctx)
        names = [a.id for a in arms_in(scored)]
        primary = primary_arm(scored.arms)
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
            'Learning ("Accessibility in Mathematics", section "PDF to HTML / Using Gemini", '
            "https://www.purdue.edu/innovativelearning/tools-resources/accessibility/"
            "digital-instructional-materials-accessibility-checklist/digital-math/, page "
            "last modified 2026-07-28, read 2026-10-09), verbatim, and quoted as the input "
            "it is, not as a claim this project makes:",
            "<!-- quote -->\n> " + chatbot.PROMPTS["purdue"] + "\n<!-- /quote -->",
            send_text(scored) + " The HTML in the reply is "
            "scored as it comes: a reply that was cut off or empty is a failure of the "
            "section, not asked again. Equations are read from the reply's MathJax TeX or "
            "MathML, converted the way ReadableSTEM converts its own model's LaTeX, and aligned "
            "to the gold equations by the document task's code, so the same scorer reads "
            "both systems.",
            arms_text(ctx, scored),
        ]
        missing = [a.id for a in family(scored) if a.id not in scored.arms]
        if missing:
            intro.append(
                "**Not run in this recording:** "
                + ", ".join(f"`{m}`" for m in missing)
                + " (see the status table)."
            )
        blocks += intro
        if primary is not None:
            k = len(FAMILY_A)
            comparisons = [
                systems_tables.compare(
                    key,
                    label,
                    scored.a11y_vectors,
                    scored.vectors[primary],
                    a_name=A11Y,
                    other=primary,
                    k=k,
                    n_boot=n_boot,
                )
                for key, label in FAMILY_A
            ]
            blocks.append(
                systems_tables.claims_table(
                    "Family A: ReadableSTEM against the Purdue-prompt chatbot on "
                    + chatbot.ARM_BY_ID[primary].model,
                    comparisons,
                    a_name=A11Y,
                    k=k,
                    note="Paired cluster bootstrap by section: the same resampled sections "
                    'for both systems, B = 10,000, seed 0. "X is better" is written only '
                    "when the 99% interval (Bonferroni, k = 5) of the difference excludes 0; "
                    'otherwise "no difference this set can detect". With 15 sections '
                    "a percentile bootstrap is approximate and tends to be a little narrow. "
                    + pooled_note(ctx, scored, primary)
                    + "Every number here is a rate over "
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
                "wall clock, per PDF page of the section. ReadableSTEM: pipeline plus second "
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
