"""A fillable form: text fields, a drop-down, checkboxes and radio buttons with typed labels.
First made for the layout corpus of November 10, 2026 (its page s05)."""

from __future__ import annotations

import pymupdf

from . import fixture
from .rawpdf import save

ORIGIN = "layout corpus (lane Y), 2026-11-10"
PLAIN, BOLD = "helv", "hebo"


@fixture(
    "form-acroform.pdf",
    origin=ORIGIN,
    shows="an accommodation request form with fillable fields beside their labels",
)
def fillable_accommodation_form() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)

    def text(x: float, y: float, words: str, size: float = 11, bold: bool = False) -> None:
        page.insert_text((x, y), words, fontsize=size, fontname=BOLD if bold else PLAIN)

    def box(rect: pymupdf.Rect, name: str, kind: int, **more: object) -> None:
        widget = pymupdf.Widget()
        widget.rect = rect
        widget.field_name = name
        widget.field_type = kind
        widget.border_color = (0, 0, 0)
        for key, value in more.items():
            setattr(widget, key, value)
        page.add_widget(widget)

    def field(
        y: float, label: str, name: str, width: float = 300, choices: list[str] | None = None
    ) -> None:
        text(54, y + 13, label)
        more: dict[str, object] = {"fill_color": (0.95, 0.95, 1), "text_fontsize": 10}
        kind = pymupdf.PDF_WIDGET_TYPE_TEXT
        if choices:
            kind = pymupdf.PDF_WIDGET_TYPE_COMBOBOX
            more.update(choice_values=choices, field_value=choices[0])
        box(pymupdf.Rect(220, y, 220 + width, y + 18), name, kind, **more)

    text(54, 70, "Student Accommodation Request Form", 20, True)
    text(
        54,
        92,
        "Complete this form and return it to the Accessibility Office at least two weeks before the first",
        10,
    )
    text(54, 105, "class meeting. Fields marked with an asterisk are required.", 10)
    text(54, 128, "Section 1. About you", 14, True)
    field(140, "Full name *", "full_name")
    field(166, "Student ID *", "student_id", 140)
    field(192, "Campus email *", "email")
    field(218, "Course *", "course", 200, ["ENVS 101", "CHEM 110L", "BIO 150", "MATH 120"])
    text(54, 262, "Section 2. Accommodations requested", 14, True)
    text(54, 280, "Check every accommodation you are requesting.", 10)
    options = [
        "Extended time on exams",
        "Note-taking support",
        "Course materials in an accessible format",
        "Other (describe below)",
    ]
    for number, option in enumerate(options):
        y = 292 + number * 24
        box(pymupdf.Rect(60, y, 74, y + 14), f"acc_{number}", pymupdf.PDF_WIDGET_TYPE_CHECKBOX)
        text(84, y + 11, option)
    text(54, 410, "Term *")
    for number, term in enumerate(["Fall", "Spring", "Summer"]):
        x = 120 + number * 90
        box(
            pymupdf.Rect(x, 398, x + 14, 412),
            "term",
            pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON,
            field_value=False,
        )
        text(x + 20, 410, term)
    text(54, 446, "Section 3. Explanation", 14, True)
    text(54, 464, "Describe how your disability affects your coursework (optional).", 10)
    box(
        pymupdf.Rect(54, 472, 558, 560),
        "explanation",
        pymupdf.PDF_WIDGET_TYPE_TEXT,
        field_flags=pymupdf.PDF_TX_FIELD_IS_MULTILINE,
        fill_color=(0.95, 0.95, 1),
    )
    field(584, "Signature *", "signature", 240)
    field(610, "Date *", "date", 120)
    text(
        54,
        700,
        "Office use only: received ____ / ____ / ______   Reviewed by ______________________",
        9,
    )
    return save(doc, clean=False)
