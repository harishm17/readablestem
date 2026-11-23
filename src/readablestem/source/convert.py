"""A LaTeX source job end to end: `convert_source` has the shape of `pipeline.run.convert_pdf`
(`convert_pdf` calls it for a source envelope, `source.intake.wrap`), so the worker, the
conversion child, the step cache, the progress events and the spend accounting are the ones a
PDF uses.

1. The upload is unpacked into a private temporary folder (`web.archive_intake`: every limit
   applies again here, in the process that works on the bytes), linted, its main file found
   and its class and packages checked against `source.support` (`source.intake`).
2. LaTeXML runs on it inside the sandbox (`source.latexml`, `core.sandbox`): no network, a
   minimal root, the project read-only, time, memory and output caps.
3. What LaTeXML said is read from its messages before the temporary folder goes
   (`source.report`), and the XHTML it wrote is read into elements and the fixes that need no
   model (`source.ltx_reader`): exact equations, tables, headings. The report is kept on the
   result (`DocumentResult.source_report`).
4. Each figure goes through the ordinary figure step with the project's own picture.
5. The ordinary assembler writes the HTML; the result says `origin="latex"`.

A source that cannot be converted raises `SourceFailed` with a content-free code; the job then
fails with a plain message and nothing partial is kept (`web.views.JOB_ERROR_MESSAGES`). A
source that would lose content outside a formula is one of them (`source.report.stop`): a
missing file, a brace left open, commands that are not defined. Its `detail` names the files
and commands for the job page only; it is stored with the job and never logged.
"""

from __future__ import annotations

import logging
import tempfile
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Literal

from readablestem.config import Settings
from readablestem.core import sandbox, triage
from readablestem.core.page_checks import make
from readablestem.core.types import (
    ConversionOptions,
    ConversionOutput,
    DocumentResult,
    Element,
    Kind,
    ModelCall,
    Notice,
    SourceReport,
)
from readablestem.models.base import VisionClient
from readablestem.pipeline import assemble, decoration, html_checks, step_facts, text_pass
from readablestem.pipeline import notices as notice_rules
from readablestem.pipeline import options as run_options
from readablestem.pipeline import run as pipeline
from readablestem.source import intake, latexml, ltx_reader
from readablestem.source import report as source_report
from readablestem.web import archive_intake as arc
from readablestem.web.isolated_convert import ConversionFailed

# The runs CONTENT_RUNS switches (a source reader makes these; "break" is not one of them).
CONTENT_RUNS = frozenset({"deleted", "strong", "emphasis", "code", "highlight"})

log = logging.getLogger(__name__)

# Result of the conversion, for the job's record: the origin and the LaTeXML run's budget.
FAILURE_CODES = {
    "source_unavailable",  # no sandbox or no LaTeXML on this machine
    "source_busy",  # the machine had no room for the conversion's tasks right now
    "source_failed",  # LaTeXML (or pandoc, for a Word file) did not produce a document
    "source_unreadable",  # it did, and it is not usable
    "source_refused",  # the source is not allowed (the child checks again)
    "timeout",  # it waited too long for another conversion's turn: worth trying again
    "source_timeout",  # the program ran out of time on this source: trying again will not help
    *source_report.STOP_CODES,  # it would lose content outside a formula
}


# The lock every source conversion takes, and how long one may wait for its turn (a conversion
# holds it at most `latexml.LIMITS.wall_s`; the job's own time limit is what ends a longer wait).
GATE_NAME = "source-conversion"
GATE_WAIT_S = 600.0


