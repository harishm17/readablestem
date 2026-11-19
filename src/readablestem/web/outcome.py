"""The words of a job's outcome beyond its status: why it stopped, or what of a finished job
was not converted. `web.states` decides which state a job is in and asks here for the words.

The worker records outcomes on *finished* jobs (`worker.result_outcome`): a spend cap that
stopped model work part way ("budget_<scope>") or pages kept as images, not converted
("pages_not_converted": handwritten, scanned, or with nothing readable on them, and
handwriting on typed pages; jobs from before page types say "scanned_pages"). A finished job
converted during an AI service outage, with most of its equations and figures failed, is read
from the stored result (the job carries no code), and so is a LaTeX source job whose reader
left words or pictures unread (`SOURCE_UNREAD`). Each of these is a gap note on the job's
state (`states.gaps_of`), and a note with nothing in it is not produced. The messages of a
stopped job's codes with a longer explanation (scanned PDFs, no model key, removed access)
are here too (`FAILED_MESSAGES`).

Messages say what happened, what the output contains and what to do next, and never
quote the document.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from readablestem.core.types import PageType
from readablestem.pipeline import page_type
from readablestem.pipeline.checked_words import source_noun

if TYPE_CHECKING:
    from readablestem.config import Settings

# How many page numbers to list before "and N more".
MAX_PAGES_LISTED = 10


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


# The kinds a model converts, in the order the headlines name them.
_MODEL_KINDS = ("equation", "figure", "table")
_KIND_PLURALS = {"equation": "equations", "figure": "figures", "table": "tables"}


def _kinds_phrase(fixes: list[dict[str, Any]]) -> str:
    """ "equations and figures", or "equations, figures and tables" when the document has
    tables: the kinds its model fixes are of (equations and figures when it has none)."""
    present = [_KIND_PLURALS[k] for k in _MODEL_KINDS if any(f.get("kind") == k for f in fixes)]
    words = present or ["equations", "figures"]
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + " and " + words[-1]


def _not_converted(result_json: dict[str, Any] | None, reason: str) -> tuple[int, int, str] | None:
    """(fixes FAILED with `reason`, model fixes, what they are) from a stored result."""
    if not result_json:
        return None
    fixes = [f for f in result_json.get("fixes") or [] if isinstance(f, dict)]
    model_fixes = [f for f in fixes if f.get("kind") in _MODEL_KINDS]
    stopped = [f for f in model_fixes if f.get("status") == "failed" and f.get("reason") == reason]
    return len(stopped), len(model_fixes), _kinds_phrase(model_fixes)


def _count_sentence(counts: tuple[int, int, str] | None, cause: str) -> str:
    if counts is None or counts[1] == 0:
        return f"{cause}: some equations and figures were not converted."
    n, total, kinds = counts
    return f"{cause}: {n} of {total} {kinds} were not converted."


def _pages_text(pages: list[int]) -> str:
    """1-based page numbers, e.g. "page 3" or "pages 1, 2 and 5"."""
    numbers = [str(p + 1) for p in pages[:MAX_PAGES_LISTED]]
    extra = len(pages) - len(numbers)
    if extra:
        listed = ", ".join(numbers) + f" and {extra} more"
    elif len(numbers) > 1:
        listed = ", ".join(numbers[:-1]) + " and " + numbers[-1]
    else:
        listed = numbers[0] if numbers else ""
    return f"{_plural(len(pages), 'Page', 'Pages')} {listed}"


KEPT = "The HTML keeps their original images, marked “Not converted”."

# Figures kept as images before any model work: never sent to the service.
KEPT_IMAGE_REASONS = frozenset(
    {"scanned_page", "handwritten_page", "unread_page", "page_read_failed", "handwriting_on_page"}
)
# A finished job converted while the AI service was down: most of its equations, figures
# and tables failed because the service returned errors or did not answer in time. Read
# from the stored result (the job itself carries no code); every such item is an open
# decision, so a list that reads no result shows the job as needing decisions, and the
# note goes away once the instructor has filled enough of them in on the review screen.
OUTAGE_REASONS = frozenset({"model_api_error", "model_timeout"})


def _outage(result_json: dict[str, Any] | None) -> tuple[int, int, str] | None:
    """(fixes the service failed, model fixes, what they are) when more than half failed
    that way."""
    if not result_json:
        return None
    fixes = [f for f in result_json.get("fixes") or [] if isinstance(f, dict)]
    model = [
        f
        for f in fixes
        if f.get("kind") in _MODEL_KINDS and f.get("reason") not in KEPT_IMAGE_REASONS
    ]
    failed = [f for f in model if f.get("status") == "failed" and f.get("reason") in OUTAGE_REASONS]
    if failed and 2 * len(failed) > len(model):
        return len(failed), len(model), _kinds_phrase(model)
    return None


# A LaTeX source job whose reader left words or pictures out (`source.ltx_reader`, the
# `source_unread` notice): the file is finished but not whole. Read from the stored result's
# `source_report`; counts only, never its words.
SOURCE_UNREAD = "source_unread"


def unread_phrase(words: int, pictures: int) -> str:
    """ "12 words and 1 picture", "1 word", "2 pictures"."""
    parts = []
    if words:
        parts.append(f"{words} {_plural(words, 'word', 'words')}")
    if pictures:
        parts.append(f"{pictures} {_plural(pictures, 'picture', 'pictures')}")
    return " and ".join(parts)


def unread_message(result_json: dict[str, Any] | None) -> str:
    """The gap note of a finished LaTeX source job whose reader left words or pictures
    unread, or "" when it read everything. Converting again reads the same way, so the note
    offers no retry."""
    report = (result_json or {}).get("source_report")
    if not isinstance(report, dict):
        return ""
    words, pictures = report.get("unread_words"), report.get("unread_pictures")
    words = words if isinstance(words, int) and words > 0 else 0
    pictures = pictures if isinstance(pictures, int) and pictures > 0 else 0
    if not (words or pictures):
        return ""
    one = words + pictures == 1
    noun = source_noun(str((result_json or {}).get("origin", "pdf")))
    return (
        f"This tool could not read part of the {noun}: {unread_phrase(words, pictures)} "
        f"from it {'is' if one else 'are'} not in the file, as listed under “Not handled in "
        f"this file” below. Check that the file reads well without {'it' if one else 'them'} "
        "before you share it, or upload the PDF instead."
    )


FAILED_MESSAGES = {
    # Every page handwritten or scanned (pipeline.page_type.refusal_code): the upload
    # probe refuses these first; a job sees them when the probe could not tell inside its
    # allowance of path items.
    # Never "run OCR": OCR'd scans are what these are.
    "scanned": "Every page of this PDF is a scanned image, so this tool cannot convert it. "
    "It has no text this tool can use. Export a PDF from the original file (LaTeX, Word or "
    "PowerPoint) and upload that instead.",
    "scanned_ocr": "Every page of this PDF is a scanned image, so this tool cannot convert "
    "it. Its text was guessed by OCR, which is often wrong in math, so it is not used. "
    "Export a PDF from the original file (LaTeX, Word or PowerPoint) and upload that instead.",
    "handwritten": "This PDF is handwritten, so this tool cannot convert it. Its hidden text, "
    "guessed from the handwriting, is often wrong in math, so it is not used. Export a PDF "
    "from a typed version (LaTeX, Word or PowerPoint) and upload that instead.",
    "handwritten_no_text": "This PDF is handwritten, so this tool cannot convert it. "
    "It has no text this tool can use. Export a PDF from a typed version (LaTeX, Word or "
    "PowerPoint) and upload that instead.",
    "no_content": "This PDF has no text, equations or figures that could be converted.",
    "model_not_configured": "Conversions are not set up on this server yet. Nothing was "
    "converted or sent anywhere. Tell the person who runs this site.",
    "access_removed": "Your access to this tool was removed, so this document was not converted.",
    "page_too_large": "The PDF has a page that is too large to convert.",
    "image_too_large": "The PDF has a picture that is too large to read. Nothing was converted. "
    "Export the PDF again with its pictures at a normal size (300 dpi is plenty) and upload that.",
    "content_too_large": "The PDF is too large to read: a page of it, or the file as a whole, "
    "holds far more drawing instructions than a course document does. Nothing was converted. "
    "Save it as a PDF again (Print to PDF makes a simpler file), or split it into parts, and "
    "upload that.",
    "too_complex": "A page of this PDF has so many separate shapes that reading it takes more "
    "time or memory than a conversion may use. Nothing was converted. Export a simpler or "
    "flattened PDF, or split the page off and upload the rest.",
}


# The refusals of a PDF that is only scans or handwriting, and what each says the PDF is. When
# the deployment offers "Convert scanned or handwritten pages" (ADR 0007, FEATURE_IMAGE_PAGES)
# the message does not say the tool cannot convert it: it says such pages are read only when
# asked for, and offers that route.
IMAGE_PAGE_FAILS = {
    "scanned": "Every page of this PDF is a scanned image",
    "scanned_ocr": "Every page of this PDF is a scanned image",
    "handwritten": "This PDF is handwritten",
    "handwritten_no_text": "This PDF is handwritten",
}
OPT_IN_ROUTE = (
    "Upload it again with “Convert scanned or handwritten pages” checked, or export a PDF from "
    "the original."
)


def failed_message(code: str, *, image_pages_offered: bool = False, cost: str = "") -> str | None:
    """The message of a stopped job's error code, or None for a code with no message here.
    `image_pages_offered`: the deployment offers to convert scanned and handwritten pages, so
    a PDF of only such pages was not converted because the option was not ticked. `cost`:
    what reading them costs (`image_pages_cost`), said after the way to ask."""
    if image_pages_offered and code in IMAGE_PAGE_FAILS:
        what = IMAGE_PAGE_FAILS[code]
        lead = f"{what}, so it was not converted: such pages are read only when you ask."
        return " ".join(part for part in (lead, OPT_IN_ROUTE, cost) if part)
    return FAILED_MESSAGES.get(code)


# A queued job held back because today's budget is spent (the worker claims nothing then).
WAITING_BUDGET = (
    "Today's conversion budget has been used up, so conversions start again after 00:00 UTC. "
    "Your document stays in the queue and starts then; you can leave this page."
)


def outage_message(result_json: dict[str, Any] | None, *, offered: bool = True) -> str:
    """The gap note of a finished job converted while the AI service was down, or "".
    `offered`: "Convert the missing parts" is offered, so the note may say to use it."""
    outage = _outage(result_json)
    if outage is None:
        return ""
    n, total, kinds = outage
    text = (
        f"The AI service did not answer for {n} of {total} {kinds}, so they were not "
        f"converted. Nothing is wrong with your PDF. {KEPT}"
    )
    if offered:
        text += (
            " Use “Convert the missing parts” below, now or later: only those parts are sent "
            "again, and it does not use up your daily pages."
        )
    return text


def budget_message(result_json: dict[str, Any] | None, code: str, *, offered: bool = True) -> str:
    """The gap note of a finished job a spend cap stopped part way, or "" when nothing was
    left unconverted by it (F-319: never "0 of N were not converted"). `offered`: as in
    `outage_message`."""
    counts = _not_converted(result_json, code)
    if counts is not None and counts[0] == 0:
        return ""
    if code == "budget_daily":
        text = _count_sentence(counts, "Today's conversion budget ran out part way through")
        text += f" {KEPT}"
        if offered:
            text += (
                " After 00:00 UTC use “Convert the missing parts” below to convert them: only "
                "those parts are sent again, and it does not use up your daily pages."
            )
        return text
    return (
        _count_sentence(counts, "This document needed more model work than one conversion may use")
        + f" {KEPT} Split the PDF into smaller parts and upload each one: every part gets its "
        "own allowance."
    )


def pages_message(result_json: dict[str, Any] | None) -> str:
    """The gap note of a finished job with pages kept as images, or "" when every such page
    was left out of the file (T3-16)."""
    by_reason, handwriting, _ = _kept(result_json)
    if not (by_reason or handwriting) and (result_json or {}).get("omitted"):
        return ""
    return _pages_message(result_json)


# Done with pages kept as images ("scanned_pages" is the code jobs had before page types).
PAGES_NOT_CONVERTED = frozenset({"pages_not_converted", "scanned_pages"})
# How the job page names the pages kept whole as images, by their fix's reason
# (pipeline.page_type), in this order. Final wording for the beta (design, section 3.3).
PAGE_WORDS = {
    "handwritten_page": ("is handwritten", "are handwritten"),
    "scanned_page": ("is a scanned image", "are scanned images"),
    "unread_page": (
        "has drawings or writing this tool could not read",
        "have drawings or writing this tool could not read",
    ),
    "page_read_failed": ("could not be read", "could not be read"),
}
HANDWRITING_ON_PAGE = "handwriting_on_page"
HIDDEN_FEW = 3  # pipeline.page_type.HIDDEN_FEW: hidden characters that count as a layer


def _kept(result_json: dict[str, Any] | None) -> tuple[dict[str, list[int]], list[int], bool]:
    """From a stored result: the pages kept whole as images, by reason; the pages whose
    handwriting is kept as an image; and whether hidden text on the kept pages was left
    out. Only codes, page numbers and counts are read."""
    data = result_json or {}
    page_of = {
        e.get("id"): e.get("page")
        for e in data.get("elements") or []
        if isinstance(e, dict) and isinstance(e.get("page"), int)
    }
    by_reason: dict[str, list[int]] = {}
    handwriting: set[int] = set()
    for fix in data.get("fixes") or []:
        if not isinstance(fix, dict) or (page := page_of.get(fix.get("element_id"))) is None:
            continue
        reason = fix.get("reason")
        if reason in PAGE_WORDS:
            by_reason.setdefault(reason, []).append(page)
        elif reason == HANDWRITING_ON_PAGE:
            handwriting.add(page)
    if not by_reason and not handwriting:  # stored before page types: scanned pages only
        pages = [p for p in data.get("image_only_pages") or [] if isinstance(p, int)]
        if pages:
            by_reason["scanned_page"] = pages
    kept = {p for pages in by_reason.values() for p in pages}
    hidden = any(
        isinstance(t, dict)
        and t.get("page") in kept
        and int(t.get("invisible_chars") or 0) + int(t.get("covered_chars") or 0) >= HIDDEN_FEW
        for t in data.get("page_types") or []
    )
    return {r: sorted(set(p)) for r, p in by_reason.items()}, sorted(handwriting), hidden


# The kinds `page_type.ImagePageCount` counts, in the words of `PAGE_WORDS` (the job page's
# terms): "2 scanned images, 1 handwritten".
_KIND_COUNT_WORDS = (
    ("scanned", "scanned image", "scanned images"),
    ("handwritten", "handwritten", "handwritten"),
    (
        "unread",
        "with drawings or writing this tool could not read",
        "with drawings or writing this tool could not read",
    ),
)


def image_pages_in(result_json: dict[str, Any] | None) -> page_type.ImagePageCount:
    """The pages of a stored result that the image page pass would read, by kind: the one
    count (`page_type.count_image_pages`) over its page records. A result stored before page
    records has the pages its fixes keep, by reason."""
    data = result_json or {}
    records = [
        PageType.model_validate(p) for p in data.get("page_types") or [] if isinstance(p, dict)
    ]
    count = page_type.count_image_pages(records)
    if count.found or records:
        return count
    by_reason, _, _ = _kept(result_json)
    return page_type.ImagePageCount(
        scanned=len(by_reason.get("scanned_page", ())),
        handwritten=len(by_reason.get("handwritten_page", ())),
        unread=len(by_reason.get("unread_page", ())),
    )


def _count_parts(count: page_type.ImagePageCount) -> str:
    parts = []
    for key, one, many in _KIND_COUNT_WORDS:
        n = getattr(count, key)
        if n:
            parts.append(f"{n} {_plural(n, one, many)}")
    return ", ".join(parts)


def _dollars(value: float) -> str:
    return f"${value:.2f}" if value >= 0.01 else f"${value:.4f}"


def image_pages_cost(settings: Settings, pages: int | None = None, many: bool = True) -> str:
    """What reading scanned and handwritten pages costs the service (never a charge to the
    person: the words say "of the server's budget"), in the one wording every place that
    offers the option uses (the upload form, the offer, the refusal, /limits). Both figures are
    limits, so they say "up to": the per-page setting is what one page may spend and the
    per-job setting the most one document may (`Settings.image_pages_estimate`, the figure the
    job's cap uses); a job that reaches it stops reading and keeps the rest as pictures.
    With `pages` (the count of such pages in this file) it gives the estimate for the file.
    `many`: those pages are more than one ("them", not "it")."""
    per_page = settings.image_pages_usd_per_page
    most = settings.image_pages_max_usd_per_job
    if not pages:
        return (
            f"Reading a scanned or handwritten page uses up to {_dollars(per_page)} of the "
            f"server\u2019s budget, and one document at most {_dollars(most)}. A document that "
            "reaches that stops being read, and the pages left stay pictures."
        )
    each = f"{pages} {_plural(pages, 'page', 'pages')} at up to {_dollars(per_page)} each"
    if pages * per_page > most:
        each += f", and one document uses at most {_dollars(most)}"
    cost = _dollars(settings.image_pages_estimate(pages))
    return (
        f"Reading {'them' if many else 'it'} would use up to {cost} of the server\u2019s budget "
        f"({each})."
    )


def image_pages_cost_offered(settings: Settings) -> str:
    """`image_pages_cost` where the option is offered, else nothing (a deployment without it
    has no scan option to say a cost of)."""
    return image_pages_cost(settings) if settings.feature_image_pages else ""


def image_pages_offer(
    count: page_type.ImagePageCount, settings: Settings, *, listed: bool = False
) -> str:
    """What is said of scanned and handwritten pages the box was not checked for: how many,
    that they are kept as pictures unless the reader asks, and what reading them would cost
    (the per-page setting, `Settings.image_pages_estimate`). `listed`: the page already names
    the pages and says they are kept (the job page's gap note), so only the count's cost is added.
    "" when there is no such page and none could not be told."""
    if not count.possible:
        return ""
    pages = count.found
    many = pages != 1 or count.at_least
    sentences = []
    if not listed:
        what = f"{count.words} {'pages are' if many else 'page is'} scanned or handwritten"
        if (parts := _count_parts(count)) and count.found:
            what += f" ({parts})"
        sentences.append(
            f"{what}. They are kept as pictures, marked “Not converted”, unless you ask for "
            "them to be read."
        )
    # With the count unsure ("at least") the pages counted are not all there are, so an
    # estimate for them would understate: say the per-page and per-document limits instead.
    sentences.append(image_pages_cost(settings, None if count.at_least else pages, many))
    return " ".join(sentences)


def _pages_message(result_json: dict[str, Any] | None) -> str:
    """ "Pages 2 and 5 are handwritten, so they were not converted. ..." from the result."""
    by_reason, handwriting, hidden = _kept(result_json)
    sentences = []
    for reason, (one, many) in PAGE_WORDS.items():
        pages = by_reason.get(reason)
        if pages:
            verb = _plural(len(pages), one, many)
            it = _plural(len(pages), "it was", "they were")
            sentences.append(f"{_pages_text(pages)} {verb}, so {it} not converted.")
    if sentences:
        kept = sum(len(p) for p in by_reason.values())
        each = "it" if kept == 1 else "each one"
        sentences.append(f"The HTML keeps {each} as an image, marked “Not converted”.")
    elif not handwriting:  # a stored result without the details
        sentences.append(
            "Some pages were not converted. The HTML keeps each one as an image, marked "
            "“Not converted”."
        )
    if hidden:
        sentences.append(
            "Hidden text that a tablet or a text recognition (OCR) program guessed from "
            f"{_plural(sum(len(p) for p in by_reason.values()), 'it', 'them')} was not used."
        )
    if handwriting:
        have = _plural(len(handwriting), "has", "have")
        sentences.append(
            f"{_pages_text(handwriting)} {have} handwriting on typed text: the typed text was "
            "converted, and the handwriting is kept as an image, marked “Not converted”."
        )
    return " ".join(sentences)
