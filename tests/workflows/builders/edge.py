"""Edge cases of the upload: files that are not what their name says, locked files, long
files, pages that are only pictures, and the small Word and LaTeX sources. First made in the
audit of November 9, 2026, where the long and picture-only files were cut from public slides;
here they are made from text written for the test, so nothing has to be fetched."""

from __future__ import annotations

import io

import pymupdf

from . import fixture
from .rawpdf import rezip, save

ORIGIN = "audit lane B edge files, 2026-11-09"

TOPICS = (
    "variables and types",
    "branching",
    "loops",
    "functions",
    "tuples and lists",
    "dictionaries",
    "recursion",
    "testing and debugging",
    "program efficiency",
    "searching and sorting",
)


def lecture(pages: int) -> pymupdf.Document:
    """A typed lecture handout: each page has a heading, a paragraph and three points."""
    doc = pymupdf.open()
    for number in range(pages):
        topic = TOPICS[number % len(TOPICS)]
        page = doc.new_page()
        page.insert_text((72, 80), f"Lecture {number + 1}: {topic}", fontsize=20)
        page.insert_textbox(
            pymupdf.Rect(72, 110, 540, 220),
            f"Today's lecture is about {topic}. We start from an example, name each part of it, and "
            f"then write a short program that uses it. Page {number + 1} ends with three points to "
            "remember before the next class.",
            fontsize=12,
        )
        for point in range(3):
            page.insert_text(
                (90, 250 + 24 * point),
                f"{point + 1}. Point {point + 1} about {topic}.",
                fontsize=12,
            )
        page.insert_text((300, 760), str(number + 1), fontsize=9)
    return doc


def _picture_of(page: pymupdf.Page) -> bytes:
    return page.get_pixmap(dpi=100).tobytes("jpeg")


def _opens_only_restricted(data: bytes) -> None:
    doc = pymupdf.open(stream=data, filetype="pdf")
    if doc.needs_pass or not (doc.metadata or {}).get("encryption"):
        raise AssertionError("the file should open without a password and still be encrypted")
    if doc.permissions & (pymupdf.PDF_PERM_COPY | pymupdf.PDF_PERM_PRINT):
        raise AssertionError("the file should forbid copying and printing")


def _needs_password(data: bytes) -> None:
    if not pymupdf.open(stream=data, filetype="pdf").needs_pass:
        raise AssertionError("the file opens without a password")


@fixture("kinetics-pset.docx", origin=ORIGIN, shows="a one-page Word problem set with a table")
def kinetics_problem_set_docx() -> bytes:
    import docx

    document = docx.Document()
    document.add_heading("Problem Set 3: Kinetics", 1)
    document.add_paragraph(
        "Rate law: rate = k[A]^2. The half-life is t1/2 = 1/(k[A]0). Use R = 8.314 J/(mol K)."
    )
    document.add_heading("Table of data", 2)
    table = document.add_table(rows=3, cols=3)
    table.style = "Table Grid"
    rows = [
        ["T (K)", "k (1/M s)", "Ea (kJ/mol)"],
        ["298", "0.012", "52.1"],
        ["310", "0.031", "52.4"],
    ]
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            table.cell(r, c).text = text
    document.add_paragraph(
        "1. Compute the activation energy from the Arrhenius equation k = A e^(-Ea/RT)."
    )
    document.add_paragraph("2. Balance: 2 H2 + O2 -> 2 H2O and state delta H.")
    out = io.BytesIO()
    document.save(out)
    return rezip(out.getvalue())


