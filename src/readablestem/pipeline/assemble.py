"""Elements + fixes -> one self-contained accessible HTML file.

What the output looks like and why is in docs/output.md. In short:

- One HTML5 document: `<html lang>`, `<meta charset>`, a viewport, `<title>`, a small
  inline stylesheet and one `<main>`. No scripts and no external resources, so the file
  works offline, inside an LMS page, and saved to disk.
- Content in reading order (`Element.order`): headings from heading fixes, paragraphs as
  `<p>` with inline equations placed at their markers, consecutive list items as nested
  `<ul>`/`<ol>`, block equations as `<math display="block">` (with their equation number
  after them), figures as `<figure><img alt>` + `<figcaption>`.
- Anything not converted (ABSTAINED, FAILED, REJECTED, or MathML the sanitiser refused)
  keeps the original crop as an image with a fixed alt text and a visible note, marked
  with a border *and* a text label, never by colour alone.
- A short note for students at the end of `<main>` ("About this version", a labeled
  `<section>`; inside `<main>` so every reader of the page, pandoc among them, gets it from
  the same markup): what the file is, the statement of what was reviewed and converted
  (`pipeline.checked`: who reviewed how many of the parts the tool flagged and on which day,
  how many parts are pictures, which pages were left out), and to tell the instructor about
  anything hard to read. When nobody reviewed it, or a part is a picture or left out, the
  same statement opens `<main>` too, with a link to each place (`_checked_notice`). An
  optional `notice` at the top (e.g. "offline test run": placeholders, not conversions).

Security: all document and model text is
escaped with `html.escape`. MathML is never pasted as a string: `sanitize_mathml` parses
it with defusedxml and re-serialises an allowlisted tree of presentation elements and
attributes (both live in `safe_html`, shared with `table_html`). Text this module writes
itself carries the `AUTHORED_CLASS` class so the wording check in `html_checks` can tell
it apart from the instructor's text.

Extension points: TABLE elements are written by
`assemble_tables.render_table_element`, and inline text (with its markers, and later its
links, colours and language) by `inline_text.render_inline_text`, so features that change
those land in their own modules. CSS a feature needs is added only to documents that use
it (`_Renderer.add_style`), so documents without those features are written exactly as
before.
"""

from __future__ import annotations

import base64
import math
import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from itertools import pairwise
from typing import Literal
from urllib.parse import quote
from xml.etree.ElementTree import Element as XmlElement
from xml.etree.ElementTree import ParseError

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException

from readablestem import __version__
from readablestem.config import get_settings
from readablestem.core import inline
from readablestem.core.types import (
    HEADING_REPEATS_ABOVE,
    IMAGE_PAGE_OMISSION_REASON,
    IMAGE_PAGE_ORIGINAL_REASON,
    IMAGE_PAGE_REASON,
    KEPT_ORIGINAL_STATUSES,
    Element,
    Fix,
    Kind,
    Link,
    Status,
    TableGrid,
)
from readablestem.core.types import INLINE_MARKER_RE as INLINE_MARKER_RE
from readablestem.pipeline import (
    assemble_tables,
    checked,
    checked_words,
    decoration,
    equation_scale,
    figure_details,
    figure_images,
    inline_text,
    navigation,
    output_chars,
    student_sheet,
    table_html,
    table_math,
)
from readablestem.pipeline.safe_html import MATHML_ATTRIBUTES as MATHML_ATTRIBUTES
from readablestem.pipeline.safe_html import MATHML_ELEMENTS as MATHML_ELEMENTS
from readablestem.pipeline.safe_html import MATHML_NS as MATHML_NS
from readablestem.pipeline.safe_html import MATHML_UNWRAP as MATHML_UNWRAP
from readablestem.pipeline.safe_html import MAX_MATHML_DEPTH as MAX_MATHML_DEPTH
from readablestem.pipeline.safe_html import MAX_MATHML_NODES as MAX_MATHML_NODES
from readablestem.pipeline.safe_html import Display, alttext_from_speech, sanitize_mathml
from readablestem.pipeline.safe_html import escape_text as _escape
from readablestem.pipeline.safe_html import split_tag as _split_tag

# Class on every element whose text this module wrote (notes, labels, the about section).
AUTHORED_CLASS = table_html.AUTHORED_CLASS

# The version of the student file's making: raise it with any change to the sheet, the
# markup, the student words or the speech rules. The page carries it in its head
# (`RENDER_META`), and the web app makes a stored page again from its stored result when
# the page has none or an older one (`web.routes.jobs.current_html`), so a fix reaches
# documents converted before it, not only new ones and ones somebody edits again.
# 4: the statement of what was reviewed and converted (SW7). 5: the note "About this version"
# inside `<main>` (WS22.1); 5 on its own branch too: one H1, the title not said twice (SW6c), and
# empty alt text only for decoration with evidence (WS6.4). 6: both together. 7: nothing is cut
# on paper, long descriptions print open, every code block that can scroll is a tab stop, the
# maths font stack (SW45, WS21.2, WS21.4) and the words of a part kept as a picture (WS21.9).
# 8: authored notices carry an audience (SW16 part 4): the student file has no sentence
# written to the person who checks it ("Check it against the original.", "not checked",
# "Needs a description.", "Check it before students use it."), and the image-page note and the
# equation alt text are said to a student; a draft is made in the checker's words.
# 9: a paragraph with a role is written in `<aside aria-label="Sidebar">` or `<blockquote>`
# (WS16.3); a paragraph left out is counted in the note; a part that was not converted is
# said once (the visible label is hidden from a screen reader when the alt text says it); an
# equation of a Word or LaTeX file shown as its LaTeX is not called a picture, and its link
# names no page; the speech rules for scripts and accents changed.
# 10: a table that goes on from the page before is written with that part's header rows; the
# finish sentence's date has its year; a sidebar's name; every code block that can scroll and
# every wide table is a tab stop, a script inside a script keeps a readable size, and a very wide
# equation is not cut on paper; the words for a Word file's equations; the speech rules for
# bars and for raised and lowered text.
RENDER_VERSION = 10
RENDER_META = "readablestem-render"
_RENDER_LINE = re.compile(
    r'<meta name="' + re.escape(RENDER_META) + r'" content="(\d{1,9})">', re.ASCII
)


def render_version(html: str) -> int:
    """The render version written in a stored page's head; 0 for a page made before pages
    carried one."""
    head = html[: html.find("</head>")] if "</head>" in html else html[:4096]
    match = _RENDER_LINE.search(head)
    return int(match.group(1)) if match else 0


@dataclass(frozen=True)
class Stamp:
    """Which version of a document a file is (WS20.1): the id the web app shows, the first six
    characters of the job id and the count of changes that reached its output ("b9487b-12"),
    and the day of the last one. The assembler takes it as a parameter and reads no clock; a
    caller with no job (the CLI) passes none and the file says nothing of a version."""

    version_id: str
    day: date

    @classmethod
    def of(cls, job_id: str, version: int, changed: datetime) -> Stamp:
        """The stamp of job `job_id` at output version `version`, last changed at `changed`
        (the day is the UTC day)."""
        when = changed.astimezone(UTC) if changed.tzinfo else changed
        return cls(f"{job_id[:6]}-{version}", when.date())

    @property
    def date_text(self) -> str:
        """The day written out in full, with no time ("November 10, 2026")."""
        return checked_words.long_date(self.day)

    @property
    def line(self) -> str:
        return f"Version {self.version_id}, {self.date_text}"


VERSION_META = "readablestem-version"
MODIFIED_META = "dcterms.modified"


# --------------------------------------------------------------------------- audiences
#
# Who an authored notice is written to. The student file (what a student gets, and every file
# made from it: Word, EPUB, the Canvas zip, the cartridge, the Listen transcript and speech)
# holds only `STUDENT` words: what is missing and whom to ask. A sentence written to the person
# who checks the file ("Check it against the original.") is a `CHECKER` word: it is written
# only into the draft and the owner's preview of one (`render_html(audience=CHECKER)`), and
# never into a stored page. Every authored notice is built through `say`, so a notice has one
# place that says who hears it.
STUDENT = "student"
CHECKER = "checker"


@dataclass(frozen=True)
class Words:
    """An authored notice in the words of each audience: the student's, and, where the
    checker is told something more or something else, the checker's."""

    student: str
    checker: str | None = None


def say(words: Words, audience: str = STUDENT, **values: object) -> str:
    """`words` for `audience` (the checker's when it has them, else the student's), with
    `values` put in its `{placeholders}`."""
    text = words.checker if audience == CHECKER and words.checker is not None else words.student
    return text.format(**values) if values else text


DEFAULT_TITLE = "Converted document"
EQUATION_NOT_CONVERTED_ALT = "Equation not converted"
# The alt text of an equation kept as an image, when its region has text in the PDF: that
# text, said to be what the PDF holds and not checked ("v = d / t, read from the PDF
# text; not checked"). Never a guess: it is the text layer or nothing, and nothing falls
# back to the equation's number ("Equation (2.4), not converted") or the fixed words.
# A student hears where the words are from ("as written in the PDF"); the checker is told
# they were not compared with the page ("; not checked").
EQUATION_TEXT_ALT_SUFFIX = ", as written in the PDF"
EQUATION_TEXT_ALT_CHECK_SUFFIX = ", read from the PDF text; not checked"
# ... and when the region has fractions, roots, exponents or several lines, whose order and
# size the flat text layer loses ("v2 = v20 + 2ad" for v squared = v zero squared + 2ad), the
# warning comes FIRST, so a listener who stops early has not heard a wrong formula as the
# equation: "Equation (2.2), not converted. The characters the PDF has for it, with fractions
# and exponents run together: d = 12gt2". The equation's own not-converted words, then these.
EQUATION_FLAT_ALT_TAIL = Words(
    ". The characters the PDF has for it, with fractions and exponents run together: ",
    ". The characters the PDF has for it, with fractions and exponents run together, and not "
    "compared with the page: ",
)
# Every suffix a page can carry (the student's and the checker's): what marks the tool's own
# words in an alt text (`_FIXED_ALT_IMG`) and what the evaluations strip to get the PDF's text.
EQUATION_TEXT_ALT_SUFFIXES = (EQUATION_TEXT_ALT_SUFFIX, EQUATION_TEXT_ALT_CHECK_SUFFIX)
_NOT_CONVERTED_LEAD = r"Equation(?: \({char}{{1,50}}\),)? not converted"


def flat_alt_lead(char: str = ".") -> str:
    """The pattern of the warning that opens a flattened equation's alt text (both
    audiences); `char` is what the equation's number may be made of. The words have no
    character that the page writes as an entity, so one pattern reads the alt text as the
    page has it and as a parser gives it."""
    tails = (EQUATION_FLAT_ALT_TAIL.student, EQUATION_FLAT_ALT_TAIL.checker or "")
    said = "|".join(re.escape(tail) for tail in tails)
    return _NOT_CONVERTED_LEAD.format(char=char) + f"(?:{said})"


_FLAT_ALT_LEAD = re.compile(flat_alt_lead())


def equation_alt_text(alt: str) -> str:
    """The PDF's own text of an equation kept as a picture: its alt text without the words
    the tool put before it (the warning of a flattened equation) or after it (where the
    text is from); "" for any other picture."""
    lead = _FLAT_ALT_LEAD.match(alt)
    if lead:
        return alt[lead.end() :]
    for mark in EQUATION_TEXT_ALT_SUFFIXES:
        if alt.endswith(mark):
            return alt[: -len(mark)]
    return ""


# Layout's reasons that say the text layer of an equation is not in reading order.
_FLATTENED_REASONS = ("superscript", "fraction bar", "radical sign", "lines merged")
EQUATION_ALT_MAX_CHARS = 200  # a longer text layer is not alt text: the fixed words instead
# A figure with no description, never empty alt text: only decoration with evidence has that
# (`pipeline.decoration`, WS6.4).
FIGURE_NOT_DESCRIBED_ALT = "Figure not described"
# The class of what is written as decoration; the page checks allow empty alt text only
# inside it (`html_checks`, "html.img_alt_content").
DECORATIVE_CLASS = "decorative"
# The student note (`_about_section`). It speaks to students: what this file is and who
# to tell about problems. What was reviewed and converted is the statement of
# `pipeline.checked_words` (SW7): a person "reviewed", never "checked", which can read as
# a certificate.
ABOUT_TITLE = "About this version"
# The first sentence names what the file is made from (`checked_words.source_noun`, the one
# noun every sentence about the job's source uses) and what the file itself is (`ABOUT_MEDIA`;
# each export sets its own, `with_medium`).
ABOUT_MEDIA = {
    "web": "a web",
    "EPUB": "an EPUB",
    "Word": "a Word",
    "Canvas": "a Canvas",
    "Common Cartridge": "a Common Cartridge",
}


def about_opening(origin: str = "pdf", medium: str = "web") -> str:
    """ "This is a web version of a PDF from your course, made with ReadableSTEM, ..." for the
    source `origin` names (`DocumentResult.origin`) and the file's `medium` (`ABOUT_MEDIA`)."""
    noun = checked_words.source_noun(origin)
    return (
        f"This is {ABOUT_MEDIA[medium]} version of a {noun} from your course, made with "
        "ReadableSTEM, a tool that assists with accessibility."
    )


