"""What carry-over needs of the earlier version, kept at the moment of the upload (WS20.2, WS20.3).

An instructor uploads a revised version of a document they reviewed. The worker puts the
earlier version's decisions and edits on the new one when it finishes (`web.carry`,
`web.carry_edits`), which may be minutes later. If the earlier document is deleted by then (by
its owner, or by the hourly purge), the decisions would be lost without a word. So the upload
writes out what carry-over reads of the earlier version and stores it with the new job:

- the result's elements and fixes (the pages left out too), not its HTML, notices or stats;
- for each picture, equation and table, the SHA-256 of its crop, not the crop: a fingerprint is
  all `carry.fingerprint` uses of one. Only the pictures and equations the reviewer added by
  hand keep their bytes, because carry-over copies those onto the new file;
- the structural review edits (`STRUCTURE_ACTIONS`), oldest first;
- how the regions carry-over compares looked in the earlier PDF (a digest of each drawing), and
  whether the new PDF is the very same file, so "this page looks the same" can be answered
  without the earlier PDF (`unchanged_after`);
- the earlier file's name, for the card on the job page. Nothing links to the earlier document.

It is a `Blob` of the new job (`kind="carry"`), so every way a job is deleted
(`ops.retention.delete_jobs`: the owner's delete, the hourly purge, account deletion) removes
it with the job. The worker removes it as soon as carry-over has run (`pop`), whether or not
anything carried: it is a copy of a document's content and has no other use. At finish the
earlier job's own row is used when it is still there (it may hold decisions made after the
upload), and the snapshot only when it is not: both are read into the same `Earlier`.
"""

from __future__ import annotations

import base64
import json
import logging
import zlib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from readablestem.core.types import DocumentResult, DocumentStats
from readablestem.web import carry, carry_edits
from readablestem.web.database import as_utc
from readablestem.web.db import Blob, Job, JobStatus, ReviewEdit, session_scope, utcnow
from readablestem.web.logsafe import log_exception

logger = logging.getLogger(__name__)

KIND = "carry"  # `Blob.kind`
VERSION = 1
# `ReviewEdit.action` of the edits that change the structure and not a fix (web.page_edits):
# their rows are read when a new conversion inherits from an earlier one.
STRUCTURE_ACTIONS = frozenset(
    {
        "edit_text",
        "make_heading",
        "set_role",
        "leave_out_block",
        "restore_block",
        "add_figure",
        "add_equation",
        "omit_page",
        "restore_page",
        "not_equation",
        "read_again",  # listed as not made again (`carry_edits.READ_AGAIN`)
        "undo",  # takes back the edit before it
    }
)
# How a region is drawn when two PDFs are compared: the same for the earlier file at upload and
# the new one at finish (`worker.Worker._unchanged` too).
COMPARE_ZOOM = 1.5
COMPARE_MAX_SIDE_PX = 2400
# Regions drawn at upload at most (carry_edits asks for at most MAX_CHECKS pictures, plus a
# page for each one left out); one not drawn counts as "changed" when asked about.
MAX_LOOKS = 120

Box = carry_edits.Box


@dataclass
class Earlier:
    """The version a new one inherits decisions from: its result, its crops (or their
    digests), its file name, whether it is the job's own earlier result (a try-again), the
    job it came from and its structural edits, oldest first. `looks` is set for a snapshot:
    the digest of each compared region of the earlier PDF (None for the live row, whose PDF
    is compared directly) and `same_file` whether the new upload is the earlier file."""

    result: DocumentResult
    crops: dict[str, carry.Crop]
    name: str
    own: bool
    source_id: str = ""
    edits: list[carry_edits.EditRow] = field(default_factory=list)
    looks: dict[str, str] | None = None
    same_file: bool = False


def read(session: Session, source: Job, *, own: bool) -> Earlier | None:
    """What carry-over reads of a job's stored version (None if its result cannot be read).
    The one place it is read: the live row at finish and the snapshot at upload."""
    try:
        result = DocumentResult.model_validate(source.result_json)
    except ValueError:
        return None
    crops: dict[str, carry.Crop] = {}
    for element_id, png in session.execute(
        select(Blob.element_id, Blob.content).where(Blob.job_id == source.id, Blob.kind == "crop")
    ):
        if element_id:
            hand_made = carry_edits.ADDED_ID.fullmatch(element_id)
            crops[element_id] = bytes(png) if hand_made else carry.crop_digest(png)
    rows = session.scalars(
        select(ReviewEdit)
        .where(
            ReviewEdit.job_id == source.id,
            ReviewEdit.action.in_(STRUCTURE_ACTIONS | {carry.CARRIED_ACTION}),
        )
        .order_by(ReviewEdit.created_at, ReviewEdit.id)
    )
    edits = [
        row
        for r in rows
        if (row := carry_edits.edit_row(r.element_id, r.action, r.before, r.after)) is not None
    ]
    return Earlier(result, crops, source.filename, own, source.id, edits)


# --------------------------------------------------------------------------- at the upload


