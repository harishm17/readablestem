"""One submit rule (SW43): a repeated submit is answered as the first one was.

A double click, a slow page clicked again, or a tab reloaded after a POST used to reach
each handler's own conflict rule (a stale version, a duplicate name, a pending upload) and
come back as a failure ("not saved") although the first request had worked. Two halves:

- In the page: the `form(key)` macro (`templates/components/forms.html`) writes a hidden
  `request_token`, a random value minted when the form is drawn, beside the CSRF token, and
  the form's busy label. `static/app.js` marks the form as submitting and blocks a second
  submit of it until the next page.
- On the server: `submit_once(key)` wraps the handler of each form in `FORMS`. The first
  request with a token runs the action; the token's row (`db.RequestToken`) is written in
  the same transaction as the action (`_attach`, on the first commit that writes), and
  the answer it got (status, redirect target, one-time notices) is stored on the row. A
  request whose row exists returns that answer and runs nothing; one that arrives while the
  first is still running waits for it (`_LOCKS` in this process; on Postgres the unique key
  makes a second process wait on the row) and returns the same answer.

The token is not a CSRF token and does not replace one: the CSRF check runs first. It is
looked up with the signed-in user's id, so another person's token replays nothing. A
request without a token (a form not drawn by the macro, a script) is handled as before.
A failed request (status 400 or more) keeps no row, so trying again runs again.

Rows hold no document content. They go when the session ends (`forget_user`: sign-out,
blocked, removed), with the account (`ops.retention.erase_user`), and once older than a
session can live (`purge`). The one row without a user is the account deletion's: the
repeat of "Delete my account" arrives signed out, so its row is kept with no user, tied to
that browser's session, until the session would have lapsed.

`FORMS` is the census of the forms that follow the rule: stage 1 holds the review, create
and convert forms and the account deletion; `docs/forms.md` describes the rule.
"""

from __future__ import annotations

import functools
import inspect
import logging
import re
import secrets
import threading
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

import anyio
from fastapi import Request
from sqlalchemy import delete, event, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse, Response

from readablestem.web.database import as_utc
from readablestem.web.db import RequestToken, session_scope, utcnow
from readablestem.web.logsafe import log_event

logger = logging.getLogger(__name__)

REQUEST_TOKEN_FIELD = "request_token"
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
# The session key that ties the account deletion's row (no user) to the browser it was made in.
SESSION_GONE = "rt_gone"
# How long a repeat waits for the first request to finish. An upload may take minutes
# (its probes and Word or PowerPoint conversions have their own budgets in `routes.jobs`).
WAIT_S = 300.0
_POLL_S = 0.05

STILL_SAVING = (
    409,
    "Still saving",
    "Your first click is still being saved. Reload this page in a moment to see the result. "
    "Nothing was done twice.",
)


class Kind(StrEnum):
    CREATE = "create"
    DECIDE = "decide"
    SETTING = "change a setting"
    STATE = "change of state"
    DELETE = "delete"
    SEND = "send"
    SIGN_IN = "sign in"
    SIGN_OUT = "sign out"


# What a pressed button says while its request is on the way. One table for every form,
# so the words match; SW10's button macro reads it too.
BUSY_LABELS = {
    "adding": "Adding…",
    "deleting": "Deleting…",
    "opening": "Opening…",
    "reading": "Reading…",
    "removing": "Removing…",
    "saving": "Saving…",
    "sending": "Sending…",
    "starting": "Starting…",
    "stopping": "Stopping…",
    "undoing": "Undoing…",
    "uploading": "Uploading…",
}

# The repeat rules (SW43, "What identical repeat means"), as `FormRow.repeat` says them.
SAME_TOKEN = "The same token: the first answer again."
REPEAT_RULES = {
    Kind.CREATE: "Another token, and the thing already exists: a notice that links to it.",
    Kind.DECIDE: "Another token, and the item already has this decision by you: Already saved.",
    Kind.SETTING: "Another token: saved again; setting the same value changes nothing.",
    Kind.STATE: "Another token, and it is already in that state: the success it already was.",
    Kind.DELETE: "Another token, and it is already deleted: the Deleted page.",
}


@dataclass(frozen=True)
class FormRow:
    """One POST route that follows the rule: the form key the macro takes, the route, its
    kind, the busy label's key in `BUSY_LABELS`, and what a repeat did before the rule."""

    key: str
    route: str
    kind: Kind
    busy: str
    harm: str
    token: bool = True

    @property
    def repeat(self) -> str:
        return f"{SAME_TOKEN} {REPEAT_RULES.get(self.kind, '')}".strip()

    @property
    def busy_label(self) -> str:
        return BUSY_LABELS[self.busy]


