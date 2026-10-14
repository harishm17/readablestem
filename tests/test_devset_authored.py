"""The authored documents of the extended development set (data/dev-v2/dev/authored).

Their PDFs are committed with their gold; these tests check that each one is a usable
input (a readable untagged PDF with its source), that the gold is internally consistent
(LaTeX and MathML agree, the headings are on the page) and that the layout step reads
every one of them. Offline; no Tectonic, LibreOffice or python-docx needed.
"""

from __future__ import annotations

import json
import unicodedata
from functools import cache
from pathlib import Path

import pymupdf
import pytest

from a11y_stem.core.types import Kind
from a11y_stem.eval.equations import latex_to_mathml
from a11y_stem.eval.gold_scoring import score_equations
from a11y_stem.gold import devset as ds
from a11y_stem.gold import devset_classify as dc
from a11y_stem.pipeline.layout import analyze

ROOT = Path(__file__).resolve().parents[1] / "data" / "dev-v2" / "dev" / "authored"
DOCS = sorted(p.name for p in ROOT.iterdir() if p.is_dir()) if ROOT.is_dir() else []
KINDS = {
    "equation", "heading", "script", "code", "glossary", "table", "hyphen_break", "exercise_text",
}  # fmt: skip
SOURCE = {"tectonic": ".tex", "libreoffice": ".docx", "chromium": ".html"}

pytestmark = pytest.mark.skipif(not DOCS, reason="data/dev-v2 is not built")


@cache
def gold(doc: str) -> list[dict]:
    return ds.parse_jsonl((ROOT / doc / "gold.jsonl").read_bytes())


@cache
def pdf_pages(doc: str) -> tuple[str, ...]:
    with pymupdf.open(ROOT / doc / f"{doc}.pdf") as pdf:
        return tuple(page.get_text() for page in pdf)


_FOLD = str.maketrans(
    {
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2212": "-",
    }
)  # fmt: skip


def flat(text: str, *, hyphens: bool = True) -> str:
    """NFKC, typographic quotes and dashes folded, soft hyphens, whitespace and (unless
    `hyphens` is false) hyphens removed: a heading that wraps and breaks inside a word
    still matches its source text."""
    folded = unicodedata.normalize("NFKC", text).replace("\u00ad", "").translate(_FOLD)
    folded = "".join(folded.split())
    return folded.replace("-", "") if hyphens else folded


@pytest.mark.parametrize("doc", DOCS)
def test_each_document_has_its_source_a_small_untagged_pdf_and_dev_gold(doc: str) -> None:
    records = gold(doc)
    assert records and {r["variant"] for r in records} == {"dev"}
    toolchain = records[0]["toolchain"]
    assert (ROOT / doc / f"{doc}{SOURCE[toolchain]}").is_file()
    pdf = ROOT / doc / f"{doc}.pdf"
    assert pdf.stat().st_size < 300_000
    with pymupdf.open(pdf) as opened:
        assert opened.page_count >= 1
        assert opened.xref_get_key(opened.pdf_catalog(), "StructTreeRoot")[0] == "null"
    assert all(r["pdf"] == f"authored/{doc}/{doc}.pdf" for r in records)


@pytest.mark.parametrize("doc", DOCS)
def test_records_carry_licence_known_kinds_and_classes(doc: str) -> None:
    records = gold(doc)
    ids = [r["id"] for r in records]
    assert len(ids) == len(set(ids))
    for r in records:
        assert r["kind"] in KINDS and r["source"] == "authored"
        assert r["license_name"] == "MIT License" and r["license_url"].endswith("/MIT")
        assert set(r["classes"]) <= set(dc.CLASS_IDS)
        assert r["id"].startswith(f"authored/{doc}/")
        assert 0 <= r["page"] < len(pdf_pages(doc))


@pytest.mark.parametrize("doc", DOCS)
def test_equation_gold_is_the_latex_it_was_set_from(doc: str) -> None:
    for r in (r for r in gold(doc) if r["kind"] == "equation"):
        assert latex_to_mathml(r["latex"], r["display"]) == r["mathml"], r["id"]
        assert r["appearance"] in {"text", "image"}
        if r["appearance"] == "image":
            assert "equation_image" in r["classes"] and (ROOT.parent / r["image"]).is_file()


@pytest.mark.parametrize("doc", DOCS)
def test_headings_and_other_text_gold_is_on_the_page_it_names(doc: str) -> None:
    for r in gold(doc):
        if r["kind"] == "heading":
            needle = flat(r["text"])
        elif r["kind"] == "glossary":
            needle = flat(r["term"])
        elif r["kind"] == "code":
            needle = flat(r["text"].splitlines()[0])
        else:
            continue
        assert needle in flat(pdf_pages(doc)[r["page"]]), r["id"]


@pytest.mark.parametrize("doc", DOCS)
def test_hyphen_breaks_name_a_word_that_is_in_the_document(doc: str) -> None:
    for r in (r for r in gold(doc) if r["kind"] == "hyphen_break"):
        assert r["left"] and r["right"] and r["word"]
        assert flat(r["word"]) == flat(r["left"] + r["right"])
        assert r["compound"] == ("-" in r["word"])


@pytest.mark.parametrize("doc", DOCS)
def test_the_layout_step_reads_the_pdf(doc: str) -> None:
    layout = analyze((ROOT / doc / f"{doc}.pdf").read_bytes())
    kinds = {e.kind for e in layout.elements}
    assert Kind.HEADING in kinds or doc == "latex-hyphenation"
    equations = [r for r in gold(doc) if r["kind"] == "equation" and r["appearance"] == "text"]
    if len(equations) > 10:
        assert any(e.kind == Kind.EQUATION for e in layout.elements)


def test_equation_gold_scores_one_against_itself() -> None:
    equations = [r for doc in DOCS for r in gold(doc) if r["kind"] == "equation"]
    summary = score_equations(equations, None)["summary"]
    assert summary["n"] == len(equations) > 200
    assert summary["exact_lenient"] == 1.0


def test_the_documents_cover_the_toolchains_and_the_manifest_lists_them() -> None:
    toolchains = {gold(doc)[0]["toolchain"] for doc in DOCS}
    assert toolchains == {"tectonic", "libreoffice", "chromium"}
    manifest = json.loads((ROOT.parent / "manifest.json").read_text(encoding="utf-8"))
    assert [d["id"] for d in manifest["authored_documents"]] == DOCS
    index = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
    for entry in index["documents"]:
        assert (ROOT / entry["pdf"]).is_file() and (ROOT / entry["gold"]).is_file()
