"""Scorers for the dev-v2 classes that had gold but no scorer (prose scripts, code
blocks, glossary entries, hyphenated line ends): what each result means, and the gold
self-check scoring 1.0 on the committed development set."""

from __future__ import annotations

from pathlib import Path

import pytest

from readablestem.eval import prose_classes as pc
from readablestem.eval.gold_scoring import iter_gold_records

DEV = Path(__file__).parent.parent / "data" / "dev-v2" / "dev"


def page(body: str) -> str:
    return f"<html><head><title>t</title></head><body>{body}</body></html>"


def script(text: str, *runs: tuple[str, str, str], cls: str = "prose_chem_script") -> dict:
    return {
        "kind": "script",
        "id": "g/sc-0",
        "text": text,
        "runs": [{"script": s, "value": v, "class": cls} for s, v, _ in runs],
        "classes": [cls],
    }


def result(body: str, record: dict) -> list[str]:
    return [row["result"] for row in pc.score_scripts(pc.parse_html(page(body)), [record])]


# ---------------------------------------------------------------------- scripts

ZN = script("only the concentration of Zn^{2+} changes.", ("sup", "2+", ""))


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("<p>only the concentration of Zn<sup>2+</sup> changes.</p>", "sub_sup"),
        (
            "<p>only the concentration of <math><msup><mi>Zn</mi><mrow><mn>2</mn><mo>+</mo>"
            "</mrow></msup></math> changes.</p>",
            "math",
        ),
        ("<p>only the concentration of Zn2+ changes.</p>", "flattened"),
        ("<p>only the concentration of Zn<sub>2+</sub> changes.</p>", "flattened"),  # wrong kind
        ("<p>nothing like it</p>", "lost"),
    ],
)
def test_a_script_run_is_judged_by_how_the_output_shows_it(body, expected):
    assert result(body, ZN) == [expected]


def test_a_nuclide_prefix_script_stands_before_its_symbol():
    record = script("an isotope of oxygen, ^{17}O. It was made", ("sup", "17", ""))
    assert result("<p>an isotope of oxygen, <sup>17</sup>O. It was made</p>", record) == ["sub_sup"]
    assert result("<p>an isotope of oxygen, 17O. It was made</p>", record) == ["flattened"]
    inline = (
        "<p>an isotope of oxygen, <math><mmultiscripts><mi>O</mi><none/><none/><mprescripts/>"
        "<none/><mn>17</mn></mmultiscripts></math>. It was made</p>"
    )
    assert result(inline, record) == ["math"]


# ------------------------------------------------------------------------ code

CODE = {
    "kind": "code",
    "id": "g/cd-0",
    "language": "python",
    "text": "def f(x):\n    if x:\n        return 1\n    return 0",
}


def code_result(body: str) -> list[str]:
    rows, _ = pc.score_code(pc.parse_html(page(body)), [CODE])
    return [r["result"] for r in rows]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "<pre><code>def f(x):\n    if x:\n        return 1\n    return 0\n</code></pre>",
            "exact",
        ),
        ("<pre>def f(x):\nif x:\nreturn 1\nreturn 0</pre>", "in_pre"),  # indentation lost
        ("<pre>def f(x): if x: return 1 return 0</pre>", "in_pre"),  # lines merged
        ("<p>def f(x):</p><p>if x:</p><p>return 1</p><p>return 0</p>", "text_only"),
        ("<p>nothing here</p>", "missing"),
    ],
)
def test_a_code_block_is_judged_by_pre_and_indentation(body, expected):
    assert code_result(body) == [expected]


# --------------------------------------------------------------------- glossary

TERM = {
    "kind": "glossary",
    "id": "g/gl-0",
    "term": "Derivative",
    "definition": "The limit of the ratio of the change in a function to the change in its input.",
}


def glossary_result(body: str) -> str:
    return pc.score_glossary(pc.parse_html(page(body)), [TERM])[0]["result"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "<dl><dt>Derivative</dt><dd>The limit of the ratio of the change in a function to "
            "the change in its input.</dd></dl>",
            "definition_list",
        ),
        (
            "<p><strong>Derivative</strong> The limit of the ratio of the change in a function "
            "to the change in its input.</p>",
            "text_block",
        ),
        (
            "<ul><li>Derivative: the limit of the ratio of the change in a function to the "
            "change in its input.</li></ul>",
            "text_block",
        ),
        (
            "<h3>Derivative</h3><p>Later: The limit of the ratio of the change in a function to "
            "the change in its input.</p>",
            "found_apart",
        ),
        ("<p>Integral</p>", "missing"),
    ],
)
def test_a_glossary_entry_is_judged_by_its_markup(body, expected):
    assert glossary_result(body) == expected


# ------------------------------------------------------------------ hyphen breaks


def breaks(body: str, *words: tuple[str, str, str, bool]) -> list[str]:
    records = [
        {
            "kind": "hyphen_break",
            "id": f"g/hy-{i}",
            "word": word,
            "left": left,
            "right": right,
            "compound": compound,
        }
        for i, (word, left, right, compound) in enumerate(words)
    ]
    return [r["result"] for r in pc.score_hyphen_breaks(pc.parse_html(page(body)), records)]


INVESTIGATES = ("investigates", "inves", "tigates", False)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("<p>The author investigates the claim.</p>", "joined"),
        ("<p>The author inves-tigates the claim.</p>", "kept_hyphen"),
        ("<p>The author inves\u00adtigates the claim.</p>", "joined"),  # a soft hyphen is no break
        ("<p>The author inves tigates the claim.</p>", "split_words"),
        ("<p>The author studies the claim.</p>", "missing"),
    ],
)
def test_a_hyphenated_word_is_joined_or_not(body, expected):
    assert breaks(body, INVESTIGATES) == [expected]


def test_an_unbroken_copy_elsewhere_cannot_hide_a_broken_one():
    body = "<p>It investigates. Later it inves-tigates again.</p>"
    assert breaks(body, INVESTIGATES) == ["kept_hyphen"]
    assert breaks("<p>It investigates. Later it investigates.</p>", INVESTIGATES) == ["joined"]


def test_a_word_gold_has_twice_needs_two_copies():
    twice = (INVESTIGATES, INVESTIGATES)
    assert breaks("<p>investigates</p>", *twice) == ["joined", "missing"]
    assert breaks("<p>investigates investigates</p>", *twice) == ["joined", "joined"]


# -------------------------------------------------------------- summary and wiring


def test_the_gold_scores_one_on_itself_for_every_class():
    """The wiring and the rules, not quality: HTML rendered from the dev-v2 gold records
    must score 1.0 on every metric (746 script runs, 40 code blocks, 126 glossary entries,
    198 hyphenated words)."""
    records = list(iter_gold_records(DEV))
    out = pc.self_check(records)
    assert out["scripts"]["n"] == 746 and out["scripts"]["scripted_rate"] == 1.0
    assert set(out["scripts"]["by_class"]) == set(pc.SCRIPT_CLASSES)
    assert all(v["scripted_rate"] == 1.0 for v in out["scripts"]["by_class"].values())
    assert out["code"]["n"] == 40 and out["code"]["exact_rate"] == 1.0
    assert out["code"]["extra_pre"] == 0
    assert out["glossary"]["n"] == 126 and out["glossary"]["structured_rate"] == 1.0
    assert out["hyphen_breaks"]["n"] == 198 and out["hyphen_breaks"]["joined_rate"] == 1.0
