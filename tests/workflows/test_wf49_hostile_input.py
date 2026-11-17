"""WF49. Hostile input: each file of the hostile-input set is uploaded by one person, and a
second person uploads a two-page syllabus behind it.

The set and what each file must end as are in `hostile_set.py`. The site has production's
limits on size, which are the defaults of the code (docs/deploy-railway.md): the harness's
own are narrower and would refuse the larger files for their page count alone.

The queue is run by a worker in a process of its own (`harness.ReplaySite.work`), so a file
that hangs the worker or ends it is seen and stopped. A LaTeX source runs once on each
sandbox this machine can start.

Not checked here, and owed: that the three files which tell a model what to write did not
steer it. That is read from the model's answers, and they are not recorded yet
(docs/eval.md, "The hostile-input set").
"""

from __future__ import annotations

import functools
import io
import os
import resource
import subprocess
import sys
import time
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import harness
import lxml.html
import pytest
from builders import load
from fastapi.testclient import TestClient
from hostile_set import CASES, Case

import source_support
from fixtures.web.support import job_id_from, upload
from readablestem.config import Settings
from readablestem.core import sandbox
from readablestem.source import latexml
from readablestem.web.db import Job, JobStatus, session_scope

pytestmark = pytest.mark.workflow

LIMITS = {
    name: Settings.model_fields[name].default
    for name in ("max_pages_per_job", "max_source_pages", "max_upload_mb", "max_large_upload_mb")
}
# What the second person's two-page file may add to their wait: alone it takes about 4 s.
NEXT_S = 15.0
# Everything a person can open or download of a job.
OUTPUTS = (
    "",
    "/download",
    "/view",
    "/review",
    "/review/list",
    "/listen",
    "/record",
    "/record.csv",
    "/symbols",
    "/share",
    "/download.docx",
    "/download.epub",
    "/download/canvas",
    "/download/cartridge",
)
BACKENDS = ("bwrap", "landlock")
# How a link's target starts when opening it runs something.
RUNS = ("javascript:", "vbscript:", "data:text/html")


@dataclass
class Run:
    """What one upload of a hostile file led to."""

    status: int
    location: str
    codes: list[str]  # of the refusals logged
    upload_s: float
    upload_mb: float  # how far the upload raised the web process's highest memory use
    log: str
    job: Job | None = None
    other: Job | None = None
    worked: harness.Ran | None = None
    outputs: dict[str, bytes] = field(default_factory=dict)

    @property
    def seconds(self) -> float:
        """From the upload to the end: the request, and the job when one was queued."""
        if self.job is None:
            return self.upload_s
        if self.job.started_at is None or self.job.finished_at is None:
            return float("inf")
        return self.upload_s + (self.job.finished_at - self.job.started_at).total_seconds()

    @property
    def megabytes(self) -> float:
        worked = self.worked
        return max(
            self.upload_mb, worked.own_mb if worked else 0, worked.started_mb if worked else 0
        )


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[harness.ReplaySite]:
    site = harness.ReplaySite(tmp_path_factory.mktemp("site"), **LIMITS)
    if latexml.installed() and sandbox.available():
        # Tests do not run the sandbox probe at startup; this is the state it leaves where
        # LaTeX can be read.
        site.ctx.sandbox.set("pass", {})
    yield site
    site.close()


@functools.cache
def can_start(backend: str) -> bool:
    """Whether this machine can start that sandbox. Asked in a new process: the choice is
    made once in the life of a process."""
    asked = subprocess.run(
        [sys.executable, "-c", "from readablestem.core import sandbox; print(sandbox.backend())"],
        env=os.environ | {sandbox.BACKEND_ENV: backend},
        capture_output=True,
        text=True,
        timeout=120,
    )
    return asked.stdout.strip() == backend


def able(case: Case, backend: str) -> None:
    """Skips a source where this machine cannot read LaTeX. CI's jobs that have LaTeXML
    say so (`source_support.REQUIRED`), and there bubblewrap must start."""
    if not case.file.endswith((".tex", ".zip")):
        return
    needed = backend or "bwrap"
    if source_support.REQUIRED and needed == "bwrap":
        assert latexml.installed() and can_start("bwrap"), "LaTeXML and bubblewrap are required"
    elif not latexml.installed():
        pytest.skip("LaTeXML is not installed")
    elif not (can_start(backend) if backend else sandbox.available()):
        pytest.skip(f"this machine cannot start the {backend or 'a'} sandbox")


def outputs(site: harness.ReplaySite, job_id: str) -> dict[str, bytes]:
    """Each page and download of the job the owner gets, by name; a zip as its members."""
    found: dict[str, bytes] = {}
    for path in OUTPUTS:
        got = harness.fetch(site.browser, f"/jobs/{job_id}{path}")
        if got.status_code != 200:
            continue
        if got.content.startswith(b"PK"):
            with zipfile.ZipFile(io.BytesIO(got.content)) as archive:
                for name in archive.namelist():
                    found[f"{path}: {name}"] = archive.read(name)
        else:
            found[path or "/"] = got.content
    return found


