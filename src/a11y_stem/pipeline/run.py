"""The conversion pipeline end to end.

convert_pdf: layout.analyze -> heading_fixes -> per page: crops (serial, calling thread)
-> equation/figure steps (thread pool) -> assemble.render_html ->
html_checks.static_checks.

Design notes:

- PyMuPDF is not thread-safe, so every crop is made on the calling thread. Model calls
  are I/O-bound, so the steps run in a `ThreadPoolExecutor(concurrency)`. Cropping page
  N+1 overlaps with the model calls for page N.
- One element's failure never sinks the document. A step that raises becomes a FAILED
  fix with reason "internal_error" (a crop that fails: "crop_error"). The exception
  message is not logged: it may quote document text.
- A misconfigured run is different: `config.ConfigError` (no API key; WP1's client reads
  it on its first call) or `pricing.UnknownModelError` (an unpriced model) would fail
  every element the same way, so the first one stops the run and is re-raised. The
  caller reports one clear error instead of a document with everything flagged.
- A `BudgetExceeded` from any step (or a step returning reason "budget_<scope>") stops
  new model work: queued tasks are cancelled and every element not yet processed
  becomes FAILED with reason "budget_<scope>". The document is still assembled, with
  the originals kept and flagged.
- Calls a step made before it raised were still billed, so each step gets a recording
  wrapper around the client and those calls are kept on the FAILED fix: cost and call
  counts stay true.
- Static checks of the HTML are stored on `DocumentResult.html_checks` and, as before,
  returned on `PipelineOutput.checks` (a subclass of `ConversionOutput`).
- Scanned pages: a page whose only content is one image covering it, with no text, is a
  picture of a page. Layout drops such an image as a page background, so the pipeline
  checks the pages where layout found no text (`page_kinds`), adds the page as a figure
  element that is FAILED with reason "scanned_page" before any model work (cropped, so the
  output keeps the page image, flagged), and lists the pages on
  `DocumentResult.image_only_pages`. The web app reports them to the user.
- Run-level outcomes are recorded on the result, content-free: `stopped_by` (the
  "budget_<scope>" that stopped model work early, else None) and `html_checks` (the
  static checks below).
- `layout` (WP2) keeps a few opened PDFs cached for cropping. Once the last crop is made
  (or the run fails) the document is released, so a long-lived worker does not keep a
  user's PDF in memory after its job.
- Tracer spans (document, headings, page, step, model_call, assemble) carry ids, page
  numbers, models, token counts, costs, statuses and reason codes, never document text.
  Attributes are scalars only (WP6 drops anything else); each model request gets its own
  `model_call` span opened with `model=`, which WP6 records as a Langfuse generation.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import threading
import time
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Callable, Generator, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from typing import Any

from pydantic import Field

from a11y_stem.config import ConfigError, Settings
from a11y_stem.core.budget import BudgetExceeded
from a11y_stem.core.types import (
    BBox,
    Check,
    ConversionOutput,
    DocumentResult,
    DocumentStats,
    Element,
    Fix,
    Kind,
    ModelCall,
    PageStats,
    Status,
    inline_marker,
)
from a11y_stem.models.base import T, VisionClient, VisionRequest
from a11y_stem.models.pricing import UnknownModelError
from a11y_stem.observability.tracing import NoopTracer, Tracer
from a11y_stem.pipeline import (
    assemble,
    equation_step,
    figure_step,
    heading_step,
    html_checks,
    layout,
    page_kinds,
)

log = logging.getLogger(__name__)

PIPELINE_VERSION = "0.2.0"

ProgressFn = Callable[[int, int], None]  # (pages_done, pages_total)

# Neighbouring text sent with each crop as untrusted context (the client fences it).
CONTEXT_CHARS = 600
_TEXT_KINDS = (Kind.PARAGRAPH, Kind.HEADING)
_MODEL_KINDS = (Kind.EQUATION, Kind.FIGURE)
# Errors that mean the run itself is misconfigured, not that one element is hard: they
# stop the run and propagate (see the module docstring).
FATAL_ERRORS: tuple[type[Exception], ...] = (ConfigError, UnknownModelError)
# Fix.reason of the figure that stands for a scanned page (nothing on it to convert).
SCANNED_PAGE_REASON = "scanned_page"
_PAGE_TEXT_KINDS = (Kind.PARAGRAPH, Kind.HEADING, Kind.LIST_ITEM)


class PipelineOutput(ConversionOutput):
    """`ConversionOutput` plus the static checks of `result.html`.

    The same checks are on `result.html_checks` (stored with the result); this field is
    kept for callers written before that existed.
    """

    checks: list[Check] = Field(default_factory=list)


# --------------------------------------------------------------------------- helpers


class _RecordingClient:
    """Passes requests to the real client and keeps every `ModelCall` it returns.

    Each request also gets a `model_call` span. It is opened with `model=` (known from the
    request before anything is sent) so a WP6 tracer records it as a generation, and it
    is closed with the call's tokens, cost and outcome.
    """

    def __init__(self, inner: VisionClient, tracer: Tracer) -> None:
        self._inner = inner
        self._tracer = tracer
        self.calls: list[ModelCall] = []

    def run(
        self, request: VisionRequest, schema: type[T], *, is_fallback: bool = False
    ) -> tuple[T | None, ModelCall]:
        with _span(
            self._tracer,
            "model_call",
            step=request.step,
            model=request.model,
            prompt_version=request.prompt_version,
            is_fallback=is_fallback,
        ) as span:
            parsed, call = self._inner.run(request, schema, is_fallback=is_fallback)
            self.calls.append(call)
            _annotate(
                span,
                model=call.model,
                outcome=call.outcome,
                attempt=call.attempt,
                input_tokens=call.input_tokens,
                cached_input_tokens=call.cached_input_tokens,
                output_tokens=call.output_tokens,
                reasoning_tokens=call.reasoning_tokens,
                cost_usd=call.cost_usd,
                latency_ms=call.latency_ms,
            )
        return parsed, call

    def __getattr__(self, name: str) -> Any:  # anything else a step may read
        return getattr(self._inner, name)


class _Stop:
    """Run-wide stop signals, set by workers and read by workers and the scheduler.

    `scope`: the first refused spend reservation; the rest of the run fails
    "budget_<scope>" and the document is still assembled. `fatal`: the first
    configuration error (`FATAL_ERRORS`); no further step runs and the run re-raises it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.scope: str | None = None
        self.fatal: Exception | None = None

    def trip(self, scope: str) -> None:
        with self._lock:
            if self.scope is None:
                self.scope = scope

    def abort(self, exc: Exception) -> None:
        with self._lock:
            if self.fatal is None:
                self.fatal = exc

    def reason(self) -> str:
        return f"budget_{self.scope or 'unknown'}"


