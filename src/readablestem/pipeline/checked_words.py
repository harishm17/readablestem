"""Every sentence about what was converted, checked or reviewed, as a function of a
`pipeline.checked.CheckedSummary` (SW7).

The student note and the notice that opens the student page, the job page's status, checks
line and finish line, the before/after card's "Yes", the review screen's finish, the record
and its CSV, the EPUB's accessibility metadata and the Canvas README all print what these
functions return. No template and no other module writes such a sentence, so a later item
can move them into one string table as they are.

Rules every sentence here keeps (honesty claims, CONTRIBUTING.md "Wording"):

- A person "reviewed"; the tool's checks are "structure checks". Nothing says "checked" of
  a person's review, and nothing says a document is accessible or conforms to anything.
- An optional note nobody opened is never counted as reviewed, and an "Accept anyway" is said
  apart: "kept as the tool wrote them without a check passing".
- A missing fact never gives a stronger claim: "every part the tool found" (parts it did not
  find are not counted), no date when the day is not known, no "all checks passed" when no
  checks are stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from readablestem.pipeline.checked import CheckedSummary

# --------------------------------------------------------------------------- words


def plural(n: int, one: str, many: str | None = None) -> str:
    """ "1 equation", "3 equations"."""
    return f"{n} {one if n == 1 else (many or one + 's')}"


def join_words(parts: list[str]) -> str:
    """ "a", "a and b", "a, b and c"."""
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def long_date(day: date) -> str:
    """The date as "October 9, 2026" (US order, as the rest of the site)."""
    return f"{day:%B} {day.day}, {day.year}"


KIND_WORDS = {
    "equation": ("equation", "equations"),
    "figure": ("figure", "figures"),
    "table": ("table", "tables"),
    "heading": ("heading", "headings"),
    # Text read from an image of a page (ADR 0007), kept as its picture.
    "paragraph": ("piece of text", "pieces of text"),
    "page": ("page", "pages"),
    "handwriting": ("piece of handwriting", "pieces of handwriting"),
}


# The student's words for a kind where the owner's would say more than the result can back: a
# part kept as a picture is named "handwriting" for a box of vector lines too (WS21.9), so a
# student is told only that it is a picture.
STUDENT_KIND_WORDS = {"handwriting": "picture"}


def kinds_phrase(counts: Mapping[str, int]) -> str:
    """ "1 equation and 2 figures" (in a fixed order)."""
    parts = [
        plural(counts[kind], one, many)
        for kind, (one, many) in KIND_WORDS.items()
        if counts.get(kind)
    ]
    return join_words(parts) or plural(sum(counts.values()), "part")


def _pages_list(pages: tuple[int, ...]) -> str:
    numbers = [str(p) for p in pages]
    return numbers[0] if len(numbers) == 1 else ", ".join(numbers[:-1]) + " and " + numbers[-1]


# --------------------------------------------------------------------------- never checked

# What the tool does not know, whatever the document. Plain facts about the method; the
# record lists them, and the job page names the first two.
NEVER_CHECKED = (
    "Whether the file reads well with a screen reader. Nobody has listened to it with NVDA, "
    "JAWS or VoiceOver.",
    "The axe accessibility test. It runs on the tool's own test documents, not on yours.",
    "Whether each part says what the PDF meant. The structure checks look at the structure "
    "of the file (language, title, headings, lists, tables, image descriptions), not at "
    "the meaning.",
)

# --------------------------------------------------------------------------- the student

# The student statement: the same sentences close the page ("About this version") and,
# when the page is unreviewed or a part is a picture or left out, open it.
NOT_REVIEWED = "This version has not been reviewed by a person."
MATHS_ALL = "Its equations are written so that screen readers can read them aloud."
NOTICE_TITLE = "Before you read"
NOTICE_ABOUT_LINK = "About this version"
NOTICE_LEFT_OUT_LINK = "Which pages were left out"
NOTICE_LEFT_OUT_BLOCKS_LINK = "What was left out"
NOTICE_MAX_LINKS = 10  # links to pictures; the rest are counted


def maths_sentence(s: CheckedSummary) -> str:
    """How many equations a screen reader can read ("" when there are none, or none is)."""
    c = s.converted
    if not c.equations or not c.equations_mathml:
        return ""
    if c.equations_mathml == c.equations:
        return MATHS_ALL
    verb = "is" if c.equations_mathml == 1 else "are"
    # A Word or LaTeX source has no picture of an equation: the page shows its LaTeX.
    others = "pictures" if c.pictures_by_kind.get("equation") else "shown as LaTeX text"
    return (
        f"{c.equations_mathml} of its {c.equations} equations {verb} written so that screen "
        f"readers can read them aloud; the others are {others}."
    )


def review_sentence(s: CheckedSummary) -> str:
    """One of three forms: not reviewed by a person; "Your instructor reviewed N of M parts
    the tool flagged, on <date>."; and the same with the parts kept with a check failing.
    An optional note nobody opened is in M, never in N."""
    if not s.reviewed:
        return NOT_REVIEWED
    when = f", on {long_date(s.reviewed_on)}" if s.reviewed_on is not None else ""
    flagged = "part the tool flagged" if s.flagged == 1 else "parts the tool flagged"
    text = f"Your instructor reviewed {s.load.decided} of {s.flagged} {flagged}{when}."
    if k := s.accepted_failing:
        were, them = ("was", "it") if k == 1 else ("were", "them")
        text += (
            f" {plural(k, 'part')} {were} kept as the tool wrote {them} without a check passing."
        )
    return text


# What a document's source is called in every sentence about it ("your Word file"): the
# `DocumentResult.origin` to its noun. One table, so that no sentence types "PDF" or "LaTeX"
# for the job's source by itself.
SOURCE_NOUNS = {
    "pdf": "PDF",
    "latex": "LaTeX source",
    "beamer": "LaTeX slide deck",
    "docx": "Word file",
}


def source_noun(origin: str) -> str:
    """The noun for a job's source: "PDF", "LaTeX source", "LaTeX slide deck", "Word file"."""
    return SOURCE_NOUNS.get(origin, "PDF")


