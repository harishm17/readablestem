"""pipeline.language: detecting the document's language and passages in another one,
and checking language tags.

Detection runs on words only: inline equations, numbers, single letters (variables),
symbols and code are left out, and short or maths-heavy text gives no answer.
"""

from __future__ import annotations

import pytest

from a11y_stem.core.types import inline_marker
from a11y_stem.pipeline import language
from a11y_stem.pipeline.language import (
    document_language,
    document_tag,
    is_language_tag,
    language_name,
    normalise_tag,
    passage_language,
    prose,
)

ENGLISH = [
    "The velocity of the particle is the derivative of its position with respect to time.",
    "Find the eigenvalues of the matrix and show that they are real.",
    f"A cart that starts with speed {inline_marker('p1-e1')} speeds up at a steady rate.",
    "Kinetic energy is conserved in an elastic collision between two carts.",
]
FRENCH = (
    "La vitesse moyenne est le quotient du déplacement par la durée du trajet, et elle "
    "s\u2019exprime en mètres par seconde."
)
GERMAN = "Die Geschwindigkeit des Teilchens ist die Ableitung seines Ortes nach der Zeit."


# --------------------------------------------------------------------------- prose


def test_prose_keeps_words_and_leaves_maths_and_code_out():
    text = f"Let {inline_marker('p1-e2')} be 3.5 m/s, x = 2 and f(x) = x_1; the speed\u2019s rate."
    words, word_count, tokens = prose(text)
    assert words == "Let be and the speed\u2019s rate"
    assert (word_count, tokens) == (6, 15)


def test_prose_leaves_out_styled_maths_letters():
    assert prose("the vector \U0001d41d\U0001d41f points up")[0] == "the vector points up"


# --------------------------------------------------------------------------- document


def test_an_english_document_is_detected():
    assert document_language(ENGLISH) == "en"


def test_a_french_document_is_detected():
    assert document_language([FRENCH, FRENCH.replace("moyenne", "instantanée")]) == "fr"


def test_too_little_text_gives_no_language():
    assert document_language(["Speeds are below.", "The speed doubles."]) is None


def test_a_page_of_maths_gives_no_language():
    maths = [" ".join(inline_marker(f"p1-e{i}") for i in range(5)) + " = 0" for _ in range(20)]
    assert document_language(maths) is None


# --------------------------------------------------------------------------- passages


@pytest.mark.parametrize(("text", "expected"), [(FRENCH, "fr"), (GERMAN, "de")])
def test_a_passage_in_another_language_is_marked(text, expected):
    assert passage_language(text, "en") == expected
    assert passage_language(text, "en-US") == expected


def test_a_passage_in_the_document_language_is_not_marked():
    assert passage_language(FRENCH, "fr") is None
    assert passage_language(FRENCH, "fr-CA") is None
    for text in ENGLISH:
        assert passage_language(text, "en") is None


@pytest.mark.parametrize(
    "text",
    [
        "Wir müssen wissen.",  # too short
        "Soit " + " ".join(inline_marker(f"p1-e{i}") for i in range(8)) + " une fonction.",
        "for i in range(10): print(i); total += values[i] * weights[i]",
        "Let x and y be real numbers such that x plus y equals one, and solve for x.",
        "Proof. Suppose x > 0 and y > 0. Then xy > 0 by the axiom of order.",
    ],
)
def test_short_maths_heavy_or_english_text_is_not_marked(text):
    assert passage_language(text, "en") is None


# --------------------------------------------------------------------------- tags


@pytest.mark.parametrize(
    "tag",
    ["en", "fr", "pt-BR", "zh-Hant-TW", "es-419", "de-CH-1996", "en-u-nu-thai", "x-private",
     "sl-rozaj-biske", "EN-us"],
)  # fmt: skip
def test_well_formed_tags(tag):
    assert is_language_tag(tag)


@pytest.mark.parametrize(
    "tag", ["", "e", "fr_FR", "english language", "en-", "-en", "en--US", "a" * 9, "en-US-x"]
)
def test_malformed_tags(tag):
    assert not is_language_tag(tag)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("EN_us", "en-US"),
        ("zh-hant-tw", "zh-Hant-TW"),
        ("en-u-nu-thai", "en-u-nu-thai"),
        ("x-Private", "x-private"),
        (" fr ", "fr"),
        ("fr FR", None),
    ],
)
def test_tags_are_written_in_their_usual_case(value, expected):
    assert normalise_tag(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("French", "fr"),
        ("  english ", "en"),
        ("pt-br", "pt-BR"),
        ("de", "de"),
        ("x-none", None),
        ("und", None),
        ("Englsh", None),
        ("", None),
    ],
)
def test_a_document_needs_a_real_language(value, expected):
    assert document_tag(value) == expected


def test_language_names():
    assert language_name("fr-CA") == "French"
    assert language_name("tlh") == "tlh"
    assert language.DEFAULT_LANG == "en"
