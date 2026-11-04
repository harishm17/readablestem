"""A zip of documents and the batch page (`FEATURE_BATCH_ZIP`, `routes/batch.py`): the batch zip
through the upload, the limits by tier (10 for the open tier, 30 for invited people), the page
with its live status and its summary line, polling, "Download all", and who may open it."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from fixtures.web.support import (
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
from readablestem.web import access
from readablestem.web.db import Job, QuotaDay, User, session_scope
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


def test_without_the_switch_a_zip_is_not_a_pdf(tmp_path):
    env = WebEnv(tmp_path)  # FEATURE_BATCH_ZIP and FEATURE_LATEX_SOURCE are off by default
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        page = client.get("/app").text
        assert "data-zip" not in page and ".zip" not in page
        response = send_zip(client, make_zip({"a.pdf": make_pdf(1)}))
        assert response.status_code == 415 and "is not a PDF" in response.text
        assert jobs(env) == []


def test_the_form_offers_zips_and_says_how_many_documents(web):
    page = text_of(web.alice.get("/app").text)
    assert "A zip of PDFs and LaTeX project folders also works" in page
    assert "30 at a time" in page
    raw = web.alice.get("/app").text
    assert 'data-zip="1"' in raw and ".zip,application/zip" in raw


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


def test_a_zip_with_a_project_and_a_pdf_places_both(web):
    data = make_zip({"p1/main.tex": ARTICLE, "slides.pdf": make_pdf(1)})
    batch_url(send_zip(web.alice, data))
    names = [j.filename for j in jobs(web)]
    assert names == ["p1.zip", "slides.pdf"]
    with session_scope(web.ctx.factory) as session:
        blob_types = {
            j.filename: session.scalar(
                select(__import__("readablestem.web.db", fromlist=["Blob"]).Blob.media_type).where(
                    __import__("readablestem.web.db", fromlist=["Blob"]).Blob.job_id == j.id
                )
            )
            for j in jobs(web)
        }
    assert blob_types["p1.zip"] == "application/x-readablestem-zip"
    assert blob_types["slides.pdf"] == "application/pdf"


def test_a_zip_that_is_one_project_is_not_a_batch(web):
    response = send_zip(web.alice, make_zip({"main.tex": ARTICLE}), name="paper.zip")
    assert response.status_code == 303 and response.headers["location"].startswith("/jobs/")
    assert [j.filename for j in jobs(web)] == ["paper.zip"]


def test_a_bad_document_in_a_zip_is_one_refusal_beside_the_others(web):
    data = make_zip({"good.pdf": make_pdf(1), "bad.pdf": b"not a pdf"})
    url = batch_url(send_zip(web.alice, data))
    text = text_of(web.alice.get(url).text)
    assert "1 of 2 files uploaded" in text and "bad.pdf is not a PDF" in text
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


def test_the_limits_by_tier_are_ten_for_open_accounts_and_thirty_for_invited():
    assert access.files_per_upload(access.OPEN) == 10
    assert access.files_per_upload(access.INVITED) == 30


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


def test_several_plain_files_follow_the_same_tier_limit(web):
    make_open(web, ALICE)
    token_ = token(web.alice)
    files = [("pdf", (f"f{n}.pdf", make_pdf(1), "application/pdf")) for n in range(11)]
    response = web.alice.post("/jobs", data={"csrf_token": token_}, files=files)
    assert response.status_code == 400 and "more than 10 files" in text_of(response.text)
    assert jobs(web) == []


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


def many_zips(client, n: int, members: dict[str, bytes]):
    token_ = token(client)
    files = [("pdf", (f"z{i}.zip", make_zip(members), "application/zip")) for i in range(n)]
    return client.post("/jobs", data={"csrf_token": token_}, files=files)


def test_the_unpacked_budget_is_one_per_request_not_one_per_zip(web, monkeypatch):
    """Ten zips of two 900 KB members each would unpack 18 MB; the request's budget is twice the
    upload limit (2 MB here), so the zips after the first few are refused unread."""
    from readablestem.web import batch_intake

    reads: list[int] = []
    real = batch_intake._read_member

    def spy(archive, info, budget, cap):
        raw = real(archive, info, budget, cap)
        reads.append(len(raw))
        return raw

    monkeypatch.setattr(batch_intake, "_read_member", spy)
    member = b"%PDF-1.7 " + b"A" * 900_000  # under the ratio floor: not judged by its ratio
    response = many_zips(web.alice, 10, {"a.pdf": member, "b.pdf": member})
    assert response.status_code == 303
    text = text_of(web.alice.get(response.headers["location"]).text)
    assert "too big or has too many files" in text  # the budget ran out part-way
    assert sum(reads) <= 2 * web.settings.upload_limit_bytes + 900_010  # cut where it ran out
    assert len(jobs(web)) <= 3  # most of the 20 documents were never read


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


def test_documents_past_the_limit_are_not_read_at_all(web, monkeypatch):
    from readablestem.web import batch_intake

    make_open(web, ALICE)
    reads: list[int] = []
    real = batch_intake._read_member
    monkeypatch.setattr(
        batch_intake,
        "_read_member",
        lambda archive, info, budget, cap: reads.append(1) or real(archive, info, budget, cap),
    )
    send_zip(web.alice, make_zip({f"d{i:02d}.pdf": make_pdf(1) for i in range(30)}))
    assert len(reads) == 10  # ten read and placed; the other twenty only named


def test_a_pdf_over_the_upload_limit_is_refused_without_being_read(web, monkeypatch):
    from readablestem.web import batch_intake

    reads: list[int] = []
    real = batch_intake._read_member
    monkeypatch.setattr(
        batch_intake,
        "_read_member",
        lambda archive, info, budget, cap: reads.append(1) or real(archive, info, budget, cap),
    )
    block = __import__("os").urandom(8192)  # repeats: compresses 50x, under the ratio cap
    big = b"%PDF-1.7 " + block * (web.settings.upload_limit_bytes // 8192 + 20)
    ok = make_pdf(1)
    url = batch_url(send_zip(web.alice, make_zip({"big.pdf": big, "ok.pdf": ok}), name="b.zip"))
    text = text_of(web.alice.get(url).text)
    assert "big.pdf is larger than" in text
    assert len(reads) == 1  # only ok.pdf was read
    assert [j.filename for j in jobs(web)] == ["ok.pdf"]


# --------------------------------------------------------------------------- the batch page


def run_all(env) -> None:
    while env.run_worker():
        pass


def test_the_page_shows_each_documents_status_as_it_is_now(web):
    url = batch_url(send_zip(web.alice, make_zip({"a.pdf": make_pdf(1), "b.pdf": make_pdf(1)})))
    first = web.alice.get(url).text
    assert "Waiting" in first and 'hx-trigger="every 4s"' in first  # polls while unfinished
    assert (
        'id="batch-summary"' in first and 'role="status"' in first and 'aria-live="polite"' in first
    )
    run_all(web)
    done = web.alice.get(url).text
    assert "hx-trigger" not in done  # nothing left to wait for: no polling
    text = text_of(done)
    assert "2 documents:" in text
    assert "to make" in text or "Ready to download" in text  # the fake result has decisions


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


def test_the_summary_counts_and_names_nobody(web):
    from readablestem.web.routes.batch import summary

    rows = [
        {"state": "ready"},
        {"state": "ready"},
        {"state": "decide"},
        {"state": "converting"},
        {"state": "waiting"},
    ]
    assert summary(rows, 0, 0) == (
        "5 documents: 2 ready to download, 1 needs your decisions, 1 converting, 1 waiting."
    )
    assert summary([{"state": "ready"}], 1, 2).endswith(
        "1 file waits for you to choose pages; 2 files were not uploaded."
    )
    assert summary([], 0, 0) == "0 documents."


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


def test_a_document_with_decisions_is_held_back_and_said_so(web):
    url = batch_url(send_zip(web.alice, make_zip({"a.pdf": make_pdf(1)})))
    run_all(web)  # the default fake result has decisions open
    text = text_of(web.alice.get(url).text)
    assert "needs your decisions" in text and "No document in this batch is ready to share" in text
    assert web.alice.get(url + "/download").status_code == 409


def test_download_all_holds_only_this_batchs_documents(web):
    web.converter.output = ready_output()
    url = batch_url(send_zip(web.alice, make_zip({"a.pdf": make_pdf(1)})))
    other = web.alice.post(
        "/jobs",
        data={"csrf_token": token(web.alice)},
        files={"pdf": ("other.pdf", make_pdf(1), "application/pdf")},
    )
    assert other.status_code == 303
    run_all(web)
    response = web.alice.get(url + "/download")
    assert response.status_code == 200
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        html = [n for n in archive.namelist() if n.endswith(".html")]
        assert len(html) == 1  # this batch's one document; the other upload is not in it


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


def test_the_old_address_redirects_to_the_batch_page_for_the_owner_only(web):
    url = batch_url(send_zip(web.alice, make_zip({"a.pdf": make_pdf(1)})))
    old = url.replace("/app/batch/", "/app/upload/")
    redirected = web.alice.get(old)
    assert redirected.status_code == 303 and redirected.headers["location"] == url
    assert web.bob.get(old).status_code == 404


def test_the_page_is_gone_after_its_hour_but_the_documents_stay(web):
    from datetime import timedelta

    from readablestem.web.db import UploadResult, utcnow

    url = batch_url(send_zip(web.alice, make_zip({"a.pdf": make_pdf(1)})))
    with session_scope(web.ctx.factory) as session:
        row = session.scalar(select(UploadResult))
        assert row is not None
        row.retain_until = utcnow() - timedelta(minutes=1)
    gone = web.alice.get(url)
    assert gone.status_code == 404 and "Your documents are on My documents" in gone.text
    assert len(jobs(web)) == 1


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