ABOUT_WORDING = about_opening()


def with_medium(html: str, medium: str) -> str:
    """The finished page with the note's first sentence saying what this file is ("an EPUB
    version"), for the exports made from the page, which read the page and not the result. The
    page itself says "web version"; a page with no such note is returned as it is."""
    at = html.find(f'id="{ABOUT_ID}"')
    if at < 0:
        return html
    kinds = "|".join(re.escape(m) for m in ABOUT_MEDIA.values())
    note = re.sub(
        f"This is (?:{kinds}) version of a ",
        f"This is {ABOUT_MEDIA[medium]} version of a ",
        html[at:],
        count=1,
    )
    return html[:at] + note


CONTACT = "If something is hard to read or looks wrong, tell your instructor."
# In a draft, where the student's file links to the instructor's contact line (`instructor_link`).
DRAFT_CONTACT_NOTE = "In the student's file, a link here goes to the document's contact line."
# A draft (`mark_draft`): a file that left the tool with decisions still open says so before
# anything else, and its head says so to a program (SW21). An honesty claim: one string.
DRAFT_STATE_META = '<meta name="readablestem-state" content="draft">'
DRAFT_CLASS = "draft-mark"
# The flag notes speak to students, as the about section does (UX review MF7): what is
# missing, and whom to ask. Never reviewer wording ("review", "publishing"). A box says only
# what this part is (F-114); why a screen reader cannot read a picture, and what to do about
# it, is said once, in the notice at the top (`_checked_notice`, `NOT_CONVERTED_WHY`).
EQUATION_NOTE = "Shown as an image of the original."
# The same for a formula of a LaTeX source, shown as it was written (no picture).
EQUATION_WRITTEN_NOTE = "Shown as it was written in LaTeX."
# A formula of a Word file has no LaTeX in the file: the tool made it from Word's equation.
EQUATION_MADE_NOTE = "Shown as LaTeX that this tool made from the Word equation."
FIGURE_NOTE = "This figure has no text description yet."
# Said once, in the notice at the top, when any part is not converted: the explanation that
# each box used to repeat. What to do ("ask your instructor for a version you can read") is
# in the statement of the same notice.
NOT_CONVERTED_WHY = Words(
    "A screen reader cannot read what a picture shows, so it says only that each of these "
    "parts is there; for an equation it also reads the characters the PDF holds, and says "
    "where they are from. Each one is marked where it stands, with what it is."
)
NOT_CONVERTED_WHY_WRITTEN = Words(
    "A screen reader may not read these parts well. Each one is marked where it stands, with "
    "what it is."
)
# Pages and pictures kept as images (pipeline.page_type: there is no handwriting or scan
# conversion): by the fix's reason, ONE table of the picture's alt text ("{page}" is the page
# number), the label of its note and the short reason students see at the box. Rendered from the
# stored reason, so a stored result renders the same. A reason that names what the picture is (a
# handwritten page, a scanned page) is a page-level finding; `handwriting_on_page` is also what a
# box of vector lines got named before the reader had pen evidence (WS6.10, WS3.5), so it says
# only that the part is a picture and never names handwriting (WS21.9): its label says all, and
# its reason is empty. A drawing has no reason of its own yet (P1 and P3 record it); it is
# added to this table then.
KEPT_IMAGE_LABEL = "Not converted."
KEPT_PICTURE_LABEL = "Shown as a picture, not converted to text."
KEPT_IMAGE_WORDS = {
    "handwritten_page": (
        "Page {page}: handwritten notes, not converted to text.",
        KEPT_IMAGE_LABEL,
        "A page of handwritten notes.",
    ),
    "scanned_page": (
        "Page {page}: a scanned page, not converted to text.",
        KEPT_IMAGE_LABEL,
        "A scanned page.",
    ),
    "unread_page": (
        "Page {page}: not converted to text.",
        KEPT_IMAGE_LABEL,
        "A page shown as a picture.",
    ),
    "page_read_failed": (
        "Page {page}: this page could not be read.",
        KEPT_IMAGE_LABEL,
        "This page could not be read.",
    ),
    "handwriting_on_page": (
        "Part of page {page}, shown as a picture, not converted to text.",
        KEPT_PICTURE_LABEL,
        "",
    ),
    # A block of a page converted from an image that the instructor chose to keep as the
    # picture (`_kept_prose`): not a page, and not a whole-page reason either.
    "image_page_text": (
        "Text on page {page}, not converted to text.",
        KEPT_IMAGE_LABEL,
        "Text on the page, shown as a picture.",
    ),
}
# A page kept as a picture because something painted on it was read into nothing (an
# equation drawn with lines, F-451), when text was read from it too: its text has holes
# ("Newton's second law of motion is ."), so a note opens that text and links to the
# picture after it, and no reader meets a sentence with a gap unwarned (`_Renderer.lead`).
UNREAD_PARTS_CLASS = "unread-parts-note"
UNREAD_PARTS_NOTE = "Parts of this page could not be read and are missing from the text below."
UNREAD_PARTS_LINK = "The page is shown as a picture after it."
# Writing on a page converted from an image that the reading left out (`image_page_omission`).
OMITTED_NOTE = "Writing on the page that is not in the text."
# A page converted from an image of it (ADR 0007): the note at its start and the toggle that
# shows the original. The notice is the author's wording; it assists, it does not say the
# conversion is right.
# A student is told the page was read from a picture and that small errors are possible; the
# checker is told to check it, with the label that goes with it.
IMAGE_PAGE_NOTE = Words(
    "This page was read from a picture of the original, so small errors are possible.",
    "Check it against the original.",
)
IMAGE_PAGE_LABEL = Words("Note:", "Converted from an image of the page.")
IMAGE_PAGE_SHOW = Words("Show the original page {page}")
IMAGE_PAGE_ORIGINAL_ALT = Words(
    "Original of page {page}.", "Original of page {page}, shown for checking the converted text."
)
# The label of a figure's note: to the student what is missing, to the checker what to do.
FIGURE_LABEL = Words("No description.", "Needs a description.")
# The about section names the pages kept whole as images.
KEPT_PAGE_REASONS = frozenset(
    {"handwritten_page", "scanned_page", "unread_page", "page_read_failed"}
)

# Fix statuses whose proposal is not used: the original stays, flagged.
NOT_CONVERTED = KEPT_ORIGINAL_STATUSES

# Stands for a footnote's backlink until the body is written (`_Renderer.footnote`): a
# control character, which escaped document text never contains.
_BACKLINK_SLOT = "\x00"
_BACKLINK_RE = re.compile(_BACKLINK_SLOT + "([^\x00]*)" + _BACKLINK_SLOT)

ABOUT_ID = "about-conversion"
ABOUT_TITLE_ID = "about-conversion-title"
# The statement at the top of the page (`_checked_notice`), and the sentence of the note at
# the end that names the pages left out, which it links to.
NOTICE_ID = "checked-notice"
NOTICE_TITLE_ID = "checked-notice-title"
LEFT_OUT_ID = "about-left-out"
# The statement in the head, for the files made from the page (`_summary_meta`).
SUMMARY_META = "readablestem-summary"
SUFFICIENT_META = "readablestem-access-sufficient"


# BCP 47-ish: "en", "en-US", "zh-Hant-TW". Anything else falls back to "en".
_LANG = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*")
_REASON = re.compile(r"[a-z0-9_]{1,64}")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# An ordered list marker as layout reports it: "3.", "b)", "(iv)".
_ORDERED_MARKER = re.compile(
    r"(?:\((?P<inner>[0-9A-Za-z]{1,6})\)|(?P<plain>[0-9A-Za-z]{1,6})(?P<sep>[.)]))"
)
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
_PT_TO_CSS_PX = 96 / 72  # a PDF point is 1/72 in; a CSS pixel is 1/96 in


# --------------------------------------------------------------------------- width

# Rough advance widths in em, for deciding which display equations may be wider than a
# phone screen (see `WIDE_EQUATION_EM`). Erring wide is cheap: one more tab stop.
_RELATIONS = frozenset(
    "=<>\u2264\u2265\u2260\u2248\u2261\u2245\u223c\u221d"
    "\u2192\u2190\u2194\u21d2\u21d0\u21d4\u2208\u2209\u2282\u2286\u2283\u2287"
)
_BINARY = frozenset("+-\u2212\u00d7\u00b7\u00f7\u00b1\u2213\u2218\u2217\u222a\u2229\u2227\u2228")
_SCRIPT_SCALE = 0.71  # scriptlevel 1 is about 71% of the base size


def _token_em(name: str, text: str) -> float:
    chars = len(text.strip())
    if name == "mo":
        stripped = text.strip()
        if stripped in _RELATIONS:
            return 0.6 + 0.56  # a thick space either side
        if stripped in _BINARY:
            return 0.6 + 0.44  # a medium space either side
        return 0.45 * chars + (0.17 if stripped in {",", ";"} else 0.0)
    if name == "ms":
        chars += 2  # the quotes
    return 0.6 * chars + 0.25 * text.strip().count(" ")


def _width_em(node: XmlElement) -> float:
    _, name = _split_tag(node.tag) if isinstance(node.tag, str) else ("", "")
    children = list(node)
    if name in {"mi", "mn", "mo", "mtext", "ms"}:
        return _token_em(name, "".join(node.itertext()))
    if name == "mspace":
        width = node.attrib.get("width", "")
        return float(width[:-2]) if re.fullmatch(r"\d+(\.\d+)?em", width) else 0.0
    widths = [_width_em(child) for child in children]
    if not widths:
        return 0.0
    if name == "mfrac":
        return max(widths) + 0.2
    if name in {"msub", "msup"}:
        return widths[0] + _SCRIPT_SCALE * sum(widths[1:])
    if name == "msubsup":
        return widths[0] + _SCRIPT_SCALE * max(widths[1:], default=0.0)
    if name in {"munder", "mover", "munderover"}:
        return max([widths[0], *(_SCRIPT_SCALE * w for w in widths[1:])])
    if name == "msqrt":
        return sum(widths) + 1.0
    if name == "mroot":
        return widths[0] + _SCRIPT_SCALE * sum(widths[1:]) + 1.0
    if name == "menclose":
        return sum(widths) + 0.5
    if name == "mmultiscripts":
        return widths[0] + _SCRIPT_SCALE * sum(widths[1:]) / 2
    if name == "mtable":
        columns: list[float] = []  # widest cell per column
        for row in children:
            for i, cell in enumerate(row):
                if i == len(columns):
                    columns.append(0.0)
                columns[i] = max(columns[i], _width_em(cell))
        return sum(columns) + 0.8 * max(0, len(columns) - 1)
    return sum(widths)  # rows, styles and anything else laid out in a line


def estimate_width_em(mathml: str) -> float:
    """Approximate rendered width of (sanitised) MathML in em of its own font size."""
    try:
        root = SafeET.fromstring(mathml, forbid_dtd=True)
    except (ParseError, DefusedXmlException, ValueError):
        return 0.0
    try:
        return _width_em(root)
    except RecursionError:  # the sanitiser caps depth at 64; this is belt and braces
        return float("inf")


# --------------------------------------------------------------------------- helpers


def _clean_lang(lang: str) -> str:
    lang = (lang or "").strip()
    return lang if _LANG.fullmatch(lang) else "en"


def _clean_title(title: str | None) -> str:
    text = INLINE_MARKER_RE.sub("[equation]", title or "")
    text = " ".join(text.split())
    return text or DEFAULT_TITLE


# A section number in front of a title ("1 Derivatives", "2.1 Speed"). One or two digits per
# part, so a year ("2024 Annual report") stays, and no words ("Week 1: Vectors" is a title). T1-7.
_SECTION_NUMBER = re.compile(
    r"^\d{1,2}(?:\.\d{1,2}){0,3}[.):]?\s+(?=\S)",
)
# Titles that name a part of a document, not the document.
GENERIC_TITLES = frozenset(
    {"contents", "table of contents", "outline", "index", "overview", "agenda", "notes"}
)


def strip_section_number(text: str) -> str:
    """ "1 Derivatives" -> "Derivatives"; the text as it is when it has no section number
    or stripping would leave nothing."""
    stripped = _SECTION_NUMBER.sub("", text.strip(), count=1).strip()
    return stripped or text.strip()


# Where a document's title came from, in the order they are tried (SW14).
TITLE_YOURS, TITLE_PDF, TITLE_HEADING, TITLE_FILE, TITLE_STOCK = (
    "yours",
    "pdf",
    "heading",
    "file",
    "stock",
)
# A file extension: a dot and up to five letters or digits at the end of the name.
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}\Z")
_NAME_SEPARATORS = re.compile(r"[_\-\s]+")


@dataclass(frozen=True)
class DocumentTitle:
    """The title of a document and where it came from (`TITLE_*`)."""

    text: str
    source: str


