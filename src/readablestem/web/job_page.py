"""What the job page says beyond the bare status: live progress, an honest headline, the
before/after card, the notices list, the finish line and the document's language.

Everything here is built from data the page already has (the job row, its progress and
its stored result) by pure functions, so the routes and `views` stay thin and the
wording is tested in one place. Design notes:

- **Progress** (`stage_lines`, `stage_sentence`): the stored `ProgressEvent`s become a
  short list ("Equations: 14 of 21 read and checked against your PDF's text"). The
  status box, a polite live region, only says which step is running, so a screen
  reader hears a change per step, not per element; the counts are in a list outside it.
- **Headline** (`headline`, MF5 of the UX review): the label and sentence come from what
  is left to do. Precedence: an AI outage, a stop or scanned pages (`web.outcome`),
  then open decisions, then parts shown as images, then "Ready to share". A finished
  job is never just "Finished" while something waits for the instructor.
- **Counts** (`breakdown`, MF6): one breakdown everywhere. Every item is in exactly one
  of "waiting for your decision", "accepted for you" and "decided by you", so the three
  add up to the items; decisions are counted as on the review screen (a group of
  repeated headings is one).
- **Before/after card** (`before_after`): 5-7 rows "Can a screen-reader user ... | In
  your PDF | In the HTML" from `DocumentResult.source_facts` and the result, and up to
  four equations picked by the structure their text layer loses. The PDF side is
  specific about what is lost ("the fraction bar and the power are gone"); the text
  layer is never called unreadable, and there is no score. The equation text shown is
  document content: it is rendered for the owner only and never logged.
- **Notices** (`notice_lists`): `pipeline.notices.current` sorted into "Not handled in
  this file" and "Worth checking", one sentence each (`web.messages`), with the page
  and a link to the decision when a notice has one.
- **Language** (`language_line`, `language_choices`): the document language and where
  it came from, for the control that sets it. The names are the text work's
  (`pipeline.language.LANGUAGE_NAMES`); the change itself is
  `pipeline.text_pass.set_document_language` (lang_source "user").
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Literal

from readablestem.core import triage
from readablestem.core.mathchars import base_char, char_variant
from readablestem.core.types import (
    KEPT_ORIGINAL_STATUSES,
    DocumentResult,
    Element,
    Fix,
    Kind,
    Notice,
    SourceFacts,
    Status,
)
from readablestem.pipeline import language as languages
from readablestem.pipeline import notices as notice_rules
from readablestem.pipeline.source_facts import flattened_parts
from readablestem.pipeline.speech import GREEK, SYMBOLS
from readablestem.web import notice_places
from readablestem.web.messages import explain_notice
from readablestem.web.outcome import JobOutcome

# --------------------------------------------------------------------------- words


def plural(n: int, one: str, many: str | None = None) -> str:
    """ "1 equation", "3 equations"."""
    return f"{n} {one if n == 1 else (many or one + 's')}"


def join_words(parts: list[str]) -> str:
    """ "a", "a and b", "a, b and c"."""
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


# --------------------------------------------------------------------------- progress

StageState = Literal["done", "now"]
# Stages in the order they run (`core.types.Stage`, without "queued").
STAGE_ORDER = ("layout", "equations", "figures", "tables", "assemble", "checks")
# The status box's sentence while a stage runs: one per step, so the live region
# announces a change per step.
STAGE_SENTENCES = {
    "layout": "Reading the layout of your PDF.",
    "equations": "Reading the equations and checking each against your PDF's text.",
    "figures": "Describing the figures.",
    "tables": "Looking at the tables.",
    "assemble": "Building the HTML file.",
    "checks": "Running the structure checks on the HTML file.",
}
FOUND_KINDS = (
    ("equation", "equation", "equations"),
    ("figure", "figure", "figures"),
    ("table", "table", "tables"),
    ("heading", "heading", "headings"),
    ("list_item", "list item", "list items"),
)


@dataclass(frozen=True)
class StageLine:
    text: str
    state: StageState


def _events(progress: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """The stored events by stage (`web.worker` writes `{"stages": {...}}`); anything
    else is no progress (an older row, a broken value)."""
    if not isinstance(progress, dict) or not isinstance(progress.get("stages"), dict):
        return {}
    return {
        stage: event
        for stage, event in progress["stages"].items()
        if stage in STAGE_ORDER and isinstance(event, dict)
    }


def _count(event: dict[str, Any], key: str) -> int:
    value = event.get(key)
    return value if isinstance(value, int) and value >= 0 else 0


def _finished(event: dict[str, Any]) -> bool:
    total = _count(event, "total")
    return total > 0 and _count(event, "done") >= total


def current_stage(progress: dict[str, Any] | None) -> str | None:
    """The step the conversion is on: the first started stage that is not finished (the
    equations and figures run side by side), else the last one started."""
    events = _events(progress)
    started = [s for s in STAGE_ORDER if s in events]
    if not started:
        return None
    last = started[-1]
    for stage in started:
        if stage in ("assemble", "checks"):
            continue
        if not _finished(events[stage]) and stage != last:
            return stage
    return last


def stage_sentence(progress: dict[str, Any] | None) -> str:
    stage = current_stage(progress)
    return STAGE_SENTENCES.get(stage or "", "Starting the conversion.")


def stage_lines(progress: dict[str, Any] | None) -> list[StageLine]:
    """The steps so far, with their counts, for the list beside the status box."""
    events = _events(progress)
    now = current_stage(progress)
    lines: list[StageLine] = []
    layout = events.get("layout")
    if layout is not None:
        pages = _count(layout, "total")
        read = "Reading the layout" + (f": {plural(pages, 'page')}" if pages else "")
        lines.append(StageLine(read, "done" if _finished(layout) else "now"))
        counts = layout.get("counts") if isinstance(layout.get("counts"), dict) else {}
        found = [
            plural(n, one, many)
            for key, one, many in FOUND_KINDS
            if (n := _count(counts or {}, key))
        ]
        if _finished(layout):
            text = f"Found {join_words(found)}" if found else "Found no equations or figures"
            lines.append(StageLine(text, "done"))
    for stage, verb in (
        ("equations", "read and checked against your PDF's text"),
        ("figures", "described"),
        ("tables", "looked at"),
    ):
        event = events.get(stage)
        if event is not None and _count(event, "total"):
            done, total = _count(event, "done"), _count(event, "total")
            text = f"{stage.capitalize()}: {done} of {total} {verb}"
            lines.append(StageLine(text, "done" if _finished(event) else "now"))
    for stage, text in (
        ("assemble", "Building the HTML file"),
        ("checks", "Running the structure checks"),
    ):
        if stage in events:
            lines.append(StageLine(text, "now" if stage == now else "done"))
    return lines


def wait_words(seconds: float | None) -> str | None:
    """ "less than a minute", "about 1 minute", "about 12 minutes"; None when unknown."""
    if seconds is None or seconds < 0:
        return None
    minutes = round(seconds / 60)
    if minutes < 1:
        return "less than a minute"
    return f"about {plural(minutes, 'minute')}"


def queue_sentence(ahead: int, yours: int, seconds: float | None) -> str:
    """The waiting status (MF17): place, an estimate, and that leaving is fine."""
    if ahead == 0:
        place = "Your document is next in line"
    else:
        place = f"{plural(ahead, 'document')} ahead of yours"
        if yours:
            place += f" ({yours} {'is' if yours == 1 else 'are'} yours)"
    wait = wait_words(seconds)
    estimate = f", {wait} to go" if wait else ""
    return f"{place}{estimate}. You can leave this page; it keeps its place."


# --------------------------------------------------------------------------- counts


@dataclass
class Breakdown:
    """Every item (fix) in exactly one of three places, plus what is shown as an image.

    `waiting + accepted + decided == items`. `decisions` counts the waiting items the way
    the review screen does (a group of repeated headings is one decision)."""

    items: int = 0
    waiting: int = 0  # items waiting for a decision
    decisions: int = 0  # ... as decisions
    accepted: int = 0  # accepted for you (the pipeline's own acceptance)
    notes: int = 0  # ... of them with a note nobody has acted on
    decided: int = 0  # accepted, changed or kept as an image by the instructor
    decided_units: int = 0  # ... as decisions
    as_image: int = 0  # shown as an image of the original (not converted)
    as_image_kinds: dict[str, int] = field(default_factory=dict)
    # Waiting decisions that hold more than one item: repeated headings, and pages (or
    # handwriting) kept as images for one reason.
    heading_groups: int = 0
    page_groups: int = 0

    @property
    def grouped_reason(self) -> str:
        """Why the waiting items make fewer decisions than there are items."""
        reasons = []
        if self.heading_groups:
            reasons.append("repeated headings are decided together")
        if self.page_groups:
            reasons.append("pages kept as images for the same reason are decided together")
        return "; ".join(reasons)

    @property
    def as_image_sentence(self) -> str:
        """ "2 pages are shown as images of the original." ("" when none)."""
        if not self.as_image:
            return ""
        verb = "is shown as an image" if self.as_image == 1 else "are shown as images"
        return f"{kinds_phrase(self.as_image_kinds)} {verb} of the original."


def shown_as_image(fix: Fix) -> bool:
    """The original is kept as an image in the HTML file (T1-3). Not a heading: "Make bold
    text" rejects a heading too (`Status.REJECTED`), but what the student file gets is the
    heading's text as bold text, which a screen reader reads."""
    # An image the instructor described in words (T3-16) is read out: not a silent image.
    return (
        fix.kind is not Kind.HEADING
        and fix.status in KEPT_ORIGINAL_STATUSES
        and not (fix.described or "").strip()
    )


