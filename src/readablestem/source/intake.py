"""What an uploaded `.tex` or `.zip` is, before anything runs: its kind, its main file, the
pages it will count as, and whether it may go to the converter at all.

`inspect` reads the files into memory (text files only, capped), applies the zip limits
(`web.archive_intake`) and the lint (`source.lint`), finds the main file and estimates the
pages. It never starts LaTeXML. The conversion child calls it again on the same bytes: the web
process's answer is not trusted there.

Pages: the quota unit for a source is one page per 3,000 characters of TeX (comments removed,
`.tex` files only) or one per figure (`\\includegraphics`, a `tikzpicture`), whichever is larger,
and at least 1 (docs/web.md, "LaTeX source"). It is an estimate made before the job exists, so
the day's quota can be taken at upload as for a PDF.

Refusals are `SourceRefused(code)` with the codes the upload page words (`web.messages`):
`source_bad_zip`, `source_zip_limits`, `source_unsafe_zip`, `source_unsupported_file`,
`source_unsafe`, `source_no_main`, `source_many_mains`, `source_not_utf8`, `source_beamer`,
`source_too_many_pages`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from readablestem.source import lint
from readablestem.source.envelope import EnvelopeError, is_envelope, unwrap, wrap
from readablestem.web import archive_intake as arc

CHARS_PER_PAGE = 3000
MAX_TEXT_FILE_BYTES = 4 * 1024 * 1024  # a larger .tex is refused: no paper has one
MAX_TEXT_TOTAL_BYTES = 24 * 1024 * 1024
# Beamer is read in a later step (docs/web.md, "LaTeX source"); until its switch is on, a
# deck is refused with the answer "upload the PDF".
BEAMER_ENABLED = False

_ZIP_CODES = {
    "not_zip": "source_bad_zip",
    "bad_zip": "source_bad_zip",
    "encrypted": "source_bad_zip",
    "too_large": "source_zip_limits",
    "too_many_entries": "source_zip_limits",
    "unpacked_too_large": "source_zip_limits",
    "bomb": "source_zip_limits",
    "unsafe_path": "source_unsafe_zip",
    "symlink": "source_unsafe_zip",
    "eps": "source_unsupported_file",
    "unsupported_file": "source_unsupported_file",
}
_DOCUMENTCLASS = re.compile(r"\\documentclass\b\s*(?:\[[^\]]*\]\s*)?\{([^}]*)\}")
_BEGIN_DOCUMENT = re.compile(r"\\begin\s*\{document\}")
_INCLUDE = re.compile(
    r"\\(?:input|include|subfile|subimport)\b\s*(?:\{[^}]*\}\s*)?\{?([^\s{}]+)\}?"
)
_FIGURES = re.compile(r"\\includegraphics\b|\\begin\s*\{tikzpicture\}")
PREFERRED_MAINS = ("main", "ms", "paper", "article", "document", "slides", "thesis", "report")


class SourceRefused(ValueError):
    """The source is refused before it is queued; `code` is a category, safe to log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SourceInfo:
    kind: str  # "tex" or "zip"
    main: str  # path of the main file, POSIX, relative to the project
    pages: int
    beamer: bool
    files: int


def kind_of(filename: str, data: bytes) -> str | None:
    """ "tex" or "zip" when the name and the first bytes say a LaTeX source; else None (a PDF,
    a Word file, anything else goes the old way). A Word or PowerPoint file is a zip too, so the
    name decides: `.zip` and `.tex` only."""
    lower = filename.lower()
    if lower.endswith(".tex") and not data.startswith(b"%PDF-"):
        return "tex"
    if lower.endswith(".zip") and arc.looks_like_zip(data):
        return "zip"
    return None


