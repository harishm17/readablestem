"""The page numbers readers know the pages by, for the page markers of the output file.

A student told "see page 12" needs to find the end of page 12 of the PDF, and the number
printed on the page is often not the PDF's position (a cover and a contents page come
first). The output marks each source page boundary with a label (`assemble`, docs/output.md
"Page markers"); this module finds the label of each page. In order of preference:

1. the label the PDF itself gives the page (`/PageLabels`: "iv", "12", "A-3"), read with
   PyMuPDF's `Page.get_label`;
2. the number printed in the top or bottom margin ("7", "Page 7", "- 7 -", "7 of 30", a roman
   numeral), when the pages that show one agree on the same offset from the PDF's own
   position (so a figure number in the margin of one page is not taken for a page number);
   an arabic offset is carried over to the pages in between that print nothing;
3. otherwise no label: the output then says the page's position in the PDF.

Nothing here is document text that is stored beyond a short label (at most `MAX_LABEL`
characters, letters, digits and a few joiners). A page that cannot be read has no label and
nothing fails: this is a convenience, never a reason to lose a conversion.
"""

from __future__ import annotations

import contextlib
import re
from collections import Counter
from collections.abc import Iterable

import pymupdf

MAX_LABEL = 16
# A label from the PDF: letters, digits, and the few joiners real labels use ("A-3", "2.1").
_LABEL = re.compile(rf"[A-Za-z0-9][A-Za-z0-9.\-]{{0,{MAX_LABEL - 1}}}")
# The margin bands where a page number is printed, as a share of the page's height (the
# running-header rule of `layout` uses the same 7 %).
BAND = 0.07
# A line that is nothing but a page number: "7", "Page 7", "p. 7", "- 7 -", "7 of 30",
# "7 / 30" (a slide footer), "iv".
_NUMBER_LINE = re.compile(
    r"""^\W*(?:page\s+|p\.\s*)?
        (?P<n>\d{1,4}|[ivxlc]{1,7}|[IVXLC]{1,7})
        \W*(?:(?:of|/)\s*\d{1,4})?\W*$""",
    re.IGNORECASE | re.VERBOSE,
)
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
# At least this many pages must show a number at the same offset for it to count.
MIN_AGREEING_PAGES = 2


def _roman(text: str) -> int | None:
    lower = text.lower()
    if not lower or any(c not in _ROMAN for c in lower):
        return None
    values = [_ROMAN[c] for c in lower]
    total = sum(
        -v if i + 1 < len(values) and values[i + 1] > v else v for i, v in enumerate(values)
    )
    return total if total > 0 else None


def _to_roman(number: int, upper: bool) -> str:
    out = ""
    for value, letters in (
        (100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"),
        (5, "v"), (4, "iv"), (1, "i"),
    ):  # fmt: skip
        while number >= value:
            out, number = out + letters, number - value
    return out.upper() if upper else out


def clean_label(text: str | None) -> str | None:
    """The label if it is short and made of letters, digits and "." or "-"; else None."""
    label = (text or "").strip()
    return label if _LABEL.fullmatch(label) else None


def _margin_number(page: pymupdf.Page) -> tuple[str, int] | None:
    """("arabic" | "roman", value) of the number printed alone in the top or bottom margin."""
    height = page.rect.height
    found: list[tuple[str, int]] = []
    for _x0, y0, _x1, y1, text, *_ in page.get_text("blocks"):
        if not (y1 <= BAND * height or y0 >= (1 - BAND) * height):
            continue
        for line in str(text).splitlines():
            match = _NUMBER_LINE.match(line.strip())
            if match is None:
                continue
            digits = match["n"]
            if digits.isdigit():
                found.append(("arabic", int(digits)))
            elif (value := _roman(digits)) is not None:
                found.append(("roman", value))
    return found[-1] if len(found) == 1 else None  # two numbers: not clearly a page number


def _detected(numbers: dict[int, tuple[str, int]]) -> dict[int, str]:
    """Labels for the pages whose margin number agrees with the offset most pages share,
    per numeral kind; an arabic offset also labels the pages between them."""
    labels: dict[int, str] = {}
    for kind in ("arabic", "roman"):
        offsets = Counter(v - page for page, (k, v) in numbers.items() if k == kind)
        if not offsets:
            continue
        offset, count = offsets.most_common(1)[0]
        if count < MIN_AGREEING_PAGES:
            continue
        agreeing = sorted(p for p, (k, v) in numbers.items() if k == kind and v - p == offset)
        for page in agreeing:
            value = numbers[page][1]
            labels[page] = str(value) if kind == "arabic" else _to_roman(value, False)
        if kind == "arabic":  # the pages between the first and last that print one
            for page in range(agreeing[0], agreeing[-1] + 1):
                if page not in numbers and page + offset > 0:
                    labels.setdefault(page, str(page + offset))
    return labels


def read(pdf_bytes: bytes, pages: Iterable[int]) -> dict[int, str]:
    """Label of each of the 0-based `pages` that has one (PDF label, else printed number).
    Never raises: an unreadable PDF or page has no label."""
    wanted = sorted(set(pages))
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return {}
    labels: dict[int, str] = {}
    try:
        numbers: dict[int, tuple[str, int]] = {}
        for index in wanted:
            if not 0 <= index < doc.page_count:
                continue
            with contextlib.suppress(Exception):
                page = doc[index]
                if label := clean_label(page.get_label()):
                    labels[index] = label
                elif (number := _margin_number(page)) is not None:
                    numbers[index] = number
        for index, label in _detected(numbers).items():
            labels.setdefault(index, label)
    finally:
        doc.close()
    return {page: label for page, label in labels.items() if page in set(wanted)}
