r"""A cheap look at TeX source for what it should not contain.

This is **defence in depth and a kindness, not the guarantee**. TeX can rebuild any command
from characters (`\catcode`, `\csname`, `\expandafter`), so no scan of the text can prove a
file harmless; what stops a document from reading a file, running a program or reaching the
network is the sandbox (`core/sandbox.py`) it runs in, and the tests in
`tests/test_source_escapes.py` run hostile files through the sandbox with this scan switched
off. What the scan does is refuse the plain forms early, with a plain answer, before a job is
queued: a shell escape (`\write18` and its spellings), `\openout`, a pipe in `\input`, Lua
(`\directlua`), and a file argument that is absolute, goes up (`..`), names a home folder or is
a URL.

It reads the text with comments removed (a `%` that is not escaped, to the end of the line) and
returns a content-free code: the caller never shows or logs the matching text.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

# Commands whose argument is a file name. `\input file` and `\input{file}` are both read.
_FILE_COMMANDS = (
    "input", "include", "includegraphics", "includepdf", "InputIfFileExists", "usepackage",
    "RequirePackage", "documentclass", "LoadClass", "bibliography", "addbibresource",
    "lstinputlisting", "verbatiminput", "import", "subimport", "includeonly", "openin",
    "read", "csvreader", "pgfplotstableread", "pgfplotsinvokeforeach",
)  # fmt: skip
_COMMAND = "|".join(_FILE_COMMANDS)
_ARG = re.compile(
    rf"\\(?P<cmd>{_COMMAND})(?![A-Za-z])\s*(?:\[[^\]]*\]\s*)*"
    rf"(?:\{{(?P<braced>[^{{}}]*)\}}|(?P<bare>[^\s{{}}\\]+))"
)
# Commands that name a bibliography database: LaTeXML reads the `.bbl` the author compiled and
# never opens the database, so an absolute path there (a path on the author's machine, the usual
# case) reads nothing and is no reason to refuse the upload. `..`, `~`, `|` and URLs still are.
_BIBLIOGRAPHY = frozenset({"bibliography", "addbibresource"})
# A class or package file of the upload. A journal's own `.cls` that came with the paper holds
# `\immediate\openout` and `\input{\csname...}` as TeX Live's copy does, and the document is
# read with LaTeXML's binding of that class, not with the file. One that fails the scan is
# therefore left out of the project (`left_out`) instead of refusing the upload, unless a file
# names it with its suffix in an input command. It cannot stay in the project unscanned:
# LaTeXML does read such a file when a binding loads the raw file of its own name (88 of the
# bindings of 0.8.8 do, `algorithm` and `revtex4-1` among them: with the TeX tree hidden they
# find nothing, with a file of that name in the project they find it), and when the document
# loads the `latexml` package with `rawstyles` or `rawclasses`.
_STYLE_FILES = (".cls", ".clo", ".sty", ".bst")
_INPUT = re.compile(r"\\(?:input|include|InputIfFileExists)(?![A-Za-z])\s*\{?\s*([^\s{}]+)")
_FORBIDDEN = re.compile(
    r"\\write\s*\{?\s*-?18(?![A-Za-z])"  # \write18, \write 18, \write{18}
    r"|\\immediate\s*\\write\s*\{?\s*-?18(?![A-Za-z])"
    r"|\\openout(?![A-Za-z])"
    r"|\\input\s*\{?\s*\|"  # \input{|cmd}
    r"|\\directlua(?![A-Za-z])|\\luaexec(?![A-Za-z])|\\luadirect(?![A-Za-z])"
    r"|\\ShellEscape(?![A-Za-z])|\\pdfshellescape(?![A-Za-z])|\\shellescape(?![A-Za-z])"
    r"|\\usepackage(?:\[[^\]]*\])?\{[^}]*(?<![A-Za-z])(?:shellesc|minted|pythontex|sagetex|gnuplottex)(?![A-Za-z])[^}]*\}"
)
_COMMENT = re.compile(r"(?<!\\)%.*")
_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def strip_comments(text: str) -> str:
    return "\n".join(_COMMENT.sub("", line) for line in text.splitlines())


_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def _bad_path(value: str, *, absolute_ok: bool = False) -> bool:
    for name in value.split(","):
        name = name.strip()
        if not name:
            continue
        if ".." in name.replace("\\", "/").split("/") or "\x00" in name:
            return True
        if absolute_ok and (name.startswith(("/", "\\")) or _DRIVE_PATH.match(name)):
            continue
        if name.startswith(("/", "~", "\\", "|")) or _URL.match(name):
            return True
    return False


def problem(text: str) -> str | None:
    """The code of the first thing wrong in `text` ("shell", "path"), or None."""
    clean = strip_comments(text)
    if _FORBIDDEN.search(clean):
        return "shell"
    for match in _ARG.finditer(clean):
        value = match.group("braced") or match.group("bare") or ""
        if _bad_path(value, absolute_ok=match.group("cmd") in _BIBLIOGRAPHY):
            return "path"
    return None


def left_out(texts: dict[str, str]) -> set[str]:
    """The paths of the upload's class and package files that fail `problem` and that no file of
    the upload names with its suffix in `\\input`, `\\include` or `\\InputIfFileExists`. The
    conversion does not put them in the project it gives LaTeXML (`convert._project`), so no
    file LaTeXML can read has failed the scan."""
    named = {
        PurePosixPath(match.group(1)).name
        for text in texts.values()
        for match in _INPUT.finditer(strip_comments(text))
    }
    return {
        path
        for path, text in texts.items()
        if path.lower().endswith(_STYLE_FILES)
        and PurePosixPath(path).name not in named
        and problem(text) is not None
    }


def project_problem(texts: dict[str, str]) -> str | None:
    """`problem` for every text file of an upload (by path) that the conversion gives LaTeXML:
    all but the ones `left_out`."""
    skipped = left_out(texts)
    for path, text in texts.items():
        if path not in skipped and (found := problem(text)) is not None:
            return found
    return None