def take(
    factory: sessionmaker[Session], user_id: str, revises: str, new_pdf: bytes
) -> bytes | None:
    """The snapshot of the earlier version `revises`, ready to store with the new job, or None
    (the upload then behaves as it did before: carry-over reads the earlier row at finish).

    Taken only of the person's own finished, kept document, the same test the upload makes
    (`routes.jobs._users_revision`), read in a transaction of its own that writes nothing: the
    earlier document's deletion time is not touched."""
    try:
        with session_scope(factory) as session:
            source = session.get(Job, revises)
            if (
                source is None
                or source.user_id != user_id
                or source.is_sample
                or source.status != JobStatus.DONE
                or not source.result_json
                or (retain := as_utc(source.retain_until)) is None
                or retain <= utcnow()
            ):
                return None
            earlier = read(session, source, own=False)
            if earlier is None:
                return None
            old_pdf = session.scalar(
                select(Blob.content).where(Blob.job_id == source.id, Blob.kind == "upload")
            )
            earlier.same_file = old_pdf is not None and bytes(old_pdf) == new_pdf
            earlier.looks = {}
            if old_pdf is not None and not earlier.same_file:
                earlier.looks = _looks(earlier, bytes(old_pdf))
            return _dump(earlier)
    except Exception as exc:  # unreadable: the upload goes through without a snapshot
        log_exception(logger, "carry_snapshot_failed", exc, job_id=revises)
        return None


def store(session: Session, job_id: str, data: bytes) -> None:
    session.add(Blob(job_id=job_id, kind=KIND, media_type="application/octet-stream", content=data))


def _key(page: int, box: Box | None) -> str:
    return f"{page}:" + ("page" if box is None else ",".join(f"{v:.2f}" for v in box))


def _looks(earlier: Earlier, pdf: bytes) -> dict[str, str]:
    """A digest of each region carry-over will ask about, drawn from the earlier PDF."""
    from readablestem.web import page_images

    looks: dict[str, str] = {}
    old = carry_edits.with_omitted_back(earlier.result)
    for page, box in carry_edits.regions_compared(old)[:MAX_LOOKS]:
        try:
            drawn = page_images.render_region(
                pdf, page, zoom=COMPARE_ZOOM, max_side=COMPARE_MAX_SIDE_PX, clip=box
            )
        except page_images.RenderFailed:
            continue
        looks[_key(page, box)] = carry.crop_digest(drawn)
    return looks


def _dump(earlier: Earlier) -> bytes:
    """The result as carry-over reads it: elements, fixes, page frames, the pages left
    out (their elements and fixes) and the paragraphs left out, nothing else."""
    result = earlier.result
    kept = DocumentResult(
        pipeline_version=result.pipeline_version,
        stats=DocumentStats(pages=result.stats.pages, cost_usd=0.0, latency_ms=0, model_calls=0),
        elements=result.elements,
        fixes=result.fixes,
        page_sizes=result.page_sizes,
        omitted=[o.model_copy(update={"notices": []}) for o in result.omitted],
        left_out_blocks=[b.model_copy(update={"notices": []}) for b in result.left_out_blocks],
    )
    document: dict[str, Any] = {
        "v": VERSION,
        "name": earlier.name,
        "source_id": earlier.source_id,
        "result": kept.model_dump(mode="json"),
        # A digest is a string; a picture added by hand is its bytes, as text.
        "crops": {
            i: c if isinstance(c, str) else {"png": base64.b64encode(c).decode("ascii")}
            for i, c in earlier.crops.items()
        },
        "edits": [[e.element_id, e.action, e.before, e.after] for e in earlier.edits],
        "looks": earlier.looks or {},
        "same_file": earlier.same_file,
    }
    return zlib.compress(json.dumps(document, separators=(",", ":")).encode("utf-8"), 6)


# --------------------------------------------------------------------------- at the finish


def drop(session: Session, job_id: str) -> None:
    """Remove the job's snapshot, in the caller's transaction: its revision ended without a
    version to carry onto, so nothing will read it."""
    session.execute(delete(Blob).where(Blob.job_id == job_id, Blob.kind == KIND))


def pop(session: Session, job_id: str) -> Earlier | None:
    """The job's snapshot as an `Earlier`, removed in the caller's transaction (None when it
    has none or it cannot be read). Removed whatever it holds: once carry-over has run it has
    no use."""
    row = session.scalar(select(Blob).where(Blob.job_id == job_id, Blob.kind == KIND))
    if row is None:
        return None
    content = bytes(row.content)
    session.execute(delete(Blob).where(Blob.id == row.id))
    try:
        return _load(json.loads(zlib.decompress(content)))
    except (ValueError, zlib.error, KeyError, TypeError) as exc:
        log_exception(logger, "carry_snapshot_unreadable", exc, job_id=job_id)
        return None


def _load(document: dict[str, Any]) -> Earlier | None:
    if document.get("v") != VERSION:
        return None
    crops: dict[str, carry.Crop] = {
        i: c if isinstance(c, str) else base64.b64decode(c["png"])
        for i, c in document["crops"].items()
    }
    edits = [carry_edits.EditRow(*row) for row in document["edits"]]
    return Earlier(
        result=DocumentResult.model_validate(document["result"]),
        crops=crops,
        name=str(document["name"]),
        own=False,
        source_id=str(document["source_id"]),
        edits=edits,
        looks=dict(document["looks"]),
        same_file=bool(document["same_file"]),
    )


def unchanged_after(earlier: Earlier, new_pdf: bytes | None) -> carry_edits.Unchanged:
    """`carry_edits.Unchanged` for a snapshot: the new file drawn the way the earlier one was
    at upload, and the digests compared. A region not drawn then, a file that cannot be read
    or a drawing that fails is "no"."""
    if earlier.same_file:
        return lambda page, box: True
    looks = earlier.looks or {}
    if new_pdf is None or not looks:
        return lambda page, box: False
    from readablestem.web import page_images

    def same(page: int, box: Box | None) -> bool:
        before = looks.get(_key(page, box))
        if before is None:
            return False
        try:
            drawn = page_images.render_region(
                new_pdf, page, zoom=COMPARE_ZOOM, max_side=COMPARE_MAX_SIDE_PX, clip=box
            )
        except page_images.RenderFailed:
            return False
        return carry.crop_digest(drawn) == before

    return same
