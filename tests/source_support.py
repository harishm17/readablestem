"""Shared helpers for the LaTeX source tests: fixtures, a PNG, zips, and a LaTeXML check.

LaTeXML is the distribution's package (Dockerfile, `INSTALL_LATEXML`); where it is not installed
the tests that run it skip, and `READABLESTEM_LATEXML_ROOT` can point at unpacked packages
(docs/development.md, "LaTeX source tests")."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from typing import NamedTuple

import pytest

from readablestem.core import sandbox
from readablestem.source import latexml

FIXTURES = Path(__file__).parent / "fixtures" / "source"

# CI's `latex source` job sets this: there a missing LaTeXML or sandbox is a failure to see, not
# a skip to scroll past.
REQUIRED = bool(os.environ.get("READABLESTEM_REQUIRE_SANDBOX_TESTS"))

needs_latexml = pytest.mark.skipif(
    not REQUIRED and not (latexml.installed() and sandbox.available()),
    reason="needs LaTeXML and a working sandbox (bubblewrap or Landlock)",
)


class Loss(NamedTuple):
    """A source that would lose content (SW42). It stops with `code`, and the job page names
    `shown`; or it converts (`code` None) with `shown` in the student's file, `figures` figure
    elements, and, with `decision`, a required decision whose failed check has that code.
    `line`: a line the report must hold (level, category, name, file). `files`: pictures
    uploaded with it. `detail`: what the stop's detail must hold."""

    text: str
    code: str | None
    shown: str
    line: tuple[str, str, str, str] | None = None
    files: tuple[str, ...] = ()
    figures: int = 0
    decision: str | None = None
    detail: tuple[tuple[str, str], ...] = ()


def _article(body: str, preamble: str = "\\usepackage{graphicx}\\usepackage{amsmath}") -> str:
    return (
        f"\\documentclass{{article}}{preamble}\n\\begin{{document}}\n\\section{{S}}Lead.\n\n"
        f"{body}\n\\end{{document}}\n"
    )


