"""Who receives what a person uploads: the one list the public pages are written from.

`recipients(settings)` is built from the settings, so the privacy page, the account deletion
notes and the launch checks (`ops.launch`) cannot disagree about it: the model provider
(`MODEL_PROVIDER`), each handwriting reader that is on (`MATH_READER_*_ENABLED`; the reader's
company may or may not be the model provider), the host, and tracing when it is on.
`privacy_context` is what the /privacy route renders; `notice_text` renders that same page for
the launch checks and strips it to text, so they test what a visitor would read.

Adding a recipient here (a new provider, a new reader) changes the page; the privacy template
loops over this list and names every entry. Provider-specific retention wording lives in
`templates/_recipients.html`, keyed by the recipient's name.

What each recipient receives is the second list, `sent_material(settings)`: one row per kind
of material (`MATERIALS`), tied to the settings that send it, with what is kept from the
answer and the day the row was first stated in public. The privacy page, the vendor line
(`vendor_sentence`), the offices page, its summary and drawing and the account deletion notes
are written from it, and a production start is refused while a row that is on is not stated
on the rendered page or has no date (`ops.launch.sent_material_problems`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jinja2

from readablestem.web import office_convert, reports
from readablestem.web.dates import format_day
from readablestem.web.events import (
    EVENT_RETENTION_DAYS,
    MAX_SHARE,
    MIN_ACCOUNTS,
    MIN_DOCUMENTS,
)
from readablestem.web.retention import WEEK_DAYS

if TYPE_CHECKING:
    from readablestem.config import Settings

MODEL_COMPANIES = {"anthropic": "Anthropic", "openai": "OpenAI"}
READER_COMPANIES = {"claude": "Anthropic", "openai": "OpenAI"}


@dataclass(frozen=True)
class Recipient:
    """One company that receives something. `purposes` holds "model" (what `sent_material`
    lists for the model provider: cut-outs, and whole pages when a setting sends them),
    "handwriting" (a crop of a handwritten expression, read a second time), "hosting" or
    "tracing"."""

    name: str
    purposes: tuple[str, ...]

    @property
    def gets_documents(self) -> bool:
        """Receives pictures cut from a person's document (a model company)."""
        return "model" in self.purposes or "handwriting" in self.purposes


def recipients(settings: Settings, *, tracer_on: bool = False) -> list[Recipient]:
    """Every recipient the settings turn on, model provider first, one entry per company."""
    purposes: dict[str, list[str]] = {MODEL_COMPANIES[settings.model_provider]: ["model"]}
    for reader in settings.math_readers_on:
        purposes.setdefault(READER_COMPANIES[reader], []).append("handwriting")
    found = [Recipient(name, tuple(p)) for name, p in purposes.items()]
    found.append(Recipient("Railway", ("hosting",)))
    if tracer_on:
        found.append(Recipient("Langfuse", ("tracing",)))
    return found


def model_vendor(settings: Settings) -> str:
    """The company that gets pictures of equations and figures: the recipient with the
    "model" purpose, so a line that names it stays true if the provider changes."""
    return next(r.name for r in recipients(settings) if "model" in r.purposes)


# ----------------------------------------------------------------------- what is sent


