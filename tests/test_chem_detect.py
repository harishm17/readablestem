"""Chemistry in running text (`pipeline.chem` detection): what is a formula, an ion, a state
symbol, an arrow, a reaction, and what is not (units, variables, footnote marks, words).

Words are given as pieces, `(text, kind)` with kind None for text at body size and "sub" or
"sup" for a lowered or raised piece, as `layout._script_glyphs` hands them over."""

from __future__ import annotations

import pytest

from readablestem.pipeline import chem
from readablestem.pipeline.chem import Word

Piece = str | tuple[str, str]


def word(*pieces: Piece, start: int = 0) -> Word:
    text, kinds = "", []
    for piece in pieces:
        body, kind = (piece, None) if isinstance(piece, str) else piece
        text += body
        kinds += [kind] * len(body)
    return Word(start, start + len(text), text, tuple(kinds))


def label(*pieces: Piece) -> str:
    return chem.label_word(word(*pieces)).label


def cut(*pieces: Piece) -> str:
    """The part of the word that is the formula, sentence marks and wrapping cut off."""
    item = chem.label_word(word(*pieces))
    return item.word.text[item.lo : item.hi]


# ------------------------------------------------------------------------- formulas


@pytest.mark.parametrize(
    "pieces",
    [
        ("H", ("2", "sub"), "O"),
        ("CO", ("2", "sub")),
        ("C", ("6", "sub"), "H", ("12", "sub"), "O", ("6", "sub")),
        ("Ca(OH)", ("2", "sub")),
        ("Al", ("2", "sub"), "(SO", ("4", "sub"), ")", ("3", "sub")),
        ("NH", ("4", "sub"), ("+", "sup")),
        ("Na", ("+", "sup")),
        ("OH", ("-", "sup")),
        ("Cl", ("−", "sup")),
        ("Ca", ("2+", "sup")),
        ("Fe", ("3+", "sup")),
        ("SO", ("4", "sub"), ("2−", "sup")),
        ("Hg", ("2", "sub"), ("2+", "sup")),
        ("[Al(H", ("2", "sub"), "O)", ("6", "sub"), "]", ("3+", "sup")),
        ("[H", ("3", "sub"), "O", ("+", "sup"), "]"),
        ("[Zn", ("2+", "sup"), "]"),
        ("e", ("−", "sup")),
        ("CuSO", ("4", "sub"), "·5H", ("2", "sub"), "O"),
        ("NaCl(aq)",),  # a state symbol is evidence too
        ("Mg(s)",),
        ("H", ("2", "sub"), "O(l)"),
        ("CO", ("2", "sub"), "(g)"),
    ],
)
def test_formulas_and_ions_are_formulas(pieces):
    assert label(*pieces) == "formula"


@pytest.mark.parametrize(
    "pieces",
    [
        ("c", "m", ("3", "sup")),  # a unit
        ("m", ("2", "sup")),
        ("m", ("3", "sup")),
        ("K", ("w", "sub")),  # an equilibrium constant: a letter subscript
        ("K", ("−1", "sup")),  # an exponent: K to the minus one
        ("J", ("−1", "sup")),
        ("mol", ("−1", "sup")),
        ("Nature", ("1", "sup")),  # a footnote mark
        ("H", ("2", "sup")),  # a raised number with no sign is an exponent
        ("NASA", ("1", "sup")),
        ("x", ("2", "sub")),  # not an element
        ("Hello",),
        ("Water",),
        ("The",),
        ("pH",),
        ("A", ("2", "sub")),  # A is no element
        ("Xx", ("2", "sub")),
        ("H", ("x", "sub")),  # a letter is not a count
        ("O", ("+-", "sup")),
    ],
)
def test_units_variables_footnotes_and_words_are_not_formulas(pieces):
    assert label(*pieces) != "formula"


def test_plain_text_with_no_scripts_is_not_a_formula():
    # H2O set at one size cannot be told from a word.
    assert label("H2O") == "other"
    assert label("CO2") == "other"
    assert label("HCl") == "bare"  # elements, no evidence: counts only inside a reaction
    assert label("Hello") == "other"


