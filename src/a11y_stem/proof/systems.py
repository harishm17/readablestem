"""Other systems' outputs (chatbot recipe, open parsers) and how they are scored.

Layout in the bundle, one JSON per system and section (the form `proof.intermediate`
reads: `arm`, `section`, `status`, `seconds`, `versions`, `settings`) plus keys of ours,
and the output itself next to it:

    systems/<arm>/<book>__<page>[.runN].json    metadata, cost, time, figure judgements
    systems/<arm>/<book>__<page>[.runN].html    the reply's HTML, as it came (chatbot)
    systems/<arm>/<book>__<page>[.runN].md      the parser's Markdown export

Every system is scored from that output file by one reader (`eval.invented.read`), against
the same gold and the same PDF, with the same code that scores a11y-stem's own HTML where
the measure is shared (preregistration section 7):

- equations: the output's maths (MathJax TeX, `<math>`, Markdown `$...$`) is converted the
  way a11y-stem converts its own model's LaTeX (`equation_step.clean_latex`, then the
  allowlist and `latex2mathml`), then aligned to the gold equations by the document task's
  code (`pipeline_eval.document_page_item`), MathML signature only. A second row skips the
  allowlist (PARSERS-PLAN 4.4a), descriptive only;
- headings: `pipeline_eval.heading_page_item`;
- invented content: `eval.invented`;
- coverage: the share of the PDF text layer's word 6-grams found in the output;
- figures: the description found by the rule of section 7.3, judged by the figure judge
  when the system was recorded (the verdicts are in the JSON); no description is a fail.

A system that could not convert a section (status other than ok) scores as found nothing.
Nothing here prints document text.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from a11y_stem.core.types import Element, EquationFix, Fix, Kind, Status
from a11y_stem.eval import pipeline_eval as pe
from a11y_stem.eval.invented import figures as inv_figures
from a11y_stem.eval.invented import read as inv_read
from a11y_stem.eval.invented import score as inv_score
from a11y_stem.eval.invented import text as inv_text
from a11y_stem.pipeline import equation_step as es
from a11y_stem.pipeline.validate import (
    LatexConversionError,
    check_mathml,
    has_errors,
    safe_latex_to_mathml,
)
from a11y_stem.proof.bundle import SECTION_SEP, Bundle
from a11y_stem.proof.errors import BundleError

COVERAGE_GRAM = 6
_FILE = re.compile(r"^(?P<section>.+?)(?:\.run(?P<run>\d+))?\.json$")
FORMATS = {"html": "html", "md": "markdown"}


@dataclass(frozen=True)
class SystemRun:
    """One output of one system on one section (one repeat)."""

    arm: str
    section: str
    run: int
    meta: dict[str, Any]
    output_path: Path | None

    @property
    def status(self) -> str:
        return str(self.meta.get("status", "crashed"))

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.output_path is not None

    @property
    def pdf_pages(self) -> int:
        return int(self.meta.get("pdf_pages") or 0)

    @property
    def cost_usd(self) -> float:
        return float(self.meta.get("cost_usd") or 0.0)

    def read(self) -> inv_read.Output | None:
        if not self.ok or self.output_path is None:
            return None
        fmt = FORMATS[str(self.meta.get("output", {}).get("format", "html"))]
        return inv_read.read_output(self.output_path.read_text("utf-8", errors="replace"), fmt)


def load_arms(bundle: Bundle) -> dict[str, dict[str, list[SystemRun]]]:
    """arm -> section id -> its outputs (one per repeat), from `systems/`."""
    return load_arms_dir(bundle.systems_dir)


def load_arms_dir(root: Path) -> dict[str, dict[str, list[SystemRun]]]:
    """The same, from any folder laid out like `systems/`."""
    out: dict[str, dict[str, list[SystemRun]]] = defaultdict(lambda: defaultdict(list))
    if not root.is_dir():
        return {}
    for arm_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in sorted(arm_dir.glob("*.json")):
            match = _FILE.match(path.name)
            if match is None:
                continue
            where = f"systems/{arm_dir.name}/{path.name}"
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise BundleError(f"{where}: cannot be read: {exc}") from exc
            section = match.group("section").replace(SECTION_SEP, "/", 1)
            if meta.get("section") != section or meta.get("arm") != arm_dir.name:
                raise BundleError(f"{where}: arm or section does not match the file's place")
            file = meta.get("output", {}).get("file")
            run = SystemRun(
                arm=arm_dir.name,
                section=section,
                run=int(match.group("run") or 1),
                meta=meta,
                output_path=(arm_dir / file) if file else None,
            )
            out[arm_dir.name][section].append(run)
    return {a: {s: sorted(r, key=lambda x: x.run) for s, r in d.items()} for a, d in out.items()}


# ----------------------------------------------------------------------------- gold


@dataclass(frozen=True)
class SectionGold:
    id: str
    book: str
    page: str
    equations: list[dict]
    headings: list[dict]
    figures: list[dict]


def section_golds(records: Sequence[dict], order: Sequence[str]) -> dict[str, SectionGold]:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by[f"{r['book_slug']}/{r['page_slug']}"].append(r)
    result = {}
    for section in order:
        rs = by.get(section, [])
        book, _, page = section.partition("/")
        result[section] = SectionGold(
            id=section,
            book=book,
            page=page,
            equations=pe._of_kind(rs, "equation"),
            headings=pe._of_kind(rs, "heading"),
            figures=pe._of_kind(rs, "figure"),
        )
    return result


def figure_references(
    gold: SectionGold, image_for: Callable[[str], bytes] | None = None
) -> list[inv_figures.FigureReference]:
    refs = []
    for record in gold.figures:
        images = record.get("images") or []
        first = images[0] if images else {}
        url = first.get("url")
        picture = None
        if image_for is not None and url:
            try:
                picture = image_for(url)
            except Exception:  # an image that cannot be fetched is "no image"
                picture = None
        refs.append(
            inv_figures.FigureReference(
                id=record["id"],
                number=record.get("figure_number"),
                caption=" ".join((record.get("caption") or "").split()),
                alt=" ".join((first.get("alt") or "").split()),
                image_url=url,
                image=picture,
            )
        )
    return refs


# -------------------------------------------------------------------------- equations


def _allowlist_free(equation: inv_read.Equation) -> str | None:
    """MathML for a TeX equation without the allowlist (descriptive row)."""
    if equation.source != "tex":
        return equation.mathml
    try:
        latex = es.clean_latex(equation.raw)
        mathml = safe_latex_to_mathml(latex, equation.display)
    except (LatexConversionError, Exception):
        return None
    return None if has_errors(check_mathml(mathml)) else mathml


def equation_item(
    section: str,
    gold_equations: Sequence[dict],
    output: inv_read.Output | None,
    *,
    allowlist_free: bool = False,
    mathml_of: Callable[[inv_read.Equation], str | None] | None = None,
) -> dict[str, Any]:
    """The document task's item for one section of one system. `output` None: it found
    nothing. `mathml_of` replaces how an equation becomes MathML (a descriptive row that
    rewrites a parser's markup first); the default is what the reader made of it."""
    elements: list[Element] = []
    fixes: dict[str, Fix] = {}
    for position, equation in enumerate(output.equations if output else []):
        if mathml_of is not None:
            mathml = mathml_of(equation)
        else:
            mathml = _allowlist_free(equation) if allowlist_free else equation.mathml
        eid = f"x{position}"
        elements.append(
            Element(
                id=eid,
                kind=Kind.EQUATION,
                page=0,
                bbox=(0, 0, 0, 0),
                order=position,
                text=None,
                display=equation.display,
            )
        )
        proposal = (
            EquationFix(latex=equation.raw, mathml=mathml, speech="", display=equation.display)
            if mathml
            else None
        )
        fixes[eid] = Fix(
            element_id=eid,
            kind=Kind.EQUATION,
            status=Status.ACCEPTED if proposal else Status.FAILED,
            confidence=None,
            equation=proposal,
            reason=None if proposal else "invalid_latex",
        )
    return pe.document_page_item(section, list(gold_equations), elements, fixes, [], [])


# --------------------------------------------------------------------------- headings


def heading_item(
    section: str, gold_headings: Sequence[dict], output: inv_read.Output | None
) -> dict[str, Any]:
    predicted = [
        {"level": h.level, "text": h.text, "status": "accepted"}
        for h in (output.headings if output else [])
        if h.text.strip()
    ]
    return pe.heading_page_item(section, list(gold_headings), predicted)


# ---------------------------------------------------------------------------- coverage


def output_words(output: inv_read.Output) -> list[str]:
    words: list[str] = []
    for block in output.blocks:
        words += inv_text.norm_words(block.text.replace(inv_text.MATH, " "))
    for heading in output.headings:
        words += inv_text.norm_words(heading.text)
    for cell in output.cells:
        words += inv_text.norm_words(cell.text)
    for description in output.descriptions:
        words += inv_text.norm_words(description.text)
        if description.caption:
            words += inv_text.norm_words(description.caption)
    for equation in output.equations:
        words += inv_text.norm_words(equation.tokens)
    return words


def coverage_counts(
    reference: inv_text.Reference, output: inv_read.Output | None
) -> tuple[int, int]:
    """(R's word 6-grams found in the output, R's word 6-grams)."""
    wanted = inv_text.ngrams(reference.words, COVERAGE_GRAM)
    if output is None:
        return 0, len(wanted)
    have = inv_text.ngrams(output_words(output), COVERAGE_GRAM)
    return len(wanted & have), len(wanted)


# ---------------------------------------------------------------------------- figures


@dataclass
class FigureOutcome:
    figure_id: str
    outcome: str  # "judged" | "no_description" | "judge_failed"
    passed: bool | None = None
    factual_error: bool | None = None
    source: str | None = None
    cost_usd: float = 0.0
    prompt_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "figure_id": self.figure_id,
            "outcome": self.outcome,
            "passed": self.passed,
            "factual_error": self.factual_error,
            "source": self.source,
            "cost_usd": self.cost_usd,
            "prompt_version": self.prompt_version,
        }


