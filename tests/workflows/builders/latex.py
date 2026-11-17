"""LaTeX sources written for the tests: six single files and one project of several files.
The sources are in `sources/latex/`. First made in the edge-case round of November 10, 2026."""

from __future__ import annotations

import itertools
from pathlib import Path

import pymupdf

from . import fixture
from .rawpdf import write_zip

ORIGIN = "edge-case round ZE2 (part G), 2026-11-10"
SOURCES = Path(__file__).with_name("sources") / "latex"

SINGLE = {
    "article.tex": "an article with theorems, a footnote in the author line and cross-references",
    "macros.tex": "an article that defines its own commands",
    "tikz.tex": "a TikZ drawing",
    "booktabs.tex": "a table set with booktabs rules",
    "beamer.tex": "a Beamer slide deck",
    "exam.tex": "the exam class with points and parts",
}


def _single(name: str, shows: str) -> None:
    @fixture(f"latex-{name}", origin=ORIGIN, shows=shows)
    def read() -> bytes:
        return (SOURCES / name).read_bytes()


for _name, _shows in SINGLE.items():
    _single(_name, _shows)


def isotherm_png() -> bytes:
    """The project's figure: pressure against volume, drawn with lines."""
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=220)
    page.draw_line((40, 190), (290, 190))
    page.draw_line((40, 190), (40, 10))
    points = [(40 + v * 25, 190 - 1500 / (v * 25 + 10)) for v in range(1, 11)]
    for start, end in itertools.pairwise(points):
        page.draw_line(start, end, color=(0, 0, 0.8), width=1.5)
    page.insert_text((150, 210), "V (L)", fontsize=10)
    page.insert_text((5, 100), "P (kPa)", fontsize=10)
    return page.get_pixmap(dpi=150).tobytes("png")


@fixture(
    "latex-project.zip",
    origin=ORIGIN,
    shows="a LaTeX project: a main file, a macro file, two section files and a figure",
)
def latex_project_zip() -> bytes:
    root = SOURCES / "thermo"
    names = ["main.tex", "macros.tex", "sections/intro.tex", "sections/laws.tex"]
    entries = [(name, (root / name).read_bytes()) for name in names]
    entries.append(("figs/pv.png", isotherm_png()))
    return write_zip(entries)
