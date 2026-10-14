"""The part of the held-out run that needs no model: ReadableSTEM's headings and the parsers.

    uv run python proof/interim/report.py --gold <held-out gold folder> [--data DIR] [--out FILE]

Reads, from `--data` (default: `proof/interim/heldout-2026-10`):

    pdfs/<book>__<page>.pdf          the 15 section PDFs every system was given
    a11y-headings/headings-items.jsonl   `readablestem eval pipeline --task headings --set heldout`
    a11y-headings/headings.json          the same task's report (review load and flags)
    parsers/<arm>/<book>__<page>.{json,md}   `proof/parsers` (`parsers-bench proof`)

and writes one Markdown report: ReadableSTEM's heading detection (the headings task makes no model
call and runs once) next to each parser's, ReadableSTEM's review load and heading flags, and the
parsers' equations, headings, invented content, coverage, time and memory against the held-out
answer key. The answer key is fetched with `make heldout-gold` (network) and must be the
registered one (its manifest SHA-256 is checked here).

The registered rows are computed as registered. The rows marked "descriptive" were added
after the results were known, to show how far a result depends on how it is read; none of them
changes a registered row, and none was used to change any code that produced an output.

This is NOT the registered comparison: ReadableSTEM's equation, figure and whole-document runs and
the chatbot arms need paid model calls and do not exist. Nothing here compares ReadableSTEM's
equations with anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
# The parsers' own harness (a separate project) holds the markup rewrites of PARSERS-PLAN 4.4b.
sys.path.insert(0, str(HERE.parent / "parsers" / "src"))

from parsers_bench import equations as parser_equations  # noqa: E402

from readablestem.eval.invented import read as inv_read  # noqa: E402
from readablestem.eval.invented import score as inv_score  # noqa: E402
from readablestem.eval.invented import text as inv_text  # noqa: E402
from readablestem.proof import a11y_tables, goldinfo, stats, systems, systems_tables  # noqa: E402
from readablestem.proof.bundle import pdf_name  # noqa: E402
from readablestem.proof.report import Section, Table, num, wording_problems  # noqa: E402
from readablestem.proof.systems import SystemRun  # noqa: E402

MANIFEST_SHA = "9be8c5650fe492d0c5312199b3040aa6ea741595d5ba97df4b68e816455c9aa3"
N_BOOT = 10_000
K = 8  # 2 comparisons x 4 parser arms (Addendum 1, D3)
NEED_GOLD = (
    "the held-out answer key is not in {path}: run `make heldout-gold` first (it fetches the "
    "answer key from OpenStax and needs the network), then `make proof-interim`"
)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_BARE_NUMBER = re.compile(r"^\W*\d+[.)]?\W*$")
_MATH_SIGNS = re.compile(
    r"[=+\u2212\-\u00d7\u00b7\u00f7/^()\[\]<>\u2264\u2265\u2248\u00b1\u221e\u221a\u03b1-\u03c9]|\d\s\d"
)


# ---------------------------------------------------------------------------- helpers


def _fmt(items: Sequence[tuple[int, int]]) -> list[str]:
    return [f"{num(a / b if b else None)} ({a:,}/{b:,})" for a, b in items]


def heading_recall_split(items: Sequence[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    """Gold headings found / gold headings, for free-standing ones (no enclosing OpenStax box)
    and for the ones inside a box (example, solution, note, exercise and so on)."""
    found = {"free": 0, "boxed": 0}
    total = {"free": 0, "boxed": 0}
    for item in items:
        for pair in item["pairs"]:
            kind = "boxed" if pair.get("gold_context") else "free"
            found[kind] += 1
            total[kind] += 1
        for missed in item["missed"]:
            total["boxed" if missed.get("context") else "free"] += 1
    return {k: (found[k], total[k]) for k in found}


def bare_number_extras(items: Sequence[dict[str, Any]]) -> tuple[int, int]:
    """(headings the tool wrote that match no gold heading, of them how many are only a
    number such as an exercise number)."""
    extras = [e for item in items for e in item["extra"]]
    return len(extras), sum(1 for e in extras if _BARE_NUMBER.match(e["text"]))


def _f1(matched: int, predicted: int, gold: int) -> float | None:
    return stats.f1_of_counts(np.asarray([matched, predicted, gold], float))


def _tex_normalised(equation: inv_read.Equation) -> str | None:
    """MathML for a TeX equation after the harness's markup-only rewrites (descriptive row)."""
    if equation.source != "tex":
        return equation.mathml
    return inv_read.tex_to_mathml(
        parser_equations.normalise_plus(equation.raw), equation.display, {}
    )


