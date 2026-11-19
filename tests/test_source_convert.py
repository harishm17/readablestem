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
    client = client or FakeVisionClient()
    result = convert_source(blob, settings=get_settings(), client=client).result
    # every fixture is read whole: nothing the reader's skip list does not name is left out
    report = result.source_report
    assert report is not None and (report.unread_words, report.unread_pictures) == (0, 0)
    return result


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
    assert "web version of a LaTeX source from your course" in result.html
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
    # the one error, and the line that no compile witness ran (`source_not_compared`)
    assert [n.code for n in result.notices] == ["source_not_compared", "source_error"]


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


@pytest.mark.parametrize("name", ["rich_figure", "rich_figure_bare"])  # fig1.png, then fig1
def test_figures_from_the_project_go_through_the_figure_step(name):
    def figure(request):
        return {
            "figure_type": "chart",
            "decorative": False,
            "alt": "A red box",
            "confidence": 0.95,
        }

    client = FakeVisionClient(responders={"figure": figure})
    result = convert(name, extra={"fig1.png": png_bytes()}, client=client)
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


# The constructs of the reader probe (SW42.4): (preamble, body), each body with a marker word.
CONSTRUCTS: dict[str, tuple[str, str]] = {
    "plain": ("", "A plain ZQplain paragraph."),
    "center": ("", "\\begin{center}Centered ZQcenter words.\\end{center}"),
    "minipage": ("", "\\begin{minipage}{5cm}Boxed ZQminipage words.\\end{minipage}"),
    "fbox": ("", "Then \\fbox{framed ZQfbox words} here."),
    "parbox": ("", "\\parbox{4cm}{Parbox ZQparbox words.}"),
    "marginpar": ("", "Body text.\\marginpar{Margin ZQmarginpar note.}"),
    "footnote": ("", "Body\\footnote{Foot ZQfootnote note.} text."),
    "quote": ("", "\\begin{quote}Quoted ZQquote words.\\end{quote}"),
    "verse": ("", "\\begin{verse}Verse ZQverse line\\\\second line\\end{verse}"),
    "abstract": ("", "\\begin{abstract}Abstract ZQabstract words.\\end{abstract}"),
    "paragraphhead": ("", "\\paragraph{Head ZQparagraphhead} then text."),
    "description": (
        "",
        "\\begin{description}\\item[Term ZQdescription] meaning.\\end{description}",
    ),
    "nested_list": (
        "",
        "\\begin{enumerate}\\item One\\begin{itemize}\\item Inner ZQnestedlist item"
        "\\end{itemize}\\end{enumerate}",
    ),
    "item_two_pars": (
        "",
        "\\begin{itemize}\\item First.\n\nSecond ZQitemtwopars par.\\end{itemize}",
    ),
    "item_display": (
        "",
        "\\begin{itemize}\\item Solve \\[x^2 = 4\\] then ZQitemdisplay.\\end{itemize}",
    ),
    "tabbing": ("", "\\begin{tabbing}Name \\= Value\\\\ ZQtabbing \\> 4\\end{tabbing}"),
    "table_env": (
        "",
        "\\begin{table}\\caption{Cap ZQtableenvcap}\\begin{tabular}{ll}a & ZQtableenv\\\\"
        "\\end{tabular}\\end{table}",
    ),
    "table_note": (
        "",
        "\\begin{table}\\begin{tabular}{ll}a & b\\\\\\end{tabular}\n\nNote ZQtablenote under"
        " the table.\\end{table}",
    ),
    "figure_text": (
        "",
        "\\begin{figure}Words ZQfiguretext inside a figure.\\caption{Cap}\\end{figure}",
    ),
    "multicol": (
        "\\usepackage{multicol}",
        "\\begin{multicols}{2}Two ZQmulticol columns.\\end{multicols}",
    ),
    "href": ("\\usepackage{hyperref}", "See \\href{https://example.org}{the ZQhref page}."),
    "textcolor": ("\\usepackage{xcolor}", "A \\textcolor{red}{red ZQtextcolor word}."),
    "theorem": (
        "\\newtheorem{thm}{Theorem}",
        "\\begin{thm}[Name ZQtheoremname]Statement ZQtheorem here.\\end{thm}",
    ),
    "proof": ("\\usepackage{amsthm}", "\\begin{proof}Proof ZQproof words.\\end{proof}"),
    "verbatim": ("", "\\begin{verbatim}\ncode ZQverbatim line\n\\end{verbatim}"),
    "equation_text": (
        "\\usepackage{amsmath}",
        "\\begin{equation}x = 1 \\text{ for ZQequationtext}\\end{equation}",
    ),
    "cases_text": (
        "\\usepackage{amsmath}",
        "\\[f(x)=\\begin{cases}1 & \\text{if ZQcasestext}\\\\0 & \\text{else}\\end{cases}\\]",
    ),
    "intertext": (
        "\\usepackage{amsmath}",
        "\\begin{align}a&=b\\\\ \\intertext{Between ZQintertext rows}c&=d\\end{align}",
    ),
    "tag": ("\\usepackage{amsmath}", "\\begin{equation}a=b\\tag{ZQtag}\\end{equation}"),
    "appendix": ("", "\\appendix\\section{Extra ZQappendix}Appendix text."),
    "thanks": ("", ""),  # the title page, in `construct`
    "toc": ("", ""),
    "subfigure": (
        "\\usepackage{graphicx}\\usepackage{subcaption}",
        "\\begin{figure}\\begin{subfigure}{3cm}\\includegraphics{s1.png}\\caption{Left "
        "ZQsubfigure}\\end{subfigure}\\begin{subfigure}{3cm}\\includegraphics{s2.png}"
        "\\caption{Right}\\end{subfigure}\\caption{Both}\\end{figure}",
    ),
    "wrapfigure": (
        "\\usepackage{graphicx}\\usepackage{wrapfig}",
        "\\begin{wrapfigure}{r}{3cm}\\includegraphics{w1.png}\\caption{Wrapped ZQwrapfigure}"
        "\\end{wrapfigure}Text beside.",
    ),
    "longtable": (
        "\\usepackage{longtable}",
        "\\begin{longtable}{ll}\\caption{Long ZQlongtablecap}\\\\ a & ZQlongtable\\\\"
        "\\end{longtable}",
    ),
    "tabular_par": ("", "\\begin{tabular}{lp{3cm}}a & Cell ZQtabularpar words\\\\\\end{tabular}"),
    "nested_tabular": (
        "",
        "\\begin{tabular}{ll}a & \\begin{tabular}{l}inner ZQnestedtabular\\end{tabular}\\\\"
        "\\end{tabular}",
    ),
    # its label is drawn: it is in the figure's picture, not in the text
    "picture_env": ("", "\\begin{picture}(50,20)\\put(5,5){Label}\\end{picture}"),
    "centered_picture": (
        "\\usepackage{graphicx}",
        "\\begin{center}\\includegraphics{c1.png}\\end{center}",
    ),
    "inline_picture": (
        "\\usepackage{graphicx}",
        "Press \\includegraphics[height=1em]{i1.png} to go.",
    ),
    "item_picture": (
        "\\usepackage{graphicx}",
        "\\begin{itemize}\\item See \\includegraphics{l1.png}\\end{itemize}",
    ),
    "cell_picture": (
        "\\usepackage{graphicx}",
        "\\begin{tabular}{ll}a & \\includegraphics{t1.png}\\\\\\end{tabular}",
    ),
    "caption_of": (
        "\\usepackage{graphicx}\\usepackage{caption}",
        "\\begin{center}\\includegraphics{k1.png}\\captionof{figure}{Loose ZQcaptionof}"
        "\\end{center}",
    ),
    "heading_math": ("", "\\section{About $x^2$ ZQheadingmath}Text."),
    "title_only": ("", ""),
}


