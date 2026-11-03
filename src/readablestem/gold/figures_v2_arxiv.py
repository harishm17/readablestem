"""Figure gold v2, arXiv strand: pgfplots charts whose plotted numbers are in the TeX source.

A chart drawn by pgfplots from inline `coordinates {...}` or an inline `table {...}` carries its
own answer key: the numbers the figure plots. This module finds such charts in a paper's TeX,
reads the data into a reference table and a reference long description built from the same
numbers and the axis labels (a template, so nothing in it is a model's reading of the picture),
and writes the standalone TeX that `scripts/figures_v2_build.py` compiles to the image.
It is offline: parsing only, no compiler and no network.

What counts, written before the draw:

- one `tikzpicture` with one `axis` (no group plots), at least one and at most `MAX_SERIES`
  `\\addplot` commands, every one with inline data (no file, no formula);
- `MIN_ROWS` to `MAX_ROWS` points per series, every y value a plain number, x either numbers or
  symbolic names;
- a series count and a point count that fit the 40-row, 8-column table the product asks for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_ROWS = 3
MAX_ROWS = 40
MAX_SERIES = 5

_BLOCK = re.compile(r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}", re.S)
_AXIS_OPEN = re.compile(r"\\begin\{axis\}(\[(?P<opts>.*?)\])?\s*\n", re.S)
_ADDPLOT = re.compile(r"\\addplot\*?(?:\+)?(?P<rest>.*?);", re.S)
_COORDS = re.compile(r"coordinates\s*\{(?P<body>.*?)\}", re.S)
_TABLE = re.compile(r"table\s*(\[(?P<opts>[^\]]*)\])?\s*(?P<arg>\{.*?\}|\S+)", re.S)
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_PAIR = re.compile(rf"\(\s*(?P<x>[^,()]+?)\s*,\s*(?P<y>{_NUM})\s*(?:\[[^\]]*\])?\s*\)")
_FILE_ARG = re.compile(r"\.(dat|csv|txt|tsv|table)\b|\\input|\\pgfplotstable", re.I)
_LEGEND = re.compile(r"\\addlegendentry\{(?P<t>(?:[^{}]|\{[^{}]*\})*)\}")
_LEGEND_LIST = re.compile(r"\\legend\{(?P<t>[^}]*)\}")
_CAPTION = re.compile(r"\\caption\s*(\[[^\]]*\])?\s*\{")
_PREAMBLE_LINE = re.compile(
    r"^\s*\\(definecolor|pgfplotsset|usetikzlibrary|usepgfplotslibrary)\b.*$", re.M
)


def strip_comments(tex: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", tex)


def _braced(text: str, start: int) -> str | None:
    """The text of the balanced `{...}` whose opening brace is at `text[start - 1]`."""
    depth = 1
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i]
    return None


def plain(text: str) -> str:
    """A label as readable text: math and font commands removed, nothing else rewritten.
    Returns "" when LaTeX commands are left (the label would not read as text)."""
    out = re.sub(r"\\(?:mathrm|mathbf|mathit|text|textbf|textit|emph)\s*\{([^{}]*)\}", r"\1", text)
    out = out.replace("$", "").replace("~", " ").replace("\\%", "%").replace("\\,", " ")
    out = re.sub(r"[{}]", "", out)
    out = " ".join(out.split())
    return "" if "\\" in out else out


@dataclass(frozen=True)
class Series:
    name: str
    points: tuple[tuple[str, str], ...]  # (x, y) exactly as the source writes them


@dataclass(frozen=True)
class PlotFigure:
    tex: str  # the tikzpicture
    series: tuple[Series, ...]
    xlabel: str
    ylabel: str
    kind: str  # "bar" or "line"
    caption: str = ""
    preamble: str = field(default="", repr=False)

    def table(self) -> dict[str, object]:
        """The reference table: x first, one column per series when they share x values,
        else `series | x | y` rows."""
        x_name = self.xlabel or "x"
        y_name = self.ylabel or "y"
        xs = [tuple(x for x, _ in s.points) for s in self.series]
        if all(x == xs[0] for x in xs):
            header = [x_name] + [s.name if len(self.series) > 1 else y_name for s in self.series]
            rows = [[x] + [s.points[i][1] for s in self.series] for i, x in enumerate(xs[0])]
        else:
            header = ["series", x_name, y_name]
            rows = [[s.name, x, y] for s in self.series for x, y in s.points]
        return {"header": header, "rows": rows, "source": "source-data"}

    def long_description(self) -> str:
        """A description built only from the source numbers and labels."""
        kind = "bar chart" if self.kind == "bar" else "line or scatter chart"
        names = ", ".join(s.name for s in self.series)
        parts = [
            f"A {kind} with {len(self.series)} series: {names}."
            if len(self.series) > 1
            else f"A {kind} with one series.",
            f"The horizontal axis is {self.xlabel or 'unlabelled'}; "
            f"the vertical axis is {self.ylabel or 'unlabelled'}.",
        ]
        for s in self.series:
            values = "; ".join(f"{x}: {y}" for x, y in s.points)
            parts.append(f"{s.name} ({len(s.points)} points): {values}.")
        return " ".join(parts)

    def standalone(self) -> str:
        """A document that compiles this figure alone."""
        return (
            "\\documentclass[border=6pt]{standalone}\n"
            "\\usepackage{pgfplots,amsmath,amssymb}\n"
            "\\pgfplotsset{compat=1.16}\n"
            "\\usetikzlibrary{patterns,arrows.meta,positioning,calc}\n"
            "\\usepgfplotslibrary{fillbetween}\n"
            f"{self.preamble}\n"
            "\\begin{document}\n"
            f"{self.tex}\n"
            "\\end{document}\n"
        )


def _parse_coordinates(body: str) -> tuple[tuple[str, str], ...] | None:
    pairs = list(_PAIR.finditer(body))
    if "\\" in _PAIR.sub("", body) or _PAIR.sub("", body).strip():
        return None
    points = tuple((m.group("x").strip(), m.group("y")) for m in pairs)
    return points or None


def _parse_inline_table(rest: str) -> tuple[tuple[str, str], ...] | None:
    match = _TABLE.search(rest)
    if match is None:
        return None
    arg = match.group("arg")
    if not arg.startswith("{") or _FILE_ARG.search(arg):
        return None
    lines = [ln.split() for ln in arg.strip("{} \n").splitlines() if ln.strip()]
    if len(lines) < 2 or any(len(ln) < 2 for ln in lines):
        return None
    head, body = lines[0], lines[1:]
    options = match.group("opts") or ""
    xcol = re.search(r"\bx\s*=\s*(\w+)", options)
    ycol = re.search(r"\by\s*=\s*(\w+)", options)
    xi = head.index(xcol.group(1)) if xcol and xcol.group(1) in head else 0
    yi = head.index(ycol.group(1)) if ycol and ycol.group(1) in head else 1
    out = []
    for ln in body:
        if len(ln) <= max(xi, yi) or not re.fullmatch(_NUM, ln[yi]):
            return None
        out.append((ln[xi], ln[yi]))
    return tuple(out)


def parse_block(block: str, preamble: str = "", caption: str = "") -> PlotFigure | None:
    """One `tikzpicture` as a chart with plotted data, or None when it fails a gate."""
    if block.count("\\begin{axis}") != 1 or "groupplot" in block or "\\nextgroupplot" in block:
        return None
    if "\\input" in block or "\\pgfplotstable" in block:
        return None
    opened = _AXIS_OPEN.search(block)
    options = (opened.group("opts") or "") if opened else ""
    if opened is None and "\\begin{axis}" in block:
        options = ""
    plots = list(_ADDPLOT.finditer(block))
    if not 1 <= len(plots) <= MAX_SERIES:
        return None
    if len(re.findall(r"\\addplot\b", block)) != len(plots):
        return None  # an \addplot the pattern could not delimit
    legend = [plain(m.group("t")) for m in _LEGEND.finditer(block)]
    listed = _LEGEND_LIST.search(block)
    if not legend and listed:
        legend = [plain(t) for t in listed.group("t").split(",")]
    series: list[Series] = []
    for index, match in enumerate(plots):
        rest = match.group("rest")
        coords = _COORDS.search(rest)
        points = _parse_coordinates(coords.group("body")) if coords else _parse_inline_table(rest)
        if points is None or not MIN_ROWS <= len(points) <= MAX_ROWS:
            return None
        name = legend[index] if index < len(legend) and legend[index] else f"series {index + 1}"
        series.append(Series(name, points))
    if len({s.name for s in series}) != len(series):
        return None
    xlabel = _option(options + block, "xlabel")
    ylabel = _option(options + block, "ylabel")
    kind = "bar" if re.search(r"\b[xy]bar\b", options) else "line"
    return PlotFigure(
        tex=block,
        series=tuple(series),
        xlabel=plain(xlabel),
        ylabel=plain(ylabel),
        kind=kind,
        caption=plain(caption),
        preamble=preamble,
    )


def _option(text: str, key: str) -> str:
    match = re.search(rf"\b{key}\s*=\s*\{{", text)
    if match is None:
        return ""
    return _braced(text, match.end()) or ""


def preamble_lines(tex: str) -> str:
    """Single-line colour and pgfplots settings of the paper's preamble, which many figures use."""
    head = tex.split("\\begin{document}", 1)[0]
    return "\n".join(m.group(0).strip() for m in _PREAMBLE_LINE.finditer(head))


def _caption_for(tex: str, position: int) -> str:
    """The caption of the `figure` environment that contains `position`, or ""."""
    opening = tex.rfind("\\begin{figure", 0, position)
    closing = tex.rfind("\\end{figure", 0, position)
    if opening == -1 or closing > opening:
        return ""
    match = _CAPTION.search(tex, opening)
    if match is None:
        return ""
    return _braced(tex, match.end()) or ""


def find_plots(tex: str) -> list[PlotFigure]:
    """Every chart of one TeX file that passes the gates, in file order."""
    clean = strip_comments(tex)
    preamble = preamble_lines(clean)
    found = []
    for block in _BLOCK.finditer(clean):
        figure = parse_block(block.group(0), preamble, _caption_for(clean, block.start()))
        if figure is not None:
            found.append(figure)
    return found
