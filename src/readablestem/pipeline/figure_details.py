"""Long descriptions and chart data tables of complex figures (owned by the figures work).

With long descriptions on (`ConversionOptions.long_descriptions`), the figure step asks
for more than short alt text (prompt `figure_long.md`): a long description for charts,
diagrams and complex illustrations, the values of a chart as a data table, and whether
the figure tells things apart by colour alone. This module holds the deterministic parts
that do not depend on the model call, so the step, the assembler and the review screen
share them:

- `clean_long_description` / `long_description_ok`: what is stored is what was checked
  (format characters removed, spaces collapsed, one paragraph per line), and a long
  description that is markup, a URL, a placeholder, too short or too long is dropped
  rather than shown (the figure then has a notice, `long_description_missing`, never a
  decision of its own);
- `data_grid`: the model's header and rows -> a `TableGrid` (source "model"; one header
  row with units, the first column as row headers), or None when the answer is not a
  usable table of a chart's values (ragged rows, too big, no number in it);
- `values_in_text`: True only when every number in the table appears in the PDF's own
  text inside the figure region (`Element.text` of a figure: the labels layout found in
  and beside it). Minus signs, thousands separators and trailing zeros are normalised
  the same way on both sides. A raster chart has no such text, so its values are always
  "read from the image";
- `data_table_shown` / `data_table_offered`: a table whose values all come from the
  PDF's text is written; one read from the image is an optional extra, kept on the fix
  and offered on the review screen, and written only once a reviewer adds it
  (`FigureFix.data_table_added`). Additive extras never add a required decision
  (docs/design/content-features.md "Review tiers");
- `render_details`: the `<details>` written after a figure in the output (the long
  description, then the data table, when shown, through `table_html.render_table`).

Everything here is pure and bounded, and never reads `ConversionOptions`: rendering
depends on the stored fix only (docs/design/content-features.md rule 3).
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from readablestem.core.types import FigureFix, TableCell, TableGrid
from readablestem.pipeline.safe_html import escape_text
from readablestem.pipeline.table_html import render_table
from readablestem.pipeline.validate import check_alt, clean_alt

# Bounds of a stored long description. The prompt asks for 100-1200 characters in at
# most 8 lines; outside these the text is dropped (it is not a usable description).
LONG_MIN_CHARS = 40
LONG_MAX_CHARS = 2000
LONG_MAX_LINES = 12
# Alt-text checks that also disqualify a long description (length is judged above, a
# leading "Image of" or a caption copy only waste words).
_LONG_ERROR_CODES = frozenset({"empty", "filename", "url_or_markup", "placeholder", "bidi_control"})

# A chart's data table: small enough to read, and far inside `TableGrid`'s own limits.
MAX_DATA_ROWS = 60  # body rows
MAX_DATA_COLS = 12
MAX_CELL_CHARS = 120

# Thin space, narrow no-break space and no-break space.
_GROUP_SPACES = "".join(map(chr, (0x2009, 0x202F, 0x00A0)))
# Minus sign, en dash, figure dash, small and fullwidth hyphen-minus.
_MINUS_SIGNS = "".join(map(chr, (0x2212, 0x2013, 0x2012, 0xFE63, 0xFF0D)))
_EN_DASH = chr(0x2013)
# A number as printed: an optional sign (only where it cannot be a dash between two
# numbers or words: "1-3" is 1 and 3), digits with optional thousands groups ("1,000",
# "1 000" with a thin or no-break space), and an optional decimal part (".5" too). ASCII
# digits only: subscripts never count, and a run of superscripts is read as a number of
# its own first (`_SUPERSCRIPT_RUN`), so 10²⁴ is 10 and 24 and its exponent is
# compared too (6.42 x 10²³ is not printed where the text says 10²⁴).
_NUMBER = re.compile(
    r"(?<![0-9A-Za-z.])(?P<sign>-)?"
    rf"(?P<int>[0-9]{{1,3}}(?:[,{_GROUP_SPACES}][0-9]{{3}})+(?![0-9])|[0-9]+)?"
    r"(?:\.(?P<frac>[0-9]+))?"
)
# Superscript digits and signs: a run becomes " ^" and its plain form ("10^24", as a model
# types an exponent), so it is read as a number apart from its base.
_SUPERSCRIPT_RUN = re.compile("[\u00b2\u00b3\u00b9\u2070\u2074-\u207b]+")
# Minus signs, as PDFs and models write them, become "-" before numbers are read.
_MINUS = str.maketrans(dict.fromkeys(_MINUS_SIGNS, "-"))
_GROUP_SEPARATORS = str.maketrans("", "", "," + _GROUP_SPACES)
# "Figure 2.3", "Fig. 4", "FIGURE 1-2" at the start of a caption.
_FIGURE_LABEL = re.compile(
    rf"^\s*(?:Figure|FIGURE|Fig\.|FIG\.)\s*(\d+[A-Za-z]?(?:[.\-{_EN_DASH}]\d+[A-Za-z]?)*)"
)


# --------------------------------------------------------------------------- text


def clean_long_description(text: str) -> str:
    """One paragraph per line, each as `validate.clean_alt` stores alt text (invisible
    format characters and bidi controls removed, spaces collapsed); blank lines dropped."""
    lines = (clean_alt(line) for line in text.replace("\r\n", "\n").split("\n"))
    return "\n".join(line for line in lines if line)


def long_description_ok(text: str) -> bool:
    """A cleaned long description worth showing: within the bounds above, and passing
    the alt-text checks that say it is a description at all (no markup, URL, file name
    or placeholder)."""
    lines = text.split("\n") if text else []
    if not LONG_MIN_CHARS <= len(text) <= LONG_MAX_CHARS or len(lines) > LONG_MAX_LINES:
        return False
    failed = {c.code for c in check_alt(" ".join(lines), decorative=False) if not c.passed}
    return not failed & _LONG_ERROR_CODES


def paragraphs(text: str | None) -> list[str]:
    return [line for line in (text or "").split("\n") if line.strip()]


# --------------------------------------------------------------------------- numbers


def numbers_in(text: str | None) -> list[Decimal]:
    """Every number printed in `text`, normalised ("−0.50" and "-0.5" are equal, "1,000"
    is 1000). Read the same way from a table cell and from the PDF's text."""
    found: list[Decimal] = []
    plain = _SUPERSCRIPT_RUN.sub(lambda m: " ^" + unicodedata.normalize("NFKC", m[0]), text or "")
    for match in _NUMBER.finditer(plain.translate(_MINUS)):
        whole, frac = match["int"], match["frac"]
        if whole is None and frac is None:
            continue
        literal = f"{match['sign'] or ''}{(whole or '0').translate(_GROUP_SEPARATORS)}"
        if frac is not None:
            literal += f".{frac}"
        try:
            value = Decimal(literal)
        except InvalidOperation:  # pragma: no cover - the pattern only matches numbers
            continue
        found.append(value.normalize() if value else Decimal(0))
    return found


