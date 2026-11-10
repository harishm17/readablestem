"""Inventions planted by hand: the plan is applied, found, and the blind spot is measured."""

from __future__ import annotations

from readablestem.eval.invented import plant
from readablestem.eval.invented import text as T

PAGE = """2.1 Speed and Velocity
Average speed is the distance traveled divided by the time it takes to travel it.
A car travels 150 km in 3.2 h, so its average speed is about 47 km/h on that trip.
A scalar quantity is fully described by its magnitude while a vector also needs a direction.
Newton's second law says that the net force on an object equals its mass times acceleration.
v = d / t
Access for free at openstax.org. Physics (OpenStax), CC BY 4.0. Page 1 of 1
"""
REFERENCE = T.Reference.from_pages([PAGE])

HTML = (
    "<!doctype html><html lang='en'><body>"
    "<h2>2.1 Speed and Velocity</h2>"
    "<p>Average speed is the distance traveled divided by the time it takes to travel it.</p>"
    "<p>A car travels 150 km in 3.2 h, so its average speed is about 47 km/h on that trip.</p>"
    "<p>A scalar quantity is fully described by its magnitude while a vector also needs a "
    "direction.</p>"
    "<p>Newton's second law says that the net force on an object equals its mass times "
    "acceleration.</p>"
    "<math><mi>v</mi><mo>=</mo><mfrac><mi>d</mi><mi>t</mi></mfrac></math>"
    "</body></html>"
)

PLAN = {
    "sentences": ["Students usually find it easier to learn this topic after watching a video."],
    "headings": ["Study Tips for This Section"],
    "equations": [
        "<math><mi>K</mi><mo>=</mo><mfrac><mn>1</mn><mn>2</mn></mfrac><mi>m</mi>"
        "<msup><mi>v</mi><mn>2</mn></msup></math>"
    ],
    "edits": [
        {"kind": "number", "find": "150 km in 3.2 h", "replace": "150 km in 3.9 h"},
        {"kind": "word", "find": "A scalar quantity is", "replace": "A vector quantity is"},
    ],
}


def test_planted_text_is_found_and_the_blind_spot_is_measured():
    # One word changed inside a copied sentence keeps most of its 4-grams: not found.
    swap = {"kind": "word", "find": "its magnitude while", "replace": "its direction while"}
    plan = {**PLAN, "edits": [PLAN["edits"][0], swap]}
    result = plant.self_check(REFERENCE, HTML, plan)
    assert result["found"]["sentence"] == result["planted"]["sentence"] == 1
    assert result["found"]["heading"] == 1
    assert result["found"]["equation"] == 1
    assert result["found"]["number"] == 1  # 3.9 is not printed in the PDF
    assert result["found"]["word"] == 0 and result["planted"]["word"] == 1
    assert result["recall"]["word"] == 0.0
    assert result["newly_flagged_unexplained"] == 0
    assert result["plan_in_reference"]["numbers_the_pdf_prints"] == 0