def file_title(filename: str) -> str:
    """A file name as a title: no extension, `_` and `-` read as spaces ("week_3-notes.pdf"
    is "week 3 notes"). "" when nothing is left."""
    return _NAME_SEPARATORS.sub(" ", _EXTENSION.sub("", filename.strip())).strip()


def looks_like_file_name(text: str) -> bool:
    """A "title" that is a file name written out: no spaces, and an underscore or a mix of
    letters and digits ("MIT6_0001F16_Welcome", "lec03"). A single plain word is a title."""
    return (
        bool(text)
        and not any(ch.isspace() for ch in text)
        and ("_" in text or (any(ch.isdigit() for ch in text) and any(ch.isalpha() for ch in text)))
    )


def _usable_title(candidate: str | None) -> str:
    """The candidate as a title, or "" when it is empty, the stock title or only names a part
    of a document ("Contents")."""
    if not candidate or not candidate.strip():
        return ""
    text = strip_section_number(_clean_title(candidate))
    if text == DEFAULT_TITLE or text.casefold().rstrip(".:") in GENERIC_TITLES:
        return ""
    return text


def document_title(
    override: str | None,
    raw: str | None,
    headings: Sequence[str] = (),
    filename: str = "",
) -> DocumentTitle:
    """The one title of a document and its source (SW14), the same everywhere the document
    is named (the student `<title>`, the Word and EPUB title, the Canvas and Cartridge names,
    the list, the download name): the title the instructor typed; the document's own title
    (without its section number) when it is not generic and not a file name written out; the
    first heading that is not generic; the file name without its extension and separators;
    `DEFAULT_TITLE` only when there is nothing else."""
    if override and override.strip():
        return DocumentTitle(_clean_title(override), TITLE_YOURS)
    own = _usable_title(raw)
    if own and not looks_like_file_name(own):
        return DocumentTitle(own, TITLE_PDF)
    for heading in headings:
        if text := _usable_title(heading):
            return DocumentTitle(text, TITLE_HEADING)
    if name := file_title(filename):
        return DocumentTitle(name, TITLE_FILE)
    return DocumentTitle(own or DEFAULT_TITLE, TITLE_PDF if own else TITLE_STOCK)


def suggested_title(raw: str | None, headings: Sequence[str] = ()) -> str:
    """The title students hear when the instructor has not chosen one and the file has no
    name to fall back on (`document_title`)."""
    return document_title(None, raw, headings).text


def _is_png(data: bytes | None) -> bool:
    return data is not None and data.startswith(_PNG_SIGNATURE)


def _png_data_uri(png: bytes | None) -> str | None:
    if png is None or not _is_png(png):
        return None
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _unit_scale(_element: Element) -> float:
    return 1.0


def _size_attrs(element: Element) -> str:
    """Display the crop at its printed size (a crop is rendered at ~200 dpi)."""
    x0, y0, x1, y1 = element.bbox
    width, height = round((x1 - x0) * _PT_TO_CSS_PX), round((y1 - y0) * _PT_TO_CSS_PX)
    if 0 < width <= 4000 and 0 < height <= 4000:
        return f' width="{width}" height="{height}"'
    return ""


_LABEL_PREFIX = re.compile(r"^(?:equation|eqn?)\.?\s*", re.IGNORECASE)
_LABEL_BRACKETS = "()[] \u00a0"


def equation_number(label: str | None) -> str | None:
    """ "2.4" from "(2.4)", "[2.4]", "Eq. 2.4" or "2.4"; None when there is no number."""
    if not label:
        return None
    number = _LABEL_PREFIX.sub("", " ".join(label.split())).strip(_LABEL_BRACKETS)
    return number or None


def equation_alt(element: Element, audience: str = STUDENT) -> str:
    """The alt text of an equation that is shown as an image (not converted).

    Text the PDF holds for the equation's region (`Element.text`), marked as read from the
    PDF and not checked, so a student hears what the PDF says and how much to trust it;
    without usable text, "Equation (2.4), not converted" (the equation's number) or
    "Equation not converted". The text is whitespace-normalised and nothing else: nothing
    is written that the PDF does not hold. Text that is empty, has no letter or digit, holds
    a character that is not text (a symbol font's glyph with no letter, `output_chars`), or
    is longer than EQUATION_ALT_MAX_CHARS is not used. When layout saw fractions, roots,
    exponents or several lines in the region, the alt text says first that the equation is
    not converted and that the characters that follow run them together
    (EQUATION_FLAT_ALT_TAIL), and only then gives them. The checker's words also say the
    characters were not compared with the page; the student's say only where they are from
    (`audience`).
    """
    number = equation_number(element.equation_label)
    named = f"Equation ({number})" if number else "Equation"
    unconverted = f"{named}, not converted" if number else EQUATION_NOT_CONVERTED_ALT
    text = " ".join(_ALT_CONTROL.sub(" ", element.text or "").split())
    usable = any(c.isalnum() for c in text) and not output_chars.count(text)
    if text and len(text) <= EQUATION_ALT_MAX_CHARS and usable:
        if any(key in reason for reason in element.reasons for key in _FLATTENED_REASONS):
            return f"{unconverted}{say(EQUATION_FLAT_ALT_TAIL, audience)}{text}"
        lead = f"{named}: " if number else ""
        suffix = EQUATION_TEXT_ALT_CHECK_SUFFIX if audience == CHECKER else EQUATION_TEXT_ALT_SUFFIX
        return f"{lead}{text}{suffix}"
    return unconverted


# Control and invisible characters a text layer can carry (they would end up in an attribute
# read by a screen reader): replaced by a space before the text is used.
_ALT_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")


def _equation_label(label: str | None) -> str:
    """The equation's number ("(2.4)", "2.4"), written after its maths. Screen readers
    hear "Equation 2.4" (the word is visually hidden); sighted readers see the number
    as printed, at the right of the equation."""
    if not label or not label.strip():
        return ""
    return (
        '<span class="equation-label">'
        f'<span class="visually-hidden {AUTHORED_CLASS}">Equation </span>'
        f"{_escape(' '.join(label.split()))}</span>"
    )


# A display equation estimated at least this wide (em of its own font) may not fit a
# 320 CSS px screen: the column is then 288 px and the maths 19.8 px, so 14.5 em fit.
# The estimate came within -18% / +44% of Chromium's widths for the gold equations, and
# maths fonts differ between systems, hence the margin.
WIDE_EQUATION_EM = 11.0


def _scroller_attrs(mathml: str, label: str | None) -> str:
    """Attributes that make a wide equation's box a named tab stop ("" if not wide).

    `.equation` (display) and `.equation-scroll` (inline) scroll sideways when the
    maths is wider than the screen (WCAG 1.4.10: the equation scrolls, not the page).
    Keyboard users can only scroll a box that can take focus (axe
    `scrollable-region-focusable`), but a tab stop on every equation would make a long
    document tedious to tab through, so only equations that may be too wide for a
    phone get one, named "Equation" (plus its number) for the focus.
    """
    if estimate_width_em(mathml) < WIDE_EQUATION_EM:
        return ""
    name = f"Equation {' '.join(label.split())}" if label and label.strip() else "Equation"
    return f' tabindex="0" role="group" aria-label="{_escape(name)}"'


# A kept picture that is its box's own child keeps its size under 40 em and the box scrolls
# sideways (`student_sheet.NARROW`). One narrower than this fits the narrowest column (320 px
# less the page's and the box's padding), so it needs no tab stop, as a short equation has none.
WIDE_PICTURE_PX = 240
_IMG_WIDTH = re.compile(r'\swidth="(\d+)"')


def _picture_scroller_attrs(body: str) -> str:
    """The tab stop and name of a kept picture's box when its picture can be wider than the
    box ("" otherwise). A keyboard user can only scroll a box that takes focus (axe
    `scrollable-region-focusable`), as for equations (`_scroller_attrs`)."""
    if not body.startswith("<img"):
        return ""  # a figure's picture shrinks with the column, and text wraps
    width = _IMG_WIDTH.search(body[: body.find(">") + 1])
    if width and int(width.group(1)) < WIDE_PICTURE_PX:
        return ""
    return ' tabindex="0" role="group" aria-label="Original picture"'


# An equation estimated at least this wide (em of the maths font) may not fit a sheet: at
# Letter and 150% the text column is about 22 em of it. On paper it is scaled to fit the
# column down to 70% of its size; wider than that it keeps 70% and the page prints a note
# (`student_sheet.PRINT`, SW45 rule 6). The estimate is not calibrated (A23); it errs wide.
PRINT_FIT_EM = 14.0


def print_width_bucket(mathml: str) -> int | None:
    """The estimated width of an equation, rounded up to a step (4 em, then 16 em), when it
    may not fit a sheet; None when it does. Few steps, so the stylesheet gets few rules."""
    width = min(estimate_width_em(mathml), 512.0)
    if width < PRINT_FIT_EM:
        return None
    step = 4 if width <= 48 else 16
    return math.ceil(width / step) * step


def _fix_attrs(fix: Fix | None, reason: str | None) -> str:
    """Content-free status/reason codes, for tooling that reads the output."""
    attrs = f' data-status="{fix.status.value}"' if fix is not None else ""
    if reason and _REASON.fullmatch(reason):
        attrs += f' data-reason="{reason}"'
    return attrs


# --------------------------------------------------------------------------- lists


