"""`page_type.producer_hint`: which PDFs are called TeX-made (the layout reads TeX conventions only
from those: `layout._TEX_MADE`), and which are not.

XeLaTeX (`xdvipdfmx`, what Overleaf offers) and Tectonic wrote no "tex" in their producer and were
missed; a substring match took "PDFContext" (Quartz, a Mac-saved Word file) and "iText" for TeX."""

from __future__ import annotations

import pymupdf
import pytest

from readablestem.pipeline import page_type, slides


def hint(producer: str = "", creator: str = "") -> str:
    doc = pymupdf.open()
    doc.new_page()
    doc.set_metadata({"producer": producer, "creator": creator})
    return page_type.producer_hint(doc)


@pytest.mark.parametrize(
    ("producer", "creator"),
    [
        ("pdfTeX-1.40.25", "TeX"),
        ("pdfTeX-1.40.25", "LaTeX with hyperref"),
        ("xdvipdfmx (20240305)", "XeTeX output 2026.11.03:1200"),  # XeLaTeX on Overleaf
        ("xdvipdfmx (0.1)", "tectonic"),
        ("LuaTeX-1.17.0", "LaTeX with hyperref"),
        ("LuaHBTeX, Version 1.17.0", "LaTeX with hyperref"),
        ("dvipdfmx (20211230)", "LaTeX"),
        ("dvipdfm 0.13.2c", "TeX output"),
        ("GPL Ghostscript 9.55.0", "dvips(k) 2023.1 Copyright 2023 Radical Eye Software"),
        ("GPL Ghostscript 10.02", "LaTeX with Beamer class"),
        ("Tectonic 0.15", ""),
        ("", "TeX output 2021.03.01:1200"),
        # Every distinct TeX producer/creator pair in the repo's PDF corpora (tests, data, the gold
        # sets), as the files carry them.
        ("pikepdf 8.15.1", "arXiv GenPDF (tex2pdf:)"),  # arXiv's own build: "tex2pdf" is one word
        ("pikepdf 8.15.1", "arXiv GenPDF (tex2pdf:c0aef68)"),
        ("pdfTeX-1.40.25", "LaTeX with log_2024.sty"),
        ("pdfTeX-1.40.29", "Simone Chiarella"),
        ("pdfTeX-1.40.29", "LaTeX with Beamer class"),
        ("pdfTeX-1.40.21", "LaTeX with hyperref"),
        ("pdfLaTeX with theoretics.cls v0.91 ", "LaTeX with hyperref"),
        ("GPL Ghostscript 10.01.2", "LaTeX with hyperref"),
        ("dvips + GPL Ghostscript GIT PRERELEASE 9.22", "LaTeX with hyperref"),
        ("xdvipdfmx (0.1)", "LaTeX with Beamer class"),
        ("xdvipdfmx (0.1)", "LaTeX with hyperref"),
        ("xdvipdfmx (20260317)", "LaTeX with hyperref"),
        ("", "LaTeX with hyperref"),
        ("Acrobat Distiller 9.4.5 (Windows)", "VTeX PDF Tools"),
    ],
)
def test_a_pdf_that_says_tex_made_it_is_tex(producer, creator):
    assert hint(producer, creator) == "tex"


@pytest.mark.parametrize(
    ("producer", "creator", "expected"),
    [
        ("Quartz PDFContext", "Word", "office"),  # "PDFContext" holds "tex"
        ("Quartz PDFContext", "", "other"),
        ("iText 5.5.13 ©2000-2018 iText Group NV", "", "other"),
        ("Microsoft® Word 2019", "Microsoft® Word 2019", "office"),
        ("LibreOffice 7.5", "Writer", "other"),
        ("Adobe Acrobat Pro 23.1", "Acrobat PDFMaker 23 for Word", "office"),
        ("Skia/PDF m120", "Chromium", "chromium"),
        ("GPL Ghostscript 9.55.0", "PScript5.dll Version 5.2.2", "other"),
        ("GPL Ghostscript 10.02", "", "other"),
        ("", "", "other"),
        # Real strings from the corpora that hold "tex" only inside another word.
        (
            "iOS Version 16.6 (Build 20G75) Quartz PDFContext",
            "iOS Version 16.6 Quartz PDFContext",
            "apple_mobile",
        ),
        ("macOS Version 14.1.1 (Build 23B81) Quartz PDFContext", "", "other"),
        ("macOS Version 10.14.6 (Build 18G95) Quartz PDFContext", "Keynote", "other"),
        ("MiKTeX GPL Ghostscript 9.25", "(ImageMagick)", "other"),  # a Ghostscript build, not TeX
        ("Skia/PDF m156", "Chromium", "chromium"),
        ("LibreOffice 26.2.6.3 (X86_64)", "Writer", "other"),
        ("OCRmyPDF 16 / Tesseract", "OCRmyPDF 16 / Tesseract", "ocr_tool"),
        ("ReadableSTEM fixtures", "ReadableSTEM fixtures", "other"),
        ("Matplotlib pdf backend v3.11.2", "Matplotlib v3.11.2", "other"),
    ],
)
def test_other_producers_are_not_tex(producer, creator, expected):
    assert hint(producer, creator) == expected


def test_beamer_is_a_word_in_the_creator():
    assert slides._BEAMER.search("LaTeX with Beamer class")
    assert not slides._BEAMER.search("Beamerology Suite")
