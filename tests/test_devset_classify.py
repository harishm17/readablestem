"""Classes of the extended development set: what counts as which (gold/devset_classify.py).

Small hand-written MathML and XHTML; no network, no files.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from readablestem.gold import devset_classify as dc

NS = 'xmlns="http://www.w3.org/1998/Math/MathML"'


def math(body: str, display: str = "block") -> str:
    return f'<math {NS} display="{display}"><semantics><mrow>{body}</mrow></semantics></math>'


def row(*cells: str) -> str:
    return "<mtr>" + "".join(f"<mtd>{c}</mtd>" for c in cells) + "</mtr>"


FRACTION = "<mfrac><mn>3</mn><mn>4</mn></mfrac><mi>x</mi>"
SYSTEM_ROWS = row("<mn>2</mn><mi>x</mi><mo>+</mo><mi>y</mi><mo>=</mo><mn>15</mn>") + row(
    "<mn>3</mn><mi>x</mi><mo>-</mo><mi>y</mi><mo>=</mo><mn>5</mn>"
)
BRACED_SYSTEM = f"<mo>{{</mo><mtable>{SYSTEM_ROWS}</mtable>"
FENCED_SYSTEM = f'<mfenced open="{{" close=""><mtable>{SYSTEM_ROWS}</mtable></mfenced>'
PIECEWISE = (
    "<mi>f</mi><mo>(</mo><mi>x</mi><mo>)</mo><mo>=</mo><mo>{</mo><mtable>"
    + row("<mi>x</mi>", "<mtext>if</mtext><mi>x</mi><mo>&lt;</mo><mn>0</mn>")
    + row("<mn>2</mn>", "<mtext>otherwise</mtext>")
    + "</mtable>"
)
DETERMINANT = (
    "<mrow><mo>|</mo><mtable>"
    + row("<mn>5</mn>", "<mn>2</mn>")
    + row("<mn>-6</mn>", "<mn>3</mn>")
    + "</mtable><mo>|</mo></mrow>"
)
MATRIX = (
    '<mfenced open="[" close="]"><mtable>'
    + row("<mn>1</mn>", "<mn>0</mn>")
    + row("<mn>0</mn>", "<mn>1</mn>")
    + "</mtable></mfenced>"
)


def classes(body: str, **kwargs) -> list[str]:
    kwargs.setdefault("display", "block")
    return dc.classify_equation(math(body, kwargs["display"]), **kwargs)


def test_an_inline_fraction_is_only_a_fraction_that_sits_in_a_line_of_text() -> None:
    assert "inline_fraction" in classes(FRACTION, display="inline")
    assert "inline_fraction" not in classes(FRACTION, display="block")


@pytest.mark.parametrize("system", [BRACED_SYSTEM, FENCED_SYSTEM])
def test_a_table_of_relations_with_a_brace_is_a_braced_system(system: str) -> None:
    assert classes(system) == ["system_braced"]


def test_a_table_of_relations_without_a_brace_is_a_multi_row_equation() -> None:
    assert classes(f"<mtable>{SYSTEM_ROWS}</mtable>") == ["multirow_equations"]


def test_a_brace_with_words_in_the_rows_is_a_piecewise_function() -> None:
    assert classes(PIECEWISE) == ["piecewise_words"]


def test_bars_make_a_determinant_and_brackets_a_matrix() -> None:
    assert classes(DETERMINANT) == ["determinant"]
    assert classes(MATRIX) == ["matrix"]


def test_a_bracketed_table_with_a_relation_in_every_row_is_not_a_matrix() -> None:
    fenced = f'<mfenced open="[" close="]"><mtable>{SYSTEM_ROWS}</mtable></mfenced>'
    assert "matrix" not in classes(fenced)


def test_numbered_exercise_equations_carry_the_number() -> None:
    assert classes("<mi>x</mi>", exercise_number="18") == ["numbered_exercise_eq"]


NUCLIDE = (
    "<msubsup><mrow/><mn>6</mn><mn>14</mn></msubsup><mtext>C</mtext>"
    "<mo>&#x27F6;</mo>"
    "<msubsup><mrow/><mn>7</mn><mn>14</mn></msubsup><mtext>N</mtext><mo>+</mo>"
    "<msubsup><mrow/><mn>-1</mn><mn>0</mn></msubsup><mtext>e</mtext>"
)


def test_stacked_prefix_scripts_on_an_element_symbol_are_a_nuclide() -> None:
    assert "nuclide" in classes(NUCLIDE)


def test_a_sum_with_a_combination_prefix_is_not_a_nuclide() -> None:
    assert "nuclide" not in classes("<msubsup><mrow/><mi>n</mi><mi>k</mi></msubsup><mi>C</mi>")


CHEM_EQUATION = (
    "<mn>2</mn><mtext>H2</mtext><mo>+</mo><mtext>O2</mtext><mo>&#x27F6;</mo><mn>2</mn>"
    "<mtext>H2O</mtext>"
)
ION = "<msup><mtext>Na</mtext><mo>+</mo></msup><mo>+</mo><msup><mtext>Cl</mtext><mo>-</mo></msup>"
ION_EQUATION = "<mtext>NaCl</mtext><mo>&#x27F6;</mo>" + ION


def test_chemical_formulas_charges_and_equations() -> None:
    assert classes(CHEM_EQUATION) == ["chem_formula", "chem_equation"]
    assert set(classes(ION)) == {"chem_formula", "chem_charge"}
    found = classes(ION_EQUATION)
    assert {"chem_formula", "chem_charge", "chem_equation"} <= set(found)


def test_a_formula_with_subscripts_built_from_letters_is_a_chemical_formula() -> None:
    body = '<msub><mi mathvariant="normal">Ca</mi><mn>2</mn></msub>'
    assert "chem_formula" in classes(body)


def test_ordinary_algebra_has_no_class() -> None:
    assert classes("<mi>x</mi><mo>+</mo><mn>1</mn>", display="inline") == []


# --------------------------------------------------------------------------- prose


def xhtml(body: str) -> ET.Element:
    return ET.fromstring(f"<div>{body}</div>")


def runs_of(body: str) -> list[tuple[str, str, str]]:
    out = []
    for record in dc.prose_script_records(xhtml(body)):
        out += [(r["script"], r["value"], r["class"]) for r in record["runs"]]
    return out


def test_units_with_exponents_in_prose() -> None:
    body = "<p>The density is 1.0 g/cm<sup>3</sup> and the speed 9.8 m/s<sup>2</sup>.</p>"
    assert runs_of(body) == [
        ("sup", "3", "prose_unit_power"),
        ("sup", "2", "prose_unit_power"),
    ]


def test_chemical_subscripts_and_charges_in_prose() -> None:
    body = (
        "<p>Water is H<sub>2</sub>O; sodium is Na<sup>+</sup>; "
        "sulfate is SO<sub>4</sub><sup>2-</sup>.</p>"
    )
    assert runs_of(body) == [
        ("sub", "2", "prose_chem_script"),
        ("sup", "+", "prose_chem_script"),
        ("sub", "4", "prose_chem_script"),
        ("sup", "2-", "prose_chem_script"),
    ]


def test_a_mass_number_before_a_symbol_is_a_nuclide_prefix() -> None:
    assert runs_of("<p>Carbon dating uses <sup>14</sup>C and <sup>238</sup>U.</p>") == [
        ("sup", "14", "prose_nuclide"),
        ("sup", "238", "prose_nuclide"),
    ]


def test_other_scripts_are_kept_apart() -> None:
    assert runs_of("<p>The term x<sub>1</sub> grows like 10<sup>-3</sup>.</p>") == [
        ("sub", "1", "prose_script_other"),
        ("sup", "-3", "prose_script_other"),
    ]


def test_the_marked_text_writes_scripts_in_braces_and_leaves_maths_out() -> None:
    (record,) = dc.prose_script_records(
        xhtml(f"<p>Take cm<sup>3</sup> then <math {NS}><mi>x</mi></math> now.</p>")
    )
    assert record["text"] == "Take cm^{3} then [math] now."


def test_paragraphs_without_scripts_make_no_record() -> None:
    assert dc.prose_script_records(xhtml("<p>Plain words.</p>")) == []


# ---------------------------------------------------------------------- exercises


def exercise(problem: str) -> tuple[ET.Element, ET.Element, dict]:
    root = xhtml(
        f'<div data-type="problem"><span class="os-number">18</span>'
        f'<span class="os-divider">. </span><div class="os-problem-container"><p>{problem}</p>'
        "</div></div>"
    )
    parents = {c: p for p in root.iter() for c in p}
    node = next(e for e in root.iter() if e.tag.endswith("math"))
    return root, node, parents


def test_an_equation_that_opens_a_numbered_exercise_gets_its_number() -> None:
    _, node, parents = exercise(f"<math {NS}><mi>x</mi></math> is a root.")
    assert dc.opening_exercise_number(node, parents) == "18"


def test_an_equation_after_words_does_not_open_the_exercise() -> None:
    _, node, parents = exercise(f"Solve <math {NS}><mi>x</mi></math>.")
    assert dc.opening_exercise_number(node, parents) is None


def test_list_items_of_an_ordered_list_are_numbered_from_start() -> None:
    root = xhtml(f'<ol start="5"><li>one</li><li><math {NS}><mi>y</mi></math> = 2</li></ol>')
    parents = {c: p for p in root.iter() for c in p}
    node = next(e for e in root.iter() if e.tag.endswith("math"))
    assert dc.opening_exercise_number(node, parents) == "6"


# ------------------------------------------------------------- headings and blocks


def test_heading_classes() -> None:
    assert dc.heading_classes("Solve the system by addition.", []) == ["heading_sentence"]
    assert dc.heading_classes("Why does this work?", []) == ["heading_sentence"]
    assert dc.heading_classes("Key Takeaways", ["Key Takeaways", "Key Takeaways"]) == [
        "heading_repeated_label"
    ]
    long = "Spectrophotometric Determination of Dichromate Concentrations"
    assert len(long) >= dc.MULTILINE_CHARS and len(long.split()) < 8
    assert dc.heading_classes(long, [long]) == ["heading_multiline"]
    assert dc.heading_classes("Solving Systems of Linear Equations by Substitution in Practice", [])
    assert dc.heading_classes("Velocity", ["Velocity", "Speed"]) == []


def test_code_blocks_keep_their_lines() -> None:
    (block,) = dc.code_records(xhtml("<pre>def f(x):\n    return x\n</pre><p>after</p>"))
    assert block["text"] == "def f(x):\n    return x"


def test_glossary_entries_from_definition_lists() -> None:
    entries = dc.glossary_records(
        xhtml("<dl><dt>matrix</dt><dd>a rectangular array</dd><dt>row</dt><dd>one line</dd></dl>")
    )
    assert [(e["term"], e["definition"]) for e in entries] == [
        ("matrix", "a rectangular array"),
        ("row", "one line"),
    ]


def test_every_class_has_a_unique_id_and_a_target() -> None:
    ids = [c.id for c in dc.CLASSES]
    assert len(ids) == len(set(ids)) == len(dc.CLASS_IDS)
    assert all(c.target > 0 for c in dc.CLASSES)