def _roman(text: str) -> int | None:
    """Value of a canonical roman numeral below 400 ("iv" -> 4), else None. (Layout has the
    same reading; it is repeated here so assembly does not import the PDF code.)"""
    lower = text.lower()
    if not lower or any(c not in _ROMAN for c in lower) or not (text.islower() or text.isupper()):
        return None
    values = [_ROMAN[c] for c in lower]
    total = sum(
        -v if i + 1 < len(values) and values[i + 1] > v else v for i, v in enumerate(values)
    )
    canonical, rest = "", total
    for number, letters in ((100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"),
                            (5, "v"), (4, "iv"), (1, "i")):  # fmt: skip
        while rest >= number:
            canonical, rest = canonical + letters, rest - number
    return total if 0 < total < 400 and canonical == lower else None


@dataclass(frozen=True)
class _ListStyle:
    """How one run of sibling list items is written."""

    tag: Literal["ul", "ol"]
    family: tuple[str, ...]  # siblings of one family share a list
    type: str | None = None  # <ol type>: "a", "A", "i" or "I" ("1" is the default)
    start: int | None = None  # <ol start> when the first value is not 1
    printed: bool = False  # irregular markers: list-style none, the marker printed


def _marker_parts(marker: str) -> tuple[str, str] | None:
    """("{}." | "{})" | "({})", body) of an ordered marker, or None."""
    match = _ORDERED_MARKER.fullmatch(marker.strip())
    if not match:
        return None
    if match["inner"] is not None:
        return "({})", match["inner"]
    return "{}" + match["sep"], match["plain"]


def _family(item: Element) -> tuple[str, ...]:
    """Items that can share one list: bullets with bullets; ordered items with the same
    kind of marker (numbers or letters) printed the same way ("a." and "b." but not "(c)")."""
    parts = _marker_parts(item.list_marker or "") if item.list_ordered else None
    if parts is None:
        return ("bullet",) if not item.list_ordered else ("other",)
    form, body = parts
    return ("decimal" if body.isdigit() else "letter", form)


def _sequence(bodies: list[str]) -> tuple[str | None, int] | None:
    """(<ol type>, first value) when the bodies count up by one, else None.

    Single letters that count up are letters ("a", "b", "c"; "h", "i", "j"; a lone "c"),
    otherwise roman numerals that count up ("i", "ii", "iii"; "iv", "v").
    """
    if all(b.isdigit() for b in bodies):
        values = [int(b) for b in bodies]
        return (None, values[0]) if _counts_up(values) else None
    if not (all(b.islower() for b in bodies) or all(b.isupper() for b in bodies)):
        return None
    lower = bodies[0].islower()
    if all(len(b) == 1 and b.isascii() and b.isalpha() for b in bodies):
        letters = [ord(b.lower()) - ord("a") + 1 for b in bodies]
        if _counts_up(letters):
            return ("a" if lower else "A"), letters[0]
    romans = [_roman(b) for b in bodies]
    numbers = [r for r in romans if r is not None]
    if len(numbers) == len(bodies) and _counts_up(numbers):
        return ("i" if lower else "I"), numbers[0]
    return None


def _counts_up(values: list[int]) -> bool:
    return all(b == a + 1 for a, b in pairwise(values))


def _list_style(items: list[Element]) -> _ListStyle:
    family = _family(items[0])
    if family == ("bullet",):
        return _ListStyle("ul", family)
    parts = [_marker_parts(item.list_marker or "") for item in items]
    bodies = [p[1] for p in parts if p is not None]
    sequence = _sequence(bodies) if len(bodies) == len(items) else None
    if sequence is None or family[1] != "{}.":
        return _ListStyle("ol", family, printed=True)  # "(a)", "3)", or a broken sequence
    kind, first = sequence
    return _ListStyle("ol", family, type=kind, start=first if first != 1 else None)


def _list_levels(items: list[Element]) -> list[int]:
    """Levels relative to the first item, never more than one deeper than the item before
    (a deeper item needs a parent item to nest in)."""
    base = items[0].list_level or 1
    levels: list[int] = []
    for item in items:
        level = max(1, (item.list_level or 1) - base + 1)
        levels.append(min(level, levels[-1] + 1) if levels else 1)
    return levels


# --------------------------------------------------------------------------- headings


ASIDE_LABEL = "Sidebar"
# How a paragraph with a role is wrapped (`Element.role`): an aside is a landmark with a label
# (a screen reader says what it is), a quote a `<blockquote>`.
_ROLE_WRAP: dict[str, tuple[str, str]] = {
    "aside": (f'<aside aria-label="{ASIDE_LABEL}">', "</aside>"),
    "quote": ("<blockquote>", "</blockquote>"),
}


def _one_wrapper_each(chunks: list[str]) -> list[str]:
    """Paragraphs of one role that follow each other with nothing written between them (no
    page marker) go in one wrapper: one sidebar, one quotation."""
    out: list[str] = []
    for chunk in chunks:
        for opening, closing in _ROLE_WRAP.values():
            if (
                out
                and out[-1].startswith(opening)
                and out[-1].endswith(closing)
                and chunk.startswith(opening)
                and chunk.endswith(closing)
            ):
                out[-1] = out[-1][: -len(closing)] + chunk[len(opening) :]
                break
        else:
            out.append(chunk)
    return out


def _name_sidebars(chunks: list[str]) -> list[str]:
    """Sidebars are told apart by a screen reader's list of regions: when a page has more than
    one, each is "Sidebar 2 of 3" and not the same "Sidebar". A sidebar holds only paragraphs
    (only a paragraph takes the role), so there is no heading of its own to name it by."""
    opening = _ROLE_WRAP["aside"][0]
    total = sum(chunk.startswith(opening) for chunk in chunks)
    if total < 2:
        return chunks
    out: list[str] = []
    seen = 0
    for chunk in chunks:
        if chunk.startswith(opening):
            seen += 1
            named = f'<aside aria-label="{ASIDE_LABEL} {seen} of {total}">'
            chunk = named + chunk[len(opening) :]
        out.append(chunk)
    return out


def is_entry(element: Element) -> bool:
    """A glossary entry that is written as a term and its definition."""
    text = element.text or ""
    return (
        element.kind is Kind.PARAGRAPH
        and element.term_end is not None
        and 0 < element.term_end < len(text)
        and bool(text[: element.term_end].strip())
        and bool(text[element.term_end :].strip())
    )


def heading_parts(element: Element, fix: Fix | None) -> tuple[int | None, str]:
    """(level, text) for a heading element; level None means it is written as <p>
    (no fix, rejected or not converted, or no text)."""
    if fix is None or fix.heading is None or fix.status in NOT_CONVERTED:
        return None, element.text or ""
    text = fix.heading.text if fix.heading.text.strip() else element.text or ""
    return (fix.heading.level if text.strip() else None), text


@dataclass
class Outline:
    """The outline repair: the level each heading is written at; never skips down.

    A rejected or failed heading (written as a paragraph) can open a gap: h1, [h2],
    h3. Clamping to "previous heading + 1" closes it but turns siblings into parent
    and child (h1, [h2], h3 a, h3 b -> h1, h2 a, h3 b). So headings still open are
    kept on a stack: a heading first closes those at its own level or deeper, then
    goes at most one level below the one left open. Siblings get the same level, and
    the children of a rejected heading all move up together. The first heading keeps
    its level.

    The review screen uses `would_write` to refuse a level this repair would change.
    """

    # Open headings as (level from the fix, level written), outermost first. Both
    # increase strictly from the bottom of the stack to the top.
    open: list[tuple[int, int]] = field(default_factory=list)

    def would_write(self, level: int) -> int:
        """The level a heading at `level` would be written at next (no change)."""
        parents = [entry for entry in self.open if entry[0] < level]
        return min(level, parents[-1][1] + 1) if parents else level

    def write(self, level: int) -> int:
        """Write the next heading: its level in the output."""
        written = self.would_write(level)
        while self.open and self.open[-1][0] >= level:
            self.open.pop()
        self.open.append((level, written))
        return written


def outline_before(elements: list[Element], fixes: list[Fix], element_id: str) -> Outline:
    """The outline as `render_html` has it just before the element `element_id` (reading
    order; the first fix for an id wins, as in `render_html`)."""
    first: dict[str, Fix] = {}
    for fix in fixes:
        first.setdefault(fix.element_id, fix)
    outline = Outline()
    for element in sorted(elements, key=lambda e: e.order):
        if element.id == element_id:
            break
        if element.kind is Kind.HEADING:
            level, _ = heading_parts(element, first.get(element.id))
            if level is not None:
                outline.write(level)
    return outline


def title_key(text: str) -> str:
    """A title as the one-H1 rule compares it (SW6c): the way `suggested_title` cleans a
    title, then case folded, with punctuation and blanks collapsed, so "1 Kinematics",
    "KINEMATICS" and "Kinematics." are one title. "" for no words."""
    if not text.strip():
        return ""
    cleaned = strip_section_number(" ".join(INLINE_MARKER_RE.sub(" ", text).split()))
    return " ".join(re.findall(r"\w+", cleaned.casefold()))


def same_title(a: str, b: str) -> bool:
    """Two titles that a student would hear as the same words (`title_key`)."""
    key = title_key(a)
    return bool(key) and key == title_key(b)


@dataclass(frozen=True)
class TitlePlan:
    """How `render_html` writes the document's one `<h1>` (SW6c). `text` is the H1's text and
    the page's `<title>`. `heading_id` is the heading written as the H1, or None when the
    title is added above the headings. Every other heading is written `shift` levels below
    the outline repair's level (`Outline`), except the ones in `unshifted`: the H1 heading
    and the headings of its own section, which already sit under it. Stored levels never
    change; this is only what is written."""

    text: str
    heading_id: str | None
    shift: int
    unshifted: frozenset[str] = frozenset()
    source: str = TITLE_STOCK  # where `text` came from (`document_title`)

    def level(self, element_id: str, written: int) -> int:
        """The `<hN>` of a heading the outline repair writes at `written`."""
        offset = 0 if element_id in self.unshifted else self.shift
        return max(1, min(6, written + offset))


def title_plan(
    elements: list[Element],
    fixes: list[Fix],
    *,
    title: str | None,
    title_set: bool = False,
    filename: str = "",
) -> TitlePlan:
    """The one-H1 rule (SW6c), read from the same headings, in the same order, as the
    renderer writes them (the first fix for an id wins).

    The title is the instructor's (`title_set`, used as typed) or `document_title` of the
    PDF's, the headings and the file name (`filename`). When it is the same title
    (`same_title`) as the first top-level heading, that
    heading is the H1 and nothing is added. When it is not and the outline has exactly one
    top-level heading, that heading is the H1, unless the instructor chose the title: a
    chosen title is never replaced. Otherwise (no top-level heading, several, or a chosen
    title) the title is added as the H1 and the headings move down under it, so the
    shallowest one is an `<h2>`."""
    first: dict[str, Fix] = {}
    for fix in fixes:
        first.setdefault(fix.element_id, fix)
    outline = Outline()
    written: list[tuple[str, int, str]] = []  # (element id, level written, text)
    for element in sorted(elements, key=lambda e: e.order):
        if element.kind is Kind.HEADING:
            level, text = heading_parts(element, first.get(element.id))
            if level is not None:
                written.append((element.id, outline.write(level), text))
    tops = [entry for entry in written if entry[1] == 1]
    chosen = (
        DocumentTitle(_clean_title(title), TITLE_YOURS)
        if title_set
        else document_title(None, title, [t for *_, t in written], filename)
    )
    if tops and (same_title(chosen.text, tops[0][2]) or (len(tops) == 1 and not title_set)):
        h1 = tops[0]
        section = {h1[0]}
        for element_id, level, _ in written[written.index(h1) + 1 :]:
            if level == 1:
                break
            section.add(element_id)
        return TitlePlan(
            text=_clean_title(h1[2]),
            heading_id=h1[0],
            shift=1 if len(tops) > 1 else 0,
            unshifted=frozenset(section),
            source=(
                chosen.source if same_title(chosen.text, h1[2]) or title_set else TITLE_HEADING
            ),
        )
    shift = 2 - min((level for _, level, _ in written), default=2)
    return TitlePlan(text=chosen.text, heading_id=None, shift=shift, source=chosen.source)


# --------------------------------------------------------------------------- renderer


@dataclass
class _Renderer:
    fixes: dict[str, Fix]
    crops: dict[str, bytes]
    equations: dict[str, Element]
    used_ids: set[str] = field(
        default_factory=lambda: {ABOUT_ID, ABOUT_TITLE_ID, NOTICE_ID, NOTICE_TITLE_ID, LEFT_OUT_ID}
    )
    placed: set[str] = field(default_factory=set)  # equations rendered inside text
    # A table that goes on from the page before, with that table's header rows on top.
    carried: dict[str, TableGrid] = field(default_factory=dict)
    flagged: set[str] = field(default_factory=set)  # ids shown as originals
    outline: Outline = field(default_factory=Outline)
    # CSS a feature added for this document (`add_style`), by name.
    styles: dict[str, str] = field(default_factory=dict)
    # Write the stored speech as each <math>'s alttext (Settings.mathml_alttext).
    math_alttext: bool = False
    # Where each heading is written under the document's one H1 (SW6c, `title_plan`).
    titles: TitlePlan = field(default_factory=lambda: TitlePlan(DEFAULT_TITLE, None, 0))
    # The scale an unconverted equation's picture is shown at (T3-15).
    scale_of: Callable[[Element], float] = field(default_factory=lambda: _unit_scale)
    # Navigation (`pipeline.navigation`): the headings as written (for the contents list),
    # the printed label of each source page and whether page markers are written (the
    # document has two pages or more), the page the last marker or block was on, and the
    # footnotes: their element ids, and the id of the mark that links to each.
    toc: list[navigation.TocEntry] = field(default_factory=list)
    h1_id: str | None = None  # the id of the `<h1>` a heading is (the skip link's target)
    page_labels: Mapping[int, str] = field(default_factory=dict)
    # Page of the PDF each of the document's pages is, when it is not its own number (a part of
    # a longer file: 0-based local page to 0-based page of the source).
    pdf_pages: Mapping[int, int] = field(default_factory=dict)
    mark_pages: bool = False
    last_page: int | None = None
    footnote_ids: Collection[str] = ()
    noterefs: dict[str, str] = field(default_factory=dict)
    # "pdf", or "latex" / "beamer" for a source (`DocumentResult.origin`): a source's
    # original is its own text, shown as written, never a missing picture.
    origin: str = "pdf"
    # Pages kept as a picture for unread content whose text is written too: page -> the
    # picture's element id, until the note that opens the page's text is written.
    unread_parts: dict[int, str] = field(default_factory=dict)
    # Whom the authored notices speak to (`STUDENT`, or `CHECKER` for a draft's page).
    audience: str = STUDENT
    # The instructor's contact line, when the owner gave one: each note about something not
    # converted then links to it (`instructor_link`).
    contact: str | None = None

    def add_style(self, name: str, css: str) -> None:
        """Add CSS to this document's stylesheet, once per name. Features add their CSS
        only when a document uses them, so other documents are written as before."""
        self.styles.setdefault(name, css)

    # ----- ids and text

    def claim_id(self, element_id: str) -> str | None:
        """Each element id is used once; a repeat (a layout bug) gets no id."""
        if not element_id or element_id in self.used_ids:
            return None
        if any(c.isspace() for c in element_id):
            return None
        self.used_ids.add(element_id)
        return element_id

    def id_attr(self, element_id: str) -> str:
        claimed = self.claim_id(element_id)
        return f' id="{_escape(claimed)}"' if claimed else ""

    def fresh_id(self, stem: str) -> str:
        """An id not used yet, `stem-1`, `stem-2`, ... (for what has no pipeline id)."""
        number = 1
        while f"{stem}-{number}" in self.used_ids:
            number += 1
        self.used_ids.add(f"{stem}-{number}")
        return f"{stem}-{number}"

    def page_mark(self, element: Element) -> str:
        """The page marker to write before `element` when it is on a page after the last one
        written; "" otherwise (and always in a one-page document). Never inside a block of
        text: callers ask only between blocks, or at the start of a list item."""
        if not self.mark_pages or element.page == self.last_page:
            return ""
        return self.mark_for(element.page)

    def lead(self, element: Element) -> str:
        """What goes before a block that starts `element`: its page marker, and the note
        that opens the text of a page kept as a picture for unread content."""
        mark = self.page_mark(element)
        picture = self.unread_parts.get(element.page)
        if picture is None or picture == element.id:
            return mark
        del self.unread_parts[element.page]
        link = (
            f' <a href="#{_escape(picture)}">{UNREAD_PARTS_LINK}</a>'
            if not any(c.isspace() for c in picture)
            else f" {UNREAD_PARTS_LINK}"
        )
        return (
            f'{mark}<p class="notice flag-note {UNREAD_PARTS_CLASS} {AUTHORED_CLASS}" '
            f'role="note"><span class="flag-label">{KEPT_IMAGE_LABEL}</span> '
            f"{UNREAD_PARTS_NOTE}{link}</p>"
        )

    def marks_after(self, element: Element) -> str:
        """The markers of the pages a paragraph goes on over (`Element.last_page`), to write
        right after it: it is one element on the page it starts, so the pages it runs into
        have no element of their own to carry their marker, and a marker inside the text
        would cut it. Written in page order, so the markers stay continuous (Page 1, Page 2,
        Page 3) and the next element on the last of those pages gets none."""
        if not self.mark_pages or element.last_page is None:
            return ""
        start = (element.page if self.last_page is None else self.last_page) + 1
        return "".join(
            self.mark_for(page)
            for page in range(start, element.last_page + 1)
            if not self.pdf_pages or page in self.pdf_pages  # a page that is not in the file
        )

    def page_words(self, page: int) -> tuple[str, int]:
        """What the student's page marker says for `page` ("Page 5", "PDF page 5"), and its
        position in the PDF."""
        position = self.pdf_pages.get(page, page) + 1
        printed = self.page_labels.get(page)
        if printed:
            return f"Page {printed}", position
        if self.page_labels:  # others have printed labels: never let this one read as one
            return f"PDF page {position}", position
        return f"Page {position}", position

    def mark_for(self, page: int) -> str:
        """The marker for `page`, which becomes the last page written."""
        self.last_page = page
        said, position = self.page_words(page)
        self.add_style("pagebreak", student_sheet.PAGE_MARKER)
        return navigation.pagebreak(said, self.claim_id(f"pagebreak-{position}"), AUTHORED_CLASS)

    def instructor_link(self, element: Element, reason: str) -> str:
        """A link that writes to the instructor about one thing not converted, or "" when the
        owner gave no contact line (the note then says only "Ask your instructor"). In a draft
        (`CHECKER`) it is a sentence saying where the student's file has the link. The subject
        names the document and the thing by kind and page ("Equation on page 3"); nothing of
        its content is in the link, and the message body is empty (`contact_link`)."""
        if element.kind is Kind.EQUATION:
            noun = "equation"
        elif element.kind is Kind.TABLE:
            noun = "table"
        elif reason in KEPT_PAGE_REASONS:
            noun = "page"
        elif reason in KEPT_IMAGE_WORDS or reason == "image_page_omitted":
            noun = "picture"
        else:
            noun = "figure"
        where = self.page_words(element.page)[0] if self.mark_pages else ""
        if noun == "page":
            what = where or "This page"
        else:
            what = f"{noun.capitalize()} on {where.lower()}" if where else noun.capitalize()
        text = "{verb} your instructor about this " + noun
        link = contact_link(self.contact, self.titles.text, what, text)
        if link is not None and self.audience == CHECKER:
            # The person who checks the draft is not the student, and "your instructor" may be
            # themself: no link to write with; they are told what the student's file has here.
            return DRAFT_CONTACT_NOTE
        return link or ""

    def link_attrs(self, link: Link) -> str:
        """The mark of a footnote is a `doc-noteref`; the first one for a note also gets the
        id its backlink returns to."""
        target = link.target_id
        if link.kind != "internal" or target is None or target not in self.footnote_ids:
            return ""
        attrs = ' role="doc-noteref"'
        if target not in self.noterefs:
            ref = self.claim_id(f"noteref-{target}")
            if ref is not None:
                self.noterefs[target] = ref
                attrs = f' id="{_escape(ref)}"{attrs}'
        return attrs

    def rich(self, text: str, element: Element | None = None) -> str:
        """Escaped text with inline-equation markers replaced by maths (or a flag).

        `element` is given when `text` is that element's stored text, so its links and
        colour runs can apply (`inline_text.render_inline_text`)."""
        if element is not None and text != element.text:
            element = None
        return inline_text.render_inline_text(
            text,
            element=element,
            equation=self.marker_html,
            add_style=self.add_style,
            link_attrs=self.link_attrs if self.footnote_ids else None,
        )

    def marker_html(self, element_id: str) -> str | None:
        equation = self.equations.get(element_id)
        return None if equation is None else self.inline_equation(equation)

    def heading_parts(self, element: Element) -> tuple[int | None, str]:
        return heading_parts(element, self.fixes.get(element.id))

    # ----- elements

    def render_all(self, ordered: list[Element]) -> list[str]:
        """Every element in reading order; consecutive list items become one list.

        A display equation between two list items belongs to the item before it (layout
        does not end a list there either) and is written inside that item, so the list
        and its nesting go on after it. One after the last item stays outside the list.
        """
        chunks: list[str] = []
        i = 0
        while i < len(ordered):
            if is_entry(ordered[i]):
                mark = self.lead(ordered[i])
                entries: list[Element] = []
                while i < len(ordered):
                    if is_entry(ordered[i]):
                        entries.append(ordered[i])
                    elif ordered[i].id not in self.placed:
                        break  # (a term's inline equations are written inside it)
                    i += 1
                chunks.extend([*([mark] if mark else []), self.definitions(entries)])
                continue
            if ordered[i].kind is Kind.LIST_ITEM:
                mark = self.lead(ordered[i])
                items: list[Element] = []
                inside: dict[str, list[Element]] = {}
                while i < len(ordered):
                    element = ordered[i]
                    if element.kind is Kind.LIST_ITEM:
                        items.append(element)
                    elif element.id in self.placed:
                        pass  # an item's inline equations are written inside it
                    elif element.kind is Kind.EQUATION and self.list_goes_on(ordered, i):
                        inside.setdefault(items[-1].id, []).append(element)
                    else:
                        break
                    i += 1
                chunks.extend([*([mark] if mark else []), self.lists(items, inside)])
                continue
            chunk = self.render(ordered[i])
            if chunk:
                if mark := self.lead(ordered[i]):
                    chunks.append(mark)
                chunks.append(chunk)
                if after := self.marks_after(ordered[i]):
                    chunks.append(after)
            i += 1
        return _one_wrapper_each(chunks)

    def list_goes_on(self, ordered: list[Element], start: int) -> bool:
        """Is the next element after the equations from `start` on a list item?"""
        for element in ordered[start:]:
            if element.kind is not Kind.EQUATION:
                return element.kind is Kind.LIST_ITEM
        return False

    def render(self, element: Element) -> str:
        if element.kind is Kind.HEADING:
            return self.heading(element)
        if element.kind is Kind.PARAGRAPH:
            return self.paragraph(element, element.text or "")
        if element.kind is Kind.LIST_ITEM:
            return self.lists([element])
        if element.kind is Kind.EQUATION:
            return "" if element.id in self.placed else self.block_equation(element)
        if element.kind is Kind.FIGURE:
            return self.figure(element)
        if element.kind is Kind.TABLE:
            return assemble_tables.render_table_element(
                element, self.fixes.get(element.id), self, self.carried.get(element.id)
            )
        return ""

    def paragraph(self, element: Element, text: str) -> str:
        if not text.strip():
            return ""
        if element.preformatted:
            return self.code_block(element, text)
        if element.id in self.footnote_ids:
            return self.footnote(element, text)
        attrs = self.id_attr(element.id) + inline_text.passage_attrs(element)
        written = f"<p{attrs}>{self.rich(text, element)}</p>"
        if element.role is None or element.term_end is not None or element.list_marker:
            return written
        if element.role == "aside":
            self.add_style("aside", student_sheet.ASIDE)
        opening, closing = _ROLE_WRAP[element.role]
        return f"{opening}{written}{closing}"

    def footnote(self, element: Element, text: str) -> str:
        """A footnote, left where it falls in the text, as a `doc-footnote` with a
        `doc-backlink` to its mark. The mark comes earlier in reading order in nearly every
        document but is not known until it is written, so the link is a placeholder that
        `fill_backlinks` replaces once the whole body is written (and drops when no mark
        was written for this note)."""
        self.add_style("footnote", student_sheet.FOOTNOTE)
        attrs = self.id_attr(element.id) + inline_text.passage_attrs(element)
        return (
            f'<div role="doc-footnote"{attrs}><p>{self.rich(text, element)}'
            f"{_BACKLINK_SLOT}{element.id}{_BACKLINK_SLOT}</p></div>"
        )

    def fill_backlinks(self, parts: list[str]) -> list[str]:
        def link(match: re.Match[str]) -> str:
            ref = self.noterefs.get(match.group(1))
            return " " + navigation.backlink(ref, AUTHORED_CLASS) if ref else ""

        return [_BACKLINK_RE.sub(link, part) for part in parts]

    def code_block(self, element: Element, text: str) -> str:
        """Program code as `<pre><code>`: its lines and indentation as printed, no
        highlighting; the maths in an algorithm's step stays maths. A long line scrolls
        inside the block (WCAG 1.4.10), and a block with a line wider than a phone screen
        is a tab stop, so a keyboard user can scroll it (axe `scrollable-region-focusable`),
        named "Program code" for the focus."""
        self.add_style("code", student_sheet.CODE)
        wide = max((len(line) for line in text.split("\n")), default=0) > CODE_WIDE_CHARS
        stop = ' tabindex="0" role="group" aria-label="Program code"' if wide else ""
        attrs = self.id_attr(element.id) + inline_text.passage_attrs(element)
        body = text.strip(chr(10))
        shown = self.rich(body) if INLINE_MARKER_RE.search(body) else _escape(body)  # algorithms
        return f"<pre{attrs}{stop}><code>{shown}</code></pre>"

    def definitions(self, entries: list[Element]) -> str:
        """Glossary entries (`Element.term_end`) as one definition list: the term in a
        `<dt>`, its definition in the `<dd>` after it."""
        self.add_style("definitions", student_sheet.DEFINITIONS)
        out = []
        for entry in entries:
            text = entry.text or ""
            end = entry.term_end or 0
            attrs = self.id_attr(entry.id) + inline_text.passage_attrs(entry)
            term, definition = text[:end].strip(), text[end:].strip()
            page = self.page_mark(entry)
            out.append(
                f"<dt{attrs}>{page}<strong>{self.rich(term)}</strong></dt>"
                f"<dd>{self.rich(definition)}</dd>"
            )
        return f"<dl>{''.join(out)}</dl>"

    def lists(self, items: list[Element], inside: dict[str, list[Element]] | None = None) -> str:
        """Consecutive list items as nested lists.

        A deeper item nests inside the item before it (`_list_levels`), after the
        display equations `inside` puts in that item. At one level, siblings of one
        family (`_family`) share a list. A regular ordered list ("a.", "b.", ... or "3.",
        "4.", ...) is an `<ol>` with `type` and `start`, and the browser prints the
        markers; any other ordered list ("(a)", "3)", a gap in the numbering) keeps its
        printed markers as text in a list with `list-style: none` and `role="list"`
        (Safari drops list semantics without a list style otherwise).
        """
        levels = _list_levels(items)
        inside = inside or {}

        def build(start: int, level: int) -> tuple[str, int]:
            out, i = [], start
            while i < len(items) and levels[i] == level:
                family = _family(items[i])
                members: list[tuple[Element, str]] = []
                while i < len(items) and levels[i] == level and _family(items[i]) == family:
                    item, i = items[i], i + 1
                    nested = "".join(self.block_equation(e) for e in inside.get(item.id, []))
                    if i < len(items) and levels[i] > level:
                        deeper, i = build(i, level + 1)
                        nested += deeper
                    members.append((item, nested))
                out.append(self.one_list([m for m, _ in members], [n for _, n in members]))
            return "".join(out), i

        html, i = "", 0
        while i < len(items):
            chunk, i = build(i, levels[i])
            html += chunk
        return html

    def one_list(self, items: list[Element], nested: list[str]) -> str:
        style = _list_style(items)
        attrs = ""
        if style.type:
            attrs += f' type="{style.type}"'
        if style.start is not None:
            attrs += f' start="{style.start}"'
        if style.printed:
            attrs += ' class="printed-markers" role="list"'
        lis = []
        for item, inner in zip(items, nested, strict=True):
            marker = (
                f'<span class="list-marker">{_escape(item.list_marker or "")}</span> '
                if style.printed
                else ""
            )
            text = self.rich(item.text or "", item)
            item_attrs = self.id_attr(item.id) + inline_text.passage_attrs(item)
            page = self.page_mark(item)  # a list that runs over a page boundary
            lis.append(f"<li{item_attrs}>{page}{marker}{text}{inner}</li>")
        return f"<{style.tag}{attrs}>{''.join(lis)}</{style.tag}>"

    def heading(self, element: Element) -> str:
        level, text = self.heading_parts(element)
        if level is None:
            fix = self.fixes.get(element.id)
            decided = fix is not None and (
                (element.bold and fix.status is Status.REJECTED)
                # The same text as the heading above: bold text, by the heading step's rule.
                or (fix.status is Status.ACCEPTED and fix.reason == HEADING_REPEATS_ABOVE)
            )
            if decided and text.strip():
                # A reviewer said this bold line is a label, not a heading ("Make them
                # bold text"), or the heading step saw it repeat the heading above: it
                # stays bold, as in the PDF.
                attrs = self.id_attr(element.id) + inline_text.passage_attrs(element)
                return f"<p{attrs}><strong>{self.rich(text, element)}</strong></p>"
            return self.paragraph(element, text)
        level = self.titles.level(element.id, self.outline.write(level))
        # Every heading has an id, so the contents list can link to it: the pipeline's own
        # (`p3-e7`, the same on every render of a result), else the next free `heading-N`.
        anchor = self.claim_id(element.id) or self.fresh_id("heading")
        attrs = f' id="{_escape(anchor)}"' + inline_text.passage_attrs(element)
        if level == 1:  # the skip link jumps here, and focus moves with it (not a tab stop)
            attrs += ' tabindex="-1"'
            self.h1_id = anchor
        plain = " ".join(INLINE_MARKER_RE.sub("[equation]", text).split())
        if plain:
            self.toc.append(navigation.TocEntry(level, anchor, plain))
        return f"<h{level}{attrs}>{self.rich(text, element)}</h{level}>"

    def converted_mathml(
        self, element: Element, display: Display, *, with_id: bool
    ) -> tuple[str | None, str]:
        """(sanitised MathML or None, reason code when None)."""
        fix = self.fixes.get(element.id)
        if fix is None:
            return None, "no_fix"
        if fix.status in NOT_CONVERTED:
            return None, fix.reason or ""  # data-status already says why
        if fix.equation is None or not fix.equation.mathml.strip():
            return None, "no_output"
        claimed = self.claim_id(element.id) if with_id else None
        alttext = alttext_from_speech(fix.equation.speech) if self.math_alttext else None
        mathml = sanitize_mathml(
            fix.equation.mathml, display=display, element_id=claimed, alttext=alttext
        )
        if mathml is None:
            if claimed:
                self.used_ids.discard(claimed)  # the flagged fallback carries it instead
            return None, "mathml_rejected"
        return mathml, ""

    def print_fit(self, mathml: str) -> str:
        """The classes that fit an equation to the sheet on paper (" pw pw-24") and the rules
        of its width step, or "" when it fits any sheet (SW45 rule 6)."""
        bucket = print_width_bucket(mathml)
        if bucket is None:
            return ""
        self.add_style(f"print-fit-{bucket:03d}", student_sheet.print_fit(bucket))
        return f" pw pw-{bucket}"

    def block_equation(self, element: Element) -> str:
        mathml, reason = self.converted_mathml(element, "block", with_id=False)
        label = _equation_label(element.equation_label)
        if mathml is not None:
            css = ("equation equation-labelled" if label else "equation") + self.print_fit(mathml)
            attrs = self.id_attr(element.id) + _scroller_attrs(mathml, element.equation_label)
            return f'<div class="{css}"{attrs}>{mathml}{label}</div>'
        original = (
            self.original(element, self.image_alt(element, equation_alt(element, self.audience)))
            + label
        )
        if self.has_picture(element):
            note = EQUATION_NOTE
        else:
            note = EQUATION_MADE_NOTE if self.origin == "docx" else EQUATION_WRITTEN_NOTE
        return self.flag_block(element, reason, original, "Not converted.", note)

    def picture_size(self, element: Element, png: bytes | None) -> str:
        """The `width` and `height` of a crop's `<img>`: a figure or table at its printed
        size, an equation at the size of the file's text (T3-15, `pipeline.equation_scale`)."""
        if element.kind is not Kind.EQUATION or png is None:
            return _size_attrs(element)
        x0, y0, x1, y1 = element.bbox
        printed = (round((x1 - x0) * _PT_TO_CSS_PX), round((y1 - y0) * _PT_TO_CSS_PX))
        size = equation_scale.display_size(png, self.scale_of(element), printed)
        return f' width="{size[0]}" height="{size[1]}"' if size else ""

    def inline_equation(self, element: Element) -> str:
        mathml, reason = self.converted_mathml(element, "inline", with_id=True)
        if mathml is not None:
            # A long one would widen the page: it scrolls in a box of its own instead
            # (only those, since a scroll box sits on the text's baseline differently).
            scroller = _scroller_attrs(mathml, None)
            if scroller:
                fit = self.print_fit(mathml)
                return f'<span class="equation-scroll{fit}"{scroller}>{mathml}</span>'
            return mathml
        self.flagged.add(element.id)
        attrs = self.id_attr(element.id) + _fix_attrs(self.fixes.get(element.id), reason)
        src = _png_data_uri(self.crops.get(element.id))
        if src is not None:
            alt = _escape(self.image_alt(element, equation_alt(element, self.audience)))
            size = self.picture_size(element, self.crops.get(element.id))
            image = f'<img src="{src}" alt="{alt}"{size}>'
            # Read unless the alt text already says it (alt text that gives the PDF's
            # characters does not).
            hidden = ' aria-hidden="true"' if "not converted" in alt.lower() else ""
            label = f'<span class="flag-label {AUTHORED_CLASS}"{hidden}>'
        else:
            image = _escape(element.text or "")
            label = f'<span class="flag-label {AUTHORED_CLASS}">'
        return (
            f'<span class="flagged-inline"{attrs}>{image}{label}(equation not converted)'
            "</span></span>"
        )

    def figure(self, element: Element) -> str:
        """A figure: its image (re-encoded for the student file, `figure_images`), alt
        text and caption, then its long description and data table folded in a
        `<details>` when it has them (`figure_details`); or the original, flagged."""
        if IMAGE_PAGE_ORIGINAL_REASON in element.reasons:
            return self.image_page_note(element)
        fix = self.fixes.get(element.id)
        caption_text = element.caption_marked or element.caption
        caption = (
            f"<figcaption>{self.rich(caption_text)}</figcaption>"
            if caption_text and caption_text.strip()
            else ""
        )
        proposal = fix.figure if fix is not None else None

        def picture(alt: str) -> str:
            img = figure_images.img_html(
                self.crops.get(element.id),
                alt,
                _size_attrs(element),
                proposal.figure_type if proposal is not None else None,
            )
            return img if img is not None else self.original(element, alt)

        if IMAGE_PAGE_OMISSION_REASON in element.reasons and not (
            proposal is not None
            and fix is not None
            and fix.status not in NOT_CONVERTED
            and (proposal.decorative or proposal.alt.strip())
        ):
            # Writing on a converted page that the reading left out, not yet described: its
            # picture, said to be unconverted (the instructor's words replace this alt text).
            alt = self.image_alt(element, f"Part of page {element.page + 1}, not converted.")
            return self.flag_block(
                element, "image_page_omitted", f"<figure>{picture(alt)}</figure>",
                KEPT_IMAGE_LABEL, OMITTED_NOTE,
            )  # fmt: skip
        if fix is not None and fix.status in NOT_CONVERTED and fix.reason in KEPT_IMAGE_WORDS:
            # A page (or handwriting) kept as an image: what it is, never "a figure".
            alt, label, note = KEPT_IMAGE_WORDS[fix.reason]
            alt = self.image_alt(element, alt.format(page=element.page + 1))
            figure = f"<figure>{picture(alt)}</figure>"
            return self.flag_block(element, fix.reason, figure, label, note)
        if fix is None:
            reason = "no_fix"
        elif fix.status in NOT_CONVERTED:
            reason = fix.reason or ""
        elif proposal is None or not (proposal.decorative or proposal.alt.strip()):
            reason = "no_output"
        elif proposal.decorative and not decoration.is_decoration(element, fix):
            reason = decoration.UNPROVEN  # only the model's word: not described, never silent
        else:
            alt = "" if proposal.decorative else proposal.alt.strip()
            if _is_png(self.crops.get(element.id)):
                image = picture(alt)
            else:  # no crop to show: the description itself is the content
                image = f"<p>{_escape(alt)}</p>" if alt else ""
            ids = self.id_attr(element.id)
            if not alt and not caption:  # decorative: no empty "figure" for screen readers
                return f'<div class="{DECORATIVE_CLASS}"{ids}>{image}</div>'
            marked = f' class="{DECORATIVE_CLASS}"' if not alt else ""
            html = f"<figure{marked}{ids}>{image}{caption}</figure>"
            if not figure_details.has_details(proposal):
                return html
            # The long description and data table follow the figure, folded (W3C WAI
            # complex images: a short alt plus a long description next to the image).
            self.add_style("figure_description", student_sheet.FIGURE_DESCRIPTION)
            table_id = assemble_tables.id_prefix(element.id) + "-data"
            if figure_details.data_table_shown(proposal):
                if self.claim_id(f"{table_id}-caption") is None:
                    table_id = None  # an id already used (a layout bug): no table
                else:
                    self.add_style("table", student_sheet.TABLE)
            details = figure_details.render_details(
                proposal,
                element.caption,
                table_id=table_id,
                authored_class=AUTHORED_CLASS,
                math_alttext=self.math_alttext,
            )
            return html + details
        figure = f"<figure>{picture(FIGURE_NOT_DESCRIBED_ALT)}{caption}</figure>"
        label = say(FIGURE_LABEL, self.audience)
        return self.flag_block(element, reason, figure, label, FIGURE_NOTE)

    def image_page_note(self, element: Element) -> str:
        """The start of a page converted from an image (ADR 0007): the notice, and the
        original page in a `<details>` the reader opens (no script; the summary is the
        control and says which page). Without the picture, the notice alone."""
        self.add_style("image_page", student_sheet.IMAGE_PAGE)
        page = element.page + 1
        label = say(IMAGE_PAGE_LABEL, self.audience)
        body = say(IMAGE_PAGE_NOTE, self.audience)
        notice = f'<p><span class="flag-label">{label}</span> {body}</p>'
        png = self.crops.get(element.id)
        alt = say(IMAGE_PAGE_ORIGINAL_ALT, self.audience, page=page)
        image = figure_images.img_html(png, alt, _size_attrs(element), "photo")
        if image is None:
            shown = f'<p class="{AUTHORED_CLASS}">The original image is not available.</p>'
        else:
            summary = say(IMAGE_PAGE_SHOW, self.audience, page=page)
            shown = (
                f'<details class="{AUTHORED_CLASS}"><summary>{summary}</summary>{image}</details>'
            )
        attrs = self.id_attr(element.id)
        css = f"image-page-note {AUTHORED_CLASS}"
        return f'<div class="{css}" role="note"{attrs}>{notice}{shown}</div>'

    def image_alt(self, element: Element, default: str) -> str:
        """The alt text of an item shown as an image: the instructor's own description in
        words when there is one (T3-16), else the tool's `default` (the PDF's text, marked
        as not checked, or fixed words). Escaped by the caller, like every alt text."""
        fix = self.fixes.get(element.id)
        described = (fix.described or "").strip() if fix is not None else ""
        return described or default

    def has_picture(self, element: Element) -> bool:
        """The element's original is a picture (a crop), not a source's own text."""
        return self.origin == "pdf" or element.id in self.crops

    def original(self, element: Element, alt: str) -> str:
        """The crop as an <img>, or the extracted text when there is no image. A source's
        text is its own, shown as written: no picture was ever missing."""
        src = _png_data_uri(self.crops.get(element.id))
        if src is not None:
            size = self.picture_size(element, self.crops.get(element.id))
            return f'<img src="{src}" alt="{_escape(alt)}"{size}>'
        text = (element.text or "").strip()
        if text and not self.has_picture(element):
            return f'<p class="original-text">{_escape(text)}</p>'
        unavailable = f'<p class="{AUTHORED_CLASS}">The original image is not available.</p>'
        if text:
            return f'<p class="original-text">{_escape(text)}</p>{unavailable}'
        return unavailable

    def flag_block(self, element: Element, reason: str, body: str, label: str, note: str) -> str:
        self.flagged.add(element.id)
        fix = self.fixes.get(element.id)
        attrs = self.id_attr(element.id) + _fix_attrs(fix, reason)
        link = self.instructor_link(element, reason)
        said = "".join(f" {part}" for part in (note, link) if part)
        # A picture's alt text already says "not converted" (or "not described"): the label
        # beside it is for the eye, as an unconverted inline equation's is (`inline_equation`).
        # Without a picture (the original's text, or none) the label is what says it.
        hidden = ' aria-hidden="true"' if _alt_says_label(body, label) else ""
        attrs += _picture_scroller_attrs(body)
        return (
            f'<div class="flagged"{attrs}>{body}'
            f'<p class="flag-note {AUTHORED_CLASS}" role="note">'
            f'<span class="flag-label"{hidden}>{label}</span>{said}</p></div>'
        )


# --------------------------------------------------------------------------- document

# The labels that only repeat what the alt text of the picture beside them can say, with the
# words the alt text must hold for that to be so: "Not converted." for an equation, table,
# page or picture, and a figure's "No description." for a student (the checker's "Needs a
# description." tells what to do, so it is always read). An alt text that gives the PDF's
# characters or a person's description does not say the part was not converted: the label
# is then the only thing that does, and it is read.
_LABELS_IN_ALT = {
    KEPT_IMAGE_LABEL: "not converted",
    KEPT_PICTURE_LABEL: "not converted",
    assemble_tables.TABLE_FLAG_LABEL: "not converted",
    FIGURE_LABEL.student: "not described",
}
_PICTURE_ALT = re.compile(r'<img\b[^>]*?\salt="([^"]*)"')


def _alt_says_label(body: str, label: str) -> bool:
    """Does the picture of a flagged block (not one in its caption, which are inline
    equations) carry alt text that says what `label` says?"""
    words = _LABELS_IN_ALT.get(label)
    alt = _PICTURE_ALT.search(body.split("<figcaption>")[0])
    return words is not None and alt is not None and words in alt.group(1).lower()


def _placed_equations(
    elements: list[Element], renderer: _Renderer, equation_ids: set[str]
) -> set[str]:
    """Equations whose marker appears in text that will be rendered (rendered inline)."""
    texts: list[str] = []
    for element in elements:
        if element.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM):
            texts.append(element.text or "")
        elif element.kind is Kind.HEADING:
            texts.append(renderer.heading_parts(element)[1])
        elif element.kind is Kind.FIGURE:
            texts.append(element.caption_marked or element.caption or "")
    found = {m.group(1) for text in texts for m in INLINE_MARKER_RE.finditer(text)}
    return found & equation_ids


