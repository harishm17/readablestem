"""The recording answers every question the corpus asks, and a changed question is named.

Each file of the manifest is uploaded the way `harness.UPLOADS` says and converted by the
real app from the recorded answers. A conversion that asks for an answer the recording
lacks fails here with the step and what changed, unless the file is on `harness.OWED` with
exactly that many answers still to record.

The same conversions show that a job's step facts account for it: the model work they list
is what the job settled, and their stages hold the job's time (`ops.step_report`).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import harness
import pytest

from readablestem.eval import corpus
from readablestem.ops import step_report
from readablestem.web import step_facts
from readablestem.web.db import session_scope

pytestmark = pytest.mark.workflow

UPLOADS = [
    pytest.param((entry_id, ticked), id="+".join([entry_id, *ticked]))
    for entry_id in corpus.load_manifest()
    for ticked in harness.UPLOADS.get(entry_id, ({},))
]


@pytest.fixture(scope="module", params=UPLOADS)
def converted(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[harness.ReplaySite, str]]:
    """One upload converted on a site of its own (one account keeps at most 40 documents,
    and the corpus has more): the site and the job's id."""
    entry_id, ticked = request.param
    site = harness.ReplaySite(tmp_path_factory.mktemp(entry_id))
    try:
        yield site, site.convert(entry_id, **ticked)
    finally:
        site.close()


def test_the_file_converts_from_the_recording(converted: tuple[harness.ReplaySite, str]) -> None:
    site, job_id = converted
    assert site.job(job_id).status == "done"


def test_the_step_facts_account_for_the_job(converted: tuple[harness.ReplaySite, str]) -> None:
    site, job_id = converted
    job = site.job(job_id)
    with session_scope(site.ctx.factory) as session:
        made = step_report.report(step_facts.measured(session, job_ids=[job_id]))
    assert made.jobs == 1
    assert made.usd == pytest.approx(job.cost_usd, abs=0.005)  # to the cent
    assert made.counted_calls == job.model_calls
    assert made.wall_s is not None
    assert made.stage_s == pytest.approx(made.wall_s, rel=0.05)


@pytest.mark.skipif(harness.MODE != "offline", reason="a refresh records what the tests ask")
def test_a_question_that_changed_since_the_recording_fails_by_name(tmp_path: Path) -> None:
    # The same file, asked with less thinking than the recording was made with.
    changed = {"anthropic_effort_cheap": "high", "anthropic_effort_main": "low"}
    site = harness.ReplaySite(tmp_path, model_answers_offline=True, **changed)
    try:
        with pytest.raises(pytest.fail.Exception, match=r"effort .* recorded, .* asked"):
            site.convert("s15_charts_figures")
    finally:
        site.close()
