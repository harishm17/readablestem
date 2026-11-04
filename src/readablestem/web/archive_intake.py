"""Unpacking an uploaded zip of a LaTeX project (or, for a batch, of documents) safely.

A zip is untrusted input twice over: the archive itself (a bomb, a path that leaves the folder,
a link that points at a file of this machine) and what is in it (TeX is a program: the sandbox
in `core/sandbox.py` is what stops that; this module only keeps the bytes that reach the
sandbox small and plain). Every limit here is checked on the central directory before anything
is written, and again on the bytes as they are read, because a header can lie about its size.

What is refused, each with a short category `code` (never a file name, never content):

- `not_zip`, `encrypted`, `bad_zip` (damaged, or a member that does not read);
- `too_large` (the archive is over `max_zip_bytes`), `too_many_entries` (a directory counts),
  `unpacked_too_large` (declared or actual bytes over `max_unpacked_bytes`; a member over
  `max_file_bytes`), `bomb` (a member that expands more than `max_ratio` times);
- `unsafe_path` (absolute, a drive letter, a backslash, `..`, a control character, too deep or
  too long, or a name used twice), `symlink` (a link, a device or any member that is not a
  regular file or a folder);
- `eps` (EPS figures are not read in this release) and `unsupported_file` (an extension that is
  not on the list below; nested zips included).

Build leftovers (`.aux`, `.log`, `.DS_Store`, a `__MACOSX` folder, ...) are skipped, not
refused: a project zipped from a working folder has them. They still count against the limits.
The unpacked files are written under one folder the caller gives, with the member's own relative
path, through `os.open` with `O_EXCL` and no following of links, after the path has been checked
to stay inside it. Nothing is executed or opened as a document here.
"""

from __future__ import annotations

import io
import os
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# Extensions that are written out. Lower case; `.synctex.gz` and the like are in `IGNORED`.
TEXT_SUFFIXES = frozenset(
    {".tex", ".sty", ".cls", ".clo", ".def", ".cfg", ".bst", ".bib", ".bbl", ".ltx", ".txt",
     ".dat", ".csv", ".md"}
)  # fmt: skip
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".pdf"})
ALLOWED_SUFFIXES = TEXT_SUFFIXES | IMAGE_SUFFIXES
# Left out without a word: what a TeX run or an operating system leaves in a project folder.
IGNORED_SUFFIXES = frozenset(
    {".aux", ".log", ".out", ".toc", ".lof", ".lot", ".nav", ".snm", ".vrb", ".fls", ".blg",
     ".idx", ".ilg", ".ind", ".glo", ".gls", ".bcf", ".run.xml", ".synctex", ".synctex.gz",
     ".fdb_latexmk",
     ".ds_store", ".gz"}
)  # fmt: skip
IGNORED_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
IGNORED_FOLDERS = frozenset({"__macosx", ".git", ".svn", ".idea", ".vscode"})

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE = re.compile(r"^[A-Za-z]:")


class ArchiveRefused(ValueError):
    """The zip is refused; `code` is a category (module docstring), safe to log."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ArchiveLimits:
    max_zip_bytes: int = 20 * 1024 * 1024
    max_unpacked_bytes: int = 100 * 1024 * 1024
    max_entries: int = 500
    max_file_bytes: int = 50 * 1024 * 1024
    max_ratio: int = 200  # a member may expand this many times; over it is a bomb
    ratio_floor_bytes: int = 1024 * 1024  # small members are not judged by their ratio
    max_depth: int = 8
    max_name_chars: int = 200
    # A `.pdf` member declared bigger than this is neither counted nor refused here: the caller
    # names it as refused and never reads it (`web.batch_intake`). None: no such exemption.
    unread_pdf_over_bytes: int | None = None


DEFAULT_LIMITS = ArchiveLimits()


@dataclass(frozen=True)
class Entry:
    """One member that will be written: its clean relative path (POSIX) and declared size."""

    path: str
    size: int


def looks_like_zip(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04" or data[:4] == b"PK\x05\x06"


def _clean_name(raw: str, limits: ArchiveLimits) -> tuple[PurePosixPath, bool]:
    """The member's path and whether it names a folder; `ArchiveRefused("unsafe_path")`."""
    if not raw or _CONTROL.search(raw) or "\\" in raw or _DRIVE.match(raw):
        raise ArchiveRefused("unsafe_path")
    if raw.startswith("/") or "//" in raw.rstrip("/"):
        raise ArchiveRefused("unsafe_path")
    is_dir = raw.endswith("/")
    segments = raw.rstrip("/").split("/")
    if len(segments) > limits.max_depth or len(raw) > limits.max_name_chars:
        raise ArchiveRefused("unsafe_path")
    if any(p in ("..", ".", "") or len(p) > 120 for p in segments):
        raise ArchiveRefused("unsafe_path")
    path = PurePosixPath(*segments)
    return path, is_dir


def suffix_of(name: str) -> str:
    lowered = name.lower()
    for double in (".run.xml", ".synctex.gz"):
        if lowered.endswith(double):
            return double
    return PurePosixPath(lowered).suffix


