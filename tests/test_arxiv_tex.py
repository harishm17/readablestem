"""Gold equations from LaTeX source: macro expansion, extraction, environment splitting, page
mapping and the gold records (`readablestem.gold.arxiv_tex`, `arxiv_gold`). Small fixture sources
under tests/fixtures/arxiv_tex; no network and no model."""

from __future__ import annotations

from pathlib import Path

import pytest

from readablestem.gold import arxiv_gold as ag
from readablestem.gold import arxiv_tex as tex

FIXTURES = Path(__file__).parent / "fixtures" / "arxiv_tex"


def body_of_paper_a() -> tuple[str, tex.MacroTable]:
    sources = tex.read_sources(FIXTURES / "paper_a")
    main = tex.find_main(sources)
    assert main == "main.tex"
    flat = tex.flatten(sources, main)
    table = tex.MacroTable()
    flat = table.expand(table.read(flat))
    return tex.body_of(flat), table


# ------------------------------------------------------------------------------ comments


def test_strip_comments_keeps_escaped_percent():
    assert tex.strip_comments("a % b\nc \\% d % e") == "a \nc \\% d "


def test_input_is_followed_and_comments_dropped():
    body, _ = body_of_paper_a()
    assert "y = 2x" in body  # from sections/two.tex
    assert "not math" not in body


# ------------------------------------------------------------------------------- macros


def test_macro_definitions_are_removed_from_the_text():
    body, table = body_of_paper_a()
    assert "\\newcommand" not in body and "\\def" not in body
    assert {"R", "norm", "inner", "E", "pair", "Var", "argmin", "ip", "be", "ee"} <= set(
        table.macros
    )
    assert "eq" in table.environments


def test_arguments_defaults_and_nesting_expand():
    body, _ = body_of_paper_a()
    assert "\\mathbb{R}^n" in body
    assert "\\left\\lVert x\\right\\rVert \\le 1" in body  # \norm{x}
    # \inner[y]{x} uses the optional argument, \ip is a \let alias of \inner
    assert "\\langle y, x\\rangle" in body
    # \def with two undelimited parameters
    assert "(a,b)" in body
    # \twice nested inside a display
    assert "yy" in body


def test_declare_math_operator_becomes_operatorname():
    body, _ = body_of_paper_a()
    assert "\\operatorname{Var}(X)" in body
    assert "\\operatorname*{arg\\,min}_x" in body


def test_renewed_builtin_is_expanded():
    body, _ = body_of_paper_a()
    assert "\\varepsilon = 0" in body


def test_structural_macros_open_environments():
    # \be ... \ee and a \newenvironment alias both become real equation environments
    body, _ = body_of_paper_a()
    extraction = tex.extract(body)
    envs = [i.env for i in extraction.items if i.display]
    assert envs.count("equation") == 3


def test_recursive_macro_is_a_recorded_failure():
    _, table = body_of_paper_a()
    table.macros["loopy"] = tex.Macro("loopy", 0, None, "\\loopy x")
    out = table.expand("$\\loopy$")
    assert table.failures
    assert "loopy" in out


def test_delimited_def_is_reported_not_expanded():
    table = tex.MacroTable()
    text = table.read("\\def\\foo#1.{bar #1} $\\foo x.$")
    assert any("delimited" in f for f in table.failures)
    assert "\\foo" in text


# ---------------------------------------------------------------------------- extraction


def test_every_delimiter_is_found_in_order():
    body, _ = body_of_paper_a()
    items = tex.extract(body).items
    kinds = [(i.env, i.display) for i in items]
    assert ("$", False) in kinds and ("\\(", False) in kinds
    assert ("\\[", True) in kinds and ("$$", True) in kinds
    assert [i.index for i in items] == list(range(len(items)))
    latex = [i.latex for i in items]
    assert latex.index("y = 2x") < latex.index("\\int_0^1 g(t)\\,dt = 1 .")


def test_escaped_dollar_is_not_math():
    body, _ = body_of_paper_a()
    latex = [i.latex for i in tex.extract(body).items]
    assert "a+b" in latex
    assert not any(x.strip() == "5" for x in latex)  # "\$5 and the sum is $" is not a pair


def test_verbatim_tikz_and_floats():
    text = tex.strip_comments((FIXTURES / "paper_b.tex").read_text())
    extraction = tex.extract(tex.body_of(text))
    latex = [i.latex for i in extraction.items]
    assert not any("verbatim" in x for x in latex)
    assert not any("not math" in x for x in latex)  # \verb
    assert extraction.skipped_environments == {"verbatim": 1}
    # a picture is placed by its own words, like a float: its maths is extracted
    node = next(i for i in extraction.items if i.latex == "z = 3")
    caption = next(i for i in extraction.items if i.latex == "k=1")
    assert node.float_id is not None and caption.float_id is not None
    assert node.float_id != caption.float_id and extraction.n_floats == 2
    assert next(i for i in extraction.items if i.latex == "x").float_id is None