def upload_and_work(
    site: harness.ReplaySite,
    other: tuple[TestClient, bytes],
    case: Case,
    data: bytes,
    backend: str,
) -> Run:
    logged = len(site.logged)
    high = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    start = time.monotonic()
    sent = upload(site.browser, data, filename=case.file)
    run = Run(
        status=sent.status_code,
        location=sent.headers.get("location", ""),
        codes=[],
        upload_s=time.monotonic() - start,
        upload_mb=(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - high) / 1024,
        log="",
    )
    if run.location.startswith("/jobs/"):
        job_id = job_id_from(sent)
        behind = job_id_from(upload(other[0], other[1], filename="syllabus.pdf"))
        env = {sandbox.BACKEND_ENV: backend} if backend else {}
        # Twice the bound, and the time a new process takes to load the app.
        run.worked = site.work(limit_s=2 * case.seconds + 60, env=env)
        with session_scope(site.ctx.factory) as session:
            # A job the stopped worker left behind must not be taken up by the next case.
            for job in (session.get(Job, job_id), session.get(Job, behind)):
                if job is not None and job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                    job.status = JobStatus.FAILED
                    job.error_code = "left_by_the_test"
        run.job, run.other = site.job(job_id), site.job(behind)
        run.outputs = outputs(site, job_id)
    lines = site.logged[logged:]
    run.codes = [fields.get("code", "") for event, fields in lines if event == "upload_rejected"]
    run.log = "\n".join(f"{event} {fields}" for event, fields in lines)
    if run.worked is not None:
        run.log += "\n" + run.worked.log
    return run


@pytest.fixture(scope="module")
def run(site: harness.ReplaySite) -> Callable[[Case, str], Run]:
    """Uploads a case's file and runs the queue, once for the whole file of tests."""
    harness.replayable("s02_syllabus_sidebar")
    other = site.account("next@example.org"), harness.document("s02_syllabus_sidebar")
    files = load()
    runs: dict[tuple[str, str], Run] = {}

    def one(case: Case, backend: str) -> Run:
        able(case, backend)
        key = (case.file, backend)
        if key not in runs:
            runs[key] = upload_and_work(site, other, case, files[case.file].make(), backend)
        return runs[key]

    return one


def each(cases: list[Case]) -> list:
    return [
        pytest.param(
            case,
            backend,
            id=f"{case.file}-{backend}" if backend else case.file,
            marks=[pytest.mark.slow] if case.slow else [],
        )
        for case in cases
        for backend in (BACKENDS if case.source else ("",))
    ]


def known(request: pytest.FixtureRequest, case: Case, check: str) -> None:
    if check in case.open:
        request.applymarker(harness.finding(case.open[check]))


EVERY = each(list(CASES))
QUEUED = each([case for case in CASES if case.ends])


@pytest.mark.parametrize(("case", "backend"), EVERY)
def test_a_hostile_file_ends_as_its_case_states(
    run: Callable[[Case, str], Run], request: pytest.FixtureRequest, case: Case, backend: str
) -> None:
    known(request, case, "ends")
    got = run(case, backend)
    if case.refused:
        assert (got.status >= 400, got.codes, got.job) == (True, [case.refused], None)
    elif case.held:
        assert got.status == 303
        assert got.location.startswith("/uploads/")
    else:
        assert got.job is not None, f"refused ({got.status}): {got.codes}"
        assert got.worked is not None
        assert got.worked.ended == "finished", got.worked.log[-2000:]
        assert (got.job.status, got.job.error_code) == (case.ends, case.code)
        assert got.worked.missed[got.job.id] == case.unanswered


@pytest.mark.parametrize(("case", "backend"), EVERY)
def test_it_ends_inside_its_time_and_memory(
    run: Callable[[Case, str], Run], request: pytest.FixtureRequest, case: Case, backend: str
) -> None:
    known(request, case, "bound")
    got = run(case, backend)
    assert got.seconds <= case.seconds
    assert got.megabytes <= case.megabytes
    if got.worked is not None and case.source:
        # A sandboxed program is held to the sandbox's own limit.
        assert got.worked.started_mb <= latexml.LIMITS.group_memory_bytes / 2**20


@pytest.mark.parametrize(("case", "backend"), QUEUED)
def test_the_next_person_gets_their_file_soon_after(
    run: Callable[[Case, str], Run], request: pytest.FixtureRequest, case: Case, backend: str
) -> None:
    # There is one worker, so the second job waits for the whole of the first: the bound on
    # the hostile job is the bound on the wait. With the queue's slots (WF50) the wait
    # becomes one slice.
    known(request, case, "next")
    got = run(case, backend)
    assert got.job is not None and got.other is not None and got.worked is not None
    assert got.other.status == JobStatus.DONE
    assert got.worked.missed[got.other.id] == 0
    assert got.job.started_at is not None and got.other.finished_at is not None
    waited = (got.other.finished_at - got.job.started_at).total_seconds()
    assert waited <= case.seconds + NEXT_S


def unsafe(data: bytes, planted: tuple[str, ...]) -> list[str]:
    """What in this HTML a browser would run: a handler, a link that runs its target, or a
    script that holds a planted string."""
    found = []
    for node in lxml.html.fromstring(data).iter():
        if not isinstance(node.tag, str):
            continue
        for name, value in node.attrib.items():
            if name.lower().startswith("on"):
                found.append(f"<{node.tag} {name}>")
            elif "".join(value.split()).lower().startswith(RUNS):
                found.append(f"<{node.tag} {name}={value[:40]!r}>")
        if node.tag == "script" and any(mark in (node.text or "") for mark in planted):
            found.append("a script holding a planted string")
    return found


@pytest.mark.parametrize(("case", "backend"), QUEUED)
def test_nothing_planted_runs_or_is_logged(
    run: Callable[[Case, str], Run], request: pytest.FixtureRequest, case: Case, backend: str
) -> None:
    known(request, case, "planted")
    got = run(case, backend)
    planted = case.never + case.as_text
    assert [mark for mark in planted if mark in got.log] == []
    assert got.job is not None
    assert ("/download" in got.outputs) == (got.job.status == JobStatus.DONE)
    for name, data in got.outputs.items():
        assert [mark for mark in case.never if mark.encode() in data] == [], name
        if name.endswith((".html", ".xhtml")) or data.lstrip()[:15].lower().startswith(
            (b"<!doctype html", b"<html")
        ):
            assert unsafe(data, planted) == [], name
