"""Shared helpers for the LaTeX source tests: fixtures, a PNG, zips, and a LaTeXML check.

LaTeXML is the distribution's package (Dockerfile, `INSTALL_LATEXML`); where it is not installed
the tests that run it skip, and `READABLESTEM_LATEXML_ROOT` can point at unpacked packages
(docs/development.md, "LaTeX source tests")."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

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


# Sources that lose content (SW42): the text, the code the job stops with, a name the job
# page must show, and a line the report must hold (level, category, name, file).
LOSSES: dict[str, tuple[str, str, str, tuple[str, str, str, str]]] = {
    "missing_input": (
        "\\documentclass{article}\\begin{document}\nIntro.\n\\input{problems/set3}\n"
        "\\end{document}\n",
        "source_missing_file",
        "problems/set3",
        ("error", "missing_file", "problems/set3", "main.tex"),
    ),
    "unclosed_brace": (
        "\\documentclass{article}\\usepackage{amsmath}\\begin{document}\n"
        "\\begin{align}a &= \\frac{1}{2\n\\end{align}\n\\section{Harmonic series}Gone.\n"
        "\\end{document}\n",
        "source_syntax_error",
        "main.tex, line",
        ("error", "expected", "}", "main.tex"),
    ),
    "undefined_text": (
        "\\documentclass{article}\\begin{document}\nA \\term{word} and \\hw.\n\\end{document}\n",
        "source_undefined",
        "\\term",
        ("error", "undefined", "\\term", "main.tex"),
    ),
    "missing_package": (
        "\\documentclass{article}\\usepackage{coursemacros}\\begin{document}\nHi \\course.\n"
        "\\end{document}\n",
        "source_unsupported_package",
        "coursemacros",
        ("missing", "missing_file", "coursemacros", "main.tex"),
    ),
    # A class on none of `source.support`'s lists goes to LaTeXML (`exam`, refused by name,
    # never gets there: tests/test_source_intake.py).
    "unknown_class": (
        "\\documentclass{tufte-handout}\\begin{document}\\newthought{In the beginning} there "
        "was text.\n\\end{document}\n",
        "source_unsupported_class",
        "tufte-handout",
        ("missing", "missing_file", "tufte-handout", "main.tex"),
    ),
    "tikz_figure": (
        "\\documentclass{article}\\usepackage{tikz}\\usetikzlibrary{arrows.meta}\n"
        "\\begin{document}\\section{Drawn}\n\\begin{figure}\\begin{tikzpicture}"
        "\\draw (0,0) -- (1,1);\\end{tikzpicture}\\caption{A line}\\end{figure}\n"
        "\\end{document}\n",
        "source_unsupported_package",
        "{tikzpicture}",
        ("missing", "missing_file", "tikz", ""),
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
