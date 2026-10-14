"""Where a notice is, in words and on the page (T3-7).

A notice (`core.types.Notice`) is a code, a page, an element id and a count; it never
holds document text. The job page and the review screen show it to the document's owner,
who needs to know *which* link or figure it is about and to be taken to it. This works
that out again each time from the result as it is now:

- `names`: the specific things it is about. For a link whose text says nothing on its own,
  the text of each such link in the paragraph ("click here"); for a figure, table or page,
  its name in the review ("Figure 2", "Table 3", "Page 4");
- `snippet`: for a notice about a paragraph, its first words, so two notices on one page
  are told apart;
- `query`: the Split View address of its place (the part after `/jobs/{id}/review?`): the
  item's own panel when the element has a fix, a panel for the notice when it is about
  text (`note=<code>:<element id>`, with a mark on the page), the page when it is about a
  page, nothing when it is about the whole document.

Nothing here is stored or logged: it is document content, for the owner's pages only.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlencode

from a11y_stem.core.types import DocumentResult, Element, Fix, Kind, Notice
from a11y_stem.pipeline import notices as notice_rules
from a11y_stem.pipeline.text_pass import context_words, unclear_link_text, visible_text

SNIPPET_CHARS = 70
NAME_CHARS = 60
MAX_NAMES = 5
# Notices that are a decision of their own (the table or page kept as an image is a fix the
# reviewer decides): they are in the queue, not in the optional checks.
DECISION_CODES = frozenset({"table_not_rebuilt"}) | notice_rules.KEPT_IMAGE_CODES
# What the names are, in a sentence ("Links: “click here”"). Anything else: "Here:".
NAMES_LEAD = {
    "link_text_unclear": "The link text",
    "colour_only": "The figure",
    "long_description_missing": "The figure",
    "table_not_rebuilt": "The table",
}


@dataclass(frozen=True)
class Place:
    notice: Notice
    names: tuple[str, ...] = ()
    snippet: str = ""
    query: str = ""  # "page=3&note=link_text_unclear%3Ap3-e4": the Split View address
    key: str = ""  # "link_text_unclear:p3-e4", the `note` of a notice about text

    @property
    def lead(self) -> str:
        return NAMES_LEAD.get(self.notice.code, "Here")

    @property
    def anchor(self) -> str:
        """Where on the review screen the address lands: the panel of an item or a notice,
        or the page."""
        return "decision-title" if ("item=" in self.query or "note=" in self.query) else "page-view"

    @property
    def address(self) -> str:
        """The query and the fragment, or "" for a notice about the whole document."""
        return f"{self.query}#{self.anchor}" if self.query else ""


def note_key(notice: Notice) -> str:
    return f"{notice.code}:{notice.element_id}"


def _shorten(text: str, limit: int) -> str:
    one = " ".join(text.split())
    return one if len(one) <= limit else one[:limit].rsplit(" ", 1)[0] + "…"


def readable(text: str) -> str:
    """An element's text for a person: an inline equation marker as "[equation]"."""
    return visible_text(text, 0, len(text))


def unclear_links(element: Element) -> list[str]:
    """The text of each link in the element that says nothing on its own, as the text pass
    judged it when it made the notice (`text_pass.unclear_link_text`)."""
    text = element.text or ""
    found = []
    for link in element.links:
        if link.kind == "crossref":
            continue
        shown = visible_text(text, link.start, link.end)
        if shown and unclear_link_text(shown, context_words(text, link.start, link.end)):
            found.append(_shorten(shown, NAME_CHARS))
    return found


def place_of(
    notice: Notice,
    elements: dict[str, Element],
    fixes: dict[str, Fix],
    labels: dict[str, str],
) -> Place:
    """The names and the Split View address of one notice."""
    shown = notice.page + 1 if notice.page is not None else None
    page = f"page={shown}" if shown is not None else ""
    element = elements.get(notice.element_id or "")
    if element is None:
        return Place(notice, query=page)
    fix = fixes.get(element.id)
    if fix is not None and fix.kind in {Kind.FIGURE, Kind.TABLE, Kind.EQUATION, Kind.HEADING}:
        name = labels.get(element.id, "")
        query = urlencode({"item": element.id} | ({"page": shown} if shown is not None else {}))
        return Place(notice, names=(name,) if name else (), query=query)
    key = note_key(notice)
    names: list[str] = []
    if notice.code == "link_text_unclear":
        names = unclear_links(element)[:MAX_NAMES]
    snippet = _shorten(readable(element.text or ""), SNIPPET_CHARS)
    query = urlencode({"page": shown, "note": key}) if shown is not None else f"note={quote(key)}"
    return Place(notice, names=tuple(names), snippet=snippet, query=query, key=key)


def places(result: DocumentResult, labels: dict[str, str]) -> list[Place]:
    """Every notice as the job page lists them now, with its names and place."""
    elements = {e.id: e for e in result.elements}
    fixes: dict[str, Fix] = {}
    for fix in result.fixes:
        fixes.setdefault(fix.element_id, fix)
    return [place_of(n, elements, fixes, labels) for n in notice_rules.current(result)]


def shown_in_review(place: Place, fixes: dict[str, Fix]) -> bool:
    """Listed with the review screen's optional checks: everything but the notices that are
    a decision of their own (a table or page kept as an image) and the whole-document ones
    that are about the file's language (the job page has the control)."""
    notice = place.notice
    fix = fixes.get(notice.element_id or "")
    if fix is not None and fix.review == "required" and notice.code in DECISION_CODES:
        return False
    return notice.page is not None or notice.element_id is not None


@dataclass(frozen=True)
class Row:
    """One notice as a line of the review screens' optional checks."""

    label: str  # "Page 3"
    sentence: str
    what: str  # the names or the paragraph's first words, without quotation marks
    href: str  # its place on the review screen


def address(job_id: str, place: Place) -> str:
    return f"/jobs/{job_id}/review?{place.query}#{place.anchor}" if place.query else ""


def rows(job_id: str, found: list[Place], fallback: str) -> list[Row]:
    """The lines for these notices. A notice about the whole document has no place on a
    page: its line goes to `fallback`."""
    from a11y_stem.web.messages import explain_notice

    out = []
    for place in found:
        notice = place.notice
        where = f"Page {notice.page + 1}" if notice.page is not None else "The whole document"
        # The template puts the quotation marks round it.
        what = "”, “".join(place.names) or place.snippet
        href = address(job_id, place) or fallback
        out.append(Row(where, explain_notice(notice).text, what, href))
    return out
