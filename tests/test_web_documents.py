"""My documents as a to-do list, times in the reader's zone, the deleted-document page,
upload messages that name the file, the budget-spent page and the posting help (UX
review BW5, BW9, MF10, MF11, MF16, MF20, MF21, MF22, MF24)."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pymupdf
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
from readablestem.core.content_budget import PAGE_CONTENT_BUDGET_BYTES
from readablestem.core.types import Status
from readablestem.ops.retention import purge_expired
from readablestem.web import states
from readablestem.web.dates import format_when
from readablestem.web.db import Job, JobStatus, session_scope, utcnow
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


def test_an_expired_document_says_when_it_went_and_never_a_negative_span(web):
    """F-050: a row whose deletion time is before its upload time printed "after -2 hours".
    The page names the date, and a span is only printed when it is a real one. After the
    purge the row is gone and only the upload date is known: it says that and stops."""
    job_id = finish(web)
    gone = utcnow() - timedelta(hours=2)
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.created_at, job.retain_until = utcnow() - timedelta(hours=1), gone
    flat = text(web.browser.get(f"/jobs/{job_id}").text)
    assert format_when(gone) in flat
    assert not re.search(r"-\d|after 0 hour", flat)

    purge_expired(web.ctx.factory)
    flat = text(web.browser.get(f"/jobs/{job_id}").text)
    assert "has been deleted" in flat and format_when(gone) not in flat
    assert re.search(r"\b20\d\d, \d\d:\d\d UTC", flat)  # the upload date


def test_the_list_marks_a_document_in_its_last_hours_and_no_other(web):
    soon, later = finish(web, filename="soon.pdf"), finish(web, filename="later.pdf")
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, soon)
        assert job is not None
        job.retain_until = utcnow() + timedelta(hours=3)
    html = web.browser.get("/app/documents", headers={"HX-Request": "true"}).text
    cell = r'id="doc-(\w+)".*?(<td[^>]*Deleted.*?</td>)'
    rows = dict(re.findall(cell, html, re.S))
    assert "<strong>" in rows[soon] and "<strong>" not in rows[later]


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


def page_of_content(inflated: int) -> bytes:
    """One page with a line of text whose content stream inflates to `inflated` bytes."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "A page with a long content stream.")
    xref = page.get_contents()[0]
    text = doc.xref_stream(xref)
    doc.update_stream(xref, b" " * (inflated - len(text)) + text)  # stored compressed
    return doc.tobytes()


@pytest.mark.parametrize(
    ("inflated", "refused"),
    [(PAGE_CONTENT_BUDGET_BYTES - 4096, False), (PAGE_CONTENT_BUDGET_BYTES + 4096, True)],
    ids=["just-under", "just-over"],
)
def test_a_page_whose_content_inflates_past_the_budget_is_refused(inflated, refused):
    data = page_of_content(inflated)
    assert len(data) < inflated // 100  # small in the file, large once inflated
    if not refused:
        assert probe(data, max_pages=50) == 1
        return
    with pytest.raises(ProbeRejected) as refusal:
        probe(data, max_pages=50)
    assert refusal.value.code == "content_too_large"


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


def test_a_stopped_document_is_listed_with_what_needs_you_and_offers_try_again(web):
    """F-414: a stopped document is not grouped with the ones still working: it is under
    "Needs you" with its reason in its label and "Try again" as its next step."""
    finish(web, filename="decide.pdf")
    stopped = finish(web, filename="stopped.pdf")
    waiting = job_id_from(upload(web.browser, make_pdf(1), filename="waiting.pdf"))
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, stopped)
        assert job is not None
        job.status, job.error_code = JobStatus.FAILED, "timeout"
    html = web.browser.get("/app/documents", headers={"HX-Request": "true"}).text
    needs = html[html.index(f'id="docs-{states.NEEDS}"') : html.index("</table>")]
    assert f'id="doc-{stopped}"' in needs and f'id="doc-{waiting}"' not in needs
    assert states.TABLE["stopped_service"].label in text(needs)
    assert re.search(rf'action="/jobs/{stopped}/retry".*?id="act-{stopped}"', needs, re.S)


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
    shown = web.job(job_id).result_title
    assert shown and shown in first and "3 decisions to make" in first
    assert (web.job(job_id).open_decisions, web.job(job_id).result_title) == (3, shown)
    reads = Reads(web)
    web.browser.get("/app/documents", headers={"HX-Request": "true"})
    assert not any("result_json" in s for s in reads.stop())  # read once, not every poll