def _failed(element: Element, reason: str, calls: Iterable[ModelCall] = ()) -> Fix:
    return Fix(
        element_id=element.id,
        kind=element.kind,
        status=Status.FAILED,
        reason=reason,
        calls=list(calls),
    )


def _budget_scope(fix: Fix) -> str | None:
    """Scope if a step reported a refused reservation itself instead of raising."""
    reason = fix.reason or ""
    if fix.status is Status.FAILED and reason.startswith("budget_"):
        return reason.removeprefix("budget_") or "unknown"
    return None


_SCALARS = (str, int, float, bool)


def _annotate(span: Any, **attrs: Any) -> None:
    """Add result attributes to an open span. Never raises.

    WP6's span handle takes `set(**attrs)`; `update(**attrs)` is accepted too (simpler
    tracers). Only scalar values are sent and None is left out: WP6 keeps numbers,
    booleans and short code-like strings and silently drops anything else, so a dict or
    list here would just disappear.
    """
    method = getattr(span, "set", None)
    if not callable(method):
        method = getattr(span, "update", None)
    if not callable(method):
        return
    values = {key: value for key, value in attrs.items() if isinstance(value, _SCALARS)}
    if not values:
        return
    try:
        method(**values)
    except Exception:  # tracing must never break a conversion
        log.debug("span update failed", exc_info=False)


def _status_counts(prefix: str, fixes: Iterable[Fix]) -> dict[str, int]:
    """{"<prefix>_accepted": 3, ...}: per-status counts as flat scalar span attributes."""
    counts = Counter(fix.status.value for fix in fixes)
    return {f"{prefix}_{status}": n for status, n in sorted(counts.items())}


def _single_model(calls: Iterable[ModelCall]) -> str | None:
    """The model a step used, if it used exactly one (each call has its own span)."""
    models = {call.model for call in calls}
    return models.pop() if len(models) == 1 else None


