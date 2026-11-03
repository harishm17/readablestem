"""Figure gold v2: the seeded draw, the licence and exclusion gates, the committed gold file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from readablestem.gold import figures_v2 as fv

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "data" / "gold-figures-v2"
CC_BY = "https://creativecommons.org/licenses/by/4.0/"
LONG_ALT = (
    "A line graph shows position versus time. The vertical axis runs from 0 to 100 meters and "
    "the horizontal axis from 0 to 50 seconds, with ticks at 10, 20 and 30. The line rises."
)


def _figure(fid: str, alt: str = LONG_ALT, **extra: object) -> dict:
    return {
        "kind": "figure",
        "id": fid,
        "caption_has_math": False,
        "images": [{"url": "https://openstax.org/apps/archive/x/resources/abc", "alt": alt}],
        **extra,
    }


def _pool(n_per_stratum: int = 12, books: int = 4) -> list[fv.Candidate]:
    out = []
    for stratum in fv.STRATA:
        for i in range(n_per_stratum):
            book = f"book-{i % books}"
            out.append(
                fv.Candidate(
                    id=f"openstax/{book}/{stratum}-page-{i}/fig-000",
                    stratum=stratum,
                    source="openstax",
                    source_key=book,
                    page_key=f"{stratum}-page-{i}",
                )
            )
    return out


# ------------------------------------------------------------------------ the draw


def test_selection_is_deterministic_and_independent_of_input_order() -> None:
    pool = _pool()
    first = fv.select(pool)
    again = fv.select(list(reversed(pool)))
    assert [c.id for c in first] == [c.id for c in again]


def test_selection_changes_with_the_seed() -> None:
    pool = _pool(30, books=10)
    assert [c.id for c in fv.select(pool, seed="a")] != [c.id for c in fv.select(pool, seed="b")]


def test_quotas_are_met_and_total_is_in_range() -> None:
    chosen = fv.select(_pool(40, books=14))
    counts = fv.strata_counts([c.to_json() for c in chosen])
    assert counts == dict(fv.QUOTAS)
    assert 40 <= len(chosen) <= 60


def test_one_figure_per_page_and_source_cap() -> None:
    pool = [
        fv.Candidate(f"openstax/b/p{i // 2}/fig-{i}", fv.CHART, "openstax", "b", f"p{i // 2}")
        for i in range(40)
    ]
    chosen = fv.select(pool, quotas={fv.CHART: 14})
    assert len({c.page_key for c in chosen}) == len(chosen)
    assert len(chosen) == fv.MAX_PER_SOURCE


def test_skipped_figure_is_passed_over_in_order_not_replaced_by_choice() -> None:
    pool = _pool(30, books=10)
    first = fv.select(pool)
    victim = first[0]
    second = fv.select(pool, skip=[victim.id])
    assert victim.id not in {c.id for c in second}
    # the figures that were already ahead of the next one in the draw keep their places
    kept = [c.id for c in first if c.stratum == victim.stratum and c.id != victim.id]
    assert [c.id for c in second if c.stratum == victim.stratum][: len(kept)] == kept


def test_splits_alternate_inside_each_stratum() -> None:
    chosen = fv.select(_pool(40, books=14))
    splits = fv.assign_splits(chosen)
    for stratum in fv.STRATA:
        order = [splits[c.id] for c in chosen if c.stratum == stratum]
        assert order[:4] == ["analysis", "held", "analysis", "held"]
        assert abs(order.count("analysis") - order.count("held")) <= 1
    assert splits == fv.assign_splits(chosen)


def test_pool_hash_ignores_order() -> None:
    pool = _pool(5)
    assert fv.pool_sha256(pool) == fv.pool_sha256(list(reversed(pool)))


# ------------------------------------------------------------------- licence gates


@pytest.mark.parametrize(
    "url",
    [
        "https://creativecommons.org/licenses/by/4.0/",
        "http://creativecommons.org/licenses/by/4.0/",
        "http://creativecommons.org/licenses/by/3.0/",
    ],
)
def test_cc_by_passes(url: str) -> None:
    assert fv.licence_ok("openstax", url)
    assert fv.licence_ok("arxiv", url)


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "https://creativecommons.org/licenses/by-nc-sa/4.0/",
        "https://creativecommons.org/licenses/by-sa/4.0/",
        "https://creativecommons.org/licenses/by-nc/4.0/",
        "https://creativecommons.org/licenses/by-nd/4.0/",
        "http://creativecommons.org/publicdomain/zero/1.0/",
        "http://arxiv.org/licenses/nonexclusive-distrib/1.0/",
    ],
)
def test_other_licences_are_refused(url: str | None) -> None:
    assert not fv.licence_ok("openstax", url)
    assert not fv.licence_ok("arxiv", url)


def test_unknown_source_is_refused() -> None:
    assert not fv.licence_ok("flickr", CC_BY)


def test_candidates_need_a_cc_by_book() -> None:
    page = [_figure("openstax/physics/p/fig-000")]
    ok = fv.openstax_candidates("physics", "p", page, licence_url=CC_BY, excluded_pages=[])
    nc = fv.openstax_candidates(
        "physics",
        "p",
        page,
        licence_url="https://creativecommons.org/licenses/by-nc-sa/4.0/",
        excluded_pages=[],
    )
    assert len(ok) == 1
    assert nc == []
    assert fv.openstax_candidates("physics", "p", page, licence_url=None, excluded_pages=[]) == []


def test_candidates_refuse_a_book_outside_the_list() -> None:
    page = [_figure("openstax/other/p/fig-000")]
    assert fv.openstax_candidates("other", "p", page, licence_url=CC_BY, excluded_pages=[]) == []


# ---------------------------------------------------------------- exclusion and gates


def test_held_out_and_development_pages_are_excluded() -> None:
    page = [_figure("openstax/physics/p/fig-000")]
    assert (
        fv.openstax_candidates("physics", "p", page, licence_url=CC_BY, excluded_pages=["p", "q"])
        == []
    )


def test_real_held_out_sections_are_known_to_the_pool_script() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("pool", ROOT / "scripts" / "figures_v2_pool.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    slugs = module.known_page_slugs(ROOT / "data")
    assert "2-4-box-plots" in slugs  # held-out section
    assert "2-3-position-vs-time-graphs" in slugs  # v1 gold page


def test_short_reference_and_multi_image_figures_are_out() -> None:
    short = _figure("a", alt="A graph.")
    two = _figure("b")
    two["images"] = two["images"] * 2
    math_caption = _figure("c", caption_has_math=True)
    out = fv.openstax_candidates(
        "physics", "p", [short, two, math_caption], licence_url=CC_BY, excluded_pages=[]
    )
    assert out == []


@pytest.mark.parametrize(
    ("book", "alt", "stratum"),
    [
        ("physics", "A line graph of velocity versus time rises.", fv.CHART),
        ("statistics", "A bar chart shows five bars of frequencies.", fv.CHART),
        ("physics", "A photograph of a runner on a track.", fv.PHOTO),
        ("physics", "A circuit diagram shows a battery and two resistors in series.", fv.DIAGRAM),
        ("chemistry", "The structure of ethanol is shown with a hydroxyl group.", fv.CHEMISTRY),
        ("physics", "Some text only.", None),
    ],
)
def test_classification(book: str, alt: str, stratum: str | None) -> None:
    assert fv.classify_openstax(book, alt) == stratum


def test_flow_charts_are_diagrams_not_charts() -> None:
    assert (
        fv.classify_openstax("biology", "A flow chart shows the hierarchy of organisms.")
        == fv.DIAGRAM
    )


def test_a_chart_needs_readable_values_in_its_reference() -> None:
    bare = _figure("a", alt="A line graph rises from left to right. " * 4)
    numbered = _figure("b")
    out = fv.openstax_candidates(
        "physics", "p", [bare, numbered], licence_url=CC_BY, excluded_pages=[]
    )
    assert [cand.id for cand, _ in out] == ["b"]


def test_chemistry_stratum_only_from_a_chemistry_book() -> None:
    alt = "The structure of ethanol is shown with a hydroxyl group and a methyl group."
    assert fv.classify_openstax("physics", alt) != fv.CHEMISTRY


# ---------------------------------------------------------------------- gold records


def _record(**over: object) -> dict:
    record: dict = {
        "id": "openstax/physics/p/fig-000",
        "stratum": fv.CHART,
        "source": "openstax",
        "page_key": "p",
        "split": "held",
        "source_url": "https://openstax.org/books/physics/pages/p",
        "licence_name": "CC BY 4.0",
        "licence_url": CC_BY,
        "attribution": "OpenStax, Physics",
        "image": {"url": "https://openstax.org/x", "sha256": "0" * 64},
        "reference_long_description": LONG_ALT,
        "reference_long_source": "openstax-alt",
        "reference_table": None,
    }
    record.update(over)
    return record


def test_valid_record_has_no_problems() -> None:
    assert fv.validate_record(_record()) == []


def test_record_with_a_non_cc_by_licence_is_rejected() -> None:
    problems = fv.validate_record(
        _record(licence_url="https://creativecommons.org/licenses/by-nc-sa/4.0/")
    )
    assert any("CC BY gate" in p for p in problems)


def test_record_without_a_licence_or_source_is_rejected() -> None:
    problems = fv.validate_record(_record(licence_url="", source_url=""))
    assert any("missing licence_url" in p for p in problems)
    assert any("missing source_url" in p for p in problems)


def test_table_rules() -> None:
    good = {"header": ["x", "y"], "rows": [["1", "2"], ["3", "4"]], "source": "source-data"}
    assert fv.validate_record(_record(reference_table=good)) == []
    ragged = dict(good, rows=[["1"], ["3", "4"]])
    assert any("not all 2 cells" in p for p in fv.validate_record(_record(reference_table=ragged)))
    other = dict(good, source="guess")
    assert any("table source" in p for p in fv.validate_record(_record(reference_table=other)))
    assert any(
        "only charts" in p
        for p in fv.validate_record(_record(stratum=fv.DIAGRAM, reference_table=good))
    )


def test_duplicate_ids_and_pages_are_reported() -> None:
    problems = fv.validate_gold([_record(), _record()])
    assert any("duplicate id" in p for p in problems)
    assert any("more than one figure from one page" in p for p in problems)


# ------------------------------------------------------------- the committed gold file


@pytest.mark.skipif(not (GOLD / "figures.jsonl").exists(), reason="gold not built")
def test_committed_gold_passes_every_gate() -> None:
    records = [
        json.loads(line)
        for line in (GOLD / "figures.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    assert 40 <= len(records) <= 60
    assert fv.validate_gold(records) == []
    assert (
        fv.strata_counts(records)
        == json.loads((GOLD / "selection.json").read_text("utf-8"))["strata_counts"]
    )


@pytest.mark.skipif(not (GOLD / "figures.jsonl").exists(), reason="gold not built")
def test_committed_gold_avoids_excluded_pages() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("pool", ROOT / "scripts" / "figures_v2_pool.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    excluded = module.known_page_slugs(ROOT / "data")
    for line in (GOLD / "figures.jsonl").read_text("utf-8").splitlines():
        record = json.loads(line)
        if record["source"] == "openstax":
            assert record["page_key"] not in excluded, record["id"]
