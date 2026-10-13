"""Assembles PROOF.md from the pieces a proof run produced.

The text is deterministic: it holds only what the bundle, the registry and the stages
give it, never the time of the run, the host, a path outside the repository or a key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from a11y_stem.proof.bundle import Bundle
from a11y_stem.proof.errors import ProofError
from a11y_stem.proof.goldinfo import SectionFacts
from a11y_stem.proof.registry import PinCheck, ProofSet
from a11y_stem.proof.report import (
    NA,
    STATUS_WORDS,
    Section,
    StageResult,
    Table,
    bullets,
    num,
    usd,
    wording_problems,
)

DISCLAIMER = (
    "a11y-stem assists a person who makes course material accessible. Nothing here measures "
    "conformance with WCAG or any other standard, and no number below may be read as a claim "
    "that a document is accessible. Automated HTML checks are reported as what a machine can "
    "detect. Judge numbers are agreement with a model judge, not accuracy."
)


@dataclass
class DocumentInfo:
    pset: ProofSet
    mode: str  # "replay" | "live"
    bundle: Bundle
    pins: list[PinCheck]
    facts: list[SectionFacts]
    books: list[dict[str, Any]]  # from the gold manifest: title, authors, licence, version
    identity_diffs: list[str] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)
    compared_runs: int = 0
    replay_stats: list[dict[str, Any]] = field(default_factory=list)
    source_checks: list[dict[str, Any]] = field(default_factory=list)  # reprint check rows
    fetched_gold: str | None = None  # "matches the pin" / "does not match", when fetched
    stages: list[tuple[str, StageResult]] = field(default_factory=list)  # (title, result)
    fake: bool = False
    commands: list[str] = field(default_factory=list)


def _banner(info: DocumentInfo) -> list[str]:
    lines = []
    if info.fake:
        lines.append(
            "> **PLUMBING CHECK ONLY.** The model answers in this run were produced by the "
            "offline fake client. The numbers below test the harness, not a11y-stem."
        )
    if not info.pset.held_out:
        lines.append(
            "> **Development set, not held out.** These sections were available while "
            "a11y-stem was built. The numbers show that the pipeline and this harness work "
            "end to end; they are not held-out results."
        )
    if info.drift or info.identity_diffs:
        lines.append(
            "> **DRIFT.** Replaying the recorded answers with this checkout does not "
            "reproduce the recorded run exactly (see Status). The numbers below belong to "
            "this replay, not to the recorded run."
        )
    incomplete = info.bundle.manifest.get("incomplete")
    if incomplete:
        lines.append(
            f"> **INCOMPLETE RUN.** The spend cap stopped the recording after "
            f"{incomplete['runs_made']} of {incomplete['runs_planned']} planned runs: "
            f"{incomplete['reason']}. The runs it did not make are missing from every table "
            "that would have used them."
        )
    unwired = [t for t, r in info.stages if r.status == "not_wired"]
    if unwired:
        lines.append("> **Incomplete.** Not computed in this build: " + "; ".join(unwired) + ".")
    return lines


def _status(info: DocumentInfo) -> Section:
    b, code = info.bundle, info.bundle.code
    git = code.get("git", {})
    models = code.get("settings", {})
    prompts = code.get("prompts", {})
    mode_text = (
        "replay of a recorded live run: no model call, no key, no cost"
        if info.mode == "replay"
        else "live run that was recorded and then replayed to write this file"
    )
    rows = [
        [
            "Proof set",
            f"`{info.pset.id}`" + (" (held out)" if info.pset.held_out else " (development)"),
        ],
        ["Mode", mode_text],
        ["Run recorded on", b.recorded_on or NA],
        [
            "Code",
            f"commit `{str(git.get('commit', 'unknown'))[:12]}`"
            + (" (uncommitted changes)" if git.get("dirty") else ""),
        ],
        ["Pipeline version", str(code.get("pipeline_version", NA))],
        [
            "Models",
            f"main {models.get('main_model', NA)}, cheap {models.get('cheap_model', NA)}, "
            f"second check {models.get('decisions_model', NA)}",
        ],
        ["Prompt versions", ", ".join(f"{k} {v}" for k, v in sorted(prompts.items())) or NA],
        ["Recorded spend", usd(b.manifest.get("spend", {}).get("total_usd"), 4)],
        ["Runs", ", ".join(f"{task} x{n}" for task, n in info.pset.repeats.items())],
    ]
    if b.manifest.get("resumed_runs"):
        rows.append(
            [
                "Recording",
                "interrupted once and resumed: "
                + ", ".join(b.manifest["resumed_runs"])
                + " finished before the interruption and were read back",
            ]
        )
    blocks: list[str | Table] = [Table("", ["", ""], rows)]
    pin_rows = [[c.name, c.status, c.detail] for c in info.pins]
    blocks.append(Table("Pins checked before the run", ["Pin", "Result", "Detail"], pin_rows))
    if info.fetched_gold:
        blocks.append(f"Gold set fetched again from OpenStax: {info.fetched_gold}.")
    if info.mode == "replay" or info.compared_runs:
        if info.drift:
            blocks.append(
                "Replay compared with the recorded reports: **differs** in "
                f"{len(info.drift)} place(s). First differences:\n\n"
                + bullets([f"`{d}`" for d in info.drift[:8]])
            )
        else:
            blocks.append(
                f"Replay compared with the recorded reports: **identical** "
                f"({info.compared_runs} report(s): every metric and cost, timing excluded)."
            )
    if info.identity_diffs:
        blocks.append(
            "This checkout differs from the code that made the recording:\n\n"
            + bullets(info.identity_diffs)
        )
    if info.replay_stats:
        total = sum(s["requests"] for s in info.replay_stats)
        verify = sum(s["verify_requests"] for s in info.replay_stats)
        misses = sum(s["misses"] for s in info.replay_stats)
        blocks.append(
            f"Replayed {total:,} model requests and {verify:,} second-check requests from the "
            f"bundle; {misses} had no recorded answer."
        )
    if info.source_checks:
        rows = [
            [
                r["section"],
                f"{r['pages_recorded']} / {r['pages_now']}",
                "yes" if r["same_text"] else "no",
                "yes" if r["same_bytes"] else "no",
            ]
            for r in info.source_checks
        ]
        blocks.append(
            Table(
                "PDFs printed again from OpenStax, against the recorded ones",
                ["Section", "Pages (recorded / now)", "Same text layer", "Same bytes"],
                rows,
                note="Scoring always uses the recorded PDFs. Bytes differ between prints "
                "(Chromium stamps a creation date; fonts differ by machine); page counts "
                "should not.",
            )
        )
    if info.stages:
        blocks.append(
            Table(
                "Parts of this proof",
                ["Part", "Status"],
                [
                    [title, STATUS_WORDS.get(result.status, result.status)]
                    for title, result in info.stages
                ],
            )
        )
    return Section("Status", blocks)


def _sections_table(info: DocumentInfo) -> Section:
    pdf_by_section = {e["section"]: e for e in info.bundle.pdfs}
    rows = []
    total = [0, 0, 0, 0, 0, 0]
    for f in info.facts:
        pdf = pdf_by_section.get(f.id, {})
        rows.append(
            [
                f.id,
                f.page_title,
                num(pdf.get("pages")) if pdf else NA,
                f"{f.equations} ({f.block} / {f.inline})",
                str(f.g1),
                f"{f.figures} ({f.figures_with_alt})",
                str(f.tables),
                f"{f.headings} ({f.headings_in_boxes})",
                f"`{str(pdf.get('sha256', ''))[:12]}`" if pdf else NA,
            ]
        )
        for i, v in enumerate(
            [pdf.get("pages") or 0, f.equations, f.g1, f.figures, f.tables, f.headings]
        ):
            total[i] += v
    rows.append(
        [
            "**Total**",
            "",
            f"**{total[0]}**",
            f"**{total[1]}**",
            f"**{total[2]}**",
            f"**{total[3]}**",
            f"**{total[4]}**",
            f"**{total[5]}**",
            "",
        ]
    )
    table = Table(
        "",
        [
            "Section",
            "Title",
            "PDF pages",
            "Equations (block / inline)",
            "G1",
            "Figures (with OpenStax alt)",
            "Tables",
            "Headings (in boxes)",
            "PDF sha256",
        ],
        rows,
        note="The PDF is the one every system was given. G1: equations whose gold MathML "
        "packs operators into text, which exact match can fail even when the answer is "
        "right.",
    )
    blocks: list[str | Table] = []
    if info.pset.description:
        blocks.append(info.pset.description)
    blocks.append(table)
    if info.pset.notes:
        blocks.append(bullets(list(info.pset.notes)))
    return Section(f"The {len(info.facts)} sections", blocks)


def _limits(info: DocumentInfo) -> Section:
    generic = [
        "Exact match of MathML is strict: a correct answer can fail on markup (see G1), and a "
        "wrong digit in a long equation costs little tree-edit similarity. It is not a "
        "measure of how a screen reader would speak the result.",
        "With this few sections the bootstrap intervals are approximate and tend to be a "
        "little narrow. Per-book numbers are point estimates.",
        "The figure judge is a model and is not calibrated against human labels.",
        "A replay shows that the numbers follow from the recorded answers and this code. It "
        "cannot show that a new live run would give the same answers: model output varies "
        "from run to run, which is why tasks are repeated and each run's value is shown.",
    ]
    return Section("What these numbers leave out", [bullets([*info.pset.limits, *generic])])


def _reproduce(info: DocumentInfo) -> Section:
    sid = info.pset.id
    return Section(
        "Reproduce",
        [
            "```\n"
            "uv sync --locked                      # Python 3.12+, uv; no key\n"
            f"make proof SET={sid}      # replay: writes this file, no network needed\n"
            f"make proof-sources SET={sid}   # also fetch the answer key from OpenStax and "
            "print the PDFs again (network, Chromium)\n"
            f"make proof-live SET={sid} MAX_USD=N   # new live run with OPENAI_API_KEY, capped\n"
            "```\n"
            "A replay writes byte-identical output on any machine with the same dependency "
            "versions. A live run calls the models again, so its numbers differ from the "
            "recorded ones by the usual run-to-run variation.",
        ],
    )


def _attribution(info: DocumentInfo) -> Section:
    lines = []
    for book in info.books:
        authors = ", ".join(book.get("authors", [])) or "OpenStax"
        lines.append(
            f"{book.get('title')} by {authors}, OpenStax, version {book.get('version')}, "
            "licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); "
            f"original at https://openstax.org/details/books/{book.get('slug')}."
        )
    lines.append(
        "Changes: each section was reformatted as a printable page for evaluating a11y-stem, "
        "image alt text was left out of the print, and no wording was edited. a11y-stem is "
        "not affiliated with or endorsed by OpenStax."
    )
    return Section("Attribution", [bullets(lines)])


def render(info: DocumentInfo) -> str:
    """The text of PROOF.md. Raises `ProofError` if it breaks the wording rules."""
    title = f"# Proof: {info.pset.title}"
    parts = [title, DISCLAIMER, *_banner(info), _status(info).markdown()]
    parts.append(_sections_table(info).markdown())
    for _title, result in info.stages:
        for section in result.sections:
            parts.append(section.markdown())
    parts.append(_limits(info).markdown())
    parts.append(_reproduce(info).markdown())
    parts.append(_attribution(info).markdown())
    text = "\n\n".join(parts).rstrip() + "\n"
    problems = wording_problems(text)
    if problems:
        raise ProofError("the report breaks the wording rules: " + "; ".join(problems[:5]))
    return text
