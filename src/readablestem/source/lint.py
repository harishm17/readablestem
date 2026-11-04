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

# Commands whose argument is a file name. `\input file` and `\input{file}` are both read.
_FILE_COMMANDS = (
    "input", "include", "includegraphics", "includepdf", "InputIfFileExists", "usepackage",
    "RequirePackage", "documentclass", "LoadClass", "bibliography", "addbibresource",
    "lstinputlisting", "verbatiminput", "import", "subimport", "includeonly", "openin",
    "read", "csvreader", "pgfplotstableread", "pgfplotsinvokeforeach",
)  # fmt: skip
_COMMAND = "|".join(_FILE_COMMANDS)
_ARG = re.compile(
    rf"\\(?:{_COMMAND})(?![A-Za-z])\s*(?:\[[^\]]*\]\s*)*(?:\{{([^{{}}]*)\}}|([^\s{{}}\\]+))"
)
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


def _bad_path(value: str) -> bool:
    for name in value.split(","):
        name = name.strip()
        if not name:
            continue
        if name.startswith(("/", "~", "\\", "|")) or _URL.match(name):
            return True
        if ".." in name.replace("\\", "/").split("/"):
            return True
        if "\x00" in name:
            return True
    return False


def problem(text: str) -> str | None:
    """The code of the first thing wrong in `text` ("shell", "path"), or None."""
    clean = strip_comments(text)
    if _FORBIDDEN.search(clean):
        return "shell"
    for match in _ARG.finditer(clean):
        if _bad_path(match.group(1) or match.group(2) or ""):
            return "path"
    return None
