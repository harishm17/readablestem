"""A second reader's reading of each handwritten display equation, compared with the page's.

On a page read from an image the page reader (a vision model) gives every display equation a
reading, and the equation step reads the crop of its box once more. Both are the same family
of model, so they tend to misread the same symbol the same way. With
`Settings.image_pages_second_reader` set, one more reader that does not share those mistakes
(the local one, or another model) reads the crop, and this module compares the two:

- they agree (`math_canonical`: the same notation or the same words when spoken): a note on the
  fix, `second_reader_agrees`, that the person is not shown. Nothing else changes: handwritten
  maths is always a decision (`image_page.enforce_maths_required`), and two readers of the same
  ink can be wrong the same way (docs/eval-handwriting.md);
- they differ: the note `second_reader_differs`, and where the difference is a few symbols,
  `Element.alternatives` (`reading_alternatives.from_reading`): each differing stretch of the
  page's reading with the other reading's LaTeX as a choice, only choices that convert, at most
  `MAX_DOUBTS_PER_READING` stretches of `MAX_OPTIONS_PER_DOUBT` options. The review card shows
  them as one-click chips; nothing is chosen for the person;
- the reader could not answer (a timeout, a refusal, unreadable output, an error, no time left
  in the job's share for second readings, a spend limit of the job itself) or the readers' own
  dollar share is spent (`BudgetExceeded("reader_usd")`): no note on the equation (the same line
  on every one would drown the card), one notice for the document with how many equations have
  only the first reading (`second_reader_unavailable`, `second_reader_budget`), and the item is
  as it was.

The agree and differ notes are `Check`s on a fix that stands. A reader's spend limit is never a
failed fix with a `budget_` reason:
`pipeline.run._budget_scope` reads that as "the job's money is gone" and stops every other
step, and a second reading is an optional extra that must never cost the conversion anything.
A reader's failure of any kind costs one expression its second reading, never the job.

What it reads: display equations of image pages (`IMAGE_PAGE_REASON`) that have a crop and a
reading to compare. Inline formulas have no crop of their own. Pure of the network: a reader
is passed in (`models.math_readers`), and the hosted conversion child, which has no keys, only
ever builds the local one (`default_readers`). No crop, LaTeX or option is logged.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from readablestem.config import ConfigError, Settings
from readablestem.core.budget import BudgetExceeded
from readablestem.core.types import (
    IMAGE_PAGE_REASON,
    Check,
    Element,
    Fix,
    Kind,
    Notice,
    SecondReaderSummary,
    Status,
)
from readablestem.models.math_readers.base import MathReader, ReadRequest
from readablestem.observability.logging import log_fields
from readablestem.pipeline import equation_step, math_canonical, reading_alternatives
from readablestem.pipeline.math_canonical import Kind as Sameness
from readablestem.pipeline.validate import has_errors

log = logging.getLogger(__name__)

NOTE = "image_page.second_reader"
AGREES = "second_reader_agrees"
DIFFERS = "second_reader_differs"
UNAVAILABLE = "second_reader_unavailable"  # notice codes (core.types.NOTICE_CODES)
BUDGET = "second_reader_budget"
# Seconds kept for everything after the second readings (the output, the checks) when the
# job's own time limit is known: a share never runs into them.
RESERVE_OF_LIMIT = 0.2
# Same notation or same words aloud: what a listener could not tell apart. A letter style
# that differs (bold, double-struck) is a difference.
_AGREE_AT = (Sameness.NOTATION, Sameness.SPOKEN)
# The equation step's own note that the crop did not show an equation: nothing to read twice.
_CROP_FAILED = "single_reading_crop_failed"
_UNUSABLE = frozenset({math_canonical.UNREADABLE_A, math_canonical.UNREADABLE_B})


@dataclass
class Summary:
    """Counts of one job's second readings (content-free)."""

    reader: str = ""
    considered: int = 0
    read: int = 0
    agreed: int = 0
    differed: int = 0
    with_choices: int = 0
    unavailable: int = 0
    over_budget: int = 0
    seconds: float = 0.0
    cost_usd: float = 0.0

    def record(self) -> SecondReaderSummary:
        return SecondReaderSummary(
            reader=self.reader,
            considered=self.considered,
            read=self.read,
            agreed=self.agreed,
            differed=self.differed,
            with_choices=self.with_choices,
            unavailable=self.unavailable,
            over_budget=self.over_budget,
            seconds=round(self.seconds, 2),
            cost_usd=round(self.cost_usd, 6),
        )

    def notices(self) -> list[Notice]:
        """One notice for the document per reason equations were left with one reading."""
        found = []
        if self.unavailable:
            found.append(Notice(code=UNAVAILABLE, count=self.unavailable, review="optional"))
        if self.over_budget:
            found.append(Notice(code=BUDGET, count=self.over_budget, review="optional"))
        return found


def share_s(settings: Settings, time_limit_s: float | None, elapsed_s: float) -> float:
    """The seconds second readings may take: `image_pages_second_reader_time_s`, and never more
    than what the job's own time limit leaves once `RESERVE_OF_LIMIT` of it is kept for the rest
    (a finished job must not become a timeout)."""
    share = settings.image_pages_second_reader_time_s
    if time_limit_s is not None:
        share = min(share, time_limit_s * (1 - RESERVE_OF_LIMIT) - elapsed_s)
    return max(0.0, share)