def _after_title(parts: Iterable[str], contents: str) -> list[str]:
    """The body's parts with the contents list right after the one that holds the `<h1>`
    (first on the page when the title is not in a part): the title comes first, so on a phone
    it is on the first screen and the skip link has its target before the list (F-116)."""
    out = list(parts)
    if contents:
        at = next((i + 1 for i, part in enumerate(out) if "<h1" in part), 0)
        out.insert(at, contents)
    return out


def _notice(text: str | None) -> str | None:
    """A note at the top of `<main>`, outlined and labelled (not colour alone)."""
    if not text or not text.strip():
        return None
    return (
        f'<p class="notice {AUTHORED_CLASS}" role="note">'
        f'<span class="flag-label">Note:</span> {_escape(" ".join(text.split()))}</p>'
    )


def kept_pages_note(kept_pages: list[int]) -> str:
    """ "Pages 2 and 5 are shown only as pictures of the page and were not converted."
    (1-based), or "" when no page is kept whole as an image. Whatever the reason (a
    handwritten or scanned page, or one with drawings nothing could be read from): each
    image's own note says which."""
    numbers = [str(p + 1) for p in sorted(set(kept_pages))]
    if not numbers:
        return ""
    if len(numbers) == 1:
        return f"Page {numbers[0]} is shown only as a picture of the page and was not converted."
    listed = ", ".join(numbers[:-1]) + " and " + numbers[-1]
    return f"Pages {listed} are shown only as pictures of the pages and were not converted."