def breakdown(fixes: list[Fix]) -> Breakdown:
    waiting = [f for f in fixes if f.pending]
    decided = [f for f in fixes if not f.pending and f.decided_by_reviewer]
    accepted = [f for f in fixes if not f.pending and not f.decided_by_reviewer]
    images = [f for f in fixes if shown_as_image(f)]
    grouped = [unit[0] for unit in triage.units(waiting) if len(unit) > 1]
    return Breakdown(
        items=len(fixes),
        waiting=len(waiting),
        decisions=triage.open_decisions(fixes),
        accepted=len(accepted),
        notes=sum(f.optional_note for f in fixes),
        decided=len(decided),
        decided_units=len(triage.units(decided)),
        as_image=len(images),
        as_image_kinds=dict(Counter(word_kind(f) for f in images)),
        heading_groups=sum(f.kind is Kind.HEADING for f in grouped),
        page_groups=sum(word_kind(f) in ("page", "handwriting") for f in grouped),
    )


KIND_WORDS = {
    "equation": ("equation", "equations"),
    "figure": ("figure", "figures"),
    "table": ("table", "tables"),
    "heading": ("heading", "headings"),
    # Pages and handwriting kept as images (pipeline.page_type) are figures in the data
    # model, but not in words.
    "page": ("page", "pages"),
    "handwriting": ("piece of handwriting", "pieces of handwriting"),
}


