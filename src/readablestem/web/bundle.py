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
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field

MAX_DOCUMENTS = 25
MAX_BYTES = 80 * 1024 * 1024
# A fixed time in every entry, so the same documents give the same zip and no clock leaks.
_STAMP = (1980, 1, 1, 0, 0, 0)


@dataclass
class Bundle:
    data: bytes
    included: list[tuple[str, str]] = field(default_factory=list)  # (title, file name)
    left_out_size: int = 0  # ready, but over the zip's limits
    held_back: int = 0  # not ready: decisions still open


def build(items: list[tuple[str, str, str]], *, held_back: int = 0) -> Bundle:
    """The zip of `items` (title, file name, HTML), in order, as far as the limits allow."""
    buffer = io.BytesIO()
    bundle = Bundle(b"", held_back=held_back)
    total = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for title, name, html in items:
            raw = html.encode("utf-8")
            if len(bundle.included) >= MAX_DOCUMENTS or total + len(raw) > MAX_BYTES:
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
        "These are the student files (HTML) of the documents that were ready to share when",
        "you downloaded them. Put each one in your course as a file and link it where the",
        "PDF was; do not paste it into a page editor.",
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
    if bundle.left_out_size:
        lines += [
            "",
            f"Not included: {bundle.left_out_size} more "
            f"{'document' if bundle.left_out_size == 1 else 'documents'}, because one zip holds "
            f"at most {MAX_DOCUMENTS} documents and {MAX_BYTES // (1024 * 1024)} MB. "
            "Download them one by one.",
        ]
    return "\n".join(lines) + "\n"