def image_pages_note(pages: list[int]) -> str:
    """ "Page 2 was converted from an image of the page: it starts with a note and the
    original to check it against." (1-based), or "" when no page was."""
    numbers = [str(p + 1) for p in sorted(set(pages))]
    if not numbers:
        return ""
    tail = "it starts with a note and the original page to check it against."
    if len(numbers) == 1:
        return f"Page {numbers[0]} was converted from an image of the page: {tail}"
    listed = ", ".join(numbers[:-1]) + " and " + numbers[-1]
    return (
        f"Pages {listed} were converted from images of the pages: each starts with a note "
        "and the original page to check it against."
    )


def _about_section(
    summary: checked.CheckedSummary,
    source_note: str | None,
    kept_pages: list[int] | None = None,
    contact: str | None = None,
    image_pages: list[int] | None = None,
    title: str = "",
    stamp: Stamp | None = None,
) -> str:
    """The note for students at the end of `<main>` (MF7 of the UX review): what this
    is, the statement of what was reviewed and converted (`checked_words.statement`), which
    pages are only pictures, who to tell about problems. It speaks to the people who read
    the file, with no review vocabulary ("needs review", "rejected"). `kept_pages`: 0-based
    pages kept whole as an image. The sentence about pages left out carries an id, so the
    notice at the top can link to it."""
    opening = about_opening(summary.origin)
    maths = checked_words.maths_sentence(summary)
    lines = [f"<p>{opening} {maths}</p>" if maths else f"<p>{opening}</p>"]
    left_out = checked_words.left_out_sentence(summary)
    for sentence in checked_words.statement(summary):
        where = f' id="{LEFT_OUT_ID}"' if sentence == left_out else ""
        lines.append(f"<p{where}>{_escape(sentence)}</p>")
    if pages_line := kept_pages_note(kept_pages or []):
        lines.append(f"<p>{pages_line}</p>")
    if converted := image_pages_note(image_pages or []):
        lines.append(f"<p>{converted}</p>")
    lines.append(f"<p>{_contact_sentence(contact, title)}</p>")
    if source_note and source_note.strip():
        lines.append(f"<p>{_escape(source_note)}</p>")
    if stamp is not None:
        lines.append(f'<p class="about-version">{_escape(stamp.line)}</p>')
    return (
        f'<section id="{ABOUT_ID}" class="about {AUTHORED_CLASS}" '
        f'aria-labelledby="{ABOUT_TITLE_ID}">\n'
        f'<p id="{ABOUT_TITLE_ID}" class="about-title">{ABOUT_TITLE}</p>\n'
        + "\n".join(lines)
        + "\n</section>"
    )


