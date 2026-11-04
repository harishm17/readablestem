"""A LaTeX source job end to end: `convert_source` has the shape of `pipeline.run.convert_pdf`
(`convert_pdf` calls it for a source envelope, `source.intake.wrap`), so the worker, the
conversion child, the step cache, the progress events and the spend accounting are the ones a
PDF uses.

1. The upload is unpacked into a private temporary folder (`web.archive_intake`: every limit
   applies again here, in the process that works on the bytes), linted and its main file found
   (`source.intake`).
2. LaTeXML runs on it inside the sandbox (`source.latexml`, `core.sandbox`): no network, a
   minimal root, the project read-only, time, memory and output caps.
3. The XHTML it wrote is read into elements and the fixes that need no model
   (`source.ltx_reader`): exact equations, tables, headings.
4. Each figure goes through the ordinary figure step with the project's own picture.
5. The ordinary assembler writes the HTML; the result says `origin="latex"`.

A source that cannot be converted raises `SourceFailed` with a content-free code; the job then
fails with a plain message and nothing partial is kept (`web.views.JOB_ERROR_MESSAGES`).
"""

from __future__ import annotations

import logging
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from readablestem.config import Settings
from readablestem.core import sandbox
from readablestem.core.types import (
    ConversionOptions,
    ConversionOutput,
    DocumentResult,
    Element,
    Kind,
    ModelCall,
    Notice,
)
from readablestem.models.base import VisionClient
from readablestem.pipeline import assemble, html_checks
from readablestem.pipeline import notices as notice_rules
from readablestem.pipeline import options as run_options
from readablestem.pipeline import run as pipeline
from readablestem.source import intake, latexml, ltx_reader
from readablestem.web import archive_intake as arc
from readablestem.web.isolated_convert import ConversionFailed

log = logging.getLogger(__name__)

# Result of the conversion, for the job's record: the origin and the LaTeXML run's budget.
FAILURE_CODES = {
    "source_unavailable",  # no sandbox or no LaTeXML on this machine
    "source_busy",  # the machine had no room for the conversion's tasks right now
    "source_failed",  # LaTeXML did not produce a document
    "source_unreadable",  # it did, and it is not usable
    "source_refused",  # the source is not allowed (the child checks again)
    "timeout",
}


# The lock every source conversion takes, and how long one may wait for its turn (a conversion
# holds it at most `latexml.LIMITS.wall_s`; the job's own time limit is what ends a longer wait).
GATE_NAME = "source-conversion"
GATE_WAIT_S = 600.0