def word_kind(fix: Fix) -> str:
    """The kind a fix is named by: "page" or "handwriting" for one kept as an image
    before any model work, else its kind."""
    if fix.kind is Kind.FIGURE and fix.reason in notice_rules.KEPT_IMAGE_CODES:
        return "handwriting" if fix.reason == "handwriting_on_page" else "page"
    return fix.kind.value


def kinds_phrase(counts: dict[str, int]) -> str:
    """ "1 equation and 2 figures" (in a fixed order)."""
    parts = [
        plural(counts[kind], one, many)
        for kind, (one, many) in KIND_WORDS.items()
        if counts.get(kind)
    ]
    return join_words(parts) or plural(sum(counts.values()), "part")


# --------------------------------------------------------------------------- headline

Badge = Literal["done", "running", "failed", "queued"]
# Outcome codes after which the instructor should convert again, not share.
STOPPED = frozenset({"budget_daily", "budget_job_usd", "budget_job_calls", "model_outage"})


@dataclass(frozen=True)
class Headline:
    """The status box: a short label, one sentence, and what kind of state it is."""

    label: str
    message: str
    badge: Badge
    state: str  # "queued", "running", "failed", "stopped", "decisions", "images", "ready"


def headline(
    status: str, detail: str, outcome: JobOutcome | None, counts: Breakdown | None
) -> Headline:
    """The status box for any job: `detail` is the plain status sentence
    (`views.JobView.status_detail`), `outcome` the job's outcome (`web.outcome`), `counts`
    a finished job's breakdown."""
    if status == "queued":
        if outcome is not None:
            return Headline(outcome.label, outcome.message, "queued", "queued")
        return Headline("Waiting", detail, "queued", "queued")
    if status == "running":
        return Headline("Converting", detail, "running", "running")
    if status != "done":
        message = outcome.message if outcome is not None else detail
        return Headline("Stopped", message, "failed", "failed")
    if outcome is not None:
        state = "stopped" if outcome.code in STOPPED else "images"
        return Headline(outcome.label, outcome.message, "failed", state)
    counts = counts or Breakdown()
    if counts.decisions:
        need = "needs" if counts.decisions == 1 else "need"
        message = f"{plural(counts.decisions, 'decision')} {need} you before you share it."
        return Headline("Converted", message, "running", "decisions")
    if counts.as_image:
        verb = "is" if counts.as_image == 1 else "are"
        message = (
            f"{kinds_phrase(counts.as_image_kinds)} {verb} shown as an image that screen "
            "readers cannot read."
        )
        return Headline("Ready", message, "done", "images")
    return Headline("Ready to share", "Everything was converted.", "done", "ready")


# --------------------------------------------------------------------------- before/after


@dataclass(frozen=True)
class Row:
    question: str  # completes "Can a screen-reader user ..."
    pdf: str
    html: str
    # A link from the HTML cell to the notice that explains it (T1-15): the section it is
    # listed in on the same page, and the words of the link.
    href: str = ""
    href_text: str = ""


@dataclass(frozen=True)
class Example:
    element_id: str
    title: str
    pdf_reading: str  # document content: for the owner's eyes only, never logged
    html_reading: str
    lost: str


@dataclass(frozen=True)
class Card:
    rows: list[Row]
    examples: list[Example]
    # A tagged PDF may give a screen reader more than its text layer (alt text on a
    # formula, say), so the examples then say "text layer", not what a reader gets.
    tagged: bool = False