def _flagged_numbers(score: inv_score.Score) -> list[inv_score.Verdict]:
    return [v for v in score.verdicts if v.kind == "number" and v.unsupported]


# ------------------------------------------------------------------------------ build


def build(data: Path, gold_dir: Path) -> str:
    manifest_path = gold_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(NEED_GOLD.format(path=gold_dir))
    manifest = manifest_path.read_bytes()
    if hashlib.sha256(manifest).hexdigest() != MANIFEST_SHA:
        raise SystemExit("this is not the registered held-out answer key")
    records = goldinfo.load_records(gold_dir)
    order = sorted({f"{r['book_slug']}/{r['page_slug']}" for r in records})
    golds = systems.section_golds(records, order)
    g1 = goldinfo.g1_ids(records)
    pdf_dir = data / "pdfs"

    # ReadableSTEM: the headings task items
    builder = systems_tables.VectorBuilder()
    a11y_items: list[dict[str, Any]] = []
    for line in (data / "a11y-headings" / "headings-items.jsonl").read_text().splitlines():
        item = json.loads(line)
        a11y_items.append(item)
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
    headings_report = json.loads((data / "a11y-headings" / "headings.json").read_text())

    references = {
        s: inv_text.Reference.from_pdf((pdf_dir / pdf_name(s)).read_bytes()) for s in order
    }
    gold_mathml = {s: [g["mathml"] for g in golds[s].equations] for s in order}
    arms = systems.load_arms_dir(data / "parsers")
    vectors: dict[str, systems_tables.Vectors] = {}
    totals: dict[str, systems_tables.ArmTotals] = {}
    statuses: dict[str, dict[str, int]] = {}
    written: dict[str, int] = {}
    heading_items: dict[str, list[dict[str, Any]]] = {}
    flagged_rows: list[list[str]] = []
    for arm, sections in sorted(arms.items()):
        scores = []
        extra = systems_tables.VectorBuilder()
        total = systems_tables.ArmTotals()
        statuses[arm] = {}
        written[arm] = 0
        flagged = {"all": 0, "negative": 0, "math": 0}
        for section in order:
            run = (
                sections.get(section) or [SystemRun(arm, section, 1, {"status": "not_run"}, None)]
            )[0]
            pdf = (pdf_dir / pdf_name(section)).read_bytes()
            output = run.read()
            score = systems.score_section(
                run,
                golds[section],
                pdf,
                gold_mathml[section],
                reference=references[section],
                output=output,
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
            # descriptive row: the equations after the harness's markup-only rewrites
            norm_item = systems.equation_item(
                section, golds[section].equations, output, mathml_of=_tex_normalised
            )
            extra.add("e2e_norm", section, a11y_tables.document_counts(g1)(norm_item)["e2e"])
            # descriptive rows: the invented-content rule with HTML comments removed
            if output is not None and run.output_path is not None:
                source = run.output_path.read_text("utf-8", errors="replace")
                stripped = inv_read.read_output(_HTML_COMMENT.sub("", source), "md")
                again = inv_score.score_output(
                    references[section], stripped, gold_equations=list(gold_mathml[section])
                )
                counts = again.counts["sentence"]
                extra.add("inv_sentence_nocomment", section, (counts.unsupported, counts.n))
                full = inv_score.score_output(
                    references[section], output, gold_equations=list(gold_mathml[section])
                )
                for verdict in _flagged_numbers(full):
                    flagged["all"] += 1
                    flagged["negative"] += verdict.text.startswith(("-", "−"))
                    flagged["math"] += bool(
                        _MATH_SIGNS.search(verdict.text)
                        or _MATH_SIGNS.search(str(verdict.detail.get("context", "")))
                    )
            else:
                extra.add("inv_sentence_nocomment", section, (0, 0))
        vectors[arm] = {**systems_tables.system_vectors(scores, g1), **extra.done()}
        totals[arm] = total
        heading_items[arm] = [s.heading for s in scores]
        flagged_rows.append([arm, *[num(flagged[k]) for k in ("all", "negative", "math")]])

    parser_cols = list(vectors.items())
    both = [("ReadableSTEM (headings task)", a11y_v), *parser_cols]
    heading_keys = ["h_f1", "h_precision", "h_recall", "h_level"]
    claims = [
        systems_tables.compare(
            "h_f1", "heading F1", a11y_v, v, a_name="ReadableSTEM", other=arm, k=K, n_boot=N_BOOT
        )
        for arm, v in vectors.items()
    ]
    eq = ["e2e", "e2e_clean", "unflagged", "matched_exact", "recall", "precision", "e2e_free"]
    inv = ["inv_sentence", "inv_number", "inv_heading", "inv_eq_aligned", "inv_eq_unaligned"]
    intro: list[str] = [
        "**What this is.** The two parts of the held-out run that need no model call: "
        "ReadableSTEM's heading detection (the headings task, run once) and the open parsers "
        "(CPU, one process per section, 30-minute limit). ReadableSTEM's equation, "
        "figure and whole-document runs and the chatbot arms need paid model calls, which "
        "could not be made (the API account had no credit), so **they do not exist and "
        "nothing about them is claimed here**. No number below compares ReadableSTEM's equation "
        "transcription with anything. Held-out answer key: registered, manifest "
        f"`{MANIFEST_SHA}`; 15 sections, 242 Letter pages, 509 gold headings, 1,369 gold "
        "equations. Inputs are OpenStax retired first editions (CC BY 4.0), attribution in "
        "each PDF; figures inside them keep the credits and licences OpenStax gives them.",
        "**The PDFs are printed by this project's own stylesheet.** Every system was given "
        "the same PDF bytes, but they were not the publisher's PDFs: the sections were "
        "printed from OpenStax's HTML by `readablestem.eval.render` (its `PRINT_CSS`, Liberation "
        "Sans, bordered boxes and bold labels for examples, solutions and notes). That "
        "stylesheet was written for ReadableSTEM's own tests, and the headings rules were "
        "developed on pages it printed. A result that depends on how a box is drawn may not "
        "carry over to a publisher's PDF; see the heading tables below.",
        "**Reading the numbers.** `value [cluster 95% interval by section]`: cluster "
        "bootstrap by section, B = 10,000, seed 0; with 15 sections it is approximate and "
        "tends to be a little narrow. Parser timings were taken on a shared machine whose "
        "load average was above 1.5 for most runs (the harness was told not to wait for a "
        "quiet one): they are upper bounds. A perfect transcription is not scored 1.0 by "
        "this scorer (a hand-written correct sample of 40 gold equations scored 28 of 40; "
        "`proof/scorer-readiness/`), and the scorer also charges a tool for how it writes "
        "LaTeX; the descriptive row below shows how much.",
        "**Which settings.** Docling ran in its default settings, where a formula is not "
        "decoded and no LaTeX is written. The registration also planned a Docling arm with "
        "formula recognition switched on (Addendum 1, D2: 30 minutes per section, a section "
        "that does not finish scores as found nothing). It was started, stopped by hand "
        "after one section (11 pages, 1,495 s) and **not carried out** (Addendum 4), so "
        "there is no Docling formula number and Docling's 0.0000 on equations says only "
        "that its defaults write none. MinerU ran at its `basic` tier on CPU. Its own "
        "documentation names `standard` as the default tier (also CPU-capable), so "
        "**mineru-basic is not MinerU at its default settings.** Any number for MinerU here "
        "is for the basic tier. PyMuPDF4LLM ran with its layout model and defaults.",
        "**The invented-content rows are not audited.** They count units (sentences, "
        "numbers) that the PDF's text layer does not contain. The rule's precision and "
        "recall have not been measured (the blind audit of Preregistration 7.5 has not been "
        "done), it was validated on three physics pages only, and on Markdown from a "
        "parser it counts things that are not invented content: see the table "
        "'What the flagged units are' below. Read the rows as a text-layer mismatch, not "
        "as an invention rate.",
    ]

    sections = [
        Section(
            "Headings: ReadableSTEM against the parsers",
            [
                systems_tables.metrics_table(
                    "Headings (preregistration 7.2)", heading_keys, both, N_BOOT
                ),
                systems_tables.counts_table("Counts", heading_keys, both),
                systems_tables.claims_table(
                    "Heading F1: ReadableSTEM against each parser",
                    claims,
                    a_name="ReadableSTEM",
                    k=K,
                    note="Paired cluster bootstrap by section. k = 8: the registration's rule "
                    "of 2 comparisons per parser arm with four arms (Addendum 1, D3), "
                    'although only the heading half can be made. The registered rule writes "X '
                    'is better" when the Bonferroni interval excludes 0; this report adds the '
                    'sign-flip test and writes "higher by point estimate" when the two do '
                    "not agree. Heading level is compared "
                    "relative to the section's top level; Docling and MinerU write every "
                    "heading at one level in their Markdown export, which is a property of "
                    "that export.",
                ),
                _heading_split_table(a11y_items, heading_items),
                _heading_extras_table(a11y_items, heading_items),
            ],
            level=3,
        ),
        _review_section(headings_report),
        Section(
            "Parsers on equations, invented content, coverage",
            [
                systems_tables.metrics_table(
                    "Equations (7.1)", [*eq, "e2e_norm"], parser_cols, N_BOOT
                ),
                systems_tables.metrics_table(
                    "Invented content (7.5): text-layer mismatch, rule unaudited",
                    [*inv, "inv_sentence_nocomment"],
                    parser_cols,
                    N_BOOT,
                ),
                Table(
                    "What the flagged units are (descriptive, computed from the same units)",
                    [
                        "Parser",
                        "Flagged numbers",
                        "of them negative (a minus sign)",
                        "of them with a sign, bracket, operator or spaced digits in context",
                    ],
                    flagged_rows,
                    note="`prose sentences not in the text layer, HTML comments removed` takes "
                    "the Markdown reader's view with `<!-- ... -->` removed first: PyMuPDF4LLM "
                    "wraps a boxed region in `<!-- Start of picture text -->` and its words "
                    "otherwise count as prose. A negative flagged number is mostly an exponent: "
                    "the PDF's text layer holds `10−14` with no space, which the number "
                    "rule reads as 14, and a Markdown `10 −14` reads as -14. The last "
                    "column is a loose pattern on the unit's context, not a classification "
                    "by a person. The registered rows above are unchanged.",
                ),
                Table(
                    "Equations the tool wrote as TeX or MathML (the answer key has 1,369)",
                    ["Parser", "Written"],
                    [[a, num(written[a])] for a in vectors],
                ),
                systems_tables.counts_table(
                    "Counts behind the rates",
                    [*eq, "e2e_norm", *inv, "inv_sentence_nocomment", "coverage"],
                    parser_cols,
                ),
                systems_tables.metrics_table("Coverage (7.6)", ["coverage"], parser_cols, N_BOOT),
                systems_tables.metrics_table(
                    "Figures (7.3): a parser writes no description, so every figure fails",
                    ["fig_pass", "fig_nodesc"],
                    parser_cols,
                    N_BOOT,
                ),
                systems_tables.wilson_table(
                    "Wilson 95% (items treated as independent)",
                    [*eq, "e2e_norm", *inv],
                    parser_cols,
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
        Section(
            "What these numbers leave out",
            [
                "- ReadableSTEM's equations, figures and whole documents, and the chatbot arms, "
                "were not run: there is no held-out comparison on equations.\n"
                "- Docling with formula recognition was not run to the registered rule; "
                "MinerU was run at the `basic` tier, not its documented default (`standard`). "
                "A one-off run of the `standard` tier on the same 15 PDFs, made after these "
                "results "
                "were known (not registered, outputs not committed), scored 266 of 1,369 "
                "equations exactly under the same scorer (0.194) and the same heading F1 "
                "(0.9228).\n"
                "- The PDFs are printed by this project's stylesheet, not the publisher's.\n"
                "- Heading F1 is the only comparison with a result. It rests on 15 sections of "
                "5 books. A sign-flip test on the section differences is above the "
                "Bonferroni threshold for MinerU and PyMuPDF4LLM, so those two are "
                "differences in point estimate that the registered interval only just "
                "separates from 0.\n"
                "- On free-standing headings ReadableSTEM is not ahead (0.964 recall against 0.973 to "
                "1.000 for the parsers); its lead is in headings inside boxes, and a part of "
                "Docling's and PyMuPDF4LLM's deficit is bare exercise numbers that they write "
                "as headings (tables above).\n"
                "- ReadableSTEM's review screen asked about none of the 28 wrong headings "
                "(table above).\n"
                "- The invented-content rows are a text-layer mismatch from an unaudited "
                "rule, and are not comparable between a Markdown parser and HTML.\n"
                "- The parser model weights are not pinned or hashed in the run records "
                "(`proof/parsers/WEIGHTS.md` lists what the cache held afterwards), and the "
                "runs were not offline.\n"
                "- No screen-reader testing, no scans, no handwriting."
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


def _heading_split_table(
    a11y_items: Sequence[dict[str, Any]], parser_items: dict[str, list[dict[str, Any]]]
) -> Table:
    names = ["ReadableSTEM", *parser_items]
    splits = [heading_recall_split(a11y_items)] + [
        heading_recall_split(items) for items in parser_items.values()
    ]
    free = _fmt([s["free"] for s in splits])
    boxed = _fmt([s["boxed"] for s in splits])
    return Table(
        "Heading recall: free-standing headings and headings inside a box (descriptive)",
        ["Where the gold heading sits", *names],
        [
            ["Free-standing (no enclosing box)", *free],
            ["Inside a box (example, solution, note, exercise, ...)", *boxed],
        ],
        note="Recall = gold headings found / gold headings, pooled over the 15 sections, "
        "no interval. This split was made after the results were known; it shows where "
        "the difference in recall in the registered table comes from.",
    )


def _heading_extras_table(
    a11y_items: Sequence[dict[str, Any]], parser_items: dict[str, list[dict[str, Any]]]
) -> Table:
    rows = []
    for name, items in [("ReadableSTEM", list(a11y_items)), *parser_items.items()]:
        matched = sum(i["matched"] for i in items)
        predicted = sum(i["n_pred"] for i in items)
        gold = sum(i["n_gold"] for i in items)
        extra, bare = bare_number_extras(items)
        rows.append(
            [
                name,
                num(extra),
                num(bare),
                num(_f1(matched, predicted, gold)),
                num(_f1(matched, predicted - bare, gold)),
            ]
        )
    return Table(
        "Extra headings that are only a number, and heading F1 without them (descriptive)",
        [
            "System",
            "Headings that match no gold heading",
            "of them only a number (an exercise number, say)",
            "Heading F1 (registered row)",
            "Heading F1 if those are not counted as written",
        ],
        rows,
        note="Docling and PyMuPDF4LLM write a bare exercise number such as `142.` as a "
        "heading; the gold has none. The last column is a sensitivity check made after the "
        "results were known, not a rule.",
    )


def _review_section(report: dict[str, Any]) -> Section:
    load = report["metrics"]["review_load"]
    flags = load["flags"]
    by_status = report["metrics"]["precision_by_status"]
    wrong = load["wrong_not_required"]
    where = Counter((w["status"], w["review"]) for w in wrong)
    only = ", ".join(f"{n} {s}, {r} note" for (s, r), n in sorted(where.items()))
    table = Table(
        "ReadableSTEM's review screen on headings (headings task, registered metrics 7.4)",
        ["Measure", "Value"],
        [
            ["PDF pages", num(load["pdf_pages"])],
            ["Required decisions (one per group of fixes)", num(load["required_decisions"])],
            ["Required fixes (several can share one decision)", num(load["required_fixes"])],
            ["**Required decisions per PDF page**", f"**{num(load['required_per_pdf_page'])}**"],
            ["Optional notes per PDF page", num(load["optional_per_pdf_page"])],
            ["Wrong headings (predicted, not in the answer key)", num(flags["wrong"])],
            [
                "Wrong headings a required decision pointed at",
                f"{num(flags['wrong_required'])} of {num(flags['wrong'])}",
            ],
            [
                "Required decisions that pointed at a wrong heading",
                f"{num(flags['required_units_wrong'])} of {num(flags['required_units'])}",
            ],
            ["**Heading flags: precision**", f"**{num(flags['flag_precision'])}**"],
            ["**Heading flags: recall**", f"**{num(flags['flag_recall'])}**"],
            ["Wrong headings not asked about (wrong_not_required)", num(len(wrong))],
            [
                "Precision of headings left accepted / sent to review",
                f"{num(by_status['accepted']['precision'])} (n = {by_status['accepted']['n']}) / "
                f"{num(by_status['needs_review']['precision'])} "
                f"(n = {by_status['needs_review']['n']})",
            ],
        ],
        note="Flag precision is the share of required decisions that point at a wrong "
        "heading; recall is the share of wrong headings a person is asked about. All "
        f"{len(wrong)} wrong headings were left in the page as accepted headings ({only}). "
        "On this set the review screen caught none of the heading errors the answer key "
        "shows. On the development sample (PROOF.md, document task) the same two measures "
        "were precision 0.5556 and recall 0.7778; that sample is the one the rules were "
        "tuned on, so it is not evidence for the held-out case. The 115 headings sent to "
        "review were all right: the screen asks about repeated labels, which were not where "
        "the errors were.",
    )
    return Section("Review load and heading flags (ReadableSTEM)", [table], level=3)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=HERE / "heldout-2026-10")
    parser.add_argument("--out", type=Path, default=HERE / "INTERIM-heldout-2026-10.md")
    args = parser.parse_args()
    args.out.write_text(build(args.data, args.gold), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
