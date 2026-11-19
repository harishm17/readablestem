"""What a review write did, as content-free outcomes (`decision_outcome`, web/events.py).

Every review write calls `record_write` in its own transaction, before its `ReviewEdit` rows
are made; an Undo calls `record_undo` with the rows it reverses. Each changed thing gives at
most one `events.Outcome`: its kind, the reason code that put it in front of the reviewer
("none" when nothing did), one of six outcomes, where it was read from and what raised it.

- kept: "Accept", bulk accept, "Keep as headings", "Accept the clean tables".
- edited: "Save edit", "Use my edit anyway", "Save, decide later", a changed cell, level or
  description, a symbol changed on the Symbols screen, a paragraph's words, a line made a
  heading.
- replaced: the other reading was taken (a second reader's or the page witness's choice on
  the card, or "Use the grid read from the PDF" on a table).
- rejected: "Keep as image", "Keep the original", a heading kept as bold text.
- removed: "This is not a table", "Not an equation", a page left
  out (a page put back takes it back: -1).
- added: a picture or an equation marked by hand.

One thing, one outcome: a write on something whose content the reviewer already changed
records nothing (that change was counted), a later decision on something already decided
counts with the reason "none" (it is a correction nobody asked for), keeping something
nobody asked about records nothing, and later decisions on content added by hand record
nothing (the addition was counted). What a write recorded is kept in its ReviewEdit's
`after` under `OUTCOME_KEY`, so Undo takes back exactly that. Carried, reused and
course-applied decisions record nothing: no person decided them here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from readablestem.config import Settings
from readablestem.core import triage
from readablestem.core.types import Check, DocumentResult, Fix, Status
from readablestem.web import events
from readablestem.web.db import Job, ReviewEdit
from readablestem.web.page_edits import StructureChange
from readablestem.web.review import (
    CHOICE_KEY,
    NOT_A_TABLE_NOTE,
    STRUCTURE_KEY,
    USE_LOCAL_NOTE,
    WITNESS_KEY,
    ReviewChange,
)

# In a ReviewEdit's `after`: what that write recorded for this change, as
# [kind, reason, outcome, origin, source, count, write mode]; absent when it recorded nothing.
OUTCOME_KEY = "outcome"
# The parts of a fix's snapshot that are its content (what a student gets), not its state.
CONTENT_FIELDS = ("equation", "figure", "heading", "table", "described")
_ADDED_ID = re.compile(r"p\d+-x\d+")  # `page_edits.next_added_id`
_DECIDED = {Status.EDITED.value, Status.REJECTED.value}
_FIX_OUTCOMES = {"accept": "kept", "reject": "rejected"}
_EDITS = {"edit", "add_data_table", "describe", "relabel"}
_STRUCTURE_OUTCOMES = {
    "edit_text": "edited",
    "make_heading": "edited",
    "set_role": "edited",
    "leave_out_block": "removed",
    "restore_block": "removed",
    "omit_page": "removed",
    "restore_page": "removed",
    "add_figure": "added",
    "add_equation": "added",
    "not_equation": "removed",
}

Change = ReviewChange | StructureChange


def record_write(
    session: Session,
    settings: Settings,
    *,
    user_id: str,
    job: Job,
    result: DocumentResult,
    changes: Sequence[Change],
    write_mode: str,
) -> None:
    """Record what this write did (`events.add_outcomes`) and mark each change's `after`
    with it, for Undo. Call before the write's ReviewEdits are made."""
    firsts = _first_befores(session, job.id, [c.element_id for c in changes])
    origin = "source" if result.origin in {"latex", "beamer", "docx"} else "page"
    recorded: list[events.Outcome] = []
    for change in changes:
        structural = isinstance(change, StructureChange)
        outcome = _outcome(change, result, firsts.get((change.element_id, structural)), origin)
        if outcome is None:
            continue
        recorded.append(outcome)
        change.after[OUTCOME_KEY] = [
            outcome.kind,
            outcome.reason,
            outcome.outcome,
            outcome.origin,
            outcome.source,
            outcome.count,
            write_mode,
        ]
    events.add_outcomes(
        session,
        settings,
        user_id=user_id,
        job=job,
        release=events.release_of(result.pipeline_version),
        write_mode=write_mode,
        outcomes=recorded,
    )


def record_undo(
    session: Session,
    settings: Settings,
    *,
    user_id: str,
    job: Job,
    result: DocumentResult,
    reversed_edits: Sequence[ReviewEdit],
) -> None:
    """The outcomes the reversed write recorded, written again with the count negated."""
    stamped = [
        e.after[OUTCOME_KEY]
        for e in reversed_edits
        if isinstance(e.after, dict) and isinstance(e.after.get(OUTCOME_KEY), list)
    ]
    if not stamped:
        return
    write_mode = str(stamped[0][6])
    outcomes = [
        events.Outcome(kind, reason, outcome, origin, source, int(count))
        for kind, reason, outcome, origin, source, count, _ in stamped
    ]
    events.add_outcomes(
        session,
        settings,
        user_id=user_id,
        job=job,
        release=events.release_of(result.pipeline_version),
        write_mode=write_mode,
        outcomes=outcomes,
        sign=-1,
    )


