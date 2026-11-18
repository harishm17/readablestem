"""A zip of documents and the batch page (`FEATURE_BATCH_ZIP`, `routes/batch.py`): the batch zip
through the upload, the limits by tier (10 for the open tier, 30 for invited people), the page
with its live status and its summary line, polling, "Download all", and who may open it."""

from __future__ import annotations

import copy
import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from fixtures.web.support import (
    ADMIN,
    ALICE,
    BOB,
    WebEnv,
    csrf_from,
    login,
    make_pdf,
    token,
)
from readablestem.core import sandbox
from readablestem.source import latexml
from readablestem.web import access, states
from readablestem.web.database import utc_today
from readablestem.web.db import (
    Job,
    JobStatus,
    QuotaDay,
    SpendDay,
    User,
    session_scope,
    utcnow,
)
from source_support import make_zip

ARTICLE = "\\documentclass{article}\n\\begin{document}\nHello $x$.\n\\end{document}\n"


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr(latexml, "installed", lambda: True)
    monkeypatch.setattr(sandbox, "available", lambda: True)
    env = WebEnv(
        tmp_path,
        feature_batch_zip=True,
        feature_latex_source=True,
        per_user_daily_page_quota=500,
        max_pages_per_job=5,
    )
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.alice = client
        env.bob = env.client()
        login(env.bob, BOB)
        yield env


def jobs(env) -> list[Job]:
    with session_scope(env.ctx.factory) as session:
        return sorted(session.scalars(select(Job)), key=lambda j: j.filename)


def pages_used(env) -> int:
    with session_scope(env.ctx.factory) as session:
        return sum(row.pages for row in session.scalars(select(QuotaDay)))


def send_zip(client, data: bytes, name: str = "term.zip", **fields):
    return client.post(
        "/jobs",
        data={"csrf_token": token(client), **fields},
        files={"pdf": (name, data, "application/zip")},
    )


def batch_url(response) -> str:
    assert response.status_code == 303, response.status_code
    url = response.headers["location"]
    assert url.startswith("/app/batch/")
    return url


# ------------------------------------------------------------------ the switch and the form


# --------------------------------------------------------------------------- a batch zip


def test_a_zip_of_pdfs_becomes_a_document_each_and_the_batch_page(web):
    data = make_zip({"a.pdf": make_pdf(1), "Unit 2/b.pdf": make_pdf(2), "c.pdf": make_pdf(1)})
    response = send_zip(web.alice, data)
    url = batch_url(response)
    assert [j.filename for j in jobs(web)] == ["a.pdf", "b.pdf", "c.pdf"]
    assert pages_used(web) == 4  # each document's own pages, like any upload
    page = web.alice.get(url)
    assert page.status_code == 200
    text = text_of(page.text)
    assert "3 of 3 files uploaded" in text and "3 documents: 3 waiting." in text
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        assert name in text


@pytest.mark.parametrize(
    ("bad", "words"),
    [
        ({"bad.pdf": b"not a pdf"}, "bad.pdf is not a PDF"),
        (  # a project folder in an exam class: refused by name, like an upload of its own
            {"Quiz 3/main.tex": "\\documentclass{exam}\\begin{document}x\\end{document}"},
            "Quiz 3.zip uses a document class",
        ),
    ],
    ids=["not-a-pdf", "exam-project"],
)
def test_a_bad_document_in_a_zip_is_one_refusal_beside_the_others(web, bad, words):
    data = make_zip({"good.pdf": make_pdf(1), **bad})
    url = batch_url(send_zip(web.alice, data))
    text = text_of(web.alice.get(url).text)
    assert "1 of 2 files uploaded" in text and words in text
    assert [j.filename for j in jobs(web)] == ["good.pdf"]
    assert pages_used(web) == 1  # a refusal costs nothing


@pytest.mark.parametrize(
    ("data", "words"),
    [
        (make_zip({"notes.txt": "x"}), "no PDF and no LaTeX project"),
        (make_zip({"../x.pdf": b"x"}), "path or a link"),
        (make_zip({"a.pdf": b"x", "inner.zip": b"x"}), "kind of file this tool does not read"),
        (b"PK\x03\x04 not a zip", "could not be opened as a zip"),
    ],
    ids=["empty", "traversal", "nested-zip", "broken"],
)
def test_a_refused_zip_costs_nothing_and_says_why(web, data, words):
    response = send_zip(web.alice, data)
    text = text_of(
        response.text if response.status_code != 303 else web.alice.get(batch_url(response)).text
    )
    assert words in text and "term.zip" in text
    assert jobs(web) == [] and pages_used(web) == 0


