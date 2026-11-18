"""A Word file as the pipeline's document model: pandoc's tree of the working copy
(`source.pandoc`, `source.docx_package`) read into the same `ltx_reader.Reading` a LaTeX source
gives, so the figure step, assembly, review, the record and the exports take it from there.

What each part of the file becomes (WS18.15; "exact" means the file says it, no guess):

- Heading 1 to 6 are headings at that level; a paragraph in the Title style is the document's
  title and a level 1 heading; Subtitle, Author and Date are paragraphs. A generated table of
  contents (the `toc N` styles and the TOC heading) is left out, counted, and is made again from
  the headings.
- A list is one `list_item` per item with its level, and its marker and start for a numbered
  one. A paragraph typed with "1." by hand is a paragraph (a list is a proposal there, not here).
- A table is a grid with spans. Its header rows are exact only when the rows are marked "repeat as
  header row"; a table with only the style's first-row flag gets one header row as a proposal
  (a warning check, `source.header_unmarked`), and a table whose rows are all headers gets one.
- A footnote or endnote is written where its mark is, as the LaTeX path writes a note. A link is
  a link when it is a web or mail address; a link to a bookmark on a heading is an internal link
  to that heading (pandoc keeps a bookmark only on a heading, and the words of a `REF` field
  are plain text), and a link to any other bookmark is its words. Struck text is kept
  and marked deleted; superscripts and subscripts are runs.
- An equation typed in Word (`m:oMath`) is the LaTeX pandoc reads from it, checked by the same
  `check_latex` as any LaTeX the product accepts and written to MathML by the pipeline's one
  `safe_latex_to_mathml`; no model is asked. `ltx_reader.equation_fix` makes the fix, so a source
  equation is judged by one set of checks. An equation that does not convert is shown as written
  and is a required decision (the LaTeX path's rule); it is counted in `Report` by the failing
  check. An equation typed flat ("a2 + b2 = c2": a letter then a digit in a formula with no
  script) gets a warning check, `source.flat_equation`: the author's own content, flagged by form.
- A picture is a figure. The picture is the package's own member (`Prepared.picture`) through the
  same checks a LaTeX project's figures get (`source.figures`). Its alt text is the author's when
  `tagged.usable_alt` says it says something (Word's own "Description automatically generated" is
  not the author's): the figure then has an accepted fix with those words and no model reads it.
  Without such alt text it goes to the figure step with the picture's own bytes. A picture that
  cannot be read (a format the product has no reader for) is a required decision.
  A paragraph in the Caption style next to a figure is its caption.
- Hidden text was taken out of the working copy and counted (`docx_package`); tracked changes are
  read as accepted (pandoc's `--track-changes=accept`) and comments are left out, each counted by
  the census (`web.office_census`); headers, footers, text boxes, SmartArt, charts and embedded
  objects are counted from the package. The language is a proposal: the language most letters are
  written in, else Word's default, else English.

After reading, the words the file holds (`Facts.words`) are set against the words in the tree:
each one the tree does not carry is counted (`Reading.unread_words`), and any is the required
notice `source_unread`. Nothing is dropped without a number.

Nothing here runs a program, opens a file, or asks a model.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from readablestem.core import inline
from readablestem.core.types import (
    INLINE_MARKER_RE,
    MAX_TABLE_CELLS,
    MAX_TABLE_COLS,
    MAX_TABLE_ROWS,
    Check,
    Element,
    FigureFix,
    Fix,
    HeadingFix,
    Kind,
    Link,
    Notice,
    Run,
    ScriptRun,
    Status,
    TableCell,
    TableFix,
    TableGrid,
    inline_marker,
    is_safe_href,
)
from readablestem.source import docx_package, figures
from readablestem.source.ltx_reader import (
    BOX,
    CHARS_PER_PAGE,
    FORMULA_UNREAD,
    MAX_DEPTH,
    MAX_ELEMENTS,
    MAX_MATH_CHARS,
    SOURCE_UNREAD_NOTICE,
    TABLE_PICTURE,
    UNKNOWN_FIGURE_REASON,
    ReadError,
    Reading,
    equation_fix,
    link_internal,
    needs_decision,
)

Display = Literal["block", "inline"]
HIDDEN_NOTICE = "word_hidden_text"  # optional: hidden text left out
BOXES_NOTICE = "word_text_boxes"  # optional: text boxes read where they are anchored
HEADERS_NOTICE = "word_headers_footers"  # optional: running heads left out
OBJECTS_NOTICE = "word_objects"  # required: SmartArt, charts, embedded objects: not in the file
CHANGES_NOTICE = "word_tracked_changes"  # required: changes were read as accepted
RTL_NOTICE = "word_rtl_text"  # optional: right-to-left paragraphs, in reading order, shown as LTR
NOTICE_CODES = (
    HIDDEN_NOTICE,
    BOXES_NOTICE,
    HEADERS_NOTICE,
    OBJECTS_NOTICE,
    CHANGES_NOTICE,
    RTL_NOTICE,
)
FLAT_CHECK = "source.flat_equation"
HEADER_CHECK = "source.header_unmarked"
_FLAT = re.compile(r"[A-Za-z]\d")
_COMMAND = re.compile(r"\\([A-Za-z]+)")
_QUOTES = {"SingleQuote": ("\u2018", "\u2019"), "DoubleQuote": ("“", "”")}
_TOC = re.compile(
    r"^(?:toc\s*\d*|toc heading|table of (?:contents|figures)|contents heading)$", re.I
)
_CAPTION = "caption"


@dataclass
class Report:
    """What the reader did with the file, counts only (the facts the package pass found are on
    `docx_package.Facts`). `math_*`: the file's equations and what became of each."""

    math_held: int = 0  # `m:oMath` in the file
    math_read: int = 0  # equations pandoc gave (the ones below are of these)
    math_converted: int = 0
    math_in_headings: int = 0  # written as TeX in the heading, a decision (nothing to speak)
    math_failed: int = 0
    math_failures: Counter[str] = field(default_factory=Counter)  # failing check -> equations
    failed_commands: Counter[str] = field(default_factory=Counter)  # command not allowed -> n
    math_flat: int = 0
    toc_paragraphs: int = 0
    pictures: int = 0
    pictures_authored_alt: int = 0
    pictures_unread: int = 0
    captions: int = 0
    extra_words: int = 0  # words the tree has that the file's own pass does not
    kinds: Counter[str] = field(default_factory=Counter)


