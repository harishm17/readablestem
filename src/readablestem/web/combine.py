"""Combine the parts of a long source into one file (`pipeline.combine`), for one person.

A source over the page limit is converted in parts, each a document of its own
(`web.routes.parts`); the parts of one source share `Job.part_group` and say which pages they
hold in `Job.part_label`. This module finds the parts a person may join:

- only the person's own (`Job.user_id`), whatever group id is asked about;
- only finished ones (`JobStatus.DONE`);
- only ones still kept: a part past its `retain_until` counts as deleted even before the
  hourly purge removes it (as `job_access` treats it), so a deleted part is never in the file
  and the file is not made from anything the retention promise says is gone;
- only ones whose label names as many pages as the part has (otherwise a page cannot be put
  back where it was in the source).

It reads, never writes: no retention clock is restarted, no row changes, so the route needs
no CSRF token (it is a GET like the other downloads, for the owner only).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from readablestem.core.types import DocumentResult, Fix
from readablestem.pipeline import (
    assemble,
    checked_words,
    combine,
    decoration,
    language,
    output_chars,
)
from readablestem.web import export_store, ranges
from readablestem.web.db import Blob, Job, JobStatus, utcnow

# A part's file name is "<name> (pages 31-59).pdf" (`web.routes.parts.part_filename`).
_PART_SUFFIX = re.compile(r"\s*\(pages [^)]*\)\s*$")
DEFAULT_TITLE = "Combined document"
NAMED_AT_MOST = 3  # parts a sentence names before "and N more" (as the review screen's notices)


@dataclass(frozen=True)
class KeptPart:
    job: Job
    pages: tuple[int, ...]  # 0-based pages of the source this part holds


def kept_parts(session: Session, job: Job, now: datetime | None = None) -> list[KeptPart]:
    """The finished parts of `job`'s source that its owner still keeps, in page order.
    `job` must be a part; one that is not has none. A part with a decision still open is a
    draft and is held back by the same rule as a download (`web.output.export_state`). When two
    parts name a page in common (the same pages converted twice) the newest is kept and the
    older one left out, so the file never holds a page twice."""
    # Imported here: `output` imports the app's dependencies, which import the worker, and the
    # worker imports this module. At the top it made `import readablestem.web.worker` fail on
    # its own (the proof report's limits table, tests/test_ops_limits.py run alone).
    from readablestem.web import output

    if not job.part_group:
        return []
    rows = session.scalars(
        select(Job)
        .where(
            Job.part_group == job.part_group,
            Job.user_id == job.user_id,  # never another person's, even in the same group
            Job.status == JobStatus.DONE,
            Job.retain_until > (now or utcnow()),
            Job.result_json.is_not(None),
        )
        .order_by(Job.created_at.desc(), Job.id)
    ).all()
    kept: list[KeptPart] = []
    taken: set[int] = set()
    for row in rows:  # newest first
        pages = ranges.pages_of_label(row.part_label)
        if pages is None or len(pages) != row.page_count or taken & set(pages):
            continue
        if output.export_state(row).key != output.READY:  # a draft is never joined (SW21)
            continue
        taken.update(pages)
        kept.append(KeptPart(row, pages))
    return sorted(kept, key=lambda k: k.pages[0])


def title_of(first: Job) -> str:
    """What the file is called (`assemble.document_title`): the title the instructor chose for
    the first part, else the title of the file the parts came from (its name without the part
    tag, the extension and separators), never one part's own heading. It names the Word and
    EPUB files too, so a character that is not text in it is written as
    `output_chars.UNREADABLE`."""
    stem, dot, extension = first.filename.rpartition(".")
    name = f"{_PART_SUFFIX.sub('', stem)}{dot}{extension}" if dot else first.filename
    found = assemble.document_title(first.title_override, "", (), name)
    return DEFAULT_TITLE if found.source == assemble.TITLE_STOCK else output_chars.plain(found.text)


def language_check(kept: list[KeptPart]) -> str:
    """The one sentence the combine page says about the file's language ("" when there is
    nothing to say): asked once for the whole, whatever the number of parts, when a part's
    language was assumed or mixed and is not yet confirmed, or when the parts disagree and no
    language has most of them (`combine.common_language`). The answer is made on the part's
    own page, where the language is changed."""
    parts = []
    for item in kept:
        data = item.job.result_json or {}
        codes = [n.get("code") for n in data.get("notices") or [] if isinstance(n, dict)]
        parts.append((str(data.get("lang") or language.DEFAULT_LANG), combine.unconfirmed(codes)))
    pages = [f"pages {item.job.part_label}" for item in kept]  # as the page's rows say them
    if len(parts) < 2:
        return ""
    tag, check = combine.common_language(parts)
    name = language.language_name(tag)
    if check == "unconfirmed":
        named = [pages[n] for n, (_, unconfirmed) in enumerate(parts) if unconfirmed]
        waiting = len(named)
        more = waiting - NAMED_AT_MOST
        listed = ", ".join(named[:NAMED_AT_MOST]) + (f" and {more} more" if more > 0 else "")
        return (
            f"The language of {checked_words.plural(waiting, 'part')} was assumed or could not be "
            f"settled, so the combined file reads {'it' if waiting == 1 else 'them'} in {name}. "
            f"Open the {'part' if waiting == 1 else 'parts'} for {listed}, then confirm the "
            "language before you share the file: a screen reader reads each passage in the "
            "voice of its language."
        )
    if check == "split":
        return (
            f"The parts are in different languages and no language has most of them, so the "
            f"combined file is set to {name}; each part is read in its own language. Check each "
            "part's language before you share the file."
        )
    return ""


def combined_html(
    session: Session, kept: list[KeptPart], *, math_alttext: bool | None = None
) -> tuple[str, str]:
    """(the one HTML file, its title) for `kept` parts. Raises `combine.CombineError`."""
    parts = []
    for item in kept:
        try:
            result = DocumentResult.model_validate(item.job.result_json or {})
        except ValidationError:
            raise combine.CombineError("unreadable_part") from None
        blobs = session.scalars(
            select(Blob).where(Blob.job_id == item.job.id, Blob.kind == "crop")
        ).all()
        crops = {b.element_id: b.content for b in blobs if b.element_id}
        # A part stored before decorations were settled is settled as its own page is when it
        # is made again (`review.rerender_html`), so a decoration reads the same in both.
        first: dict[str, Fix] = {}
        for fix in result.fixes:
            first.setdefault(fix.element_id, fix)
        decoration.settle(result.elements, first, crops, kept=assemble.KEPT_IMAGE_WORDS)
        parts.append(combine.Part(result, crops, item.pages))
    title = title_of(kept[0].job)
    contact = next((k.job.contact_line for k in kept if k.job.contact_line), None)
    return combine.combine(parts, title=title, contact=contact, math_alttext=math_alttext), title


def combined_file(
    session: Session,
    kept: list[KeptPart],
    file_type: str,
    *,
    math_alttext: bool | None = None,
    make: Callable[[str, str], bytes],
) -> bytes:
    """The combined file as `file_type` ("html", or what `make(html, title)` builds), built
    once for these parts and kept like the other exports (`web.export_store`) on the first
    part. The key is the first part's version and `RENDER_VERSION`, and a digest of the title,
    the setting, and each part's id, version, pages and stored result, so a new decision, a
    release, a part added, deleted or made again each build once more. It is deleted with any
    part of the group (`ops.retention.delete_jobs`). Raises what `combined_html` and `make`
    raise; nothing is kept then."""
    title = title_of(kept[0].job)
    contact = next((k.job.contact_line for k in kept if k.job.contact_line), None)
    parts = [
        "{}:{}:{}:{}".format(
            k.job.id,
            k.job.result_version or 0,
            ",".join(map(str, k.pages)),
            hashlib.sha256(json.dumps(k.job.result_json, sort_keys=True).encode()).hexdigest(),
        )
        for k in kept
    ]
    options = "|".join([*parts, str(math_alttext), contact or ""])

    def build() -> bytes:
        html, _ = combined_html(session, kept, math_alttext=math_alttext)
        return html.encode("utf-8") if file_type == "html" else make(html, title)

    return export_store.get_or_build(
        lambda: contextlib.nullcontext(session),
        kept[0].job.id,
        kept[0].job.result_version or 0,
        export_store.COMBINED + file_type,
        "",
        title,
        build,
        options,
    )


def number_part_pages(job: Job, result: DocumentResult) -> bool:
    """For a part of a longer file: record which page of that file each page of the part is
    (`DocumentResult.source_pages`), so the part's page markers, like the combined file's,
    say "PDF page 31" for a page that has no printed label. Printed labels (the PDF's own,
    kept when the part was cut out, or a number read from the page) are not touched. True if
    this changed the result (the file written before is then out of date)."""
    pages = ranges.pages_of_label(job.part_label)
    if pages is None or result.source_pages == list(pages):
        return False
    result.source_pages = list(pages)
    return True
