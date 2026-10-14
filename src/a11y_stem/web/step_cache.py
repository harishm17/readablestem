"""The finished model steps of a job, kept in the database (T3-1).

A conversion that stops half way (the process restarted, the time limit passed, the AI
service did not answer for the last third of the document) has already paid for the
equations, figures and tables it finished. `DbStepCache` is the `pipeline.run.StepCache`
the worker hands to the converter: it writes each finished step's `Fix` to `step_records`
as the step ends, and when the same job is converted again it gives those back, so "Try
again" asks the model only for what is missing and spends only on that.

A record is used only under the key it was made with (`pipeline.run.step_key`): the same
element, picture, nearby text, options and models. A document that changed, a different
course notation or another model finds nothing and asks again. Failures a new try could
change (the service did not answer, a budget ran out) are never stored (`run.reusable`).

The cache never fails a conversion: a read or write that raises is logged by type and
counts as a miss. Nothing here is logged with content. Records are document-derived and
leave with the job.
"""

from __future__ import annotations

import logging
import threading

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from a11y_stem.core.types import Element, Fix
from a11y_stem.web.db import StepRecord, session_scope
from a11y_stem.web.logsafe import log_event, log_exception

logger = logging.getLogger(__name__)


class DbStepCache:
    """`StepCache` over `step_records` for one job.

    The job's records are read once, on first use. `store` replaces the element's record.
    `reused_usd` and `reused_calls` are what the steps given back cost when they were
    first run: the worker takes them off the new result's totals, since that spend is
    already on the job.
    """

    def __init__(self, factory: sessionmaker[Session], job_id: str) -> None:
        self._factory = factory
        self._job_id = job_id
        self._lock = threading.Lock()
        self._loaded: dict[str, tuple[str, dict]] | None = None
        self.reused = 0
        self.reused_usd = 0.0
        self.reused_calls = 0

    def _records(self) -> dict[str, tuple[str, dict]]:
        if self._loaded is None:
            try:
                with session_scope(self._factory) as session:
                    rows = session.execute(
                        select(
                            StepRecord.element_id, StepRecord.input_key, StepRecord.fix_json
                        ).where(StepRecord.job_id == self._job_id)
                    ).all()
                self._loaded = {r.element_id: (r.input_key, r.fix_json) for r in rows}
            except Exception as exc:  # no records: every step is asked
                log_exception(logger, "step_cache_read_failed", exc, job_id=self._job_id)
                self._loaded = {}
        return self._loaded

    def lookup(self, element: Element, key: str) -> Fix | None:
        with self._lock:
            found = self._records().get(element.id)
        if found is None or found[0] != key:
            return None
        try:
            fix = Fix.model_validate(found[1])
        except ValidationError:  # written by a release this one cannot read
            return None
        if fix.element_id != element.id or fix.kind != element.kind:
            return None
        with self._lock:
            self.reused += 1
            self.reused_usd += fix.cost_usd
            self.reused_calls += len(fix.calls)
        return fix

    def store(self, element: Element, key: str, fix: Fix) -> None:
        payload = fix.model_dump(mode="json")
        try:
            with session_scope(self._factory) as session:
                session.execute(
                    delete(StepRecord).where(
                        StepRecord.job_id == self._job_id, StepRecord.element_id == element.id
                    )
                )
                session.add(
                    StepRecord(
                        job_id=self._job_id,
                        element_id=element.id,
                        input_key=key,
                        fix_json=_without_nul(payload),
                    )
                )
        except Exception as exc:  # the job may have been deleted meanwhile; never fatal
            log_exception(logger, "step_cache_write_failed", exc, job_id=self._job_id)
            return
        with self._lock:
            self._records()[element.id] = (key, payload)

    def log_reuse(self) -> None:
        if self.reused:
            log_event(
                logger,
                "steps_reused",
                job_id=self._job_id,
                steps=self.reused,
                saved_usd=round(self.reused_usd, 6),
            )


def _without_nul(value):
    """The payload with NUL characters taken out of every string (Postgres refuses them in
    JSON; the same rule as `worker.db_safe`)."""
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, dict):
        return {_without_nul(k): _without_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_without_nul(v) for v in value]
    return value


def forget(session: Session, job_id: str) -> None:
    """Delete a job's records (it finished and cannot be tried again, or it is deleted)."""
    session.execute(delete(StepRecord).where(StepRecord.job_id == job_id))