class SourceFailed(ConversionFailed):
    """The source could not be converted; `code` becomes the job's error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class _SourceRun(pipeline._Run):
    """The pipeline's run state with the figures' pictures supplied, not cropped from a PDF."""

    images: dict[str, bytes] = field(default_factory=dict)

    def crop_and_submit(self, element: Element) -> None:
        png = self.images.get(element.id)
        if png is None:
            return  # decided before any model work (no picture: a required decision)
        self.raise_if_fatal()
        self.crops[element.id] = png
        if element.id in self.fixes:
            return
        if self.stop.scope is not None:
            self.fixes[element.id] = pipeline._failed(element, self.stop.reason())
            self.step_done(element)
            return
        if self.cache is not None:
            key = pipeline.step_key(
                element, png, self.contexts.get(element.id), self.options, self.settings
            )
            if (kept := self.cache.lookup(element, key)) is not None:
                self.fixes[element.id] = kept
                self.step_done(element)
                return
            self.keys[element.id] = key
        import contextvars
        from functools import partial

        work = partial(
            pipeline._run_step,
            pipeline._Task(element, png, self.contexts.get(element.id), None),
            client=self.client,
            settings=self.settings,
            stop=self.stop,
            tracer=self.tracer,
            observer=self.observer,
        )
        context = contextvars.copy_context()
        self.futures[self.pool.submit(lambda: context.run(work))] = element
        self.pages.add(element.page)

    def process_page(self, page: int, elements: list[Element]) -> None:
        for element in elements:
            if element.kind is Kind.FIGURE:
                self.crop_and_submit(element)
        self.pages.submitted(page)


def _project(blob: bytes, tmp: Path, *, beamer_ok: bool) -> tuple[Path, str, bool]:
    """Unpack the upload under `tmp/project` and return it, the main file's path and whether it
    is a Beamer deck. The same checks as the web process made: the zip limits, the lint, the
    main file."""
    try:
        kind, hint, data = intake.unwrap(blob)
    except intake.EnvelopeError:
        raise SourceFailed("source_refused") from None
    project = tmp / "project"
    project.mkdir()
    try:
        texts = intake.read_texts(kind, data, arc.DEFAULT_LIMITS)
        from readablestem.source import lint

        for text in texts.values():
            if lint.problem(text) is not None:
                raise SourceFailed("source_refused")
        main = intake.find_main(texts, hint)
        beamer = intake._is_beamer(texts[main])
        if beamer and not beamer_ok:
            raise SourceFailed("source_refused")
        if kind == "tex":
            (project / "main.tex").write_bytes(data)
        else:
            arc.extract(data, project)
    except intake.SourceRefused:
        raise SourceFailed("source_refused") from None
    except arc.ArchiveRefused:
        raise SourceFailed("source_refused") from None
    return project, main, beamer


def run_latexml(project: Path, main: str, out: Path, *, beamer: bool = False) -> bytes:
    """LaTeXML on the project in the sandbox; the XHTML it wrote. `SourceFailed` otherwise."""
    if not latexml.installed():
        raise SourceFailed("source_unavailable")
    try:
        # One conversion at a time on this machine, so that the sandbox's tasks x address
        # space is the bound for the whole server (docs/security.md); others wait their turn.
        with sandbox.exclusive(GATE_NAME, GATE_WAIT_S):
            outcome = latexml.convert(project, main, out, beamer=beamer)
    except TimeoutError:
        raise SourceFailed("timeout") from None
    except (sandbox.SandboxUnavailable, OSError):
        raise SourceFailed("source_unavailable") from None
    if outcome.timed_out:
        raise SourceFailed("timeout")
    if outcome.busy:
        raise SourceFailed("source_busy")
    document = out / latexml.DOC_PATH
    if not outcome.ok or not document.is_file():
        raise SourceFailed("source_failed")
    if document.stat().st_size > ltx_reader.MAX_XHTML_BYTES:
        raise SourceFailed("source_failed")
    return document.read_bytes()


def convert_source(
    blob: bytes,
    *,
    settings: Settings,
    client: VisionClient,
    pages: list[int] | None = None,
    progress: pipeline.ProgressFn | None = None,
    tracer: Any = None,
    concurrency: int = 4,
    notice: str | None = None,
    source_note: str | None = None,
    options: ConversionOptions | None = None,
    stage_progress: pipeline.StageProgressFn | None = None,
    step_cache: pipeline.StepCache | None = None,
    call_observer: Callable[[ModelCall], None] | None = None,
    readers: Mapping[str, Any] | None = None,
    time_limit_s: float | None = None,
) -> ConversionOutput:
    """Convert the source in `blob` (`source.intake.wrap`). See the module docstring."""
    if options is None:
        options = ConversionOptions.from_settings(settings)
    with run_options.using(options):
        return _convert(
            blob,
            settings=settings,
            client=client,
            progress=progress,
            tracer=tracer,
            concurrency=concurrency,
            notice=notice,
            source_note=source_note,
            options=options,
            stage_progress=stage_progress,
            step_cache=step_cache,
            call_observer=call_observer,
        )


def _convert(
    blob: bytes,
    *,
    settings: Settings,
    client: VisionClient,
    progress: pipeline.ProgressFn | None,
    tracer: Any,
    concurrency: int,
    notice: str | None,
    source_note: str | None,
    options: ConversionOptions,
    stage_progress: pipeline.StageProgressFn | None,
    step_cache: pipeline.StepCache | None,
    call_observer: Callable[[ModelCall], None] | None,
) -> ConversionOutput:
    from readablestem.observability.tracing import NoopTracer

    tracer = tracer or NoopTracer()
    started = time.perf_counter()
    pipeline._send(stage_progress, "layout")
    with tempfile.TemporaryDirectory(prefix="rs-source-") as scratch:
        tmp = Path(scratch)
        project, main, beamer = _project(blob, tmp, beamer_ok=settings.feature_beamer_source)
        out = tmp / "out"
        xhtml = run_latexml(project, main, out, beamer=beamer)
        try:
            xml = out / latexml.XML_PATH
            graphics = ltx_reader.graphics_of(xml.read_bytes()) if xml.is_file() else {}
            reading = ltx_reader.read(xhtml, project, graphics, beamer=beamer)
        except ltx_reader.ReadError:
            raise SourceFailed("source_unreadable") from None
    return _finish(
        reading,
        started=started,
        settings=settings,
        client=client,
        progress=progress,
        tracer=tracer,
        concurrency=concurrency,
        notice=notice,
        source_note=source_note,
        options=options,
        stage_progress=stage_progress,
        step_cache=step_cache,
        call_observer=call_observer,
        origin="beamer" if beamer else "latex",
    )


def _finish(
    reading: ltx_reader.Reading,
    *,
    started: float,
    settings: Settings,
    client: VisionClient,
    progress: pipeline.ProgressFn | None,
    tracer: Any,
    concurrency: int,
    notice: str | None,
    source_note: str | None,
    options: ConversionOptions,
    stage_progress: pipeline.StageProgressFn | None,
    step_cache: pipeline.StepCache | None,
    call_observer: Callable[[ModelCall], None] | None,
    origin: Literal["latex", "beamer"] = "latex",
) -> ConversionOutput:
    """From a reading to a stored result: the figure steps, the assembler, the checks."""
    elements = sorted(reading.elements, key=lambda e: e.order)
    for element in elements:  # a source has no pages to number (the reading's are estimates)
        element.page = 0
    page_list = [0]
    counts = Counter(e.kind.value for e in elements)
    pipeline._send(stage_progress, "layout", 1, 1, dict(counts))
    run = _SourceRun(
        pdf_bytes=b"",
        settings=settings,
        options=options,
        stage_progress=stage_progress,
        client=client,
        tracer=tracer,
        pages=pipeline._Pages(page_list, progress),
        contexts=pipeline.context_texts(elements),
        pool=ThreadPoolExecutor(max_workers=max(1, concurrency), thread_name_prefix="step"),
        stop=pipeline._Stop(),
        fixes=dict(reading.fixes),
        crops={},
        page_ms=Counter(),
        futures={},
        cache=step_cache,
        observer=call_observer,
        images=dict(reading.images),
    )
    try:
        run.start_stages(elements)
        run.process_page(0, elements)
        run.collect(block=True)
    finally:
        run.pool.shutdown(wait=True, cancel_futures=True)
    ordered = pipeline._ordered_fixes(elements, run.fixes)
    pipeline._send(stage_progress, "assemble")
    html = assemble.render_html(
        title=reading.title,
        lang=reading.lang,
        elements=elements,
        fixes=ordered,
        crops=run.crops,
        source_note=source_note,
        notice=notice,
        math_alttext=settings.mathml_alttext,
        origin=origin,
    )
    pipeline._send(stage_progress, "checks")
    checks = html_checks.static_checks(html)
    wall_ms = round((time.perf_counter() - started) * 1000)
    stats = pipeline._stats(page_list, elements, ordered, run.page_ms, wall_ms)
    derived = notice_rules.from_fixes(
        elements, ordered, long_descriptions=options.long_descriptions
    )
    notices: list[Notice] = notice_rules.ordered(
        notice_rules.merge(derived, reading.notices), elements
    )
    result = DocumentResult(
        pipeline_version=pipeline.PIPELINE_VERSION,
        prompt_versions=pipeline._prompt_versions(ordered),
        title=reading.title,
        lang=reading.lang,
        elements=elements,
        fixes=ordered,
        stats=stats,
        html=html,
        stopped_by=run.stop.reason() if run.stop.scope is not None else None,
        html_checks=checks,
        notices=notices,
        lang_source="default",
        origin=origin,
    )
    log.info(
        "convert_source done: elements=%d fixes=%d calls=%d cost_usd=%.4f",
        len(elements),
        len(ordered),
        stats.model_calls,
        stats.cost_usd,
    )
    return pipeline.PipelineOutput(result=result, crops=run.crops, checks=checks)
