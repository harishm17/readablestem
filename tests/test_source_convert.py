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
from readablestem.source.support import SUPPORT
from source_support import LOSSES, fixture, loss_upload, make_zip, needs_latexml, png_bytes

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


@pytest.mark.parametrize("name", list(LOSSES))
def test_the_report_names_what_latexml_said(name, tmp_path):
    """Each source that would lose content stops with the names for the job page (SW42.2), or
    converts with what it holds read and its decisions required (SW42.4), never leaving
    anything unread."""
    from readablestem.source import latexml
    from readablestem.source import report as source_report

    row = LOSSES[name]
    if row.line is not None:
        (tmp_path / "main.tex").write_text(row.text)
        latexml.convert(tmp_path, "main.tex", tmp_path / "out")
        report = source_report.parse(
            tmp_path / "out",
            project=tmp_path,
            loaded=source_report.loaded_packages({"main.tex": row.text}),
        )
        found = {(p.level, p.category, p.name, p.file) for p in [*report.problems, *report.missing]}
        assert row.line in found
        assert report.log_errors >= 1
    data, filename = loss_upload(name)
    kind = "zip" if filename.endswith(".zip") else "tex"
    blob = envelope.wrap(kind, filename, "main.tex" if kind == "zip" else None, data)
    if row.code is not None:
        with pytest.raises(SourceFailed) as raised:
            convert_source(blob, settings=get_settings(), client=FakeVisionClient())
        assert raised.value.code == row.code and raised.value.detail
        assert all(raised.value.detail.get(key) == value for key, value in row.detail)
        return
    result = convert_source(blob, settings=get_settings(), client=FakeVisionClient()).result
    assert row.shown in result.html
    assert sum(1 for e in result.elements if e.kind is Kind.FIGURE) == row.figures
    if row.decision is not None:
        assert any(
            f.review == "required" and any(c.code == row.decision for c in f.checks if not c.passed)
            for f in result.fixes
        )
    assert result.source_report is not None
    assert (result.source_report.unread_words, result.source_report.unread_pictures) == (0, 0)


# ------------------------------------------------------------ classes and packages (WS18.1)


def test_the_support_table_lists_the_bindings_latexml_ships():
    """`SUPPORT`'s `bound` lists are copied from LaTeXML's folder: a LaTeXML that adds or drops
    a binding fails here until they are copied again (src/readablestem/source/support.py)."""
    import os
    from pathlib import Path

    from readablestem.source.latexml import DEV_ROOT_VAR

    root = Path(os.environ.get(DEV_ROOT_VAR) or "/")
    folder = root / "usr/share/perl5/LaTeXML/Package"
    shipped = {
        kind: {p.name.removesuffix(f".{kind}.ltxml") for p in folder.glob(f"*.{kind}.ltxml")}
        for kind in ("cls", "sty")
    }
    for kind, lists in (("cls", SUPPORT.classes), ("sty", SUPPORT.packages)):
        assert shipped[kind], folder
        assert lists.bound == shipped[kind] - lists.plain - lists.refused, kind


PLAIN = {
    **{c: f"\\documentclass[11pt,a4paper]{{{c}}}" for c in sorted(SUPPORT.classes.plain)},
    **{p: f"\\documentclass{{article}}\\usepackage{{{p}}}" for p in sorted(SUPPORT.packages.plain)},
}


@pytest.mark.parametrize("name", list(PLAIN))
def test_what_is_read_without_a_binding_converts_with_no_error(name):
    """Each class read as an article, and each package read without its binding, stays on
    `SUPPORT`'s `plain` list only while a document of it converts with nothing lost: no error,
    no error node, and no missing file but its own binding."""
    preamble = PLAIN[name]
    text = (
        f"{preamble}\n\\begin{{document}}\\title{{Notes}}\\author{{A. Teacher}}\\maketitle\n"
        "\\section{Waves}Plain text and $a+b$.\n\\begin{itemize}\\item First.\\item Second."
        "\\end{itemize}\n\\begin{equation}E = mc^2\\end{equation}\n\\end{document}\n"
    )
    blob = envelope.wrap("tex", "notes.tex", None, text.encode())
    result = convert_source(blob, settings=get_settings(), client=FakeVisionClient()).result
    report = result.source_report
    assert report is not None
    assert not report.problems and not report.marks and not report.figures
    assert report.log_errors == 0 and report.error_nodes == 0
    assert {p.name for p in report.missing} <= {name}
    assert "Waves" in result.html and "Second." in result.html


def test_a_package_refused_by_name_still_fails_when_it_is_only_loaded(tmp_path):
    """`csquotes` is refused at upload because loading it alone already fails here (its
    binding reads the package's own TeX, which is hidden). When a LaTeXML reads it, this fails
    and it comes off the list."""
    from readablestem.source import latexml
    from readablestem.source import report as source_report

    for name in sorted(SUPPORT.packages.refused):
        text = f"\\documentclass{{article}}\\usepackage{{{name}}}\\begin{{document}}\nHi.\n"
        (tmp_path / "main.tex").write_text(text + "\\end{document}\n")
        out = tmp_path / f"out-{name}"
        latexml.convert(tmp_path, "main.tex", out)
        assert source_report.parse(out, project=tmp_path).log_errors > 0, name


def test_a_package_stop_says_whether_the_package_is_a_tex_package(tmp_path, monkeypatch):
    """With the TeX distribution's names written (the image build), a stop for a package's
    commands says whether the package is one of them or the instructor's own; without them it
    says neither."""
    from readablestem.source import support

    monkeypatch.setattr(support, "NAMES_PATH", tmp_path / "names.txt")
    own = LOSSES["missing_package"][0]  # \usepackage{coursemacros}, then \course
    tex = (
        "\\documentclass{article}\\usepackage[french,english]{babel}\\begin{document}\n"
        "Hello \\foreignlanguage{french}{bonjour}.\n\\end{document}\n"
    )

    def origin(text: str) -> object:
        blob = envelope.wrap("tex", "a.tex", None, text.encode())
        with pytest.raises(SourceFailed) as raised:
            convert_source(blob, settings=get_settings(), client=FakeVisionClient())
        assert raised.value.code == "source_unsupported_package" and raised.value.detail
        return raised.value.detail.get("origin")

    assert origin(own) is None
    support.write_distribution_names({"babel", "mhchem", "siunitx"}, support.NAMES_PATH)
    assert origin(own) == "own"
    assert origin(tex) == "distribution"


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