def figure_outcomes(meta: dict[str, Any], gold: SectionGold) -> list[dict[str, Any]]:
    """One row per gold figure that has an OpenStax alt (the judge's reference), from the
    verdicts recorded with the output; a figure with no row is a figure with no
    description."""
    recorded = {j["figure_id"]: j for j in meta.get("figure_judgements", [])}
    rows = []
    for record in gold.figures:
        images = record.get("images") or []
        if not images or not (images[0].get("alt") or "").strip():
            continue
        row = recorded.get(record["id"])
        if row is None or row["outcome"] == "no_description":
            rows.append({"figure_id": record["id"], "outcome": "no_description"})
        else:
            rows.append(row)
    return rows


@dataclass
class SectionScore:
    """Everything the stages need from one system's output on one section."""

    arm: str
    section: str
    run: int
    status: str
    pdf_pages: int
    equation: dict[str, Any]
    equation_free: dict[str, Any]
    heading: dict[str, Any]
    invented: dict[str, Any] | None
    coverage: tuple[int, int]
    figures: list[dict[str, Any]]
    counts: dict[str, int] = field(default_factory=dict)


def score_section(
    run: SystemRun,
    gold: SectionGold,
    pdf: bytes,
    gold_equation_mathml: Sequence[str],
    *,
    reference: inv_text.Reference | None = None,
    output: inv_read.Output | None = None,
) -> SectionScore:
    """Score one output (or the lack of one) on one section."""
    reference = reference or inv_text.Reference.from_pdf(pdf)
    output = output if output is not None else run.read()
    invented = (
        inv_score.score_output(
            reference, output, gold_equations=list(gold_equation_mathml)
        ).to_dict()
        if output is not None
        else None
    )
    return SectionScore(
        arm=run.arm,
        section=run.section,
        run=run.run,
        status=run.status,
        pdf_pages=reference.pages,
        equation=equation_item(run.section, gold.equations, output),
        equation_free=equation_item(run.section, gold.equations, output, allowlist_free=True),
        heading=heading_item(run.section, gold.headings, output),
        invented=invented,
        coverage=coverage_counts(reference, output),
        figures=figure_outcomes(run.meta, gold),
        counts=output.counts() if output else {},
    )
