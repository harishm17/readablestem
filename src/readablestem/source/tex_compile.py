"""Compile a TeX file to a PDF with `pdflatex`, inside the sandbox.

`compile_tex(tex, project)` is the one place the product runs TeX itself (LaTeXML, in
`source/latexml.py`, is the other program that reads TeX, and it never typesets). It returns a
`TexResult`: the PDF, or a named failure; it never raises for what the document contains and
never reports what it said (a TeX log quotes the document).

The child can read what it is given and nothing else: the project folder (or the one file, alone
in a folder of its own) is mounted read-only, the PDF and TeX's own files go to a writable folder
of their own, there is no network, and the limits are LaTeXML's (`latexml.LIMITS`). On top of the
sandbox, TeX's own file rules refuse any path outside the work folder (`openin_any=p`,
`openout_any=p`: no absolute paths, no `..`), shell escape is off, and the programs TeX starts to
make a missing font (`mktexpk` and the like) are switched off, so a font the image lacks is an
error and not a generated bitmap.

It takes no lock: a caller that must hold `convert.GATE_NAME` (one source program at a time)
holds it. It does not build the document: a caller that has only a formula brings its own
preamble, and one that has a project brings the project's. Two passes for references are two
calls.

There are two TeX compiles in the repository, on purpose. This is the product's: a single
`pdflatex` pass in the product's sandbox (bubblewrap or Landlock), so a caller in the product
uses it. `gold.safe_tex.compile_tex` is development only (it needs bubblewrap, runs `latexmk` for
as many passes as a document needs, and also runs `xelatex` and Tectonic), and could not be made
to call this one without changing the documents the gold sets build.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from readablestem.core import sandbox
from readablestem.source import latexml

# Failures, by name (what the caller reports; none carries text from the document).
NO_ENGINE = "no_engine"  # pdflatex is not installed here: "not measured", never a pass
NO_SANDBOX = "no_sandbox"  # the sandbox cannot start here
NOT_FINISHED = "not_finished"  # stopped by a limit: time, memory, tasks or output size
FAILED = "failed"  # TeX stopped on an error (a refused file included), or made no PDF

# What a run may not do. `p` (paranoid) is TeX's strictest file rule; the four `MKTEX*` are the
# programs kpathsea starts for a missing font, metric file, source or format.
_ENV = {
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "TZ": "UTC",
    "SOURCE_DATE_EPOCH": "1",
    "FORCE_SOURCE_DATE": "1",
    "openin_any": "p",
    "openout_any": "p",
    "shell_escape": "f",
    "MKTEXPK": "0",
    "MKTEXTFM": "0",
    "MKTEXMF": "0",
    "MKTEXFMT": "0",
    "TEXMFOUTPUT": "/out",
}
_MAIN = "main.tex"


@dataclass(frozen=True)
class TexResult:
    pdf: bytes | None
    failure: str = ""  # one of the names above when `pdf` is None


def engine() -> str | None:
    """The `pdflatex` of this machine, or None."""
    return shutil.which("pdflatex")


def compile_tex(
    tex: Path, project: Path | None = None, *, limits: sandbox.Limits | None = None
) -> TexResult:
    """Typeset `tex` once. With `project` (a folder), `tex` is a file inside it and the child may
    read the whole folder; without it, `tex` is the only file the child sees."""
    program = engine()
    if program is None:
        return TexResult(None, NO_ENGINE)
    with tempfile.TemporaryDirectory(prefix="rs-tex-") as scratch:
        work, out = Path(scratch, "work"), Path(scratch, "out")
        out.mkdir()
        if project is None:
            work.mkdir()
            shutil.copyfile(tex, work / _MAIN)
            main = _MAIN
        else:
            work = project
            try:
                main = tex.resolve().relative_to(project.resolve()).as_posix()
            except ValueError:
                return TexResult(None, FAILED)  # a file outside its project is not compiled
        if main.startswith("-"):
            return TexResult(None, FAILED)  # would read as an option
        try:
            outcome = sandbox.run(
                [
                    program, "-no-shell-escape", "-halt-on-error", "-interaction=nonstopmode",
                    "-output-directory=/out", main,
                ],
                ro=((work, "/work"),), rw=((out, "/out"),), env=_ENV, cwd="/work",
                limits=limits or latexml.LIMITS, log=out / "tex.out",
            )  # fmt: skip
        except sandbox.SandboxUnavailable:
            return TexResult(None, NO_SANDBOX)
        if (
            outcome.timed_out
            or outcome.over_quota
            or outcome.over_memory
            or outcome.over_tasks
            or outcome.busy
            or not 0 <= outcome.returncode < 128  # killed by a signal (the CPU limit is 137)
        ):
            return TexResult(None, NOT_FINISHED)
        pdf = out / (Path(main).stem + ".pdf")
        if outcome.returncode != 0 or not pdf.is_file():
            return TexResult(None, FAILED)
        return TexResult(pdf.read_bytes())
