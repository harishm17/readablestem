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

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

import lxml.html
import pytest
from fastapi.testclient import TestClient

from fixtures.web.support import fetch, job_id_from, login, make_settings, upload
from readablestem.config import Settings
from readablestem.core.types import DocumentResult
from readablestem.eval import corpus
from readablestem.ops import profile
from readablestem.pipeline import table_model
from readablestem.proof.bundle import dump_line
from readablestem.web.app import create_app
from readablestem.web.auth import set_allowed
from readablestem.web.db import Job, session_scope
from readablestem.web.worker import Worker, default_client_factory

__all__ = ["OWED", "UPLOADS", "ReplaySite", "document", "fetch", "finding", "words"]

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


# The audit's register of findings numbers its rows F-001 to F-558.
REGISTER_ROWS = 558


def finding(ident: str) -> pytest.MarkDecorator:
    """Marks an assertion that an open finding of the register makes false today. Strict:
    when the fix lands the test passes, the run fails, and the mark is removed with the fix."""
    if not (re.fullmatch(r"F-\d{3}", ident) and 1 <= int(ident[2:]) <= REGISTER_ROWS):
        raise ValueError(f"{ident!r} is not a finding of the register (F-001 to F-{REGISTER_ROWS})")
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
        self.app = create_app(self.settings, client_factory=self._client)
        self.ctx = self.app.state.ctx
        self.browser = TestClient(self.app, follow_redirects=False)
        self.browser.__enter__()
        with session_scope(self.ctx.factory) as session:
            set_allowed(session, OWNER, allowed=True)
        login(self.browser, OWNER)

    def close(self) -> None:
        self.browser.__exit__(None, None, None)

    def _client(self, settings: Settings, budget: Any) -> Any:
        client = default_client_factory(settings, budget)
        self._clients.append(client)
        if getattr(client, "second_check", None) is not None:
            self._clients.append(client.second_check)
        return client

    def convert(self, entry_id: str, *, image_pages: bool = False, exam: bool = False) -> str:
        """Upload the corpus file as the owner and convert it. Returns the job's id. Fails
        the test when the conversion needed an answer the recording does not hold, unless
        the file is listed in `OWED` with that many."""
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