def test_the_zip_counts_once_for_the_rate_limit_not_once_per_document(tmp_path, monkeypatch):
    monkeypatch.setattr(latexml, "installed", lambda: True)
    monkeypatch.setattr(sandbox, "available", lambda: True)
    env = WebEnv(
        tmp_path, feature_batch_zip=True, rate_limits_enabled=True, per_user_daily_page_quota=500
    )
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        data = make_zip({f"d{i}.pdf": make_pdf(1) for i in range(25)})
        assert send_zip(client, data).status_code == 303
        assert len(jobs(env)) == 25


# --------------------------------------------------------------------------- limits by tier


def make_open(env, email: str) -> None:
    with session_scope(env.ctx.factory) as session:
        user = session.scalar(select(User).where(User.email == email))
        assert user is not None
        user.access_tier = access.OPEN


def test_an_invited_person_gets_thirty_documents_from_a_zip_and_the_rest_are_refused_by_name(web):
    data = make_zip({f"d{i:02d}.pdf": make_pdf(1) for i in range(32)})
    url = batch_url(send_zip(web.alice, data))
    assert len(jobs(web)) == 30
    text = text_of(web.alice.get(url).text)
    assert "30 of 32 files uploaded" in text
    assert "was not uploaded: one upload takes 30 documents at a time for your account" in text
    assert "d30.pdf" in text and "d31.pdf" in text


def test_an_open_account_gets_ten(web):
    make_open(web, ALICE)
    data = make_zip({f"d{i:02d}.pdf": make_pdf(1) for i in range(12)})
    url = batch_url(send_zip(web.alice, data))
    assert len(jobs(web)) == 10
    text = text_of(web.alice.get(url).text)
    assert "10 of 12 files uploaded" in text and "one upload takes 10 documents at a time" in text
    assert "up to 10 files" in text_of(
        web.alice.get("/app").text
    ).lower() or "10 at a time" in text_of(web.alice.get("/app").text)


def test_documents_waiting_are_capped_by_tier_too(web):
    make_open(web, ALICE)
    for n in range(10):
        response = web.alice.post(
            "/jobs",
            data={"csrf_token": token(web.alice)},
            files={"pdf": (f"w{n}.pdf", make_pdf(1), "application/pdf")},
        )
        assert response.status_code == 303
    late = web.alice.post(
        "/jobs",
        data={"csrf_token": token(web.alice)},
        files={"pdf": ("late.pdf", make_pdf(1), "application/pdf")},
    )
    assert late.status_code == 429 and "You already have 10 documents waiting" in text_of(late.text)


# --------------------------------------------------------------------------- memory, per request


def test_a_zip_met_after_the_documents_limit_is_refused_unopened(web, monkeypatch):
    from readablestem.web import batch_intake

    make_open(web, ALICE)
    opened: list[str] = []
    real = batch_intake.split

    def spy(filename, data, **kwargs):
        opened.append(filename)
        return real(filename, data, **kwargs)

    monkeypatch.setattr(batch_intake, "split", spy)
    first = make_zip({f"d{i:02d}.pdf": make_pdf(1) for i in range(10)})
    second = make_zip({"late.pdf": make_pdf(1)})
    response = web.alice.post(
        "/jobs",
        data={"csrf_token": token(web.alice)},
        files=[
            ("pdf", ("first.zip", first, "application/zip")),
            ("pdf", ("second.zip", second, "application/zip")),
        ],
    )
    text = text_of(web.alice.get(response.headers["location"]).text)
    assert opened == ["first.zip"]  # the second was never opened
    assert "second.zip was not uploaded: one upload takes 10 documents" in text