def pictures_sentence(s: CheckedSummary) -> str:
    """The parts shown as pictures (as written, for a LaTeX source), with their count."""
    k = s.pictures
    if not k:
        return ""
    parts = f"{plural(k, 'part')} could not be converted"
    if s.origin == "pdf":
        shown = "is shown as a picture" if k == 1 else "are shown as pictures"
        shown += " of the original"
    else:
        shown = "is shown as it was written" if k == 1 else "are shown as they were written"
        shown += f" in the {source_noun(s.origin)}"
    return (
        f"{parts} and {shown}, marked “Not converted” or “No description”: ask your "
        "instructor for a version you can read."
    )


def left_out_sentence(s: CheckedSummary) -> str:
    """ "Page 1 of the original PDF was left out of this file." ("" when none was), then, when
    a person left single paragraphs or list items out, "One paragraph or list item on page 3
    of the original PDF was left out of this file." The one anchor of the note's link."""
    return " ".join(p for p in (_pages_left_out(s), _blocks_left_out(s)) if p)


def _pages_left_out(s: CheckedSummary) -> str:
    pages = s.converted.left_out_pages
    if not pages:
        return ""
    if len(pages) == 1:
        return f"Page {pages[0]} of the original PDF was left out of this file."
    total = s.converted.pages
    of = f" ({len(pages)} of {total})" if total and total >= len(pages) else ""
    return f"Pages {_pages_list(pages)}{of} of the original PDF were left out of this file."


def _blocks_left_out(s: CheckedSummary) -> str:
    pages = s.converted.left_out_blocks
    if not pages:
        return ""
    where = _pages_list(tuple(dict.fromkeys(pages)))
    if len(pages) == 1:
        return (
            f"One paragraph or list item on page {where} of the original PDF was left out "
            "of this file."
        )
    label = "page" if len(set(pages)) == 1 else "pages"
    return (
        f"{len(pages)} paragraphs or list items on {label} {where} of the original PDF were "
        "left out of this file."
    )


def left_out_link(s: CheckedSummary) -> str:
    """The words of the notice's link to the sentence above."""
    return NOTICE_LEFT_OUT_LINK if s.left_out else NOTICE_LEFT_OUT_BLOCKS_LINK


