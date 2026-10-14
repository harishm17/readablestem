"""Small synthetic PDFs for the layout speed tests and `scripts/bench_layout.py`.

Each builder makes a document from a fixed random seed, so a run is repeatable. They
imitate what made real files slow (a scanned-looking or handwritten page that is really
tens of thousands of tiny vector paths, a lecture slide with pen strokes drawn over it)
without any real course material in the repository.
"""

from __future__ import annotations

import random

import pymupdf

WORDS = "The quick brown fox jumps over the lazy dog while a small cat watches quietly. "


def _strokes(count: int, seed: int, *, points: int = 6, spread: float = 3.0) -> bytes:
    """`count` separate pen strokes (each its own path: moveto, lines, stroke) as a
    content stream, scattered over a 595 x 842 pt page."""
    rnd = random.Random(seed)
    ops = ["0 G 0.8 w"]
    for _ in range(count):
        x, y = rnd.uniform(40, 555), rnd.uniform(40, 800)
        ops.append(f"{x:.2f} {y:.2f} m")
        for _ in range(points):
            x += rnd.uniform(-spread, spread)
            y += rnd.uniform(-spread, spread)
            ops.append(f"{x:.2f} {y:.2f} l")
        ops.append("S")
    return "\n".join(ops).encode()


def _append(doc: pymupdf.Document, page: pymupdf.Page, stream: bytes) -> None:
    contents = page.get_contents()
    if not contents:  # a page with nothing on it has no content stream yet
        xref = doc.get_new_xref()
        doc.update_object(xref, "<<>>")
        doc.update_stream(xref, stream)
        page.set_contents(xref)
        return
    doc.update_stream(contents[0], doc.xref_stream(contents[0]) + b"\n" + stream)


def _typed(page: pymupdf.Page, lines: int = 30) -> None:
    for i in range(lines):
        page.insert_text((60, 70 + i * 14), WORDS * 2, fontsize=10)


def ink_page(strokes: int, *, typed: bool, seed: int = 1) -> bytes:
    """One page of `strokes` separate pen strokes, with or without a typed paragraph."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    if typed:
        _typed(page)
    _append(doc, page, _strokes(strokes, seed))
    data = doc.tobytes()
    doc.close()
    return data


def ink_document(pages: int, strokes: int, *, typed_first: bool = False) -> bytes:
    """`pages` pages of `strokes` strokes each, no text layer (a handwritten or scanned
    looking file); the first page has a typed title paragraph with `typed_first`."""
    doc = pymupdf.open()
    for number in range(pages):
        page = doc.new_page(width=595, height=842)
        if typed_first and number == 0:
            _typed(page, 3)
        _append(doc, page, _strokes(strokes, 100 + number))
    data = doc.tobytes()
    doc.close()
    return data


def dots_page(dots: int, seed: int = 3) -> bytes:
    """A typed page with `dots` separate small filled squares (a scatter plot)."""
    rnd = random.Random(seed)
    ops = ["0 0 1 rg"] + [
        f"{rnd.uniform(50, 545):.2f} {rnd.uniform(100, 780):.2f} 2.4 2.4 re f" for _ in range(dots)
    ]
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    _typed(page)
    _append(doc, page, "\n".join(ops).encode())
    data = doc.tobytes()
    doc.close()
    return data


def slide_with_ink(paths: int = 400, items: int = 80) -> bytes:
    """A landscape slide with a small grid of words (the table detector's prefilter lets
    it through) and `paths` long pen strokes of `items` segments each over it: about
    `paths * items` path items, what made a lecture PDF with annotations take 40 s a
    page in the table detector."""
    doc = pymupdf.open()
    page = doc.new_page(width=1224, height=792)
    for row in range(5):
        for col in range(4):
            page.insert_text((100 + col * 200, 120 + row * 24), f"cell {row}{col}", fontsize=14)
    rnd = random.Random(5)
    ops = ["0 G 1.2 w"]
    for _ in range(paths):
        x, y = rnd.uniform(60, 1100), rnd.uniform(300, 700)
        ops.append(f"{x:.2f} {y:.2f} m")
        for _ in range(items):
            x += rnd.uniform(-4, 4)
            y += rnd.uniform(-4, 4)
            ops.append(f"{x:.2f} {y:.2f} l")
        ops.append("S")
    _append(doc, page, "\n".join(ops).encode())
    data = doc.tobytes()
    doc.close()
    return data
