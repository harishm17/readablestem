"""Accumulates an `Intermediate` in reading order, and reads Markdown into it.

Every adapter ends here, so the rules for what counts as a heading, a paragraph, an inline
or display equation, a figure or a table are written once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin

from parsers_bench.intermediate import (
    Block,
    Equation,
    Figure,
    Heading,
    Intermediate,
    Table,
)

Display = Literal["block", "inline"]
_SPACES = re.compile(r"\s+")
_TAGS = re.compile(r"<[^>]*>")


def squash(text: str) -> str:
    """One line, single spaces, no edge whitespace."""
    return _SPACES.sub(" ", text).strip()


class Builder:
    """Add items in reading order; each gets the next index of one shared counter."""

    def __init__(self, arm: str, section: str) -> None:
        self.doc = Intermediate(arm=arm, section=section)
        self._order = 0

    def _next(self) -> int:
        order = self._order
        self._order += 1
        return order

    def heading(self, level: int, text: str) -> None:
        text = squash(text)
        if text:
            self.doc.headings.append(Heading(self._next(), min(max(level, 1), 6), text))

    def text(self, kind: str, text: str) -> None:
        text = squash(text)
        if text:
            self.doc.blocks.append(Block(self._next(), kind, text))

    def equation(
        self,
        display: Display,
        *,
        latex: str | None = None,
        mathml: str | None = None,
        raw: str | None = None,
    ) -> None:
        latex = squash(latex) if latex else None
        raw = squash(raw) if raw else None
        if latex or mathml or raw:
            self.doc.equations.append(
                Equation(self._next(), display, latex or None, mathml, raw or None)
            )

    def figure(self, caption: str | None, image_kept: bool, alt: str | None) -> None:
        caption = squash(caption) if caption else None
        alt = squash(alt) if alt else None
        self.doc.figures.append(Figure(self._next(), caption or None, image_kept, alt or None))

    def table(self, grid: list[list[str]]) -> None:
        cleaned = [[squash(cell) for cell in row] for row in grid]
        if any(cell for row in cleaned for cell in row):
            self.doc.tables.append(Table(self._next(), cleaned))

    def markdown(self, text: str, *, kind: str = "para") -> None:
        """Read a Markdown string (with `$...$` and `$$...$$` maths) into this document."""
        feed_markdown(self, text, kind=kind)


# ------------------------------------------------------------------ Markdown


def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark").enable("table")
    dollarmath_plugin(md, allow_space=True, allow_digits=True, double_inline=True)
    return md


_MD = _parser()


@dataclass
class _Inline:
    text: str
    math: list[tuple[Display, str]]  # (display, latex)
    images: list[str]  # alt text of each image


def _flatten(token: Token, *, keep_math: bool) -> _Inline:
    """Plain text of an `inline` token. Maths and images are taken out of the text (and
    returned beside it); `keep_math` leaves the TeX in, as a heading's text would have it."""
    parts: list[str] = []
    math: list[tuple[Display, str]] = []
    images: list[str] = []
    for child in token.children or []:
        kind = child.type
        if kind in ("text", "code_inline"):
            parts.append(child.content)
        elif kind in ("softbreak", "hardbreak"):
            parts.append(" ")
        elif kind == "math_inline":
            math.append(("inline", child.content))
            parts.append(f" {child.content} " if keep_math else " ")
        elif kind == "math_inline_double":
            math.append(("block", child.content))
            parts.append(f" {child.content} " if keep_math else " ")
        elif kind == "image":
            images.append(child.content)
            parts.append(" ")
        # html_inline (for example PyMuPDF4LLM's <u> underline), emphasis and link markers
        # carry no text of their own.
    return _Inline(squash("".join(parts)), math, images)


def feed_markdown(builder: Builder, text: str, *, kind: str = "para") -> None:
    tokens = _MD.parse(text)
    list_depth = 0
    in_table = False
    grid: list[list[str]] = []
    row: list[str] = []
    for i, token in enumerate(tokens):
        kind_t = token.type
        if kind_t in ("bullet_list_open", "ordered_list_open"):
            list_depth += 1
        elif kind_t in ("bullet_list_close", "ordered_list_close"):
            list_depth -= 1
        elif kind_t == "table_open":
            in_table, grid = True, []
        elif kind_t == "tr_open":
            row = []
        elif kind_t == "tr_close":
            grid.append(row)
        elif kind_t == "table_close":
            in_table = False
            builder.table(grid)
        elif kind_t == "math_block":
            builder.equation("block", latex=token.content)
        elif kind_t in ("fence", "code_block"):
            builder.text(kind, token.content)
        elif kind_t == "html_block":
            # A block that starts with a tag (for example <u>) is raw HTML to the parser;
            # the text inside the tags is still the tool's text.
            builder.text("list" if list_depth else kind, _TAGS.sub(" ", token.content))
        elif kind_t == "inline":
            opener = tokens[i - 1].type if i else ""
            if opener == "heading_open":
                inline = _flatten(token, keep_math=True)
                builder.heading(int(tokens[i - 1].tag[1]), inline.text)
            elif in_table and opener in ("th_open", "td_open"):
                row.append(_flatten(token, keep_math=True).text)
            else:
                inline = _flatten(token, keep_math=False)
                builder.text("list" if list_depth else kind, inline.text)
                for alt in inline.images:
                    builder.figure(None, True, alt)
                for display, latex in inline.math:
                    builder.equation(display, latex=latex)


# ------------------------------------------------------------------ HTML tables


def table_grid_from_html(html: str) -> list[list[str]]:
    """Rows and cells of the first <table> in `html`, as plain text (no spans expanded)."""
    from html.parser import HTMLParser

    class _Grid(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.rows: list[list[str]] = []
            self._cell: list[str] | None = None
            self._depth = 0

        def handle_starttag(self, tag: str, attrs: list) -> None:
            if tag == "table":
                self._depth += 1
            elif tag == "tr" and self._depth == 1:
                self.rows.append([])
            elif tag in ("td", "th") and self._depth == 1:
                self._cell = []

        def handle_endtag(self, tag: str) -> None:
            if tag == "table":
                self._depth -= 1
            elif tag in ("td", "th") and self._cell is not None and self.rows:
                self.rows[-1].append("".join(self._cell))
                self._cell = None

        def handle_data(self, data: str) -> None:
            if self._cell is not None:
                self._cell.append(data)

    parser = _Grid()
    parser.feed(html)
    return [[squash(cell) for cell in row] for row in parser.rows if row]