def test_a_pdf_over_the_upload_limit_is_refused_without_being_read(web, monkeypatch):
    from readablestem.web import batch_intake

    reads: list[str] = []
    real = batch_intake._read_member
    monkeypatch.setattr(
        batch_intake,
        "_read_member",
        lambda archive, info, budget, cap: (
            reads.append(info.filename) or real(archive, info, budget, cap)
        ),
    )
    block = __import__("os").urandom(8192)  # repeats: compresses 50x, under the ratio cap
    big = b"%PDF-1.7 " + block * (web.settings.upload_limit_bytes // 8192 + 20)
    ok = make_pdf(1)
    url = batch_url(send_zip(web.alice, make_zip({"big.pdf": big, "ok.pdf": ok}), name="b.zip"))
    text = text_of(web.alice.get(url).text)
    assert "big.pdf is larger than" in text  # refused by name, from its header size
    assert "big.pdf" not in reads  # no member over the limit is read
    assert reads == ["ok.pdf"]
    assert [j.filename for j in jobs(web)] == ["ok.pdf"]  # the other documents become jobs


# --------------------------------------------------------------------------- the batch page


def run_all(env) -> None:
    while env.run_worker():
        pass


def test_the_poll_is_204_until_something_changes_then_a_fragment_with_the_summary_out_of_band(web):
    url = batch_url(send_zip(web.alice, make_zip({"a.pdf": make_pdf(1), "b.pdf": make_pdf(1)})))
    page = web.alice.get(url).text
    version = re.search(r'hx-get="[^"]*\?v=([0-9a-f]+)"', page)
    assert version
    hx = {"HX-Request": "true"}
    assert web.alice.get(f"{url}?v={version.group(1)}", headers=hx).status_code == 204
    web.run_worker()  # one finishes
    changed = web.alice.get(f"{url}?v={version.group(1)}", headers=hx)
    assert changed.status_code == 200
    assert 'id="batch-summary"' in changed.text and 'hx-swap-oob="innerHTML"' in changed.text
    assert "<html" not in changed.text  # a fragment, not the page
    assert 'role="status"' not in changed.text  # the live region node itself is not replaced


def ready_output():
    """The fake converter's document with nothing left to decide."""
    from fixtures.web.support import fake_output
    from readablestem.core.types import Status

    output = fake_output()
    for fix in output.result.fixes:
        fix.status, fix.review = Status.ACCEPTED, "none"
        fix.reason = None
    return output


def test_download_all_is_offered_when_nothing_is_waiting_and_gives_this_batches_ready_files(web):
    web.converter.output = ready_output()
    url = batch_url(send_zip(web.alice, make_zip({"a.pdf": make_pdf(1), "b.pdf": make_pdf(1)})))
    before = web.alice.get(url).text
    assert 'id="batch-download"' not in before  # no link while anything is waiting
    assert "when every document has finished converting" in text_of(before)
    run_all(web)
    after = web.alice.get(url).text
    assert "2 documents: 2 ready to download." in text_of(after)
    link = re.search(r'id="batch-download" href="([^"]+)"', after)
    assert link and "Download 2 ready documents as one zip" in text_of(after)
    zipped = web.alice.get(link.group(1))
    assert zipped.status_code == 200 and zipped.headers["content-type"] == "application/zip"
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(zipped.content)) as archive:
        assert sorted(n for n in archive.namelist() if n != "contents.txt") != []
        assert len([n for n in archive.namelist() if n.endswith(".html")]) == 2


# --------------------------------------------------------------------------- who may open it


def test_only_the_owner_opens_the_batch_page_and_its_download(web):
    url = batch_url(send_zip(web.alice, make_zip({"secret-name.pdf": make_pdf(1)})))
    assert "secret-name.pdf" in web.alice.get(url).text
    missing = web.bob.get("/app/batch/" + "0" * 32)
    for path in (url, url + "/download", url + "?v=x"):
        stranger = web.bob.get(path)
        assert stranger.status_code == missing.status_code == 404
        assert "secret-name.pdf" not in stranger.text
    assert web.client().get(url).status_code in {303, 401, 403}  # signed out
    assert web.alice.get("/app/batch/not-an-id").status_code == 404


def test_a_deleted_document_is_a_deleted_row(web):
    url = batch_url(send_zip(web.alice, make_zip({"a.pdf": make_pdf(1), "b.pdf": make_pdf(1)})))
    victim = jobs(web)[0]
    delete = web.alice.post(
        f"/jobs/{victim.id}/delete",
        data={
            "csrf_token": csrf_from(web.alice.get(f"/jobs/{victim.id}/delete").text),
            "confirm": "yes",
        },
    )
    assert delete.status_code in {200, 303}
    text = text_of(web.alice.get(url).text)
    assert "1 deleted" in text


# --------------------------------------------------------------------------- one state table


def _decide_all(result: dict) -> dict:
    result = copy.deepcopy(result)
    for fix in result["fixes"]:
        fix["status"], fix["review"], fix["reason"] = "accepted", "none", None
    return result


