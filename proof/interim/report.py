"""The part of the held-out run that needs no model: a11y-stem's headings and the parsers.

    uv run python proof/interim/report.py --gold <held-out gold folder> [--data DIR] [--out FILE]

Reads, from `--data` (default: `proof/interim/heldout-2026-10`):

    pdfs/<book>__<page>.pdf          the 15 section PDFs every system was given
    a11y-headings/headings-items.jsonl   `a11y-stem eval pipeline --task headings --set heldout`
    parsers/<arm>/<book>__<page>.{json,md}   `proof/parsers` (`parsers-bench proof`)

and writes one Markdown report: a11y-stem's heading detection (the headings task makes no model
call and runs once) next to each parser's, and the parsers' equations, headings, invented
content, coverage, time and memory against the held-out answer key. The answer key is fetched
with `a11y-stem gold fetch --set heldout --allow-retired --date 2026-10-13` and must be the
registered one (its manifest SHA-256 is checked here).

This is NOT the registered comparison: a11y-stem's equation, figure and whole-document runs and
the chatbot arms need paid model calls and do not exist. Nothing here compares a11y-stem's
equations with anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from a11y_stem.eval.invented import text as inv_text
from a11y_stem.proof import a11y_tables, goldinfo, systems, systems_tables
from a11y_stem.proof.bundle import pdf_name
from a11y_stem.proof.report import Section, Table, num, wording_problems
from a11y_stem.proof.systems import SystemRun

MANIFEST_SHA = "e9e7e429a5ce20ebc36c4c313ea46a8754d8b47cad6d359ec5dfb7f14d899ceb"
N_BOOT = 10_000
K = 8  # 2 comparisons x 4 parser arms (Addendum 1, D3)


def build(data: Path, gold_dir: Path) -> str:
    manifest = (gold_dir / "manifest.json").read_bytes()
    if hashlib.sha256(manifest).hexdigest() != MANIFEST_SHA:
        raise SystemExit("this is not the registered held-out answer key")
    records = goldinfo.load_records(gold_dir)
    order = sorted({f"{r['book_slug']}/{r['page_slug']}" for r in records})
    golds = systems.section_golds(records, order)
    g1 = goldinfo.g1_ids(records)
    pdf_dir = data / "pdfs"

    # a11y-stem: the headings task items
    builder = systems_tables.VectorBuilder()
    for line in (data / "a11y-headings" / "headings-items.jsonl").read_text().splitlines():
        item = json.loads(line)
        counts = a11y_tables.heading_counts(item)
        for name, key in (
            ("h_f1", "f1"),
            ("h_precision", "precision"),
            ("h_recall", "recall"),
            ("h_level", "level"),
        ):
            builder.add(name, item["id"], counts[key])
    a11y_v = builder.done()
    if set(a11y_v["h_f1"]) != set(order):
        raise SystemExit("the headings items must cover the 15 sections")

    references = {
        s: inv_text.Reference.from_pdf((pdf_dir / pdf_name(s)).read_bytes()) for s in order
    }
    gold_mathml = {s: [g["mathml"] for g in golds[s].equations] for s in order}
    arms = systems.load_arms_dir(data / "parsers")
    vectors: dict[str, systems_tables.Vectors] = {}
    totals: dict[str, systems_tables.ArmTotals] = {}
    statuses: dict[str, dict[str, int]] = {}
    written: dict[str, int] = {}
    for arm, sections in sorted(arms.items()):
        scores = []
        total = systems_tables.ArmTotals()
        statuses[arm] = {}
        written[arm] = 0
        for section in order:
            run = (
                sections.get(section) or [SystemRun(arm, section, 1, {"status": "not_run"}, None)]
            )[0]
            score = systems.score_section(
                run,
                golds[section],
                (pdf_dir / pdf_name(section)).read_bytes(),
                gold_mathml[section],
                reference=references[section],
            )
            scores.append(score)
            statuses[arm][run.status] = statuses[arm].get(run.status, 0) + 1
            written[arm] += score.counts.get("equations", 0)
            pages = run.pdf_pages or score.pdf_pages
            if run.meta.get("seconds") is not None and pages:
                total.seconds_per_page.append(float(run.meta["seconds"]) / pages)
            rss = (run.meta.get("measure") or {}).get("peak_rss_mb")
            if rss:
                total.peak_rss_mb.append(float(rss))
        vectors[arm] = systems_tables.system_vectors(scores, g1)
        totals[arm] = total

    parser_cols = list(vectors.items())
    both = [("a11y-stem (headings task)", a11y_v), *parser_cols]
    heading_keys = ["h_f1", "h_precision", "h_recall", "h_level"]
    claims = [
        systems_tables.compare(
            "h_f1", "heading F1", a11y_v, v, a_name="a11y-stem", other=arm, k=K, n_boot=N_BOOT
        )
        for arm, v in vectors.items()
    ]
    eq = ["e2e", "e2e_clean", "unflagged", "matched_exact", "recall", "precision", "e2e_free"]
    inv = ["inv_sentence", "inv_number", "inv_heading", "inv_eq_aligned", "inv_eq_unaligned"]
    intro: list[str] = [
        "**What this is.** The two parts of the held-out run that need no model call: "
        "a11y-stem's heading detection (the headings task, run once) and the open parsers "
        "(CPU, defaults, one process per section, 30-minute limit). a11y-stem's equation, "
        "figure and whole-document runs and the chatbot arms need paid model calls, which "
        "could not be made (the API account had no credit), so **they do not exist and "
        "nothing about them is claimed here**. No number below compares a11y-stem's equation "
        "transcription with anything. Held-out answer key: registered, manifest "
        f"`{MANIFEST_SHA}`; 15 sections, 242 Letter pages, 509 gold headings, 1,369 gold "
        "equations. Inputs are OpenStax retired first editions (CC BY 4.0), attribution in "
        "each PDF.",
        "**Reading the numbers.** `value [cluster 95% interval by section]`: cluster "
        "bootstrap by section, B = 10,000, seed 0; with 15 sections it is approximate and "
        "tends to be a little narrow. Parser timings were taken on a shared machine whose "
        "load average was above 1.5 for most runs (the harness was told not to wait for a "
        "quiet one): they are upper bounds. Docling in default settings and PyMuPDF4LLM "
        "write no LaTeX by design, so their equation numbers are 0 by construction: that "
        "says what the tools are built for, not how well they read maths. A perfect "
        "transcription is not scored 1.0 by this scorer (a hand-written correct sample of 40 "
        "gold equations scored 28 of 40; `proof/scorer-readiness/`).",
    ]
    sections = [
        Section(
            "Headings: a11y-stem against the parsers",
            [
                systems_tables.metrics_table(
                    "Headings (preregistration 7.2)", heading_keys, both, N_BOOT
                ),
                systems_tables.counts_table("Counts", heading_keys, both),
                systems_tables.claims_table(
                    "Heading F1: a11y-stem against each parser",
                    claims,
                    a_name="a11y-stem",
                    k=K,
                    note="Paired cluster bootstrap by section. k = 8: the registration's rule "
                    "of 2 comparisons per parser arm with four arms (Addendum 1, D3), "
                    'although only the heading half can be made. "X is better" is written '
                    "only when the Bonferroni interval excludes 0. Heading level is compared "
                    "relative to the section's top level; Docling and MinerU write every "
                    "heading at one level in their Markdown export, which is a property of "
                    "that export.",
                ),
            ],
            level=3,
        ),
        Section(
            "Parsers on equations, invented content, coverage",
            [
                systems_tables.metrics_table("Equations (7.1)", eq, parser_cols, N_BOOT),
                systems_tables.metrics_table("Invented content (7.5)", inv, parser_cols, N_BOOT),
                systems_tables.metrics_table("Coverage (7.6)", ["coverage"], parser_cols, N_BOOT),
                systems_tables.metrics_table(
                    "Figures (7.3): a parser writes no description, so every figure fails",
                    ["fig_pass", "fig_nodesc"],
                    parser_cols,
                    N_BOOT,
                ),
                Table(
                    "Equations the tool wrote as TeX or MathML (the answer key has 1,369)",
                    ["Parser", "Written"],
                    [[a, num(written[a])] for a in vectors],
                ),
                systems_tables.counts_table(
                    "Counts behind the rates", [*eq, *inv, "coverage"], parser_cols
                ),
                systems_tables.wilson_table(
                    "Wilson 95% (items treated as independent)", [*eq, *inv], parser_cols
                ),
                systems_tables.failure_table(
                    "Sections that did not convert",
                    {a: (statuses[a], len(order)) for a in vectors},
                ),
                systems_tables.cost_time_table(
                    "Time and memory per PDF page (CPU, shared machine, upper bounds)", totals
                ),
            ],
            level=3,
        ),
    ]
    text = "\n\n".join(
        [
            "# Held-out results that need no model call (interim)",
            *intro,
            *[section.markdown() for section in sections],
        ]
    )
    problems = wording_problems(text)
    if problems:
        raise SystemExit("forbidden wording in the report: " + "; ".join(problems))
    return text + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=here / "heldout-2026-10")
    parser.add_argument("--out", type=Path, default=here / "INTERIM-heldout-2026-10.md")
    args = parser.parse_args()
    args.out.write_text(build(args.data, args.gold), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