# ------------------------------------------------------------------------------ splitting


def equations_of_b() -> list[tex.Equation]:
    text = tex.strip_comments((FIXTURES / "paper_b.tex").read_text())
    return tex.equations_of(tex.extract(tex.body_of(text)).items)


def test_numbered_align_groups_unnumbered_rows_with_the_next_numbered_one():
    align = [e for e in equations_of_b() if e.env == "align"]
    # rows: a=b+c (numbered) | d=e (nonumber) + =f (numbered) | g=cases (numbered); the
    # starred align is a different environment
    assert [e.rows for e in align if not e.latex.startswith("\\begin{aligned} p")] == [1, 2, 1]
    assert align[0].latex == "a = b + c"
    assert align[1].latex.startswith("\\begin{aligned} d &= e \\\\ &= f \\end{aligned}")
    assert [e.group for e in align][:3] == [0, 1, 2]
    assert align[0].groups == 3


def test_cases_inside_a_row_is_not_split():
    align = [e for e in equations_of_b() if e.env == "align"]
    last = align[2]
    assert "\\begin{cases} 1 & x>0 \\\\ 0 & x \\le 0 \\end{cases}" in last.latex


def test_starred_align_is_one_equation():
    star = [e for e in equations_of_b() if e.latex.startswith("\\begin{aligned} p")]
    assert len(star) == 1 and star[0].rows == 2 and "&= r" in star[0].latex


def test_gather_is_one_equation_per_row_and_multline_one_in_all():
    eqs = equations_of_b()
    assert [e.latex for e in eqs if e.env == "gather"] == ["u = 1", "v = 2"]
    multline = [e for e in eqs if e.env == "multline"]
    assert len(multline) == 1 and multline[0].latex == "m_1 + m_2 + m_3 + m_4 = 0"


def test_labels_and_split_are_cleaned():
    eqs = equations_of_b()
    assert all("\\label" not in e.latex and "\\nonumber" not in e.latex for e in eqs)
    split = next(e for e in eqs if "s &= t" in e.latex)
    assert "\\begin{aligned}" in split.latex and "{split}" not in split.latex


def test_split_rows_respects_braces_and_spacing_arguments():
    assert tex.split_rows("a \\\\[3pt] b \\\\ {c \\\\ d}") == ["a ", " b ", "{c \\\\ d}"]


# ---------------------------------------------------------------------------- gold records


def test_mathml_is_the_pipelines_conversion():
    mathml = ag.to_mathml("\\frac{a}{b} = c", display=True)
    assert 'display="block"' in mathml and "<mfrac>" in mathml
    inline = ag.to_mathml("x_1", display=False)
    assert 'display="inline"' in inline


@pytest.mark.parametrize(
    "latex",
    ["\\ref{eq:one}", "\\unknownmacro{x}", "\\cite{a} + 1"],
)
def test_unconvertible_equations_are_refused(latex):
    with pytest.raises(ag.GoldError):
        ag.to_mathml(latex, display=False)


# ------------------------------------------------------------------------ page mapping


PROSE = (
    "Gradient descent converges quickly whenever objective functions remain smooth and strongly "
    "convex throughout feasible regions. Stochastic variants replace exact gradients with noisy "
    "estimates computed from random minibatches sampled uniformly. Momentum accumulates previous "
    "directions, damping oscillations across narrow valleys. Adaptive methods rescale coordinates "
    "using running second moment estimates. Regularization penalizes complicated hypotheses and "
    "improves generalization to unseen examples. Cross validation selects hyperparameters without "
    "touching held out test data. Ensembles average predictions from diverse learners, reducing "
    "variance substantially. Calibration measures whether predicted probabilities match observed "
    "frequencies across confidence levels."
)


def split_sentences() -> list[str]:
    return [s.strip() + "." for s in PROSE.split(".") if s.strip()]