@contextmanager
def _span(tracer: Tracer, name: str, **attrs: Any) -> Generator[Any]:
    """`tracer.span`, except that tracing never changes the outcome of the work.

    A tracer that fails to open or close a span is logged at debug level and ignored;
    an exception from the work itself always propagates (a tracer cannot swallow it).
    """
    try:
        manager = tracer.span(name, **attrs)
        span = manager.__enter__()
    except Exception:
        log.debug("tracer could not open a %s span", name)
        yield None
        return
    try:
        yield span
    except BaseException as exc:
        try:
            manager.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:
            log.debug("tracer could not close a %s span", name)
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception:
            log.debug("tracer could not close a %s span", name)


# --------------------------------------------------------------------------- context


def _without_markers(text: str) -> str:
    """Document text for the model: our inline markers become a readable placeholder."""
    return assemble.INLINE_MARKER_RE.sub("[equation]", text)


def _head(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + " ..."


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else "... " + text[-limit:].lstrip()


def _inline_context(element: Element, parent: Element) -> str | None:
    """A window of the parent paragraph centred on this equation's marker."""
    text = parent.text or ""
    marker = inline_marker(element.id)
    at = text.find(marker)
    if at == -1:
        return None
    half = CONTEXT_CHARS // 2
    before = _without_markers(text[:at])
    after = _without_markers(text[at + len(marker) :])
    if not (before.strip() or after.strip()):
        return None
    return f"{_tail(before, half)}[this equation]{_head(after, half)}".strip()


def _neighbour_context(element: Element, before: str | None, after: str | None) -> str | None:
    half = CONTEXT_CHARS // 2
    label = "[this figure]" if element.kind is Kind.FIGURE else "[this equation]"
    parts = []
    if before:
        parts.append(_tail(_without_markers(before), half))
    parts.append(label)
    if after:
        parts.append(_head(_without_markers(after), half))
    return "\n".join(parts) if (before or after) else None


def _first_text(elements: list[Element], indexes: Iterable[int], skip: str | None) -> str | None:
    """Text of the first element at `indexes` that is not `skip` (the parent paragraph)."""
    return next((elements[i].text for i in indexes if elements[i].id != skip), None)


def context_texts(elements: list[Element]) -> dict[str, str | None]:
    """Untrusted neighbouring text for each equation and figure (reading order).

    Inline equations get a window of their own paragraph around their marker. Block
    equations and figures (and an inline equation alone in its paragraph) get the end
    of the previous text element and the start of the next one, ~CONTEXT_CHARS in
    total. Inline markers become "[equation]".
    """
    by_id = {e.id: e for e in elements}
    text_at = [i for i, e in enumerate(elements) if e.kind in _TEXT_KINDS and e.text]
    result: dict[str, str | None] = {}
    for index, element in enumerate(elements):
        if element.kind not in _MODEL_KINDS:
            continue
        parent = by_id.get(element.parent_id or "")
        if parent is not None and parent.text and inline_marker(element.id) in parent.text:
            context = _inline_context(element, parent)
            if context is not None:
                result[element.id] = context
                continue
        skip = parent.id if parent is not None else None
        pos = bisect_left(text_at, index)  # first text element after this one
        before = _first_text(elements, (text_at[i] for i in range(pos - 1, -1, -1)), skip)
        after = _first_text(elements, (text_at[i] for i in range(pos, len(text_at))), skip)
        result[element.id] = _neighbour_context(element, before, after)
    return result


# --------------------------------------------------------------------------- steps


@dataclass(frozen=True)
class _Task:
    element: Element
    png: bytes
    context_text: str | None


@dataclass(frozen=True)
class _Done:
    element: Element
    fix: Fix
    elapsed_ms: int


def _step_for(kind: Kind) -> tuple[str, Callable[..., Fix]]:
    # Looked up at call time so tests (and the eval harness) can swap a step module's
    # function without touching this one.
    if kind is Kind.EQUATION:
        return "equation", equation_step.transcribe
    return "figure", figure_step.describe


def _validated(fix: object, element: Element, calls: list[ModelCall]) -> Fix:
    if not isinstance(fix, Fix) or fix.element_id != element.id or fix.kind != element.kind:
        log.warning("step returned a fix for another element (kind=%s)", element.kind.value)
        return _failed(element, "internal_error", calls)
    if len(calls) > len(fix.calls):  # keep every billed call, even ones a step dropped
        return fix.model_copy(update={"calls": calls})
    return fix


def _run_step(
    task: _Task, *, client: VisionClient, settings: Settings, stop: _Stop, tracer: Tracer
) -> _Done:
    """Worker body: one element through its step.

    Raises only `FATAL_ERRORS` (after setting `stop.fatal`) and BaseException; any other
    exception becomes a FAILED fix.
    """
    element = task.element
    if stop.fatal is not None:  # the run is failing; this result is never used
        return _Done(element, _failed(element, "internal_error"), 0)
    if stop.scope is not None:  # budget gone while this task was queued
        return _Done(element, _failed(element, stop.reason()), 0)
    step_name, step = _step_for(element.kind)
    recorder = _RecordingClient(client, tracer)
    started = time.perf_counter()
    with _span(
        tracer,
        "step",
        step=step_name,
        kind=element.kind.value,
        element_id=element.id,
        page=element.page,
    ) as span:
        try:
            fix = step(
                element,
                task.png,
                client=recorder,
                settings=settings,
                context_text=task.context_text,
            )
        except BudgetExceeded as exc:
            stop.trip(exc.scope)
            fix = _failed(element, f"budget_{exc.scope}", recorder.calls)
        except FATAL_ERRORS as exc:  # misconfigured: every element would fail the same way
            log.warning("%s step stopped the run: %s", step_name, type(exc).__name__)
            stop.abort(exc)
            raise
        except Exception as exc:  # isolate: one element never sinks the document
            log.warning("%s step failed: %s", step_name, type(exc).__name__)
            fix = _failed(element, "internal_error", recorder.calls)
        else:
            fix = _validated(fix, element, recorder.calls)
            if (scope := _budget_scope(fix)) is not None:
                stop.trip(scope)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        _annotate(
            span,
            status=fix.status.value,
            reason=fix.reason,
            model=_single_model(fix.calls),
            calls=len(fix.calls),
            cost_usd=fix.cost_usd,
            latency_ms=elapsed_ms,
        )
    return _Done(element, fix, elapsed_ms)


def _heading_fixes(elements: list[Element], tracer: Tracer) -> list[Fix]:
    headings = [e for e in elements if e.kind is Kind.HEADING]
    with _span(tracer, "headings", count=len(headings)) as span:
        try:
            fixes = list(heading_step.heading_fixes(elements))
        except Exception as exc:
            log.warning("heading step failed: %s", type(exc).__name__)
            fixes = [_failed(e, "internal_error") for e in headings]
        _annotate(span, fixes=len(fixes), **_status_counts("fixes", fixes))
    return fixes


# --------------------------------------------------------------------------- scheduling


class _Pages:
    """Tracks outstanding tasks per page; reports a page once all of its tasks are done."""

    def __init__(self, pages: list[int], progress: ProgressFn | None) -> None:
        self.total = len(pages)
        self.done = 0
        self._pending: dict[int, int] = dict.fromkeys(pages, 0)
        self._open: set[int] = set(pages)  # still being cropped/submitted
        self._progress = progress

    def add(self, page: int) -> None:
        self._pending[page] = self._pending.get(page, 0) + 1

    def submitted(self, page: int) -> None:
        self._open.discard(page)
        self._maybe_done(page)

    def finished(self, page: int) -> None:
        self._pending[page] -= 1
        self._maybe_done(page)

    def _maybe_done(self, page: int) -> None:
        if page in self._open or self._pending.get(page, 0) or page not in self._pending:
            return
        del self._pending[page]
        self.done += 1
        if self._progress is not None:
            self._progress(self.done, self.total)


@dataclass
class _Run:
    """State of one conversion while its steps are in flight."""

    pdf_bytes: bytes
    settings: Settings
    client: VisionClient
    tracer: Tracer
    pages: _Pages
    contexts: dict[str, str | None]
    pool: ThreadPoolExecutor
    stop: _Stop
    fixes: dict[str, Fix]
    crops: dict[str, bytes]
    page_ms: Counter[int]
    futures: dict[Future[_Done], Element]

    def process_page(self, page: int, elements: list[Element]) -> None:
        """Crop this page's equations and figures here (PyMuPDF), queue their steps."""
        work = [e for e in elements if e.kind in _MODEL_KINDS]
        with _span(self.tracer, "page", page=page, elements=len(elements), tasks=len(work)):
            for element in work:
                self.crop_and_submit(element)
        self.pages.submitted(page)

    def crop_and_submit(self, element: Element) -> None:
        self.raise_if_fatal()
        try:
            png = layout.crop(self.pdf_bytes, element)
        except Exception as exc:
            log.warning("crop failed: %s", type(exc).__name__)
            self.fixes[element.id] = _failed(element, "crop_error")
            return
        self.crops[element.id] = png
        if element.id in self.fixes:  # decided before any model work: a scanned page
            return
        if self.stop.scope is not None:  # still cropped: the output shows the original
            self.fixes[element.id] = _failed(element, self.stop.reason())
            return
        work = partial(
            _run_step,
            _Task(element, png, self.contexts.get(element.id)),
            client=self.client,
            settings=self.settings,
            stop=self.stop,
            tracer=self.tracer,
        )
        # Each task runs in a copy of this context, so a contextvars-based tracer nests the
        # step span under this page's span even though it runs on a pool thread.
        context = contextvars.copy_context()
        future: Future[_Done] = self.pool.submit(lambda: context.run(work))
        self.futures[future] = element
        self.pages.add(element.page)

    def collect(self, *, block: bool) -> None:
        """Record finished tasks; cancel the queue once the budget is gone."""
        while self.futures:
            done, _ = wait(
                list(self.futures), timeout=None if block else 0, return_when=FIRST_COMPLETED
            )
            if not done:
                return
            for future in done:
                self.record(future, self.futures.pop(future))
            self.raise_if_fatal()
            if self.stop.scope is not None:
                for future in self.futures:
                    future.cancel()

    def raise_if_fatal(self) -> None:
        """Re-raise a worker's configuration error here (the pool is then shut down)."""
        if self.stop.fatal is not None:
            raise self.stop.fatal

    def record(self, future: Future[_Done], element: Element) -> None:
        if future.cancelled():
            self.fixes[element.id] = _failed(element, self.stop.reason())
        else:
            done = future.result()  # re-raises a FATAL_ERRORS from the worker
            self.fixes[element.id] = done.fix
            self.page_ms[element.page] += done.elapsed_ms
        self.pages.finished(element.page)


# --------------------------------------------------------------------------- results


def _ordered_fixes(elements: list[Element], fixes: dict[str, Fix]) -> list[Fix]:
    ids = [e.id for e in elements]
    known = set(ids)
    return [fixes[i] for i in ids if i in fixes] + [f for i, f in fixes.items() if i not in known]


def _stats(
    pages: list[int],
    elements: list[Element],
    fixes: list[Fix],
    page_ms: Counter[int],
    wall_ms: int,
) -> DocumentStats:
    """Totals plus per-page numbers.

    `latency_ms` on the document is wall-clock time for the whole conversion. On a page
    it is the time spent inside that page's steps (model calls, retries, validation),
    summed: pages overlap in the thread pool, so per-page wall time would mislead.
    """
    page_of = {e.id: e.page for e in elements}
    per_page = []
    for page in pages:
        page_fixes = [f for f in fixes if page_of.get(f.element_id) == page]
        per_page.append(
            PageStats(
                page=page,
                elements=sum(1 for e in elements if e.page == page),
                fixes=len(page_fixes),
                cost_usd=round(sum(f.cost_usd for f in page_fixes), 8),
                latency_ms=page_ms[page],
            )
        )
    return DocumentStats(
        pages=len(pages),
        cost_usd=round(sum(f.cost_usd for f in fixes), 8),
        latency_ms=wall_ms,
        model_calls=sum(len(f.calls) for f in fixes),
        by_kind=dict(Counter(f.kind.value for f in fixes)),
        by_status=dict(Counter(f.status.value for f in fixes)),
        per_page=per_page,
    )


def _prompt_versions(fixes: list[Fix]) -> dict[str, str]:
    """Declared step prompt versions, overridden by the versions actually sent."""
    versions: dict[str, str] = {}
    for step, module in (("equation", equation_step), ("figure", figure_step)):
        declared = getattr(module, "PROMPT_VERSION", None)
        if isinstance(declared, str) and declared:
            versions[step] = declared
    used: defaultdict[str, set[str]] = defaultdict(set)
    for fix in fixes:
        for call in fix.calls:
            used[call.step].add(call.prompt_version)
    for step, seen in used.items():
        versions[step] = ",".join(sorted(seen))
    return versions


# --------------------------------------------------------------------------- scanned pages


def _image_only_pages(
    pdf_bytes: bytes, page_list: list[int], elements: list[Element]
) -> dict[int, BBox]:
    """Pages that are only a picture of a page, with each page's box.

    Only pages on which layout found no text are opened, so a normal document costs
    nothing here. `layout.analyze` has already rendered every analysed page in its crash
    probe, so reading these pages again in this process is safe. PyMuPDF is not
    thread-safe: hold layout's lock (when it has one) like every other PyMuPDF call.
    """
    with_text = {e.page for e in elements if e.kind in _PAGE_TEXT_KINDS}
    candidates = [page for page in page_list if page not in with_text]
    if not candidates:
        return {}
    import pymupdf

    found: dict[int, BBox] = {}
    lock = getattr(layout, "_LOCK", None)
    with lock if lock is not None else contextlib.nullcontext():
        pymupdf.TOOLS.mupdf_display_errors(False)
        pymupdf.TOOLS.mupdf_display_warnings(False)
        try:
            doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            try:
                for number in candidates:
                    page = doc[number]
                    if page_kinds.page_kind(page) is page_kinds.PageKind.IMAGE_ONLY:
                        rect = page.rect
                        found[number] = (rect.x0, rect.y0, rect.x1, rect.y1)
            finally:
                doc.close()
        except Exception as exc:  # a detection problem never fails the conversion
            log.warning("scanned-page check failed: %s", type(exc).__name__)
            return {}
        finally:
            pymupdf.TOOLS.reset_mupdf_warnings()  # the warning store can quote the PDF
    return found


def _next_id(page: int, elements: list[Element]) -> str:
    """A free "p{page+1}-e{index}" id on this page (one past the highest index used)."""
    prefix = f"p{page + 1}-e"
    used = [
        int(e.id.removeprefix(prefix))
        for e in elements
        if e.id.startswith(prefix) and e.id.removeprefix(prefix).isdecimal()
    ]
    return f"{prefix}{max(used) + 1 if used else 0}"


def _with_page_images(
    elements: list[Element], scans: dict[int, BBox]
) -> tuple[list[Element], list[Fix]]:
    """`elements` plus one figure per scanned page (placed after the page's other
    elements, orders renumbered), and the FAILED fixes that keep them from the model."""
    if not scans:
        return elements, []
    added: list[Element] = []
    result: list[Element] = []
    pending = sorted(scans)
    for element in elements:
        while pending and pending[0] < element.page:
            added.append(_page_image(pending.pop(0), scans, elements))
            result.append(added[-1])
        result.append(element)
    for page in pending:
        added.append(_page_image(page, scans, elements))
        result.append(added[-1])
    renumbered = [e.model_copy(update={"order": i}) for i, e in enumerate(result)]
    return renumbered, [_failed(e, SCANNED_PAGE_REASON) for e in added]


def _page_image(page: int, scans: dict[int, BBox], elements: list[Element]) -> Element:
    return Element(
        id=_next_id(page, elements),
        kind=Kind.FIGURE,
        page=page,
        bbox=scans[page],
        order=0,  # renumbered by the caller
        reasons=[SCANNED_PAGE_REASON],
    )


# --------------------------------------------------------------------------- entry point


class _CachedDocument:
    """Releases `layout`'s cached copy of one PDF, once.

    WP2's `layout` keeps a few opened documents (and their bytes) so crops do not reopen
    the file, and asks the pipeline to call `release(pdf_bytes)` when a document is done
    so user content does not stay in a long-lived worker's memory. Looked up at call
    time: a `layout` without `release` (the stub, a test fake) is fine.
    """

    def __init__(self, pdf_bytes: bytes) -> None:
        self._pdf_bytes = pdf_bytes
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        release = getattr(layout, "release", None)
        if not callable(release):
            return
        try:
            release(self._pdf_bytes)
        except Exception as exc:  # a cache problem must not fail the conversion
            log.warning("layout.release failed: %s", type(exc).__name__)


def _process(
    run: _Run, elements: list[Element], page_list: list[int], cached: _CachedDocument
) -> None:
    """Crop page by page on this thread, run the steps in the pool, collect every result."""
    by_page: defaultdict[int, list[Element]] = defaultdict(list)
    for element in elements:
        by_page[element.page].append(element)
    try:
        for page in page_list:
            run.process_page(page, by_page[page])
            run.collect(block=False)  # report finished pages while cropping the rest
        cached.release()  # every crop is made; the steps only need the PNGs
        run.collect(block=True)
    finally:
        run.pool.shutdown(wait=True, cancel_futures=True)


def convert_pdf(
    pdf_bytes: bytes,
    *,
    settings: Settings,
    client: VisionClient,
    pages: list[int] | None = None,
    progress: ProgressFn | None = None,
    tracer: Tracer | None = None,
    concurrency: int = 4,
    notice: str | None = None,
    source_note: str | None = None,
) -> ConversionOutput:
    """Convert one PDF (0-based `pages`, all by default) into a `PipelineOutput`.

    Raises `layout.LayoutError` when the PDF cannot be read (or has more than
    `settings.max_pages_per_job` pages), and `config.ConfigError` or
    `pricing.UnknownModelError` when the model client is misconfigured (no API key, an
    unpriced model): the first one stops the run, since every element would fail the
    same way. Nothing else from the steps escapes. Calls `progress(pages_done,
    pages_total)` on the calling thread each time a page's steps have all finished. An
    exception from `progress` aborts the run (queued steps are cancelled, running ones
    finish) and propagates.

    `notice` is plain text shown at the top of the HTML (the CLI uses it to mark an
    offline test run); `source_note` is plain text for its "About this conversion"
    section, e.g. an OpenStax attribution.
    """
    tracer = tracer or NoopTracer()
    started = time.perf_counter()
    with _span(tracer, "document", pipeline_version=PIPELINE_VERSION) as doc_span:
        cached = _CachedDocument(pdf_bytes)
        try:
            doc = layout.analyze(pdf_bytes, pages=pages, max_pages=settings.max_pages_per_job)
            elements = sorted(doc.elements, key=lambda e: e.order)
            page_list = list(dict.fromkeys([*doc.pages, *(e.page for e in elements)]))
            scans = _image_only_pages(pdf_bytes, page_list, elements)
            elements, scan_fixes = _with_page_images(elements, scans)
            fixes = {f.element_id: f for f in _heading_fixes(elements, tracer)}
            fixes.update({f.element_id: f for f in scan_fixes})
            run = _Run(
                pdf_bytes=pdf_bytes,
                settings=settings,
                client=client,
                tracer=tracer,
                pages=_Pages(page_list, progress),
                contexts=context_texts(elements),
                pool=ThreadPoolExecutor(max_workers=max(1, concurrency), thread_name_prefix="step"),
                stop=_Stop(),
                fixes=fixes,
                crops={},
                page_ms=Counter(),
                futures={},
            )
            _process(run, elements, page_list, cached)
        finally:
            cached.release()  # also when analysis, a step or `progress` failed

        ordered = _ordered_fixes(elements, run.fixes)
        with _span(tracer, "assemble", elements=len(elements), fixes=len(ordered)):
            html = assemble.render_html(
                title=doc.title,
                lang=doc.lang,
                elements=elements,
                fixes=ordered,
                crops=run.crops,
                source_note=source_note,
                notice=notice,
            )
            checks = html_checks.static_checks(html)
        wall_ms = round((time.perf_counter() - started) * 1000)
        stats = _stats(page_list, elements, ordered, run.page_ms, wall_ms)
        _annotate(
            doc_span,
            pages=stats.pages,
            fixes=len(ordered),
            model_calls=stats.model_calls,
            cost_usd=stats.cost_usd,
            latency_ms=stats.latency_ms,
            reason=run.stop.reason() if run.stop.scope is not None else None,
            image_only_pages=len(scans),
            checks_failed=sum(1 for c in checks if not c.passed),
            **_status_counts("fixes", ordered),
        )
    log.info(
        "convert_pdf done: pages=%d fixes=%d calls=%d cost_usd=%.4f latency_ms=%d",
        stats.pages,
        len(ordered),
        stats.model_calls,
        stats.cost_usd,
        stats.latency_ms,
    )
    result = DocumentResult(
        pipeline_version=PIPELINE_VERSION,
        prompt_versions=_prompt_versions(ordered),
        title=doc.title,
        lang=doc.lang,
        elements=elements,
        fixes=ordered,
        stats=stats,
        html=html,
        stopped_by=run.stop.reason() if run.stop.scope is not None else None,
        image_only_pages=sorted(scans),
        html_checks=checks,
    )
    return PipelineOutput(result=result, crops=run.crops, checks=checks)
