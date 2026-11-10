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


# ------------------------------------------------------------------- licence gates


# ---------------------------------------------------------------- exclusion and gates


def test_held_out_and_development_pages_are_excluded() -> None:
    page = [_figure("openstax/physics/p/fig-000")]
    assert (
        fv.openstax_candidates("physics", "p", page, licence_url=CC_BY, excluded_pages=["p", "q"])
        == []
    )


@pytest.mark.parametrize(
    ("caption", "out"),
    [
        ("A graph. (credit: NASA/JPL)", False),
        ("A graph. (credit: modification of work by ESO)", False),
        ("A graph (credit a: work by CDC; credit b: work by USFWS)", False),
        ("A graph, no credit.", False),
        ("A photo. (credit: modification of work by “X”/Wikimedia Commons)", True),
        ("A photo. (credit: Jane Doe, CC BY-SA 2.0)", True),
        ("A photo. (credit: used with permission)", True),
        ("A photo. (credit: James St. John)", True),
        ("A photo. (credit a: NASA; credit b: Scott Bauer)", True),
    ],
)
def test_third_party_credit_gate(caption: str, out: bool) -> None:
    assert fv.third_party_licence(caption) is out


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


def test_record_without_a_licence_or_source_is_rejected() -> None:
    problems = fv.validate_record(_record(licence_url="", source_url=""))
    assert any("missing licence_url" in p for p in problems)
    assert any("missing source_url" in p for p in problems)


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
