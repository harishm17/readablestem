"""The tables gold in the repo: the 13 OpenStax pages (CC BY), their reference pages,
the authored documents (MIT), `gold fetch` keeping table records only, and the three
table print styles."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from a11y_stem.eval import render
from a11y_stem.eval import tables as T
from a11y_stem.gold.openstax import fetch_gold, render_attribution
from test_openstax import CC_BY, FakeFetcher

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data" / "gold" / "tables"
AUTHORED = ROOT / "data" / "authored" / "tables"
FIXTURE_PAGE = Path(__file__).parent / "fixtures" / "openstax_page.xhtml"  # two tables


def openstax_records() -> list[dict]:
    return [r for p in sorted(GOLD.glob("openstax/*/*.jsonl")) for r in T.load_jsonl(p)]


def test_the_openstax_table_gold_is_the_corrected_table_bench_set():
    records = openstax_records()
    assert len(records) == 64 and sum(not r["is_layout"] for r in records) == 54
    assert {r["kind"] for r in records} == {"table"}
    pages = {(r["book_slug"], r["page_slug"]) for r in records}
    assert pages == {(b, s) for b, slugs in T.TABLE_GOLD_PAGES.items() for s in slugs}
    assert {r["license_url"] for r in records} == {CC_BY}
    assert {r["retrieved_at"] for r in records} == {T.TABLE_GOLD_DATE}
    for record in records:
        assert T.cells_from_html(record["html"]), record["id"]


def test_the_manifest_and_attribution_match_the_files():
    manifest = json.loads((GOLD / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["totals"] == {"table": 64}
    for entry in manifest["files"]:
        data = (GOLD / entry["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    attribution = (GOLD / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert attribution == render_attribution(manifest)
    assert "contain tables extracted" in attribution
    assert "Barbara Illowsky" in attribution and "Paul Peter Urone" in attribution


def test_reference_pages_cover_every_table_in_every_style_with_the_4_2_fix():
    pages = T.reference_pages(GOLD)
    ids = {r["id"] for r in openstax_records()}
    assert set(pages) == set(T.TABLE_STYLES)
    for style in T.TABLE_STYLES:
        assert set(pages[style]) == ids
    stats = "openstax/statistics/4-2-mean-or-expected-value-and-standard-deviation"
    assert pages["grid"][f"{stats}/tab-005"] == pages["grid"][f"{stats}/tab-006"] == 6


def test_the_tables_gold_does_not_join_the_other_tasks_gold():
    """data/gold/tables is a gold dir of its own: the equations, headings, figures and
    document tasks read data/gold/openstax/*/ only, so their selection is unchanged."""
    from a11y_stem.eval.pipeline_eval import load_gold

    main_gold = load_gold(ROOT / "data" / "gold")
    assert main_gold and not any(r["book_slug"] == "statistics" for r in main_gold)


def test_a_nested_gold_set_is_read_only_on_its_own(tmp_path):
    from a11y_stem.eval.gold_scoring import iter_gold_records

    (tmp_path / "openstax" / "b").mkdir(parents=True)
    (tmp_path / "openstax" / "b" / "p.jsonl").write_text('{"id": "outer"}\n')
    nested = tmp_path / "tables" / "openstax" / "b"
    nested.mkdir(parents=True)
    (nested / "p.jsonl").write_text('{"id": "inner"}\n')
    (tmp_path / "extra").mkdir()
    (tmp_path / "extra" / "x.jsonl").write_text('{"id": "loose"}\n')  # no manifest: included
    assert sorted(r["id"] for r in iter_gold_records(tmp_path)) == ["inner", "loose", "outer"]
    (tmp_path / "tables" / "manifest.json").write_text("{}")
    assert sorted(r["id"] for r in iter_gold_records(tmp_path)) == ["loose", "outer"]
    assert [r["id"] for r in iter_gold_records(tmp_path / "tables")] == ["inner"]


# ------------------------------------------------------------------------- authored


def authored_index() -> list[dict]:
    return json.loads((AUTHORED / "index.json").read_text(encoding="utf-8"))["documents"]


def test_authored_documents_have_a_pdf_a_source_and_gold():
    docs = authored_index()
    assert {d["id"] for d in docs} == {
        "tablebench-latex",
        "tablebench-word",
        "handout-latex",
        "handout-word",
        "maths-and-headers",
        "slides",
    }
    for doc in docs:
        folder = AUTHORED / doc["id"]
        assert (AUTHORED / doc["pdf"]).is_file()
        sources = [p for p in folder.iterdir() if p.suffix in {".tex", ".html", ".docx"}]
        assert sources, doc["id"]
        for record in T.load_jsonl(AUTHORED / doc["gold"]):
            assert record["id"].startswith(f"authored/{doc['id']}/")
            assert T.cells_from_html(record["html"])


def test_every_authored_gold_table_is_printed_on_its_page():
    """The gold pages are right: each table's words are (nearly all) on its page."""
    for doc in authored_index():
        texts, _ = T.pdf_page_text((AUTHORED / doc["pdf"]).read_bytes())
        for record in T.load_jsonl(AUTHORED / doc["gold"]):
            want = T.table_tokens(T.cells_from_html(record["html"]))
            have = Counter(T.tokens(texts[record["page"]]))
            held = sum((want & have).values()) / sum(want.values())
            assert held >= 0.9, (record["id"], held)


def test_the_authored_set_covers_what_the_brief_asks_for():
    records = {r["id"]: r for doc in authored_index() for r in T.load_jsonl(AUTHORED / doc["gold"])}
    cells = {i: T.cells_from_html(r["html"]) for i, r in records.items()}
    row_headers = [i for i, c in cells.items() if any(x.header == "row" for x in c)]
    two_level = [i for i, c in cells.items() if any(x.header and x.col_span > 1 for x in c)]
    maths_in_headers = [i for i, c in cells.items() if any(x.header and x.math for x in c)]
    split = Counter(r["cluster"] for r in records.values() if r.get("cluster"))
    assert len(row_headers) >= 3 and len(two_level) >= 3
    assert "authored/maths-and-headers/T1" in maths_in_headers
    assert set(split.values()) == {2} and len(split) >= 3  # a table over two pages, x3
    assert any(r["is_layout"] for r in records.values())
    assert "authored/slides/T2" in row_headers
    assert "authored/tablebench-latex/T5-unruled" in records


def test_authored_readme_states_the_licence_and_has_no_openstax_text():
    readme = (AUTHORED / "README.md").read_text(encoding="utf-8")
    assert "MIT" in readme
    for doc in authored_index():
        for record in T.load_jsonl(AUTHORED / doc["gold"]):
            assert "openstax" not in record["html"].lower()


# --------------------------------------------------------------------- gold fetch


def test_gold_fetch_can_keep_only_table_records(tmp_path):
    _, results = fetch_gold(
        "physics", 1, tmp_path, FakeFetcher(), retrieved_at="2026-10-08", kinds=("table",)
    )
    lines = results[0].path.read_text(encoding="utf-8").splitlines()
    kinds = {json.loads(x)["kind"] for x in lines}
    assert kinds == {"table"} and results[0].counts == {"table": 2}
    first = json.loads(lines[0])
    assert first["id"].endswith("/tab-000")  # ids keep their place on the page
    attribution = (tmp_path / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "The JSONL files contain tables extracted from the OpenStax web editions" in attribution


def test_the_full_gold_attribution_is_unchanged():
    gold = ROOT / "data" / "gold"
    manifest = json.loads((gold / "manifest.json").read_text(encoding="utf-8"))
    assert render_attribution(manifest) == (gold / "ATTRIBUTION.md").read_text(encoding="utf-8")


# ------------------------------------------------------------------------ print styles


def page_source() -> render.PageSource:
    return render.PageSource(
        book_slug="physics",
        book_title="Physics",
        page_slug="2-2-speed",
        page_title="2.2 Speed",
        page_url="https://openstax.org/books/physics/pages/2-2-speed",
        content_url="https://openstax.org/apps/archive/x/contents/b@v:p.json",
        license_name="Creative Commons Attribution License",
        license_url=CC_BY,
        xhtml=FIXTURE_PAGE.read_text(encoding="utf-8"),
    )


def test_table_styles_add_their_css_and_the_default_print_is_unchanged():
    plain = render.printable_html(page_source(), lambda url: None)
    assert render.printable_html(page_source(), lambda url: None, table_style=None) == plain
    for style, css in render.TABLE_STYLE_CSS.items():
        styled = render.printable_html(page_source(), lambda url: None, table_style=style)
        assert css in styled and styled.replace(css + "\n", "") == plain
    assert set(render.TABLE_STYLE_CSS) == set(T.TABLE_STYLES)


@pytest.mark.browser
def test_each_style_prints_the_same_words():
    with render.ChromiumRenderer() as browser:
        texts = {
            style: render.pdf_text(
                browser.pdf(
                    render.printable_html(page_source(), lambda url: None, table_style=style)
                )
            )
            for style in T.TABLE_STYLES
        }
    assert len({tuple(T.tokens(t)) for t in texts.values()}) == 1
    assert {"quantity", "kilogram", "meter"} <= set(T.tokens(texts["norules"]))
