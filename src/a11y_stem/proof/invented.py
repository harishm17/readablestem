"""Stage: the invented-content rate of every system (preregistration section 7.5).

The rules are `eval.invented` (frozen: its code fingerprint is printed below and recorded
in the preregistration addendum before the run). a11y-stem's output is the HTML its own
pipeline writes, rebuilt by the replay; the chatbot's and the parsers' outputs are the
files in the bundle's `systems/`. Every output is scored against the text layer of the PDF
it was made from.
"""

from __future__ import annotations

from a11y_stem.eval.invented import pool as inv_pool
from a11y_stem.eval.invented.report import code_fingerprint
from a11y_stem.proof import stages, systems_stage, systems_tables
from a11y_stem.proof.bundle import BundleWriter
from a11y_stem.proof.report import NA, Section, StageResult, Table, bullets, fraction, num
from a11y_stem.proof.stages import RecordContext, StageContext

TITLE = "Invented-content rate"


class InventedStage:
    name = "invented_content"
    title = TITLE

    def record(self, ctx: RecordContext, writer: BundleWriter) -> None:
        return None  # computed from outputs the other stages record

    def run(self, ctx: StageContext) -> StageResult:
        scored = systems_stage.get_scored(ctx)
        if scored.a11y_entries is None:
            return StageResult(
                "skipped",
                [Section(TITLE, ["Not computed: this replay produced no HTML for a11y-stem."])],
                [f"{TITLE}: no a11y-stem HTML in this replay"],
            )
        columns: list[tuple[str, list]] = [(systems_stage.A11Y, scored.a11y_entries)]
        for name in scored.arms:
            columns.append((name, scored.entries[name]))
        all_text = systems_tables.invented_pool_table(
            "Unsupported units / units, all text (the rate to quote)", columns
        )
        content = systems_tables.invented_pool_table(
            "The same without the text a11y-stem wrote itself",
            [(systems_stage.A11Y, scored.a11y_entries)],
            content_only=True,
        )
        symbols = []
        for name, entries in columns:
            s = inv_pool.equation_symbols(entries)
            symbols.append(
                [
                    name,
                    fraction(s["added"], s["aligned_output_symbols"]),
                    num(s["per_1000"], 2),
                    num(s["per_1000_without_tags"], 2),
                    num(s["aligned_pairs"]),
                ]
            )
        added = Table(
            "Symbols added to aligned equations",
            [
                "System",
                "Added / output symbols",
                "Per 1,000",
                "Per 1,000, less a kept equation number",
                "Aligned equations",
            ],
            symbols,
        )
        unchecked = []
        for name, entries in columns:
            p = inv_pool.summarize(entries)
            unchecked.append(
                [
                    name,
                    num(p["unchecked"]["equation_unaligned"]),
                    num(p["too_short"]["equation_unaligned"]),
                ]
            )
        unchecked_table = Table(
            "Equations the rule could not check",
            ["System", "TeX that did not convert", "No gold partner and under 3 symbols"],
            unchecked,
            note="These are counted, never scored as correct.",
        )
        vec = {
            name: scored.vectors.get(name, {}) for name, _ in columns if name != systems_stage.A11Y
        }
        figure_rows = []
        for name in [systems_stage.A11Y, *vec]:
            v = scored.a11y_vectors if name == systems_stage.A11Y else vec[name]
            spec = systems_tables.METRICS["fig_factual"]
            figure_rows.append(
                [name, systems_tables.count_cell(spec, v), systems_tables.cell(spec, v, ctx.n_boot)]
            )
        figures = Table(
            "Figure descriptions: judged as stating something the image contradicts",
            ["System", "Judge: factual error / descriptions judged", "Rate [cluster 95%]"],
            figure_rows,
            note="Agreement with a model judge reading the image (gpt-6.1-sol), not accuracy; "
            "the judge is not calibrated against human labels in this run. A figure with no "
            "description is not judged here (it is a fail in the figure tables).",
        )
        fingerprint = code_fingerprint()["sha256"]
        text = [
            "The invented-content rate of a type of output unit is the share of the system's "
            "units of that type that the source PDF does not support (preregistration 7.5). "
            "The reference is the PDF's own text layer, footer and attribution included.",
            f"Rules: `a11y_stem.eval.invented`, code fingerprint `{fingerprint}`. Units: prose "
            "sentences of 7 or more words (unsupported when under 25% of their word "
            "4-grams are in the PDF); numbers (not among the PDF's numbers); headings (no "
            "line of the PDF within difflib ratio 0.85); equations aligned to a gold "
            "equation (hold a letter, digit or Greek letter the gold equation does not); "
            "equations with no gold partner and 3 or more symbols (no matching run of the "
            "PDF's symbols, ratio 0.8); table cells.",
            "**Primary rows** are prose sentences and numbers. a11y-stem copies prose from "
            "the PDF's text layer, so for it those rows measure the copying, plus the "
            'sentences the tool writes itself (the "About this version" section and '
            "notices), which are counted in the first table and left out in the second. The "
            "types where a11y-stem can invent are equations and figure descriptions.",
            "Intervals: cluster bootstrap by section (the number to quote), shown as "
            "`rate (unsupported/units) [95% interval]`. With few sections that have units "
            "of a type the interval is not given.",
            "**What the rule cannot see.** A changed word inside a sentence that keeps a "
            "quarter of its 4-grams passes the sentence rule (a changed number does not "
            "pass the number rule); a faithful paraphrase or a reordering counts as "
            "unsupported (a false positive); the PDF holds what is printed, so a correct "
            'description of a figure is never "supported" by it, which is why figures use '
            "the judge. **The audit of the rule's precision and recall (blind labels by the "
            "author, section 7.5) has not been done**: until it is, the raw rates are the "
            "only rates, and a rate for a system that re-types the page includes "
            "paraphrase the rule cannot tell from invention.",
        ]
        sections = [
            Section(
                TITLE,
                [
                    *text,
                    all_text,
                    content,
                    added,
                    unchecked_table,
                    figures,
                    "Units counted per system and section, with the text of every flagged "
                    "unit, are not part of this file; `a11y-stem eval invented score "
                    "--units` writes them for one output.",
                ],
            )
        ]
        return StageResult("ok", sections)


stages.register(InventedStage())
_ = (bullets, NA)