@dataclass(frozen=True)
class Material:
    """One kind of material that leaves the server for a model, and the settings that send it.

    `switches` are the `Settings` fields that turn it on (any one of them is enough); empty
    means it is always sent. `what` is the sentence every long list prints (the privacy page,
    the offices page and its summary); `stored` says what is kept from the answer; `short`
    is its name in the one-line notes (the account deletion notes, the drawing), None when the
    cut-outs' name already covers it; `label` names it in the privacy page's dated list.
    `vendor` holds its words in the one-line vendor sentence
    (`vendor_sentence`), each as (slot, words): "parts" names what the pictures are of,
    "with" goes after "a little nearby text", "also" is a whole-page clause after "and so do",
    "sentence" is a sentence of its own ("{to}" is replaced by the companies). `stated` is the
    day the row was first stated in public (its sentence on the privacy page): a production
    start is refused while a row that is on has no date or a date still to come
    (`ops.launch.sent_material_problems`). `starts`: the day it starts being sent in
    production, for a row stated before it is on; from `stated` until it is on, the privacy
    page lists it as a coming change with this day, and a production start that turns it on
    earlier is refused. `reader`: it goes to the handwriting readers'
    companies, not to the model provider. `whole_pages`: it is a picture of a whole page, not
    of a part of one. `to` is filled by `sent_material`."""

    key: str
    switches: tuple[str, ...]
    what: str
    stored: str
    short: str | None
    label: str
    vendor: tuple[tuple[str, str], ...]
    stated: date | None
    reader: bool = False
    whole_pages: bool = False
    to: tuple[str, ...] = ()
    starts: date | None = None

    @property
    def stated_text(self) -> str:
        return format_day(self.stated)

    @property
    def starts_text(self) -> str:
        return format_day(self.starts)


# Every kind of material a conversion or a review sends to a model, in the order the lists
# print them. Adding a kind is one row here: the privacy page, the vendor line, the offices
# page, its summary and drawing, the deletion notes and the launch check all read this list.
# Senders (grep the callers of `VisionClient.run`): equation_step, figure_step and the second
# check (models.verifier, models.claude_verifier) send the cut-outs; table_step the tables;
# notation_memory adds to the equation request; image_page and image_page_check the scanned
# pages; page_witness and page_math whole typed pages; models.math_readers the close-ups;
# web.reread the regions and cells read from the review screen.
MATERIALS: tuple[Material, ...] = (
    Material(
        key="cutouts",
        switches=(),
        what="Pictures of the equations and figures cut from your file, with a little of the "
        "nearby text, to propose LaTeX for equations and alt text for figures.",
        stored="The proposed LaTeX, MathML and alt text are kept with the document.",
        short="equation and figure images and nearby text",
        label="Pictures of equations and figures, with a little nearby text",
        vendor=(("parts", "equations"), ("parts", "figures")),
        stated=date(2026, 10, 7),
    ),
    Material(
        key="tables",
        switches=("feature_tables",),
        what="Pictures of the tables cut from your file, to rebuild them as tables.",
        stored="The rebuilt table is kept with the document.",
        short="table images",
        label="Pictures of tables",
        vendor=(("parts", "tables"),),
        stated=date(2026, 10, 9),
    ),
    Material(
        key="notation",
        switches=("feature_notation_memory",),
        what="For a document you upload for one of your courses, that course's notation (its "
        "symbols and what they mean), sent with the equations.",
        stored="Nothing more is kept from the answer than the equations' readings.",
        short="course notation",
        label="A course's notation",
        vendor=(("with", "and, for a document in one of your courses, the course's notation"),),
        stated=date(2026, 10, 9),
    ),
    Material(
        key="math_pages",
        switches=("feature_page_witness", "feature_page_math"),
        what="A picture of each whole page that holds math, with the page's own text, for a "
        "second reading of where its formulas are and what they say.",
        stored="What comes back, where each formula on the page is and how it reads, is kept "
        "with the document; where it differs from the reading of the formula's own picture, "
        "you are asked.",
        short="page pictures",
        label="Pictures of whole pages that hold math, with their text",
        vendor=(("also", "pictures of whole pages that hold math"),),
        # Never stated in public before this row: a production start with either switch on
        # is refused until this is the day the sentence went live (privacy.html, "Changes to
        # this notice": the change is stated, with its date, before it takes effect).
        stated=None,
        whole_pages=True,
    ),
    Material(
        key="reading_order",
        switches=("feature_layout_model_order",),
        what="A picture of each whole page, with its lines of text as a numbered list, so a "
        "model can say which lines belong together and in what order to read them.",
        stored="Only the grouping and the order that come back are used; the text of your "
        "document stays the text of your file, and what is kept is counts.",
        short="page pictures",
        label="Pictures of whole pages, with their lines of text, to group and order the text",
        vendor=(("also", "whole pages, to say in what order to read them"),),
        # Stated in public as a coming change on 2026-11-27, a day before the switch goes on in
        # production (privacy.html, "Changes to this notice": before it takes effect).
        # `stated` must be the day (UTC) the page saying so went live in production: if the
        # deploy slips, move both days in the same commit.
        stated=date(2026, 11, 27),
        starts=date(2026, 11, 28),
        whole_pages=True,
    ),
    Material(
        key="scanned_pages",
        switches=("feature_image_pages",),
        what="If you check “Convert scanned or handwritten pages”, a picture of each scanned "
        "or handwritten page, and of parts of it that need a second look, to read it.",
        stored="The text read from the page is kept with the document, and every equation and "
        "table read from it is a decision for you.",
        short="page pictures",
        label="Pictures of scanned or handwritten pages you ask to have converted",
        vendor=(("also", "whole pages you ask to have read as scans"),),
        stated=date(2026, 10, 21),
        whole_pages=True,
    ),
    Material(
        key="handwriting_reader",
        switches=("math_reader_claude_enabled", "math_reader_openai_enabled"),
        what="A close-up picture of a handwritten expression can be sent, with no other text, "
        "to be read a second time.",
        stored="The second reading is kept with the document.",
        short="close-ups of handwriting",
        label="Close-ups of handwritten expressions, for a second reading",
        vendor=(
            (
                "sentence",
                "A close-up picture of a handwritten expression can also go to {to}, to be "
                "read a second time.",
            ),
        ),
        stated=date(2026, 11, 3),
        reader=True,
    ),
    Material(
        key="review_reads",
        switches=(),
        what="On the review screen, a picture of a region you mark as an equation, or of a "
        "table cell you ask to have read as math, with a little of the nearby text.",
        stored="The reading is kept with the document as a proposal for you to decide on.",
        short=None,
        label="Pictures of regions and table cells you ask to have read on the review screen",
        vendor=(),
        # Sent since the review actions were built (T3-5, T3-11), and first named on its own
        # in the release that went live on this day (pull request 172; the sentence was
        # written on 2026-11-11, but the privacy page a visitor could read did not carry it
        # before the release). Before, only as "images of those parts of the page".
        stated=date(2026, 11, 19),
    ),
)


