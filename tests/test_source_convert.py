"""LaTeX sources converted end to end with the real LaTeXML in the real sandbox and the fake model
client (no key, no network): the fixtures in tests/fixtures/source/. Skipped where LaTeXML or a
working bubblewrap is missing (docs/development.md, "LaTeX source tests")."""

from __future__ import annotations

import pytest

from readablestem.config import get_settings
from readablestem.core.types import DocumentResult, Kind, Status
from readablestem.models.fake import FakeVisionClient
from readablestem.source import envelope
from readablestem.source.convert import SourceFailed, convert_source
from source_support import fixture, make_zip, needs_latexml, png_bytes

pytestmark = needs_latexml


def convert(name: str, extra: dict[str, bytes | str] | None = None, client=None):
    """A fixture as a `.tex` upload, or as a zip when it comes with other files."""
    text = fixture(f"{name}.tex")
    if extra:
        blob = envelope.wrap(
            "zip", f"{name}.zip", f"{name}.tex", make_zip({f"{name}.tex": text, **extra})
        )
    else:
        blob = envelope.wrap("tex", f"{name}.tex", None, text.encode())
    return convert_source(blob, settings=get_settings(), client=client or FakeVisionClient()).result


def by_status(result: DocumentResult, kind: Kind) -> dict[Status, int]:
    counts: dict[Status, int] = {}
    for fix in result.fixes:
        if fix.kind is kind:
            counts[fix.status] = counts.get(fix.status, 0) + 1
    return counts


def test_amsmath_gives_exact_equations_with_no_decision():
    result = convert("amsmath")
    assert result.origin == "latex" and result.title == "Equations"
    assert by_status(result, Kind.EQUATION) == {Status.ACCEPTED: 6}
    assert result.stats.required_decisions == 0 and result.stats.model_calls == 0
    latex = [f.equation.latex for f in result.fixes if f.equation]
    assert any("vmatrix" in text for text in latex) and any(r"\int" in text for text in latex)
    assert "<math" in result.html and "<mtable" in result.html  # the matrix and the cases
    assert "web version of a LaTeX document from your course" in result.html
    assert not [c for c in result.html_checks if not c.passed and c.severity == "error"]


def test_macros_are_expanded_by_the_source_not_guessed():
    result = convert("macros")
    assert by_status(result, Kind.EQUATION) == {Status.ACCEPTED: 6}
    latex = " ".join(f.equation.latex for f in result.fixes if f.equation)
    assert r"\mathbb{R}" in latex and r"\left\lVert" in latex and r"\operatorname{grad}" in latex


def test_siunitx_quantities_are_read_as_units():
    result = convert("siunitx")
    assert by_status(result, Kind.EQUATION) == {Status.ACCEPTED: 5}
    speech = " ".join(f.equation.speech for f in result.fixes if f.equation)
    assert speech  # every one is spoken


def test_mhchem_is_a_decision_with_our_own_reading_as_the_proposal():
    result = convert("mhchem")
    equations = [f for f in result.fixes if f.kind is Kind.EQUATION]
    assert [f.status for f in equations] == [Status.NEEDS_REVIEW, Status.NEEDS_REVIEW]
    assert all(f.review == "required" and f.equation is not None for f in equations)
    assert result.stats.required_decisions == 2
    assert [n.code for n in result.notices] == ["source_error"]


def test_a_bibliography_becomes_a_list_with_no_repeats():
    result = convert("bib")
    items = [e.text for e in result.elements if e.kind is Kind.LIST_ITEM]
    assert items == ["I. Newton, Principia, 1687.", "G. Leibniz, Nova Methodus, 1684."]
    assert result.html.count("Newton") == 1


def test_a_broken_macro_is_a_required_decision_showing_the_tex():
    result = convert("broken_macro")
    statuses = by_status(result, Kind.EQUATION)
    assert statuses == {Status.ACCEPTED: 1, Status.FAILED: 2}
    failed = [
        e for e in result.elements if (f := result.fix_for(e.id)) and f.status is Status.FAILED
    ]
    assert {e.text for e in failed} == {r"\mysterycommand{x}+y", r"\anotherunknown{z}=1"}
    assert result.stats.required_decisions == 2
    assert r"\mysterycommand{x}+y" in result.html  # shown as written, flagged


