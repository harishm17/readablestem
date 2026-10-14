"""Stage: a11y-stem against the open PDF parsers (preregistration 6.3, PARSERS-PLAN.md).

The parsers are Docling (default settings; a second arm with formula recognition on was
registered and, if it is not in the bundle, was not carried out), MinerU (basic tier on CPU,
which is not the tier its documentation names as the default) and PyMuPDF4LLM. They were run by
`proof/parsers` (a separate uv project, one pinned environment per tool); their Markdown
exports are in the bundle's `systems/`, and a time and memory record of each run.

Family B of the claims (section 8.3): a11y-stem against each parser on
`end_to_end_exact_lenient` and heading F1, 2 x (number of parsers) comparisons, each judged
at the Bonferroni level for that k. Two of the parsers write no LaTeX by design, so their
equation number is 0 by construction: it says what the tool is built for, not how well it
does it, and the table says so beside it. Everything else is descriptive.
"""

from __future__ import annotations

from collections.abc import Sequence

from readablestem.proof import chatbot, comparison, stages, systems_stage, systems_tables
from readablestem.proof.bundle import BundleWriter
from readablestem.proof.report import NA, Section, StageResult, Table, num
from readablestem.proof.stages import RecordContext, StageContext
from readablestem.proof.systems_stage import A11Y, Scored

TITLE = "Comparison with open PDF parsers"
FAMILY_B = (("e2e", "end_to_end_exact_lenient"), ("h_f1", "heading F1"))
# Addendum 1, D3: the held-out family is judged for four parser arms (Docling, Docling with
# formula recognition, MinerU, PyMuPDF4LLM), so k = 8, although the arm with formula
# recognition was not carried out (Addendum 4). A development set uses the arms it holds.
REGISTERED_PARSER_ARMS = 4


def family_b_k(parsers_that_ran: int, held_out: bool) -> int:
    """Comparisons in family B: two per parser, and on a held-out set at least the registered
    four parsers' worth."""
    return len(FAMILY_B) * max(parsers_that_ran, REGISTERED_PARSER_ARMS if held_out else 0)


def _registered_k_note(ran: int) -> str:
    return f" ({ran} parsers ran; the registered four are counted, Addendum 1 D3)"


PARSER_NOTES = {
    "docling": "default settings: a formula is not decoded, so no LaTeX is written (its Markdown "
    "holds `<!-- formula-not-decoded -->`); the registered arm with formula recognition on was "
    "not carried out (Addendum 4)",
    "mineru-basic": "the basic tier on CPU; MinerU's documentation names the standard tier as "
    "its default, and that tier was not run, so this is not MinerU at its default settings",
    "pymupdf4llm": "has no formula recognition",
}

# Fixed text from PARSERS-PLAN.md (path in the research folder; not repeated here).
BUILT_FOR = [
    [
        "a11y-stem",
        "accessible HTML from STEM course PDFs, with a review screen",
        "re-typing prose (it keeps the PDF's text); tables in production (behind a flag); scans",
    ],
    [
        "Docling",
        "lossless document conversion for gen-AI pipelines (Markdown, JSON; HTML is one export)",
        "accessibility remediation: no alt text on pictures, one heading level in the export, "
        "formulas left undecoded unless the opt-in enrichment is on",
    ],
    [
        "MinerU",
        "extraction for LLM corpora and RAG (Markdown, JSON)",
        "accessible HTML: empty alt text on images, one heading level in its Markdown",
    ],
    [
        "PyMuPDF4LLM",
        "fast text-layer to Markdown for LLM and RAG ingestion",
        "mathematics (no formula recognition); images are off by default",
    ],
]
NOT_RUN = [
    ["Marker 2.0.0", "waits for a licence decision: its weights' licence counts evaluating as use "
     "and bars competing services, with no research exception"],
    ["Nougat 0.1.17", "on CPU the first of three development pages took 19 minutes; trained on "
     "arXiv papers; unmaintained since 2023"],
    ["olmOCR 0.4.27", "needs an NVIDIA GPU with at least 12 GB; the machine has no working driver"],
    ["Mathpix", "paid API; no key on the machine"],
]  # fmt: skip


def parser_names(scored: Scored) -> list[str]:
    return [n for n, a in scored.arms.items() if a.kind == "parser"]


def _transcribed(scored: Scored, name: str) -> tuple[int, int]:
    """(equations the tool wrote as TeX or MathML, equations it wrote at all)."""
    report = scored.arms[name]
    written = sum(s.counts.get("equations", 0) for s in report.scores)
    return written, written


def _versions(scored: Scored, name: str) -> str:
    for meta in scored.arms[name].metas:
        versions = meta.get("versions")
        if versions:
            keep = {k: v for k, v in versions.items() if k in {"docling", "mineru", "pymupdf4llm"}}
            return ", ".join(f"{k} {v}" for k, v in keep.items()) or NA
    return NA


