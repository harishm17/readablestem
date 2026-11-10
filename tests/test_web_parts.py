"""T3-3: a file over the page or size limit is kept, and the person chooses which pages to
convert, in parts that each fit; heavy pictures can be shrunk first.

Nothing is converted or counted when the file is put aside; each part is cut out in a child
process, checked like an upload, held to the quota and the caps, and queued as a document of
its own. The rest of the file stays so it can be chosen later.
"""

from __future__ import annotations

import os
import re
from datetime import timedelta
from html import unescape

import pymupdf
import pytest
from sqlalchemy import func, select

from fixtures.web.support import ALICE, BOB, WebEnv, csrf_from, login, make_pdf, token
from readablestem.ops.retention import erase_user, purge_expired
from readablestem.web.db import Blob, Job, PendingUpload, QuotaDay, User, session_scope, utcnow
from readablestem.web.uploads import ExtractFailed, extract_part_isolated


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


@pytest.fixture
def web(tmp_path):
    # A conversion takes 5 pages and 1 MB; a file up to 40 pages and 5 MB is kept.
    env = WebEnv(
        tmp_path,
        max_large_upload_mb=5,
        max_source_pages=40,
        per_user_daily_page_quota=40,
        feature_notation_memory=True,
    )
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.alice = client
        env.bob = env.client()
        login(env.bob, BOB)
        yield env
        env.bob.close()


def heavy_pdf(pages: int = 3, megabytes: float = 1.5) -> bytes:
    """A few pages, each with a picture of random noise that does not compress."""
    doc = pymupdf.open()
    side = int((megabytes * 1_000_000 / pages / 3) ** 0.5)
    for n in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Heavy page {n + 1}")
        pix = pymupdf.Pixmap(pymupdf.csRGB, side, side, os.urandom(side * side * 3), False)
        page.insert_image(pymupdf.Rect(72, 100, 540, 500), pixmap=pix)
    data = doc.tobytes(deflate=True)
    doc.close()
    return data


def post_file(client, name: str, data: bytes, **fields):
    return client.post(
        "/jobs",
        data={"csrf_token": token(client)} | fields,
        files={"pdf": (name, data, "application/pdf")},
    )


def keep_big(web, pages: int = 12, name: str = "chapter2.pdf") -> str:
    response = post_file(web.alice, name, make_pdf(pages))
    assert response.status_code == 303, response.text[:300]
    location = response.headers["location"]
    assert location.startswith("/uploads/"), location
    return location.rsplit("/", 1)[-1]


def jobs_of(env) -> list[Job]:
    with session_scope(env.ctx.factory) as session:
        rows = list(session.scalars(select(Job).order_by(Job.created_at, Job.id)))
        session.expunge_all()
        return rows


def pending_rows(env) -> list[PendingUpload]:
    with session_scope(env.ctx.factory) as session:
        rows = list(session.scalars(select(PendingUpload)))
        session.expunge_all()
        return rows


def pages_used(env) -> int:
    with session_scope(env.ctx.factory) as session:
        return int(session.scalar(select(func.coalesce(func.sum(QuotaDay.pages), 0))) or 0)


def choose(web, upload_id: str, parts: str, client=None, **fields):
    client = client or web.alice
    page = f"/uploads/{upload_id}"
    return client.post(page, data={"csrf_token": token(client, page), "parts": parts} | fields)


# --------------------------------------------------------------------------- keeping the file


def test_a_file_over_the_page_limit_is_kept_not_turned_away_and_costs_nothing(web):
    upload_id = keep_big(web, 12)
    assert jobs_of(web) == [] and pages_used(web) == 0
    [row] = pending_rows(web)
    assert (row.id, row.pages, row.filename) == (upload_id, 12, "chapter2.pdf")
    assert row.done_pages == [] and row.user_id and bytes(row.content).startswith(b"%PDF")


def test_a_file_over_the_hard_limits_is_still_refused_with_the_reason(web):
    too_many = post_file(web.alice, "huge.pdf", make_pdf(41))
    assert too_many.status_code == 422
    assert "huge.pdf has 41 pages; the limit is 40 per upload" in text_of(too_many.text)
    too_big = post_file(web.alice, "big.pdf", make_pdf(1) + b"\0" * 5_300_000)
    assert too_big.status_code == 413 and "larger than 5 MB" in text_of(too_big.text)
    assert pending_rows(web) == [] and jobs_of(web) == []


