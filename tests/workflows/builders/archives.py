"""Zip uploads: a batch of documents, a zip inside a zip, a folder zipped on a Mac, and zips
that mix a PDF with another kind of file. First made in the audit of November 9, 2026, with
two public course PDFs inside; here the PDFs inside are the typed test documents."""

from __future__ import annotations

from . import fixture
from .edge import kinetics_problem_set_docx, lecture, text_named_pdf
from .hostile import notes
from .rawpdf import save, write_zip

ORIGIN = "audit lane B edge files, 2026-11-09"


def _lecture_pdf() -> bytes:
    return save(lecture(5))


def _notes_pdf() -> bytes:
    return save(notes(8))


@fixture(
    "docs-batch.zip",
    origin=ORIGIN,
    shows="two PDFs, a text file named .pdf, a Word file, Mac junk and a readme in a folder",
)
def batch_of_documents_zip() -> bytes:
    return write_zip(
        [
            ("lecture12.pdf", _lecture_pdf()),
            ("pset2.pdf", _notes_pdf()),
            ("not-a-pdf.pdf", text_named_pdf()),
            ("kinetics-pset.docx", kinetics_problem_set_docx()),
            ("__MACOSX/._junk.pdf", b"junk"),
            ("notes/readme.txt", b"hello"),
        ]
    )


@fixture("nested.zip", origin=ORIGIN, shows="a zip whose only entry is another zip")
def zip_inside_a_zip() -> bytes:
    return write_zip([("inner.zip", batch_of_documents_zip())], stored=True)


@fixture(
    "mac-folder.zip",
    origin=ORIGIN,
    shows="a folder with a space in its name, zipped on a Mac, with resource forks and .DS_Store",
)
def mac_folder_zip() -> bytes:
    return write_zip(
        [
            ("Week 3/lecture12.pdf", _lecture_pdf()),
            ("Week 3/pset2.pdf", _notes_pdf()),
            ("__MACOSX/Week 3/._lecture12.pdf", b"\x00\x05\x16\x07junk"),
            ("Week 3/.DS_Store", b"\x00\x00\x00\x01Bud1"),
        ]
    )


@fixture("pdf-with-docx.zip", origin=ORIGIN, shows="one PDF and one Word file")
def pdf_with_docx_zip() -> bytes:
    return write_zip(
        [("lecture12.pdf", _lecture_pdf()), ("kinetics.docx", kinetics_problem_set_docx())]
    )


@fixture("pdf-with-txt.zip", origin=ORIGIN, shows="one PDF and one text file")
def pdf_with_txt_zip() -> bytes:
    return write_zip([("lecture12.pdf", _lecture_pdf()), ("readme.txt", b"hi")])