class ParserStage:
    name = "parser_comparison"
    title = TITLE

    def record(self, ctx: RecordContext, writer: BundleWriter) -> None:
        ctx.say(
            "parsers: recorded by `a11y-stem proof --add-systems parsers`; nothing recorded here"
        )

    def run(self, ctx: StageContext) -> StageResult:
        scored = systems_stage.get_scored(ctx)
        names = parser_names(scored)
        if not names:
            return StageResult(
                "not_wired",
                [Section(TITLE, ["**Not computed**: this bundle holds no parser outputs."])],
                [f"{TITLE}: no parser outputs in the bundle"],
            )
        n_boot = ctx.n_boot
        facts = ctx.facts
        k = family_b_k(len(names), ctx.pset.held_out)
        comparisons = [
            systems_tables.compare(
                key,
                label,
                scored.a11y_vectors,
                scored.vectors[name],
                a_name=A11Y,
                other=name,
                k=k,
                n_boot=n_boot,
            )
            for name in names
            for key, label in FAMILY_B
        ]
        blocks: list = [
            "The parsers solve a different problem: getting a PDF's text into an LLM. They are "
            "here because they are what an engineer reaches for first, and because equations, "
            "headings and reading order are the hard part of both problems. The comparison "
            "is on the measures they share, and there is no overall score and no winner. "
            "Every parser runs on the same PDFs, byte for byte, with the tool's defaults on "
            "CPU, once per section, in a fresh process; a crash, an empty output or a "
            "30-minute limit scores the section as found nothing. Each parser's Markdown "
            "export is read by the same reader as the chatbot's HTML (`eval.invented.read`), "
            "its maths converted and aligned to gold by the same code as a11y-stem's.",
            Table(
                "What each system is built for (not failures: design differences)",
                ["System", "Built for", "Not built for"],
                BUILT_FOR,
            ),
            Table("Considered and not run", ["System", "Why"], NOT_RUN),
            Table(
                "Parsers that ran",
                ["Arm", "Versions", "Equations written as TeX/MathML", "Note"],
                [
                    [
                        n,
                        _versions(scored, n),
                        num(_transcribed(scored, n)[0]),
                        PARSER_NOTES.get(n, ""),
                    ]
                    for n in names
                ],
            ),
            systems_tables.claims_table(
                "Family B: a11y-stem against each parser",
                comparisons,
                a_name=A11Y,
                k=k,
                note=f"Preregistration 8.3 as written: 2 comparisons per parser, k = {k}"
                f"{_registered_k_note(len(names)) if k > len(FAMILY_B) * len(names) else ''}, the "
                f"{100 * (1 - 0.05 / k):.3f}% paired cluster-bootstrap interval of the "
                "difference decides. For a parser that writes no LaTeX by design "
                "(`docling`, `pymupdf4llm`) the equation row compares a11y-stem with a tool "
                "that does not attempt equations; read it as a description of what the tool "
                "is built for, not as evidence that a11y-stem transcribes better. Flat heading "
                "levels in a parser's export are a property of that export.",
            ),
        ]
        blocks += comparison.arm_block(ctx, scored, names, title_prefix="All parsers")
        cols = comparison._columns(scored, names)
        blocks.append(
            comparison.section_rows("End-to-end exact match by section", "e2e", facts, cols)
        )
        blocks.append(comparison.book_table("Heading F1 by book", "h_f1", facts, cols))
        blocks.append(
            systems_tables.cost_time_table(
                "Time and memory per PDF page",
                comparison.cost_totals(scored, names),
                note="Parsers: wall clock of one fresh process per section divided by its PDF "
                "pages, model load included, CPU only, API cost $0 (hosting not estimated); "
                "peak memory is the process tree's maximum. a11y-stem: seconds of the "
                "document task at concurrency 4 (it waits on a hosted model, so its time is "
                "not CPU time). Timings are from one machine; the load average at each "
                "start is in the recorded run, and a busy machine makes a time an upper bound.",
            )
        )
        blocks.append(
            systems_tables.failure_table(
                "Sections that did not convert", comparison.status_rows(scored, names, len(facts))
            )
        )
        blocks.append(_timing_note(scored, names))
        return StageResult("ok", [Section(TITLE, [b for b in blocks if b])])


def _timing_note(scored: Scored, names: Sequence[str]) -> str:
    quiet = busy = 0
    for n in names:
        for meta in scored.arms[n].metas:
            flag = (meta.get("measure") or {}).get("quiet_box")
            if flag is True:
                quiet += 1
            elif flag is False:
                busy += 1
    host = ""
    for n in names:
        for meta in scored.arms[n].metas:
            h = (meta.get("measure") or {}).get("host")
            if h:
                host = f"{h.get('cpu_model')}, {h.get('logical_cpus')} logical CPUs"
                break
        if host:
            break
    return (
        f"Parser timings: {quiet} runs started on a quiet machine (1-minute load under 1.5) "
        f"and {busy} did not. Host: {host or NA}."
    )


stages.register(ParserStage())
_ = chatbot