def _first_befores(
    session: Session, job_id: str, element_ids: list[str]
) -> dict[tuple[str, bool], dict]:
    """The `before` of the first ReviewEdit of each element, one for its fix and one for its
    structure (keyed by whether it is structural): what the tool had made, before any
    person's write (carried decisions included, which are not a person's here)."""
    if not element_ids:
        return {}
    rows = session.execute(
        select(ReviewEdit.element_id, ReviewEdit.before)
        .where(ReviewEdit.job_id == job_id, ReviewEdit.element_id.in_(set(element_ids)))
        .order_by(ReviewEdit.created_at, ReviewEdit.id)
    )
    firsts: dict[tuple[str, bool], dict] = {}
    for element_id, before in rows:
        if isinstance(before, dict):
            firsts.setdefault((element_id, STRUCTURE_KEY in before), before)
    return firsts


def _outcome(
    change: Change, result: DocumentResult, first: dict | None, origin: str
) -> events.Outcome | None:
    if isinstance(change, StructureChange):
        return _structure_outcome(change, result, first, origin)
    if _ADDED_ID.fullmatch(change.element_id):
        return None  # content added by hand: the addition was the outcome
    before = change.before
    if _content(before) != _content(first or before):
        return None  # the reviewer's own change already stands, and was counted
    if change.action in _FIX_OUTCOMES:
        outcome = _FIX_OUTCOMES[change.action]
    elif change.action in _EDITS:
        outcome = "edited"
        other_reading = change.after.get(CHOICE_KEY) or change.after.get(WITNESS_KEY)
        if other_reading or change.note == USE_LOCAL_NOTE:
            outcome = "replaced"
        elif change.note == NOT_A_TABLE_NOTE:
            outcome = "removed"
    else:
        return None
    decided = bool(before.get("reviewed")) or before.get("status") in _DECIDED
    reason = events.NO_REASON if decided else _flag_reason(change.fix, before)
    if outcome == "kept" and reason == events.NO_REASON:
        return None  # nobody was asked, and nothing changed
    return _made(change.fix.kind.value, reason, outcome, origin, source=_opener(change.fix))


def _structure_outcome(
    change: StructureChange, result: DocumentResult, first: dict | None, origin: str
) -> events.Outcome | None:
    outcome = _STRUCTURE_OUTCOMES.get(change.action)
    if outcome is None:
        return None
    held = change.before.get(STRUCTURE_KEY) or {}
    if change.action in {"omit_page", "restore_page"}:
        count = -1 if change.action == "restore_page" else 1
        return _made("page", events.NO_REASON, outcome, origin, count)
    if change.action in {"leave_out_block", "restore_block"}:
        count = -1 if change.action == "restore_block" else 1
        return _made("paragraph", events.NO_REASON, outcome, origin, count)
    if change.action == "add_figure":
        return _made("figure", events.NO_REASON, outcome, origin)
    if change.action == "add_equation":
        return _made("equation", events.NO_REASON, outcome, origin)
    element = (held.get("elements") or {}).get(change.element_id) or {}
    kind = str(element.get("kind", "paragraph"))
    if change.action == "not_equation":
        # The equation that was asked about, and the code that asked.
        asked = (held.get("fixes") or {}).get(change.element_id)
        reason, source = events.NO_REASON, triage.PIPELINE
        if isinstance(asked, dict):
            fix = Fix.model_validate(asked)
            if not fix.decided_by_reviewer:
                reason, source = _flag_reason(fix, {}), _opener(fix)
        return _made(kind, reason, outcome, origin, source=source)
    if change.action == "edit_text":
        was = ((first or {}).get(STRUCTURE_KEY) or {}).get("elements", {}).get(change.element_id)
        if isinstance(was, dict) and was.get("text") != element.get("text"):
            return None  # its words were already changed by a person, and counted
    fix = result.fix_for(change.element_id) if change.action == "edit_text" else None
    reason, source = events.NO_REASON, triage.PIPELINE
    if fix is not None and not fix.decided_by_reviewer:
        reason, source = _flag_reason(fix, {}), _opener(fix)
    return _made(kind, reason, outcome, origin, source=source)


def _opener(fix: Fix) -> str:
    """The source that put this fix in front of the reviewer: the first it names."""
    return triage.sources_of(fix)[0]


def _made(
    kind: str,
    reason: str,
    outcome: str,
    origin: str,
    count: int = 1,
    source: str = triage.PIPELINE,
) -> events.Outcome | None:
    if kind not in events.OUTCOME_KINDS:
        return None
    if reason == events.NO_REASON:
        source = events.BROWSING
    return events.Outcome(kind, reason, outcome, origin, source, count)


def _content(snapshot: Mapping[str, Any]) -> tuple:
    return tuple(snapshot.get(name) for name in CONTENT_FIELDS)


def _flag_reason(fix: Fix, before: Mapping[str, Any]) -> str:
    """The one code that put this fix in front of the reviewer, from its checks before the
    write (an edit checks it again): its own reason when it has one, else the first of its
    tier's codes; "none" when its tier gave it none."""
    checks = before.get("checks")
    probe = fix
    if isinstance(checks, list):
        probe = fix.model_copy(update={"checks": [Check.model_validate(c) for c in checks]})
    codes = triage.tier_reasons(probe)
    if not codes:
        return events.NO_REASON
    code = fix.reason if fix.reason in codes else codes[0]
    return code if code in events.reason_codes() else events.OTHER_REASON
