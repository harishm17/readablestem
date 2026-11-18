"""The content budget: how much drawing instructions a page, and a document, may hold.

A page's content streams are compressed in the file and inflated each time MuPDF reads the
page: for its text, its drawings, its picture. A 260 KB file can hold one page that
inflates to 256 MB of instructions (F-559), and every step that reads the page parses all
of it again: 7 s in the upload probe and 24 s in a conversion, growing with the inflated
size. So the inflated size is counted before anything parses the page, and over a budget
the file is refused (`content_too_large`).

What is counted is what MuPDF interprets when it draws a page: the page's content streams
(one, or an array of them), and every stream they draw in turn: form XObjects, tiling
patterns, the glyph procedures of Type3 fonts and the appearance streams of annotations,
followed through their own resources. A stream drawn twice on a page counts once for that
page, and a stream shared by several pages counts once for the document. Forms that draw
each other in a loop are followed once.

Each stream is read through MuPDF's own decoders (`pdf_open_stream_number`: every filter
MuPDF knows, chains of them, an encrypted file's decryption) and its output is skipped in
chunks, never kept: counting stops one byte past the budget, so a stream is never inflated
whole just to be counted. A stream MuPDF cannot decode counts what it gave before the error,
which is all the interpreter would read of it too.

The object streams of a file (PDF 1.5 packs most of its small objects into compressed
streams) are inflated whenever an object in them is first needed, at every open, before any
page can be read: the page tree can sit behind 256 MB of padding. `check_objects` counts
them, from the cross-reference table alone, before anything resolves an object; together
they may inflate to the document budget.

Images are not counted here: their pixels have their own budget (`core.images`).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pymupdf

# Measured on 2026-11-11 over the development corpus (`readablestem.eval.corpus`, all 72
# entries present on the machine, `measure` with no limit): the largest page draws
# 3,158,078 bytes of inflated content (its streams plus what they draw) and the largest
# document holds 10,413,420 bytes, both a 4-page handwritten file. The whole public files the
# corpus cuts its pages from (28 books, papers and slide decks, up to 920 pages) reach the
# same 3.0 MiB on a page and 12.6 MiB for a whole file. The budgets are about 10 times those
# maxima: 32 MiB a page (10.6x the largest page) and 128 MiB a document (12.9x the largest
# corpus document, 10.2x the largest whole file). The 256 MB page of F-559 is 8 times the
# page budget; at the document budget the four steps that read every page take about 12 s.
# Object streams (`check_objects`) inflate to at most 7,858 bytes in a corpus file and
# 893,679 bytes in a whole file, far inside the document budget they are held to.
PAGE_CONTENT_BUDGET_BYTES = 32 * 1024 * 1024
DOCUMENT_CONTENT_BUDGET_BYTES = 128 * 1024 * 1024

# How much of a stream is skipped per call: small enough to stop close to the budget.
_CHUNK = 1024 * 1024


class ContentTooLarge(ValueError):
    """A page, or the whole document, holds more inflated content than its budget."""


@dataclass(frozen=True)
class ContentSize:
    """Inflated bytes: of the largest page counted, and of the pages counted together.
    A count that stopped at a budget is one byte past it."""

    largest_page: int
    total: int


def check(doc: pymupdf.Document, pages: Iterable[int] | None = None) -> None:
    """Raise `ContentTooLarge` when a page (all by default) or the pages together are over
    their budget. Errors MuPDF raises reading the page objects go up to the caller."""
    size = measure(doc, pages)
    if size.largest_page > PAGE_CONTENT_BUDGET_BYTES:
        raise ContentTooLarge("a page holds more content than a page may")
    if size.total > DOCUMENT_CONTENT_BUDGET_BYTES:
        raise ContentTooLarge("the document holds more content than a document may")


def check_objects(doc: pymupdf.Document) -> None:
    """Raise `ContentTooLarge` when the object streams together inflate past the document
    budget. Call it right after opening, before the page count or any page is read."""
    if measure_objects(doc) > DOCUMENT_CONTENT_BUDGET_BYTES:
        raise ContentTooLarge("the document's object streams hold more than a document may")


def measure_objects(doc: pymupdf.Document, limit: int = DOCUMENT_CONTENT_BUDGET_BYTES) -> int:
    """The bytes the object streams of `doc` inflate to together, counting stopped one byte
    past `limit`. Which streams are object streams is read from the cross-reference entries
    of the objects packed in them, so no object is resolved and no object stream loaded."""
    import pymupdf

    mu = pymupdf.mupdf
    pdf = pymupdf._as_pdf_document(doc)
    holders: set[int] = set()
    for number in range(1, mu.pdf_xref_len(pdf)):
        entry = mu.ll_pdf_get_xref_entry_no_null(pdf.m_internal, number)
        if entry.type in (ord("o"), "o"):  # packed in an object stream; `ofs` is its number
            holders.add(entry.ofs)
    total = 0
    for number in sorted(holders):
        total += _inflated_length(mu, pdf, number, limit + 1 - total)
        if total > limit:
            break
    return total


def measure(
    doc: pymupdf.Document,
    pages: Iterable[int] | None = None,
    *,
    page_limit: int = PAGE_CONTENT_BUDGET_BYTES,
    document_limit: int = DOCUMENT_CONTENT_BUDGET_BYTES,
) -> ContentSize:
    """The inflated content of `pages` (0-based, all by default), counting stopped one byte
    past `page_limit` for a page and `document_limit` for the total."""
    import pymupdf

    mu = pymupdf.mupdf
    pdf = pymupdf._as_pdf_document(doc)
    numbers = range(doc.page_count) if pages is None else pages
    lengths: dict[int, int] = {}  # inflated length per stream object, read once per document
    counted: set[int] = set()  # streams already in the document total
    largest = total = 0
    for number in numbers:
        page = mu.pdf_lookup_page_obj(pdf, number)
        size = 0
        for stream in _drawn(mu, page):
            if stream not in lengths:
                lengths[stream] = _inflated_length(mu, pdf, stream, page_limit + 1)
            size += lengths[stream]
            if stream not in counted:
                counted.add(stream)
                total += lengths[stream]
            if size > page_limit or total > document_limit:
                break
        largest = max(largest, size)
        if largest > page_limit or total > document_limit:
            break
    return ContentSize(min(largest, page_limit + 1), min(total, document_limit + 1))


def _inflated_length(mu, pdf, number: int, limit: int) -> int:
    """Bytes stream `number` decodes to, counted up to `limit`; nothing is kept."""
    try:
        stream = mu.pdf_open_stream_number(pdf, number)
    except Exception:  # not a stream after all, or no filter MuPDF can open: nothing to read
        return 0
    got = 0
    while got < limit:
        try:
            step = mu.fz_skip(stream, min(_CHUNK, limit - got))
        except Exception:  # broken data: the interpreter stops reading at the same place
            break
        if step <= 0:
            break
        got += step
    return got


def _drawn(mu, page) -> Iterator[int]:
    """The object numbers of every stream drawing the page draws, each once: its content
    streams, its annotations' appearances, then what they draw (module docstring)."""
    seen: set[int] = set()
    walked: set[int] = set()  # indirect resource dictionaries already followed
    todo: list = [mu.pdf_dict_get_inheritable(page, mu.PdfObj(mu.PDF_ENUM_NAME_Resources))]
    for number in _streams(mu, mu.pdf_dict_gets(page, "Contents")):
        if number not in seen:
            seen.add(number)
            yield number
    drawn: list = []  # streams with resources of their own: forms, patterns, appearances
    annots = mu.pdf_dict_gets(page, "Annots")
    for i in range(mu.pdf_array_len(annots) if mu.pdf_is_array(annots) else 0):
        appearance = mu.pdf_dict_gets(mu.pdf_array_get(annots, i), "AP")
        for key in ("N", "R", "D"):  # each a stream, or a dictionary of one per state
            state = mu.pdf_dict_gets(appearance, key)
            drawn.extend([state] if mu.pdf_is_stream(state) else _values(mu, state))
    while drawn or todo:
        while drawn:
            item = drawn.pop()
            if not mu.pdf_is_stream(item):
                continue
            number = mu.pdf_to_num(item)
            if number in seen:  # drawn before on this page, or a loop of forms
                continue
            seen.add(number)
            yield number
            todo.append(mu.pdf_dict_gets(item, "Resources"))
        if not todo:
            break
        resources = todo.pop()
        if not mu.pdf_is_dict(resources):
            continue
        if mu.pdf_is_indirect(resources):
            key = mu.pdf_to_num(resources)
            if key in walked:
                continue
            walked.add(key)
        for item in _values(mu, mu.pdf_dict_gets(resources, "XObject")):
            if mu.pdf_to_name(mu.pdf_dict_gets(item, "Subtype")) == "Form":  # not an image
                drawn.append(item)
        drawn.extend(_values(mu, mu.pdf_dict_gets(resources, "Pattern")))  # tiling ones
        for font in _values(mu, mu.pdf_dict_gets(resources, "Font")):
            if mu.pdf_to_name(mu.pdf_dict_gets(font, "Subtype")) == "Type3":
                drawn.extend(_values(mu, mu.pdf_dict_gets(font, "CharProcs")))
                todo.append(mu.pdf_dict_gets(font, "Resources"))


def _streams(mu, obj) -> Iterator[int]:
    """The object numbers of the stream `obj` is, or of the streams in the array it is."""
    if mu.pdf_is_stream(obj):
        yield mu.pdf_to_num(obj)
    elif mu.pdf_is_array(obj):
        for i in range(mu.pdf_array_len(obj)):
            item = mu.pdf_array_get(obj, i)
            if mu.pdf_is_stream(item):
                yield mu.pdf_to_num(item)


def _values(mu, obj) -> list:
    """The values of the dictionary `obj` (none when it is not one)."""
    if not mu.pdf_is_dict(obj):
        return []
    return [mu.pdf_dict_get_val(obj, i) for i in range(mu.pdf_dict_len(obj))]
