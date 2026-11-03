"""The equation diagnostics (`scripts/eq_diag_*.py`) on one fixture paper: the calculus notes of
`tests/fixtures/layout/latex` (pdfTeX, Computer Modern: CMMI, CMSY, CMEX, scripts and limits)
with a gold written here for four of its formulas. Offline; nothing reads the real-world cache."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import eq_diag_common as common  # noqa: E402
import eq_diag_glyphs as glyphs  # noqa: E402
import eq_diag_tables as tables  # noqa: E402
import eq_diag_unflagged as unflagged  # noqa: E402

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


def test_locate_finds_a_formula_by_its_text(page: common.Stream) -> None:
    sig = common.gold_signature(record("g", r"f(a+h)", 0))
    spans, how = common.locate(page, sig)
    assert how == "exact" and spans
    window = page.window(*spans[0])
    assert {c.font for c in window} >= {"CMMI10", "CMR10"}


def test_locate_says_none_for_text_the_page_lacks(page: common.Stream) -> None:
    spans, how = common.locate(page, common.signature("zzqqxxvv"))
    assert (spans, how) == ([], "none")


def test_partial_match_when_the_text_layer_lacks_glyphs(page: common.Stream) -> None:
    # a floor bracket drawn as a rule is not in a text layer: the rest of the text still locates
    sig = common.signature("⌊") + common.gold_signature(record("g", r"f(a+h)", 0))
    spans, how = common.locate(page, sig)
    assert how in ("fuzzy", "partial") and spans


def test_evidence_of_a_formula_and_of_plain_text(page: common.Stream) -> None:
    frame = common.page_frame(page.chars)
    formula, _ = common.locate(page, common.gold_signature(record("g", r"f(a+h)", 0)))
    found = common.evidence_of(page.window(*formula[0]), frame)
    assert found["CMMI"] >= 2 and found["any maths font"] >= 2 and found["any evidence"] >= 2
    words, _ = common.locate(page, common.signature("derivative"))
    assert common.evidence_of(page.window(*words[0]), frame) == {}


def test_a_limit_subscript_is_a_lowered_script(page: common.Stream) -> None:
    frame = common.page_frame(page.chars)
    spans, _ = common.locate(page, common.gold_signature(record("g", r"h\to 0", 0)))
    found = common.evidence_of(page.window(*spans[0]), frame)
    assert found.get("lowered", 0) >= 1


def test_math_family_names() -> None:
    assert common.math_family("CMMIB10") == "CMMI"
    assert common.math_family("CMBSY10") == "CMSY"
    assert common.math_family("MSBM10") == "MSBM"
    assert common.math_family("STIXTwoMath-Regular") == "other"
    assert common.math_family("CMR10") is None


def test_glyph_task_groups_and_background() -> None:
    records = [
        record("g/0", r"f(a+h)", 0),
        record("g/1", r"h\to 0", 1),
        record("g/2", r"qqqzzzvvv", 2),
    ]
    targets = {
        "g/0": {"group": glyphs.GROUPS[5]},
        "g/1": {"group": glyphs.GROUPS[0]},
        "g/2": {"group": glyphs.GROUPS[0]},
    }
    out = glyphs.paper_task({"pdf": str(PDF), "records": records, "targets": targets})
    rows = {r["id"]: r for r in out["rows"]}
    assert rows["g/0"]["located"] and "CMMI" in rows["g/0"]["certain"]
    assert "lowered" in rows["g/1"]["possible"]
    assert not rows["g/2"]["located"]
    assert out["background"]["chars"] > 500
    assert out["background"].get("CMEX", 0) < out["background"]["chars"] * 0.05
    text = glyphs.report([{"part": "p", "run": "r", "rows": out["rows"],
                           "background": out["background"], "gold": 3}])  # fmt: skip
    assert "undetected, running text" in text and "Background" in text


# ------------------------------------------------------------------------------- tables


def _paper_doc(tmp_path: Path, rotated: bool = False) -> pymupdf.Document:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((100, 200), "gain 47.8 here", fontsize=11, rotate=90 if rotated else 0)
    path = tmp_path / "t.pdf"
    doc.save(path)
    return pymupdf.open(path)


def _table_case(tmp_path: Path, *, rotated: bool = False):
    gold = {"g/0": record("g/0", "47.8", 0)}
    split = SimpleNamespace(gold=gold)
    return split, _paper_doc(tmp_path, rotated)


def _bbox(doc: pymupdf.Document) -> tuple[float, float, float, float]:
    rect = doc[0].get_text("words")
    xs = [w[0] for w in rect] + [w[2] for w in rect]
    ys = [w[1] for w in rect] + [w[3] for w in rect]
    return (min(xs) - 5, min(ys) - 5, max(xs) + 5, max(ys) + 5)


def test_table_miss_in_a_table_with_no_maths_cell(tmp_path: Path) -> None:
    split, doc = _table_case(tmp_path)
    regions = {1: [("p1-e0", "table", _bbox(doc))]}
    item = {"unmatched": [], "pairs": []}
    row = tables.diagnose(split, item, regions, "g/0", doc, {})
    assert row["cause"] == tables.CAUSES[4] and row["detected"] is True


def test_table_miss_when_other_cells_of_the_table_were_read(tmp_path: Path) -> None:
    split, doc = _table_case(tmp_path)
    regions = {1: [("p1-e0", "table", _bbox(doc))]}
    item = {"unmatched": [{"element_id": "p1-e0-m3", "pred_text": "zz"}], "pairs": []}
    assert tables.diagnose(split, item, regions, "g/0", doc, {})["cause"] == tables.CAUSES[5]


def test_table_not_detected_names_what_holds_the_text(tmp_path: Path) -> None:
    split, doc = _table_case(tmp_path)
    regions = {1: [("p1-e0", "paragraph", _bbox(doc))]}
    row = tables.diagnose(split, {"unmatched": [], "pairs": []}, regions, "g/0", doc, {})
    assert row["cause"] == tables.CAUSES[3] and row["held_by"] == "paragraph"


def test_rotated_text_is_its_own_cause(tmp_path: Path) -> None:
    split, doc = _table_case(tmp_path, rotated=True)
    row = tables.diagnose(split, {"unmatched": [], "pairs": []}, {}, "g/0", doc, {})
    assert row["cause"] == tables.CAUSES[2]


def test_a_prediction_with_its_text_is_an_order_misalignment(tmp_path: Path) -> None:
    split, doc = _table_case(tmp_path)
    item = {"unmatched": [{"element_id": "p1-e0-m0", "pred_text": "47.8"}], "pairs": []}
    row = tables.diagnose(split, item, {}, "g/0", doc, {})
    assert row["cause"] == tables.CAUSES[0]


def test_text_absent_from_the_page_is_not_in_the_text_layer(tmp_path: Path) -> None:
    split = SimpleNamespace(gold={"g/0": record("g/0", r"qqqzzzvvv", 0)})
    row = tables.diagnose(
        split, {"unmatched": [], "pairs": []}, {}, "g/0", _paper_doc(tmp_path), {}
    )
    assert row["cause"] == tables.CAUSES[1]


def test_table_typed_kinds() -> None:
    assert tables.is_table("table") and tables.is_table("sidewaystable")
    assert not tables.is_table("figure>subfigure") and not tables.is_table("text")


# ------------------------------------------------------------------------------ unflagged


def pair(text: str | None, latex: str, gold_latex: str) -> tuple[dict, str]:
    mathml = latex_to_mathml(latex, "inline")
    return (
        {"pred_text": text, "pred_latex": latex, "pred_mathml": mathml},
        latex_to_mathml(gold_latex, "inline"),
    )


@pytest.mark.parametrize(
    ("text", "latex", "gold", "reason"),
    [
        ("", r"x+1", r"x+2", 0),
        ("(cid:12) = 1", r"x=1", r"y=1", 1),
        ("", r"x=1", r"y=1", 1),
        ("= +", r"x=1", r"y=1", 1),  # no letter, digit or Greek letter at all
        ("u", r"\mathbf{u}", r"\boldsymbol{u}", 3),
        ("(x)", r"(x)", r"[x]", 4),
        ("x → y", r"x \to y", r"x = y", 6),
        ("x+y", r"x+y", r"x-y", 7),
        ("x2", r"x^2", r"x_2", 8),
        ("xy", r"xy", r"xz", 9),
    ],
)
def test_reasons_a_silent_error_was_not_flagged(
    text: str, latex: str, gold: str, reason: int
) -> None:
    p, gold_mathml = pair(text, latex, gold)
    assert unflagged.reason_for(p, gold_mathml) == unflagged.REASONS[reason]


def test_a_bag_that_differs_is_reported_as_firing() -> None:
    p, gold_mathml = pair("x+1", r"x+2", r"x+1")
    assert unflagged.reason_for(p, gold_mathml) == unflagged.REASONS[2]


def test_dev_is_not_pooled_with_dev2() -> None:
    both = [{"part": "dev"}, {"part": "dev2"}, {"part": "fresh4"}]
    assert [r["part"] for r in common.pooled(both)] == ["dev2", "fresh4"]
    assert common.pooled(both[:1]) == both[:1]


def test_style_pairs_name_the_two_styles() -> None:
    assert unflagged.style_pairs(r"\mathbf{u}", latex_to_mathml(r"\boldsymbol{u}", "inline")) == [
        "bold -> bold-italic"
    ]