def test_figures_from_the_project_go_through_the_figure_step():
    def figure(request):
        return {
            "figure_type": "chart",
            "decorative": False,
            "alt": "A red box",
            "confidence": 0.95,
        }

    client = FakeVisionClient(responders={"figure": figure})
    result = convert("rich_figure", extra={"fig1.png": png_bytes()}, client=client)
    figures = [f for f in result.fixes if f.kind is Kind.FIGURE]
    assert len(figures) == 1
    assert [r.step for r in client.requests] == ["figure"]  # one picture, one request
    assert figures[0].status in {Status.ACCEPTED, Status.NEEDS_REVIEW}
    assert "data:image/png;base64" in result.html  # the project's picture is in the file


def test_a_project_that_is_not_latex_fails_with_a_plain_code():
    blob = envelope.wrap(
        "tex", "a.tex", None, b"\\documentclass{article}\n\\begin{document}\\end{document}"
    )
    with pytest.raises(SourceFailed) as raised:
        convert_source(blob, settings=get_settings(), client=FakeVisionClient())
    assert raised.value.code in {"source_unreadable", "source_failed"}


def test_a_second_conversion_waits_for_the_first_and_gives_up_as_a_timeout(monkeypatch, tmp_path):
    from readablestem.core import sandbox
    from readablestem.source import convert

    monkeypatch.setattr(convert, "GATE_WAIT_S", 0.3)
    # (the holder waits long: other test workers convert under the same lock)
    with sandbox.exclusive(convert.GATE_NAME, 300), pytest.raises(convert.SourceFailed) as raised:
        convert.run_latexml(tmp_path, "main.tex", tmp_path / "out")
    assert raised.value.code == "timeout"


def test_conversions_one_after_another_each_get_the_gate():
    for _ in range(2):
        assert convert("bib").stats.required_decisions == 0


# ------------------------------------------------------------------ what LaTeXML reported

LOSSES = {  # a source that loses content, and the line its report must hold
    "missing_input": (
        "\\documentclass{article}\\begin{document}\nIntro.\n\\input{problems/set3}\n"
        "\\end{document}\n",
        ("error", "missing_file", "problems/set3", "main.tex"),
    ),
    "unclosed_brace": (
        "\\documentclass{article}\\usepackage{amsmath}\\begin{document}\n"
        "\\begin{align}a &= \\frac{1}{2\n\\end{align}\n\\section{Harmonic series}Gone.\n"
        "\\end{document}\n",
        ("error", "expected", "}", "main.tex"),
    ),
    "undefined_text": (
        "\\documentclass{article}\\begin{document}\nA \\term{word} and \\hw.\n\\end{document}\n",
        ("error", "undefined", "\\term", "main.tex"),
    ),
    "missing_package": (
        "\\documentclass{article}\\usepackage{coursemacros}\\begin{document}\nHi \\course.\n"
        "\\end{document}\n",
        ("missing", "missing_file", "coursemacros", "main.tex"),
    ),
    "unknown_class": (
        "\\documentclass{exam}\\begin{document}\\begin{questions}\\question[5] Two?\n"
        "\\begin{solution}Four.\\end{solution}\\end{questions}\\end{document}\n",
        ("missing", "missing_file", "exam", "main.tex"),
    ),
}


@pytest.mark.parametrize("name", list(LOSSES))
def test_the_report_names_what_latexml_said(name, tmp_path):
    from readablestem.source import latexml
    from readablestem.source import report as source_report

    text, (level, category, expected, where) = LOSSES[name]
    (tmp_path / "main.tex").write_text(text)
    latexml.convert(tmp_path, "main.tex", tmp_path / "out")
    report = source_report.parse(tmp_path / "out", project=tmp_path)
    found = {(p.level, p.category, p.name, p.file) for p in [*report.problems, *report.missing]}
    assert (level, category, expected, where) in found
    assert report.log_errors >= 1


def test_a_clean_article_with_hyperref_has_an_empty_report():
    blob = envelope.wrap(
        "tex",
        "clean.tex",
        None,
        b"\\documentclass{article}\\usepackage{hyperref}\\usepackage{amsmath}\n"
        b"\\begin{document}\\section{One}Clean $a+b$ and \\url{https://example.org}.\n"
        b"\\end{document}\n",
    )
    result = convert_source(blob, settings=get_settings(), client=FakeVisionClient()).result
    assert result.source_report is not None and result.source_report.empty
    assert result.source_report.warnings == 8  # the helpers hyperref's binding asks for


def test_every_error_node_is_counted_and_placed():
    result = convert("broken_macro")
    report = result.source_report
    assert report is not None and report.error_nodes == 2 and report.unplaced_errors == 0
    assert {m.name for m in report.marks} == {r"\mysterycommand", r"\anotherunknown"}
    assert all(m.in_formula and m.element_id for m in report.marks)