MAX_ROWS = 7
MAX_EXAMPLES = 4
MAX_READING_CHARS = 300
# What one line of text loses, per structure code (`source_facts.STRUCTURE_PARTS`).
LOST_WORDS = {
    "fraction": "the fraction bar",
    "root": "where the root ends",
    "power": "the power",
    "superscript": "the raised part",
    "subscript": "the subscript",
    "limits": "the limits above and below",
    "rows": "the rows and columns",
    "accent": "the mark over a letter",
    "bold": "the bold of the vectors",
}
# Words for the PDF row about equations: what the document's text layer loses.
DOC_LOST_WORDS = {
    "fraction": "fraction bars",
    "root": "roots",
    "power": "powers",
    "superscript": "raised parts",
    "subscript": "subscripts",
    "limits": "limits",
    "rows": "rows and columns",
    "accent": "marks over letters",
}
_BOLD_VARIANTS = frozenset({"bold", "bold-italic"})
# Characters a screen reader names differently in running text than the spoken reading
# of maths does (`speech.SYMBOLS`): a slash in "m/s" is read as "slash", not "divided by".
TEXT_SYMBOLS = {"/": "slash"}


def pdf_reading(text: str) -> str:
    """An equation's text layer written out the way a screen reader gets it from a PDF:
    one flat line. A run of letters is one word, digits one number, Greek letters and
    symbols are named, and a styled maths letter (bold d) is the plain letter the text
    layer gives. Approximate: screen readers differ in what they say for some symbols."""
    words: list[str] = []
    word = ""
    chars = [base_char(ch) for ch in text]
    for i, ch in enumerate(chars):
        if ch.isascii() and ch.isalnum():
            if word and (word[-1].isdigit() or word[-1] == ".") != ch.isdigit():
                words.append(word)
                word = ""
            word += ch
            continue
        if ch == "." and word.isdigit() and i + 1 < len(chars) and chars[i + 1].isdigit():
            word += ch  # a decimal point
            continue
        if word:
            words.append(word)
            word = ""
        if ch.isspace():
            continue
        if ch in GREEK:
            words.append(GREEK[ch])
        elif ch in TEXT_SYMBOLS:
            words.append(TEXT_SYMBOLS[ch])
        elif ch in SYMBOLS:
            words.append(SYMBOLS[ch])
        elif (name := unicodedata.name(ch, "")).startswith(("SUPERSCRIPT ", "SUBSCRIPT ")):
            words.append(name.lower().replace(" digit", ""))
        else:
            words.append(ch)
    if word:
        words.append(word)
    reading = " ".join(words)
    return reading if len(reading) <= MAX_READING_CHARS else reading[:MAX_READING_CHARS] + " ..."


def _used(fix: Fix | None) -> bool:
    return fix is not None and fix.status not in KEPT_ORIGINAL_STATUSES


def _bold(text: str) -> bool:
    return any(char_variant(ch) in _BOLD_VARIANTS for ch in text)


def _bold_lost(element: Element, fix: Fix) -> bool:
    """Bold letters in the maths, which the text-layer reading drops (`pdf_reading`)."""
    mathml = fix.equation.mathml if fix.equation is not None else ""
    return _bold(element.text or "") and ('mathvariant="bold' in mathml or _bold(mathml))


# Structure codes whose words are plural ("the rows and columns are gone").
PLURAL_LOST = frozenset({"rows", "limits"})


def _lost_sentence(parts: list[str]) -> str:
    known = [p for p in parts if p in LOST_WORDS]
    words = [LOST_WORDS[p] for p in known]
    verb = "is" if len(words) == 1 and known[0] not in PLURAL_LOST else "are"
    sentence = f"{join_words(words)} {verb} gone."
    return sentence[0].upper() + sentence[1:]


def _examples(result: DocumentResult) -> list[Example]:
    """Up to four equations whose text layer loses the most, display ones first."""
    fixes = _first_fixes(result)
    scored: list[tuple[int, int, int, Example]] = []
    for element in result.elements:
        if element.kind is not Kind.EQUATION or not (element.text or "").strip():
            continue
        fix = fixes.get(element.id)
        if fix is None or not _used(fix) or fix.equation is None or not fix.equation.speech:
            continue
        parts = flattened_parts(element, fix)
        if _bold_lost(element, fix):
            parts = [*parts, "bold"]
        if not parts:
            continue
        where = f"page {element.page + 1}"
        title = (
            f"Equation {element.equation_label}, {where}"
            if element.equation_label
            else f"Equation on {where}"
        )
        example = Example(
            element_id=element.id,
            title=title,
            pdf_reading=pdf_reading(element.text or ""),
            html_reading=fix.equation.speech,
            lost=_lost_sentence(parts),
        )
        block = 1 if element.display == "block" else 0
        scored.append((len(parts), block, -element.order, example))
    scored.sort(key=lambda item: item[:3], reverse=True)
    # Each kind of loss once before any is shown twice: four equations that all lose
    # only a subscript say less than one each that loses something else.
    ranked = [example for *_, example in scored]
    seen: set[str] = set()
    varied = []
    for example in ranked:
        if example.lost not in seen:
            seen.add(example.lost)
            varied.append(example)
    chosen = {example.element_id for example in varied}
    varied += [example for example in ranked if example.element_id not in chosen]
    return varied[:MAX_EXAMPLES]