def _decode(raw: bytes) -> str:
    """TeX source as text: UTF-8, else Latin-1 (older projects)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def read_texts(kind: str, data: bytes, limits: arc.ArchiveLimits) -> dict[str, str]:
    """The `.tex` files of the upload by path (a single `.tex` is `main.tex`), plus the other
    text files the lint should see (`.sty`, `.cls`, `.bib`...). Applies the zip limits."""
    if kind == "tex":
        if len(data) > MAX_TEXT_FILE_BYTES:
            raise SourceRefused("source_zip_limits")
        return {"main.tex": _decode(data)}
    try:
        archive, chosen = arc.plan(data, limits)
    except arc.ArchiveRefused as exc:
        raise SourceRefused(_ZIP_CODES.get(exc.code, "source_bad_zip")) from None
    texts: dict[str, str] = {}
    total = 0
    try:
        for info, entry in chosen:
            if arc.suffix_of(entry.path) not in arc.TEXT_SUFFIXES:
                continue
            if info.file_size > MAX_TEXT_FILE_BYTES:
                raise SourceRefused("source_zip_limits")
            with archive.open(info) as handle:
                raw = handle.read(MAX_TEXT_FILE_BYTES + 1)
            if len(raw) > MAX_TEXT_FILE_BYTES:
                raise SourceRefused("source_zip_limits")
            total += len(raw)
            if total > MAX_TEXT_TOTAL_BYTES:
                raise SourceRefused("source_zip_limits")
            texts[entry.path] = _decode(raw)
    except (OSError, EOFError, RuntimeError, NotImplementedError, ValueError) as exc:
        if isinstance(exc, SourceRefused):
            raise
        raise SourceRefused("source_bad_zip") from None
    finally:
        archive.close()
    return texts


def _is_candidate(text: str) -> bool:
    clean = lint.strip_comments(text)
    return bool(_DOCUMENTCLASS.search(clean) and _BEGIN_DOCUMENT.search(clean))


def find_main(texts: dict[str, str], hint: str | None = None) -> str:
    """The main file's path. A `hint` (the person's choice) must name a candidate. Otherwise:
    the candidates (a `\\documentclass` and a `\\begin{document}`) that no other file includes;
    one is the answer; several are narrowed by the shallowest folder and a usual name
    (`main`, `paper`, ...); still several is `source_many_mains`."""
    tex = {p: t for p, t in texts.items() if p.lower().endswith((".tex", ".ltx"))}
    candidates = [p for p, t in tex.items() if _is_candidate(t)]
    if hint:
        wanted = hint.strip().replace("\\", "/").lstrip("./")
        if wanted not in candidates:
            raise SourceRefused("source_no_main")
        return wanted
    if not candidates:
        raise SourceRefused("source_no_main")
    included: set[str] = set()
    for text in tex.values():
        for match in _INCLUDE.finditer(lint.strip_comments(text)):
            name = match.group(1).replace("\\", "/")
            for candidate in (name, f"{name}.tex"):
                included.update(p for p in tex if p == candidate or p.endswith("/" + candidate))
    roots = [p for p in candidates if p not in included] or candidates
    if len(roots) == 1:
        return roots[0]
    shallow = min(p.count("/") for p in roots)
    roots = [p for p in roots if p.count("/") == shallow]
    if len(roots) == 1:
        return roots[0]
    named = [p for p in roots if p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() in PREFERRED_MAINS]
    if len(named) == 1:
        return named[0]
    raise SourceRefused("source_many_mains")


def estimate_pages(texts: dict[str, str]) -> int:
    chars = 0
    figures = 0
    for path, text in texts.items():
        if not path.lower().endswith((".tex", ".ltx")):
            continue
        clean = lint.strip_comments(text)
        chars += len(clean)
        figures += len(_FIGURES.findall(clean))
    return max(1, math.ceil(chars / CHARS_PER_PAGE), figures)


def inspect(
    filename: str,
    data: bytes,
    *,
    main: str | None = None,
    limits: arc.ArchiveLimits = arc.DEFAULT_LIMITS,
    max_pages: int | None = None,
) -> SourceInfo:
    """Check an upload that `kind_of` called a source. Raises `SourceRefused`."""
    kind = kind_of(filename, data)
    if kind is None:
        raise SourceRefused("source_bad_zip")
    if kind == "tex" and (not data or b"\x00" in data[:4096]):
        raise SourceRefused("source_no_main")
    texts = read_texts(kind, data, limits)
    for text in texts.values():
        if lint.problem(text) is not None:
            raise SourceRefused("source_unsafe")
    chosen = find_main(texts, main)
    beamer = _is_beamer(texts[chosen])
    if beamer and not BEAMER_ENABLED:
        raise SourceRefused("source_beamer")
    pages = estimate_pages(texts)
    if max_pages is not None and pages > max_pages:
        raise SourceRefused("source_too_many_pages")
    return SourceInfo(kind=kind, main=chosen, pages=pages, beamer=beamer, files=len(texts))


def _is_beamer(text: str) -> bool:
    match = _DOCUMENTCLASS.search(lint.strip_comments(text))
    return bool(match and match.group(1).strip().lower() == "beamer")


__all__ = [
    "EnvelopeError",
    "SourceInfo",
    "SourceRefused",
    "inspect",
    "is_envelope",
    "kind_of",
    "unwrap",
    "wrap",
]