def _checked_notice(
    summary: checked.CheckedSummary, places: Collection[str], audience: str = STUDENT
) -> str:
    """The statement again, first in `<main>`, when nobody reviewed the page or a part is a
    picture or left out (SW7), so the note at the end is not the only notice. A region named
    by its own title (not the note's, so a list of landmarks tells the two apart), with no
    live role, so it is read once, where it stands. Its links go to the note at the end, to
    each picture (numbered, up to `NOTICE_MAX_LINKS`) and to the pages left out. `places` are
    the ids the page holds, so no link points nowhere."""
    if not summary.needs_notice:
        return ""
    words = checked_words
    links = [f'<li><a href="#{ABOUT_ID}">{words.NOTICE_ABOUT_LINK}</a></li>']
    shown = [p for p in summary.converted.pictures if p.element_id in places]
    for number, picture in enumerate(shown[: words.NOTICE_MAX_LINKS], 1):
        name = _escape(words.picture_link(number, picture.kind, picture.page, summary.origin))
        links.append(f'<li><a href="#{_escape(picture.element_id)}">{name}</a></li>')
    if len(shown) > words.NOTICE_MAX_LINKS:
        rest = words.more_pictures(len(shown) - words.NOTICE_MAX_LINKS)
        links.append(f"<li>{_escape(rest)}</li>")
    if summary.left_out or summary.left_out_blocks:
        links.append(f'<li><a href="#{LEFT_OUT_ID}">{words.left_out_link(summary)}</a></li>')
    why = ""
    if summary.pictures:  # the explanation each not-converted box used to repeat (F-114)
        said = NOT_CONVERTED_WHY if summary.origin == "pdf" else NOT_CONVERTED_WHY_WRITTEN
        why = f"<p>{_escape(say(said, audience))}</p>\n"
    return (
        f'<section id="{NOTICE_ID}" class="notice checked-notice {AUTHORED_CLASS}" '
        f'aria-labelledby="{NOTICE_TITLE_ID}" tabindex="-1">\n'
        f'<p id="{NOTICE_TITLE_ID}" class="flag-label">{words.NOTICE_TITLE}</p>\n'
        f"<p>{_escape(' '.join(words.statement(summary)))}</p>\n"
        f"{why}<ul>{''.join(links)}</ul>\n</section>"
    )


def _summary_meta(summary: checked.CheckedSummary, audience: str = STUDENT) -> list[str]:
    """The statement in the page's head, for the files made from the page (the EPUB's
    accessibility summary and `accessModeSufficient`, the Canvas README's first lines),
    which read the page and not the result."""
    text = _escape(checked_words.export_summary(summary, audience == CHECKER))
    lines = [f'<meta name="{SUMMARY_META}" content="{text}">']
    if summary.text_sufficient:
        lines.append(f'<meta name="{SUFFICIENT_META}" content="textual">')
    return lines


def draft_banner(open_decisions: int) -> str:
    """The first words of a draft file: how many decisions are still open, and that it is
    not for students (SW21)."""
    many = "1 decision is" if open_decisions == 1 else f"{open_decisions} decisions are"
    return f"Draft: {many} still open. Not for students."


_HEAD_TAG = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_BODY_TAG = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


def mark_draft(html: str, open_decisions: int) -> str:
    """`html` marked as a draft: the state in its head for programs, and the banner as the
    first thing in its body, so it comes first in reading order and is said in words, not
    colour alone. The Word, EPUB, Canvas and Cartridge files are made from the marked HTML,
    so the banner is inside each of them as well. Without a `<head>` or `<body>` (a fragment)
    the banner goes first."""
    word, colon, rest = draft_banner(open_decisions).partition(":")
    banner = (
        f'<p class="{DRAFT_CLASS}" role="note" lang="en" style="border: 3px solid; '
        f'padding: 0.75em 1em; margin: 0.5rem; font-size: 1.25em">'
        f"<strong>{word}{colon}</strong>{_escape(rest)}</p>"
    )
    if head := _HEAD_TAG.search(html):
        html = html[: head.end()] + DRAFT_STATE_META + html[head.end() :]
    if body := _BODY_TAG.search(html):
        return html[: body.end()] + banner + html[body.end() :]
    return banner + html


MAX_SUBJECT_TITLE = 100  # characters of the document's title in a message's subject


def _contact_href(contact: str | None) -> tuple[str, str] | None:
    """The instructor's contact line as ("email", address) or ("web", address), or None when
    there is none or it is neither (`web.titles.clean_contact` has checked what is stored;
    anything else is left out here too)."""
    line = (contact or "").strip()
    if not line or len(line) > 320 or re.search(r"[\s<>\"']", line):
        return None
    if re.fullmatch(r"[^@/:]+@[^@/:]+\.[^@/:.]+", line):
        return "email", line
    if re.fullmatch(r"https?://[^/\s]+\.[^/\s]+\S*", line):
        return "web", line
    return None


def contact_link(contact: str | None, title: str, what: str, text: str) -> str | None:
    """A link to the instructor's contact line, or None when there is none. For an email
    address it is a `mailto:` with the subject "<title>: <what>" and an empty body: `what`
    names the thing by kind and page ("Equation on page 3"), never by its content. For a web
    address it goes there. `text` is the link text, with `{verb}` for "Email" or "Contact"."""
    found = _contact_href(contact)
    if found is None:
        return None
    kind, address = found
    if kind == "email":
        name = " ".join(title.split())
        if len(name) > MAX_SUBJECT_TITLE:
            name = name[: MAX_SUBJECT_TITLE - 3].rstrip() + "..."
        subject = quote(f"{name}: {what}" if name else what, safe="")
        href = f"mailto:{address}?subject={subject}"
    else:
        href = address
    words = text.format(verb="Email" if kind == "email" else "Contact")
    return f'<a href="{_escape(href)}">{_escape(words)}</a>'


def _contact_sentence(contact: str | None, title: str = "") -> str:
    """The "tell your instructor" sentence. With the instructor's own contact line (T1-17) it
    carries a link that writes to them about this document (`contact_link`), and the address
    as typed beside it, for a reader whose mail program is not set up or who prints the page."""
    words = "{verb} your instructor about this document"
    link = contact_link(contact, title, "a problem with this document", words)
    if link is None:
        return CONTACT
    return f"{CONTACT.removesuffix('.')}: {link} ({_escape((contact or '').strip())})."


# A line longer than this is a tab stop. Measured in Chromium at 320 px: a block first scrolls
# at 30 characters, and at 25 with WCAG 1.4.12 text spacing, so 24 leaves no scrolling block
# without one (it was 36, F-448).
CODE_WIDE_CHARS = 24


# The tool's own words (notes, labels, the student note) are English. In a document in
# another language they say so (`lang="en"`, WCAG 3.1.2 Language of Parts), on every
# element carrying AUTHORED_CLASS. Document text is escaped, so only our own tags match.
_AUTHORED_TAG = re.compile(
    r'(<[a-z][a-z0-9]*\b[^<>]*\bclass="(?:[^"]*\s)?' + AUTHORED_CLASS + r'(?:\s[^"]*)?")'
)
# ... and the tool's English words that carry no class: the fixed alt text of an original
# kept as an image, and a chart data table's caption ("Data read from Figure 2.11; the
# values are approximate", `figure_details.table_caption`; its id ends in "-data").
_FIXED_ALT_IMG = re.compile(
    r'(<img\b[^<>]*\balt="(?:'
    + "|".join(
        [
            *map(
                re.escape,
                (
                    EQUATION_NOT_CONVERTED_ALT,
                    FIGURE_NOT_DESCRIBED_ALT,
                    assemble_tables.TABLE_NOT_CONVERTED_ALT,
                ),
            ),
            # An equation kept as an image: "Equation (2.4), not converted", or its PDF
            # text with our words after it (`equation_alt`).
            r"Equation \([^\"<>]{1,50}\), not converted",
            r"[^\"<>]{1,500}(?:"
            + "|".join(re.escape(_escape(suffix)) for suffix in EQUATION_TEXT_ALT_SUFFIXES)
            + ")",
            flat_alt_lead(r'[^"<>]') + r'[^"<>]{1,500}',
            # A kept page's alt names its page: any number in its place.
            *(
                re.escape(alt).replace(re.escape("{page}"), r"\d+")
                for alt, *_ in KEPT_IMAGE_WORDS.values()
            ),
        ]
    )
    + r')")'
)
_DATA_TABLE_CAPTION = re.compile(r'(<caption id="[^"<>]*-data-caption")')