LOSSES: dict[str, Loss] = {
    "missing_input": Loss(
        "\\documentclass{article}\\begin{document}\nIntro.\n\\input{problems/set3}\n"
        "\\end{document}\n",
        "source_missing_file",
        "problems/set3",
        ("error", "missing_file", "problems/set3", "main.tex"),
    ),
    "unclosed_brace": Loss(
        "\\documentclass{article}\\usepackage{amsmath}\\begin{document}\n"
        "\\begin{align}a &= \\frac{1}{2\n\\end{align}\n\\section{Harmonic series}Gone.\n"
        "\\end{document}\n",
        "source_syntax_error",
        "main.tex, line",
        ("error", "expected", "}", "main.tex"),
    ),
    "self_calling_macro": Loss(
        "\\documentclass{article}\\begin{document}\n\\def\\a{\\a}Text \\a here.\n\\end{document}\n",
        "source_loop",
        "\\a",
        ("error", "recursion", "\\a", "main.tex"),
    ),
    "nested_macro": Loss(
        "\\documentclass{article}\\begin{document}\n\\def\\a{{\\a}}\\a\n\\end{document}\n",
        "source_loop",
        "calls itself",
        ("fatal", "perl", "deep_recursion", ""),
    ),
    "undefined_text": Loss(
        "\\documentclass{article}\\begin{document}\nA \\term{word} and \\hw.\n\\end{document}\n",
        "source_undefined",
        "\\term",
        ("error", "undefined", "\\term", "main.tex"),
    ),
    "missing_package": Loss(
        "\\documentclass{article}\\usepackage{coursemacros}\\begin{document}\nHi \\course.\n"
        "\\end{document}\n",
        "source_unsupported_package",
        "coursemacros",
        ("missing", "missing_file", "coursemacros", "main.tex"),
    ),
    # A class on none of `source.support`'s lists goes to LaTeXML (`exam`, refused by name,
    # never gets there: tests/test_source_intake.py).
    "unknown_class": Loss(
        "\\documentclass{tufte-handout}\\begin{document}\\newthought{In the beginning} there "
        "was text.\n\\end{document}\n",
        "source_unsupported_class",
        "tufte-handout",
        ("missing", "missing_file", "tufte-handout", "main.tex"),
    ),
    "tikz_figure": Loss(
        "\\documentclass{article}\\usepackage{tikz}\\usetikzlibrary{arrows.meta}\n"
        "\\begin{document}\\section{Drawn}\n\\begin{figure}\\begin{tikzpicture}"
        "\\draw (0,0) -- (1,1);\\end{tikzpicture}\\caption{A line}\\end{figure}\n"
        "\\end{document}\n",
        "source_unsupported_package",
        "{tikzpicture}",
        ("missing", "missing_file", "tikz", ""),
    ),
    # What the reader used to leave out with nothing said (SW42.4): a picture outside a float
    # follows its block as a figure, and one in a cell makes its table a decision...
    "centered_picture": Loss(
        _article("\\begin{center}\\includegraphics{c1.png}\\end{center}"),
        None,
        "",
        files=("c1.png",),
        figures=1,
    ),
    "inline_picture": Loss(
        _article("Press \\includegraphics[height=1em]{i1.png} to go."),
        None,
        "Press (picture below) to go.",
        files=("i1.png",),
        figures=1,
    ),
    "item_picture": Loss(
        _article("\\begin{itemize}\\item See \\includegraphics{l1.png}\\end{itemize}"),
        None,
        "See",
        files=("l1.png",),
        figures=1,
    ),
    "cell_picture": Loss(
        _article("\\begin{tabular}{ll}Pump & \\includegraphics{t1.png}\\\\\\end{tabular}"),
        None,
        "Pump",
        files=("t1.png",),
        figures=1,
        decision="source_table_picture",
    ),
    # ...the words in a float beside its picture or table, and between aligned equations...
    "figure_text": Loss(
        _article(
            "\\begin{figure}\\centering\\includegraphics{f1.png}\n\nNote ZQfiguretext under "
            "it.\\caption{Cap}\\end{figure}"
        ),
        None,
        "ZQfiguretext",
        files=("f1.png",),
        figures=1,
    ),
    "table_note": Loss(
        _article(
            "\\begin{table}\\begin{tabular}{ll}a & b\\\\\\end{tabular}\n\nNote ZQtablenote under "
            "the table.\\end{table}"
        ),
        None,
        "ZQtablenote",
    ),
    "intertext": Loss(
        _article("\\begin{align}a&=b\\\\ \\intertext{Between ZQintertext rows}c&=d\\end{align}"),
        None,
        "ZQintertext",
    ),
    # Struck text (`\sout`: a deleted run, or plain text when CONTENT_RUNS is off) is read, not
    # counted as unread (SW51a meets SW42.4).
    "struck_text": Loss(
        _article("The quiz is on \\sout{ZQstruck Monday} Friday.", "\\usepackage[normalem]{ulem}"),
        None,
        "ZQstruck Monday",
    ),
    # ...a formula LaTeX could not read that is shown as written in a table, a caption or a
    # heading, which is a decision on that item...
    "cell_formula": Loss(
        _article("\\begin{tabular}{ll}Rate & $\\mystery{x}+1$\\\\\\end{tabular}"),
        None,
        "Rate",
        ("error", "undefined", "\\mystery", "main.tex"),
        decision="source_formula_unread",
    ),
    "caption_formula": Loss(
        _article(
            "\\begin{table}\\caption{Rate $\\mystery{k}$}\\begin{tabular}{l}a\\\\\\end{tabular}"
            "\\end{table}"
        ),
        None,
        "Rate",
        ("error", "undefined", "\\mystery", "main.tex"),
        decision="source_formula_unread",
    ),
    "figure_caption_formula": Loss(
        _article(
            "\\begin{figure}\\includegraphics{f2.png}\\caption{Rate $\\mystery{k}$}\\end{figure}"
        ),
        None,
        "Rate",
        ("error", "undefined", "\\mystery", "main.tex"),
        files=("f2.png",),
        figures=1,
        decision="source_formula_unread",
    ),
    "heading_formula": Loss(
        _article("\\subsection{About $\\mystery{k}$}Text."),
        None,
        "About",
        ("error", "undefined", "\\mystery", "main.tex"),
        decision="source_formula_unread",
    ),
    # ...and the stop rule's holes: a bibliography that is not there, a package with no
    # binding whose command fails before a syntax error, a ^ in the text.
    "missing_bibliography": Loss(
        _article("As shown \\cite{k1}.\\bibliographystyle{plain}\\bibliography{refs}"),
        "source_missing_file",
        "refs.bib",
        ("error", "missing_file", "refs.bib", ""),
    ),
    "package_before_syntax": Loss(
        "\\documentclass{article}\\usepackage[version=4]{mhchem}\\begin{document}\n"
        "The ion \\ce{SO4^2-} here.\n\\end{document}\n",
        "source_unsupported_package",
        "mhchem",
        ("missing", "missing_file", "mhchem", "main.tex"),
    ),
    "script_in_text": Loss(
        _article("Then x^2 here."),
        "source_syntax_error",
        "the character ^",
        ("error", "unexpected", "^", "main.tex"),
        detail=(("character", "^"),),
    ),
}


def png_bytes(width: int = 40, height: int = 30, colour: tuple[int, int, int] = (200, 30, 30)):
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(out, format="PNG")
    return out.getvalue()


def make_zip(files: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def loss_upload(name: str) -> tuple[bytes, str]:
    """A `LOSSES` row as an upload (a `.tex`, or a zip when it comes with pictures): the raw
    bytes and the file name."""
    row = LOSSES[name]
    if not row.files:
        return row.text.encode(), f"{name}.tex"
    shades = {f: png_bytes(colour=(40 + 30 * i, 30, 30)) for i, f in enumerate(row.files)}
    return make_zip({"main.tex": row.text, **shades}), f"{name}.zip"
