"""Equations: a parser's LaTeX through a11y-stem's own converter, then the shared scorer.

Four rows, all through `pipeline_eval.document_page_item` (the document task's alignment to
gold in reading order, threshold 0.5, and `eval.equations.score_mathml`):

- `primary`: PREREG section 7.1, unchanged. `equation_step.clean_latex`, the allowlist
  (`check_latex`), `safe_latex_to_mathml`, `check_mathml`. This is what a11y-stem does to its
  own model's LaTeX, and what the chatbot baseline did. The row that counts.
- `allowlist_free`: the same without `check_latex` (PARSERS-PLAN 4.4a). The allowlist is a
  security rule of the product; this row shows how much of a parser's score it costs. Not a
  claim.
- `normalised_plan`: `primary` after the two markup-only rewrites PARSERS-PLAN 4.4b lists.
- `normalised_plus`: those two plus the rules this harness proposes after seeing the parsers'
  LaTeX on development pages (see `normalise_plus`). The plan is not frozen, so each proposal
  can still be struck. Neither normalised row is a claim.

A tool that wrote no LaTeX (Docling by default, PyMuPDF4LLM) has no predicted equations, so
every row scores 0 for it. That is reported as "does not transcribe equations (by design)", not
as a quality score (PARSERS-PLAN 4.3).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from typing import Any, Literal

from a11y_stem.core.types import Element, EquationFix, Fix, Kind, Status
from a11y_stem.eval import pipeline_eval as pe
from a11y_stem.pipeline import equation_step as es
from a11y_stem.pipeline.validate import (
    LatexConversionError,
    check_mathml,
    has_errors,
    safe_latex_to_mathml,
)

from parsers_bench.intermediate import Equation

Display = Literal["block", "inline"]
VARIANTS = ("primary", "allowlist_free", "normalised_plan", "normalised_plus")

# The plan's two rules (PARSERS-PLAN 4.4b).
# A multi-letter word in an upright-text command, letters possibly spaced out the way MinerU
# writes them ("\mathsf { d i s t a n c e }"), becomes \text{word}.
_UPRIGHT_WORD = re.compile(
    r"\\(?:mathrm|mathsf|textrm|operatorname)\s*\{\s*([A-Za-z](?:\s*[A-Za-z])+)\s*\}"
)
_VARIANT_CAPITAL = re.compile(r"\\var(Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega)")
# Proposed here, after seeing MinerU's LaTeX on development pages.
_TAG = re.compile(r"\\tag\*?\s*\{[^{}]*\}")
_SPACED_DIGITS = re.compile(r"(?<=\d)[ \t]+(?=\d)")
_UPRIGHT_LETTER = re.compile(r"\\(?:mathrm|mathsf|textrm)\s*\{\s*([A-Za-z])\s*\}")

_G1_CHARS = set("=+\u2212-\u00d7\u00b7\u00f7/()[]<>\u2264\u2265\u2248\u00b1")


def is_g1(mathml: str) -> bool:
    """Known scorer gap G1 (PREREG 7.1): a gold <mtext> token holding an operator or bracket
    next to other characters ("time =", "m/s)(46 s)"). latex2mathml writes those as text plus
    an <mo>, so a correct transcription can still fail exact match. Decided on gold alone.
    Same rule as the held-out selection's gold_summary.py."""
    try:
        root = ET.fromstring(mathml)
    except ET.ParseError:
        return False
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "mtext":
            text = "".join(el.itertext()).strip()
            if len(text) > 1 and any(c in _G1_CHARS for c in text):
                return True
    return False


def normalise_plan(latex: str) -> str:
    """The two markup-only rewrites of PARSERS-PLAN 4.4b; neither changes what the formula says.

    1. a multi-letter word in \\mathrm, \\mathsf, \\textrm or \\operatorname becomes
       \\text{word}, inner spaces removed;
    2. a variant Greek capital becomes its base letter (\\varDelta -> \\Delta).
    """
    text = _UPRIGHT_WORD.sub(lambda m: "\\text{" + re.sub(r"\s+", "", m.group(1)) + "}", latex)
    return _VARIANT_CAPITAL.sub(lambda m: "\\" + m.group(1), text)


def normalise_plus(latex: str) -> str:
    """`normalise_plan` plus three proposals, each a markup difference and nothing more.

    3. an equation number written \\tag{...} is dropped: the number is a label beside the
       equation, which a11y-stem keeps out of the maths (`Element.equation_label`);
    4. digits separated only by spaces are one number ("1 5 0" is 150): TeX ignores spaces in
       maths, but latex2mathml writes one <mn> per spaced digit, and MinerU spaces every token;
    5. a single upright letter in \\mathrm, \\mathsf or \\textrm becomes \\text{letter} (a unit
       such as \\mathrm{s}; the gold writes units as <mtext>).
    """
    text = normalise_plan(latex)
    text = _TAG.sub("", text)
    text = _SPACED_DIGITS.sub("", text)
    text = _UPRIGHT_LETTER.sub(lambda m: "\\text{" + m.group(1) + "}", text)
    return text.strip()


