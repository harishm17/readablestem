"""My documents as a to-do list, times in the reader's zone, the deleted-document page,
upload messages that name the file, the budget-spent page and the posting help (UX
review BW5, BW9, MF10, MF11, MF16, MF20, MF21, MF22, MF24)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fixtures.web.support import (
    ALICE,
    BOB,
    WebEnv,
    fake_output,
    job_id_from,
    login,
    make_pdf,
    upload,
)
from readablestem.core.types import Status
from readablestem.web.db import Job, session_scope
from readablestem.web.pdf_probe import ProbeRejected, probe

LAYOUT = Path(__file__).resolve().parent / "fixtures" / "layout"


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        yield env


def text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def finish(env, *, filename: str = "notes.pdf", title: str | None = "Kinematics", decided=False):
    """Upload and convert one document; `decided`: every decision already made."""
    output = fake_output()
    output.result.title = title
    if decided:
        for fix in output.result.fixes:
            if fix.pending:
                fix.status = Status.REJECTED
                fix.reviewed = True
    env.converter.output = output
    job_id = job_id_from(upload(env.browser, make_pdf(1), filename=filename))
    assert env.run_worker() == job_id
    return job_id


# --------------------------------------------------------------------------- the list


def test_the_list_is_the_owners_only(web):
    finish(web, filename="mine.pdf", title="Mine")
    bob = web.client()
    login(bob, BOB)
    assert "Mine" not in bob.get("/app").text and "Mine" not in bob.get("/app/documents").text
    assert web.client().get("/app/documents").status_code == 303  # signed out: sign in


# --------------------------------------------------------------------------- delete


# --------------------------------------------------------------------------- deleted


def test_someone_elses_live_document_is_a_plain_not_found(web):
    job_id = finish(web)
    bob = web.client()
    login(bob, BOB)
    flat = text(bob.get(f"/jobs/{job_id}").text)
    assert "There is no document at this address that this account can open." in flat
    assert "has been deleted" not in flat and "not yours to see" not in flat


# --------------------------------------------------------------------------- uploads


def truncated_pdf() -> bytes:
    """A PDF cut short, as by a download that did not finish: MuPDF repairs it and finds
    no pages (the reviewers' 15 truncated files were all called "no pages")."""
    real = (LAYOUT / "lecture_notes.pdf").read_bytes()
    return real[: len(real) // 10]


def test_a_damaged_pdf_is_not_called_empty():
    with pytest.raises(ProbeRejected) as refused:
        probe(truncated_pdf(), max_pages=50)
    assert refused.value.code == "damaged"


# --------------------------------------------------------------------------- times


# --------------------------------------------------------------------------- public pages


def test_the_privacy_notice_mentions_usage_events(web):
    flat = text(web.client().get("/privacy").text)
    assert "No cookies or scripts are used for this." in flat
    assert "deleted after 400 days" in flat


# --------------------------------------------------------------------------- list facts


class Reads:
    """The SQL an engine runs while attached (statements only)."""

    def __init__(self, env) -> None:
        from sqlalchemy import event

        self.engine = env.ctx.factory.kw["bind"]
        self.sql: list[str] = []
        self._listener = lambda conn, cur, statement, *rest: self.sql.append(statement)
        event.listen(self.engine, "before_cursor_execute", self._listener)

    def stop(self) -> list[str]:
        from sqlalchemy import event

        event.remove(self.engine, "before_cursor_execute", self._listener)
        return self.sql


def test_a_finished_job_from_before_the_columns_is_filled_in_once(web):
    job_id = finish(web, title="Old one")
    with session_scope(web.ctx.factory) as session:
        session.execute(
            Job.__table__.update()
            .where(Job.id == job_id)
            .values(open_decisions=None, result_title=None)
        )
    assert web.job(job_id).open_decisions is None
    first = text(web.browser.get("/app").text)
    assert "Old one" in first and "3 decisions to make" in first
    assert (web.job(job_id).open_decisions, web.job(job_id).result_title) == (3, "Old one")
    reads = Reads(web)
    web.browser.get("/app/documents")
    assert not any("result_json" in s for s in reads.stop())  # read once, not every poll
