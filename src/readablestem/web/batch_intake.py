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
from readablestem.web.uploads import UploadedFile

# What a member that does not read can raise (a bad CRC, a bad stream, a refusal).
_READ_ERRORS = (
    arc.ArchiveRefused,
    OSError,
    EOFError,
    RuntimeError,
    NotImplementedError,
    ValueError,
    zipfile.BadZipFile,
)
# A batch is held in memory while it is placed: tighter than a single project's 100 MB.
BATCH_LIMITS = arc.ArchiveLimits(max_unpacked_bytes=80 * 1024 * 1024)
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


def _read_members(
    archive: zipfile.ZipFile, chosen: list[tuple[zipfile.ZipInfo, arc.Entry]], wanted
) -> dict[str, bytes]:
    """Bytes of the members whose path `wanted` accepts, counted as they are read (a header may
    lie about its size, `archive_intake.extract`)."""
    out: dict[str, bytes] = {}
    budget = BATCH_LIMITS.max_unpacked_bytes
    for info, entry in chosen:
        if not wanted(entry.path):
            continue
        with archive.open(info) as handle:
            raw = handle.read(min(BATCH_LIMITS.max_file_bytes, budget) + 1)
        budget -= len(raw)
        if budget < 0 or len(raw) > BATCH_LIMITS.max_file_bytes:
            raise arc.ArchiveRefused("unpacked_too_large")
        out[entry.path] = raw
    return out


def split(filename: str, data: bytes) -> list[UploadedFile] | str | None:
    """The documents of a batch zip as uploaded files, a refusal code, or None when the zip is
    not a batch (it is one LaTeX project, or it is not a zip: the caller goes on as before)."""
    if not arc.looks_like_zip(data):
        return None
    try:
        archive, chosen = arc.plan(data, BATCH_LIMITS)
    except arc.ArchiveRefused as exc:
        return _ZIP_CODES.get(exc.code, "zip_bad")
    try:
        try:
            tex = _read_members(archive, chosen, lambda p: p.lower().endswith((".tex", ".ltx")))
        except _READ_ERRORS:
            return "zip_limits"
        texts = {path: source_intake._decode(raw) for path, raw in tex.items()}
        roots = _project_roots(texts)
        if "" in roots:
            return None  # the zip is one project: the LaTeX source path takes it
        pdfs = {
            e.path
            for _, e in chosen
            if e.path.lower().endswith(".pdf") and _under(e.path, roots) is None
        }
        if not pdfs and not roots:
            return "zip_empty"
        try:
            members = _read_members(
                archive, chosen, lambda p: p in pdfs or _under(p, roots) is not None
            )
        except _READ_ERRORS:
            return "zip_limits"
    finally:
        archive.close()
    out = [
        UploadedFile(posixpath.basename(path), members[path])
        for path in sorted(pdfs, key=str.lower)
    ]
    for root in sorted(roots, key=str.lower):
        files = {p[len(root) + 1 :]: raw for p, raw in members.items() if p.startswith(root + "/")}
        out.append(UploadedFile(f"{posixpath.basename(root)}.zip", _zip_of(files)))
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