class _Reader:
    def __init__(self, prepared: docx_package.Prepared) -> None:
        self.prepared = prepared
        self.out = Reading()
        self.report = Report()
        self.counter = 0
        self.chars = 0
        self.loose: list[dict[str, Any]] = []  # pictures met in the block being read
        self.captions: dict[str, str] = {}  # id of a caption paragraph -> its text
        self.title_set = False
        # table node -> its place among the tables of the tree (pandoc 3.1 gives no style id back)
        self.table_place: dict[int, int] = {}
        # A bookmark's name (a heading's id, or a span's) -> the element it is in, and the
        # links to such a name: element, start, end, name (`ltx_reader.link_internal`).
        self.ids: dict[str, str] = {}
        self.refs: list[tuple[Element, int, int, str]] = []

    # ------------------------------------------------------------------ elements

    def _new(self, kind: Kind, **fields: Any) -> Element:
        if self.counter >= MAX_ELEMENTS:
            raise ReadError("too many elements")
        self.counter += 1
        page = self.chars // CHARS_PER_PAGE
        element = Element(
            id=f"p{page + 1}-e{self.counter}",
            kind=kind,
            page=page,
            bbox=BOX,
            order=self.counter,
            **fields,
        )
        self.out.elements.append(element)
        return element

    def _drop(self, element: Element) -> None:
        self.out.elements.remove(element)
        self.counter -= 1

    # ------------------------------------------------------------------ blocks

    def blocks(self, blocks: list[Any], depth: int = 0) -> None:
        if depth > MAX_DEPTH:
            raise ReadError("too deep")
        for block in blocks:
            self.block(block, depth)

    def block(self, block: dict[str, Any], depth: int) -> None:
        kind = block.get("t")
        content: Any = block.get("c")
        if kind in ("Para", "Plain"):
            self._paragraph(content)
        elif kind == "Header":
            self._heading(int(content[0]), content[2], str(content[1][0]))
        elif kind == "Div":
            self._div(content[0], content[1], depth)
        elif kind == "BlockQuote":
            self.blocks(content, depth + 1)
        elif kind in ("BulletList", "OrderedList"):
            self._list(kind, content, 1, depth)
        elif kind == "Table":
            self._table(content)
        elif kind == "Figure":
            self._figure(content, depth)
        elif kind == "CodeBlock":
            text = str(content[1]).strip("\n")
            if text.strip():
                element = self._new(Kind.PARAGRAPH, text=text)
                element.preformatted = True
                self.chars += len(text)
        elif kind == "LineBlock":
            for line in content:
                self._paragraph(line)
        elif kind == "DefinitionList":
            self._definitions(content, depth)
        # RawBlock and HorizontalRule hold nothing a student reads

    def _div(self, attr: list[Any], blocks: list[Any], depth: int) -> None:
        style = dict(attr[2]).get("custom-style", "")
        name = style.lower()
        if _TOC.match(name):
            self.report.toc_paragraphs += 1
            return
        if name == "title" and self._title(_plain(_first_inlines(blocks))):
            return
        if name == _CAPTION:
            self._caption(blocks)
            return
        self.blocks(blocks, depth + 1)

    def _title(self, text: str) -> bool:
        """The document's title: a level 1 heading, once. False when there is none to make."""
        if not text or self.title_set:
            return False
        element = self._new(Kind.HEADING, text=text, bold=True)
        self._heading_fix(element, 1, text)
        self.chars += len(text)
        self.out.title = text
        self.title_set = True
        return True

    def meta(self, meta: dict[str, Any]) -> None:
        """What pandoc moves out of the body into its metadata: the Title, Subtitle, Author, Date
        and Abstract paragraphs. The title is the title, the rest are paragraphs before the
        body, in the file's own order of those five."""
        for key in META_KEYS:
            value = meta.get(key)
            if value is None:
                continue
            if key == "title" and self._title(_plain(_meta_inlines(value))):
                continue
            self.blocks(_meta_blocks(value), 1)

    def _heading_fix(self, element: Element, level: int, text: str) -> None:
        self.out.fixes[element.id] = Fix(
            element_id=element.id,
            kind=Kind.HEADING,
            status=Status.ACCEPTED,
            confidence=1.0,
            heading=HeadingFix(level=min(max(level, 1), 6), text=text),
            checks=[Check(name="source.exact", passed=True)],
        )

    def _heading(self, level: int, inlines: list[Any], name: str = "") -> None:
        element = self._new(Kind.HEADING, text="", bold=True)
        text, _, formulas = self._inline_text(inlines, owner=None)
        if not text:
            self._drop(element)
            return
        element.text = text
        if name:  # pandoc names a heading's bookmark by the heading's id
            self.ids.setdefault(name, element.id)
        self._heading_fix(element, level, text)
        if formulas:  # a formula in a heading is written as its TeX: a decision
            self.report.math_in_headings += formulas
            self.out.unread_formulas.add(element.id)
            self.out.fixes[element.id] = needs_decision(self.out.fixes[element.id], FORMULA_UNREAD)
        self.chars += len(text)

    # ------------------------------------------------------------------ paragraphs

    def _paragraph(
        self, inlines: list[Any], *, element_kind: Kind = Kind.PARAGRAPH
    ) -> Element | None:
        """A paragraph, or the blocks it holds: a display formula is an equation element of its
        own, the words around it paragraphs, a picture a figure straight after."""
        made: Element | None = None
        run: list[Any] = []
        for item in [*inlines, None]:
            if item is not None and not _is_display(item):
                run.append(item)
                continue
            if run:
                made = self._text_block(run, element_kind) or made
                run = []
            if item is not None:
                self._display_math(item["c"][1])
        return made

    def _text_block(self, inlines: list[Any], kind: Kind) -> Element | None:
        element = self._new(kind, text="")
        text, spans, _ = self._inline_text(inlines, owner=element)
        pictures, self.loose = self.loose, []
        made: Element | None = None
        if text:
            element.text = text
            self._apply_spans(element, spans)
            self.chars += len(text)
            made = element
        else:
            self._drop(element)
        for picture in pictures:
            self._picture(picture)
        return made

    def _display_math(self, latex: str) -> None:
        element = self._new(Kind.EQUATION, display="block")
        self._equation(element, latex, "block")
        self.chars += len(latex)

    def _definitions(self, items: list[Any], depth: int) -> None:
        for term, definitions in items:
            words = _plain(term)
            for blocks in definitions:
                text = _plain(_first_inlines(blocks))
                element = self._new(Kind.PARAGRAPH, text=f"{words} {text}".strip())
                if words:
                    element.term_end = len(words)
                self.chars += len(element.text or "")
                self.blocks(blocks[1:], depth + 1)

    # ------------------------------------------------------------------ inline text

    def _inline_text(
        self, inlines: list[Any], *, owner: Element | None, depth: int = 0
    ) -> tuple[str, list[tuple[str, int, int, object]], int]:
        """The text of `inlines` with an inline marker where each formula is (an equation
        element whose parent is `owner`; without an owner the formula is written as its TeX and
        counted in the third value), and the spans: links, raised and lowered text, struck text."""
        parts: list[str] = []
        spans: list[tuple[str, int, int, object]] = []
        formulas = 0
        length = 0

        def emit(text: str) -> None:
            nonlocal length
            parts.append(text)
            length += len(text)

        def visit(items: list[Any], level: int) -> None:
            nonlocal formulas
            if level > MAX_DEPTH:
                raise ReadError("too deep")
            for item in items:
                kind, content = item.get("t"), item.get("c")
                if kind == "Str":
                    emit(content)
                elif kind in ("Space", "SoftBreak", "LineBreak"):
                    emit(" ")
                elif kind == "Math":
                    latex = content[1]
                    if owner is not None:
                        eq = self._new(Kind.EQUATION, display="inline", parent_id=owner.id)
                        self._equation(eq, latex, "inline")
                        emit(inline_marker(eq.id))
                    else:
                        formulas += 1
                        emit(latex)
                elif kind == "Image":
                    self.loose.append(item)
                elif kind == "Note":
                    note, _, _ = self._inline_text(
                        _note_inlines(content), owner=None, depth=level + 1
                    )
                    if note:
                        emit(f" (note: {note})")
                elif kind == "Code":
                    emit(content[1])
                elif kind == "Quoted":
                    left, right = _QUOTES.get(content[0].get("t", ""), ("", ""))
                    emit(left)
                    visit(content[1], level + 1)
                    emit(right)
                elif kind in ("Link", "Span", "Emph", "Strong", "Underline", "SmallCaps",
                              "Strikeout", "Superscript", "Subscript", "Cite"):  # fmt: skip
                    start = length
                    if kind == "Span" and content[0][0] and owner is not None:
                        self.ids.setdefault(str(content[0][0]), owner.id)  # a bookmark
                    inner = content[1] if kind in ("Link", "Span") else content
                    inner = content[1] if kind == "Cite" else inner
                    visit(inner, level + 1)
                    if length > start:
                        if kind == "Link" and is_safe_href(content[2][0]):
                            spans.append(("uri", start, length, content[2][0]))
                        elif kind == "Link" and content[2][0].startswith("#"):
                            spans.append(("ref", start, length, content[2][0][1:]))
                        elif kind == "Superscript":
                            spans.append(("sup", start, length, None))
                        elif kind == "Subscript":
                            spans.append(("sub", start, length, None))
                        elif kind == "Strikeout":
                            spans.append(("deleted", start, length, None))

        visit(inlines, depth)
        raw = "".join(parts)
        text = re.sub(r"[ \t\r\n\u00a0]+", " ", raw)
        lead = len(text) - len(text.lstrip(" "))
        text = text.strip()
        # the squash changed the length only where whitespace ran together: spans are kept by
        # re-finding them on the squashed text through a map of offsets
        offsets = _offsets(raw)
        shifted = []
        for kind, a, b, extra in spans:
            a2, b2 = max(0, offsets[a] - lead), max(0, offsets[b] - lead)
            if b2 > a2 and b2 <= len(text):
                shifted.append((kind, a2, b2, extra))
        return text, shifted, formulas

    def _apply_spans(self, element: Element, spans: list[tuple[str, int, int, object]]) -> None:
        text = element.text or ""
        markers = [(m.start(), m.end()) for m in INLINE_MARKER_RE.finditer(text)]
        for kind, start, end, extra in spans:
            if start >= end or end > len(text):
                continue
            if any(a < start < b or a < end < b for a, b in markers):
                continue  # a span may not start or end inside an inline marker
            try:
                if kind == "uri":
                    inline.add(element, Link(kind="uri", start=start, end=end, href=str(extra)))
                elif kind == "ref":
                    self.refs.append((element, start, end, str(extra)))
                elif kind in ("sup", "sub"):
                    inline.add(element, ScriptRun(start=start, end=end, kind=kind))
                elif kind == "deleted":
                    inline.add(element, Run(start=start, end=end, kind="deleted"))
            except ValueError:
                continue

    # ------------------------------------------------------------------ equations

    def _equation(self, element: Element, latex: str, display: Display) -> None:
        report = self.report
        report.math_read += 1
        cleaned, mathml, failed = self._convert(latex, display)
        fix, failed = equation_fix(element, cleaned, mathml, failed, display)
        if failed:
            report.math_failed += 1
            self.out.errors += 1
        else:
            report.math_converted += 1
            if _is_flat(cleaned):
                report.math_flat += 1
                fix = fix.model_copy(
                    update={
                        "checks": [
                            *fix.checks,
                            Check(name=FLAT_CHECK, passed=False, severity="warning", code="flat"),
                        ],
                        "review": "optional",
                    }
                )
        self.out.fixes[element.id] = fix

    def _convert(self, latex: str, display: Display) -> tuple[str, str, bool]:
        """(LaTeX as the product stores it, MathML, failed) of pandoc's LaTeX for one equation,
        through the checks every LaTeX reading passes (`validate.check_latex`)."""
        from readablestem.pipeline.equation_step import read_latex
        from readablestem.pipeline.validate import (
            ALLOWED_COMMANDS,
            LatexConversionError,
            check_latex,
            has_errors,
            safe_latex_to_mathml,
        )

        report = self.report
        if len(latex) > MAX_MATH_CHARS:
            report.math_failures["too_long"] += 1
            return latex[:MAX_MATH_CHARS], "", True
        cleaned = read_latex(latex)
        checks = check_latex(cleaned)
        if has_errors(checks):
            for check in checks:
                if not check.passed and check.severity == "error":
                    report.math_failures[check.code or check.name] += 1
            report.failed_commands.update(
                c for c in _COMMAND.findall(cleaned) if c not in ALLOWED_COMMANDS
            )
            return cleaned, "", True
        try:
            return cleaned, safe_latex_to_mathml(cleaned, display), False
        except LatexConversionError:
            report.math_failures["converter"] += 1
            return cleaned, "", True

    # ------------------------------------------------------------------ lists

    def _list(self, kind: str, content: Any, level: int, depth: int) -> None:
        if depth > MAX_DEPTH:
            raise ReadError("too deep")
        ordered = kind == "OrderedList"
        number, style, delim = (
            (content[0][0], content[0][1].get("t", ""), content[0][2].get("t", ""))
            if ordered
            else (1, "", "")
        )
        items = content[1] if ordered else content
        for index, blocks in enumerate(items):
            marker = _marker(number + index, style, delim) if ordered else None
            element = self._new(
                Kind.LIST_ITEM, text="", list_marker=marker, list_ordered=ordered, list_level=level
            )
            lead, inlines, rest = _lead_of(blocks)
            text, spans = "", []
            if lead is not None:
                text, spans, _ = self._inline_text(inlines, owner=element)
            element.text = text
            self._apply_spans(element, spans)
            self.chars += len(text)
            pictures, self.loose = self.loose, []
            for picture in pictures:
                self._picture(picture)
            for block in [*rest, *(b for b in blocks if b is not lead)]:
                if block.get("t") in ("BulletList", "OrderedList"):
                    self._list(block["t"], block["c"], level + 1, depth + 1)
                else:
                    self.block(block, depth + 1)
            if not text.strip() and self.out.elements[-1] is element:
                self._drop(element)  # an empty item holds no word and no child: a blank line

    # ------------------------------------------------------------------ tables

    def _table(self, content: list[Any]) -> None:
        attr, caption, _, head, bodies, foot = content
        style = dict(attr[2]).get("custom-style", "")
        count = style.removeprefix(docx_package.HEADER_STYLE)
        if style.startswith(docx_package.HEADER_STYLE) and count.isdigit():
            marked = int(count)
        else:  # an older pandoc: the file's own list, when it lines up with the tree's tables
            marks = self.prepared.facts.header_marks
            place = self.table_place.get(id(content))
            marked = (
                marks[place] if place is not None and len(marks) == len(self.table_place) else 0
            )
        head_rows = list(head[1])
        rows = [*head_rows]
        for body in bodies:
            rows.extend(body[2])
            rows.extend(body[3])
        rows.extend(foot[1])
        caption_text = _plain(_first_inlines(caption[1])) or None
        element = self._new(Kind.TABLE, text=None)
        grid = self._grid(rows, caption_text)
        if grid is None:
            self._drop(element)
            self._flatten(rows)
            return
        proposal = False
        if marked:
            header_rows = min(marked, grid.rows - 1)
        elif head_rows:
            header_rows, proposal = (1 if grid.rows > 1 else 0), True
        else:
            header_rows = 0
        if header_rows:
            cells = [
                c.model_copy(update={"header": "col"}) if c.row < header_rows else c
                for c in grid.cells
            ]
            grid = grid.model_copy(update={"cells": cells, "header_rows": header_rows})
        element.table = grid
        fix = Fix(
            element_id=element.id,
            kind=Kind.TABLE,
            status=Status.ACCEPTED,
            confidence=1.0,
            table=TableFix(grid=grid),
            checks=[Check(name="source.exact", passed=True)],
        )
        if proposal:
            fix = fix.model_copy(
                update={
                    "checks": [
                        *fix.checks,
                        Check(name=HEADER_CHECK, passed=False, severity="warning", code="default"),
                    ],
                    "review": "optional",
                }
            )
        pictures, self.loose = self.loose, []
        if pictures:
            fix = needs_decision(fix, TABLE_PICTURE)
        self.out.fixes[element.id] = fix
        self.chars += sum(len(c.text) for c in grid.cells)
        for picture in pictures:
            self._picture(picture)

    def _grid(self, rows: list[Any], caption: str | None) -> TableGrid | None:
        taken: set[tuple[int, int]] = set()
        cells: list[TableCell] = []
        width = 0
        for r, row in enumerate(rows):
            c = 0
            for cell in row[1]:
                while (r, c) in taken:
                    c += 1
                row_span, col_span = max(int(cell[2]), 1), max(int(cell[3]), 1)
                for dr in range(row_span):
                    for dc in range(col_span):
                        taken.add((r + dr, c + dc))
                text, latex, mathml = self._cell(cell[4])
                cells.append(
                    TableCell(
                        row=r, col=c, row_span=row_span, col_span=col_span,
                        text=text, latex=latex, mathml=mathml,
                    )
                )  # fmt: skip
                c += col_span
                width = max(width, c)
        height = len(rows)
        if height < 1 or width < 1 or height > MAX_TABLE_ROWS or width > MAX_TABLE_COLS:
            return None
        if len(cells) > MAX_TABLE_CELLS:
            return None
        # spans may reach past the last row or column in a damaged file: cut them to the grid
        bounded = []
        for cell in cells:
            bounded.append(
                cell.model_copy(
                    update={
                        "row_span": min(cell.row_span, height - cell.row),
                        "col_span": min(cell.col_span, width - cell.col),
                    }
                )
            )
        occupied: set[tuple[int, int]] = set()
        for cell in bounded:
            occupied.update(
                (cell.row + dr, cell.col + dc)
                for dr in range(cell.row_span)
                for dc in range(cell.col_span)
            )
        for r in range(height):  # a slot no cell covers: an empty cell
            for c in range(width):
                if (r, c) not in occupied:
                    bounded.append(TableCell(row=r, col=c))
        return TableGrid(rows=height, cols=width, cells=bounded, caption=caption, source="local")

    def _cell(self, blocks: list[Any]) -> tuple[str, str | None, str | None]:
        """(text, latex, mathml) of a cell: its paragraphs joined by a space. A cell that holds
        maths is also written as LaTeX with its words in `\\text{}`, when that converts."""
        words: list[str] = []
        pieces: list[str] = []
        has_math = False
        for inlines in _paragraph_inlines(blocks):
            text, _, _ = self._inline_text(inlines, owner=None)
            words.append(text)
            for item in _flat(inlines):
                if item.get("t") == "Math":
                    has_math = True
        text = " ".join(w for w in words if w)
        if not has_math:
            return text, None, None
        for inlines in _paragraph_inlines(blocks):
            pieces.append(_latex_of(inlines))
        latex = " ".join(p for p in pieces if p).strip()
        cleaned, mathml, failed = self._convert(latex, "inline")
        if failed or not mathml:
            self.report.math_failed += 1
            return text, None, None
        self.report.math_converted += 1
        return text, cleaned, mathml

    def _flatten(self, rows: list[Any]) -> None:
        """A table too large for a grid: one list item per row."""
        for row in rows:
            texts = [self._cell(cell[4])[0] for cell in row[1]]
            text = " | ".join(t for t in texts if t)
            if text:
                element = self._new(
                    Kind.LIST_ITEM, text=text, list_marker=None, list_ordered=False, list_level=1
                )
                self.chars += len(element.text or "")

    # ------------------------------------------------------------------ figures

    def _figure(self, content: list[Any], depth: int) -> None:
        """pandoc's `Figure` block: its blocks, then its caption as the caption of the picture
        it made (or a paragraph when it made none)."""
        before = len(self.out.elements)
        self.blocks(content[2], depth + 1)
        text = _plain(_first_inlines(content[1][1])) or None
        if not text:
            return
        pictured = [e for e in self.out.elements[before:] if e.kind is Kind.FIGURE]
        if pictured and not pictured[-1].caption:
            pictured[-1].caption = text
        else:
            self._new(Kind.PARAGRAPH, text=text)
            self.chars += len(text)

    def _picture(self, image: dict[str, Any]) -> None:
        _, alt, target = image["c"]
        src = str(target[0])
        author = None
        words, _, _ = self._inline_text(alt, owner=None)
        from readablestem.pipeline.tagged import usable_alt

        author = usable_alt(words) if words else None
        element = self._new(Kind.FIGURE)
        self.report.pictures += 1
        png = None
        if not src.startswith(("http:", "https:", "data:", "//")):
            raw = self.prepared.picture(src)
            png = figures.png_of(raw) if raw is not None else None
        if png is None:
            self.report.pictures_unread += 1
            self.out.fixes[element.id] = Fix(
                element_id=element.id,
                kind=Kind.FIGURE,
                status=Status.FAILED,
                reason=UNKNOWN_FIGURE_REASON,
                review="required",
                sources=["source_read"],
            )
        else:
            self.out.images[element.id] = png
            if author is not None:
                self.report.pictures_authored_alt += 1
                self.out.fixes[element.id] = Fix(
                    element_id=element.id,
                    kind=Kind.FIGURE,
                    status=Status.ACCEPTED,
                    confidence=1.0,
                    figure=FigureFix(figure_type="other", alt=author),
                    checks=[Check(name="source.author_alt", passed=True)],
                )
        self.chars += CHARS_PER_PAGE // 3

    def _caption(self, blocks: list[Any]) -> None:
        text = _plain(_first_inlines(blocks))
        if not text:
            return
        element = self._new(Kind.PARAGRAPH, text=text)
        self.chars += len(text)
        self.captions[element.id] = text

    def attach_captions(self) -> None:
        """A caption paragraph straight after a picture or a table, or straight before one, is
        that picture's or table's caption (pandoc 3.7 gives a table its caption itself, an older
        one leaves the paragraph); any other stays a paragraph."""
        elements = self.out.elements
        for element in list(elements):
            if element.id not in self.captions:
                continue
            at = elements.index(element)
            after = elements[at - 1] if at > 0 else None
            before = elements[at + 1] if at + 1 < len(elements) else None
            for target in (after, before):
                if target is None or not self._captioned(target, element.text or ""):
                    continue
                elements.remove(element)
                self.report.captions += 1
                break

    def _captioned(self, target: Element, text: str) -> bool:
        if target.kind is Kind.FIGURE and not target.caption:
            target.caption = text
            return True
        fix = self.out.fixes.get(target.id)
        if (
            target.kind is Kind.TABLE
            and target.table is not None
            and not target.table.caption
            and fix is not None
            and fix.table is not None
        ):
            target.table = target.table.model_copy(update={"caption": text})
            fix.table = fix.table.model_copy(update={"grid": target.table})
            return True
        return False