def _first_fixes(result: DocumentResult) -> dict[str, Fix]:
    first: dict[str, Fix] = {}
    for fix in result.fixes:
        first.setdefault(fix.element_id, fix)
    return first


def _doc_lost(result: DocumentResult) -> list[str]:
    fixes = _first_fixes(result)
    found: set[str] = set()
    for element in result.elements:
        if element.kind is Kind.EQUATION:
            found.update(flattened_parts(element, fixes.get(element.id)))
    return [DOC_LOST_WORDS[p] for p in DOC_LOST_WORDS if p in found]


def _equation_row(facts: SourceFacts, result: DocumentResult) -> Row:
    n, flat = facts.equations, facts.equations_flattened
    if flat:
        lost = join_words(_doc_lost(result)) or "their layout"
        pdf = (
            f"The symbols are there, on one line. In {flat} of {n}, the {lost} "
            f"{'is' if lost == 'their layout' else 'are'} gone."
        )
    else:
        pdf = "The symbols are there, on one line; none of these has a fraction or script."
    equations = [f for f in _first_fixes(result).values() if f.kind is Kind.EQUATION]
    maths = sum(1 for f in equations if _used(f) and f.equation is not None)
    images = sum(1 for f in equations if f.status in KEPT_ORIGINAL_STATUSES)
    html = f"{maths} of {len(equations) or n} are MathML, read with their structure"
    html += f"; {images} {'is' if images == 1 else 'are'} shown as an image." if images else "."
    if facts.tagged:
        pdf = "Your PDF is tagged; we did not check how its tags give the maths. " + pdf
    return Row("hear equations with their structure", pdf, html)


def _figure_row(facts: SourceFacts, result: DocumentResult) -> Row | None:
    figures = [
        f
        for f in _first_fixes(result).values()
        if f.kind is Kind.FIGURE and f.reason not in notice_rules.KEPT_IMAGE_CODES
    ]
    if not figures and not facts.images:
        return None
    if facts.images_with_alt is not None:
        have = "has" if facts.images_with_alt == 1 else "have"
        pdf = f"{facts.images_with_alt} of {plural(facts.images, 'picture')} {have} alt text."
    elif facts.images:
        pdf = (
            f"No: {plural(facts.images, 'picture')}, none with a description. A screen "
            "reader skips them or says only “graphic”."
        )
    else:
        pdf = "No: drawings in a PDF have no description."
    described = sum(
        1 for f in figures if _used(f) and f.figure is not None and not f.figure.decorative
    )
    decorative = sum(
        1 for f in figures if _used(f) and f.figure is not None and f.figure.decorative
    )
    have = "has" if described == 1 else "have"
    html = f"{described} of {plural(len(figures), 'figure')} {have} a short description"
    if decorative:
        html += f"; {decorative} {'is' if decorative == 1 else 'are'} marked as decoration"
    waiting = sum(1 for f in figures if f.pending)
    if waiting:
        html += f"; {waiting} {'waits' if waiting == 1 else 'wait'} for your decision"
    return Row("hear what a figure shows", pdf, html + ".")


def _heading_row(facts: SourceFacts, result: DocumentResult) -> Row:
    headings = sum(1 for f in _first_fixes(result).values() if f.kind is Kind.HEADING and _used(f))
    if facts.tagged:
        pdf = "Perhaps: your PDF is tagged, so it may mark headings (we did not check)."
    else:
        pdf = "No: nothing marks a heading"
        pdf += f", though it has {plural(facts.bookmarks, 'bookmark')}." if facts.bookmarks else "."
    html = (
        f"Yes: {plural(headings, 'heading')} in one outline." if headings else "No headings found."
    )
    return Row("jump from section to section", pdf, html)


def _list_row(facts: SourceFacts, result: DocumentResult) -> Row | None:
    ordered = sorted(result.elements, key=lambda e: e.order)
    items = [e for e in ordered if e.kind is Kind.LIST_ITEM]
    if not items:
        return None
    lists = sum(
        1
        for i, e in enumerate(ordered)
        if e.kind is Kind.LIST_ITEM and (i == 0 or ordered[i - 1].kind is not Kind.LIST_ITEM)
    )
    if facts.tagged:
        pdf = "Perhaps: your PDF is tagged, so it may mark lists (we did not check)."
    else:
        pdf = "No: bullets and numbers are read as characters; nothing says “list, 3 items”."
    html = f"Yes: {plural(lists, 'list')} with {plural(len(items), 'item')}."
    return Row("hear lists as lists", pdf, html)