STALE = "a second click on a decision came back as 'Your choice was not saved' (409)"
FORMS: dict[str, FormRow] = {
    row.key: row
    for row in (
        # Decide: the review screen, the list view, the table card and the page tools.
        FormRow("review.decide", "POST /jobs/{job_id}/review/{element_id}", Kind.DECIDE,
                "saving", STALE),
        FormRow("review.undo", "POST /jobs/{job_id}/undo", Kind.DECIDE, "undoing",
                "a second Undo was refused as made from an out-of-date page"),
        FormRow("review.accept_kind", "POST /jobs/{job_id}/accept/{slug}", Kind.DECIDE,
                "saving", "a second accept-many said nothing was left to accept"),
        FormRow("review.symbols", "POST /jobs/{job_id}/symbols", Kind.DECIDE, "saving",
                "a second change of a symbol was refused as stale"),
        FormRow("review.accept_clean_tables", "POST /jobs/{job_id}/tables/accept-clean",
                Kind.DECIDE, "saving", "a second click said no clean table was left"),
        FormRow("review.read_cell", "POST /jobs/{job_id}/tables/{element_id}/read-cell",
                Kind.DECIDE, "reading", "a second click sent the cell to the model again"),
        FormRow("review.edit_element", "POST /jobs/{job_id}/edit/{element_id}", Kind.DECIDE,
                "saving", STALE),
        FormRow("review.add_region", "POST /jobs/{job_id}/pages/{number}/add", Kind.DECIDE,
                "adding", "a second click added the same picture or equation twice"),
        FormRow("review.omit_page", "POST /jobs/{job_id}/pages/{number}/omit", Kind.DECIDE,
                "saving", STALE),
        FormRow("review.restore_page", "POST /jobs/{job_id}/pages/{number}/restore",
                Kind.DECIDE, "saving", STALE),
        FormRow("course.remember", "POST /courses/{course_id}/remember", Kind.DECIDE,
                "saving", "a second click said the symbol was already in the course"),
        FormRow("course.suggestions", "POST /courses/{course_id}/suggestions", Kind.DECIDE,
                "adding", "a second click said the symbols were already in the course"),
        # Create, and the course, share and admin forms that sit with them.
        FormRow("course.create", "POST /courses", Kind.CREATE, "adding",
                "a second click said 'You already have a course with this name'"),
        FormRow("course.starter", "POST /courses/{course_id}/starter", Kind.CREATE,
                "adding", "a second click said the symbols were already in the course"),
        FormRow("course.add_entry", "POST /courses/{course_id}/entries", Kind.CREATE,
                "adding", "a second click said 'This course already has' the symbol"),
        FormRow("course.attach", "POST /courses/{course_id}/documents", Kind.CREATE,
                "adding", "a second click said the documents were not available to add"),
        FormRow("course.rename", "POST /courses/{course_id}/rename", Kind.SETTING, "saving",
                "none: renaming to the same name again changes nothing"),
        FormRow("course.delete_ask", "POST /courses/{course_id}/delete", Kind.DELETE,
                "opening", "none: the course page's button only asks first"),
        FormRow("share.add", "POST /jobs/{job_id}/share", Kind.CREATE, "saving",
                "a second click said 'Saved' again for access already given"),
        FormRow("share.revoke", "POST /jobs/{job_id}/share/{share_id}/revoke", Kind.STATE,
                "removing", "a second click answered 'Page not found'"),
        FormRow("admin.allow", "POST /admin/allowlist", Kind.CREATE, "adding",
                "a second click said 'The allowlist was updated' again"),
        FormRow("admin.remove", "POST /admin/allowlist/remove", Kind.STATE, "removing",
                "none: removing again changes nothing"),
        FormRow("admin.block", "POST /admin/users/block", Kind.STATE, "saving",
                "none: blocking again changes nothing"),
        FormRow("admin.unblock", "POST /admin/users/unblock", Kind.STATE, "saving",
                "none: unblocking again changes nothing"),
        FormRow("admin.link", "POST /admin/linked-emails", Kind.CREATE, "adding",
                "a second click said the address was already linked, as an error"),
        FormRow("admin.unlink", "POST /admin/linked-emails/remove", Kind.STATE, "removing",
                "none: removing again changes nothing"),
        FormRow("report.send", "POST /jobs/{job_id}/report", Kind.SEND, "sending",
                "a second click sent the same report twice"),
        FormRow("admin.report_close", "POST /admin/reports/{report_id}/close", Kind.STATE,
                "saving", "none: closing a closed report changes nothing"),
        # Convert: an upload, the parts of a large file, the sample and "Try again".
        FormRow("upload.create", "POST /jobs", Kind.CREATE, "uploading",
                "a second click said 'Another upload of yours is still being checked'"),
        FormRow("upload.parts", "POST /uploads/{upload_id}", Kind.CREATE, "starting",
                "a second click answered 'Page not found' for the file already started"),
        FormRow("upload.sample", "POST /sample", Kind.CREATE, "opening",
                "none: the sample opens the same document again"),
        FormRow("job.retry", "POST /jobs/{job_id}/retry", Kind.STATE, "starting",
                "a second click said 'Nothing to try again'"),
        FormRow("job.cancel", "POST /jobs/{job_id}/cancel", Kind.STATE, "stopping",
                "a second click said 'Nothing to cancel'"),
        # The account deletion's repeat arrives signed out (see the module docstring).
        FormRow("account.delete", "POST /account/delete", Kind.DELETE, "deleting",
                "a second click landed on the sign-in page"),
    )
}  # fmt: skip


