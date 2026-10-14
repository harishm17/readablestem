"""Edits to what the tool found or missed (T3-5, T3-16): leaving a page out, changing a
paragraph's words, making a line a heading, adding a picture or an equation the tool did
not find, accepting the clean tables in one action.

Every route here is a POST that is CSRF-checked and owner-only (`owned_job` answers 404 for
someone else's job), carries the review version its page was drawn at (a form from an
out-of-date tab is refused, not applied), and writes the way a review decision does
(`routes/review.py`): lock the job row, apply the edit to the stored result, store a
`ReviewEdit`, mark the HTML file out of date (it is made again when somebody asks for it,
`jobs.current_html`), count the write. Nothing renders on the click. All of them work
without JavaScript: a redirect to the Split View with a notice, or the page again (422) with
the error beside the field and what was typed kept.

The rules are in `web.page_edits` (structure) and `web.review` (decisions); Undo puts any of
these back (`web.review.undo_last`). Nothing typed is logged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from a11y_stem.core.types import DocumentResult, Element, Kind
from a11y_stem.web import events, notation, page_edits, page_images, retention
from a11y_stem.web.db import Blob, Job, session_scope
from a11y_stem.web.deps import (
    ELEMENT_ID,
    CurrentUser,
    PageError,
    UserDep,
    enforce,
    flash,
    get_ctx,
    require_csrf,
)
from a11y_stem.web.logsafe import log_event
from a11y_stem.web.page_edits import StructureChange
from a11y_stem.web.review import (
    FieldError,
    ReviewChange,
    ReviewInvalid,
    ReviewStale,
    apply_review,
    bump_result_version,
    cell_region,
    field_id,
    is_stale,
)
from a11y_stem.web.review import accept_clean_tables as accept_clean_tables_rule
from a11y_stem.web.routes.jobs import NOT_FOUND, owned_job, require_done
from a11y_stem.web.routes.review import (
    _landing,
    _review_edit,
    _split_redirect,
    _stale_page,
    done_message,
)
from a11y_stem.web.worker import db_safe

logger = logging.getLogger(__name__)
router = APIRouter()

STALE_FLASH = "Nothing was changed: this document changed after this page was opened."


def page_number(raw: str, count: int) -> int:
    """The 0-based page for a 1-based number in a URL; 404 for anything else."""
    if not raw.isascii() or not raw.isdigit() or not 1 <= int(raw) <= count:
        raise PageError(404, *NOT_FOUND)
    return int(raw) - 1


def write_structure(
    request: Request,
    job_id: str,
    user: CurrentUser,
    version: str,
    make: Callable[[DocumentResult, Job, Session], StructureChange],
) -> tuple[StructureChange, DocumentResult]:
    """One structural edit as one transaction: `make` applies it to the stored result and
    returns what to record. Raises ReviewStale, ReviewInvalid (nothing saved) or a
    PageError (404)."""
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = owned_job(session, job_id, user, lock=True)
        require_done(job)
        if is_stale(job.result_version, version):
            raise ReviewStale()
        result = DocumentResult.model_validate(job.result_json or {})
        open_before = _open_decisions(result)
        change = make(result, job, session)
        for element_id, png in change.crops.items():
            session.add(
                Blob(
                    job_id=job.id,
                    kind="crop",
                    element_id=element_id,
                    media_type="image/png",
                    content=png,
                )
            )
        job.html_stale = True  # made again when somebody asks for it (`jobs.current_html`)
        # Text the reviewer typed: NUL (which Postgres refuses in JSON) is taken out.
        job.result_json = db_safe(result.model_dump(mode="json", exclude={"html"}))
        written = bump_result_version(job)
        retention.restart_clock(job, ctx.settings)
        session.add(_review_edit(job.id, user, change, written))
        events.add_decision(
            session,
            ctx.settings,
            user_id=user.id,
            job=job,
            kind="structure",
            changed=1,
            # "Review completed" only when this edit closed the last decision: an edit to
            # a document that asked for none is not a finished review.
            open_after=_open_decisions(result) if open_before else 1,
        )
    return change, result


def _open_decisions(result: DocumentResult) -> int:
    from a11y_stem.core import triage

    return triage.open_decisions(result.fixes)


def back_to_page(job_id: str, number: int) -> RedirectResponse:
    """The Split View on page `number` (1-based), at the page tools."""
    from a11y_stem.web.routes.split import review_url

    where = review_url(job_id, page=number, anchor="page-tools", tools=True)
    return RedirectResponse(where, status_code=303)


# --------------------------------------------------------------------------- pages left out


def _page_action(
    request: Request,
    job_id: str,
    number: str,
    user: CurrentUser,
    version: str,
    action: str,
) -> Response:
    from a11y_stem.web.routes.split import _owned_page_count

    count = _owned_page_count(request, job_id, user)
    page = page_number(number, count)

    def make(result: DocumentResult, job: Job, session: Session) -> StructureChange:
        pages = page_edits.page_count(result, job.page_count)
        if action == "omit":
            return page_edits.omit_page(result, page, pages=pages)
        return page_edits.restore_page(result, page)

    try:
        change, _ = write_structure(request, job_id, user, version, make)
    except ReviewStale:
        log_event(logger, "review_stale", job_id=job_id)
        flash(request, STALE_FLASH, undo=False)
        return back_to_page(job_id, page + 1)
    except ReviewInvalid as exc:
        log_event(logger, "review_invalid", job_id=job_id, action=f"{action}_page")
        flash(request, " ".join(e.message for e in exc.errors), undo=False)
        return back_to_page(job_id, page + 1)
    log_event(logger, f"page_{action}", job_id=job_id)
    flash(request, change.message)
    return back_to_page(job_id, page + 1)


@router.post("/jobs/{job_id}/pages/{number}/omit", dependencies=[Depends(require_csrf)])
def omit_page(
    request: Request,
    job_id: str,
    number: str,
    user: UserDep,
    version: Annotated[str, Form()] = "",
) -> Response:
    """Leave a page out of review and of the student file (a blank page, a cover)."""
    return _page_action(request, job_id, number, user, version, "omit")


@router.post("/jobs/{job_id}/pages/{number}/restore", dependencies=[Depends(require_csrf)])
def restore_page(
    request: Request,
    job_id: str,
    number: str,
    user: UserDep,
    version: Annotated[str, Form()] = "",
) -> Response:
    """Put a page that was left out back."""
    return _page_action(request, job_id, number, user, version, "restore")


# --------------------------------------------------------------------------- text and headings


def _invalid_screen(
    request: Request,
    job_id: str,
    user: CurrentUser,
    page: int,
    target: str,
    exc: ReviewInvalid,
    *,
    change: str = "",
    add: str = "",
    left: str = "",
    top: str = "",
    right: str = "",
    bottom: str = "",
) -> Response:
    """The Split View on `page` (1-based) again, status 422, with the page tools open and
    the error beside the field it is about, what was typed kept."""
    from a11y_stem.web.routes.split import split_page

    return split_page(
        request,
        job_id,
        user,
        page=str(page),
        invalid=(target, exc),
        status_code=422,
        tools=True,
        change=change,
        add=add,
        left=left,
        top=top,
        right=right,
        bottom=bottom,
    )


def _page_of(request: Request, job_id: str, user: CurrentUser, element_id: str) -> int:
    """The 1-based page of an element of the user's job (404 otherwise)."""
    with session_scope(get_ctx(request).factory) as session:
        job = owned_job(session, job_id, user)
        require_done(job)
        result = DocumentResult.model_validate(job.result_json or {})
    element = next((e for e in result.elements if e.id == element_id), None)
    if element is None:
        raise PageError(404, *NOT_FOUND)
    return element.page + 1