def test_sentence_marks_and_wrapping_brackets_are_not_part_of_the_formula():
    assert cut("H", ("2", "sub"), "O,") == "H2O"
    assert cut("H", ("2", "sub"), "O.") == "H2O"
    assert cut("(NaHCO", ("3", "sub"), ")") == "NaHCO3"
    assert cut("(NaHCO", ("3", "sub"), "),") == "NaHCO3"
    assert cut("Ca", ("2+", "sup"), ";") == "Ca2+"
    assert cut("“Na", ("+", "sup")) == "Na+"
    # But a group that carries the count is part of it.
    assert cut("Al", ("2", "sub"), "(SO", ("4", "sub"), ")", ("3", "sub")) == "Al2(SO4)3"


def test_state_symbols_alone_and_arrows_and_numbers_and_plus():
    assert label("(aq)") == "state"
    assert label("(s)") == "state"
    assert label("aq") == "other"
    assert label("−→") == "arrow"
    assert label("⇌") == "arrow"
    assert label("⟶") == "arrow"
    assert label("+") == "plus"
    assert label("2") == "number"
    assert label("1/2") == "number"
    assert label("x") == "other"


# ------------------------------------------------------------------------- reactions


def row(*words: Word) -> list[chem.Labelled]:
    """Words laid out one after another with a character between them."""
    placed, at = [], 0
    for w in words:
        placed.append(Word(at, at + len(w.text), w.text, w.kinds))
        at += len(w.text) + 1
    return chem.label_words(placed)


AG = word("Ag", ("+", "sup"), "(aq)")
CL = word("Cl", ("−", "sup"), "(aq)")
AGCL = word("AgCl(s)")
PLUS = word("+")
ARROW = word("−→")


def test_a_reaction_is_an_arrow_with_species_on_both_sides():
    labelled = row(AG, PLUS, CL, ARROW, AGCL)
    (span,) = chem.reactions(labelled)
    assert span == (0, labelled[-1].last)


def test_coefficients_and_bare_words_inside_a_reaction_belong_to_it():
    labelled = row(
        word("2"),
        word("H", ("2", "sub")),
        PLUS,
        word("O", ("2", "sub")),
        word("⇌"),
        word("2"),
        word("H", ("2", "sub"), "O"),
    )
    assert chem.reactions(labelled) == [(0, labelled[-1].last)]
    labelled = row(
        word("HCl"), PLUS, word("NaOH"), ARROW, word("NaCl"), PLUS, word("H", ("2", "sub"), "O")
    )
    assert chem.reactions(labelled) == [(0, labelled[-1].last)]


def test_a_reaction_stops_at_the_words_of_the_sentence():
    labelled = row(
        word("When"),
        word("Mg(s)"),
        PLUS,
        word("HCl(aq)"),
        ARROW,
        word("MgCl", ("2", "sub")),
        word("forms."),
    )
    (start, end) = chem.reactions(labelled)[0]
    assert start == labelled[1].first and end == labelled[5].last


def test_a_trailing_number_is_not_part_of_the_reaction():
    labelled = row(word("H", ("2", "sub")), ARROW, word("H", ("2", "sub"), "O"), word("2"))
    (span,) = chem.reactions(labelled)
    assert span[1] == labelled[2].last


@pytest.mark.parametrize(
    "words",
    [
        [word("x"), word("→"), word("0")],  # a limit
        [word("f"), word("→"), word("g")],
        [word("He"), word("→"), word("2")],  # bare words only, no formula
        [word("NaCl"), ARROW, word("KCl")],
        [word("H", ("2", "sub"), "O"), ARROW],  # nothing on the right
        [ARROW, word("H", ("2", "sub"), "O")],  # nothing on the left
        [word("Na", ("+", "sup")), word("and"), word("Cl", ("−", "sup"))],  # no arrow
    ],
)
def test_arrows_without_chemistry_on_both_sides_are_not_reactions(words):
    assert chem.reactions(row(*words)) == []


def test_formulas_take_in_the_state_symbol_after_them():
    labelled = row(word("Cu", ("2+", "sup")), word("(aq)"), word("and"), word("Na", ("+", "sup")))
    spans = chem.formulas(labelled)
    assert spans == [(0, labelled[1].last), (labelled[3].first, labelled[3].last)]