def form_row(key: str) -> FormRow:
    """The registry row of `key`; the macro raises at render when the key is unknown."""
    try:
        return FORMS[key]
    except KeyError:
        raise KeyError(f"form key {key!r} is not in web.forms.FORMS") from None


def mint_token() -> str:
    """A new request token, one per drawn form."""
    return secrets.token_urlsafe(24)


def busy_label(key: str) -> str:
    """The busy words of a form key, or of a `BUSY_LABELS` key."""
    return BUSY_LABELS[key] if key in BUSY_LABELS else form_row(key).busy_label


# --------------------------------------------------------------------------- the claim


@dataclass
class _Claim:
    """The request's token while its handler runs. `_attach` writes the row on the first
    commit that writes; `lost` says another process wrote it first."""

    user_id: str | None
    token: str
    route: str
    attached: bool = False
    lost: bool = False
    row_id: str = field(default_factory=lambda: secrets.token_hex(16))


_CLAIM: ContextVar[_Claim | None] = ContextVar("request_claim", default=None)


def _writes(session: Session) -> bool:
    return bool(
        session.info.get("rt_wrote")
        or session.new
        or session.deleted
        or any(session.is_modified(obj) for obj in session.dirty)
    )


def _note_flush(session: Session, _context: Any) -> None:
    if session.new or session.deleted or any(session.is_modified(o) for o in session.dirty):
        session.info["rt_wrote"] = True


def _note_execute(state: Any) -> None:
    if state.is_insert or state.is_update or state.is_delete:
        state.session.info["rt_wrote"] = True


def _attach(session: Session) -> None:
    """Before the first commit of this request that writes: the token's row, in the same
    transaction as the action, so the two are kept or rolled back together."""
    claim = _CLAIM.get()
    if claim is None or claim.attached or not _writes(session):
        return
    try:
        session.execute(
            insert(RequestToken).values(
                id=claim.row_id,
                user_id=claim.user_id,
                token=claim.token,
                route=claim.route,
                outcome=None,
                created_at=utcnow(),
            )
        )
    except IntegrityError:
        claim.lost = True  # another process holds this token: its answer is the one
        raise
    claim.attached = True


def _reset_flag(session: Session, *_: Any) -> None:
    session.info.pop("rt_wrote", None)


def install(factory: sessionmaker[Session]) -> None:
    """Listen on the app's sessions, so a claimed token rides on the action's commit."""
    event.listen(factory, "after_flush", _note_flush)
    event.listen(factory, "do_orm_execute", _note_execute)
    event.listen(factory, "before_commit", _attach)
    event.listen(factory, "after_commit", _reset_flag)
    event.listen(factory, "after_rollback", _reset_flag)


# --------------------------------------------------------------------------- waiting


