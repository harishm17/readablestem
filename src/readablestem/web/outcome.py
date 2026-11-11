"""The words of a job's outcome beyond its status: why it stopped, or what of a finished job
was not converted. `web.states` decides which state a job is in and asks here for the words.

The worker records outcomes on *finished* jobs (`worker.result_outcome`): a spend cap that
stopped model work part way ("budget_<scope>") or pages kept as images, not converted
("pages_not_converted": handwritten, scanned, or with nothing readable on them, and
handwriting on typed pages; jobs from before page types say "scanned_pages"). A finished job
converted during an AI service outage, with most of its equations and figures failed, is read
from the stored result (the job carries no code). Each of these is a gap note on the job's
state (`states.gaps_of`), and a note with nothing in it is not produced. The messages of a
stopped job's codes with a longer explanation (scanned PDFs, no model key, removed access)
are here too (`FAILED_MESSAGES`).

Messages say what happened, what the output contains and what to do next, and never
quote the document.
"""

from __future__ import annotations

from typing import Any

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
    {"scanned_page", "handwritten_page", "unread_page", "handwriting_on_page"}
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


FAILED_MESSAGES = {
    # Every page handwritten or scanned (pipeline.page_type.refusal_code): the upload
    # probe refuses these first; a job sees them only when the probe could not tell.
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


def failed_message(code: str, *, image_pages_offered: bool = False) -> str | None:
    """The message of a stopped job's error code, or None for a code with no message here.
    `image_pages_offered`: the deployment offers to convert scanned and handwritten pages, so
    a PDF of only such pages was not converted because the option was not ticked."""
    if image_pages_offered and code in IMAGE_PAGE_FAILS:
        what = IMAGE_PAGE_FAILS[code]
        lead = f"{what}, so it was not converted: such pages are read only when you ask."
        return f"{lead} {OPT_IN_ROUTE}"
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
