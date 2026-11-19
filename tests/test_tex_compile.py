"""The one sandboxed TeX compile (WS18.22) with the real `pdflatex` in the real sandbox. Skipped,
by name, where pdfTeX or a working sandbox is missing (`READABLESTEM_REQUIRE_SANDBOX_TESTS` turns
the skip into a failure, as for LaTeXML)."""

from __future__ import annotations

import dataclasses
import shutil

import pymupdf
import pytest

from readablestem.core import sandbox
from readablestem.source import latexml, tex_compile
from source_support import REQUIRED

needs_pdftex = pytest.mark.skipif(
    not REQUIRED and not (shutil.which("pdflatex") and sandbox.available()),
    reason="needs pdfTeX (pdflatex) and a working sandbox (bubblewrap or Landlock)",
)

# The smallest preamble the cases need: not the line check's own (that is built with its caller).
HEAD = "\\documentclass{article}\n\\usepackage{amsmath,amssymb}\n\\begin{document}\n"
FORMULA = "$\\int_0^1 \\frac{\\sqrt{x}}{2}\\,dx = \\mathbb{R}\\mathcal{A}\\mathbf{b}$\n"


def write(tmp_path, body: str, name: str = "formula.tex"):
    path = tmp_path / name
    path.write_text(HEAD + body + "\\end{document}\n")
    return path


@needs_pdftex
def test_a_formula_compiles_to_a_one_page_pdf_with_no_generated_font(tmp_path):
    result = tex_compile.compile_tex(write(tmp_path, FORMULA))
    assert result.failure == "" and result.pdf is not None
    with pymupdf.open(stream=result.pdf, filetype="pdf") as pdf:
        assert pdf.page_count == 1
        fonts = {font[2] for page in pdf for font in page.get_fonts()}
    assert fonts and "Type3" not in fonts  # a generated bitmap font is a Type 3 font


@needs_pdftex
@pytest.mark.parametrize(
    "line",
    [
        "\\input{/etc/hostname}",  # a file of the machine
        "\\input{/etc/ld.so.conf}",  # one the sandbox shows: TeX's own rule is what refuses it
        "\\input{|cat /etc/hostname}",  # a shell
    ],
)
def test_a_formula_that_reads_a_file_or_opens_a_shell_is_a_named_failure(tmp_path, line):
    result = tex_compile.compile_tex(write(tmp_path, FORMULA + line + "\n"))
    assert result == tex_compile.TexResult(None, tex_compile.FAILED)


@needs_pdftex
def test_an_endless_loop_is_stopped_by_the_limit_and_not_finished(tmp_path):
    short = dataclasses.replace(latexml.LIMITS, wall_s=3, cpu_s=2)
    result = tex_compile.compile_tex(write(tmp_path, "\\def\\x{\\x}\\x\n"), limits=short)
    assert result == tex_compile.TexResult(None, tex_compile.NOT_FINISHED)


@needs_pdftex
def test_a_project_is_read_from_its_own_folder_and_nothing_above_it(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "part.tex").write_text(FORMULA)
    (tmp_path / "outside.tex").write_text("outside")
    main = write(project, "\\input{part}\n")
    assert tex_compile.compile_tex(main, project).pdf is not None
    main = write(project, "\\input{../outside}\n", "up.tex")
    assert tex_compile.compile_tex(main, project).failure == tex_compile.FAILED
    # the one file alone has no folder to read from
    assert tex_compile.compile_tex(write(project, "\\input{part}\n")).failure == tex_compile.FAILED


def test_without_pdftex_the_answer_is_no_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(tex_compile, "engine", lambda: None)
    result = tex_compile.compile_tex(write(tmp_path, FORMULA))
    assert result == tex_compile.TexResult(None, tex_compile.NO_ENGINE)