def _join(words: list[str]) -> str:
    """Words as a list in a sentence: a; a and b; a, b and c."""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def material_on(material: Material, settings: Settings) -> bool:
    """Whether these settings send this kind of material."""
    return not material.switches or any(getattr(settings, s) for s in material.switches)


def sent_material(settings: Settings) -> list[Material]:
    """Every kind of material these settings send to a model, each with the companies that
    receive it (`to`): the model provider, or for a handwriting reader the reader's company."""
    vendor = model_vendor(settings)
    readers = tuple(dict.fromkeys(READER_COMPANIES[r] for r in settings.math_readers_on))
    return [
        replace(m, to=readers if m.reader else (vendor,))
        for m in MATERIALS
        if material_on(m, settings)
    ]


def coming_material(settings: Settings) -> list[Material]:
    """The kinds of material these settings do not send yet that the privacy page already
    states as coming: stated by today (UTC), with the first day they may start."""
    today = datetime.now(UTC).date()
    vendor = model_vendor(settings)
    readers = tuple(dict.fromkeys(READER_COMPANIES[r] for r in settings.math_readers_on))
    return [
        replace(m, to=readers if m.reader else (vendor,))
        for m in MATERIALS
        if not material_on(m, settings)
        and m.starts is not None
        and m.stated is not None
        and m.stated <= today
    ]