class _Locks:
    """One lock per (user, token) in this process: the second request waits here until the
    first has stored its answer."""

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._held: dict[tuple[str | None, str], tuple[threading.Lock, int]] = {}

    def _get(self, key: tuple[str | None, str]) -> threading.Lock:
        with self._mutex:
            lock, users = self._held.get(key, (threading.Lock(), 0))
            self._held[key] = (lock, users + 1)
            return lock

    def _put(self, key: tuple[str | None, str]) -> None:
        with self._mutex:
            lock, users = self._held[key]
            if users <= 1:
                del self._held[key]
            else:
                self._held[key] = (lock, users - 1)

    async def acquire(self, key: tuple[str | None, str]) -> bool:
        lock = self._get(key)
        deadline = time.monotonic() + WAIT_S
        while not lock.acquire(blocking=False):
            if time.monotonic() > deadline:
                self._put(key)
                return False
            await anyio.sleep(_POLL_S)
        return True

    def release(self, key: tuple[str | None, str]) -> None:
        lock, _ = self._held[key]
        lock.release()
        self._put(key)


_LOCKS = _Locks()


@dataclass(frozen=True)
class _Found:
    outcome: dict[str, Any] | None
    created_at: datetime | None


def _lookup(factory: sessionmaker[Session], user_id: str | None, token: str) -> _Found | None:
    with session_scope(factory) as session:
        query = select(RequestToken.outcome, RequestToken.created_at)
        query = query.where(
            RequestToken.token == token,
            RequestToken.user_id.is_(None) if user_id is None else RequestToken.user_id == user_id,
        )
        row = session.execute(query.limit(1)).first()
    return None if row is None else _Found(row[0], as_utc(row[1]))


async def _settled(
    factory: sessionmaker[Session], user_id: str | None, token: str
) -> _Found | None:
    """The row's answer, waiting while its request is still finishing (another process).
    None when the row is gone (that request failed, so this one runs). A row still without
    an answer long after it was written belongs to a request that never finished: it is not
    waited for."""
    deadline = time.monotonic() + WAIT_S
    while True:
        found = await run_in_threadpool(_lookup, factory, user_id, token)
        if found is None or found.outcome is not None or time.monotonic() > deadline:
            return found
        written = found.created_at
        if written is not None and (utcnow() - written).total_seconds() > WAIT_S:
            return found
        await anyio.sleep(_POLL_S * 4)


# --------------------------------------------------------------------------- answers


def _flash_list(request: Request) -> list[dict[str, Any]]:
    from readablestem.web.deps import SESSION_FLASH

    items = request.session.get(SESSION_FLASH, [])
    return list(items) if isinstance(items, list) else []


def _back(request: Request) -> str:
    """Where a stored answer that was a page (not a redirect) sends a repeat: the page the
    form was on, when the browser says so and it is this site; else the documents list."""
    referer = urlsplit(request.headers.get("referer", ""))
    same_site = not referer.netloc or referer.netloc == request.url.netloc
    if same_site and referer.path.startswith("/") and not referer.path.startswith("//"):
        return referer.path + (f"?{referer.query}" if referer.query else "")
    return "/app"


def _outcome(request: Request, response: Response, notices: list[dict[str, Any]]) -> dict:
    location = response.headers.get("location")
    if 300 <= response.status_code < 400 and location:
        return {"status": response.status_code, "location": location, "notices": notices}
    return {"status": 303, "location": _back(request), "notices": notices}


def _replay(request: Request, row: FormRow, outcome: dict[str, Any]) -> Response:
    from readablestem.web.deps import SESSION_FLASH

    notices = [n for n in outcome.get("notices") or [] if isinstance(n, dict)]
    if notices:
        request.session[SESSION_FLASH] = (_flash_list(request) + notices)[-5:]
    log_event(logger, "request_replayed", form=row.key)
    status = outcome.get("status")
    status = status if isinstance(status, int) and 300 <= status < 400 else 303
    return RedirectResponse(str(outcome.get("location") or "/app"), status_code=status)


def _store(factory: sessionmaker[Session], row_id: str, outcome: dict[str, Any] | None) -> None:
    """Keep the answer on the row; None: the request failed, so the row goes."""
    with session_scope(factory) as session:
        if outcome is None:
            session.execute(delete(RequestToken).where(RequestToken.id == row_id))
        else:
            found = session.get(RequestToken, row_id)
            if found is not None:
                found.outcome = outcome


async def _submitted(request: Request, from_form: bool) -> str | None:
    value: object = request.query_params.get(REQUEST_TOKEN_FIELD)
    if value is None and from_form:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            value = (await request.form()).get(REQUEST_TOKEN_FIELD)
    return value if isinstance(value, str) and _TOKEN.fullmatch(value) else None


Handler = Callable[..., Any]


