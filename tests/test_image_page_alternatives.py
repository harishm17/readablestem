"""Image pages: the reader's doubts about a symbol, kept as choices for the review (H7).

`Block.alternatives` is what the reader says (a symbol, which time it stands, other LaTeX);
`resolve_doubts` turns it into `ReadingAlternatives` on the element (spans of the stored
LaTeX); answers recorded before the field existed still parse; the prompt carries the new
definitions and the course notation reaches the request as fenced context.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from image_page_support import block, math, text
from readablestem.config import Settings
from readablestem.core.types import (
    ConversionOptions,
    Element,
    Kind,
    NotationEntry,
    ReadingAlternatives,
    SymbolAlternative,
)
from readablestem.models.fake import FakeVisionClient
from readablestem.models.prompts import load_prompt
from readablestem.pipeline import chem, image_page, options
from readablestem.pipeline.image_page import ImagePageOutput, SymbolDoubt

BOX = (0.0, 0.0, 612.0, 792.0)


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def doubt(symbol: str, options_: list[str], *, run: int = 0, occurrence: int = 1, kind="case"):
    return {
        "run": run,
        "symbol": symbol,
        "occurrence": occurrence,
        "kind": kind,
        "options": options_,
    }


def build(*blocks: dict[str, Any]) -> image_page.Built:
    out = ImagePageOutput.model_validate({"blocks": list(blocks)})
    built = image_page.build(0, out, box=BOX, png=None)
    assert built is not None
    return built


def equations(built: image_page.Built) -> list[Element]:
    return [e for e in built.elements if e.kind is Kind.EQUATION]


# ------------------------------------------------------------------ the answer's shape


def test_an_answer_recorded_before_the_field_existed_still_parses():
    old = block("equation", latex="x = 1", confidence="medium")
    assert "alternatives" not in old
    out = ImagePageOutput.model_validate({"blocks": [old]})
    assert out.blocks[0].alternatives == [] and out.blocks[0].confidence == "medium"


def test_the_schema_given_to_the_model_lists_the_field_as_optional():
    schema = ImagePageOutput.model_json_schema()
    block_schema = schema["$defs"]["Block"]
    assert "alternatives" in block_schema["properties"]
    assert "alternatives" not in block_schema["required"]


def test_a_doubt_is_strict_and_has_a_closed_set_of_kinds():
    ok = doubt("x", ["X"])
    assert SymbolDoubt.model_validate(ok).kind == "case"
    with pytest.raises(ValidationError):
        SymbolDoubt.model_validate({**ok, "kind": "vibes"})
    with pytest.raises(ValidationError):
        SymbolDoubt.model_validate({**ok, "extra": 1})


def test_a_block_called_high_that_lists_a_doubt_is_medium():
    out = ImagePageOutput.model_validate(
        {"blocks": [block("equation", latex="x", alternatives=[doubt("x", ["X"])])]}
    )
    assert out.blocks[0].confidence == "medium"
    low = block("equation", latex="x", confidence="low", alternatives=[doubt("x", ["X"])])
    assert ImagePageOutput.model_validate({"blocks": [low]}).blocks[0].confidence == "low"


def test_an_empty_doubt_does_not_change_the_confidence():
    empty = block("equation", latex="x", alternatives=[doubt("x", [" "]), doubt(" ", ["X"])])
    assert ImagePageOutput.model_validate({"blocks": [empty]}).blocks[0].confidence == "high"


# ------------------------------------------------------------------------- finding it


@pytest.mark.parametrize(
    ("latex", "symbol", "occurrence", "span"),
    [
        ("x + x", "x", 1, (0, 1)),
        ("x + x", "x", 2, (4, 5)),
        ("x + x", "x", 3, None),
        (r"\max x", "x", 1, (5, 6)),  # not inside the command
        (r"\rho v^2", r"\rho", 1, (0, 4)),
        ("f_i(y)", "f_i", 1, (0, 3)),
        ("f_{10}", "f_{10}", 1, (0, 6)),
        ("12", "1", 1, (0, 1)),
        ("12", "2", 1, (1, 2)),
        ("x", "", 1, None),
        ("x", "x", 0, None),
    ],
)
def test_find_symbol_counts_latex_tokens(latex, symbol, occurrence, span):
    assert image_page.find_symbol(latex, symbol, occurrence) == span


# ----------------------------------------------------------------------- resolving it


def test_a_display_equation_gets_its_doubts_as_spans_of_the_stored_reading():
    built = build(
        block(
            "equation",
            latex=r"p = \rho g h",
            alternatives=[doubt("p", [r"\rho", "P"], kind="greek_latin")],
        )
    )
    (eq,) = equations(built)
    assert eq.alternatives is not None and eq.page_latex == r"p = \rho g h"
    assert eq.alternatives.latex == eq.page_latex
    (only,) = eq.alternatives.symbols
    assert (only.start, only.end, only.symbol, only.kind) == (0, 1, "p", "greek_latin")
    assert only.options == [r"\rho", "P"]
    assert eq.alternatives.apply(0, 1) == r"P = \rho g h"
    assert eq.alternatives.choices == 2


def test_an_inline_formula_is_found_by_the_position_of_its_run():
    built = build(
        block(
            "paragraph",
            confidence="medium",
            runs=[text("Let "), math("k"), text(" and "), math("v^2"), text(" be real.")],
            alternatives=[
                doubt("k", ["K"], run=1),
                doubt("v", [r"\nu"], run=3, kind="greek_latin"),
                doubt("k", ["h"], run=2),  # a text run: nothing there
            ],
        )
    )
    inline = [e for e in equations(built) if e.display == "inline"]
    assert [e.alternatives.apply(0, 0) for e in inline if e.alternatives] == ["K", r"\nu^2"]


def test_a_lone_formula_paragraph_keeps_its_doubts_when_it_becomes_an_equation():
    built = build(
        block(
            "paragraph",
            confidence="medium",
            runs=[text(" "), math("x^2")],
            alternatives=[doubt("x", ["X"], run=1)],
        )
    )
    (eq,) = equations(built)
    assert eq.display == "block" and eq.alternatives is not None
    assert eq.alternatives.apply(0, 0) == "X^2"


def test_a_script_lost_or_added_is_an_option_that_replaces_the_stretch():
    built = build(
        block(
            "equation",
            latex="f(y)",
            confidence="medium",
            alternatives=[doubt("f", ["f^{-1}"], kind="script")],
        )
    )
    (eq,) = equations(built)
    assert eq.alternatives is not None and eq.alternatives.apply(0, 0) == "f^{-1}(y)"


def test_a_doubt_that_points_nowhere_or_changes_nothing_is_dropped():
    built = build(
        block(
            "equation",
            latex="a + b",
            confidence="medium",
            alternatives=[
                doubt("z", ["Z"]),  # not in the reading
                doubt("a", ["a", " ", ""]),  # nothing but itself
                doubt("b", ["b", "B", "B"]),  # one real option, once
            ],
        )
    )
    (eq,) = equations(built)
    assert eq.alternatives is not None
    assert [(s.symbol, s.options) for s in eq.alternatives.symbols] == [("b", ["B"])]


def test_an_option_that_breaks_the_equation_is_dropped():
    built = build(
        block(
            "equation",
            latex=r"x + \frac{1}{2}",
            confidence="medium",
            alternatives=[doubt("x", [r"\frac{", "X"])],
        )
    )
    (eq,) = equations(built)
    assert eq.alternatives is not None and eq.alternatives.symbols[0].options == ["X"]


def test_no_option_left_is_no_alternatives_and_the_block_is_still_medium():
    built = build(
        block(
            "equation",
            latex="x",
            alternatives=[doubt("x", [r"\frac{"])],
        )
    )
    (eq,) = equations(built)
    assert eq.alternatives is None and eq.image_confidence == "medium"


def test_the_count_of_doubts_and_of_options_is_bounded():
    many = [doubt(s, ["A", "B", "C", "D", "E"]) for s in "abcde"]
    built = build(block("equation", latex="a b c d e", confidence="medium", alternatives=many))
    (eq,) = equations(built)
    assert eq.alternatives is not None
    assert len(eq.alternatives.symbols) == 3
    assert all(len(s.options) == 3 for s in eq.alternatives.symbols)


def test_overlapping_doubts_keep_the_first():
    built = build(
        block(
            "equation",
            latex="f_i + 1",
            confidence="medium",
            alternatives=[doubt("f_i", ["g"], kind="script"), doubt("f", ["F"])],
        )
    )
    (eq,) = equations(built)
    assert eq.alternatives is not None
    assert [s.symbol for s in eq.alternatives.symbols] == ["f_i"]


def test_a_doubt_in_chemistry_points_into_the_written_out_reading():
    built = build(
        block(
            "equation",
            latex=r"\ce{CO2 + H2O}",
            confidence="medium",
            alternatives=[doubt("O", ["o"], occurrence=1)],
        )
    )
    (eq,) = equations(built)
    assert eq.alternatives is not None
    assert eq.alternatives.latex == eq.page_latex != r"\ce{CO2 + H2O}"
    assert eq.alternatives.symbols[0].symbol == "O"


# ---------------------------------------------------------------- the stored data shape


def test_reading_alternatives_refuse_a_span_that_is_not_the_symbol():
    good = SymbolAlternative(start=0, end=1, symbol="x", kind="case", options=["X"])
    assert ReadingAlternatives(latex="x + 1", symbols=[good]).apply(0, 0) == "X + 1"
    bad = SymbolAlternative(start=0, end=1, symbol="y", kind="case", options=["Y"])
    with pytest.raises(ValidationError):
        ReadingAlternatives(latex="x + 1", symbols=[bad])
    late = SymbolAlternative(start=9, end=10, symbol="x", kind="case", options=["X"])
    with pytest.raises(ValidationError):
        ReadingAlternatives(latex="x + 1", symbols=[late])


def test_reading_alternatives_refuse_overlap_and_disorder_and_empty():
    a = SymbolAlternative(start=0, end=3, symbol="f_i", options=["g"])
    b = SymbolAlternative(start=2, end=3, symbol="i", options=["j"])
    with pytest.raises(ValidationError):
        ReadingAlternatives(latex="f_i", symbols=[a, b])
    with pytest.raises(ValidationError):
        ReadingAlternatives(latex="f_i", symbols=[])
    with pytest.raises(ValidationError):
        SymbolAlternative(start=2, end=2, symbol="", options=["x"])
    with pytest.raises(ValidationError):
        SymbolAlternative(start=0, end=1, symbol="f", options=[])


def test_an_element_round_trips_with_and_without_alternatives():
    built = build(
        block("equation", latex="x", confidence="medium", alternatives=[doubt("x", ["X"])]),
        block("equation", latex="y"),
    )
    with_doubt, without = equations(built)
    assert "alternatives" not in without.model_dump(mode="json")
    again = Element.model_validate_json(with_doubt.model_dump_json())
    assert again.alternatives == with_doubt.alternatives
    assert Element.model_validate_json(without.model_dump_json()).alternatives is None


# ------------------------------------------------------------------- the prompt and request


def test_the_prompt_defines_the_new_confidence_and_drops_the_greek_bias():
    prompt = load_prompt("image_page").text
    assert "context is physical or mathematical" not in prompt
    assert "needed context to settle" not in prompt
    assert "`alternatives`" in prompt and "occurrence" in prompt
    assert "one or two doubts you can name" in prompt
    assert "## Chemistry" in prompt and r"\ce{2 H2 + O2 -> 2 H2O}" in prompt


def test_every_alternative_kind_is_named_in_the_prompt():
    prompt = load_prompt("image_page").text
    for kind in ("case", "greek_latin", "digit_letter", "operator", "script"):
        assert f"`{kind}`" in prompt


def test_the_chemistry_the_prompt_teaches_is_accepted_by_the_converter():
    for source in (
        r"\ce{H2O}",
        r"\ce{[Cu(NH3)4]^2+}",
        r"\ce{SO4^2-}",
        r"\ce{NaCl(aq)}",
        r"\ce{2 H2 + O2 -> 2 H2O}",
        r"\ce{CaCO3(s) ->[\Delta] CaO(s) + CO2(g)}",
        r"\ce{^{14}_{6}C}",
        r"\ce{^{0}_{-1}e}",
        r"\ce{Hg2^2+}",
        r"\ce{CH3CO2-}",
    ):
        assert r"\ce" not in chem.expand_ce(source)
    assert chem.expand_ce(r"K = \frac{[\ce{NH3}]^2}{[\ce{N2}][\ce{H2}]^3}")


def test_without_course_notation_the_request_is_what_it_was():
    request = image_page.build_request(settings(), b"png")
    assert request.context_text is None
    assert request.prompt_version == image_page.PROMPT_VERSION
    assert request.instructions == load_prompt("image_page").text


def test_course_notation_goes_as_context_with_its_own_prompt_section():
    entries = [
        NotationEntry(symbol="bold d", canonical="\U0001d41d", meaning="vector", source="user")
    ]
    request = image_page.build_request(settings(), b"png", entries)
    assert request.context_text is not None and "[course notation" in request.context_text
    assert "vector" in request.context_text
    assert request.prompt_version.startswith(image_page.PROMPT_VERSION)
    assert "image_page_notation" in request.prompt_version
    assert "## Course notation" in request.instructions
    assert request.instructions.startswith(load_prompt("image_page").text.rstrip("\n"))


def test_run_reads_the_notation_of_the_conversion_on_its_own_thread():
    entry = NotationEntry(symbol="bold d", canonical="\U0001d41d", meaning="vector", source="user")
    client = FakeVisionClient(
        responders={
            "image_page": lambda request: {"blocks": [block("paragraph", runs=[text("a")])]}
        }
    )
    on = ConversionOptions(notation=[entry], notation_memory=True)
    with options.using(on):
        result = image_page.run(client, settings(), {0: lambda: b"png-bytes"})
    assert result.notation == (entry,)
    assert "[course notation" in (client.requests[0].context_text or "")
    off = FakeVisionClient(
        responders={
            "image_page": lambda request: {"blocks": [block("paragraph", runs=[text("a")])]}
        }
    )
    image_page.run(off, settings(), {0: lambda: b"png-bytes"})
    assert off.requests[0].context_text is None


def test_the_crops_prompt_agrees_with_the_page_prompt_on_confidence_and_chemistry():
    crops = load_prompt("image_page_crops").text
    assert "one or two doubts you can name" in crops
    assert r"\ce{" in crops
    assert "context is physical" not in crops