def prepare(latex: str, display: Display, variant: str) -> str:
    text = latex
    if variant == "normalised_plan":
        text = normalise_plan(text)
    elif variant == "normalised_plus":
        text = normalise_plus(text)
    text = es.clean_latex(text)
    if display == "inline":
        text = es.strip_trailing_punctuation(text)
    return text


def convert(latex: str, display: Display, variant: str) -> tuple[str | None, list[str]]:
    """(MathML or None, codes of the checks that failed)."""
    if variant == "allowlist_free":
        try:
            mathml = safe_latex_to_mathml(latex, display)
        except LatexConversionError:
            return None, ["conversion_failed"]
        checks = check_mathml(mathml)
        failed = [c.code or c.name for c in checks if not c.passed]
        return (None if has_errors(checks) else mathml), failed
    mathml, checks = es._convert(latex, display)
    return mathml, [c.code or c.name for c in checks if not c.passed]


def _elements_and_fixes(
    rows: list[dict[str, Any]],
) -> tuple[list[Element], dict[str, Fix]]:
    elements: list[Element] = []
    fixes: dict[str, Fix] = {}
    for row in rows:
        eid = f"x{row['order']}"
        elements.append(
            Element(
                id=eid,
                kind=Kind.EQUATION,
                page=0,
                bbox=(0, 0, 0, 0),
                order=row["order"],
                text=None,
                display=row["display"],
            )
        )
        equation = (
            EquationFix(latex=row["latex"], mathml=row["mathml"], speech="", display=row["display"])
            if row["mathml"]
            else None
        )
        fixes[eid] = Fix(
            element_id=eid,
            kind=Kind.EQUATION,
            status=Status.ACCEPTED if equation else Status.FAILED,
            confidence=None,
            equation=equation,
            reason=None if equation else "invalid_latex",
        )
    return elements, fixes


def predicted_rows(equations: list[Equation], variant: str) -> list[dict[str, Any]]:
    """One row per equation the tool transcribed (LaTeX or MathML), converted."""
    rows: list[dict[str, Any]] = []
    for eq in equations:
        if eq.mathml:
            rows.append(
                {
                    "order": eq.order,
                    "display": eq.display,
                    "latex": eq.latex or "",
                    "mathml": eq.mathml,
                    "failed": [],
                }
            )
        elif eq.latex:
            latex = prepare(eq.latex, eq.display, variant)
            mathml, failed = convert(latex, eq.display, variant)
            rows.append(
                {
                    "order": eq.order,
                    "display": eq.display,
                    "latex": latex,
                    "mathml": mathml,
                    "failed": failed,
                }
            )
    return rows


def score_variant(
    equations: list[Equation], gold: list[dict], variant: str, *, page_id: str
) -> dict[str, Any]:
    """The document task's item and metrics for one arm and one row."""
    rows = predicted_rows(equations, variant)
    elements, fixes = _elements_and_fixes(rows)
    item = pe.document_page_item(page_id, gold, elements, fixes, [], [])
    metrics = pe.document_metrics([item])
    gold_n = len(gold)
    g1_ids = {g["id"] for g in gold if is_g1(g["mathml"])}
    wrong_aligned = sum(not p["exact_lenient"] for p in item["pairs"])
    wrong_not_g1 = sum(not p["exact_lenient"] and p["gold_id"] not in g1_ids for p in item["pairs"])
    exact_not_g1 = sum(p["exact_lenient"] and p["gold_id"] not in g1_ids for p in item["pairs"])
    gold_not_g1 = gold_n - len(g1_ids)
    refusals = Counter(code for row in rows for code in row["failed"])
    return {
        "variant": variant,
        "transcribed": len(rows),
        "converted": sum(row["mathml"] is not None for row in rows),
        "refused_or_failed": dict(sorted(refusals.items())),
        "item": item,
        "gold_equations": gold_n,
        "detection_recall": metrics["detection_recall"],
        "detection_precision": metrics["detection_precision"],
        "matched_exact_lenient": metrics["matched_exact_lenient"],
        "matched_mean_teds": metrics["matched_mean_teds"],
        "end_to_end_exact_lenient": metrics["end_to_end_exact_lenient"] if gold_n else None,
        # Parsers ask no questions, so every wrong aligned equation is unflagged
        # (PREREG 7.1). Gold equations nothing aligned to are missed, not "wrong". The
        # headline leaves out G1 gold equations; the plain count is beside it.
        "gold_g1": len(g1_ids),
        "wrong_aligned": wrong_aligned,
        "wrong_aligned_excluding_g1": wrong_not_g1,
        "unflagged_wrong_per_100": 100 * wrong_not_g1 / gold_n if gold_n else None,
        "unflagged_wrong_per_100_with_g1": 100 * wrong_aligned / gold_n if gold_n else None,
        "end_to_end_exact_lenient_excluding_g1": (
            exact_not_g1 / gold_not_g1 if gold_not_g1 else None
        ),
        "predicted_rows": rows,
    }