def _table_row(facts: SourceFacts, result: DocumentResult) -> Row | None:
    tables = [f for f in _first_fixes(result).values() if f.kind is Kind.TABLE]
    if not facts.tables and not tables:
        return None
    if facts.tagged:
        pdf = "Perhaps: your PDF is tagged, so it may mark tables (we did not check)."
    else:
        pdf = "No: a table is read as a run of words, with no headers."
    rebuilt = sum(1 for f in tables if _used(f) and f.table is not None and f.table.grid.is_data)
    as_text = sum(
        1 for f in tables if _used(f) and f.table is not None and not f.table.grid.is_data
    )
    images = sum(1 for f in tables if f.status in KEPT_ORIGINAL_STATUSES)
    html = f"{rebuilt} of {plural(len(tables) or facts.tables, 'table')} rebuilt as data tables"
    if as_text:  # the model judged them not tables of data: their text, in reading order
        html += f"; {as_text} written as text, not a table"
    if images:
        html += f"; {images} {'is' if images == 1 else 'are'} shown as an image"
    waiting = sum(1 for f in tables if f.pending)
    if waiting:  # as for figures: a proposal still waiting is not settled yet
        html += f"; {waiting} {'waits' if waiting == 1 else 'wait'} for your decision"
    return Row("read a table cell by cell", pdf, html + ".")


def link_counts(facts: SourceFacts, result: DocumentResult) -> tuple[int, int, int]:
    """(links in the PDF, links kept in the HTML, links dropped because they were not web
    or email addresses): what the link row of the card says (T1-15)."""
    kept = sum(1 for e in result.elements for link in e.links if link.kind in ("uri", "internal"))
    dropped = sum(n.count for n in result.notices if n.code == "link_scheme_dropped")
    return facts.links, kept, dropped


def _link_row(facts: SourceFacts, result: DocumentResult) -> Row | None:
    if not facts.links:
        return None
    total, kept, dropped = link_counts(facts, result)
    pdf = f"Yes: {plural(total, 'link')}."
    if kept >= total:
        return Row("follow the links", pdf, f"Yes: {plural(kept, 'link')}.")
    html = f"{kept} of {plural(total, 'link')} kept"
    if not dropped:
        return Row("follow the links", pdf, html + ".")
    were = "was" if dropped == 1 else "were"
    html += f"; {dropped} {were} not web addresses."
    notice = next(
        (n for n in notice_rules.current(result) if n.code == "link_scheme_dropped"), None
    )
    section = notice_rules.section(notice) if notice is not None else "not_handled"
    href = "#worth-checking-heading" if section == "worth_checking" else "#not-handled-heading"
    return Row("follow the links", pdf, html, href, "See the notice")


def _title_row(facts: SourceFacts, result: DocumentResult) -> Row:
    pdf = "Yes: the PDF has a title." if facts.has_title else "No title: the file name is read."
    title = (result.title or "").strip()
    html = f"Yes: “{title}”." if title else "Yes, a general one: “Converted document”."
    return Row("know which document is open", pdf, html)


def _language_row(facts: SourceFacts, result: DocumentResult) -> Row:
    pdf = (
        "Yes: your PDF says which language it is in."
        if facts.has_lang
        else "No: the PDF does not say, so the screen reader guesses."
    )
    # A detected or assumed language is a guess, so the HTML side does not say "Yes".
    if language_needs_check(result):
        html = f"{language_line(result)}: check it under Language below."
    else:
        html = f"Yes: {language_line(result)}."
    return Row("hear the right language", pdf, html)


def before_after(result: DocumentResult) -> Card | None:
    """The card, or None for results measured before source facts existed."""
    facts = result.source_facts
    if facts is None:
        return None
    rows: list[Row | None] = []
    if facts.equations:
        rows.append(_equation_row(facts, result))
    if facts.bold_maths_letters:
        rows.append(
            Row(
                "tell a bold vector from a plain letter",
                f"No: {plural(facts.bold_maths_letters, 'bold letter')} in the maths "
                f"{'is' if facts.bold_maths_letters == 1 else 'are'} stored as plain "
                "letters, so a vector reads like any other letter.",
                "Yes: bold letters are written as bold maths. A letter the tool read "
                "differently from your PDF is one of your decisions.",
            )
        )
    rows += [
        _figure_row(facts, result),
        _heading_row(facts, result),
        _list_row(facts, result),
        _table_row(facts, result),
        _link_row(facts, result),
    ]
    # Past seven rows the last ones go: a language to check comes before the title.
    tail = [_title_row(facts, result), _language_row(facts, result)]
    rows += tail[::-1] if language_needs_check(result) else tail
    kept = [row for row in rows if row is not None][:MAX_ROWS]
    return Card(kept, _examples(result), tagged=bool(facts.tagged))


# --------------------------------------------------------------------------- notices


@dataclass(frozen=True)
class NoticeLine:
    where: str  # "Page 3" or "The whole document"
    text: str  # what it means (`web.messages.NOTICE_MESSAGES`)
    code: str
    count: int
    element_id: str | None
    decision: Literal["open", "made"] | None  # the fix that is its decision, if any
    label: str = ""  # that decision's item ("Table 2"), so two "Decide it" links differ
    # A page kept as an image whose decision is its group's, listed with its first page:
    # that page's name ("page 2"), and no "Decide it" of its own.
    with_label: str = ""
    # T3-7: the link, figure or table it is about (document content, for the owner's page
    # only), a few words of the paragraph it is in, what that is ("The link text"), and
    # the review screen address of its place (the query after `/jobs/{id}/review?`, "" for
    # the whole document).
    names: tuple[str, ...] = ()
    snippet: str = ""
    lead: str = ""
    place: str = ""