def table_numbers(grid: TableGrid) -> list[Decimal]:
    return [n for cell in grid.cells for n in numbers_in(cell.text)]


def values_in_text(grid: TableGrid, region_text: str | None) -> bool:
    """True when the table has numbers and every one of them appears in `region_text`
    (the PDF's text inside the figure region)."""
    wanted = table_numbers(grid)
    printed = set(numbers_in(region_text))
    return bool(wanted) and all(n in printed for n in wanted)


# --------------------------------------------------------------------------- tables


def data_grid(header: list[str], rows: list[list[str]]) -> TableGrid | None:
    """The model's table as a `TableGrid` (source "model"): the header row as column
    headers, the first column (the horizontal axis or the category) as row headers when
    there is more than one column. None when it is not a usable table of a chart's
    values: no rows, rows of different lengths, too big, a cell too long, or no number
    in the body at all."""
    cols = len(header)
    if not 1 <= cols <= MAX_DATA_COLS or not 1 <= len(rows) <= MAX_DATA_ROWS:
        return None
    if any(len(row) != cols for row in rows):
        return None
    texts = [[clean_alt(text) for text in row] for row in [header, *rows]]
    if any(len(text) > MAX_CELL_CHARS for row in texts for text in row):
        return None
    if not any(numbers_in(text) for row in texts[1:] for text in row):
        return None
    cells = []
    for r, row in enumerate(texts):
        for c, text in enumerate(row):
            if r == 0:
                kind = "col"
            elif c == 0 and cols > 1:
                kind = "row"
            else:
                kind = None
            cells.append(TableCell(row=r, col=c, text=text, header=kind))
    return TableGrid(
        rows=len(texts),
        cols=cols,
        cells=cells,
        header_rows=1,
        header_cols=1 if cols > 1 else 0,
        source="model",
    )