def submit_once(key: str, *, from_form: bool = True) -> Callable[[Handler], Handler]:
    """Wrap the handler of the form `key` in the rule (module docstring). `from_form` False:
    the handler reads the body itself (the upload), so the token comes in the action's
    query string, where the macro puts it for that form."""
    row = form_row(key)

    def decorate(handler: Handler) -> Handler:
        is_async = inspect.iscoroutinefunction(handler)

        async def call(kwargs: dict[str, Any]) -> Response:
            if is_async:
                return await handler(**kwargs)
            return await run_in_threadpool(functools.partial(handler, **kwargs))

        @functools.wraps(handler)
        async def endpoint(**kwargs: Any) -> Response:
            request: Request = kwargs["request"]
            token = await _submitted(request, from_form)
            if token is None:
                return await call(kwargs)
            return await _once(request, row, token, lambda: call(kwargs))

        # FastAPI reads the parameters from here; the handler's module resolves the
        # annotations (they are strings there), not this one.
        endpoint.__signature__ = inspect.signature(handler, eval_str=True)  # ty: ignore[unresolved-attribute]
        return endpoint

    return decorate


async def _once(
    request: Request, row: FormRow, token: str, run: Callable[[], Awaitable[Response]]
) -> Response:
    from readablestem.web.deps import current_user, get_ctx

    factory = get_ctx(request).factory
    user = await run_in_threadpool(current_user, request)
    if user is None:
        # Signed out: only the account deletion's own repeat, in the browser it was made in.
        if request.session.get(SESSION_GONE) == token:
            found = await run_in_threadpool(_lookup, factory, None, token)
            if found is not None and found.outcome is not None:
                return _replay(request, row, found.outcome)
        return await run()
    key = (user.id, token)
    if not await _LOCKS.acquire(key):
        raise _still_saving()
    try:
        found = await _settled(factory, user.id, token)
        if found is not None:
            if found.outcome is None:
                raise _still_saving()
            return _replay(request, row, found.outcome)
        return await _first(request, row, token, user.id, run)
    finally:
        _LOCKS.release(key)


def _still_saving() -> Exception:
    from readablestem.web.deps import PageError

    return PageError(*STILL_SAVING)


async def _first(
    request: Request,
    row: FormRow,
    token: str,
    user_id: str,
    run: Callable[[], Awaitable[Response]],
) -> Response:
    from readablestem.web.deps import SESSION_CSRF, get_ctx

    factory = get_ctx(request).factory
    gone = row.key == "account.delete"  # the user row goes in the same transaction
    claim = _Claim(None if gone else user_id, token, row.key)
    flashes = len(_flash_list(request))
    csrf = request.session.get(SESSION_CSRF)
    reset = _CLAIM.set(claim)
    try:
        response = await run()
    except IntegrityError:
        if not claim.lost:
            raise
        found = await _settled(factory, user_id, token)
        if found is None or found.outcome is None:
            raise _still_saving() from None
        return _replay(request, row, found.outcome)
    except BaseException:
        if claim.attached:
            await run_in_threadpool(_store, factory, claim.row_id, None)
        raise
    finally:
        _CLAIM.reset(reset)
    if not claim.attached:
        return response  # nothing was written: a repeat may run again
    if response.status_code >= 400:
        await run_in_threadpool(_store, factory, claim.row_id, None)
        return response
    notices = _flash_list(request)[flashes:] if not gone else _flash_list(request)
    outcome = _outcome(request, response, notices)
    await run_in_threadpool(_store, factory, claim.row_id, outcome)
    if gone and request.session.get(SESSION_CSRF) is None and isinstance(csrf, str):
        # Signed out now; the repeat of this click still carries the page's CSRF token.
        request.session[SESSION_CSRF] = csrf
        request.session[SESSION_GONE] = token
    return response


# --------------------------------------------------------------------------- clean-up


def forget_user(session: Session, user_id: str) -> None:
    """The person's session ended (sign-out, blocked, removed): their rows go, in the
    caller's transaction."""
    session.execute(delete(RequestToken).where(RequestToken.user_id == user_id))


def purge(session: Session, now: datetime) -> int:
    """Rows older than a session can live (`app.SESSION_MAX_AGE_S`), the account deletion's
    included. Returns how many went."""
    from readablestem.web.app import SESSION_MAX_AGE_S

    cutoff = now - timedelta(seconds=SESSION_MAX_AGE_S)
    result = session.execute(delete(RequestToken).where(RequestToken.created_at < cutoff))
    return int(getattr(result, "rowcount", 0) or 0)
