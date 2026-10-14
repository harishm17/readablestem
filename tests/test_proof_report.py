"""PROOF.md building blocks: number formats, tables, and the wording guard."""

from __future__ import annotations

import pytest

from readablestem.proof.report import (
    Section,
    Table,
    bullets,
    fraction,
    interval,
    num,
    usd,
    wording_problems,
)


def test_numbers_have_fixed_formats():
    assert num(None) == "n/a"
    assert num(1234) == "1,234"  # integers get separators
    assert num(0.5) == "0.5000"
    assert num(2 / 3, 2) == "0.67"
    assert usd(None) == "n/a" and usd(0.12345) == "$0.1235" and usd(1.5, 2) == "$1.50"
    assert interval(0.1, 0.25) == "[0.1000, 0.2500]"
    assert interval(None, 0.25) == "n/a"
    assert fraction(3, 10) == "3/10"
    assert fraction(1.5, 10) == "1.50/10"
    assert fraction(1200, 3000) == "1,200/3,000"


def test_a_table_renders_the_same_bytes_and_escapes_cells():
    table = Table("T", ["a", "b"], [["x|y", "line\nbreak"], ["only one"]], note="A note.")
    assert table.markdown() == (
        "**T**\n\n| a | b |\n|---|---|\n| x\\|y | line break |\n| only one |  |\n\nA note."
    )
    assert Table("", ["h"], [["v"]]).markdown().startswith("| h |")


def test_a_section_joins_text_and_tables():
    section = Section("Head", ["Text.", Table("", ["h"], [["v"]])], level=3)
    assert section.markdown() == "### Head\n\nText.\n\n| h |\n|---|\n| v |"
    assert bullets(["a", "b"]) == "- a\n- b"


@pytest.mark.parametrize(
    "text",
    [
        "This output is compliant.",
        "WCAG conformance achieved",
        "ADA-compliant pages",
        "The result was verified by hand.",
        "We guarantee access.",
        "A certified document",
        "Compliance with the standard",
    ],
)
def test_the_wording_guard_finds_what_a_report_must_never_say(text):
    assert wording_problems(text)


def test_the_wording_guard_names_the_line():
    problems = wording_problems("fine\nstill fine\nnot verified")
    assert problems == ["line 3: 'verified'"]


def test_plain_words_and_quoted_blocks_pass():
    assert wording_problems("The tool assists. Nothing here measures conformance with WCAG.") == []
    quoted = "before\n<!-- quote -->\nmake it compliant\n<!-- /quote -->\nafter verified"
    assert wording_problems(quoted) == ["line 5: 'verified'"]  # quote exempt, line kept


def test_the_fixed_disclaimer_passes_its_own_guard():
    from readablestem.proof.document import DISCLAIMER

    assert wording_problems(DISCLAIMER) == []