# ---------------------------------------------------------------------------- helpers


META_KEYS = ("title", "subtitle", "author", "date", "abstract")


def _lead_of(blocks: list[Any]) -> tuple[Any, list[Any], list[Any]]:
    """The paragraph a list item starts with: its block, its inlines and the blocks that come
    with it. With `+styles` the paragraph is wrapped in a Div carrying its style name ("List
    Paragraph"), and that Div is the block."""
    for block in blocks:
        kind = block.get("t")
        if kind in ("Para", "Plain"):
            return block, block["c"], []
        if kind == "Div":
            inner = block["c"][1]
            if inner and inner[0].get("t") in ("Para", "Plain"):
                return block, inner[0]["c"], inner[1:]
    return None, [], []


def _tables_of(tree: dict[str, Any]) -> list[Any]:
    """The `Table` nodes' contents of the tree, in the order of the file (a table inside a cell
    after the cell's table)."""
    found: list[Any] = []
    stack: list[Any] = [tree.get("blocks", [])]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(reversed(node))
        elif isinstance(node, dict):
            if node.get("t") == "Table":
                found.append(node["c"])
            if "c" in node:
                stack.append(node["c"])
    return found


def _meta_blocks(value: dict[str, Any]) -> list[Any]:
    kind = value.get("t")
    content: Any = value.get("c") or []
    if kind == "MetaInlines":
        return [{"t": "Para", "c": content}]
    if kind == "MetaString":
        return [{"t": "Para", "c": [{"t": "Str", "c": str(value.get("c") or "")}]}]
    if kind == "MetaBlocks":
        return list(content)
    if kind == "MetaList":
        return [block for item in content for block in _meta_blocks(item)]
    return []