def default_readers(settings: Settings) -> dict[str, MathReader]:
    """The readers the conversion child may build for itself: the local one when it is the
    chosen second reader. The others need a key and a network, which only the parent has."""
    if settings.image_pages_second_reader != "local":
        return {}
    from readablestem.models.math_readers.local_reader import LocalReader

    return {"local": LocalReader.from_settings(settings)}


def chosen(settings: Settings, readers: Mapping[str, MathReader]) -> MathReader | None:
    """The reader the settings name, if it was built."""
    return readers.get(settings.image_pages_second_reader)


def _candidates(elements: Sequence[Element], fixes: Mapping[str, Fix], crops: Mapping[str, bytes]):
    for element in elements:
        if element.kind is not Kind.EQUATION or element.display != "block":
            continue
        if IMAGE_PAGE_REASON not in element.reasons:
            continue
        fix = fixes.get(element.id)
        if fix is None or fix.equation is None or not fix.equation.latex.strip():
            continue
        if fix.status is Status.FAILED or element.id not in crops:
            continue
        if any(c.code == _CROP_FAILED for c in fix.checks):
            continue
        yield element, fix, crops[element.id]


def _check(fix: Fix, name: str, code: str, *, passed: bool = False) -> None:
    """Put the note on the fix (one per name: a rerun replaces it)."""
    kept = [c for c in fix.checks if c.name != name]
    fix.checks = [*kept, Check(name=name, passed=passed, severity="warning", code=code)]


def reconcile(
    elements: Sequence[Element],
    fixes: Mapping[str, Fix],
    crops: Mapping[str, bytes],
    reader: MathReader,
    settings: Settings,
    *,
    share_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Summary:
    """Read each handwritten display equation's crop with `reader` and record how it compares
    with the reading on the fix (in place: a note on the fix, `Element.alternatives` where the
    readings differ in a few symbols). Never raises for a reader's failure.

    `share_s` is the time all reads together may take (default
    `Settings.image_pages_second_reader_time_s`). A read is not started unless its worst case
    (`reader.max_read_s`, else the model timeout) fits in what is left of it."""
    summary = Summary(reader=reader.name)
    started = clock()
    deadline = started + (settings.image_pages_second_reader_time_s if share_s is None else share_s)
    worst_s = float(getattr(reader, "max_read_s", None) or settings.model_timeout_s)
    stopped: str | None = None  # why no more reads are made: BUDGET or UNAVAILABLE

    def skip(why: str) -> None:
        if why == BUDGET:
            summary.over_budget += 1
        else:
            summary.unavailable += 1

    for element, fix, crop in _candidates(elements, fixes, crops):
        summary.considered += 1
        if stopped is None and clock() + worst_s > deadline:
            stopped = UNAVAILABLE
        if stopped is not None:
            skip(stopped)
            continue
        try:
            result = reader.read(ReadRequest(crop))
        except BudgetExceeded as exc:
            # The readers' own share is `reader_usd`; any other scope is the job's own money,
            # which the conversion still needs: no more second readings either way.
            stopped = BUDGET if exc.scope == "reader_usd" else UNAVAILABLE
            skip(stopped)
            continue
        except ConfigError as exc:  # a missing key or model file fails every read the same way
            log_fields(
                log, logging.WARNING, "second_reader_misconfigured", error=type(exc).__name__
            )
            stopped = UNAVAILABLE
            skip(stopped)
            continue
        except Exception as exc:  # a reader's trouble costs this expression its second reading
            log_fields(log, logging.WARNING, "second_reader_failed", error=type(exc).__name__)
            skip(UNAVAILABLE)
            continue
        summary.cost_usd += result.call.cost_usd
        if not result.candidates:
            skip(UNAVAILABLE)
            continue
        _compare(element, fix, result.candidates[0].latex, summary)
    summary.seconds = clock() - started
    log_fields(
        log,
        logging.INFO,
        "second_reader_done",
        reader=summary.reader,
        considered=summary.considered,
        read=summary.read,
        agreed=summary.agreed,
        differed=summary.differed,
        with_choices=summary.with_choices,
        unavailable=summary.unavailable,
        over_budget=summary.over_budget,
        seconds=round(summary.seconds, 2),
        cost_usd=round(summary.cost_usd, 6),
    )
    return summary


def _compare(element: Element, fix: Fix, other: str, summary: Summary) -> None:
    assert fix.equation is not None
    ours = fix.equation.latex
    mathml, checks = equation_step._convert(other, "block")
    comparison = math_canonical.compare(ours, other, display=True)
    # (a reading that does not pass the product's own LaTeX checks says nothing about ours)
    if (
        mathml is None or has_errors(checks) or comparison.reason in _UNUSABLE
    ):  # a reading that does not convert says nothing
        summary.unavailable += 1
        return
    summary.read += 1
    if any(comparison.holds_at(kind) for kind in _AGREE_AT):
        _check(fix, NOTE, AGREES, passed=True)
        summary.agreed += 1
        return
    _check(fix, NOTE, DIFFERS)
    summary.differed += 1
    found = reading_alternatives.from_reading(ours, other, "block")
    if found is not None:
        element.alternatives = found
        summary.with_choices += 1