@router.post("/jobs/{job_id}/edit/{element_id}", dependencies=[Depends(require_csrf)])
def edit_element(
    request: Request,
    job_id: str,
    element_id: str,
    user: UserDep,
    action: Annotated[str, Form()] = "",
    text: Annotated[str, Form()] = "",
    level: Annotated[str, Form()] = "",
    version: Annotated[str, Form()] = "",
) -> Response:
    """Change what the tool did not propose: the words of a paragraph, list item or heading
    (`action=text`), or make a paragraph a heading (`action=heading`, with its `level`)."""
    if not ELEMENT_ID.match(element_id) or action not in {"text", "heading"}:
        raise PageError(404, *NOT_FOUND)
    page = _page_of(request, job_id, user, element_id)

    def make(result: DocumentResult, job: Job, session: Session) -> StructureChange:
        if action == "text":
            return page_edits.edit_text(result, element_id, text)
        return page_edits.make_heading(result, element_id, level)

    try:
        change, _ = write_structure(request, job_id, user, version, make)
    except LookupError:
        raise PageError(404, *NOT_FOUND) from None
    except ReviewStale:
        log_event(logger, "review_stale", job_id=job_id)
        flash(request, STALE_FLASH, undo=False)
        return back_to_page(job_id, page)
    except ReviewInvalid as exc:
        log_event(logger, "review_invalid", job_id=job_id, action=f"edit_{action}")
        return _invalid_screen(request, job_id, user, page, element_id, exc, change=element_id)
    log_event(logger, f"edit_{action}", job_id=job_id)
    flash(request, change.message, element_id=element_id)
    return back_to_page(job_id, page)


