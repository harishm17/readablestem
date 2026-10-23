"""Shared builders for the image page tests: a model's blocks as it sends them, and scans."""

from __future__ import annotations

import io
from typing import Any

import pymupdf
from PIL import Image, ImageDraw


def block(kind: str, **kw: Any) -> dict[str, Any]:
    """A block as the model sends it: every field present, the ones that do not apply empty."""
    base: dict[str, Any] = {
        "kind": kind,
        "x0": 100,
        "y0": 100,
        "x1": 900,
        "y1": 160,
        "confidence": "high",
        "level": 0,
        "marker": "",
        "runs": [],
        "latex": "",
        "label": "",
        "description": "",
        "caption": "",
        "rows": [],
    }
    base.update(kw)
    return base


def text(value: str) -> dict[str, str]:
    return {"kind": "text", "value": value}


def math(value: str) -> dict[str, str]:
    return {"kind": "math", "value": value}


def cell(text_: str, header: str = "none", **kw: Any) -> dict[str, Any]:
    return {"text": text_, "latex": "", "header": header, "row_span": 1, "col_span": 1, **kw}


def png_of(boxes: list[tuple[int, int, int, int]] | None = None) -> bytes:
    """A white 600 x 800 page with a dark bar at each box (pixels)."""
    image = Image.new("RGB", (600, 800), "white")
    draw = ImageDraw.Draw(image)
    for box in boxes or []:
        draw.rectangle(box, fill=(20, 20, 20))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def scan_pdf(pages: int = 1, typed_first: bool = False) -> bytes:
    """Pages that are a picture of a page and nothing else; optionally a typed page first."""
    out = pymupdf.open()
    if typed_first:
        page = out.new_page(width=612, height=792)
        for line in range(8):
            page.insert_text((72, 100 + 30 * line), f"A typed line number {line}.", fontsize=12)
    for number in range(pages):
        drawn = pymupdf.open()
        sheet = drawn.new_page(width=612, height=792)
        for line in range(10):
            sheet.insert_text((72, 100 + 30 * line), f"Scanned {line} on {number}", fontsize=14)
        picture = sheet.get_pixmap(dpi=120).tobytes("jpg")
        page = out.new_page(width=612, height=792)
        page.insert_image(page.rect, stream=picture)
    return out.tobytes()