# Notices whose element's fix is the decision they describe (a table kept as an image, a
# page or a region of handwriting kept as an image). Any other notice on an element with
# a decision (a figure telling things apart by colour alone) is about something else: it
# gets no "Decide it".
DECISION_NOTICES = frozenset({"table_not_rebuilt"}) | notice_rules.KEPT_IMAGE_CODES


@dataclass
class NoticeLists:
    not_handled: list[NoticeLine] = field(default_factory=list)
    worth_checking: list[NoticeLine] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.not_handled) + len(self.worth_checking)


def _line(
    notice: Notice,
    fixes: dict[str, Fix],
    labels: dict[str, str],
    elements: dict[str, Element] | None = None,
) -> NoticeLine:
    fix = fixes.get(notice.element_id or "")
    decision: Literal["open", "made"] | None = None
    label = ""
    if fix is not None and fix.review == "required" and notice.code in DECISION_NOTICES:
        decision = "open" if fix.pending else "made"
        label = labels.get(fix.element_id, "")
    where = f"Page {notice.page + 1}" if notice.page is not None else "The whole document"
    text, count = explain_notice(notice).text, notice.count
    if "{count}" in text:  # the count is in the sentence (characters), not "N times"
        text, count = text.replace("{count}", f"{notice.count:,}"), 1
    found = notice_places.place_of(notice, elements or {}, fixes, labels)
    return NoticeLine(
        where,
        text,
        notice.code,
        count,
        notice.element_id,
        decision,
        label,
        names=found.names,
        snippet=found.snippet,
        lead=found.lead,
        place=found.address,
    )


def notice_lists(result: DocumentResult) -> NoticeLists:
    """The notices as the job page lists them now (see `pipeline.notices.current`)."""
    from readablestem.web.review import item_labels

    fixes = _first_fixes(result)
    labels = item_labels(result)
    elements = {e.id: e for e in result.elements}
    lists = NoticeLists()
    for notice in notice_rules.current(result):
        line = _line(notice, fixes, labels, elements)
        if notice_rules.section(notice) == "worth_checking":
            lists.worth_checking.append(line)
        else:
            lists.not_handled.append(line)
    lists.not_handled = _one_line_per_kept_page(lists.not_handled, fixes, labels)
    return lists


def _one_line_per_kept_page(
    lines: list[NoticeLine], fixes: dict[str, Fix], labels: dict[str, str]
) -> list[NoticeLine]:
    """A page kept as an image says it once: its hidden text that was not used joins its
    line, and the pages kept for one reason have one "Decide it", on the first of them
    (the others say they are decided with it)."""
    kept_codes = notice_rules.KEPT_IMAGE_CODES
    kept_at = {line.where: i for i, line in enumerate(lines) if line.code in kept_codes}
    hidden: dict[int, str] = {}  # index of a kept page's line -> its hidden-text sentence
    for line in lines:
        if line.code == "ocr_text_layer_ignored" and line.where in kept_at:
            hidden[kept_at[line.where]] = line.text
    first: dict[str, str] = {}  # group key -> the name of its first page ("page 2")
    out: list[NoticeLine] = []
    for i, line in enumerate(lines):
        if line.code == "ocr_text_layer_ignored" and line.where in kept_at:
            continue  # said on the page's own line
        if i in hidden:
            line = replace(line, text=f"{line.text} {hidden[i]}")
        fix = fixes.get(line.element_id or "")
        key = fix.group_key if fix is not None and line.code in kept_codes else None
        if key and key in first and line.decision is not None:
            line = replace(line, decision=None, with_label=first[key])
        elif key:
            name = labels.get(line.element_id or "", "")
            first[key] = name[:1].lower() + name[1:]
        out.append(line)
    return out


# --------------------------------------------------------------------------- language

# Notices that ask the instructor to confirm the language; confirming answers them.
LANGUAGE_NOTICES = frozenset({"lang_detected", "lang_assumed"})
SOURCE_WORDS = {
    "pdf": "from your PDF",
    "detected": "detected; your PDF did not say",
    "default": "assumed; your PDF did not say and none was detected",
    "user": "you chose it",
}


def language_label(tag: str) -> str:
    """ "English" for "en", "Portuguese (pt-BR)" for a tag with a region, the tag itself
    for a language the control does not list (`pipeline.language.LANGUAGE_NAMES`, the
    one list of names)."""
    if tag in languages.LANGUAGE_NAMES:
        return languages.LANGUAGE_NAMES[tag]
    name = languages.language_name(tag)
    return f"{name} ({tag})" if name != tag else tag


