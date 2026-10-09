"""What to tell the user about a job beyond its status: why it is incomplete, or waiting.

`views.JobView` covers the four statuses and the codes of failed jobs. The worker also
records outcomes on *finished* jobs (`worker.result_outcome`): a spend cap that stopped
model work part way ("budget_<scope>") or pages that were only scanned images
("scanned_pages"). Those jobs have results, but showing them as a plain "Finished. The
accessible HTML is ready." would hide that part of the document was not converted. A
queued job can also be held back because today's budget is spent (the worker claims
nothing then). And a finished job converted during an AI service outage, with most of
its equations and figures failed, is not "finished" either ("model_outage", read from
the stored result). `job_outcome` turns those cases into a label and a plain-language
message for the job page and the documents list; for everything else it returns None
and the usual status text applies.

Messages say what happened, what the output contains and what to do next, and never
quote the document.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from a11y_stem.web.db import JobStatus

# How many page numbers to list before "and N more".
MAX_PAGES_LISTED = 10


@dataclass(frozen=True)
class JobOutcome:
    code: str  # a category code, e.g. "budget_daily", "scanned_pages", "waiting_budget"
    label: str  # replaces the status label ("Finished", "Waiting", "Stopped")
    message: str  # replaces the status sentence
    partial: bool = False  # finished with results, but some of it was not converted


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

# A finished job converted while the AI service was down: most of its equations, figures
# and tables failed because the service returned errors or did not answer in time. Read
# from the stored result (the job itself carries no code), so it shows only where the
# result is loaded (the job page), and it goes away once the instructor has filled
# enough of them in on the review screen.
MODEL_OUTAGE = "model_outage"
OUTAGE_REASONS = frozenset({"model_api_error", "model_timeout"})


def _outage(result_json: dict[str, Any] | None) -> tuple[int, int, str] | None:
    """(fixes the service failed, model fixes, what they are) when more than half failed
    that way."""
    if not result_json:
        return None
    fixes = [f for f in result_json.get("fixes") or [] if isinstance(f, dict)]
    model = [
        f for f in fixes if f.get("kind") in _MODEL_KINDS and f.get("reason") != "scanned_page"
    ]
    failed = [f for f in model if f.get("status") == "failed" and f.get("reason") in OUTAGE_REASONS]
    if failed and 2 * len(failed) > len(model):
        return len(failed), len(model), _kinds_phrase(model)
    return None


FAILED_MESSAGES = {
    "scanned": "Every page of this PDF is a scanned image with no text, so there is nothing "
    "this tool can convert yet. Run text recognition (OCR) on it first, for example with "
    "your scanner's software or Adobe Acrobat, then upload the result.",
    "no_content": "This PDF has no text, equations or figures that could be converted.",
    "model_not_configured": "Conversions are not set up on this server yet, so nothing "
    "was converted and nothing was sent anywhere. Please tell the person who runs this "
    "site.",
    "access_removed": "Your access to this tool was removed, so this document was not converted.",
    "page_too_large": "The PDF has a page that is too large to convert.",
}


def job_outcome(
    *,
    status: str,
    error_code: str | None,
    result_json: dict[str, Any] | None = None,
    budget_closed: bool = False,
) -> JobOutcome | None:
    """The outcome to show for a job, or None when its status says it all."""
    if status == JobStatus.QUEUED and budget_closed:
        return JobOutcome(
            "waiting_budget",
            "Waiting",
            "Today's conversion budget has been used up, so conversions start again after "
            "00:00 UTC. Your document stays in the queue and starts then; you can leave "
            "this page.",
        )
    if status == JobStatus.FAILED and error_code in FAILED_MESSAGES:
        return JobOutcome(error_code, "Stopped", FAILED_MESSAGES[error_code])
    if status == JobStatus.DONE and not error_code and (outage := _outage(result_json)):
        n, total, kinds = outage
        message = (
            f"The AI service did not answer for {n} of {total} {kinds}, so "
            f"they were not converted. Nothing is wrong with your PDF. {KEPT} Upload the "
            "document again later to convert them."
        )
        return JobOutcome(MODEL_OUTAGE, "Stopped: the AI service did not answer", message,
                          partial=True)  # fmt: skip
    if status != JobStatus.DONE or not error_code:
        return None
    if error_code == "budget_daily":
        counts = _not_converted(result_json, error_code)
        message = (
            _count_sentence(counts, "Today's conversion budget ran out part way through")
            + f" {KEPT} Upload the document again after 00:00 UTC to convert them."
        )
        return JobOutcome(error_code, "Stopped early", message, partial=True)
    if error_code in {"budget_job_usd", "budget_job_calls"}:
        counts = _not_converted(result_json, error_code)
        message = (
            _count_sentence(
                counts, "This document needed more model work than one conversion may use"
            )
            + f" {KEPT} Split the PDF into smaller parts and upload each one: every part "
            "gets its own allowance."
        )
        return JobOutcome(error_code, "Stopped early", message, partial=True)
    if error_code == "scanned_pages":
        pages = [p for p in (result_json or {}).get("image_only_pages") or [] if isinstance(p, int)]
        verb = _plural(len(pages), "is a scanned image", "are scanned images")
        lead = f"{_pages_text(pages)} {verb}" if pages else "Some pages are scanned images"
        message = (
            f"{lead} with no text, so {_plural(len(pages), 'it', 'they')} could not be "
            "converted. The HTML keeps each one as an image, marked “Not converted”. "
            "To convert them, run text recognition (OCR) on the PDF and upload it again."
        )
        return JobOutcome(error_code, "Finished, with pages not converted", message, partial=True)
    return None