def construct(name: str) -> str:
    preamble, body = CONSTRUCTS[name]
    head = f"\\documentclass{{article}}\n{preamble}\n"
    if name == "thanks":
        return (
            head + "\\title{T\\thanks{Thanks ZQthanks}}\\author{A. Writer ZQauthor}\\date{Fall "
            "ZQdate}\\begin{document}\\maketitle Text.\\end{document}\n"
        )
    if name == "toc":
        return (
            head + "\\begin{document}\\tableofcontents\\section{First ZQtoc}Text.\\end{document}\n"
        )
    if name == "title_only":
        return (
            head + "\\title{Title ZQtitleonly}\\begin{document}\\maketitle Text.\\end{document}\n"
        )
    return head + "\\begin{document}\n\\section{S}Lead.\n\n" + body + "\n\\end{document}\n"


@pytest.mark.parametrize("name", list(CONSTRUCTS))
def test_every_construct_is_read_or_said(name):
    """Whatever the source holds is in the result, or the job says it is not (SW42.4): each
    marker word is in the result or there is a required notice, and each picture asked for is
    a figure (a `picture` drawing is one)."""
    import json
    import re

    text = construct(name)
    pictures = re.findall(r"\\includegraphics(?:\[[^]]*\])?\{([^}]+)\}", text)
    files = {f: png_bytes(colour=(40 + 20 * i, 30, 30)) for i, f in enumerate(pictures)}
    if files:
        blob = envelope.wrap("zip", "probe.zip", "main.tex", make_zip({"main.tex": text, **files}))
    else:
        blob = envelope.wrap("tex", "probe.tex", None, text.encode())
    result = convert_source(blob, settings=get_settings(), client=FakeVisionClient()).result
    everything = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    lost = [m for m in sorted(set(re.findall(r"ZQ[a-z]+", text))) if m not in everything]
    assert not lost or any(n.review == "required" for n in result.notices)
    asked = len(pictures) + text.count("\\begin{picture}")
    assert sum(1 for e in result.elements if e.kind is Kind.FIGURE) == asked


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


