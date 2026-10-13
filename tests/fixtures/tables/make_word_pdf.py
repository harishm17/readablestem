"""Rebuild the Word-style table fixture: word_tables.html -> word_tables.pdf.

Usage:
    uv run python tests/fixtures/tables/make_word_pdf.py [--soffice PATH]

Needs LibreOffice (`soffice`). Writer imports the HTML as a text document and exports it
with its own PDF filter, the way a Writer (or Word) user's "Save as PDF" does: table
borders come out as separate rules per cell edge and header shading as filled
rectangles, unlike a browser print. The HTML is the source of truth (written for the
tests, no third-party text); the PDF is committed so the test run needs no LibreOffice.
Dates are fixed so a rebuild only differs where the content or the LibreOffice version
does. Keep the PDF under 300 KB, and commit a rebuilt one only after
`uv run pytest tests/test_tables_fixtures.py` passes on it.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
FIXED_DATE = "D:20261009000000Z"
MAX_BYTES = 300_000


def build(soffice: str) -> Path:
    source = HERE / "word_tables.html"
    target = HERE / "word_tables.pdf"
    with tempfile.TemporaryDirectory(prefix="a11y-word-") as scratch:
        subprocess.run(
            [
                soffice,
                "--headless",
                f"-env:UserInstallation=file://{scratch}/profile",
                "--infilter=HTML (StarWriter)",
                "--convert-to",
                "pdf",
                "--outdir",
                scratch,
                str(source),
            ],
            check=True,
            capture_output=True,
        )
        doc = pymupdf.open(Path(scratch) / "word_tables.pdf")
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
