"""LaTeXML's XHTML (with Presentation MathML) as the pipeline's document model.

`read(xhtml, project)` returns a `Reading`: elements in reading order, the fixes that are
settled without a model (equations and tables), the pictures of the figures (read from the
project's own files, `source.figures`) and the notices. The figure step, assembly, review, the
record and the exports take it from there like a PDF's.

What each part becomes:

- headings (`h1`..`h6`, abstract, theorem titles), paragraphs, lists (one `list_item` per item,
  with its marker and nesting), descriptions (a glossary entry), listings (preformatted text);
- an equation (`equation`, `align`, ... rows) or an inline `$...$`: an **exact** equation. Its
  LaTeX is the source's own (LaTeXML's `alttext`), its MathML is LaTeXML's, accepted through the
  existing sanitiser (`web.mathml`, ADR 0008) and spoken by the usual reading. A formula LaTeXML
  could not read (`ltx_ERROR`, an unknown macro) is a required decision showing the TeX, with our
  own LaTeX conversion as the proposal when that works (`\\ce` is one);
- a table (`tabular`): a grid with its header rows and spans, an accepted fix;
- a figure: one element per picture; the picture is the project's image file as a PNG; with
  no usable picture the figure is a required decision (`source_figure_missing`);
- text-level errors stay in the text as written and add the notice `source_error`.

Nothing here runs TeX or opens a file outside the project folder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from xml.etree.ElementTree import Element as Node

import defusedxml.ElementTree as SafeET

from readablestem.core.types import (
    Check,
    Element,
    EquationFix,
    Fix,
    HeadingFix,
    Kind,
    Link,
    Notice,
    ScriptRun,
    Status,
    TableCell,
    TableFix,
    TableGrid,
    inline_marker,
)
from readablestem.source import figures

CHARS_PER_PAGE = 3000
MAX_XHTML_BYTES = 60 * 1024 * 1024
MAX_DEPTH = 80
Display = Literal["block", "inline"]
MAX_ELEMENTS = 20_000
MAX_MATH_CHARS = 200_000
BOX = (0.0, 0.0, 1.0, 1.0)
UNKNOWN_FIGURE_REASON = "source_figure_missing"
SOURCE_ERROR_NOTICE = "source_error"
_SPACE = re.compile("[ \\t\\r\\n\\u00a0]+")
_LEVEL = re.compile(r"^h([1-6])$")


class ReadError(ValueError):
    """The XHTML is not usable (not well formed, too large, no document)."""


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _classes(node: Node) -> set[str]:
    return set((node.get("class") or "").split())


def _squash(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def _children(node: Node) -> list[Node]:
    return [c for c in node if isinstance(c.tag, str)]


@dataclass
class Reading:
    title: str | None = None
    lang: str = "en"
    elements: list[Element] = field(default_factory=list)
    fixes: dict[str, Fix] = field(default_factory=dict)
    images: dict[str, bytes] = field(default_factory=dict)  # figure element id -> PNG
    notices: list[Notice] = field(default_factory=list)
    errors: int = 0


class _Reader:
    def __init__(self, project: Path, graphics: dict[str, str] | None = None) -> None:
        self.project = project
        self.graphics = graphics or {}
        self.out = Reading()
        self.chars = 0
        self.counter = 0
        self.errors = 0

    # ------------------------------------------------------------------ elements

    def _new(self, kind: Kind, **fields) -> Element:
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

    def _heading_fix(self, element: Element, level: int, text: str) -> None:
        self.out.fixes[element.id] = Fix(
            element_id=element.id,
            kind=Kind.HEADING,
            status=Status.ACCEPTED,
            confidence=1.0,
            heading=HeadingFix(level=min(max(level, 1), 6), text=text),
            checks=[Check(name="source.exact", passed=True)],
        )

    # ------------------------------------------------------------------ walking

    def walk(self, node: Node, depth: int = 0) -> None:
        if depth > MAX_DEPTH:
            raise ReadError("too deep")
        tag = _local(node.tag)
        classes = _classes(node)
        if tag in ("head", "script", "style", "nav") or classes & {"ltx_page_footer", "ltx_dates"}:
            return
        if tag == "math":  # display maths outside any equation table
            self._display_math([node], None)
            return
        if m := _LEVEL.match(tag):
            self._section_heading(node, int(m.group(1)), classes)
            return
        if tag == "p":
            self._paragraph(node)
            return
        if tag in ("ul", "ol") or classes & {"ltx_itemize", "ltx_enumerate", "ltx_biblist"}:
            self._list(node, 1)
            return
        if tag == "dl" or "ltx_description" in classes:
            self._description(node)
            return
        if tag == "table" and classes & {"ltx_equation", "ltx_equationgroup"}:
            self._equation_table(node)
            return
        if tag == "table" and "ltx_tabular" in classes:
            self._table(node, caption=None)
            return
        if _is_figure(node):
            self._figure(node, depth)
            return
        if classes & {"ltx_listing", "ltx_verbatim"} or tag == "pre":
            self._listing(node)
            return
        if "ltx_theorem" in classes or "ltx_proof" in classes:
            self._theorem(node, depth)
            return
        if "ltx_authors" in classes:
            text = _squash(self._plain(node))
            if text:
                self._new(Kind.PARAGRAPH, text=text)
                self.chars += len(text)
            return
        if tag in ("span", "a", "em", "strong", "b", "i") and not _children_blocks(node):
            # loose inline text directly inside a container: a paragraph of its own
            text, _ = self._inline_text(node)
            if text:
                self._new(Kind.PARAGRAPH, text=text)
                self.chars += len(text)
            return
        for child in _children(node):
            self.walk(child, depth + 1)

    def _section_heading(self, node: Node, level: int, classes: set[str]) -> None:
        if "ltx_runin" in classes:  # a theorem's "Theorem 1." title: handled with its body
            return
        if "ltx_title_abstract" in classes:
            level = 2
        text, _ = self._inline_text(node, skip_tags=True)
        if not text:
            return
        element = self._new(Kind.HEADING, text=text, bold=True)
        self._heading_fix(element, level, text)
        self.chars += len(text)
        if "ltx_title_document" in classes and self.out.title is None:
            self.out.title = text

    def _theorem(self, node: Node, depth: int) -> None:
        title = ""
        for child in _children(node):
            if _local(child.tag).startswith("h") and "ltx_runin" in _classes(child):
                title = _squash(self._plain(child))
        self._lead = title  # the next paragraph starts with it ("Theorem 1.")
        for child in _children(node):
            if _local(child.tag).startswith("h") and "ltx_runin" in _classes(child):
                continue
            self.walk(child, depth + 1)
        self._lead = ""

    _lead = ""

    # ------------------------------------------------------------------ text

    def _paragraph(self, node: Node) -> None:
        lead, self._lead = self._lead, ""
        element = self._new(Kind.PARAGRAPH, text="")
        text, spans = self._inline_text(node, owner=element)
        if lead:
            text = f"{lead} {text}".strip()
            shift = len(lead) + 1
            spans = [(kind, a + shift, b + shift, extra) for kind, a, b, extra in spans]
        if not _squash(self._plain(node)) and "⟦" not in text:
            self.out.elements.remove(element)
            self.counter -= 1
            return
        element.text = text
        self._apply_spans(element, spans)
        self.chars += len(text)

    def _plain(self, node: Node) -> str:
        return "".join(node.itertext())

    def _inline_text(
        self, node: Node, *, owner: Element | None = None, skip_tags: bool = False
    ) -> tuple[str, list[tuple[str, int, int, object]]]:
        """The text of `node` with an inline marker where each formula is (formulas become
        equation elements whose parent is `owner`; without an owner their TeX is written in
        the text), and the spans (links, raised and lowered runs) as (kind, start, end, data)."""
        parts: list[str] = []
        spans: list[tuple[str, int, int, object]] = []
        length = [0]

        def emit(text: str) -> None:
            parts.append(text)
            length[0] += len(text)

        def visit(item: Node, depth: int) -> None:
            if depth > MAX_DEPTH:
                raise ReadError("too deep")
            tag = _local(item.tag)
            classes = _classes(item)
            if skip_tags and "ltx_tag" in classes:
                return
            if tag == "math":
                if owner is not None:
                    eq = self._inline_math(item, owner)
                    emit(inline_marker(eq.id))
                else:
                    emit(item.get("alttext") or _squash(self._plain(item)))
            elif tag in ("script", "style"):
                pass
            elif tag == "br":
                emit(" ")
            elif "ltx_ERROR" in classes:
                self.errors += 1
                emit(_squash(self._plain(item)))
            elif classes & {"ltx_note"}:
                note = _squash(
                    "".join(
                        _text_without_tag(c)
                        for c in item.iter()
                        if "ltx_note_content" in _classes(c)
                    )
                )
                if note:
                    emit(f" (note: {note})")
            else:
                start = length[0]
                if item.text:
                    emit(_SPACE.sub(" ", item.text))
                for child in _children(item):
                    visit(child, depth + 1)
                    if child.tail:
                        emit(_SPACE.sub(" ", child.tail))
                if tag == "a":
                    href = item.get("href") or ""
                    if length[0] > start and href.startswith(("http://", "https://", "mailto:")):
                        spans.append(("uri", start, length[0], href))
                elif tag in ("sup", "sub") and length[0] > start:
                    spans.append((tag, start, length[0], None))

        if node.text:
            emit(_SPACE.sub(" ", node.text))
        for child in _children(node):
            visit(child, 0)
            if child.tail:
                emit(_SPACE.sub(" ", child.tail))
        raw = "".join(parts)
        text = _squash(raw)
        # `_squash` may drop leading space: shift spans by what was removed from the front
        lead = len(raw) - len(raw.lstrip(" \u00a0\t\r\n"))
        shifted = []
        for kind, a, b, extra in spans:
            a, b = max(0, a - lead), max(0, b - lead)
            if b > a and b <= len(text):
                shifted.append((kind, a, b, extra))
        return text, shifted

    @staticmethod
    def _apply_spans(element: Element, spans: list[tuple[str, int, int, object]]) -> None:
        text = element.text or ""
        for kind, start, end, extra in spans:
            if start >= end or end > len(text):
                continue
            # a span may not start or end inside an inline marker
            if any(
                m.start() < start < m.end() or m.start() < end < m.end() for m in _markers(text)
            ):
                continue
            try:
                if kind == "uri":
                    element.links.append(Link(kind="uri", start=start, end=end, href=str(extra)))
                elif kind in ("sup", "sub"):
                    element.script_runs.append(ScriptRun(start=start, end=end, kind=kind))
            except ValueError:
                continue

    # ------------------------------------------------------------------ lists

    def _list(self, node: Node, level: int) -> None:
        ordered = _local(node.tag) == "ol" or "ltx_enumerate" in _classes(node)
        for item in _children(node):
            if _local(item.tag) != "li":
                continue
            marker = ""
            body: list[Node] = []
            for child in _children(item):
                if "ltx_tag" in _classes(child):
                    marker = _squash(self._plain(child))
                else:
                    body.append(child)
            element = self._new(
                Kind.LIST_ITEM, text="", list_marker=marker or None, list_ordered=ordered,
                list_level=level,
            )  # fmt: skip
            texts: list[str] = []
            spans: list[tuple[str, int, int, object]] = []
            rest: list[Node] = []
            taken = False
            for child in body:
                for sub in _paragraph_nodes(child):
                    if not taken:
                        text, spans = self._inline_text(sub, owner=element)
                        texts.append(text)
                        taken = True
                    else:
                        rest.append(sub)
                for sub in _non_paragraph_nodes(child):
                    rest.append(sub)
            if not taken:  # an item with no paragraph (a bibliography entry): its own text
                holder = Node("span")
                holder.extend(body)
                text, spans = self._inline_text(holder, owner=element, skip_tags=True)
                texts.append(text)
            if not taken:
                rest = [
                    r
                    for r in rest
                    if _local(r.tag) in ("ul", "ol")
                    or _classes(r) & {"ltx_itemize", "ltx_enumerate"}
                ]
            element.text = texts[0] if texts else ""
            self._apply_spans(element, spans)
            self.chars += len(element.text or "")
            for sub in rest:
                if _local(sub.tag) in ("ul", "ol") or _classes(sub) & {
                    "ltx_itemize",
                    "ltx_enumerate",
                }:
                    self._list(sub, level + 1)
                else:
                    self.walk(sub, 1)

    def _description(self, node: Node) -> None:
        term = ""
        for child in _children(node):
            tag = _local(child.tag)
            if tag == "dt" or ("ltx_item" in _classes(child) and not term):
                term = _squash(self._plain(child)).rstrip(":")
            elif tag == "dd" or (term and "ltx_item" in _classes(child)):
                body = _squash(self._plain(child))
                text = f"{term} {body}".strip() if term else body
                element = self._new(Kind.PARAGRAPH, text=text)
                if term:
                    element.term_end = len(term)
                self.chars += len(text)
                term = ""

    def _listing(self, node: Node) -> None:
        lines = [
            "".join(line.itertext()) for line in node.iter() if "ltx_listingline" in _classes(line)
        ]
        text = "\n".join(lines) if lines else "".join(node.itertext())
        text = text.strip("\n")
        if text.strip():
            element = self._new(Kind.PARAGRAPH, text=text)
            element.preformatted = True
            self.chars += len(text)

    # ------------------------------------------------------------------ maths

    def _math_parts(self, math: Node, display: Display) -> tuple[str, str, bool]:
        """(latex, mathml, has_error) of one `<math>`."""
        latex = (math.get("alttext") or "").strip()
        failed = any(_local(n.tag) == "merror" or "ltx_ERROR" in _classes(n) for n in math.iter())
        math.set("display", display)
        return latex, _serialise_math(math), failed

    def _exact(
        self, element: Element, latex: str, mathml: str, failed: bool, display: Display
    ) -> None:
        """Set the fix for an equation element from LaTeXML's reading of it."""
        from readablestem.pipeline.speech import SpeechError, mathml_to_speech
        from readablestem.web.mathml import UnsafeMathML, sanitize_mathml

        latex = re.sub(r"^\\displaystyle\s*", "", latex).strip()
        mathml_ok = ""
        if not failed and mathml:
            try:
                mathml_ok = str(sanitize_mathml(mathml))
            except UnsafeMathML:
                failed = True
        if not failed and not mathml_ok:
            failed = True
        if failed:
            self.errors += 1
            element.text = latex or None  # shown as written where nothing better exists
            proposal = _own_conversion(latex, display)
            fix = Fix(
                element_id=element.id,
                kind=Kind.EQUATION,
                status=Status.NEEDS_REVIEW if proposal is not None else Status.FAILED,
                reason=None if proposal is not None else "invalid_latex",
                confidence=None,
                equation=proposal,
                checks=[Check(name="source.macro", passed=False, code="unknown_command")],
                review="required",
            )
            self.out.fixes[element.id] = fix
            return
        speech, speech_check = "", None
        try:
            speech = mathml_to_speech(mathml_ok)
        except SpeechError:
            speech_check = Check(
                name="speech.generated", passed=False, severity="warning", code="speech_failed"
            )
        checks = [Check(name="source.exact", passed=True)]
        if speech_check is not None or not speech:
            checks.append(
                speech_check
                or Check(
                    name="speech.generated", passed=False, severity="warning", code="speech_empty"
                )
            )
        self.out.fixes[element.id] = Fix(
            element_id=element.id,
            kind=Kind.EQUATION,
            status=Status.ACCEPTED,
            confidence=1.0,
            equation=EquationFix(
                latex=latex,
                mathml=mathml_ok,
                speech=speech,
                display=display,
            ),
            checks=checks,
            review="optional" if len(checks) > 1 else "none",
        )

    def _inline_math(self, math: Node, owner: Element) -> Element:
        latex, mathml, failed = self._math_parts(math, "inline")
        element = self._new(Kind.EQUATION, display="inline", parent_id=owner.id)
        self._exact(element, latex, mathml, failed, "inline")
        return element

    def _display_math(self, maths: list[Node], label: str | None) -> None:
        latexes, parts, failed = [], [], False
        for math in maths:
            latex, mathml, bad = self._math_parts(math, "block")
            latexes.append(re.sub(r"^\\displaystyle\s*", "", latex).strip())
            parts.append(mathml)
            failed = failed or bad
        if len(maths) == 1:
            latex, mathml = latexes[0], parts[0]
        else:
            latex = " ".join(x for x in latexes if x)
            mathml = _join_math(maths)
        if not latex and not mathml:
            return
        element = self._new(Kind.EQUATION, display="block", equation_label=label)
        self._exact(element, latex, mathml, failed, "block")
        self.chars += len(latex)

    def _equation_table(self, node: Node) -> None:
        rows = [r for r in node.iter() if _local(r.tag) == "tr"]
        for row in rows:
            maths = [m for m in row.iter() if _local(m.tag) == "math"]
            if not maths:
                continue
            label = ""
            for cell in row.iter():
                if "ltx_tag_equation" in _classes(cell):
                    label = _squash(self._plain(cell))
            self._display_math(maths, label or None)

    # ------------------------------------------------------------------ tables

    def _table(self, node: Node, caption: str | None) -> None:
        grid = _grid(node, caption)
        element = self._new(Kind.TABLE, text=None)
        if grid is None:
            self._flatten_table(node, element)
            return
        element.table = grid
        self.out.fixes[element.id] = Fix(
            element_id=element.id,
            kind=Kind.TABLE,
            status=Status.ACCEPTED,
            confidence=1.0,
            table=TableFix(grid=grid),
            checks=[Check(name="source.exact", passed=True)],
        )
        self.chars += sum(len(c.text) for c in grid.cells)

    def _flatten_table(self, node: Node, element: Element) -> None:
        """A table too large for a grid: one list item per row."""
        self.out.elements.remove(element)
        self.counter -= 1
        for row in (r for r in node.iter() if _local(r.tag) == "tr"):
            cells = [_squash(self._plain(c)) for c in _children(row)]
            text = " | ".join(c for c in cells if c)
            if text:
                self._new(Kind.LIST_ITEM, text=text, list_ordered=False, list_level=1)
                self.chars += len(text)

    # ------------------------------------------------------------------ figures

    def _figure(self, node: Node, depth: int) -> None:
        classes = _classes(node)
        caption = ""
        for cap in node:
            if _local(cap.tag) == "figcaption" or "ltx_caption" in _classes(cap):
                caption = _squash(_text_without_tag(cap))
        if "ltx_table" in classes:
            for table in node.iter():
                if _local(table.tag) == "table" and "ltx_tabular" in _classes(table):
                    self._table(table, caption or None)
                    return
        nested = [c for c in node.iter() if c is not node and _is_figure(c)]
        inside = {id(n) for sub in nested for n in sub.iter()}
        below = {id(d) for sub in nested for d in sub.iter() if d is not sub}
        pictures = [
            n
            for n in node.iter()
            if id(n) not in inside
            and (_local(n.tag) in ("img", "svg") or "ltx_picture" in _classes(n))
        ]
        if not pictures and not nested:
            pictures = [None]  # type: ignore[list-item]  # a figure with no picture at all
        first = True
        for picture in pictures:
            element = self._new(Kind.FIGURE, caption=caption if first and caption else None)
            first = False
            png = self._picture(picture) if picture is not None else None
            if png is None:
                self.out.fixes[element.id] = Fix(
                    element_id=element.id,
                    kind=Kind.FIGURE,
                    status=Status.FAILED,
                    reason=UNKNOWN_FIGURE_REASON,
                    review="required",
                )
            else:
                self.out.images[element.id] = png
            self.chars += max(len(caption), CHARS_PER_PAGE // 3)
        for sub in nested:
            if id(sub) not in below:
                self._figure(sub, depth + 1)

    def _picture(self, node: Node) -> bytes | None:
        tag = _local(node.tag)
        if tag == "img":
            src = (node.get("src") or "").split("?", 1)[0].split("#", 1)[0]
            if not src or src.startswith("data:"):
                # LaTeXML was told not to convert graphics: the file's name is in its XML
                src = self.graphics.get(node.get("id") or "", "")
            return figures.figure_png(self.project, src)
        if tag == "svg" or "ltx_picture" in _classes(node):
            svg = next((n for n in node.iter() if _local(n.tag) == "svg"), None)
            if svg is None:
                return None
            return figures.svg_to_png(_serialise_svg(svg))
        return None


def _is_figure(node: Node) -> bool:
    return _local(node.tag) == "figure" or bool(_classes(node) & {"ltx_figure", "ltx_table"})


def _markers(text: str):
    from readablestem.core.types import INLINE_MARKER_RE

    return INLINE_MARKER_RE.finditer(text)


def _children_blocks(node: Node) -> bool:
    return any(
        _local(c.tag) in ("p", "div", "ul", "ol", "table", "figure", "section") for c in node.iter()
    )


def _paragraph_nodes(node: Node) -> list[Node]:
    if _local(node.tag) == "p":
        return [node]
    if "ltx_para" in _classes(node) or _local(node.tag) == "div":
        return [c for c in _children(node) if _local(c.tag) == "p"]
    return []


def _non_paragraph_nodes(node: Node) -> list[Node]:
    if _local(node.tag) == "p":
        return []
    if "ltx_para" in _classes(node) or _local(node.tag) == "div":
        return [c for c in _children(node) if _local(c.tag) != "p"]
    return [node]


def _text_without_tag(node: Node) -> str:
    parts: list[str] = []

    def visit(item: Node) -> None:
        if _classes(item) & {"ltx_tag", "ltx_note_mark"}:
            return
        if item.text:
            parts.append(item.text)
        for child in _children(item):
            if _local(child.tag) == "math":
                parts.append(child.get("alttext") or "")
            else:
                visit(child)
            if child.tail:
                parts.append(child.tail)

    visit(node)
    return "".join(parts)


# ---------------------------------------------------------------------------- MathML helpers


def _drop_empty_tokens(math: Node) -> None:
    """An empty `<mi/>` in a row (LaTeXML's placeholder before a leading relation) says nothing
    and is dropped; one that is a script's base is a real empty base and stays."""
    for parent in list(math.iter()):
        if _local(parent.tag) not in ("mrow", "math"):
            continue
        for child in list(parent):
            if _local(child.tag) == "mi" and not (child.text or "").strip() and not len(child):
                parent.remove(child)


def _serialise_math(math: Node) -> str:
    from xml.etree.ElementTree import tostring

    _drop_empty_tokens(math)
    tail, math.tail = math.tail, None  # `tostring` would write the text after the formula
    try:
        text = tostring(math, encoding="unicode")
    finally:
        math.tail = tail
    return "" if len(text) > MAX_MATH_CHARS else text


def _join_math(maths: list[Node]) -> str:
    """One `<math>` holding the children of several, in order (an aligned equation's cells)."""
    from xml.etree.ElementTree import tostring

    inner = []
    for math in maths:
        _drop_empty_tokens(math)
        for child in list(math):
            tail, child.tail = child.tail, None
            inner.append(tostring(child, encoding="unicode"))
            child.tail = tail
    return (
        '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block"><mrow>'
        + "".join(inner)
        + "</mrow></math>"
    )


def _serialise_svg(svg: Node) -> bytes:
    from xml.etree.ElementTree import tostring

    return tostring(svg, encoding="utf-8")


def _own_conversion(latex: str, display: Display) -> EquationFix | None:
    """Our own LaTeX conversion of a formula LaTeXML could not read, as the proposal for the
    decision; None when it does not convert."""
    if not latex:
        return None
    try:
        from readablestem.pipeline.equation_step import _speech, read_latex
        from readablestem.pipeline.validate import (
            LatexConversionError,
            check_latex,
            has_errors,
            safe_latex_to_mathml,
        )

        cleaned = read_latex(latex)
        if has_errors(check_latex(cleaned)):
            return None
        mathml = safe_latex_to_mathml(cleaned, display)
        speech, _ = _speech(mathml)
        return EquationFix(latex=cleaned, mathml=mathml, speech=speech, display=display)
    except (LatexConversionError, ValueError, KeyError, RecursionError):
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------- tables


def _grid(table: Node, caption: str | None) -> TableGrid | None:
    rows: list[tuple[Node, str]] = []  # (tr, section)
    for section in table:
        tag = _local(section.tag)
        if tag in ("thead", "tbody", "tfoot"):
            rows += [(r, tag) for r in section if _local(r.tag) == "tr"]
        elif tag == "tr":
            rows.append((section, "tbody"))
    if not rows:
        return None
    occupied: set[tuple[int, int]] = set()
    cells: list[TableCell] = []
    n_cols = 0
    header_rows = 0
    for r, (row, section) in enumerate(rows):
        if section == "thead":
            header_rows = r + 1
        col = 0
        for cell in row:
            tag = _local(cell.tag)
            if tag not in ("td", "th"):
                continue
            while (r, col) in occupied:
                col += 1
            try:
                row_span = max(1, min(int(cell.get("rowspan", "1")), 50))
                col_span = max(1, min(int(cell.get("colspan", "1")), 50))
            except ValueError:
                row_span = col_span = 1
            text = _squash("".join(_cell_text(cell)))
            latex = mathml = None
            maths = [m for m in cell.iter() if _local(m.tag) == "math"]
            if len(maths) == 1 and not _squash(_text_without_math(cell)):
                latex = (maths[0].get("alttext") or "").strip() or None
                mathml = _serialise_math(maths[0]) or None
            header = None
            if tag == "th":
                header = "col" if section == "thead" or "ltx_th_column" in _classes(cell) else "row"
            cells.append(
                TableCell(
                    row=r, col=col, row_span=row_span, col_span=col_span, text=text,
                    latex=latex, mathml=mathml, header=header,
                )
            )  # fmt: skip
            for dr in range(row_span):
                for dc in range(col_span):
                    occupied.add((r + dr, col + dc))
            col += col_span
            n_cols = max(n_cols, col)
    n_rows = max((c.row + c.row_span for c in cells), default=0)
    if not cells or n_rows < 1 or n_cols < 1 or n_rows > 300 or n_cols > 60 or len(cells) > 5000:
        return None
    # holes (ragged rows) become empty cells, so every slot is covered
    covered = {(r, c) for cell in cells for r in range(cell.row, cell.row + cell.row_span)
               for c in range(cell.col, cell.col + cell.col_span)}  # fmt: skip
    for r in range(n_rows):
        for c in range(n_cols):
            if (r, c) not in covered:
                cells.append(TableCell(row=r, col=c, text=""))
    if header_rows >= n_rows:
        header_rows = 0
    grid = TableGrid(
        rows=n_rows, cols=n_cols, cells=cells, header_rows=header_rows, caption=caption,
        is_data=True, source="local",
    )  # fmt: skip
    from readablestem.core.types import grid_problems

    return grid if not grid_problems(grid) else _lenient(grid)


def _lenient(grid: TableGrid) -> TableGrid | None:
    """A grid with structural problems (a header cell below the header rows, say) written with
    no header rows rather than refused: the serialiser copes with the rest."""
    from readablestem.core.types import grid_problems

    plain = grid.model_copy(
        update={
            "header_rows": 0,
            "header_cols": 0,
            "cells": [c.model_copy(update={"header": None}) for c in grid.cells],
        }
    )
    return plain if not grid_problems(plain) else None


def _cell_text(cell: Node):
    if cell.text:
        yield cell.text
    for child in _children(cell):
        if _local(child.tag) == "math":
            yield child.get("alttext") or "".join(child.itertext())
        else:
            yield from _cell_text(child)
        if child.tail:
            yield child.tail


def _text_without_math(cell: Node) -> str:
    parts = [cell.text or ""]
    for child in _children(cell):
        if _local(child.tag) != "math":
            parts.append(_text_without_math(child))
        parts.append(child.tail or "")
    return "".join(parts)


# ---------------------------------------------------------------------------- entry


def graphics_of(xml: bytes) -> dict[str, str]:
    """The image file each `graphics` element of LaTeXML's XML names, by its id (the same id the
    XHTML's `img` has). Anything that is not well formed gives an empty map."""
    try:
        root = SafeET.fromstring(xml, forbid_dtd=True, forbid_entities=True)
    except Exception:
        return {}
    found: dict[str, str] = {}
    for node in root.iter():
        if _local(node.tag) == "graphics":
            ident = node.get("{http://www.w3.org/XML/1998/namespace}id") or ""
            name = node.get("graphic") or ""
            if ident and name:
                found[ident] = name
    return found


def read(xhtml: bytes, project: Path, graphics: dict[str, str] | None = None) -> Reading:
    """The document in LaTeXML's `xhtml` (no DOCTYPE; `ReadError` when it is not usable).
    `graphics`: `graphics_of` the XML of the same run."""
    if len(xhtml) > MAX_XHTML_BYTES:
        raise ReadError("too large")
    try:
        root = SafeET.fromstring(xhtml, forbid_dtd=True, forbid_entities=True)
    except Exception:
        raise ReadError("not well formed") from None
    reader = _Reader(project, graphics)
    reader.out.lang = (
        root.get("lang") or root.get("{http://www.w3.org/XML/1998/namespace}lang") or "en"
    )[:35]
    body = next((c for c in root if _local(c.tag) == "body"), None)
    if body is None:
        raise ReadError("no body")
    reader.walk(body)
    out = reader.out
    out.errors = reader.errors
    if not out.elements:
        raise ReadError("no content")
    if reader.errors:
        out.notices.append(Notice(code=SOURCE_ERROR_NOTICE, count=reader.errors, review="optional"))
    return out


__all__ = ["ReadError", "Reading", "read"]
