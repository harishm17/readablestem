"""PDF files written object by object, and the saves every builder shares.

PyMuPDF cannot make a 20,000-pixel image without holding it in memory, so the files with
huge images are written by hand: a compressed stream of one gray value and the few objects
around it.
"""

from __future__ import annotations

import functools
import io
import zipfile
import zlib

import pymupdf

ZIP_DATE = (2026, 1, 1, 0, 0, 0)  # every archive entry carries this date, so bytes repeat


def save(doc: pymupdf.Document, *, clean: bool = True) -> bytes:
    """The document as bytes that are the same on every run (no new file id)."""
    if clean:
        return doc.tobytes(garbage=3, deflate=True, no_new_id=True)
    return doc.tobytes(no_new_id=True)


@functools.cache
def gray_image(side: int, level: int = 9) -> bytes:
    """The compressed pixel stream of a `side` by `side` image of one gray value."""
    packer = zlib.compressobj(level)
    row = bytes([200]) * side
    parts = [packer.compress(row) for _ in range(side)]
    parts.append(packer.flush())
    return b"".join(parts)


def image_object(side: int, stream: bytes) -> bytes:
    return (
        b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray"
        b" /BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n"
        % (side, side, len(stream))
        + stream
        + b"\nendstream"
    )


def stream_object(content: bytes) -> bytes:
    return b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"


HELVETICA = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"


def write_pdf(objects: list[bytes]) -> bytes:
    """A PDF 1.7 file of these objects, numbered from 1; object 1 is the catalog."""
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    table = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        table,
    )
    return bytes(out)


def write_zip(entries: list[tuple[str, bytes]], *, stored: bool = False) -> bytes:
    """A zip of these entries with a fixed date and mode on each, so the bytes repeat."""
    out = io.BytesIO()
    method = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(out, "w", method) as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.compress_type = method
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return out.getvalue()


def rezip(data: bytes) -> bytes:
    """The same archive with the fixed date on every entry (a .docx is a zip)."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return write_zip([(info.filename, archive.read(info)) for info in archive.infolist()])