def plan(
    data: bytes,
    limits: ArchiveLimits = DEFAULT_LIMITS,
    *,
    allowed: frozenset[str] = ALLOWED_SUFFIXES,
) -> tuple[zipfile.ZipFile, list[tuple[zipfile.ZipInfo, Entry]]]:
    """Open `data` as a zip and check it all against `limits` from its central directory.
    Returns the open archive and the members to write. Raises `ArchiveRefused`. The caller
    closes the archive."""
    if len(data) > limits.max_zip_bytes:
        raise ArchiveRefused("too_large")
    if not looks_like_zip(data):
        raise ArchiveRefused("not_zip")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError):
        raise ArchiveRefused("bad_zip") from None
    try:
        infos = archive.infolist()
        if len(infos) > limits.max_entries:
            raise ArchiveRefused("too_many_entries")
        seen: set[str] = set()
        folders: set[str] = set()
        total = 0
        chosen: list[tuple[zipfile.ZipInfo, Entry]] = []
        for info in infos:
            path, is_dir = _clean_name(info.filename, limits)
            mode = info.external_attr >> 16
            kind = stat.S_IFMT(mode)  # 0 when the writer set only permission bits
            if kind and kind not in (stat.S_IFREG, stat.S_IFDIR):
                raise ArchiveRefused("symlink")  # a link, a device, a fifo, a socket
            if info.flag_bits & 0x1:
                raise ArchiveRefused("encrypted")
            key = path.as_posix().casefold()
            folder = is_dir or info.is_dir()
            if key in seen or (key in folders and not folder):
                raise ArchiveRefused("unsafe_path")  # the same name twice, or a file over a folder
            parents = [str(p).casefold() for p in path.parents if str(p) != "."]
            if any(p in seen for p in parents):
                raise ArchiveRefused("unsafe_path")  # a file used as a folder
            folders.update(parents)
            if folder:
                folders.add(key)
                continue
            seen.add(key)
            if info.file_size < 0 or info.compress_size < 0:
                raise ArchiveRefused("bad_zip")
            unread = (
                limits.unread_pdf_over_bytes is not None
                and path.suffix.lower() == ".pdf"
                and info.file_size > limits.unread_pdf_over_bytes
            )
            if unread:
                chosen.append((info, Entry(path.as_posix(), info.file_size)))
                continue
            total += info.file_size
            if total > limits.max_unpacked_bytes or info.file_size > limits.max_file_bytes:
                raise ArchiveRefused("unpacked_too_large")
            if (
                info.file_size > limits.ratio_floor_bytes
                and info.file_size > limits.max_ratio * max(info.compress_size, 1)
            ):
                raise ArchiveRefused("bomb")
            lower_parts = [p.casefold() for p in path.parts]
            if (
                any(p in IGNORED_FOLDERS for p in lower_parts[:-1])
                or lower_parts[-1] in IGNORED_NAMES
            ):
                continue
            suffix = suffix_of(path.name)
            if suffix in IGNORED_SUFFIXES:
                continue
            if suffix in (".eps", ".ps", ".epsi"):
                raise ArchiveRefused("eps")
            if suffix not in allowed:
                raise ArchiveRefused("unsupported_file")
            chosen.append((info, Entry(path.as_posix(), info.file_size)))
        return archive, chosen
    except BaseException:
        archive.close()
        raise


def extract(
    data: bytes,
    dest: Path,
    limits: ArchiveLimits = DEFAULT_LIMITS,
    *,
    allowed: frozenset[str] = ALLOWED_SUFFIXES,
) -> list[Entry]:
    """Write the allowed members of the zip `data` under the existing, empty folder `dest`.

    Returns what was written. On any refusal everything written so far is the caller's to
    delete (it owns `dest`). A member's bytes are counted as they are read: a header that
    understates its size stops at its own limit, so the total and the per-file caps hold for
    what is written, not for what is claimed."""
    archive, chosen = plan(data, limits, allowed=allowed)
    root = dest.resolve()
    written: list[Entry] = []
    budget = limits.max_unpacked_bytes
    try:
        for info, entry in chosen:
            target = (root / entry.path).resolve()
            if root not in target.parents:  # belt and braces: the name was already clean
                raise ArchiveRefused("unsafe_path")
            size = 0
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except OSError:
                raise ArchiveRefused("unsafe_path") from None
            with os.fdopen(fd, "wb") as out, archive.open(info) as source:
                try:
                    while chunk := source.read(1 << 16):
                        size += len(chunk)
                        budget -= len(chunk)
                        if size > limits.max_file_bytes or budget < 0:
                            raise ArchiveRefused("unpacked_too_large")
                        if size > limits.ratio_floor_bytes and size > limits.max_ratio * max(
                            info.compress_size, 1
                        ):
                            raise ArchiveRefused("bomb")
                        out.write(chunk)
                except (zipfile.BadZipFile, EOFError, OSError, RuntimeError, NotImplementedError):
                    raise ArchiveRefused("bad_zip") from None
            written.append(Entry(entry.path, size))
        return written
    finally:
        archive.close()
