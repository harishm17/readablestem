"""A zip of documents as a batch: its PDFs and LaTeX projects, each a document of its own.

A `.zip` upload (`FEATURE_BATCH_ZIP`) is read here, in memory, with the limits of
`web.archive_intake` (20 MB zipped, 100 MB unpacked, 500 entries, no links, no absolute or `..`
paths, nested zips refused, an extension allowlist). What it holds decides what it is:

- a zip whose root has a main `.tex` file is **one LaTeX project** (`split` returns None and
  the upload goes the LaTeX source way, `routes.jobs._place_source`);
- otherwise every PDF is a document, and every folder that has a main `.tex` file is a LaTeX
  project, re-zipped from its own files (so the source path checks it again, as it checks any
  upload). Anything else in the zip (a stray `.tex` that is nobody's main file, a picture
  outside a project) is left out, not refused.

Each member then goes through exactly what an uploaded file goes through (`_place_files`): the
probe, the page quota, the caps on documents waiting and kept. This module only finds the
members. A zip that holds no document is refused (`zip_empty`).

Refusal codes (`zip_bad`, `zip_limits`, `zip_unsafe`, `zip_unsupported`, `zip_empty`) are
categories with sentences in `routes.jobs.batch_messages`; none carries a file name.
"""

from __future__ import annotations

import io
import posixpath
import zipfile

from readablestem.source import intake as source_intake
from readablestem.web import archive_intake as arc

# What a member that does not read can raise (a bad CRC, a bad stream, a refusal).
READ_ERRORS = (
    arc.ArchiveRefused,
    OSError,
    EOFError,
    RuntimeError,
    NotImplementedError,
    ValueError,
    zipfile.BadZipFile,
)
# What one request may unpack, in all its zips together, as a multiple of the upload limit
# (`Settings.upload_limit_bytes`): `RequestBudget`. Every byte read out of any zip of the request
# is charged, whatever it is read for, and the request is refused (`zip_limits`) when it is spent.
UNPACKED_FACTOR = 2
# What one request holds in memory at its peak, besides the body itself (at most the upload limit,
# zips included): the text of the `.tex` files being looked at (`TEXT_TOTAL_BYTES`, freed before
# any document is placed) and then one document at a time, a PDF (at most the upload limit, a
# bigger one is refused without being read) or a project (at most the budget), released before
# the next is read. The upload gate (`web.uploads.UploadGate`) lets 3 requests in at once.
TEXT_TOTAL_BYTES = source_intake.MAX_TEXT_TOTAL_BYTES
_ZIP_CODES = {
    "not_zip": "zip_bad",
    "bad_zip": "zip_bad",
    "encrypted": "zip_bad",
    "too_large": "zip_limits",
    "too_many_entries": "zip_limits",
    "unpacked_too_large": "zip_limits",
    "bomb": "zip_limits",
    "unsafe_path": "zip_unsafe",
    "symlink": "zip_unsafe",
    "eps": "zip_unsupported",
    "unsupported_file": "zip_unsupported",
}