def test_only_a_few_large_files_wait_at_once(web):
    keep_big(web, 12, "a.pdf")
    keep_big(web, 12, "b.pdf")
    response = post_file(web.alice, "c.pdf", make_pdf(12))
    assert response.status_code == 429
    assert "already have 2 large files waiting" in text_of(response.text)
    assert len(pending_rows(web)) == 2


# --------------------------------------------------------------------------- converting parts


def test_the_suggested_parts_become_documents_that_hold_the_right_pages(web):
    upload_id = keep_big(web, 12)
    response = choose(web, upload_id, "1-4\n5-8\n9-12")
    assert response.status_code == 303 and response.headers["location"] == "/app"
    jobs = jobs_of(web)
    assert [j.part_label for j in jobs] == ["1-4", "5-8", "9-12"]
    assert [j.filename for j in jobs] == [
        "chapter2 (pages 1-4).pdf",
        "chapter2 (pages 5-8).pdf",
        "chapter2 (pages 9-12).pdf",
    ]
    assert {j.part_group for j in jobs} == {upload_id} and {j.page_count for j in jobs} == {4}
    assert pages_used(web) == 12  # the pages of the parts, once
    with session_scope(web.ctx.factory) as session:
        for job, first in zip(
            jobs, ("Kinematics page 1", "Kinematics page 5", "Kinematics page 9"), strict=True
        ):
            data = session.scalar(
                select(Blob.content).where(Blob.job_id == job.id, Blob.kind == "upload")
            )
            doc = pymupdf.open(stream=data, filetype="pdf")
            assert doc.page_count == 4 and doc[0].get_text().strip() == first
    assert pending_rows(web) == []  # every page was sent: the waiting copy is deleted
    assert "3 parts are converting" in text_of(web.alice.get("/app").text)


def test_a_part_that_does_not_fit_today_is_not_started_and_the_file_stays(web):
    web.settings.per_user_daily_page_quota = 9
    upload_id = keep_big(web, 12)
    response = choose(web, upload_id, "1-4\n5-8\n9-12")
    assert response.status_code == 429
    text = text_of(response.text)
    assert "Part 3 (pages 9-12) has 4 pages, but you have 1 of today's 9 pages left" in text
    assert "It was not started" in text
    assert [j.part_label for j in jobs_of(web)] == ["1-4", "5-8"]
    assert pages_used(web) == 8
    [row] = pending_rows(web)
    assert row.done_pages == list(range(8))
    # The two that were started are linked from the same page.
    assert text.count("Pages 1-4") >= 1 and "2 parts are converting" in text
    # Tomorrow (the count starts again): the rest is chosen and the waiting copy goes.
    with session_scope(web.ctx.factory) as session:
        session.execute(QuotaDay.__table__.delete())
    again = choose(web, upload_id, "9-12")
    assert again.status_code == 303
    assert [j.part_label for j in jobs_of(web)] == ["1-4", "5-8", "9-12"]
    assert pending_rows(web) == []


def test_a_page_cannot_be_converted_twice(web):
    upload_id = keep_big(web, 12)
    choose(web, upload_id, "1-4")
    response = choose(web, upload_id, "3-6")
    assert response.status_code == 400 and "page 3 was already converted" in text_of(response.text)
    assert len(jobs_of(web)) == 1


def test_the_caps_on_documents_still_hold_for_parts(web):
    from readablestem.web.access import INVITED_FILES_PER_UPLOAD as MAX_ACTIVE_JOBS_PER_USER

    web.settings.per_user_daily_page_quota = 500
    for n in range(MAX_ACTIVE_JOBS_PER_USER - 1):
        post_file(web.alice, f"w{n}.pdf", make_pdf(1))
    upload_id = keep_big(web, 12)
    response = choose(web, upload_id, "1-4\n5-8\n9-12")
    assert response.status_code == 429
    assert f"You already have {MAX_ACTIVE_JOBS_PER_USER} documents waiting" in text_of(
        response.text
    )
    assert "Part 2 (pages 5-8) was not started" in text_of(response.text)
    [row] = pending_rows(web)  # the file is kept for later
    assert row.done_pages == [0, 1, 2, 3]


# --------------------------------------------------------------------------- shrinking