class SourceFailed(ConversionFailed):
    """The source could not be converted; `code` becomes the job's error code and `detail`
    (names from the source: `source.report.stop`) the job's `error_detail`."""

    def __init__(self, code: str, detail: dict[str, object] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


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


def _project(blob: bytes, tmp: Path, *, beamer_ok: bool) -> tuple[Path, str, bool, set[str]]:
    """Unpack the upload under `tmp/project` and return it, the main file's path, whether it
    is a Beamer deck and the packages its files load. The same checks as the web process made:
    the zip limits, the lint, the main file, and a class or package refused by name
    (`intake.check_support`), which fails with the code and the name the upload page gives."""
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
        try:
            intake.check_support(texts, main)
        except intake.SourceRefused as refused:  # named, like the stop LaTeXML's errors make
            key = "class" if refused.code == "source_unsupported_class" else "package"
            detail: dict[str, object] = {"items": [], "more": 0, key: refused.name}
            raise SourceFailed(refused.code, detail) from None
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
    return project, main, beamer, source_report.loaded_packages(texts)


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
    # The program hit its own limit, of time or of memory: a macro that calls itself, say.
    # The log cannot tell that from a very large source, so both end here.
    if outcome.timed_out or outcome.over_memory:
        raise SourceFailed("source_timeout")
    if outcome.busy:
        raise SourceFailed("source_busy")
    document = out / latexml.DOC_PATH
    if not outcome.ok or not document.is_file():
        raise SourceFailed("source_failed")
    if document.stat().st_size > ltx_reader.MAX_XHTML_BYTES:
        raise SourceFailed("source_failed")
    return document.read_bytes()


def _read_word(blob: bytes) -> ltx_reader.Reading:
    """The reading of a Word file (envelope kind "docx"): the gate again (the web process's
    answer is not trusted here), the working copy, pandoc in the sandbox, then the reader.
    `SourceFailed` with the codes of a LaTeX source: nothing partial is kept."""
    from readablestem.source import docx_package, docx_reader
    from readablestem.source import pandoc as pandoc_step
    from readablestem.web.office_convert import OfficeRejected

    try:
        data = intake.unwrap(blob)[2]
        prepared = docx_package.prepare(data)
    except (intake.EnvelopeError, OfficeRejected):
        raise SourceFailed("source_refused") from None
    except (OSError, ValueError, zipfile.BadZipFile):
        raise SourceFailed("source_unreadable") from None
    try:
        with sandbox.exclusive(GATE_NAME, GATE_WAIT_S):  # one source program at a time
            result = pandoc_step.read("docx", prepared.working)
    except TimeoutError:
        raise SourceFailed("timeout") from None
    except (sandbox.SandboxUnavailable, OSError):
        raise SourceFailed("source_unavailable") from None
    if result.tree is None:
        if result.timed_out or result.failure == pandoc_step.NOT_FINISHED:  # time, memory or size
            raise SourceFailed("source_timeout")
        code = {
            pandoc_step.NO_PANDOC: "source_unavailable",
            pandoc_step.NO_SANDBOX: "source_unavailable",
            pandoc_step.BUSY: "source_busy",
        }
        raise SourceFailed(code.get(result.failure, "source_failed"))
    try:
        reading, _ = docx_reader.read(prepared, result.tree)
    except docx_reader.ReadError:
        raise SourceFailed("source_unreadable") from None
    return reading


def _stop_if_lost(report: SourceReport, *, beamer: bool) -> None:
    """Stop the job when the source would lose content outside a formula
    (`source.report.stop`). Logs the counts, never a name."""
    found = source_report.stop(report, beamer=beamer)
    log.info(
        "source report: stop=%s %s", found[0] if found else None, source_report.summary(report)
    )
    if found is not None:
        raise SourceFailed(*found)


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
    watch = step_facts.Stopwatch()
    watch.stage("source")  # LaTeXML in its sandbox, then reading what it wrote
    pipeline._send(stage_progress, "layout")
    finish = partial(
        _finish,
        started=started,
        watch=watch,
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
    try:
        kind = intake.unwrap(blob)[0]
    except intake.EnvelopeError:
        raise SourceFailed("source_refused") from None
    if kind == "docx":  # a Word file: read from the file itself, with no LaTeXML
        try:
            reading = _read_word(blob)
        except SourceFailed as failed:
            # the job page words the stop for a Word file (`web.views.source_stop_message`): the
            # PDF route is not taken from here, so what the instructor can do is said
            failed.detail = failed.detail or {"kind": "docx"}
            raise
        # What the reader left unread is kept on the stored result like a LaTeX source's, so the
        # job page, the review page's student note and `checked.unread_of` still say it when the
        # result is loaded again; nothing else of a Word file is in a report.
        left = reading.unread_words or reading.unread_pictures
        unread = SourceReport(
            unread_words=reading.unread_words, unread_pictures=reading.unread_pictures
        )
        return finish(reading, origin="docx", report=unread if left else None)
    with tempfile.TemporaryDirectory(prefix="rs-source-") as scratch:
        tmp = Path(scratch)
        project, main, beamer, loaded = _project(
            blob, tmp, beamer_ok=settings.feature_beamer_source
        )
        out = tmp / "out"
        try:
            xhtml = run_latexml(project, main, out, beamer=beamer)
        except SourceFailed as failed:  # what LaTeXML said names the cause, when it can
            if failed.code == "source_failed":
                _stop_if_lost(
                    source_report.parse(out, project=project, loaded=loaded), beamer=beamer
                )
            raise
        # LaTeXML's messages, read now: the folder holding them goes with this block.
        report = source_report.parse(out, project=project, loaded=loaded)
        try:
            xml = out / latexml.XML_PATH
            graphics = ltx_reader.graphics_of(xml.read_bytes(), project) if xml.is_file() else {}
            reading = ltx_reader.read(xhtml, project, graphics, beamer=beamer)
        except ltx_reader.ReadError:
            _stop_if_lost(report, beamer=beamer)
            raise SourceFailed("source_unreadable") from None
    report = source_report.with_marks(report, reading.marks, reading.error_nodes)
    report.figures = list(reading.figures)
    report.unread_words = reading.unread_words
    report.unread_pictures = reading.unread_pictures
    report.unread_start = reading.unread_start
    _stop_if_lost(report, beamer=beamer)
    return finish(reading, origin="beamer" if beamer else "latex", report=report)


def _source_lines(report: SourceReport | None) -> list[Notice]:
    """The lines a source job has beyond its reader's own (`source_error`, `source_unread`): what
    LaTeX reported that the reader could not place, the pictures it could not find, and, for
    every source, that no compile witness ran (nothing compiles a source in a job yet, so the
    line is unconditional: the day a witness runs, it is made only when none did)."""
    found = [make("source_not_compared")]
    if report is not None and report.unplaced_errors:
        found.append(make("source_unplaced", count=report.unplaced_errors))
    if report is not None and report.figures:
        found.append(make("source_figure_missing", count=len(report.figures)))
    return found


def _finish(
    reading: ltx_reader.Reading,
    *,
    started: float,
    watch: step_facts.Stopwatch,
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
    origin: Literal["latex", "beamer", "docx"] = "latex",
    report: SourceReport | None = None,
) -> ConversionOutput:
    """From a reading to a stored result: the figure steps, the assembler, the checks."""
    # Addresses typed with no link, and references by number ("Figure 2", "Section 3"), get the
    # links the PDF text pass gives them; the links the file has are kept as they are.
    elements = text_pass.source_links(
        sorted(reading.elements, key=lambda e: e.order), reading.printed
    )
    for element in elements:  # a source has no pages to number (the reading's are estimates)
        element.page = 0
    # Struck, bold, italic and code text is written with CONTENT_RUNS (on by default); off, it
    # is counted, as the PDF text pass counts it (`text_pass.apply`). Coloured text is kept
    # only with FEATURE_COLOUR: with it off it is counted and the notice `colour_used` says so.
    struck = sum(r.kind == "deleted" for e in elements for r in e.runs)
    coloured = sum(len(e.colour_runs) for e in elements)
    with pipeline._span(tracer, "runs", deleted_runs=struck, written=options.content_runs):
        if not options.content_runs and any(
            r.kind in CONTENT_RUNS for e in elements for r in e.runs
        ):
            elements = [
                e.model_copy(update={"runs": [r for r in e.runs if r.kind not in CONTENT_RUNS]})
                for e in elements
            ]
        if not options.content_runs:  # a table cell's bold, italic and code go the same way
            grids = [e.table for e in elements if e.table]
            grids += [fix.table.grid for fix in reading.fixes.values() if fix.table]
            for cell in (cell for grid in grids for cell in grid.cells if cell.runs):
                cell.runs = []
        if not options.colour and coloured:
            elements = [e.model_copy(update={"colour_runs": []}) for e in elements]
            reading.notices.append(make(ltx_reader.COLOUR_NOTICE, count=coloured))
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
    watch.stage("steps")
    try:
        run.start_stages(elements)
        run.process_page(0, elements)
        run.collect(block=True)
    finally:
        run.pool.shutdown(wait=True, cancel_futures=True)
    decoration.settle(elements, run.fixes, run.crops, kept=assemble.KEPT_IMAGE_WORDS)  # WS6.4
    for element_id in reading.unread_formulas:  # a figure step's fix holds the decision too
        if element_id in run.fixes:
            run.fixes[element_id] = ltx_reader.needs_decision(
                run.fixes[element_id], ltx_reader.FORMULA_UNREAD
            )
    watch.stage("assemble")
    ordered = pipeline._ordered_fixes(elements, run.fixes)
    pipeline._send(stage_progress, "assemble")
    html = assemble.render_html(
        title=reading.title,
        lang=options.language or reading.lang,
        elements=elements,
        fixes=ordered,
        crops=run.crops,
        source_note=source_note,
        notice=notice,
        math_alttext=settings.mathml_alttext,
        origin=origin,
        unread=(reading.unread_words, reading.unread_pictures),
        pipeline_version=pipeline.PIPELINE_VERSION,
    )
    pipeline._send(stage_progress, "checks")
    watch.stage("checks")
    checks = html_checks.static_checks(html)
    # A source has no pages: every request is about the document.
    facts = step_facts.gathered(
        (fact for fix in ordered for fact in step_facts.of_fix(fix, None)),
        watch,
        step_facts.capped(ordered),
    )
    wall_ms = round((time.perf_counter() - started) * 1000)
    triage.stamp(ordered)  # the risk band each decision was asked at
    stats = pipeline._stats(page_list, elements, ordered, run.page_ms, wall_ms)
    derived = [
        *notice_rules.from_fixes(elements, ordered, long_descriptions=options.long_descriptions),
        *notice_rules.from_runs(elements),
        *_source_lines(report),
    ]
    notices: list[Notice] = notice_rules.ordered(
        notice_rules.merge(derived, reading.notices), elements
    )
    result = DocumentResult(
        pipeline_version=pipeline.PIPELINE_VERSION,
        prompt_versions=pipeline._prompt_versions(ordered),
        title=reading.title,
        lang=options.language or reading.lang,
        elements=elements,
        fixes=ordered,
        stats=stats,
        html=html,
        stopped_by=run.stop.reason() if run.stop.scope is not None else None,
        html_checks=checks,
        notices=notices,
        lang_source="user" if options.language else "default",
        origin=origin,
        source_report=report,
    )
    log.info(
        "convert_source done: elements=%d fixes=%d calls=%d cost_usd=%.4f",
        len(elements),
        len(ordered),
        stats.model_calls,
        stats.cost_usd,
    )
    return pipeline.PipelineOutput(result=result, crops=run.crops, checks=checks, facts=facts)