def _set_state(job: Job, key: str) -> None:
    """Stored facts that make `key` (`web.states`), on a converted job with 3 decisions open."""
    now = utcnow()
    if key in {"waiting", "restarting"}:
        job.status, job.attempts, job.attempts_at_retry = JobStatus.QUEUED, 0, 0
        job.attempts = 1 if key == "restarting" else 0  # put back after a lost attempt
    elif key in {"converting", "stopping"}:
        job.status, job.lease_until, job.pages_done = JobStatus.RUNNING, now + timedelta(hours=1), 0
        job.cancel_requested_at = now if key == "stopping" else None
    elif key == "canceled":
        job.status, job.error_code = JobStatus.FAILED, "canceled"
    elif key.startswith("stopped_"):
        job.status = JobStatus.FAILED
        job.error_code = {
            "stopped_service": "timeout",
            "stopped_no_tries": "timeout",
            "stopped_file": "encrypted",
            "stopped_limit": "budget_job_usd",
            "stopped_unknown": "a_code_this_version_does_not_know",
        }[key]
        job.retries = 3 if key == "stopped_no_tries" else 0
    elif key in {"partly_converted", "ready", "ready_remains", "sample"}:
        job.result_json = _decide_all(job.result_json or {})
        if key == "ready":  # and every part converted: nothing a picture, no check failing
            result = job.result_json
            result["elements"] = [e for e in result["elements"] if e["kind"] == "heading"]
            result["fixes"], result["html_checks"] = [], []
        job.error_code = "pages_not_converted" if key == "partly_converted" else None
        job.is_sample = key == "sample"
    elif key == "deleted":
        job.retain_until = now - timedelta(minutes=1)


def _badge(html: str, start: str) -> str:
    at = html.index(start)
    found = re.search(r'status-badge[^"]*">([^<]*)<', html[at : html.index("</tr>", at)])
    assert found, start
    return found.group(1).strip()


# Every key of the table that stored facts make on this branch; `waiting_reset` needs the
# day's budget spent and is checked after the others.
# `being_checked` is what a reader is told of "needs_decisions": the owner's surfaces never
# print it, and the reader page has its own test (tests/test_job_shares.py).
WALKED = [k for k in states.TABLE if k not in {"waiting_reset", "being_checked"}]


def test_every_state_reads_the_same_on_every_surface_from_the_one_table(web):
    """F-033, F-046, F-213: the job page, My documents, the batch page, the parts list and the
    admin queue all print a state's label from `states.TABLE`; none keeps its own words."""
    keys = ["host", *WALKED]
    url = batch_url(send_zip(web.alice, make_zip({f"{k}.pdf": make_pdf(1) for k in keys})))
    run_all(web)
    by_key: dict[str, str] = {}
    with session_scope(web.ctx.factory) as session:
        for n, job in enumerate(sorted(session.scalars(select(Job)), key=lambda j: j.filename)):
            key = job.filename.removesuffix(".pdf")
            by_key[key] = job.id
            job.part_group, job.part_label = "one-file", f"{n + 1}-{n + 1}"
            _set_state(job, key)
    admin = web.client()
    web.allow(ADMIN, admin=True)
    login(admin, ADMIN)

    def check(key: str, label: str) -> None:
        job_id = by_key[key]
        batch = web.alice.get(url).text
        if key == "deleted":
            assert label.split("{")[0] in text_of(batch[batch.index("deleted.pdf") :])
            return
        page = web.alice.get(f"/jobs/{job_id}").text
        assert re.search(r"<title>\s*" + re.escape(label) + ":", page), (key, label)
        listing = web.alice.get("/app/documents", headers={"HX-Request": "true"}).text
        assert _badge(listing, f'id="doc-{job_id}"').startswith(label), key
        assert _badge(batch, f'id="batch-doc-{job_id}"').startswith(label), key
        parts = web.alice.get(f"/jobs/{by_key['host']}").text
        assert f'href="/jobs/{job_id}">Pages ' in parts and f"({label})" in text_of(parts), key
        assert f"<dt>{label}</dt>" in admin.get("/admin").text, key

    for key in WALKED:
        check(key, states.TABLE[key].label)
    with session_scope(web.ctx.factory) as session:
        session.add(SpendDay(day=utc_today(), spent_usd=5.0, held_usd=0.0))  # the day's cap
    check("waiting", states.TABLE["waiting_reset"].label.format(time="00:00 UTC"))
