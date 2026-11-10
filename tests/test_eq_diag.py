"""The equation diagnostics (`scripts/eq_diag_*.py`) on one fixture paper: the calculus notes of
`tests/fixtures/layout/latex` (pdfTeX, Computer Modern: CMMI, CMSY, CMEX, scripts and limits)
with a gold written here for four of its formulas. Offline; nothing reads the real-world cache."""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import eq_diag_common as common  # noqa: E402

from readablestem.eval.equations import latex_to_mathml  # noqa: E402

PDF = REPO / "tests" / "fixtures" / "layout" / "latex" / "calculus_notes.pdf"


def record(gid: str, latex: str, index: int, *, page: int = 1, display: str = "inline") -> dict:
    return {
        "id": gid,
        "latex": latex,
        "mathml": latex_to_mathml(latex, display),
        "index_on_page": index,
        "pdf_page_in_extract": page,
        "display": display,
        "tokens": len(latex),
        "page_slug": "calculus_notes",
    }


@pytest.fixture(scope="module")
def page() -> common.Stream:
    doc = pymupdf.open(PDF)
    chars = common.page_chars(doc[0])
    return common.Stream.of(chars)


# ------------------------------------------------------------------------------- tables


# ------------------------------------------------------------------------------ unflagged


def pair(text: str | None, latex: str, gold_latex: str) -> tuple[dict, str]:
    mathml = latex_to_mathml(latex, "inline")
    return (
        {"pred_text": text, "pred_latex": latex, "pred_mathml": mathml},
        latex_to_mathml(gold_latex, "inline"),
    )


def test_dev_is_not_pooled_with_dev2() -> None:
    both = [{"part": "dev"}, {"part": "dev2"}, {"part": "fresh4"}]
    assert [r["part"] for r in common.pooled(both)] == ["dev2", "fresh4"]
    assert common.pooled(both[:1]) == both[:1]
