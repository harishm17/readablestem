"""The recording answers every question the corpus asks, and a changed question is named.

Each file of the manifest is uploaded the way `harness.UPLOADS` says and converted by the
real app from the recorded answers. A conversion that asks for an answer the recording
lacks fails here with the step and what changed, unless the file is on `harness.OWED` with
exactly that many answers still to record.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import harness
import pytest

from readablestem.eval import corpus

pytestmark = pytest.mark.workflow

UPLOADS = [
    pytest.param(entry_id, ticked, id="+".join([entry_id, *ticked]))
    for entry_id in corpus.load_manifest()
    for ticked in harness.UPLOADS.get(entry_id, ({},))
]


@pytest.fixture
def own_site(tmp_path: Path) -> Iterator[harness.ReplaySite]:
    """A site for one upload: one account keeps at most 40 documents, and the corpus has more."""
    site = harness.ReplaySite(tmp_path)
    yield site
    site.close()


@pytest.mark.parametrize(("entry_id", "ticked"), UPLOADS)
def test_the_file_converts_from_the_recording(
    own_site: harness.ReplaySite, entry_id: str, ticked: dict[str, bool]
) -> None:
    job = own_site.job(own_site.convert(entry_id, **ticked))
    assert job.status == "done"


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