def page_range(pages: tuple[int, ...]) -> str:
    """ "page 6", "pages 5 and 6", "pages 4 to 6" (1-based, consecutive)."""
    if len(pages) == 1:
        return f"page {pages[0]}"
    joiner = "and" if len(pages) == 2 else "to"
    return f"pages {pages[0]} {joiner} {pages[-1]}"


def cut_off_sentence(s: CheckedSummary) -> str:
    """What a student is told of a PDF that looks cut off ("" when it does not)."""
    pages = s.converted.cut_off_pages
    if not pages:
        return ""
    where = page_range(pages)
    verb = "is" if len(pages) == 1 else "are"
    return (
        f"{where[0].upper()}{where[1:]} of the original PDF {verb} empty, because the file looks "
        "cut off, so the rest is not in this file: ask your instructor for a complete version."
    )


def _unread_phrase(s: CheckedSummary) -> str:
    """ "14 words and 1 picture of the LaTeX source" ("" when it was read whole)."""
    c = s.converted
    parts = [plural(c.unread_words, "word")] if c.unread_words else []
    parts += [plural(c.unread_pictures, "picture")] if c.unread_pictures else []
    return f"{join_words(parts)} of the {source_noun(s.origin)}" if parts else ""


def unread_sentence(s: CheckedSummary) -> str:
    """What the reader of a source did not read, for the student ("" when none)."""
    if not (what := _unread_phrase(s)):
        return ""
    verb = "is" if s.unread == 1 else "are"
    return (
        f"{what[0].upper()}{what[1:]} could not be read and {verb} not in this file: ask your "
        "instructor for a version you can read."
    )


def unread_owner(s: CheckedSummary) -> str:
    """The same for the owner's lines ("" when none)."""
    if not (what := _unread_phrase(s)):
        return ""
    return f"{what[0].upper()}{what[1:]} {'was' if s.unread == 1 else 'were'} not read."


def statement(s: CheckedSummary) -> list[str]:
    """The student statement: who reviewed it, what is a picture, what was left out or not
    read."""
    parts = (
        review_sentence(s),
        pictures_sentence(s),
        left_out_sentence(s),
        cut_off_sentence(s),
        unread_sentence(s),
    )
    return [t for t in parts if t]


def picture_link(number: int, kind: str, page: int, origin: str = "pdf") -> str:
    """The words of the notice's link to one picture: numbered, so no two are the same. Its
    page only for a PDF: a Word or LaTeX file has no pages to name."""
    one = STUDENT_KIND_WORDS.get(kind) or KIND_WORDS.get(kind, (kind, kind))[0]
    where = f", page {page}" if origin == "pdf" else ""
    return f"Part {number} not converted: {one}{where}"


def more_pictures(n: int) -> str:
    where = "it is" if n == 1 else "they are"
    return f"{plural(n, 'more part')}, each marked “Not converted” where {where}."


# --------------------------------------------------------------------------- the owner

READY_ALL = "Every part the tool found was converted."
RECORD_NO_PICTURES = "No part is shown as a picture."
NO_CHECKS = "No structure checks are stored for this file."
CHECKS_SCOPE = "They check structure, not whether the content is right."


def not_converted_sentence(s: CheckedSummary) -> str:
    """What stands between the file and "Ready to share" among its parts, said as each is
    shown: "2 equations are shown as images that screen readers cannot read." for a picture;
    "2 equations are shown as LaTeX text, not written so that screen readers can read them
    aloud." for an equation of a Word or LaTeX file, which has no picture ("" when none)."""
    c = s.converted
    said = []
    if s.images:
        verb = "is shown as an image" if s.images == 1 else "are shown as images"
        said.append(f"{kinds_phrase(c.pictures_by_kind)} {verb} that screen readers cannot read.")
    if s.written:
        verb, it = ("is", "it") if s.written == 1 else ("are", "them")
        said.append(
            f"{kinds_phrase(c.written_by_kind)} {verb} shown as LaTeX text, not written so that "
            f"screen readers can read {it} aloud."
        )
    return " ".join(said)


