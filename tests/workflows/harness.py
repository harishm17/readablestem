"""The workflow tests' site: the real app, the real conversion, recorded model answers.

A `ReplaySite` is the web app with production's behaviour settings (`ops.profile`), a fresh
SQLite file and the corpus's recorded answers in front of the model client. Nothing is sent
to a model: a request the recording does not hold is refused, and `ReplaySite.convert` fails
the test with the step and what changed since the recording.

The recording is two files (`eval/corpus.py`): the committed one, and `corpus.KEPT_OUT` for
answers read from files whose license keeps them out of the repository. Where the second is
not on the machine, a test that converts one of those files is skipped.

`scripts/corpus_refresh.py` runs these same tests to rebuild the recording, and tells the
site how through the environment:

- `CORPUS_ANSWERS_MODE`: `offline` (the default), `rekey` (read a file recorded under the
  older keys and write each answer found under the new one) or `live` (ask the model for
  what is missing, up to `CORPUS_ANSWERS_MAX_USD` in all).
- `CORPUS_ANSWERS_WORK`: the plain JSON-lines file to read and append to.
- `CORPUS_ANSWERS_LOG`: a folder; each conversion adds a line saying what it read and what
  it lacked.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import pickle
import re
import signal
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lxml.html
import pytest
from fastapi.testclient import TestClient

from fixtures.web.support import fetch, job_id_from, login, make_settings, upload
from readablestem.config import Settings
from readablestem.core.types import DocumentResult
from readablestem.eval import corpus
from readablestem.observability.logging import FIELDS_ATTR
from readablestem.ops import profile
from readablestem.pipeline import table_model
from readablestem.proof.bundle import dump_line
from readablestem.web.app import create_app
from readablestem.web.auth import set_allowed
from readablestem.web.db import Job, session_scope
from readablestem.web.worker import Worker, default_client_factory

__all__ = ["OWED", "UPLOADS", "Ran", "ReplaySite", "document", "fetch", "finding", "words"]

OWNER = "owner@example.org"
MODE = os.environ.get("CORPUS_ANSWERS_MODE", "offline")
WORK = os.environ.get("CORPUS_ANSWERS_WORK")
LOG = os.environ.get("CORPUS_ANSWERS_LOG")
# The largest corpus file is 39 pages and under 8 MB, and a replay spends nothing; the
# limits on size and spending are not what is tested here.
ROOM = {
    "max_pages_per_job": 60,
    "max_source_pages": 60,
    "max_upload_mb": 25,
    "max_large_upload_mb": 25,
    "per_user_daily_page_quota": 5000,
    "daily_spend_cap_usd": 1000.0,
}

# How a person uploads a corpus file: every file once with no box ticked, except these.
SCAN = {"image_pages": True}
UPLOADS: dict[str, tuple[dict[str, bool], ...]] = {
    "w_exam_5111": ({}, {"exam": True}),
    "w_handwritten_6006": (SCAN,),
    "w_scan_slides": (SCAN,),
    "w_mixed_slides": (SCAN,),
}
# Files whose conversion asks for answers that are not recorded yet, with how many: the
# next refresh owes them (docs/eval.md, "Recording and replay").
OWED = {
    "w_lecture_5111": 2,
    "d1_scripts": 14,
    "e1_mc_columns": 6,
    "e2_solutions_rubric": 6,
    "e3_calendar": 2,
    "f1_notation": 50,
    "f2_figures": 30,
    "f4_matrices": 12,
    "ar3_sideways90": 2,
    "ar4_sideways270": 2,
}


# The audit's register of findings numbers its rows F-001 to F-558. A finding met while
# the plan is built takes the next number, and the plan's STATUS.md lists it.
LAST_FINDING = 559


def finding(ident: str) -> pytest.MarkDecorator:
    """Marks an assertion that an open finding of the register makes false today. Strict:
    when the fix lands the test passes, the run fails, and the mark is removed with the fix."""
    if not (re.fullmatch(r"F-\d{3}", ident) and 1 <= int(ident[2:]) <= LAST_FINDING):
        raise ValueError(f"{ident!r} is not a finding (F-001 to F-{LAST_FINDING})")
    return pytest.mark.xfail(strict=True, reason=ident)


def words(node: lxml.html.HtmlElement) -> str:
    """The text a person reads in `node`, with runs of white space as one space."""
    return " ".join(node.text_content().split())


def _no_network(url: str, size: int) -> bytes:
    raise corpus.CorpusError(f"not in {corpus.CACHE}: {url}")


def document(entry_id: str) -> bytes:
    """The corpus file `entry_id`. A public file comes from the cache: where it has not been
    fetched the test is skipped, except in CI, where the fetch step must have run."""
    entry = corpus.load_manifest()[entry_id]
    try:
        return corpus.document(entry, get=_no_network)
    except corpus.CorpusError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"{exc}; fetch with: python -m readablestem.eval.corpus check --fetch")


def answers_file(folder: Path) -> Path:
    """The plain file the app reads answers from: the one a refresh works on, else the two
    recorded files written out as one."""
    if WORK:
        return Path(WORK)
    path = folder / "answers.jsonl"
    rows = corpus.read_answers() + corpus.read_answers(corpus.KEPT_OUT)
    path.write_text("".join(dump_line(row) + "\n" for row in rows), encoding="utf-8")
    return path


def replayable(entry_id: str) -> None:
    """Skips the test where this machine cannot replay the conversion of `entry_id`."""
    entry = corpus.load_manifest()[entry_id]
    if not (WORK or entry.may_commit or corpus.KEPT_OUT.is_file()):
        pytest.skip(
            f"{entry_id} is {entry.license}: the answers recorded from it are kept out "
            f"of the repository, and {corpus.KEPT_OUT} is not on this machine"
        )
    if not table_model.model_path().is_file():
        # Without it tables are read another way and the questions differ.
        lacking = "the answers were recorded with the table-structure model: make table-model"
        if os.environ.get("CI"):
            raise RuntimeError(lacking)
        pytest.skip(lacking)


@dataclass(frozen=True)
class Ran:
    """What `ReplaySite.work` saw of the process that ran the queue."""

    ended: str  # "finished", "stopped" (at the time limit) or "died"
    missed: dict[str, int]  # per job: the model answers the recording did not hold
    own_mb: float  # the most memory the process held
    started_mb: float  # the most held by a program it started (a sandboxed one)
    log: str  # every line it wrote


class _Lines(logging.Handler):
    """Keeps each log line of the app as (event, fields)."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[tuple[str, dict[str, Any]]] = []

    def emit(self, record: logging.LogRecord) -> None:
        fields = dict(getattr(record, FIELDS_ATTR, None) or {})
        if record.exc_info:
            fields["exception"] = self.format(record)
        self.lines.append((record.getMessage(), fields))


