"""Exports built once per version of a document (PF7, F-280).

A Word, EPUB, Canvas or Cartridge file is a function of the finished page, so it is built
the first time it is asked for and kept as a `Blob` of the job (`kind="export"`), in the
same table as the upload and the crops: it goes when the job goes (`ops.retention.delete_jobs`,
the foreign key's cascade), under the same retention, and in no other place.

The key (`Blob.element_id`) is `result_version.render_version.file_type.digest`, where the
digest covers the title, the options that change the file (the Canvas course number) and the
page itself, draft banner and all. So a new decision (`jobs.result_version`), a release that
changes the rendering (`assemble.RENDER_VERSION`), a different file type or option, and any
change to the page each build once more; nothing else does. A file kept for an older version
is deleted when a newer one is kept, a job keeps at most `MAX_PER_JOB` files (and, on the
first part of a long upload, `MAX_COMBINED` combined files besides them), and one over
`MAX_BYTES` is built every time and not kept.

What a build found out about its file and the job page says beside the download is kept with it
as a count in a note (`NOTE_KIND`, the same key): the Word file's equations that are written as
words (`export_docx.DocxExport.not_word`). The note goes when its file goes.

The file that joins the parts of one long upload ("Combine parts", `web.combine.combined_file`)
is kept the same way on the first part, with `COMBINED` in front of its file type. It holds
every part, so it is deleted when any part of the group is (`ops.retention.delete_jobs`).

Only a finished file is kept: a build that fails (a picture the format cannot hold, pandoc
busy or missing) stores nothing, and asks again next time. Who may download, whether the
document is a draft, the usage event and the deletion clock stay in the routes, in front of
and after this: a stored file is never handed to anyone the route did not let through.
Exports are not counted against a person's stored bytes (`web.storage.user_bytes`): they can
be made again, so they are never the reason an upload is refused; they do count against the
service's whole (`storage.total_bytes`).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import NamedTuple

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from readablestem.pipeline.assemble import RENDER_VERSION
from readablestem.web.db import Blob

KIND = "export"
NOTE_KIND = "export_note"
COMBINED = "combine-"  # in front of the file type of a combined file ("Combine parts")
MAX_PER_JOB = 6
MAX_COMBINED = 3  # the combined files kept on the first part: HTML, Word, EPUB
MAX_BYTES = 32 * 1024 * 1024
MEDIA_TYPE = "application/octet-stream"

Opener = Callable[[], AbstractContextManager[Session]]


class Built(NamedTuple):
    """A built file and the count its build found out (None: nothing to say)."""

    data: bytes
    note: int | None = None


def key_for(result_version: int, file_type: str, html: str, title: str, options: str = "") -> str:
    """`result_version.render_version.file_type.digest` (at most 64 characters)."""
    digest = hashlib.sha256()
    for part in (file_type, title, options, html):
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return f"{result_version}.{RENDER_VERSION}.{file_type}.{digest.hexdigest()[:32]}"


def get(session: Session, job_id: str, key: str) -> bytes | None:
    found = session.scalar(
        select(Blob.content).where(Blob.job_id == job_id, Blob.kind == KIND, Blob.element_id == key)
    )
    return bytes(found) if found is not None else None


def note_of(session: Session, job_id: str, result_version: int, file_type: str) -> int | None:
    """The count kept with the file of this version of the document (any title or options),
    or None when none is kept: the file was not built yet, or it has no count."""
    found = session.scalar(
        select(Blob.content)
        .where(
            Blob.job_id == job_id,
            Blob.kind == NOTE_KIND,
            Blob.element_id.like(f"{result_version}.{RENDER_VERSION}.{file_type}.%"),
        )
        .order_by(Blob.created_at.desc())
        .limit(1)
    )
    try:
        return int(bytes(found)) if found is not None else None
    except ValueError:
        return None


def put(session: Session, job_id: str, key: str, data: bytes, note: int | None = None) -> None:
    if len(data) > MAX_BYTES:
        return
    current = key.rsplit(".", 2)[0] + "."  # "result_version.render_version."
    session.execute(
        delete(Blob).where(
            Blob.job_id == job_id,
            Blob.kind.in_((KIND, NOTE_KIND)),
            Blob.element_id.not_like(current + "%"),
        )
    )
    # A combined file has its own count: on the first part of a long upload the four file types
    # of the part and the three of the combined file (7) are all reachable, so one count of
    # `MAX_PER_JOB` would make Word, EPUB and Canvas of the part push each other out.
    combined = key.startswith(current + COMBINED)
    own = Blob.element_id.like(current + COMBINED + "%")
    kept = session.execute(
        select(Blob.id, Blob.element_id)
        .where(Blob.job_id == job_id, Blob.kind == KIND, own if combined else ~own)
        .order_by(Blob.created_at.desc(), Blob.id)
    ).all()
    limit = MAX_COMBINED if combined else MAX_PER_JOB
    if len(kept) >= limit:
        old = kept[limit - 1 :]
        session.execute(delete(Blob).where(Blob.id.in_([row.id for row in old])))
        session.execute(  # their notes go with them
            delete(Blob).where(
                Blob.job_id == job_id,
                Blob.kind == NOTE_KIND,
                Blob.element_id.in_([row.element_id for row in old]),
            )
        )
    try:
        with session.begin_nested():  # two first downloads at once: the second finds it kept
            session.add(
                Blob(
                    job_id=job_id,
                    kind=KIND,
                    element_id=key,
                    media_type=MEDIA_TYPE,
                    content=data,
                )
            )
        if note is not None:
            with session.begin_nested():
                session.add(
                    Blob(
                        job_id=job_id,
                        kind=NOTE_KIND,
                        element_id=key,
                        media_type=MEDIA_TYPE,
                        content=str(note).encode(),
                    )
                )
    except IntegrityError:
        pass


def get_or_build(
    sessions: Opener,
    job_id: str,
    result_version: int,
    file_type: str,
    html: str,
    title: str,
    build: Callable[[], bytes | Built],
    options: str = "",
) -> bytes:
    """The kept file for this version, or `build()` (kept for next time). `sessions` opens a
    session for the lookup and again for the keeping, so no connection is held while the file
    is built; a caller that already holds one passes `lambda: nullcontext(session)`."""
    key = key_for(result_version, file_type, html, title, options)
    with sessions() as session:
        data = get(session, job_id, key)
    if data is not None:
        return data
    made = build()
    data, note = (made.data, made.note) if isinstance(made, Built) else (made, None)
    with sessions() as session:
        put(session, job_id, key, data, note)
    return data
