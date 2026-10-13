"""Gold cut to pages of the committed demo PDF (OpenStax Physics 2.3, CC BY 4.0), the plumbing
input, and the command line."""

from __future__ import annotations

import pytest

from parsers_bench import cli, devgold


@pytest.fixture(scope="module")
def records() -> list[dict]:
    return devgold.section_records(cli.GOLD_DIR, cli.DEV_BOOK, cli.DEV_PAGE)


@pytest.fixture(scope="module")
def pdf() -> bytes:
    return cli.DEMO_PDF.read_bytes()


def test_the_dev_section_is_in_the_repo_gold(records: list[dict]) -> None:
    kinds = {r["kind"] for r in records}
    assert {"equation", "heading", "figure"} <= kinds


def test_default_pages_hold_one_inline_and_one_block_equation_and_some_headings(
    records: list[dict], pdf: bytes
) -> None:
    subset = devgold.restrict(records, pdf, [9, 10], section="s")
    assert [e["display"] for e in subset.equations] == ["inline", "block"]
    assert len(subset.headings) == 6
    assert subset.figures_on_pages == 1
    assert subset.unplaced_equations == 0
    assert subset.unplaced_headings == 0


def test_pages_partition_the_section_gold(records: list[dict], pdf: bytes) -> None:
    n_pages = 13
    placement = devgold.place(records, pdf, section="s")
    per_page = [devgold.select(placement, [p]) for p in range(n_pages)]
    gold_eq = [r for r in records if r["kind"] == "equation"]
    gold_hd = [r for r in records if r["kind"] == "heading"]
    assert sum(len(s.equations) for s in per_page) + per_page[0].unplaced_equations == len(gold_eq)
    assert sum(len(s.headings) for s in per_page) + per_page[0].unplaced_headings == len(gold_hd)


def test_the_held_out_gold_is_never_the_default() -> None:
    # The plumbing input is development material only.
    assert cli.DEV_BOOK == "physics"
    assert cli.DEV_PAGE == "2-3-position-vs-time-graphs"
    assert "proof/heldout-gold" not in str(cli.GOLD_DIR)


def test_page_ranges() -> None:
    assert cli._parse_pages("10-11") == [10, 11]
    assert cli._parse_pages("3") == [3]
    assert cli._parse_pages("1,4-5") == [1, 4, 5]


def test_list_command_prints_the_arms_and_the_ones_not_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    for name in (
        "docling",
        "docling-formula",
        "mineru-basic",
        "pymupdf4llm",
        "marker",
        "nougat",
        "olmocr",
        "mathpix",
    ):
        assert name in out
    assert "not run" in out or "not set up" in out or "ready" in out