def links_of(result: DocumentResult, element_id: str) -> list[tuple[str, str, str | None]]:
    element = next(e for e in result.elements if e.id == element_id)
    return [
        (k.kind, (element.text or "")[k.start : k.end], k.href or k.target_id)
        for k in element.links
    ]


def test_addresses_typed_in_the_text_and_references_by_number_are_linked_like_a_pdfs():
    tex = (
        "\\documentclass{article}\\usepackage{amsmath,hyperref}\n\\begin{document}\n"
        "\\section{Kinematics}\n"
        "Mail a@b.org or see www.example.org and \\href{https://example.org/h}{the page}.\n"
        "See Section 1, Equation (1), Table 1 and Figure 1, and $x=3$ in Table~\\ref{tab:d}.\n"
        "\\begin{equation}v=v_0+at\\end{equation}\n"
        "\\begin{table}\\caption{Data}\\label{tab:d}\\begin{tabular}{lc}A&1\\\\\\end{tabular}"
        "\\end{table}\n\\section{Next}Back to Section 1.\n\\end{document}\n"
    )
    blob = envelope.wrap("tex", "refs.tex", None, tex.encode())
    result = convert_source(blob, settings=get_settings(), client=FakeVisionClient()).result
    by_kind = {e.kind: e.id for e in result.elements if e.display != "inline"}
    heading, body = result.elements[0].id, result.elements[1].id
    equation, table, last = by_kind[Kind.EQUATION], by_kind[Kind.TABLE], result.elements[-1].id
    assert links_of(result, body) == [
        ("typed", "a@b.org", "mailto:a@b.org"),
        ("typed", "www.example.org", "https://www.example.org"),
        ("uri", "the page", "https://example.org/h"),  # the file's own link, as it was
        ("crossref", "Section 1", heading),
        ("crossref", "Equation (1)", equation),
        ("crossref", "Table 1", table),  # "Figure 1": no figure has that number, so no link
        ("internal", "Table 1", table),  # the file's own \ref: exact, widened to its word
    ]
    assert links_of(result, last) == [("crossref", "Section 1", heading)]
    for target in (heading, equation, table):  # every link goes to a place in the file
        assert f'href="#{target}"' in result.html and f'id="{target}"' in result.html
    # the count the job page keeps ("links kept of N in the file") is the file's own: the \href
    # and the \ref, not the typed addresses and the numbers that were guessed
    assert sum(k.kind in ("uri", "internal") for e in result.elements for k in e.links) == 2
