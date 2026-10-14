"""Read a system's output (HTML or Markdown) into the units the rules judge.

One reader for every system, so the rules see a11y-stem's HTML, the chatbot's HTML and a
parser's Markdown the same way. What it hands over (`Output`):

- `blocks`: prose, one per block element, with every maths span cut out (replaced by the
  marker `text.MATH`), headings, table cells and figure descriptions left out of them;
- `headings`: each `h1`-`h6` (or `#` line), with the maths turned into its tokens for
  matching against a line of the PDF, and the plain text for its numbers;
- `equations`: every `<math>` element and every TeX span MathJax would typeset
  (`\\( \\)`, `\\[ \\]`, `$$ $$`, equation environments, and `$ $` when the page's own
  MathJax configuration lists it), in reading order, TeX converted with a11y-stem's own
  clean-up and converter (`equation_step.clean_latex`, `_convert`). A TeX span that does
  not convert keeps `mathml=None` and is counted as unchecked, never as correct;
- `cells`: the text of every table cell, maths as its tokens;
- `descriptions`: each text a reader would hear for a figure: an `<img>` alt text, the
  text inside a `<figure>` that is not its caption, the `<details>` that follows a
  figure, an SVG's title and description, the label of a `role="img"` element. Each
  carries the image when the HTML holds it (a data URI) and the figure's caption.

Text a11y-stem wrote itself (every element with the class `a11y-stem`, see
`assemble.AUTHORED_CLASS`: the about section, notes, labels) is read like any other text
and marked `authored`. A fragment of it inside a block of the document's own text (the
hidden word "Equation" before an equation number) is dropped from the block and counted.
Placeholder alt texts the tool writes for an image it could not convert are not figure
descriptions and are counted apart (`placeholders`).

The Markdown reader is a small one for the output of PDF parsers: ATX and setext
headings, paragraphs, list items, pipe tables and HTML tables, images, and `$`, `$$`,
`\\( \\)`, `\\[ \\]` maths. It does not try to be CommonMark.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import html
import re
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Literal

from readablestem.eval.equations import MathMLError, parse_mathml
from readablestem.eval.invented.text import MATH

AUTHORED_CLASS = "readablestem"
# The class before the rename (October 2026): outputs recorded earlier still carry it.
AUTHORED_CLASSES = (AUTHORED_CLASS, "a11y-stem")
# The note the demo build puts in front of a converted page; not a conversion output.
DEMO_BANNER_LABELS = ("About this demo",)

_SKIP_TEXT = {"script", "style", "noscript", "template", "head", "textarea"}
_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
    "source", "track", "wbr", "mspace", "malignmark", "none", "mprescripts",
}  # fmt: skip
_BLOCKS = {
    "p", "li", "td", "th", "caption", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6", "dt",
    "dd", "summary", "blockquote", "pre", "div", "section", "aside", "figure", "tr", "br",
    "article", "header", "footer", "details", "ol", "ul", "dl", "table", "thead", "tbody",
    "tfoot", "main", "nav", "form", "fieldset", "legend", "hr", "body", "html", "address",
}  # fmt: skip
_NO_TEX = {"pre", "code", "textarea"}
_PLACE_OPEN, _PLACE_CLOSE = chr(0xE001), chr(0xE002)

Display = Literal["block", "inline"]


# ------------------------------------------------------------------ units


@dataclass(frozen=True)
class Block:
    """A run of prose. Maths is cut out (`MATH`)."""

    text: str
    authored: bool = False
    kind: str = "prose"  # "prose", or "caption" (a figure or table caption)
    # The first block of text after a figure: where a chatbot often writes the long
    # description of the figure. Only reported (it is prose to the rules).
    after_figure: bool = False


@dataclass(frozen=True)
class Heading:
    text: str  # maths replaced by its tokens: compared with a line of the PDF
    plain: str  # maths cut out: read for numbers
    level: int
    authored: bool = False


@dataclass(frozen=True)
class Equation:
    index: int
    source: str  # "mathml" or "tex"
    display: Display
    raw: str  # the MathML element or the TeX between its delimiters
    mathml: str | None  # None: TeX that would not convert
    authored: bool = False

    @property
    def tokens(self) -> str:
        return mathml_text(self.mathml) if self.mathml else _crude_tex_text(self.raw)


@dataclass(frozen=True)
class Cell:
    text: str  # maths as its tokens
    table: int
    row: int
    col: int
    authored: bool = False
    in_description: bool = False  # in a long description's data table


@dataclass(frozen=True)
class Description:
    text: str
    source: str  # "alt", "figure", "details", "svg" or "aria"
    figure: int | None = None
    caption: str | None = None
    image: bytes | None = None
    authored: bool = False


@dataclass
class Output:
    format: str
    sha256: str
    blocks: list[Block] = field(default_factory=list)
    headings: list[Heading] = field(default_factory=list)
    equations: list[Equation] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)
    descriptions: list[Description] = field(default_factory=list)
    placeholders: int = 0  # alt texts the tool wrote for an image it did not convert
    tool_fragments: int = 0  # tool-written pieces dropped from blocks of the document's text
    images: int = 0
    figures: int = 0

    def counts(self) -> dict[str, int]:
        return {
            "blocks": len(self.blocks),
            "headings": len(self.headings),
            "equations": len(self.equations),
            "cells": len(self.cells),
            "descriptions": len(self.descriptions),
            "images": self.images,
            "figures": self.figures,
            "placeholders": self.placeholders,
            "tool_fragments": self.tool_fragments,
        }


# ------------------------------------------------------------------ maths


def mathml_text(mathml: str) -> str:
    """The tokens of a MathML element, separated by spaces."""
    try:
        root = parse_mathml(mathml)
    except MathMLError:
        return ""
    return " ".join(" ".join(root.itertext()).split())


def _crude_tex_text(tex: str) -> str:
    """What can be said of TeX that would not convert: its words and numbers."""
    return " ".join(re.sub(r"\\[A-Za-z]+|[{}^_\\]", " ", tex).split())


_MACRO_BLOCK = re.compile(r"macros\s*:\s*\{(.*?)\}\s*[,}]", re.S)
_MACRO_ENTRY = re.compile(
    r"""['"]?([A-Za-z]+)['"]?\s*:\s*(?:\[\s*(['"])(.*?)\2\s*,\s*(\d+)\s*\]|(['"])(.*?)\5)""", re.S
)
_LABELS = re.compile(r"\\(?:tag|label)\*?\{[^{}]*\}|\\(?:nonumber|notag)(?![A-Za-z])")


def parse_macros(source: str) -> dict[str, tuple[str, int]]:
    """TeX macros the page's MathJax configuration defines (tex.macros): name -> (body,
    number of arguments). JavaScript string escapes are undone."""
    macros: dict[str, tuple[str, int]] = {}
    for block in _MACRO_BLOCK.finditer(source):
        for entry in _MACRO_ENTRY.finditer(block.group(1)):
            if entry.group(3) is not None:
                body, count = entry.group(3), int(entry.group(4))
            else:
                body, count = entry.group(6), 0
            macros[entry.group(1)] = (body.replace("\\\\", "\\"), count)
    return macros


def _argument(text: str, start: int) -> tuple[str, int]:
    i = start
    while i < len(text) and text[i].isspace():
        i += 1
    if i < len(text) and text[i] == "{":
        depth = 0
        for j in range(i, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    return text[i + 1 : j], j + 1
        return text[i + 1 :], len(text)
    if i < len(text) and text[i] == "\\":
        found = re.match(r"\\[A-Za-z]+|\\.", text[i:])
        if found:
            return found.group(0), i + len(found.group(0))
    return text[i : i + 1], i + 1


def expand_macros(latex: str, macros: dict[str, tuple[str, int]]) -> str:
    """Substitute the page's own TeX macros (a few rounds, for macros that use macros)."""
    if not macros:
        return latex
    for _ in range(5):
        changed = False
        out: list[str] = []
        i = 0
        while i < len(latex):
            found = re.match(r"\\([A-Za-z]+)", latex[i:])
            if found and found.group(1) in macros:
                body, count = macros[found.group(1)]
                j = i + len(found.group(0))
                for k in range(1, count + 1):
                    arg, j = _argument(latex, j)
                    body = body.replace(f"#{k}", arg)
                out.append(body)
                i = j
                changed = True
            elif found:
                out.append(found.group(0))
                i += len(found.group(0))
            else:
                out.append(latex[i])
                i += 1
        latex = "".join(out)
        if not changed:
            break
    return latex


def tex_to_mathml(tex: str, display: Display, macros: dict[str, tuple[str, int]]) -> str | None:
    """TeX to MathML the way a11y-stem does it for its own model output; None when the
    clean-up or the converter refuses it."""
    from readablestem.pipeline import equation_step

    latex = _LABELS.sub("", expand_macros(tex, macros)).strip()
    try:
        mathml, _checks = equation_step._convert(equation_step.clean_latex(latex), display)
    except Exception:  # model TeX is untrusted input: any failure is "unchecked"
        return None
    return mathml


_TEX_COMMON = (
    r"(?P<dd>\$\$(?P<dd_body>.+?)\$\$)"
    r"|(?P<br>\\\[(?P<br_body>.+?)\\\])"
    r"|(?P<pa>\\\((?P<pa_body>.+?)\\\))"
    r"|(?P<env>\\begin\{(?P<env_name>equation\*?|align\*?|gather\*?|multline\*?|eqnarray\*?)\}"
    r".+?\\end\{(?P=env_name)\})" + "|" + _PLACE_OPEN + r"(?P<ml>\d+)" + _PLACE_CLOSE
)
# Pandoc's rule for `$...$`: the opening $ is followed by a non-space, the closing one is
# preceded by a non-space and not followed by a digit ("$0.50 and $1" is money).
_TEX_DOLLAR = (
    r"|(?P<d1>(?<![\\$])\$(?=\S)(?!\$)(?P<d1_body>(?:[^$\\]|\\.)+?)(?<=\S)(?<!\\)\$(?!\d))"
)
_TEX = re.compile(_TEX_COMMON, re.S)
_TEX_WITH_DOLLARS = re.compile(_TEX_COMMON + _TEX_DOLLAR, re.S)


def mathjax_reads_dollars(source: str) -> bool:
    """Does the page's own MathJax configuration list `$` among its inline delimiters?"""
    found = re.search(r"inlineMath\s*:\s*\[", source)
    if not found:
        return False
    depth, start = 1, found.end()
    for i in range(start, len(source)):
        depth += (source[i] == "[") - (source[i] == "]")
        if depth == 0:
            return bool(re.search(r"""\[\s*['"]\$['"]""", source[start:i]))
    return False


class _Maths:
    """Finds maths in text and registers it as `Equation`s, in reading order."""

    def __init__(self, out: Output, *, dollars: bool, macros: dict[str, tuple[str, int]]) -> None:
        self.out = out
        self.pattern = _TEX_WITH_DOLLARS if dollars else _TEX
        self.macros = macros
        self.mathml: list[str] = []  # raw <math> elements, by placeholder number

    def placeholder(self, raw: str) -> str:
        self.mathml.append(raw)
        return f"{_PLACE_OPEN}{len(self.mathml) - 1}{_PLACE_CLOSE}"

    def scan(self, text: str, *, authored: bool, find: bool = True) -> tuple[str, str]:
        """(text with each maths span cut out as `MATH`, text with each as its tokens).
        With `find` off (inside <pre>) only the <math> placeholders are looked for."""
        plain: list[str] = []
        flat: list[str] = []
        position = 0
        pattern = self.pattern if find else _TEX_ONLY_PLACEHOLDERS
        for found in pattern.finditer(text):
            plain.append(text[position : found.start()])
            flat.append(text[position : found.start()])
            equation = self._equation(found, authored)
            plain.append(MATH)
            flat.append(f" {equation.tokens} ")
            position = found.end()
        plain.append(text[position:])
        flat.append(text[position:])
        return "".join(plain), "".join(flat)

    def _equation(self, found: re.Match[str], authored: bool) -> Equation:
        index = len(self.out.equations)
        if found.group("ml") is not None:
            raw = self.mathml[int(found.group("ml"))]
            shown = re.search(r"""display\s*=\s*["']block["']""", raw[:300])
            display: Display = "block" if shown else "inline"
            try:
                parse_mathml(raw)
                mathml: str | None = raw
            except MathMLError:
                mathml = None
            equation = Equation(index, "mathml", display, raw, mathml, authored)
        else:
            group = next(
                name for name in ("dd", "br", "pa", "env", "d1") if found.groupdict().get(name)
            )
            body = found.group(f"{group}_body") if group != "env" else found.group("env")
            display = "inline" if group in {"pa", "d1"} else "block"
            tex = (body or "").strip()
            equation = Equation(
                index, "tex", display, tex, tex_to_mathml(tex, display, self.macros), authored
            )
        self.out.equations.append(equation)
        return equation


_TEX_ONLY_PLACEHOLDERS = re.compile(_PLACE_OPEN + r"(?P<ml>\d+)" + _PLACE_CLOSE)
_MATH_ELEMENT = re.compile(r"<math\b.*?</math\s*>", re.S | re.I)


# ------------------------------------------------------------------ HTML


def _placeholder_alts() -> list[re.Pattern[str]]:
    """The alt texts a11y-stem writes for an image it did not convert."""
    from readablestem.pipeline import assemble

    fixed = [assemble.EQUATION_NOT_CONVERTED_ALT, assemble.FIGURE_NOT_DESCRIBED_ALT]
    templates = [alt for alt, _note in assemble.KEPT_IMAGE_WORDS.values()]
    patterns = [re.compile(re.escape(alt) + r"\.?") for alt in fixed]
    # An equation kept as an image: its number ("Equation (2.4), not converted"), or the
    # PDF's own text for it with a marker after ("v = d / t, read from the PDF text; not
    # checked"). That text comes from the PDF, not from a model, and is no content of ours.
    patterns.append(re.compile(r"Equation \([^()]{1,50}\), not converted\.?"))
    for suffix in assemble.EQUATION_TEXT_ALT_SUFFIXES:
        patterns.append(
            re.compile(r"(?:Equation \([^()]{1,50}\): )?[^\n]{1,500}?" + re.escape(suffix) + r"\.?")
        )
    for template in templates:
        escaped = re.escape(template).replace(re.escape("{page}"), r"\d+")
        patterns.append(re.compile(escaped))
    return patterns


def _data_image(src: str | None) -> bytes | None:
    if not src or not src.startswith("data:image/"):
        return None
    head, _, payload = src.partition(",")
    if not head.endswith(";base64"):
        return None
    try:
        return base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError):
        return None


@dataclass
class _Frame:
    tag: str
    flags: tuple[str, ...]


@dataclass
class _Fragment:
    text: str
    authored: bool


@dataclass(frozen=True)
class _Context:
    """Where a run of text sat when it began: decides which unit it becomes."""

    heading: int = 0  # level of the innermost open h1-h6, else 0
    cell: bool = False
    figure: int | None = None  # the innermost open <figure>
    figcaption: bool = False
    caption: bool = False
    details: list[str] | None = None  # the text buffer of a figure's <details>
    summary: bool = False
    no_tex: bool = False


class _HtmlReader(HTMLParser):
    def __init__(self, out: Output, maths: _Maths, skip_labels: tuple[str, ...]) -> None:
        super().__init__(convert_charrefs=True)
        self.out = out
        self.maths = maths
        self.skip_labels = skip_labels
        self.placeholder_alts = _placeholder_alts()
        self.stack: list[_Frame] = []
        self.n: Counter[str] = Counter()
        self.fragments: list[_Fragment] = []
        self.context = _Context()
        # headings
        self.heading_levels: list[int] = []
        # figures: id, caption, description pieces, "a details may follow" flag
        self.figure_count = 0
        self.figure_stack: list[int] = []
        self.figure_caption: dict[int, str] = {}
        self.figure_text: dict[int, list[str]] = {}
        self.block_after_figure = False
        self.after_figure: int | None = None
        self.details_stack: list[tuple[int, list[str]]] = []
        # tables
        self.table_count = 0
        self.table_stack: list[list[int]] = []  # [table id, row, col]
        self.cell_text: list[str] | None = None
        self.cell_authored = True
        # svg
        self.svg_parts: list[str] = []
        self.svg_label: str | None = None

    # ---- bookkeeping
    def _push(self, tag: str, flags: tuple[str, ...]) -> None:
        self.stack.append(_Frame(tag, flags))
        for flag in flags:
            self.n[flag] += 1

    def _pop_to(self, tag: str) -> list[_Frame]:
        if not any(frame.tag == tag for frame in self.stack):
            return []
        popped: list[_Frame] = []
        while self.stack:
            frame = self.stack.pop()
            for flag in frame.flags:
                self.n[flag] -= 1
            popped.append(frame)
            if frame.tag == tag:
                break
        return popped

    # ---- text blocks
    def _snapshot(self) -> _Context:
        details = self.details_stack[-1] if self.details_stack else None
        return _Context(
            heading=self.heading_levels[-1] if self.heading_levels else 0,
            cell=bool(self.n["cell"]),
            figure=self.figure_stack[-1] if self.figure_stack else None,
            figcaption=bool(self.n["figcaption"]),
            caption=bool(self.n["caption"]),
            details=details[1] if details is not None and details[0] >= 0 else None,
            summary=bool(self.n["summary"]),
            no_tex=bool(self.n["no_tex"]),
        )

    def _flush(self) -> None:
        fragments, self.fragments = self.fragments, []
        context, self.context = self.context, _Context()
        if not fragments or not "".join(f.text for f in fragments).strip():
            return
        mine = "".join(f.text for f in fragments if not f.authored)
        tool = "".join(f.text for f in fragments if f.authored)
        authored = not mine.strip()
        if authored:
            text = tool
        else:
            text = mine
            if tool.strip():
                self.out.tool_fragments += 1
        plain, flat = self.maths.scan(text, authored=authored, find=not context.no_tex)
        plain = " ".join(plain.split())
        flat = " ".join(flat.split())
        if not (plain.strip() or flat):
            return
        if context.heading:
            self.out.headings.append(
                Heading(flat, plain.replace(MATH, " "), context.heading, authored)
            )
        elif context.cell:
            if self.cell_text is not None:
                self.cell_text.append(flat)
                self.cell_authored = self.cell_authored and authored
        elif context.figure is not None and not context.figcaption:
            self.figure_text.setdefault(context.figure, []).append(flat)
        elif context.details is not None and not context.summary:
            context.details.append(flat)
        else:
            kind = "caption" if context.figcaption or context.caption else "prose"
            if context.figcaption and context.figure is not None:
                self.figure_caption[context.figure] = (
                    self.figure_caption.get(context.figure, "") + " " + flat
                ).strip()
            self.out.blocks.append(
                Block(plain, authored, kind, after_figure=self.block_after_figure)
            )
        self.block_after_figure = False

    # ---- parser callbacks
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {key: value or "" for key, value in attrs}
        classes = a.get("class", "").split()
        authored = any(c in classes for c in AUTHORED_CLASSES)
        if tag in _BLOCKS:
            self._flush()
        if self.after_figure is not None and tag != "details":
            self.after_figure = None
        if tag == "img":
            self._image(a)
        if a.get("role") == "img" and a.get("aria-label", "").strip() and tag not in {"img", "svg"}:
            self.out.descriptions.append(
                Description(
                    " ".join(a["aria-label"].split()),
                    "aria",
                    self.figure_stack[-1] if self.figure_stack else None,
                    authored=bool(self.n["authored"]) or authored,
                )
            )
        if tag in _VOID:
            return
        flags: list[str] = []
        if tag in _SKIP_TEXT or (
            tag in {"aside", "section", "div"} and a.get("aria-label") in self.skip_labels
        ):
            flags.append("skip")
        if authored:
            flags.append("authored")
        if tag in {"pre", "code", "textarea"}:
            flags.append("no_tex")
        if tag == "svg":
            flags.append("svg")
            self.svg_parts = []
            self.svg_label = a.get("aria-label", "").strip() or None
        if tag in {"title", "desc"} and self.n["svg"]:
            flags.append("svg_text")
        if tag in {"td", "th"}:
            flags.append("cell")
        if tag == "figcaption":
            flags.append("figcaption")
        if tag == "caption":
            flags.append("caption")
        if tag == "summary":
            flags.append("summary")
        self._push(tag, tuple(flags))
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.heading_levels.append(int(tag[1]))
        elif tag == "figure":
            self.figure_count += 1
            self.figure_stack.append(self.figure_count)
        elif tag == "details" and self.after_figure is not None:
            self.details_stack.append((self.after_figure, []))
            self.after_figure = None
        elif tag == "details":
            self.details_stack.append((-1, []))
        elif tag == "table":
            self.table_count += 1
            self.table_stack.append([self.table_count, -1, 0])
        elif tag == "tr" and self.table_stack:
            self.table_stack[-1][1] += 1
            self.table_stack[-1][2] = 0
        elif tag in {"td", "th"}:
            self.cell_text = []
            self.cell_authored = True
        if tag == "math":  # a <math> the pre-pass did not catch (unbalanced): ignore its text
            self.n["skip"] += 1
            self.stack[-1].flags += ("skip",)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCKS:
            self._flush()
        if tag in _VOID:
            return
        popped = self._pop_to(tag)
        if not popped:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self.heading_levels:
            self.heading_levels.pop()
        elif tag == "figure" and self.figure_stack:
            figure = self.figure_stack.pop()
            parts = [part for part in self.figure_text.pop(figure, []) if part]
            if parts:
                self.out.descriptions.append(
                    Description(
                        " ".join(parts),
                        "figure",
                        figure,
                        self.figure_caption.get(figure),
                        authored=bool(self.n["authored"]),
                    )
                )
            self.out.figures += 1
            self.after_figure = figure
            self.block_after_figure = True
        elif tag == "details" and self.details_stack:
            figure, parts = self.details_stack.pop()
            text = " ".join(part for part in parts if part)
            if figure >= 0 and text:
                self.out.descriptions.append(
                    Description(
                        text,
                        "details",
                        figure,
                        self.figure_caption.get(figure),
                        authored=bool(self.n["authored"]),
                    )
                )
        elif tag == "table" and self.table_stack:
            self.table_stack.pop()
        elif tag in {"td", "th"} and self.table_stack and self.cell_text is not None:
            table, row, col = self.table_stack[-1]
            text = " ".join(part for part in self.cell_text if part)
            if text.strip():
                in_description = bool(self.details_stack) and self.details_stack[-1][0] >= 0
                self.out.cells.append(
                    Cell(text, table, row, col, self.cell_authored, in_description)
                )
            self.table_stack[-1][2] += 1
            self.cell_text = None
        elif tag == "svg":
            text = " ".join(part for part in [self.svg_label, *self.svg_parts] if part)
            if text.strip():
                self.out.descriptions.append(
                    Description(
                        " ".join(text.split()),
                        "svg",
                        self.figure_stack[-1] if self.figure_stack else None,
                        authored=bool(self.n["authored"]),
                    )
                )
            self.svg_parts = []
            self.svg_label = None

    def handle_data(self, data: str) -> None:
        if self.n["svg_text"]:
            self.svg_parts.append(data.strip())
            return
        if self.n["skip"] or self.n["svg"]:
            return
        if data.strip():
            self.after_figure = None
        if not self.fragments:
            self.context = self._snapshot()
        self.fragments.append(_Fragment(data, bool(self.n["authored"])))

    def close(self) -> None:
        super().close()
        self._flush()

    # ---- images
    def _image(self, a: dict[str, str]) -> None:
        self.out.images += 1
        alt = " ".join(a.get("alt", "").split())
        if not alt:
            return
        if any(pattern.fullmatch(alt) for pattern in self.placeholder_alts):
            self.out.placeholders += 1
            return
        self.out.descriptions.append(
            Description(
                alt,
                "alt",
                self.figure_stack[-1] if self.figure_stack else None,
                image=_data_image(a.get("src")),
                authored=bool(self.n["authored"])
                or any(c in a.get("class", "").split() for c in AUTHORED_CLASSES),
            )
        )


def read_html(source: str, *, skip_labels: tuple[str, ...] = DEMO_BANNER_LABELS) -> Output:
    """Read an HTML document (a11y-stem's, a chatbot's) into `Output`."""
    out = Output("html", hashlib.sha256(source.encode("utf-8")).hexdigest())
    maths = _Maths(out, dollars=mathjax_reads_dollars(source), macros=parse_macros(source))
    prepared = _MATH_ELEMENT.sub(lambda found: maths.placeholder(found.group(0)), source)
    reader = _HtmlReader(out, maths, skip_labels)
    reader.feed(prepared)
    reader.close()
    _attach_captions(out, reader.figure_caption)
    return out


def _attach_captions(out: Output, captions: dict[int, str]) -> None:
    """Descriptions read before their figure's caption (an <img> comes first) get it."""
    out.descriptions[:] = [
        d if d.caption or d.figure is None else _with_caption(d, captions.get(d.figure))
        for d in out.descriptions
    ]


def _with_caption(description: Description, caption: str | None) -> Description:
    return Description(
        description.text,
        description.source,
        description.figure,
        caption,
        description.image,
        description.authored,
    )


# ------------------------------------------------------------------ Markdown

_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_SETEXT = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_MD_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_MD_FENCE = re.compile(r"^\s*(```|~~~)")
_MD_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_HTML_TABLE = re.compile(r"<table\b.*?</table\s*>", re.S | re.I)
_TABLE_TOKEN = chr(0xE003) + "{}" + chr(0xE004)
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_EMPHASIS = re.compile(r"(\*\*|__|\*|`)")


def _md_inline(text: str) -> str:
    text = _MD_LINK.sub(r"\1", text)
    text = _HTML_TAG.sub(" ", text)
    text = _EMPHASIS.sub("", text)
    return html.unescape(text)


def read_markdown(source: str) -> Output:
    """Read parser Markdown into `Output` (see the module docstring for what it knows)."""
    out = Output("markdown", hashlib.sha256(source.encode("utf-8")).hexdigest())
    maths = _Maths(out, dollars=True, macros={})
    tables: list[str] = []

    def stash(found: re.Match[str]) -> str:
        tables.append(found.group(0))
        return f"\n\n{_TABLE_TOKEN.format(len(tables) - 1)}\n\n"

    text = _HTML_TABLE.sub(stash, source.replace("\r\n", "\n"))
    # Maths first: a display span may run over several lines and hold blank-looking lines.
    text = _MATH_ELEMENT.sub(lambda found: maths.placeholder(found.group(0)), text)
    in_fence = False
    paragraph: list[str] = []

    def end_paragraph() -> None:
        if paragraph:
            _md_block(out, maths, " ".join(paragraph))
            paragraph.clear()

    lines = text.split("\n")
    for number, line in enumerate(lines):
        if _MD_FENCE.match(line):
            end_paragraph()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line.strip():
            end_paragraph()
            continue
        token = re.fullmatch(r"\s*\ue003(\d+)\ue004\s*", line)
        if token:
            end_paragraph()
            _md_html_table(out, maths, tables[int(token.group(1))])
            continue
        heading = _MD_HEADING.match(line)
        if heading:
            end_paragraph()
            _md_heading(out, maths, len(heading.group(1)), heading.group(2))
            continue
        setext = _MD_SETEXT.match(line)
        if setext and paragraph and number > 0 and lines[number - 1].strip():
            level = 1 if setext.group(1).startswith("=") else 2
            title = " ".join(paragraph)
            paragraph.clear()
            _md_heading(out, maths, level, title)
            continue
        if _MD_RULE.match(line):
            end_paragraph()
            continue
        if line.lstrip().startswith("|"):
            end_paragraph()
            if not _MD_TABLE_SEPARATOR.match(line):
                _md_pipe_row(out, maths, line, out.cells[-1].table if out.cells else 0, number)
            continue
        if _MD_ITEM.match(line):
            end_paragraph()
            line = _MD_ITEM.sub("", line, count=1)
        paragraph.append(line.strip().lstrip(">").strip())
    end_paragraph()
    return out


def _md_images(out: Output, text: str) -> str:
    for found in _MD_IMAGE.finditer(text):
        out.images += 1
        alt = " ".join(html.unescape(found.group(1)).split())
        if alt:
            out.descriptions.append(Description(alt, "alt"))
    text = _MD_IMAGE.sub(" ", text)
    for found in re.finditer(r"<img\b[^>]*>", text, re.I):
        out.images += 1
        alt = re.search(r"""alt\s*=\s*(?:"([^"]*)"|'([^']*)')""", found.group(0), re.I)
        value = " ".join(html.unescape((alt.group(1) or alt.group(2)) if alt else "").split())
        if value:
            out.descriptions.append(Description(value, "alt"))
    return re.sub(r"<img\b[^>]*>", " ", text, flags=re.I)


def _md_block(out: Output, maths: _Maths, text: str) -> None:
    text = _md_images(out, text)
    plain, _flat = maths.scan(_md_inline(text), authored=False)
    plain = " ".join(plain.split())
    if plain.replace(MATH, "").strip():
        out.blocks.append(Block(plain))


def _md_heading(out: Output, maths: _Maths, level: int, text: str) -> None:
    plain, flat = maths.scan(_md_inline(_md_images(out, text)), authored=False)
    plain, flat = " ".join(plain.split()), " ".join(flat.split())
    if flat:
        out.headings.append(Heading(flat, plain.replace(MATH, " "), level))


def _md_pipe_row(out: Output, maths: _Maths, line: str, table: int, row: int) -> None:
    cells = line.strip().strip("|").split("|")
    for col, cell in enumerate(cells):
        _plain, flat = maths.scan(_md_inline(cell), authored=False)
        flat = " ".join(flat.split())
        if flat:
            out.cells.append(Cell(flat, table, row, col))


def _md_html_table(out: Output, maths: _Maths, fragment: str) -> None:
    """An HTML table inside Markdown: read with the HTML reader, then merged in place."""
    inner = Output("html", "")
    inner_maths = _Maths(inner, dollars=True, macros={})
    prepared = _MATH_ELEMENT.sub(lambda found: inner_maths.placeholder(found.group(0)), fragment)
    reader = _HtmlReader(inner, inner_maths, ())
    reader.feed(prepared)
    reader.close()
    base_table = max((c.table for c in out.cells), default=0)
    base_equation = len(out.equations)
    for equation in inner.equations:
        out.equations.append(
            Equation(
                base_equation + equation.index,
                equation.source,
                equation.display,
                equation.raw,
                equation.mathml,
                equation.authored,
            )
        )
    for cell in inner.cells:
        out.cells.append(
            Cell(cell.text, base_table + cell.table, cell.row, cell.col, cell.authored)
        )
    out.descriptions += inner.descriptions
    out.images += inner.images


# ------------------------------------------------------------------ entry point


def read_output(source: str, fmt: str | None = None, *, name: str = "") -> Output:
    """Read `source` as `fmt` ("html" or "markdown"); guessed from `name` or the text."""
    if fmt is None:
        lowered = name.lower()
        if lowered.endswith((".md", ".markdown")):
            fmt = "markdown"
        elif lowered.endswith((".html", ".htm")):
            fmt = "html"
        else:
            fmt = (
                "html"
                if re.search(r"<(?:!doctype|html|body|p|div|h[1-6])\b", source, re.I)
                else "markdown"
            )
    if fmt in {"md", "markdown"}:
        return read_markdown(source)
    if fmt == "html":
        return read_html(source)
    raise ValueError(f"unknown output format {fmt!r} (expected html or markdown)")