def language_source(result: DocumentResult) -> str | None:
    """Where the language came from, as far as the result says. A result made before
    the language was detected has "default" whatever the PDF said: the PDF's own /Lang
    (`SourceFacts.has_lang`) means it came from the PDF, and without source facts it is
    not known."""
    if result.lang_source != "default":
        return result.lang_source
    if result.source_facts is None:
        return None
    return "pdf" if result.source_facts.has_lang else "default"


def language_line(result: DocumentResult) -> str:
    """ "English (detected; your PDF did not say)"."""
    words = SOURCE_WORDS.get(language_source(result) or "", "")
    name = language_label(result.lang)
    return f"{name} ({words})" if words else name


def language_needs_check(result: DocumentResult) -> bool:
    return language_source(result) in ("detected", "default")


def language_choices(current: str) -> list[tuple[str, str]]:
    """(tag, name) for the select in alphabetical order, the current language first if
    it is not listed."""
    choices = sorted(languages.LANGUAGE_NAMES.items(), key=lambda item: item[1])
    if current not in languages.LANGUAGE_NAMES:
        choices.insert(0, (current, language_label(current)))
    return choices


# --------------------------------------------------------------------------- finish line


@dataclass(frozen=True)
class Finish:
    """The "Ready to share" block (BW1 of the UX review), shown once no decision is open."""

    decided_units: int
    last_decision: date | None
    notes: int
    not_handled: int

    @property
    def sentence(self) -> str:
        return finish_sentence(self)


def finish_sentence(finish: Finish) -> str:
    if not finish.decided_units:
        return "Nothing in this file needed your decision."
    when = (
        f" on {finish.last_decision.day} {finish.last_decision:%B}" if finish.last_decision else ""
    )
    return f"You made {plural(finish.decided_units, 'decision')}{when}."


def draft_sentence(fixes: list[Fix]) -> str:
    """What students get when a draft is downloaded with decisions open."""
    proposals = [f for f in fixes if f.status is Status.NEEDS_REVIEW]
    images = [f for f in fixes if f.pending and f.status is not Status.NEEDS_REVIEW]
    sentences = []
    if proposals:
        kinds = kinds_phrase(dict(Counter(f.kind.value for f in proposals)))
        sentences.append(f"For {kinds}, students will get the tool's suggestion as it is.")
    if images:
        kinds = kinds_phrase(dict(Counter(word_kind(f) for f in images)))
        verb, shown = ("is", "an image") if len(images) == 1 else ("are", "images")
        sentences.append(f"{kinds[0].upper()}{kinds[1:]} {verb} {shown} marked “Not converted”.")
    return " ".join(sentences)


# --------------------------------------------------------------------------- the page


@dataclass
class PageData:
    """Everything the job page shows from a finished job's result."""

    counts: Breakdown
    found: str  # "Found 19 equations, 7 figures and 36 headings on 13 pages."
    card: Card | None
    notices: NoticeLists
    language: str  # "English (detected; your PDF did not say)"
    lang: str
    language_check: bool  # detected or assumed: worth confirming
    language_choices: list[tuple[str, str]]
    first_open: str | None  # the review screen's first open decision (a key)
    finish: Finish
    draft: str  # what students get from a draft download
    # The title the file has without the instructor's choice (T1-7): the field's start.
    title_suggested: str = ""


def suggested_title(result: DocumentResult) -> str:
    """The title students hear when the instructor has not chosen one (T1-7): what the HTML
    file's `<title>` is, so the field starts with what the file already says."""
    from readablestem.pipeline import assemble

    first = _first_fixes(result)
    headings = [
        text
        for element in sorted(result.elements, key=lambda e: e.order)
        if element.kind is Kind.HEADING
        for level, text in [assemble.heading_parts(element, first.get(element.id))]
        if level is not None
    ]
    return assemble.suggested_title(result.title, headings)


def found_sentence(result: DocumentResult) -> str:
    """ "Found 3 equations and 2 figures on 4 pages." Pages kept as images are not
    "found": the outcome says which they are."""
    kinds = Counter(
        word_kind(f) for f in _first_fixes(result).values() if word_kind(f) == f.kind.value
    )
    pages = plural(result.stats.pages, "page")
    found = kinds_phrase(dict(kinds)) if any(kinds.get(k) for k in KIND_WORDS) else ""
    return f"Found {found} on {pages}." if found else f"Read {pages}."


def page_data(result: DocumentResult, last_decision: date | None) -> PageData:
    from readablestem.web import queue

    counts = breakdown(result.fixes)
    notices = notice_lists(result)
    first = queue.first_open(queue.decisions(result))
    return PageData(
        counts=counts,
        found=found_sentence(result),
        card=before_after(result),
        notices=notices,
        language=language_line(result),
        lang=result.lang,
        language_check=language_needs_check(result),
        language_choices=language_choices(result.lang),
        first_open=first.key if first is not None else None,
        finish=Finish(
            decided_units=counts.decided_units,
            last_decision=last_decision,
            notes=counts.notes,
            not_handled=len(notices.not_handled),
        ),
        draft=draft_sentence(result.fixes),
        title_suggested=suggested_title(result),
    )