class ReplaySite:
    """One app and database, signed in as the owner, converting from the recording."""

    def __init__(self, folder: Path, **overrides: Any) -> None:
        cap = float(os.environ.get("CORPUS_ANSWERS_MAX_USD") or 0)
        base = make_settings(
            folder,
            **ROOM,
            model_answers_file=answers_file(folder),
            model_answers_offline=MODE != "live",
            model_answers_rekey=MODE == "rekey",
            model_answers_live_usd=cap if MODE == "live" else None,
            # Replay sends nothing, so any key will do; a live refresh brings the real one.
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or "sk-ant-replay-sends-nothing",
        )
        applied = profile.load().apply(base)
        self.settings = Settings(**(applied.model_dump() | overrides)) if overrides else applied
        self._clients: list[Any] = []
        self._folder = folder
        self._log = _Lines()
        self.app = create_app(self.settings, client_factory=self._client)
        logging.getLogger().addHandler(self._log)
        self.ctx = self.app.state.ctx
        self.browser = TestClient(self.app, follow_redirects=False)
        self.browser.__enter__()
        with session_scope(self.ctx.factory) as session:
            set_allowed(session, OWNER, allowed=True)
        login(self.browser, OWNER)

    def close(self) -> None:
        logging.getLogger().removeHandler(self._log)
        self.browser.__exit__(None, None, None)

    @property
    def logged(self) -> list[tuple[str, dict[str, Any]]]:
        """Every log line this process has written since the site began, as (event, fields).
        The list grows: a test reads the lines after a place it noted."""
        return self._log.lines

    def account(self, email: str) -> TestClient:
        """Another person, let in and signed in on a browser of their own."""
        with session_scope(self.ctx.factory) as session:
            set_allowed(session, email, allowed=True)
        browser = TestClient(self.app, follow_redirects=False)
        login(browser, email)
        return browser

    def work(self, *, limit_s: float, env: dict[str, str] | None = None) -> Ran:
        """Runs the queued jobs to the end in a process of its own (`queue_runner.py`),
        stopped with everything it started after `limit_s` seconds."""
        settings = self._folder / "settings.pickle"
        settings.write_bytes(pickle.dumps(self.settings))
        out = self._folder / "ran.json"
        out.unlink(missing_ok=True)
        log = self._folder / "ran.log"
        with log.open("wb") as lines:
            child = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name("queue_runner.py")), settings, out],
                stdout=lines,
                stderr=subprocess.STDOUT,
                env=os.environ | (env or {}),
                start_new_session=True,
            )
            try:
                ended = "finished" if child.wait(timeout=limit_s) == 0 else "died"
            except subprocess.TimeoutExpired:
                ended = "stopped"
            # Its whole group: a sandboxed program must not outlive the test.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()
        said = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
        return Ran(
            ended=ended,
            missed=said.get("missed", {}),
            own_mb=said.get("own_kb", 0) / 1024,
            started_mb=said.get("started_kb", 0) / 1024,
            log=log.read_text(encoding="utf-8", errors="replace"),
        )

    def _client(self, settings: Settings, budget: Any) -> Any:
        client = default_client_factory(settings, budget)
        self._clients.append(client)
        if getattr(client, "second_check", None) is not None:
            self._clients.append(client.second_check)
        return client

    def convert(self, entry_id: str, *, image_pages: bool = False, exam: bool = False) -> str:
        """Upload the corpus file as the owner and convert it. Returns the job's id. Fails
        the test when the upload is refused, or when the conversion needed an answer the
        recording does not hold, unless the file is listed in `OWED` with that many. A file
        this machine cannot replay is skipped only after its upload was taken, so every
        corpus file present is shown to pass the upload's checks."""
        entry = corpus.load_manifest()[entry_id]
        before = len(self._clients)
        sent = upload(
            self.browser,
            document(entry_id),
            filename=f"{entry_id}.pdf",
            image_pages=image_pages,
            exam=exam,
        )
        if sent.status_code != 303:
            said = " ".join(sent.text.split())
            pytest.fail(f"{entry_id}: the upload was refused ({sent.status_code}): {said[-600:]}")
        replayable(entry_id)
        job_id = job_id_from(sent)
        worker = Worker(
            self.settings,
            self.ctx.factory,
            converter=self.ctx.converter,
            client_factory=self._client,
        )
        assert worker.run_once() == job_id
        clients = self._clients[before:]
        missed: Counter[str] = Counter()
        for client in clients:
            missed.update(client.missed_because)
        if LOG:
            line = {
                "entry": entry_id,
                "ticked": [box for box, on in (("scan", image_pages), ("exam", exam)) if on],
                "commit": entry.may_commit,
                "hits": sum(c.hits for c in clients),
                "moved": sum(c.rekeyed for c in clients),
                "missed": dict(missed),
                "replayed_usd": sum(c.replayed_cost_usd for c in clients),
                "paid_usd": sum(c.live_cost_usd for c in clients),
                "keys": sorted(set().union(*(c.used for c in clients))),
            }
            with (Path(LOG) / f"{os.getpid()}.jsonl").open("a", encoding="utf-8") as out:
                out.write(json.dumps(line) + "\n")
        owed = OWED.get(entry_id, 0)
        if MODE != "live" and sum(missed.values()) != owed:
            lines = "".join(f"\n  {count} x {why}" for why, count in missed.most_common())
            pytest.fail(
                f"{entry_id}: {sum(missed.values())} model answers are not in the recording "
                f"and {owed} are listed as owed (harness.OWED). Nothing was sent.{lines}\n"
                "Record them, or take the file off the list, with scripts/corpus_refresh.py.",
                pytrace=False,
            )
        return job_id

    def page(self, path: str) -> lxml.html.HtmlElement:
        """The page at `path` as the owner gets it, parsed. A download that is still a draft
        is confirmed the way a person confirms it."""
        response = fetch(self.browser, path)
        assert response.status_code == 200, f"{path}: {response.status_code}"
        return lxml.html.fromstring(response.text)

    def student_file(self, job_id: str) -> lxml.html.HtmlElement:
        """The HTML file the instructor downloads and posts for students, parsed."""
        return self.page(f"/jobs/{job_id}/download")

    def job(self, job_id: str) -> Job:
        with session_scope(self.ctx.factory) as session:
            job = session.get(Job, job_id)
            assert job is not None
            session.expunge(job)
            return job

    def result(self, job_id: str) -> DocumentResult:
        return DocumentResult.model_validate(self.job(job_id).result_json)