def _meta_inlines(value: dict[str, Any]) -> list[Any]:
    blocks = _meta_blocks(value)
    return _first_inlines(blocks) if blocks else []


def _is_display(item: dict[str, Any]) -> bool:
    return item.get("t") == "Math" and item["c"][0].get("t") == "DisplayMath"


def _first_inlines(blocks: list[Any]) -> list[Any]:
    """The inlines of the first paragraph-like block of `blocks` (a Div wraps them)."""
    for block in blocks:
        if block.get("t") in ("Para", "Plain"):
            return block["c"]
        if block.get("t") == "Div":
            found = _first_inlines(block["c"][1])
            if found:
                return found
    return []


def _paragraph_inlines(blocks: list[Any]) -> list[list[Any]]:
    out: list[list[Any]] = []
    for block in blocks:
        kind = block.get("t")
        if kind in ("Para", "Plain"):
            out.append(block["c"])
        elif kind == "Div":
            out.extend(_paragraph_inlines(block["c"][1]))
        elif kind in ("BulletList", "OrderedList"):
            lists = block["c"][1] if kind == "OrderedList" else block["c"]
            for item in lists:
                out.extend(_paragraph_inlines(item))
        elif kind == "Table":
            for row in [*block["c"][3][1], *(r for b in block["c"][4] for r in (*b[2], *b[3]))]:
                for cell in row[1]:
                    out.extend(_paragraph_inlines(cell[4]))
    return out


