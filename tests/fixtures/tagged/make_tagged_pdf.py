"""Rebuild the tagged-PDF fixture: tagged_notes.html -> tagged_notes.pdf.

Usage:
    uv run python tests/fixtures/tagged/make_tagged_pdf.py [--soffice PATH]

Needs LibreOffice (`soffice`). Writer exports the HTML as a tagged PDF ("UseTaggedPDF"), the
way Word's "Save as PDF" with tags does: headings are structure elements, each picture is a
`Figure` with the `Alt` text of its <img>. Two edits make it look like a Word export, which
Writer does not do by itself: its "Heading 1" style is mapped to the standard paragraph type
(Word writes `H1`), and one body-size paragraph ("Practical tips") is tagged `H3` the way a
Word user's Heading 3 style set in the body font would be. The HTML is the source of truth
(written for the tests); the PDF is committed so the test run needs no LibreOffice.
"""

from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
FIXED_DATE = "D:20261009000000Z"
MAX_BYTES = 200_000
PICTURE_COLOURS = {
    "fig_a.png": (90, 120, 200),
    "fig_b.png": (200, 120, 90),
    "fig_c.png": (90, 180, 120),
}


def png(colour: tuple[int, int, int], width: int = 220, height: int = 130) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = struct.pack(">I", len(data)) + kind + data
        return body + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + bytes(colour) * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )  # fmt: skip


def structure_elements(doc: pymupdf.Document) -> list[tuple[int, str]]:
    return [
        (x, doc.xref_get_key(x, "S")[1].lstrip("/"))
        for x in range(1, doc.xref_length())
        if "/StructElem" in doc.xref_object(x, compressed=True)
    ]


def build(soffice: str) -> Path:
    target = HERE / "tagged_notes.pdf"
    with tempfile.TemporaryDirectory(prefix="a11y-tagged-") as scratch:
        work = Path(scratch)
        shutil.copy(HERE / "tagged_notes.html", work / "tagged_notes.html")
        for name, colour in PICTURE_COLOURS.items():
            (work / name).write_bytes(png(colour))
        subprocess.run(
            [
                soffice,
                "--headless",
                f"-env:UserInstallation=file://{scratch}/profile",
                "--infilter=HTML (StarWriter)",
                "--convert-to",
                'pdf:writer_pdf_Export:{"UseTaggedPDF":{"type":"boolean","value":"true"}}',
                "--outdir",
                scratch,
                str(work / "tagged_notes.html"),
            ],
            check=True,
            capture_output=True,
        )
        doc = pymupdf.open(work / "tagged_notes.pdf")
        elements = structure_elements(doc)
        # Document order: the title (Heading 1), intro, Method (H2), method text, "Practical
        # tips", its text, then the pictures' paragraphs.
        titles = [x for x, kind in elements if kind.startswith("Heading")]
        paragraphs = [x for x, kind in elements if kind.startswith("Text")]
        for xref in titles:
            doc.xref_set_key(xref, "S", "/H1")
        doc.xref_set_key(paragraphs[2], "S", "/H3")  # "Practical tips"
        metadata = dict(doc.metadata or {})
        metadata.update(creationDate=FIXED_DATE, modDate=FIXED_DATE)
        doc.set_metadata(metadata)
        target.write_bytes(doc.tobytes(garbage=3, deflate=True))
        doc.close()
    size = target.stat().st_size
    if size > MAX_BYTES:
        raise SystemExit(f"{target.name} is {size} bytes; keep it under {MAX_BYTES}")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--soffice", default="soffice")
    args = parser.parse_args(argv)
    if shutil.which(args.soffice) is None:
        print(f"LibreOffice not found: {args.soffice}")
        return 2
    path = build(args.soffice)
    print(f"wrote {path.name} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