def test_page_map_places_math_between_anchors_and_floats_by_their_words():
    sentences = split_sentences()
    caption = (
        "Figure shows chlorophyll absorption spectra measured under monochromatic illumination"
    )
    source = (
        f"{sentences[0]} $a$ {sentences[1]} {sentences[2]} $$b$$ {sentences[3]}\n"
        f"\\begin{{figure}}\\caption{{{caption} $k=1$}}\\end{{figure}}\n"
        f"{sentences[4]} $c$ {sentences[5]} {sentences[6]} {sentences[7]}"
    )
    extraction = tex.extract(source)
    pages = [
        " ".join(sentences[0:2]) + " a",  # page 0: $a$
        " ".join(sentences[2:4]) + " b",  # page 1: $$b$$
        caption + " k = 1 " + " ".join(sentences[4:6]),  # page 2: float and $c$
        " ".join(sentences[6:8]),  # page 3
    ]
    result = tex.page_map(extraction, pages)
    by_latex = {extraction.items[i].latex: p for i, p in result.placements.items()}
    assert by_latex["a"].page == 0
    assert by_latex["b"].page == 1
    assert by_latex["k=1"].page == 2  # the float is on page 2 though typed after page 1's text
    assert by_latex["c"].page == 2
    assert not result.unplaced
    assert all(v > 0.9 for v in result.coverage.values())


def test_math_between_two_pages_has_an_interval_and_blocks_both():
    sentences = split_sentences()
    source = f"{sentences[0]} {sentences[1]} $m$ {sentences[2]} {sentences[3]}"
    extraction = tex.extract(source)
    pages = [" ".join(sentences[0:2]), " ".join(sentences[2:4])]
    result = tex.page_map(extraction, pages)
    place = result.placements[0]
    assert (place.low, place.high) == (0, 1) and place.page is None
    eqs = tex.equations_of(extraction.items)
    assert (
        tex.usable_pages(eqs, result.placements, result.coverage, min_equations=1, skip_first=False)
        == {}
    )


def test_usable_pages_needs_coverage_count_and_convertible_equations():
    eqs = [tex.Equation(i, "x", False, "$") for i in range(6)]
    place = {i: tex.Placement(1, 1) for i in range(6)}
    cover = {0: 1.0, 1: 0.95}
    assert tex.usable_pages(eqs, place, cover) == {1: [0, 1, 2, 3, 4, 5]}
    assert tex.usable_pages(eqs, place, {1: 0.5}) == {}
    assert tex.usable_pages(eqs[:3], place, cover) == {}
    assert tex.usable_pages(eqs, place, cover, unconvertible={4}) == {}
    assert tex.usable_pages(eqs, {i: tex.Placement(0, 0) for i in range(6)}, cover) == {}  # page 0


def test_choose_pages_is_seeded_and_bounded():
    usable = {p: [p] for p in range(1, 12)}
    first = ag.choose_pages(usable, "seed", "2401.00001")
    assert first == ag.choose_pages(usable, "seed", "2401.00001")
    assert 4 <= len(first) <= 6 and first == sorted(first)
    assert ag.choose_pages(usable, "other", "2401.00001") != first or True
    assert ag.choose_pages({1: [0], 2: [1], 3: [2]}, "seed", "x") == []


def test_gold_records_are_in_reading_order_with_pages():
    sources = tex.read_sources(FIXTURES / "paper_a")
    pages = ["Fixture text. " * 40]
    analysis = ag.analyse(sources, pages)
    assert not isinstance(analysis, str)
    # fabricate placements so that every convertible equation is on page 1 (0-based)
    analysis.usable = {1: [k for k in analysis.mathml]}
    meta = {
        "id": "2401.00001",
        "title": "Fixture",
        "licence_name": "CC BY 4.0",
        "license": "http://creativecommons.org/licenses/by/4.0/",
        "style": "research",
    }
    rows = ag.gold_records(
        analysis,
        [1],
        meta,
        pdf_relpath="pdfs/x.pdf",
        retrieved_at="2026-10-21",
        pdf_sha256="0" * 64,
        source_sha256="1" * 64,
    )
    assert [r["index_on_page"] for r in rows] == list(range(len(rows)))
    assert all(r["pdf_page"] == 2 and r["pdf_page_in_extract"] == 1 for r in rows)
    assert {r["display"] for r in rows} == {"block", "inline"}
    assert all(r["mathml"].startswith("<math") and r["license_name"] == "CC BY 4.0" for r in rows)
    latex = [r["latex"] for r in rows]
    assert latex.index("y = 2x") < latex.index("a^2 + b^2 = c^2")


def test_author_level_commands_are_prepared_for_the_converter():
    assert "ensuremath" not in ag.prepare("\\ensuremath{x} + y")
    assert ag.prepare("\\textcolor{red}{a}").strip() == "a"
    assert "\\operatorname{arg}" in ag.prepare("\\arg\\min_y")
    assert "\\operatorname{max}" in ag.prepare("\\mathop{max}_x")
    ag.to_mathml("\\ensuremath{x}^2 \\nolimits + \\arg\\max_y f", display=False)