def _note_inlines(blocks: list[Any]) -> list[Any]:
    out: list[Any] = []
    for inlines in _paragraph_inlines(blocks):
        if out:
            out.append({"t": "Space"})
        out.extend(inlines)
    return out


def _flat(inlines: list[Any]) -> list[dict[str, Any]]:
    """Every inline of `inlines`, nested ones included (not the inside of a note)."""
    found: list[dict[str, Any]] = []
    stack = list(reversed(inlines))
    while stack:
        item = stack.pop()
        found.append(item)
        content = item.get("c")
        kind = item.get("t")
        if kind in (
            "Emph",
            "Strong",
            "Underline",
            "SmallCaps",
            "Strikeout",
            "Superscript",
            "Subscript",
        ):
            stack.extend(reversed(content))
        elif kind in ("Link", "Span") or kind == "Quoted":
            stack.extend(reversed(content[1]))
    return found


def _plain(inlines: list[Any]) -> str:
    out: list[str] = []
    for item in _flat(inlines):
        kind = item.get("t")
        if kind == "Str":
            out.append(item["c"])
        elif kind in ("Space", "SoftBreak", "LineBreak"):
            out.append(" ")
        elif kind == "Math" or kind == "Code":
            out.append(item["c"][1])
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _latex_of(inlines: list[Any]) -> str:
    """A paragraph of a table cell as one LaTeX formula: its words in `\\text{}`, its formulas as
    they are."""
    out: list[str] = []
    word: list[str] = []

    def flush() -> None:
        text = "".join(word).strip()
        word.clear()
        if text:
            out.append(r"\text{" + re.sub(r"([\\{}$&#%_^~])", " ", text) + "}")

    for item in _flat(inlines):
        kind = item.get("t")
        if kind == "Str":
            word.append(item["c"])
        elif kind in ("Space", "SoftBreak", "LineBreak"):
            word.append(" ")
        elif kind == "Math":
            flush()
            out.append(item["c"][1])
    flush()
    return " ".join(out)