KINETICS_TEX = r"""\documentclass{article}
\usepackage{amsmath}
\title{Worked Examples in Kinetics}
\author{Test Instructor}
\begin{document}
\maketitle
\section{Rate laws}
For a second-order reaction the rate is $r = k[A]^2$ and the integrated law is
\begin{equation}
\frac{1}{[A]} = \frac{1}{[A]_0} + kt.
\end{equation}
\begin{table}[h]
\centering
\begin{tabular}{lrr}
\hline
$T$ (K) & $k$ (L mol$^{-1}$ s$^{-1}$) & $E_a$ (kJ mol$^{-1}$) \\
\hline
298 & 0.012 & 52.1 \\
310 & 0.031 & 52.4 \\
\hline
\end{tabular}
\caption{Measured rate constants.}
\end{table}
\subsection{Arrhenius}
$k = A e^{-E_a/RT}$ with $R = 8.314\,\mathrm{J\,mol^{-1}\,K^{-1}}$.
\begin{align}
\ln k &= \ln A - \frac{E_a}{RT} \\
\Delta G &= \Delta H - T\Delta S
\end{align}
\end{document}
"""


@fixture(
    "kinetics.tex",
    origin=ORIGIN,
    shows="one LaTeX file with an equation, a table and an align block",
)
def kinetics_tex() -> bytes:
    return KINETICS_TEX.encode()


@fixture("not-a-pdf.pdf", origin=ORIGIN, shows="plain text in a file named .pdf")
def text_named_pdf() -> bytes:
    return b"Hello, this is plain text pretending to be a PDF. Lecture 3 notes.\n"


@fixture("empty.pdf", origin=ORIGIN, shows="a file of zero bytes")
def empty_file() -> bytes:
    return b""


@fixture("png-named.pdf", origin=ORIGIN, shows="a PNG picture in a file named .pdf")
def png_named_pdf() -> bytes:
    return lecture(1)[0].get_pixmap(dpi=60).tobytes("png")


@fixture(
    "password-user.pdf",
    origin=ORIGIN,
    shows="a 35-page file that needs a password to open",
    unstable="encryption draws a new random key on every save",
    check=_needs_password,
)
def password_to_open_35_pages() -> bytes:
    return lecture(35).tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner123",
        user_pw="user123",
        no_new_id=True,
    )


@fixture(
    "restricted.pdf",
    origin=ORIGIN,
    shows="a file that opens without a password but forbids copying and printing",
    unstable="encryption draws a new random key on every save",
    check=_opens_only_restricted,
)
def restricted_no_password() -> bytes:
    return lecture(35).tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner123",
        user_pw="",
        permissions=0,
        no_new_id=True,
    )


@fixture("pages-215.pdf", origin=ORIGIN, shows="215 typed pages, past the page chooser's limit")
def pages_215() -> bytes:
    return save(lecture(215))


@fixture(
    "image-only-30-pages.pdf", origin=ORIGIN, shows="30 pages that are pictures of typed pages"
)
def image_only_30_pages() -> bytes:
    typed = lecture(30)
    doc = pymupdf.open()
    for page in typed:
        sheet = doc.new_page(width=page.rect.width, height=page.rect.height)
        sheet.insert_image(sheet.rect, stream=_picture_of(page))
    return save(doc)


@fixture(
    "mixed-typed-and-image.pdf",
    origin=ORIGIN,
    shows="12 pages that alternate between typed text and a picture of typed text",
)
def mixed_typed_and_image_pages() -> bytes:
    typed = lecture(12)
    doc = pymupdf.open()
    for number, page in enumerate(typed):
        if number % 2 == 0:
            doc.insert_pdf(typed, from_page=number, to_page=number)
        else:
            sheet = doc.new_page(width=page.rect.width, height=page.rect.height)
            sheet.insert_image(sheet.rect, stream=_picture_of(page))
    return save(doc, clean=False)


@fixture("spanish.pdf", origin=ORIGIN, shows="one typed page in Spanish")
def spanish_page() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text(
        (72, 100),
        "Ecuacion de segundo grado: la formula es x = (-b +- raiz(b^2-4ac)) / 2a",
        fontsize=11,
    )
    page.insert_text(
        (72, 130), "Los estudiantes deben calcular la velocidad en metros por segundo.", fontsize=11
    )
    return save(doc, clean=False)