def vendor_sentence(settings: Settings) -> str:
    """The one line beside the upload button and on the landing page, built from
    `sent_material`. With the cut-outs, the tables and the scanned pages on it is the
    sentence the page has said since D-008."""
    vendor = model_vendor(settings)
    models = "Anthropic's Claude models" if vendor == "Anthropic" else f"{vendor}'s models"
    slots: dict[str, list[str]] = {"parts": [], "with": [], "also": [], "sentence": []}
    for m in sent_material(settings):
        for slot, words in m.vendor:
            slots[slot].append(words.format(to=" and ".join(m.to)))
    nearby = " ".join(["a little nearby text", *slots["with"]])
    line = f"Pictures of the {_join(slots['parts'])} in your file, with {nearby}, go to {models}"
    line += " to propose readings"
    if slots["also"]:
        line += ", and so do " + " and ".join(slots["also"])
    return " ".join([line + ".", *slots["sentence"], "They are not used to train models."])


def notation_words(settings: Settings) -> dict[str, str]:
    """The course notation disclosure, in the two places a person is told it: `course` (the
    course pages) and `upload` (the upload form's course choice). Built from the same facts
    as the `notation` row of `MATERIALS`: the company is the model provider (`model_vendor`),
    what is sent is each symbol's styled letter, its name and its meaning, at most
    `MAX_CONTEXT_ENTRIES` of them with each equation, and not the notes
    (`pipeline.notation_memory.notation_context`); who else sees the list is the course's
    name and the symbols that concern an equation, shown to a person who reviews a shared
    document (`web.notation.card_notation`, the job page's line)."""
    from readablestem.pipeline.notation_memory import MAX_CONTEXT_ENTRIES

    vendor = model_vendor(settings)

    def sent(documents: str) -> str:
        return (
            f"are sent to {vendor}, the model provider, with each equation of {documents}, "
            f"at most {MAX_CONTEXT_ENTRIES} symbols with each equation"
        )

    return {
        "course": (
            "Only you can open a course\u2019s list. A person you share a document with to review "
            "sees the course\u2019s name, the symbols the document was read with and, for each "
            "equation they review, the ones that apply to it. The symbols and their meanings "
            f"{sent('the documents you convert in the course')}. Your notes are not sent."
        ),
        "upload": (f"If the course has symbols, they and their meanings {sent('this document')}."),
    }


def material_context(settings: Settings) -> dict[str, Any]:
    """What every page that states what is sent renders with (`deps.render` adds it to all
    pages; `privacy_context` to the privacy page and the offices pages)."""
    sent = sent_material(settings)
    return {
        "sent_material": sent,
        "coming_material": coming_material(settings),
        "vendor_sentence": vendor_sentence(settings),
        # The short names, for the one-line notes: "equation and figure images and nearby
        # text and page pictures".
        "sent_short": _join(list(dict.fromkeys(m.short for m in sent if m.short))),
        "whole_pages_sent": any(m.whole_pages for m in sent),
        "notation_words": notation_words(settings),
    }


def listen_kept_documents() -> int:
    """How many documents' places the Listen page keeps in the browser (`KEEP` in
    static/listen.js): the notice says it, so it reads it."""
    from readablestem.web.app import STATIC_DIR

    found = re.search(r"var KEEP = (\d+);", (STATIC_DIR / "listen.js").read_text("utf-8"))
    if found is None:
        raise RuntimeError("static/listen.js has no `var KEEP = <number>;` line")
    return int(found.group(1))


