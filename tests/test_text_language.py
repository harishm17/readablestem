"""pipeline.language: detecting the document's language and passages in another one,
and checking language tags.

Detection runs on words only: inline equations, numbers, single letters (variables),
symbols and code are left out, and short or maths-heavy text gives no answer.
"""

from __future__ import annotations

import pytest

from readablestem.core.types import inline_marker
from readablestem.pipeline.language import (
    document_language,
    is_language_tag,
    passage_language,
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


# --------------------------------------------------------------------------- document


def test_an_english_document_is_detected():
    assert document_language(ENGLISH) == "en"


def test_a_french_document_is_detected():
    more = (
        "On distingue la cinématique, qui décrit le mouvement sans en chercher les causes, "
        "et la dynamique, qui le relie aux forces."
    )
    assert document_language([FRENCH, FRENCH.replace("moyenne", "instantanée"), more]) == "fr"


def test_another_language_needs_more_text_than_english():
    # Two sentences are enough for English, not for another language: the default
    # (English, with "check the language" on the job page) is the safer guess.
    assert document_language([FRENCH, FRENCH.replace("moyenne", "instantanée")]) is None


@pytest.mark.parametrize(
    "texts",
    [
        # A one-page reagent list and a periodic-table group: py3langid says Latin.
        ["Lab 4: Salts", "Sodium chloride", "Potassium permanganate", "Calcium carbonate",
         "Magnesium sulfate", "Ammonium nitrate", "Copper sulfate pentahydrate",
         "Ferric chloride"],
        ["Group 1", "Lithium", "Sodium", "Potassium", "Rubidium", "Caesium", "Francium",
         "Group 2", "Beryllium", "Magnesium", "Calcium", "Strontium", "Barium", "Radium"],
    ],
)  # fmt: skip
def test_lists_of_english_terms_are_not_latin(texts):
    assert document_language(texts) in (None, "en")


def test_too_little_text_gives_no_language():
    assert document_language(["Speeds are below.", "The speed doubles."]) is None


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


# --------------------------------------------------------------------------- tags


@pytest.mark.parametrize(
    "tag", ["", "e", "fr_FR", "english language", "en-", "-en", "en--US", "a" * 9, "en-US-x"]
)
def test_malformed_tags(tag):
    assert not is_language_tag(tag)