# --------------------------------------------------------------------------- a region of a page

REGION_PAD_PT = 2.0  # a little page round the region in its crop
REGION_DPI = 200  # as the conversion's own crops (`layout.crop`)
REGION_MAX_SIDE_PX = 2048
PICTURE = "picture"
EQUATION = "equation"


def _page_size(
    request: Request, job_id: str, user: CurrentUser, number: str
) -> tuple[int, float, float]:
    """(0-based page, width, height in points) of a page of the user's job."""
    from a11y_stem.web.routes.split import _load, resolve_page_sizes

    _, result, _ = _load(request, job_id, user)
    pages = page_edits.page_count(result, 0)
    page = page_number(number, pages)
    sizes = resolve_page_sizes(request, job_id, user, result)
    size = sizes.get(page)
    if size is None:
        raise PageError(
            503,
            "The page could not be read",
            "Try again in a moment.",
            headers={"Retry-After": "5"},
        )
    return page, size[0], size[1]


def region_clip(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    p = REGION_PAD_PT
    return box[0] - p, box[1] - p, box[2] + p, box[3] + p


def render_region_crop(pdf: bytes, page: int, box: tuple[float, float, float, float]) -> bytes:
    """The crop of a marked region as the student file will carry it (about 200 dpi)."""
    from a11y_stem.web import page_images

    return page_images.render_region(
        pdf,
        page,
        zoom=REGION_DPI / 72.0,
        max_side=REGION_MAX_SIDE_PX,
        clip=region_clip(box),
    )


@router.get("/jobs/{job_id}/pages/{number}/region.png")
def region_preview(
    request: Request,
    job_id: str,
    number: str,
    user: UserDep,
    left: str = "",
    top: str = "",
    right: str = "",
    bottom: str = "",
) -> Response:
    """The region the reviewer described, as it would be cut from the page: shown before
    anything is added. Rendered now from the job's own upload in the render child, never
    stored; owner only, `private, no-store` like every page image."""
    from a11y_stem.web import page_images
    from a11y_stem.web.routes.split import _png, _render_failed, _upload

    page, width, height = _page_size(request, job_id, user, number)
    try:
        box = page_edits.region_box((width, height), left, top, right, bottom, "region")
    except ReviewInvalid:
        raise PageError(404, *NOT_FOUND) from None
    try:
        with page_images.render_slot(user.id):
            png = render_region_crop(_upload(request, job_id, user), page, box)
    except page_images.RenderFailed as exc:
        return _render_failed(job_id, exc, "region")
    return _png(png)


@router.post("/jobs/{job_id}/pages/{number}/add", dependencies=[Depends(require_csrf)])
def add_region(
    request: Request,
    job_id: str,
    number: str,
    user: UserDep,
    kind: Annotated[str, Form()] = "",
    left: Annotated[str, Form()] = "",
    top: Annotated[str, Form()] = "",
    right: Annotated[str, Form()] = "",
    bottom: Annotated[str, Form()] = "",
    alt: Annotated[str, Form()] = "",
    version: Annotated[str, Form()] = "",
) -> Response:
    """Add a picture the tool did not find (with the reviewer's alt text), or mark a region
    as an equation: the equation step reads it, and the answer is a decision to make."""
    from a11y_stem.web import page_images
    from a11y_stem.web.reread import ReadRefused
    from a11y_stem.web.routes.split import _upload

    if kind not in {PICTURE, EQUATION}:
        raise PageError(404, *NOT_FOUND)
    enforce(request, "edit_region", user.id)
    page, width, height = _page_size(request, job_id, user, number)
    values = {"kind": kind, "left": left, "top": top, "right": right, "bottom": bottom, "alt": alt}
    state = {"add": kind, "left": left, "top": top, "right": right, "bottom": bottom}

    def refuse(message: str, field: str) -> Response:
        exc = ReviewInvalid([FieldError(field_id(field, str(page + 1)), message)], values)
        return _invalid_screen(request, job_id, user, page + 1, f"page-{page + 1}", exc, **state)

    try:
        box = page_edits.region_box(
            (width, height), left, top, right, bottom, field_id("region", str(page + 1))
        )
    except ReviewInvalid as exc:
        exc.values = values
        return _invalid_screen(request, job_id, user, page + 1, f"page-{page + 1}", exc, **state)
    ctx = get_ctx(request)
    try:
        # Check before anything is rendered or spent that this form is not out of date.
        with session_scope(ctx.factory) as session:
            job = owned_job(session, job_id, user)
            require_done(job)
            if is_stale(job.result_version, version):
                raise ReviewStale()
            result = DocumentResult.model_validate(job.result_json or {})
        page_edits.refuse_left_out(result, page, field_id("add", str(page + 1)))
        if kind == PICTURE:
            # The alt text is checked before the crop is cut: a refusal costs nothing.
            page_edits.check_alt_text(alt, page)
        with page_images.render_slot(user.id):
            pdf = _upload(request, job_id, user)
            png = render_region_crop(pdf, page, box)
            layer = page_images.region_text(pdf, page, region_clip(box)) if kind == EQUATION else ""
        read = None
        if kind == EQUATION:
            enforce(request, "reread", user.id)
            read = _read_new_equation(request, job, user, result, page, box, layer, png)

        def make(result: DocumentResult, job: Job, session: Session) -> StructureChange:
            if read is not None:
                return page_edits.add_equation(result, page, box, layer, png, read)
            return page_edits.add_figure(result, page, box, alt, png)

        change, result = write_structure(request, job_id, user, version, make)
    except ReviewStale:
        log_event(logger, "review_stale", job_id=job_id)
        flash(request, STALE_FLASH, undo=False)
        return back_to_page(job_id, page + 1)
    except ReviewInvalid as exc:
        log_event(logger, "review_invalid", job_id=job_id, action=f"add_{kind}")
        exc.values = values
        return _invalid_screen(request, job_id, user, page + 1, f"page-{page + 1}", exc, **state)
    except ReadRefused as exc:
        log_event(logger, "review_invalid", job_id=job_id, action="add_equation")
        return refuse(exc.message, "add")
    except page_images.RenderFailed:
        return refuse("The page could not be read just now. Try again in a moment.", "add")
    log_event(logger, f"add_{kind}", job_id=job_id)
    flash(request, change.message, element_id=change.shows)
    from a11y_stem.web.routes.split import review_url

    return RedirectResponse(
        review_url(job_id, item=change.shows, page=page + 1, anchor="decision-title"),
        status_code=303,
    )


def _read_new_equation(
    request: Request,
    job: Job,
    user: CurrentUser,
    result: DocumentResult,
    page: int,
    box: tuple[float, float, float, float],
    layer: str,
    png: bytes,
):
    """The equation step's read of a marked region, made with no lock held. The element
    exists only here (it is added by the same write that stores the answer)."""
    from a11y_stem.core.types import Element, Kind
    from a11y_stem.pipeline import run as pipeline_run
    from a11y_stem.web.reread import read_equation

    element_id = page_edits.next_added_id(result, page)
    element = Element(
        id=element_id,
        kind=Kind.EQUATION,
        page=page,
        bbox=box,
        order=0,
        text=layer or None,
        display="block",
        reasons=["marked_by_reviewer"],
    )
    probe = result.model_copy(deep=True)
    page_edits.insert_element(probe, element.model_copy())
    placed = sorted(probe.elements, key=lambda e: e.order)
    context = pipeline_run.context_texts(placed).get(element_id)
    return read_equation(request, job, user.id, element, png, context)


# --------------------------------------------------------------------------- tables (T3-11)


def write_review(
    request: Request,
    job_id: str,
    user: CurrentUser,
    version: str,
    make: Callable[[DocumentResult], list[ReviewChange]],
    *,
    kind: str,
) -> tuple[list[ReviewChange], DocumentResult]:
    """Review decisions as one write, as the decision route makes them: lock the job, apply
    `make` to the stored result, store a `ReviewEdit` for each fix it changed, count the
    write. Raises ReviewStale, ReviewInvalid (nothing saved) or a PageError."""
    ctx = get_ctx(request)
    with (
        session_scope(ctx.factory) as session,
        notation.in_force(ctx.factory, ctx.settings, job_id),
    ):
        job = owned_job(session, job_id, user, lock=True)
        require_done(job)
        if is_stale(job.result_version, version):
            raise ReviewStale()
        result = DocumentResult.model_validate(job.result_json or {})
        changes = make(result)
        job.html_stale = True  # made again when somebody asks for it (`jobs.current_html`)
        job.result_json = db_safe(result.model_dump(mode="json", exclude={"html"}))
        written = bump_result_version(job)
        retention.restart_clock(job, ctx.settings)
        session.add_all(_review_edit(job.id, user, c, written) for c in changes)
        events.add_decision(
            session,
            ctx.settings,
            user_id=user.id,
            job=job,
            kind=kind,
            changed=len(changes),
            open_after=_open_decisions(result),
        )
    return changes, result


@router.post("/jobs/{job_id}/tables/accept-clean", dependencies=[Depends(require_csrf)])
def accept_clean_tables(
    request: Request,
    job_id: str,
    user: UserDep,
    version: Annotated[str, Form()] = "",
    view: Annotated[str, Form()] = "",
) -> Response:
    """Accept every table that waits although nothing was found wrong with it, in one
    action (one write: Undo takes all of them back)."""
    from a11y_stem.web.routes.review import GROUP_FLASH, LIST_VIEW, _stale_page, list_url
    from a11y_stem.web.routes.split import review_url

    view = LIST_VIEW if view == LIST_VIEW else "split"
    try:
        changes, result = write_review(
            request, job_id, user, version, accept_clean_tables_rule, kind="group"
        )
    except ReviewStale:
        return _stale_page(request, job_id, user, view, "tables")
    except ReviewInvalid as exc:
        log_event(logger, "review_invalid", job_id=job_id, action="accept_clean")
        flash(request, " ".join(e.message for e in exc.errors), undo=False)
        where = list_url(job_id, "group-tables") if view == LIST_VIEW else review_url(job_id)
        return RedirectResponse(where, status_code=303)
    n = len(changes)
    log_event(logger, "review_accept_clean", job_id=job_id, changed=n)
    flash(
        request,
        f"Accepted {n} clean {'table' if n == 1 else 'tables'}.",
        element_id=GROUP_FLASH + "tables",
    )
    if view == LIST_VIEW:
        return RedirectResponse(_landing(job_id, result, changes[-1].element_id), status_code=303)
    return _split_redirect(job_id, result, None)


@router.post("/jobs/{job_id}/tables/{element_id}/read-cell", dependencies=[Depends(require_csrf)])
def read_cell(
    request: Request,
    job_id: str,
    element_id: str,
    user: UserDep,
    row: Annotated[str, Form()] = "",
    col: Annotated[str, Form()] = "",
    version: Annotated[str, Form()] = "",
) -> Response:
    """Read a table's maths cell with the equation step (the model reads the cell's crop),
    and put what it read through the cell's edit checks as if it had been typed: it is
    saved when it passes, and shown in the cell's box with what differs from the PDF when
    it does not. The same spend controls as marking an equation."""
    from a11y_stem.pipeline import table_step
    from a11y_stem.web.reread import ReadRefused, read_equation
    from a11y_stem.web.routes.review import _invalid_page
    from a11y_stem.web.routes.split import _upload

    if not ELEMENT_ID.match(element_id):
        raise PageError(404, *NOT_FOUND)
    enforce(request, "reread", user.id)
    ctx = get_ctx(request)
    with session_scope(ctx.factory) as session:
        job = owned_job(session, job_id, user)
        require_done(job)
        stale = is_stale(job.result_version, version)
        result = DocumentResult.model_validate(job.result_json or {})
    fix = result.fix_for(element_id)
    element = next((e for e in result.elements if e.id == element_id), None)
    if fix is None or element is None or fix.kind is not Kind.TABLE:
        raise PageError(404, *NOT_FOUND)
    if stale:
        return _stale_page(request, job_id, user, "split", element_id)
    values = {"row": row, "col": col}

    def refuse(message: str) -> Response:
        exc = ReviewInvalid([FieldError(field_id("cell", element_id), message)], values)
        return _invalid_page(request, job_id, user, "split", element_id, exc)

    found = cell_region(fix, element, row, col)
    if isinstance(found, str):
        return refuse(found)
    grid, cell, box = found
    reading = Element(
        id=f"{element_id}-r{cell.row}c{cell.col}",
        kind=Kind.EQUATION,
        page=element.page,
        bbox=box,
        order=0,
        text=table_step.cell_text_layer(grid, cell, element),
        display="inline",
        reasons=["table_cell"],
    )
    try:
        with page_images.render_slot(user.id):
            png = render_region_crop(_upload(request, job_id, user), element.page, box)
        read = read_equation(request, job, user.id, reading, png, element.caption)
    except page_images.RenderFailed:
        return refuse("The page could not be read just now. Try again in a moment.")
    except ReadRefused as exc:
        return refuse(exc.message)
    if read.equation is None or not read.equation.latex.strip():
        return refuse(NOT_READ)
    form = {"change": "cell", "row": row, "col": col, "cell": read.equation.latex}
    try:
        changes, result = write_review(
            request,
            job_id,
            user,
            version,
            lambda stored: [apply_review(stored, element_id, "edit", form)],
            kind="single",
        )
    except ReviewStale:
        return _stale_page(request, job_id, user, "split", element_id)
    except ReviewInvalid as exc:
        # What it read does not pass as it stands: shown in the cell's box, with what
        # differs from the PDF, for the reviewer to correct or to use anyway.
        log_event(logger, "review_invalid", job_id=job_id, action="read_cell")
        return _invalid_page(request, job_id, user, "split", element_id, exc)
    change = changes[0]
    log_event(
        logger, "review", job_id=job_id, element_id=element_id, action="edit", change="read_cell"
    )
    message = "The tool read this cell. " + done_message(change, element)
    flash(request, message, element_id=element_id)
    from a11y_stem.web.routes.split import review_url

    return RedirectResponse(review_url(job_id, item=element_id), status_code=303)


NOT_READ = "The tool could not read this cell as maths. Type the LaTeX yourself."