def privacy_context(settings: Settings, *, tracer_on: bool = False) -> dict[str, Any]:
    """The variables /privacy renders with (besides the page-wide ones `deps.render` adds,
    which `notice_text` supplies for itself)."""
    from readablestem.web.app import SESSION_MAX_AGE_S

    return {
        # The page names Langfuse only when traces are actually sent there, and usage events
        # only when they are recorded.
        "tracing": tracer_on,
        "events_on": settings.events_enabled,
        "event_days": EVENT_RETENTION_DAYS,
        # Problem reports (`web.reports`): how long one is kept after it is closed, and at most.
        "report_closed_days": reports.CLOSED_DAYS,
        "report_max_days": reports.MAX_DAYS,
        # What the content features send and keep, only when they are on.
        "tables_on": settings.feature_tables,
        "notation_on": settings.feature_notation_memory,
        # A person can be given every document of a course (WS27.1): the page says so.
        "course_grants_on": settings.feature_course_grants and settings.feature_notation_memory,
        "image_pages_on": settings.feature_image_pages,
        "microsoft_on": settings.ms_sign_in_enabled,
        # Word and PowerPoint uploads are taken (T3-30): the page says how they are handled.
        "office_on": bool(office_convert.input_kinds(settings)),
        "email_login_on": settings.email_login_enabled,
        # LaTeX source uploads: the route narrows this to "and the sandbox probe passed"; the
        # launch checks read the setting, which is what a start would turn on.
        "latex_source": settings.feature_latex_source,
        # A .docx read from the file itself and kept as uploaded (the route narrows it, as for
        # LaTeX source).
        "word_source": settings.feature_word_source,
        "week_days": WEEK_DAYS,
        # What the notice says of the sign-in cookie and the form tokens (`app.SESSION_MAX_AGE_S`)
        # and of Listen's places kept in the browser. The backups' days stay typed in the notice:
        # they are the host's schedule, checked against `offices.BACKUP_DAYS`.
        "session_days": SESSION_MAX_AGE_S // 86400,
        "listen_keep": listen_kept_documents(),
        # Totals of review outcomes are read only above these counts (`events.outcome_cells`).
        "outcome_min_accounts": MIN_ACCOUNTS,
        "outcome_min_documents": MIN_DOCUMENTS,
        "outcome_max_share": MAX_SHARE,
        # Which companies receive pictures and nearby text, and the rest of the page's list.
        "recipients": recipients(settings, tracer_on=tracer_on),
        # What each of them receives (the list above, `sent_material`).
        **material_context(settings),
    }


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_text(page: str) -> str:
    """The visible text of a page: tags and comments dropped, entities decoded, blanks
    collapsed."""
    parser = _Text()
    parser.feed(page)
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def notice_text(
    settings: Settings, *, tracer_on: bool = False, templates_dir: Path | None = None
) -> str | None:
    """The privacy page as a visitor would read it with `settings`, as plain text; None when
    the template does not exist. A provider that the page would not name is not named here
    either. `templates_dir`: another folder of templates (a test's stand-in)."""
    return _page_text("privacy.html", settings, tracer_on=tracer_on, templates_dir=templates_dir)


def vendor_line_text(settings: Settings, *, templates_dir: Path | None = None) -> str | None:
    """The vendor line (beside the upload button and on the landing page) as a visitor would
    read it with `settings`, as plain text; None when its template does not exist."""
    return _page_text("_vendor_line.html", settings, templates_dir=templates_dir)


def _page_text(
    name: str, settings: Settings, *, tracer_on: bool = False, templates_dir: Path | None = None
) -> str | None:
    """Template `name` rendered with `settings`, tags stripped; None when it does not exist.
    Rendered from the real template with the privacy route's own context (`privacy_context`)
    and the page-wide values the pages read."""
    from readablestem.web.app import TEMPLATES_DIR, make_templates

    folder = templates_dir or TEMPLATES_DIR
    if not (folder / name).is_file():
        return None
    env = make_templates().env.overlay(
        loader=jinja2.FileSystemLoader(folder), undefined=jinja2.ChainableUndefined
    )
    context: dict[str, Any] = {
        **privacy_context(settings, tracer_on=tracer_on),
        "model_vendor": model_vendor(settings),
        "contact_email": settings.contact_email,
        "retention_hours": settings.retention_hours,
        "opt_in_days": settings.opt_in_retention_days,
        "signup_open": settings.signup_open,
        "analytics_on": settings.analytics_enabled,
        "analytics_events": [],
        "courses_enabled": settings.feature_notation_memory,
        "source_url": settings.source_url,
        "feedback_url": settings.feedback_url,
    }
    return html_text(env.get_template(name).render(context))