def _offsets(raw: str) -> list[int]:
    """For each offset in `raw`, the offset in `raw` with runs of whitespace squashed to one
    space (so a span's ends can be moved to the squashed text)."""
    out = [0] * (len(raw) + 1)
    position = 0
    previous_space = False
    for i, ch in enumerate(raw):
        out[i] = position
        space = ch in " \t\r\n\u00a0"
        if not (space and previous_space):
            position += 1
        previous_space = space
    out[len(raw)] = position
    return out


def _is_flat(latex: str) -> bool:
    """An equation typed with no structure: a letter followed by a digit and no script at all."""
    return bool(_FLAT.search(latex)) and "^" not in latex and "_" not in latex


def _marker(number: int, style: str, delim: str) -> str:
    if style in ("LowerAlpha", "UpperAlpha"):
        letter = chr(ord("a") + (number - 1) % 26)
        label = letter.upper() if style == "UpperAlpha" else letter
    elif style in ("LowerRoman", "UpperRoman"):
        label = _roman(number)
        label = label.upper() if style == "UpperRoman" else label
    else:
        label = str(number)
    if delim == "OneParen":
        return f"{label})"
    if delim == "TwoParens":
        return f"({label})"
    return f"{label}."


_ROMAN = (
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"),
    (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
)  # fmt: skip


def _roman(number: int) -> str:
    out = ""
    for value, symbol in _ROMAN:
        while number >= value:
            out += symbol
            number -= value
    return out or "i"


def _tree_words(tree: dict[str, Any]) -> Counter[str]:
    """The words of pandoc's tree the way the file's own pass counts the file's (`Facts.words`):
    text and breaks, no formulas, no alt text; a block's end is a space."""
    out: list[str] = []
    stack: list[Any] = [tree.get("blocks", [])]
    meta = tree.get("meta", {})
    for key in reversed(META_KEYS):
        if key in meta:
            stack.extend([" ", meta[key]])
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(reversed(node))
        elif isinstance(node, dict):
            kind = node.get("t")
            if kind in ("Str", "MetaString"):
                out.append(str(node["c"]))
            elif kind in ("Space", "SoftBreak", "LineBreak"):
                out.append(" ")
            elif kind == "Code":
                out.append(node["c"][1])
            elif kind == "CodeBlock":
                out.extend((node["c"][1], " "))
            elif kind in ("Math", "Image", "RawInline", "RawBlock"):
                continue
            else:
                if kind == "Note":  # the note's words are apart from the words around the mark
                    out.append(" ")
                    stack.append(" ")
                if kind in ("Para", "Plain", "Header", "CodeBlock", "LineBlock"):
                    stack.append(" ")
                if "c" in node:
                    stack.append(node["c"])
        elif isinstance(node, str) and node == " ":
            out.append(" ")
    return Counter("".join(out).split())


def _language(facts: docx_package.Facts) -> str:
    """The document's language as a proposal: where most letters are written, else Word's own
    default, else English; the primary subtag, lower case."""
    tag = facts.language_chars.most_common(1)[0][0] if facts.language_chars else ""
    tag = tag or facts.default_language
    primary = tag.split("-", 1)[0].lower()
    return primary if re.fullmatch(r"[a-z]{2,3}", primary) else "en"


def read(prepared: docx_package.Prepared, tree: dict[str, Any]) -> tuple[Reading, Report]:
    """The reading of pandoc's `tree` of `prepared`'s working copy, and what was done with the
    file. `ReadError` for a tree that is too large or too deep."""
    reader = _Reader(prepared)
    facts = prepared.facts
    reader.table_place = {id(table): n for n, table in enumerate(_tables_of(tree))}
    reader.meta(tree.get("meta", {}))
    reader.blocks(tree.get("blocks", []))
    reader.attach_captions()
    link_internal(reader.out.elements, reader.ids, reader.refs)
    out, report = reader.out, reader.report
    out.lang = _language(facts)
    report.math_held = facts.equations
    for element in out.elements:
        report.kinds[element.kind.value] += 1
    # the coverage rule: every word of the file is in the tree or counted here
    seen = _tree_words(tree)
    out.unread_words = sum((facts.words - seen).values())
    report.extra_words = sum((seen - facts.words).values())
    census = prepared.census
    changes = census.tracked_insertions + census.tracked_deletions
    if changes:
        out.notices.append(Notice(code=CHANGES_NOTICE, count=changes, review="required"))
    if facts.hidden_runs:
        out.notices.append(Notice(code=HIDDEN_NOTICE, count=facts.hidden_runs))
    if facts.text_boxes:
        out.notices.append(Notice(code=BOXES_NOTICE, count=facts.text_boxes))
    if facts.headers_footers:
        out.notices.append(Notice(code=HEADERS_NOTICE, count=facts.headers_footers))
    if facts.rtl_paragraphs:
        out.notices.append(Notice(code=RTL_NOTICE, count=facts.rtl_paragraphs))
    objects = facts.smartart + facts.charts + facts.embedded
    if objects:
        out.notices.append(Notice(code=OBJECTS_NOTICE, count=objects, review="required"))
    if out.unread_words:
        out.notices.append(
            Notice(code=SOURCE_UNREAD_NOTICE, count=out.unread_words, review="required")
        )
    return out, report


__all__ = ["NOTICE_CODES", "ReadError", "Report", "read"]