def left_out_owner(s: CheckedSummary) -> str:
    n = s.left_out
    blocks = s.left_out_blocks
    if blocks:
        words = f"{plural(blocks, 'paragraph or list item', 'paragraphs or list items')}"
        said = f"{words} {'was' if blocks == 1 else 'were'} left out."
    else:
        said = ""
    if not n:
        return said
    total = s.converted.pages
    if total and total >= n:
        pages = f"{n} of {plural(total, 'page')} {'was' if n == 1 else 'were'} left out."
    else:
        pages = f"{plural(n, 'page')} {'was' if n == 1 else 'were'} left out."
    return f"{pages} {said}".strip()


def cut_off_owner(s: CheckedSummary) -> str:
    """The owner's line for a PDF that looks cut off, once its question is answered ("" when
    it does not)."""
    pages = s.converted.cut_off_pages
    return cut_off_owner_pages(pages) if pages else ""


def cut_off_owner_pages(pages: tuple[int, ...]) -> str:
    """The same for the empty `pages` (1-based, consecutive)."""
    where = page_range(pages)
    return (
        f"{where[0].upper()}{where[1:]} of the PDF {'is' if len(pages) == 1 else 'are'} empty "
        "(the file looks cut off), so the rest is not in this file."
    )


def cut_off_question(pages: tuple[int, ...]) -> str:
    """The question the owner answers before a cut-off PDF is ready: `pages` are the empty
    ones (1-based, consecutive, to the end of the file)."""
    before = pages[0] - 1
    kept = "page 1" if before == 1 else f"pages 1 to {before}"
    verb = "is" if len(pages) == 1 else "are"
    return (
        f"This file looks cut off: {page_range(pages)} {verb} empty. Upload a fresh copy, or "
        f"continue with {kept}."
    )


def checks_failed_sentence(s: CheckedSummary) -> str:
    if not s.checks_failed:
        return ""
    n = len(s.checks_failed)
    return f"{n} of {s.checks_run} structure checks did not pass."


def remains(s: CheckedSummary) -> str:
    """What stands between a finished job with no open decision and "Ready to share" ("" when
    nothing does)."""
    parts = [
        not_converted_sentence(s),
        left_out_owner(s),
        cut_off_owner(s),
        unread_owner(s),
        checks_failed_sentence(s),
    ]
    return " ".join(p for p in parts if p)


def checks_line(s: CheckedSummary, open_decisions: int = 0) -> str:
    """The job page's line over its list of structure checks ("" when none are stored)."""
    if not s.checks_run:
        return ""
    if not s.checks_failed:
        return f"All {s.checks_run} structure checks passed. {CHECKS_SCOPE}"
    again = " (they run again after each review decision)" if open_decisions else ""
    return f"{len(s.checks_failed)} of {s.checks_run} structure checks did not pass{again}:"


def checks_ran(s: CheckedSummary) -> str:
    """What ran on this file, for "What was checked, and what was not"."""
    if not s.checks_run:
        return "No structure checks are stored."
    return f"{plural(s.checks_run, 'structure check')}."


def finish_sentence(s: CheckedSummary, *, owner: bool = True) -> str:
    """The finish line: what people reviewed (never the optional notes nobody opened) and
    when. The owner is told "You reviewed"; a reviewer, who need not have, is told what was."""
    if not s.reviewed:
        if not s.flagged:
            return "The tool flagged nothing in this file for review, and nobody has reviewed it."
        text = "Nobody has reviewed this file yet."
    else:
        when = f", the last on {s.reviewed_on:%B} {s.reviewed_on.day}" if s.reviewed_on else ""
        flagged = plural(s.flagged, "flagged part")
        text = (
            f"You reviewed {s.load.decided} of {flagged}{when}."
            if owner
            else f"{s.load.decided} of {flagged} reviewed{when}."
        )
        if k := s.accepted_failing:
            text += f" {plural(k, 'part')} kept with a check failing."
    if u := s.unopened:
        text += f" {plural(u, 'optional note')} not opened."
    return text


def card_equations(s: CheckedSummary) -> str:
    """The before/after card's equation row: how many of the equations found are MathML."""
    c = s.converted
    text = f"{c.equations_mathml} of {c.equations} are MathML, read with their structure"
    for n, shown in (
        (c.pictures_by_kind.get("equation", 0), "an image"),
        (c.written_by_kind.get("equation", 0), "LaTeX text"),
    ):
        if n:
            text += f"; {n} {'is' if n == 1 else 'are'} shown as {shown}"
    return f"{text}."