# --------------------------------------------------------------------------- output


def figure_label(caption: str | None) -> str | None:
    """ "Figure 2.3" from a caption that starts with a figure number, else None."""
    match = _FIGURE_LABEL.match(caption or "")
    return f"Figure {match.group(1)}" if match else None


# An unnumbered figure is named by the start of its alt text, at most this many words.
NAME_WORDS = 8


def figure_name(figure: FigureFix, caption: str | None) -> str:
    """Which figure: "Figure 2.3" from its caption; for an unnumbered one the start of
    its alt text ("the figure \u201cLine graph of the position of a car\u2026\u201d"), so
    two unnumbered figures' summaries differ in a screen reader's list of controls
    (WCAG 2.4.6); "the figure" without either."""
    label = figure_label(caption)
    if label:
        return label
    words = (figure.alt or "").split()
    if not words:
        return "the figure"
    start = " ".join(words[:NAME_WORDS]).rstrip(".,;:")
    more = "\u2026" if len(words) > NAME_WORDS else ""
    return f"the figure \u201c{start}{more}\u201d"


def data_table_shown(figure: FigureFix | None) -> bool:
    """The figure's data table goes into the student file: every value is in the PDF's
    text, or a reviewer added a table read from the image."""
    return (
        figure is not None
        and figure.data_table is not None
        and (figure.data_values_from_text is True or figure.data_table_added)
    )


def data_table_offered(figure: FigureFix | None) -> bool:
    """A data table read from the image that nobody has added: an optional extra the
    review screen offers ("Add the data table"), never written as it is."""
    return (
        figure is not None
        and not figure.decorative
        and figure.data_table is not None
        and not data_table_shown(figure)
    )


def summary_text(figure: FigureFix, caption: str | None) -> str:
    """The `<summary>` of the details after a figure: says what is inside and which
    figure it belongs to ("Description of Figure 2.3")."""
    name = figure_name(figure, caption)
    return f"Description of {name}" if paragraphs(figure.long_description) else f"Data for {name}"


def table_caption(figure: FigureFix, caption: str | None) -> str:
    """The data table's caption: says when the values were read from the image."""
    name = figure_name(figure, caption)
    if figure.data_values_from_text:
        return f"Data shown in {name}"
    return f"Data read from {name}; the values are approximate"


def has_details(figure: FigureFix | None) -> bool:
    """A non-decorative figure with a long description or a data table to show."""
    return (
        figure is not None
        and not figure.decorative
        and (bool(paragraphs(figure.long_description)) or data_table_shown(figure))
    )


# Added to the stylesheet of documents with a figure description (and only those). No
# new colours: the summary is the body text, bold, with the browser's own marker.
DETAILS_CSS = """\
.figure-description { margin: -0.75em 0 1.5em; }
.figure-description > summary { cursor: pointer; font-weight: 700; }
.figure-description[open] > summary { margin-bottom: 0.5em; }
"""


def render_details(
    figure: FigureFix, caption: str | None, *, table_id: str | None, authored_class: str
) -> str:
    """The `<details>` written right after a figure: the long description as paragraphs,
    then the data table when it is shown (`data_table_shown`) and `table_id`, a unique
    id prefix for it, is given, in a box that scrolls sideways and takes focus, named by
    its caption. Everything the model wrote is escaped; the summary and the table
    caption are the tool's own words."""
    parts = [
        f'<details class="figure-description">'
        f'<summary class="{authored_class}">{escape_text(summary_text(figure, caption))}</summary>'
    ]
    parts += [f"<p>{escape_text(p)}</p>" for p in paragraphs(figure.long_description)]
    grid = figure.data_table if data_table_shown(figure) else None
    if grid is not None and table_id is not None:
        grid = grid.model_copy(update={"caption": table_caption(figure, caption)})
        table = render_table(grid, id_prefix=table_id)
        parts.append(
            f'<div class="table-scroll" role="group" aria-labelledby="{table_id}-caption" '
            f'tabindex="0">{table}</div>'
        )
    parts.append("</details>")
    return "".join(parts)