def test_shrinking_pictures_makes_a_part_small_enough_and_keeps_its_text(web):
    data = heavy_pdf(3, 3.0)  # 3 MB over a 1 MB limit: two of its pages are 2 MB
    upload_id = post_file(web.alice, "scans.pdf", data).headers["location"].rsplit("/", 1)[-1]
    plain = choose(web, upload_id, "1-2")
    assert plain.status_code == 413
    assert re.search(r"is \d\.\d MB, over the 1 MB limit", text_of(plain.text))
    assert "Shrink large pictures first" in text_of(plain.text)
    assert jobs_of(web) == [] and pages_used(web) == 0
    shrunk = choose(web, upload_id, "1-2", shrink="on")
    assert shrunk.status_code == 303, text_of(shrunk.text)[:300]
    [job] = jobs_of(web)
    with session_scope(web.ctx.factory) as session:
        stored = session.scalar(
            select(Blob.content).where(Blob.job_id == job.id, Blob.kind == "upload")
        )
    assert len(stored) < 1_000_000 < len(data)
    doc = pymupdf.open(stream=stored, filetype="pdf")
    assert [p.get_text().strip() for p in doc] == ["Heavy page 1", "Heavy page 2"]


def test_the_part_extractor_cuts_the_pages_asked_for_in_that_order():
    data = make_pdf(6)
    part = extract_part_isolated(data, [4, 1])
    doc = pymupdf.open(stream=part, filetype="pdf")
    assert [p.get_text().strip() for p in doc] == ["Kinematics page 5", "Kinematics page 2"]
    for bad in ([], [9], [1, 1], [-1]):
        with pytest.raises(ExtractFailed):
            extract_part_isolated(data, bad)
    with pytest.raises(ExtractFailed):
        extract_part_isolated(b"%PDF-1.4 not really", [0])


def test_an_extraction_that_hangs_is_a_failed_part_not_a_hung_request(web, monkeypatch):
    import subprocess

    upload_id = keep_big(web, 12)

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="extract", timeout=0.01)

    monkeypatch.setattr(subprocess, "run", hang)
    response = choose(web, upload_id, "1-4")
    assert response.status_code == 422
    assert "Part" not in text_of(response.text).split("There is a problem")[0]
    assert "could not be cut out of the file" in text_of(response.text)
    assert jobs_of(web) == [] and pages_used(web) == 0


# --------------------------------------------------------------------------- access, retention


def test_only_the_owner_can_see_choose_or_discard_a_waiting_file(web):
    upload_id = keep_big(web, 12)
    ghost = "0" * 32
    token_ = csrf_from(web.bob.get("/app").text)
    assert web.bob.get(f"/uploads/{upload_id}").status_code == 404
    assert web.bob.get(f"/uploads/{ghost}").status_code == 404
    for path in (f"/uploads/{upload_id}", f"/uploads/{upload_id}/delete"):
        assert (
            web.bob.post(
                path, data={"csrf_token": token_, "parts": "1-4", "confirm": "yes"}
            ).status_code
            == 404
        )
    assert len(pending_rows(web)) == 1 and jobs_of(web) == []
    # Without the token, nothing happens either.
    for path in (f"/uploads/{upload_id}", f"/uploads/{upload_id}/delete"):
        assert web.alice.post(path, data={"parts": "1-4", "confirm": "yes"}).status_code == 403
    assert len(pending_rows(web)) == 1


def test_discarding_asks_first_and_then_deletes_the_copy(web):
    upload_id = keep_big(web, 12)
    first = web.alice.post(
        f"/uploads/{upload_id}/delete",
        data={"csrf_token": token(web.alice, f"/uploads/{upload_id}")},
    )
    assert first.status_code == 200 and "Discard “chapter2.pdf”?" in first.text
    assert len(pending_rows(web)) == 1
    done = web.alice.post(
        f"/uploads/{upload_id}/delete",
        data={"csrf_token": token(web.alice, f"/uploads/{upload_id}"), "confirm": "yes"},
    )
    assert done.status_code == 303 and pending_rows(web) == []
    assert web.alice.get(f"/uploads/{upload_id}").status_code == 404


def test_a_waiting_file_is_gone_at_its_deletion_time_and_with_its_owner(web):
    upload_id = keep_big(web, 12)
    with session_scope(web.ctx.factory) as session:
        row = session.get(PendingUpload, upload_id)
        row.retain_until = utcnow() - timedelta(minutes=1)
    assert web.alice.get(f"/uploads/{upload_id}").status_code == 404  # before the purge runs
    assert "Large files waiting" not in web.alice.get("/app").text
    stats = purge_expired(web.ctx.factory, utcnow())
    assert stats.pending_deleted == 1 and pending_rows(web) == []
    other = keep_big(web, 12, "again.pdf")
    assert other
    with session_scope(web.ctx.factory) as session:
        user_id = session.scalar(select(User.id).where(User.email == ALICE))
        erase_user(session, user_id)
    assert pending_rows(web) == []