def notes_line(s: CheckedSummary) -> str:
    """The optional notes nobody opened, never counted as reviewed ("" when none)."""
    u = s.unopened
    if not u:
        return ""
    return (
        f"{plural(u, 'part')} {'was' if u == 1 else 'were'} accepted by the tool with an "
        "optional note nobody has opened, so not reviewed"
    )


NOTES_HOLD = "None holds up the file."
CHECKS_NOT_MEANING = (
    "the structure checks look at the structure of the file, not at whether each part says "
    "what you meant."
)
DID_NOT_RUN = (
    "the axe accessibility test (we run it on our own documents) and testing with a screen reader."
)


def card_yes(s: CheckedSummary) -> str:
    """The start of a before/after row about structure (headings, lists, links): "Yes" only
    when every part the tool found was converted; otherwise only the converted parts have it."""
    return "Yes" if s.all_found_converted else "In the converted parts"


# --------------------------------------------------------------------------- the record


def record_pictures(s: CheckedSummary) -> str:
    """The record's line when no part is listed as a picture: the section is headed "Parts
    shown as pictures", so what was left out, cut off or unread follows a sentence that says
    no part is one."""
    if s.all_found_converted:
        return READY_ALL
    rest = (left_out_owner(s), cut_off_owner(s), unread_owner(s))
    return " ".join(p for p in (RECORD_NO_PICTURES, *rest) if p)


def record_checks(s: CheckedSummary) -> str:
    if not s.checks_run:
        return NO_CHECKS
    head = (
        f"{s.checks_run} structure checks of the HTML file ran (language, title, order of "
        "headings, lists, links, tables and the alt attribute of every image)."
    )
    if s.checks_failed:
        return f"{head} {len(s.checks_failed)} did not pass:"
    return f"{head} All passed."


def record_not_reviewed(s: CheckedSummary) -> list[str]:
    """What people did not review, from the decisions: notes never opened, parts kept with a
    check failing."""
    lines = []
    if u := s.unopened:
        lines.append(
            f"{plural(u, 'optional note')} {'was' if u == 1 else 'were'} accepted by the tool "
            "and never opened by a person: not reviewed."
        )
    if k := s.accepted_failing:
        lines.append(
            f"{plural(k, 'part')} {'was' if k == 1 else 'were'} kept as the tool wrote "
            f"{'it' if k == 1 else 'them'} with a check failing (“Accept anyway”)."
        )
    return lines


# The CSV's rows for what people did, per decision (`core.triage.ACTIONS`).
ACTION_ROWS = {
    "open": "Decisions still open",
    "accepted": "Accepted as proposed by a person",
    "accepted_failing_check": "Accepted by a person with a check failing",
    "edited": "Edited by a person",
    "kept_as_image": "Kept as a picture by a person",
    "marked_decorative": "Marked as decoration by a person",
    "optional_unopened": "Optional notes never opened",
}

# --------------------------------------------------------------------------- exports

SUMMARY_TOOL = "Made by ReadableSTEM, a tool that assists with accessibility."
SUMMARY_UNKNOWN = f"{SUMMARY_TOOL} This file does not record what was converted or reviewed."
# Written to the person who checks a file, not to its readers: only a draft's page carries it
# (`assemble.render_html(audience="checker")`), never the student file or an export of it.
CHECK_BEFORE_USE = "Check it before students use it."


def export_summary(s: CheckedSummary, for_checker: bool = False) -> str:
    """The plain-text statement an export carries (the EPUB's accessibilitySummary, the
    Canvas README's first lines): the tool, how many equations a screen reader can read,
    and the student statement; for the checker (a draft) it ends with what to do."""
    c = s.converted
    parts = [SUMMARY_TOOL]
    if c.equations:
        verb = "is" if c.equations_mathml == 1 else "are"
        parts.append(f"{c.equations_mathml} of {plural(c.equations, 'equation')} {verb} MathML.")
    parts += statement(s)
    if for_checker:
        parts.append(CHECK_BEFORE_USE)
    return " ".join(parts)
