"""The invented-content rules of PREREG 7.5 on a one-page PDF written here."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from parsers_bench import invented
from parsers_bench.intermediate import Block, Heading, Intermediate

SOURCE_LINES = [
    "Speed and Velocity",
    "The car travels 150 kilometers in 3.2 hours along the road.",
    "Its average speed for the whole trip is 47 km/h in this case.",
    "v = d / t",
]


@pytest.fixture(scope="module")
def reference(tmp_path_factory: pytest.TempPathFactory) -> invented.Reference:
    path = tmp_path_factory.mktemp("pdf") / "source.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        for i, line in enumerate(SOURCE_LINES):
            page.insert_text((72, 100 + 20 * i), line, fontsize=11)
        document.save(path)
    return invented.reference_from_pdf(Path(path))


def doc(*blocks: tuple[str, str], headings: tuple[str, ...] = ()) -> Intermediate:
    out = Intermediate(arm="t", section="s")
    out.blocks = [Block(i, kind, text) for i, (kind, text) in enumerate(blocks)]
    out.headings = [Heading(100 + i, 2, h) for i, h in enumerate(headings)]
    return out


def test_a_copied_sentence_is_supported_and_an_invented_one_is_not(
    reference: invented.Reference,
) -> None:
    out = doc(
        ("para", "The car travels 150 kilometers in 3.2 hours along the road."),
        ("para", "Students should always remember to carry water on very long journeys."),
    )
    rates = invented.text_rates(out, reference)["prose_sentence"]
    assert (rates["n"], rates["unsupported"]) == (2, 1)


def test_short_sentences_are_not_units(reference: invented.Reference) -> None:
    out = doc(("para", "Too short to count. Also short."))
    assert invented.text_rates(out, reference)["prose_sentence"]["n"] == 0


def test_a_number_missing_from_the_pdf_is_invented_and_one_present_is_not(
    reference: invented.Reference,
) -> None:
    out = doc(("para", "It goes 150 kilometers in 3.2 hours, then 99 more."))
    rates = invented.text_rates(out, reference)["number"]
    assert (rates["n"], rates["unsupported"]) == (3, 1)


def test_numbers_in_a_heading_count_and_figure_text_does_not(
    reference: invented.Reference,
) -> None:
    out = doc(("figure_text", "2400 2000 1600"), headings=("Section 77",))
    rates = invented.text_rates(out, reference)
    assert (rates["number"]["n"], rates["number"]["unsupported"]) == (1, 1)
    assert rates["figure_text_numbers_not_in_text_layer"]["n"] == 3
    assert rates["figure_text_numbers_not_in_text_layer"]["not_in_text_layer"] == 3


def test_furniture_is_not_a_unit(reference: invented.Reference) -> None:
    out = doc(("furniture", "A running footer with seven or more words in it 12."))
    rates = invented.text_rates(out, reference)
    assert rates["prose_sentence"]["n"] == 0
    assert rates["number"]["n"] == 0


def test_a_heading_not_in_the_pdf_is_invented(reference: invented.Reference) -> None:
    out = doc(headings=("Speed and Velocity", "A Heading Nobody Printed"))
    rates = invented.text_rates(out, reference)["heading"]
    assert (rates["n"], rates["unsupported"]) == (2, 1)


def test_equation_with_an_added_symbol_is_flagged_and_an_unsupported_one_is_unaligned(
    reference: invented.Reference,
) -> None:
    rates = invented.equation_rates(
        aligned_output=["vdtx", "vdt"],
        aligned_gold=["vdt", "vdt"],
        unaligned=["vdt", "qzw", "ab"],
        ref=reference,
    )
    assert (rates["equation_aligned"]["n"], rates["equation_aligned"]["unsupported"]) == (2, 1)
    assert (rates["equation_unaligned"]["n"], rates["equation_unaligned"]["unsupported"]) == (2, 1)
    assert rates["equation_unaligned_too_short_to_check"] == 1
    assert rates["added_symbols_per_1000_output_symbols"] == pytest.approx(1000 / 7)


def test_rates_carry_a_wilson_interval() -> None:
    rate = invented.rate(1, 10)
    assert rate["rate"] == 0.1
    assert rate["wilson95"][0] < 0.1 < rate["wilson95"][1]
    assert invented.rate(0, 0)["rate"] is None