def test_the_list_of_finished_documents_costs_the_same_for_two_as_for_fifty_and_loads_no_result(
    web,
):
    """F-496, F-497: the state of a finished document with nothing open is read from the facts
    stored with its result, so the list, the batch page and the counts load no result and the
    statements do not grow with the documents. A row from before the column costs one read
    of its result, once."""
    from sqlalchemy import select

    from readablestem.core.types import DocumentResult
    from readablestem.pipeline import checked
    from readablestem.web import upload_result

    first = finish(web, filename="first.pdf", decided=True)

    def copies(count: int) -> None:
        with session_scope(web.ctx.factory) as session:
            source = session.get(Job, first)
            assert source is not None
            for i in range(count):
                copy = Job(
                    user_id=source.user_id,
                    status=source.status,
                    filename=f"copy{i}.pdf",
                    page_count=source.page_count,
                    retain_until=source.retain_until,
                )
                copy.result_json = source.result_json
                session.add(copy)

    def cost() -> tuple[int, int, dict[str, str]]:
        with session_scope(web.ctx.factory) as session:
            ids = list(session.scalars(select(Job.id)))
            batch = upload_result.store(
                session,
                session.get(Job, first).user_id,
                [{"filename": "x", "job_id": i} for i in ids],
            )
        reads = Reads(web)
        for url in ("/app", f"/app/batch/{batch}"):
            assert web.browser.get(url).status_code == 200
        sql = reads.stop()
        loaded = [s for s in sql if "jobs.result_json" in s.split(" FROM ")[0]]
        with session_scope(web.ctx.factory) as session:
            return len(sql), len(loaded), {j: v.key for j, v in states.load(session, ids).items()}

    copies(1)
    few, few_loaded, _ = cost()
    copies(48)
    many, many_loaded, many_states = cost()
    assert (few_loaded, many_loaded) == (0, 0) and many == few

    # The state is the one the job's own page names from its result.
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, first)
        assert job is not None
        whole = states.state_of(
            states.Facts.of(job),
            states.counts_of(checked.of_result(DocumentResult.model_validate(job.result_json))),
        )
    assert set(many_states.values()) == {whole.key}

    with session_scope(web.ctx.factory) as session:  # every row from before the column
        session.execute(Job.__table__.update().values(state_facts=None))
    _, first_loaded, _ = cost()
    _, again_loaded, again_states = cost()
    assert (first_loaded, again_loaded) == (50, 0)
    assert again_states == many_states


def test_the_zip_link_says_how_many_documents_the_zip_holds_and_names_the_rest(web, monkeypatch):
    """F-325: with more ready documents than one zip holds, the link counts what the zip has
    (the number the route returns) and the others are listed with the held-back one named."""
    import io
    import zipfile

    from readablestem.web import bundle

    monkeypatch.setattr(bundle, "MAX_DOCUMENTS", 3)
    for number in range(5):
        finish(web, filename=f"ready{number}.pdf", title=f"Ready {number}", decided=True)
    finish(web, filename="draft.pdf", title="Draft")  # decisions still open
    page = web.browser.get("/app").text
    words = text(page)
    assert "Download 3 of 5 ready documents (zip)" in words
    assert "One zip holds at most 3 documents" in words
    assert "1 document with decisions still to make is not in it" in words
    rest = page[page.index('id="zip-rest"') : page.index("</details>", page.index('id="zip-rest"'))]
    assert len(re.findall(r'href="/jobs/', rest)) == 2
    members = zipfile.ZipFile(io.BytesIO(web.browser.get("/app/download").content)).namelist()
    assert len([n for n in members if n.endswith(".html")]) == 3
    # The five share a title (the same first heading): the file in each control's name keeps
    # two rows from being the same words.
    assert all(f", file ready{number}.pdf" in words for number in range(5))


def test_every_delete_page_says_what_deleting_does_not_reach(web):
    """F-328: the two sentences account deletion has (backups, the model provider's own
    retention) are on the document's delete page too (the waiting file's and the course's
    pages include the same partial, `templates/_delete_notes.html`)."""
    job_id = finish(web)
    pages = [web.browser.get(f"/jobs/{job_id}/delete").text]
    pages.append(web.browser.get("/account/delete").text)
    for html in pages:
        words = text(html)
        assert "Backups keep a copy for about a week." in words and "stay under" in words