def _authored_in_english(part: str, lang: str) -> str:
    """`part` with `lang="en"` on the tool's own elements when the document is not in
    English; unchanged (byte for byte) when it is."""
    if _clean_lang(lang).split("-", 1)[0].lower() == "en":
        return part
    for pattern in (_AUTHORED_TAG, _FIXED_ALT_IMG, _DATA_TABLE_CAPTION):
        part = pattern.sub(r'\1 lang="en"', part)
    return part


def _without_dead_links(ordered: list[Element]) -> list[Element]:
    """The elements with every internal link and cross-reference whose target is not in
    the document taken off (the target is on a page the instructor left out, T3-16): a
    link to nowhere helps nobody. An element with none is returned as it is."""
    known = {e.id for e in ordered}
    out = []
    for element in ordered:
        if any(link.target_id and link.target_id not in known for link in element.links):
            links = [k for k in element.links if not k.target_id or k.target_id in known]
            element = element.model_copy(update={"links": links})
        out.append(element)
    return out


def _unread_parts(
    ordered: list[Element], fixes: dict[str, Fix], placed: Collection[str]
) -> dict[int, str]:
    """Pages kept as a picture for unread content (`unread_page`) whose text is written too:
    page -> the picture's element id. A page with no text of its own needs no note: its
    picture and the picture's own note are all there is."""
    pictures = {
        e.page: e.id
        for e in ordered
        if e.kind is Kind.FIGURE
        and (fix := fixes.get(e.id)) is not None
        and fix.reason == "unread_page"
        and fix.status in NOT_CONVERTED
    }
    texts = {e.page for e in ordered if e.id not in placed and e.id not in pictures.values()}
    return {page: picture for page, picture in pictures.items() if page in texts}


def _kept_prose(ordered: list[Element], fixes: dict[str, Fix]) -> list[Element]:
    """Text read from an image of a page that the reviewer chose to keep as the picture: the
    element is written as a kept image of its region (a figure with the reason
    "image_page_text", like a kept page), and the inline equations inside it are not written
    on their own. `fixes` is updated to match. Every other element is returned as it is."""
    kept = {
        e.id
        for e in ordered
        if IMAGE_PAGE_REASON in e.reasons
        and e.kind in (Kind.PARAGRAPH, Kind.LIST_ITEM)
        and (fix := fixes.get(e.id)) is not None
        and fix.status in NOT_CONVERTED
    }
    if not kept:
        return ordered
    out: list[Element] = []
    for element in ordered:
        if element.id in kept:
            fix = fixes[element.id]
            fixes[element.id] = fix.model_copy(update={"reason": "image_page_text"})
            element = element.model_copy(
                update={"kind": Kind.FIGURE, "text": None, "list_marker": None, **inline.cleared()}
            )
        elif element.parent_id in kept:
            continue
        out.append(element)
    return out


def render_html(
    *,
    title: str | None,
    lang: str,
    elements: list[Element],
    fixes: list[Fix],
    crops: dict[str, bytes],
    source_note: str | None = None,
    notice: str | None = None,
    checked_on: date | None = None,
    title_set: bool = False,
    contact: str | None = None,
    math_alttext: bool | None = None,
    omitted_pages: list[int] | None = None,
    left_out_blocks: list[int] | None = None,
    page_labels: Mapping[int, str] | None = None,
    pdf_pages: Mapping[int, int] | None = None,
    footnote_ids: Collection[str] = (),
    origin: str = "pdf",
    pages: int | None = None,
    unread: tuple[int, int] = (0, 0),
    cut_off_pages: list[int] | None = None,
    filename: str = "",
    audience: str = STUDENT,
    stamp: Stamp | None = None,
    pipeline_version: str | None = None,
) -> str:
    """One self-contained HTML5 document for the elements, in reading order.

    `audience`: whom the authored notices speak to. `STUDENT` (the default) is the student
    file, the only one that is stored and that every export is made from. `CHECKER` is the
    draft's page, made fresh for the person who checks it and never stored: it adds what a
    student must not be told ("Check it against the original.").

    `pipeline_version` is the version of the pipeline that converted the result the page is
    made from (`DocumentResult.pipeline_version`), which the generator tag names beside the
    version of this code, since the two differ when a stored result is rendered again; a page
    joined from several results names each version its parts were converted at
    (`combine.Joined.pipeline_version`); None names only this code.

    `fixes` are matched to elements by id (the first fix for an id wins, as in
    `DocumentResult.fix_for`; later duplicates are ignored, also in the counts). `crops` maps
    element ids to PNG bytes; they are embedded as data: URIs only where an image is
    shown (figures, tables kept as images, and equations that were not converted).
    `source_note` is plain text for the about section, e.g. an OpenStax attribution.
    `notice` is plain text shown first in `<main>` as a labelled note, for something
    every reader must see before the content (the CLI marks offline test runs, whose
    answers are placeholders). `checked_on` is the day the instructor made the last
    review decision: the student note gives it once no decision is open (the review
    screen passes it when it re-renders the file; the pipeline never does).
    `title_set`: `title` is the one the instructor chose (T1-7), used as typed. Otherwise the
    title is `document_title` of the PDF's, the headings and `filename` (SW14). The page has
    exactly one `<h1>` and its
    `<title>` is the H1's text (`title_plan`, SW6c). `contact`: the instructor's contact
    line for the note at the end of `<main>` (T1-17). `math_alttext`: write each equation's
    stored speech as the `alttext` of its <math> (None: `Settings.mathml_alttext`, off by
    default).
    `omitted_pages`: 0-based pages the instructor left out (T3-16); the elements are not
    here, and the student note says so. `left_out_blocks`: the 0-based page of each paragraph
    or list item the instructor left out; they are not here either, and the note counts them.
    `page_labels`: the printed label of each 0-based page
    that has one (`DocumentResult.page_labels`), for the page markers. A page without one says its
    position in the PDF ("PDF page 5") when any page has a printed label, so two pages never
    both read "Page 5", and "Page 5" when none has. `pdf_pages` maps a page of this document to
    its page in the PDF it was cut from (a part of a longer file). `footnote_ids`: the element
    ids of footnotes (`Notice` code "footnote"), whose marks and notes are linked both ways.
    `origin`: "pdf" or, for a document made from a LaTeX source, "latex" or "beamer"
    (`DocumentResult.origin`): the student note's first sentence says so. `pages`: how many
    pages the document has, when known, so the note can say "7 of 8" pages were left out.
    `unread`: the words and pictures a LaTeX source's reader did not read
    (`checked.unread_of`), which the student note says are not in the file. `cut_off_pages`: the
    0-based empty last pages of a PDF that looks cut off (`checked.cut_off_of`), which the note
    says are not in the file either.
    `stamp`: which version of the document this file is (`Stamp`), written as the last line of
    the student note and in the head. The caller supplies it (no clock or database is read here);
    without one the file says nothing of a version.
    """
    ordered = _without_dead_links(sorted(elements, key=lambda e: e.order))
    first_fix: dict[str, Fix] = {}
    for fix in fixes:  # first wins, like DocumentResult.fix_for
        first_fix.setdefault(fix.element_id, fix)
    ordered = _kept_prose(ordered, first_fix)
    first_fix = table_math.with_cell_readings(ordered, first_fix)  # a table's maths cells
    equations = {e.id: e for e in ordered if e.kind is Kind.EQUATION}
    if math_alttext is None:
        math_alttext = get_settings().mathml_alttext

    titles = title_plan(ordered, fixes, title=title, title_set=title_set, filename=filename)
    renderer = _Renderer(
        fixes=first_fix,
        crops=crops,
        equations=equations,
        math_alttext=math_alttext,
        titles=titles,
        scale_of=equation_scale.scaler(ordered),
        page_labels=page_labels or {},
        pdf_pages=pdf_pages or {},
        mark_pages=len({p for e in ordered for p in (e.page, e.last_page or e.page)}) >= 2,
        footnote_ids=set(footnote_ids),
        origin=origin,
        audience=audience,
        contact=contact,
    )
    renderer.placed = _placed_equations(ordered, renderer, set(equations)) | (
        table_math.cell_equation_ids(ordered) & set(equations)  # written in their table's cell
    )
    renderer.unread_parts = _unread_parts(ordered, first_fix, renderer.placed)
    renderer.carried = assemble_tables.carry_headers(ordered, first_fix)[0]
    body = _name_sidebars(renderer.fill_backlinks(renderer.render_all(ordered)))
    kept_pages = [
        e.page
        for e in ordered
        if (fix := first_fix.get(e.id)) is not None
        and fix.reason in KEPT_PAGE_REASONS
        and fix.status in NOT_CONVERTED
    ]
    # The statement of what was reviewed and converted, from the same parts the web app reads
    # from the stored result (`checked.of_result`), so the page and the job page agree.
    summary = checked.summarize(
        elements=elements,
        fixes=fixes,
        left_out_pages=omitted_pages or (),
        left_out_blocks=left_out_blocks or (),
        pages=pages,
        reviewed_on=checked_on,
        origin=origin,
        unread=unread,
        cut_off_pages=cut_off_pages or (),
    )
    about = _about_section(
        summary,
        source_note,
        kept_pages,
        contact,
        [e.page for e in ordered if IMAGE_PAGE_ORIGINAL_REASON in e.reasons],
        title=titles.text,
        stamp=stamp,
    )
    top = _checked_notice(summary, renderer.used_ids, audience)
    # Exactly one H1 (SW6c): the title, added above the headings, or the heading that is it.
    page_title = titles.text
    if titles.heading_id is None:
        title_h1 = f'<h1 id="{navigation.MAIN_ID}" tabindex="-1">{_escape(page_title)}</h1>'
        body = [title_h1, *body]
        renderer.h1_id = navigation.MAIN_ID
    # The PDF's own contents page is the contents list; a second one would be one too many.
    printed_contents = any(navigation.PRINTED_CONTENTS_REASON in e.reasons for e in ordered)
    contents = ""
    if len(renderer.toc) >= navigation.MIN_TOC_HEADINGS and not printed_contents:
        renderer.add_style("contents", student_sheet.CONTENTS)
        english = _clean_lang(lang).split("-", 1)[0].lower() == "en"
        contents = _authored_in_english(
            navigation.contents_nav(
                # The title stands right above the list: it is not an entry of it.
                [entry for entry in renderer.toc if entry.id != renderer.h1_id],
                AUTHORED_CLASS,
                None if english else _clean_lang(lang),
            ),
            lang,
        )
    # The skip link lands on the first thing a reader must not miss: the notice that says
    # parts are pictures or nobody reviewed the page, when there is one, else the title. A
    # link that jumped past that notice would hide it from the readers it is written for.
    skip_target = NOTICE_ID if top else (renderer.h1_id or navigation.MAIN_ID)
    # Feature CSS (tables, ...) only in documents that use it, in a fixed order.
    style = student_sheet.screen(renderer.styles)
    page = "\n".join(
        [
            "<!doctype html>",
            f'<html lang="{_escape(_clean_lang(lang))}">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f'<meta name="generator" content="ReadableSTEM {_escape(__version__)}'
            f'{f", pipeline {_escape(pipeline_version)}" if pipeline_version else ""}">',
            f'<meta name="{RENDER_META}" content="{RENDER_VERSION}">',
            *(
                [
                    f'<meta name="{VERSION_META}" content="{_escape(stamp.version_id)}">',
                    f'<meta name="{MODIFIED_META}" content="{stamp.day.isoformat()}">',
                ]
                if stamp is not None
                else []
            ),
            *_summary_meta(summary, audience),
            f"<title>{_escape(page_title)}</title>",
            f"<style>\n{style}</style>",
            "</head>",
            "<body>",
            _authored_in_english(navigation.skip_link(AUTHORED_CLASS, skip_target), lang),
            "<main>",
            *([_authored_in_english(note, lang)] if (note := _notice(notice)) else []),
            *([_authored_in_english(top, lang)] if top else []),
            *_after_title((_authored_in_english(part, lang) for part in body), contents),
            _authored_in_english(about, lang),
            "</main>",
            "</body>",
            "</html>",
            "",
        ]
    )
    # No character that is not text reaches the student (`output_chars`): a marked gap in
    # its place, which every output made from the page carries and Listen speaks.
    gap = _authored_in_english(
        f'<span class="flagged-inline {AUTHORED_CLASS}">{output_chars.UNREADABLE}</span>', lang
    )
    return output_chars.mark_html(page, gap)
