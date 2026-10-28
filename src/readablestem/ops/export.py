"""Export opted-in jobs as labelled examples, for error analysis on Harish's machine.

Only jobs whose owner ticked "Also let the developer look (30 days)"
(`Job.opt_in_retention`) are exported, and only finished ones that have not passed
`retain_until`. For each job, `DIR/<job_id>/` gets:

- `result.json`: the stored `DocumentResult` (elements, fixes, checks, stats; no HTML),
- `crops/<element_id>.png`: the original image of each equation and figure,
- `labels.jsonl`: one line per fix with what the pipeline proposed (status, checks,
  confidence, proposal) and what the reviewer decided (action, the proposal after),
- `EXPIRES`: the job's `retain_until` (ISO 8601).

Not exported: the uploaded PDF, the file name, the owner's email, the output HTML.

The export is a copy of document content outside the database, so it keeps the same
deadline: `prune_exports(DIR)` (`readablestem ops export-opted-in --prune DIR`) deletes
every job directory whose `EXPIRES` has passed. Do not use this before the privacy page
says opted-in results may be copied to the maintainer's machine for analysis and are
deleted by the same date. Reviewer decisions do not outlive the job
as labels; that would need its own retention and wording, and is not built.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from readablestem.web.db import Blob, Job, JobStatus, ReviewEdit

EXPIRES = "EXPIRES"
# The proposal fields of a fix, one per reviewable kind (`core.triage.REVIEWABLE_KINDS`).
PROPOSAL_KEYS = ("equation", "figure", "table", "heading")


@dataclass
class ExportStats:
    jobs: int = 0
    crops: int = 0
    labels: int = 0


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def export_opted_in(session: Session, out_dir: Path, *, now: datetime) -> ExportStats:
    """Write every opted-in, finished, unexpired job under `out_dir` (see the docstring)."""
    stats = ExportStats()
    jobs = session.scalars(
        select(Job)
        .where(
            Job.opt_in_retention.is_(True),
            Job.status == JobStatus.DONE,
            Job.retain_until > now,
            Job.result_json.is_not(None),
        )
        .order_by(Job.finished_at, Job.id)
    ).all()
    for job in jobs:
        target = out_dir / job.id
        (target / "crops").mkdir(parents=True, exist_ok=True)
        result: dict[str, Any] = dict(job.result_json or {})
        (target / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (target / EXPIRES).write_text(_utc(job.retain_until).isoformat(), encoding="utf-8")
        for element_id, png in session.execute(
            select(Blob.element_id, Blob.content).where(Blob.job_id == job.id, Blob.kind == "crop")
        ):
            if element_id and element_id.replace("-", "").replace("_", "").isalnum():
                (target / "crops" / f"{element_id}.png").write_bytes(png)
                stats.crops += 1
        labels = _labels(session, job.id, result)
        with (target / "labels.jsonl").open("w", encoding="utf-8") as fh:
            for label in labels:
                fh.write(json.dumps(label) + "\n")
        stats.labels += len(labels)
        stats.jobs += 1
    return stats


def _labels(session: Session, job_id: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    """One record per fix: the pipeline's proposal and the reviewer's last decision."""
    edits: dict[str, list[ReviewEdit]] = {}
    for edit in session.scalars(
        select(ReviewEdit)
        .where(ReviewEdit.job_id == job_id)
        .order_by(ReviewEdit.created_at, ReviewEdit.id)
    ):
        edits.setdefault(edit.element_id, []).append(edit)
    labels = []
    for fix in result.get("fixes") or []:
        if not isinstance(fix, dict):
            continue
        history = edits.get(str(fix.get("element_id")), [])
        first, last = (history[0], history[-1]) if history else (None, None)
        pipeline = (first.before if first is not None else None) or fix
        labels.append(
            {
                "job_id": job_id,
                "element_id": fix.get("element_id"),
                "kind": fix.get("kind"),
                "pipeline_status": pipeline.get("status") if isinstance(pipeline, dict) else None,
                "confidence": fix.get("confidence"),
                "checks": fix.get("checks"),
                "proposal": {k: pipeline.get(k) for k in PROPOSAL_KEYS}
                if isinstance(pipeline, dict)
                else None,
                "review_action": last.action if last is not None else None,
                "reviewed": {k: (last.after or {}).get(k) for k in PROPOSAL_KEYS}
                if last is not None
                else None,
            }
        )
    return labels


def prune_exports(out_dir: Path, *, now: datetime) -> int:
    """Delete exported job directories whose EXPIRES has passed (or cannot be read)."""
    removed = 0
    if not out_dir.is_dir():
        return 0
    for child in sorted(out_dir.iterdir()):
        stamp = child / EXPIRES
        if not child.is_dir() or not stamp.is_file():
            continue
        try:
            expires = _utc(datetime.fromisoformat(stamp.read_text(encoding="utf-8").strip()))
        except ValueError:
            expires = now  # unreadable: treat as expired, never keep content by accident
        if expires <= now:
            shutil.rmtree(child)
            removed += 1
    return removed
