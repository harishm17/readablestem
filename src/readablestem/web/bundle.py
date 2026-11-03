"""A zip of several finished documents: "Download all" (T3-4).

An instructor who converted a whole unit has a dozen files to fetch one by one. The zip holds
the student file of every document that is ready to share: finished, with no decision still
open (a document with decisions open is a draft, and the single download asks before giving
one; the zip leaves such documents out and says how many). Each file is named as its own
download is (`routes.jobs.download_filename`: the title and the start of the id, so two
documents called "Notes" never overwrite each other), and `contents.txt` lists them in words.

Built in memory, never on disk, like every copy of a document, so it is bounded: at most
`MAX_DOCUMENTS` documents and `MAX_BYTES` of files in one zip. Past either, the rest are
left out and the page says so, and the person downloads them in another go (the course page
or the single downloads).

The files are HTML, or Word (`docx`) or EPUB (`epub`) when the person asks for those (the
`format` of the download, `routes.bundle`). A Word zip holds fewer documents
(`MAX_DOCUMENTS_BY_FORMAT`): each one is a pandoc run, and a request waits for all of them.
A document that cannot be made in that file type is left out and counted, as the single
download refuses it.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field

MAX_DOCUMENTS = 25
# Each Word file is a pandoc child process (`pipeline.export_docx`), so a Word zip holds fewer.
MAX_DOCUMENTS_BY_FORMAT = {"docx": 10}
FORMATS = ("html", "docx", "epub")
_FILE_WORDS = {
    "html": "student files (HTML)",
    "docx": "Word files (.docx)",
    "epub": "EPUB files",
}
MAX_BYTES = 80 * 1024 * 1024
# A fixed time in every entry, so the same documents give the same zip and no clock leaks.
_STAMP = (1980, 1, 1, 0, 0, 0)


@dataclass
class Bundle:
    data: bytes
    included: list[tuple[str, str]] = field(default_factory=list)  # (title, file name)
    left_out_size: int = 0  # ready, but over the zip's limits
    held_back: int = 0  # not ready: decisions still open
    failed: int = 0  # ready, but could not be made as this file type
    file_type: str = "html"


def limit(file_type: str) -> int:
    """The most documents one zip of this file type holds."""
    return min(MAX_DOCUMENTS, MAX_DOCUMENTS_BY_FORMAT.get(file_type, MAX_DOCUMENTS))


def build(
    items: list[tuple[str, str, str | bytes]],
    *,
    held_back: int = 0,
    failed: int = 0,
    left_out: int = 0,
    file_type: str = "html",
) -> Bundle:
    """The zip of `items` (title, file name, HTML text or the file's bytes), in order, as far
    as the limits allow. `left_out` counts ready documents the caller did not make, because the
    zip was already full."""
    buffer = io.BytesIO()
    bundle = Bundle(
        b"", left_out_size=left_out, held_back=held_back, failed=failed, file_type=file_type
    )
    total = 0
    cap = limit(file_type)
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for title, name, content in items:
            raw = content.encode("utf-8") if isinstance(content, str) else content
            if len(bundle.included) >= cap or total + len(raw) > MAX_BYTES:
                bundle.left_out_size += 1
                continue
            _add(archive, name, raw)
            total += len(raw)
            bundle.included.append((title, name))
        _add(archive, "contents.txt", _contents(bundle).encode("utf-8"))
    bundle.data = buffer.getvalue()
    return bundle


def _add(archive: zipfile.ZipFile, name: str, raw: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_STAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, raw)


def _contents(bundle: Bundle) -> str:
    lines = [
        f"These are the {_FILE_WORDS[bundle.file_type]} of the documents that were ready to",
        "share when you downloaded them. Put each one in your course as a file and link it",
        "where the PDF was; do not paste it into a page editor.",
        "",
    ]
    lines += [f"{name}  {title}" for title, name in bundle.included]
    if bundle.held_back:
        lines += [
            "",
            f"Not included: {bundle.held_back} "
            f"{'document' if bundle.held_back == 1 else 'documents'} with decisions still to "
            "make. Finish the review, then download again.",
        ]
    if bundle.failed:
        lines += [
            "",
            f"Not included: {bundle.failed} "
            f"{'document' if bundle.failed == 1 else 'documents'} that could not be made as "
            f"{_FILE_WORDS[bundle.file_type]}. The HTML file of each is still available.",
        ]
    if bundle.left_out_size:
        lines += [
            "",
            f"Not included: {bundle.left_out_size} more "
            f"{'document' if bundle.left_out_size == 1 else 'documents'}, because one zip holds "
            f"at most {limit(bundle.file_type)} documents and {MAX_BYTES // (1024 * 1024)} MB. "
            "Download them one by one.",
        ]
    return "\n".join(lines) + "\n"