class RequestBudget:
    """The bytes one request may unpack out of all its zips together."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.used = 0

    @classmethod
    def for_upload_limit(cls, upload_limit_bytes: int) -> RequestBudget:
        return cls(UNPACKED_FACTOR * upload_limit_bytes)

    @property
    def left(self) -> int:
        return max(self.total - self.used, 0)

    def take(self, n: int) -> None:
        self.used += n
        if self.used > self.total:
            raise arc.ArchiveRefused("unpacked_too_large")


def _read_member(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, budget: RequestBudget, cap: int
) -> bytes:
    """One member's bytes, at most `cap`, charged to `budget` as they are read (a header may lie
    about its size, `archive_intake.extract`): reading stops at what is left of the budget."""
    with archive.open(info) as handle:
        raw = handle.read(min(cap, budget.left) + 1)
    budget.take(len(raw))
    if len(raw) > cap:
        raise arc.ArchiveRefused("unpacked_too_large")
    return raw


class Member:
    """A document of a batch zip, read only when it is its turn: `data` reads it (and charges the
    request's budget), `release` lets the bytes go. `size` is the header's claim, used to refuse a
    PDF over the upload limit before any of it is read."""

    def __init__(self, filename: str, size: int, load) -> None:
        self.filename = filename
        self.size = size
        self._load = load
        self._data: bytes | None = None

    @property
    def data(self) -> bytes:
        if self._data is None:
            self._data = self._load()
        return self._data

    def release(self) -> None:
        self._data = None


def split(
    filename: str,
    data: bytes,
    *,
    budget: RequestBudget | None = None,
    limits: arc.ArchiveLimits | None = None,
    member_cap: int | None = None,
) -> list[Member] | str | None:
    """The documents of a batch zip, not yet read, a refusal code, or None when the zip is not a
    batch (it is one LaTeX project, or it is not a zip: the caller goes on as before).

    Only the `.tex` files are read here (to find the projects), charged to `budget` (a fresh one
    of `UNPACKED_FACTOR` times the zip's own size when none is given); each PDF and project is
    read when its `Member.data` is asked for. `member_cap`: the most a member may be."""
    if not arc.looks_like_zip(data):
        return None
    spend = budget or RequestBudget.for_upload_limit(max(len(data), 1))
    limits = limits or arc.ArchiveLimits(max_unpacked_bytes=spend.total)
    cap = member_cap or limits.max_file_bytes
    try:
        archive, chosen = arc.plan(data, limits)
    except arc.ArchiveRefused as exc:
        return _ZIP_CODES.get(exc.code, "zip_bad")
    try:
        texts: dict[str, str] = {}
        text_bytes = 0
        for info, entry in chosen:
            if not entry.path.lower().endswith((".tex", ".ltx")):
                continue
            raw = _read_member(archive, info, spend, min(source_intake.MAX_TEXT_FILE_BYTES, cap))
            text_bytes += len(raw)
            if text_bytes > TEXT_TOTAL_BYTES:
                raise arc.ArchiveRefused("unpacked_too_large")
            texts[entry.path] = source_intake._decode(raw)
    except READ_ERRORS:
        archive.close()
        return "zip_limits"
    roots = _project_roots(texts)
    del texts
    if "" in roots:
        archive.close()
        return None  # the zip is one project: the LaTeX source path takes it
    infos = {entry.path: (info, entry) for info, entry in chosen}
    pdfs = sorted(
        (p for p in infos if p.lower().endswith(".pdf") and _under(p, roots) is None),
        key=str.lower,
    )
    if not pdfs and not roots:
        archive.close()
        return "zip_empty"
    out: list[Member] = []
    for path in pdfs:
        info = infos[path][0]
        out.append(
            Member(
                posixpath.basename(path),
                info.file_size,
                lambda info=info: _read_member(archive, info, spend, cap),
            )
        )
    for root in sorted(roots, key=str.lower):
        paths = [p for p in infos if p.startswith(root + "/")]
        size = sum(infos[p][0].file_size for p in paths)

        def build(paths=paths, root=root) -> bytes:
            files = {
                p[len(root) + 1 :]: _read_member(archive, infos[p][0], spend, cap) for p in paths
            }
            return _zip_of(files)

        out.append(Member(f"{posixpath.basename(root)}.zip", size, build))
    return out


def _under(path: str, roots: set[str]) -> str | None:
    """The project root `path` lies under, or None."""
    return next((root for root in roots if root == "" or path.startswith(root + "/")), None)


def _project_roots(texts: dict[str, str]) -> set[str]:
    """The folders that hold a main `.tex` file (a document class and a document body) directly
    (`""` is the zip's own root). A folder under another project is part of that project."""
    roots: set[str] = set()
    folders = sorted({posixpath.dirname(p) for p in texts}, key=lambda f: (f.count("/"), f))
    for folder in folders:
        if _under(folder + "/x", roots) is not None:
            continue
        direct = (t for p, t in texts.items() if posixpath.dirname(p) == folder)
        if any(source_intake._is_candidate(t) for t in direct):
            roots.add(folder)
    return roots


def _zip_of(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for name, raw in files.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            out.writestr(info, raw)
    return buffer.getvalue()
