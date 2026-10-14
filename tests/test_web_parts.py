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
from readablestem.web import ranges
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


def test_the_page_for_choosing_suggests_parts_that_cover_the_whole_file(web):
    upload_id = keep_big(web, 12)
    page = web.alice.get(f"/uploads/{upload_id}")
    assert page.status_code == 200
    text = text_of(page.text)
    assert "chapter2.pdf has 12 pages" in text
    assert "One conversion takes up to 5 pages" in text
    assert "Nothing has been converted yet, and no pages have been taken" in text
    area = re.search(r'<textarea[^>]*id="parts"[^>]*>(.*?)</textarea>', page.text, re.S)
    assert area is not None and area.group(1).split() == ["1-4", "5-8", "9-12"]
    assert 'for="parts"' in page.text and "Shrink large pictures first" in text
    assert "You can convert 40 more pages today" in text
    assert "This file is kept until" in text


def test_a_file_over_the_size_limit_is_kept_even_with_few_pages(web):
    response = post_file(web.alice, "scans.pdf", heavy_pdf(3, 1.6))
    assert response.status_code == 303 and response.headers["location"].startswith("/uploads/")
    page = web.alice.get(response.headers["location"])
    assert "This file is large because of its pictures, so this is ticked" in text_of(page.text)
    assert re.search(r'<input[^>]*id="shrink"[^>]*checked', page.text)


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


def test_a_revised_version_must_fit_one_conversion(web):
    first = post_file(web.alice, "a.pdf", make_pdf(2)).headers["location"].rsplit("/", 1)[-1]
    web.run_worker()
    response = web.alice.post(
        "/jobs",
        data={"csrf_token": token(web.alice), "revises": first},
        files={"pdf": ("v2.pdf", make_pdf(12), "application/pdf")},
    )
    assert response.status_code == 422 and "cannot be a revised version" in text_of(response.text)
    assert pending_rows(web) == []


def test_my_documents_lists_the_files_waiting_with_a_link_to_choose(web):
    upload_id = keep_big(web, 12)
    page = web.alice.get("/app")
    assert "Large files waiting for you" in page.text
    assert f'href="/uploads/{upload_id}"' in page.text
    assert "Choose the pages of chapter2.pdf" in text_of(page.text)


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


def test_one_part_goes_straight_to_its_page_and_says_which_pages_it_is(web):
    upload_id = keep_big(web, 12)
    response = choose(web, upload_id, "1-5")
    assert response.status_code == 303 and response.headers["location"].startswith("/jobs/")
    page = text_of(web.alice.get(response.headers["location"]).text)
    assert "This is pages 1-5 of a larger file" in page
    # The rest is still waiting and the chooser leaves out what was sent.
    again = text_of(web.alice.get(f"/uploads/{upload_id}").text)
    assert "Pages 1-5 are already sent to be converted" in again
    area = re.search(
        r'<textarea[^>]*id="parts"[^>]*>(.*?)</textarea>',
        web.alice.get(f"/uploads/{upload_id}").text,
        re.S,
    )
    assert area is not None and area.group(1).split() == ["6-9", "10-12"]


def test_the_parts_of_one_file_link_to_each_other(web):
    upload_id = keep_big(web, 12)
    choose(web, upload_id, "1-4\n5-8")
    first, second = jobs_of(web)
    page = web.alice.get(f"/jobs/{second.id}").text
    assert 'aria-label="Parts of this file"' in page
    assert f'href="/jobs/{first.id}"' in page and "Pages 5-8 (this document)" in page


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


def test_what_was_typed_wrongly_is_explained_and_kept_in_the_box(web):
    upload_id = keep_big(web, 12)
    response = choose(web, upload_id, "1-9")
    assert response.status_code == 400
    text = text_of(response.text)
    assert "The pages has 9 pages. A part can have at most 5" in text or "has 9 pages" in text
    assert 'class="error-summary"' in response.text
    assert re.search(r'<textarea[^>]*aria-invalid="true"', response.text)
    assert ">1-9</textarea>" in response.text
    assert jobs_of(web) == [] and pages_used(web) == 0
    beyond = choose(web, upload_id, "10-13")
    assert "page 13 is not in this file, which has 12 pages" in text_of(beyond.text)


def test_the_caps_on_documents_still_hold_for_parts(web):
    from readablestem.web.intake import MAX_ACTIVE_JOBS_PER_USER

    web.settings.per_user_daily_page_quota = 500
    for n in range(MAX_ACTIVE_JOBS_PER_USER - 1):
        post_file(web.alice, f"w{n}.pdf", make_pdf(1))
    upload_id = keep_big(web, 12)
    response = choose(web, upload_id, "1-4\n5-8\n9-12")
    assert response.status_code == 429
    assert "You already have 10 documents waiting" in text_of(response.text)
    assert "Part 2 (pages 5-8) was not started" in text_of(response.text)
    [row] = pending_rows(web)  # the file is kept for later
    assert row.done_pages == [0, 1, 2, 3]


def test_the_chosen_course_and_exam_mode_follow_the_file_to_every_part(web):
    made = web.alice.post(
        "/courses", data={"csrf_token": csrf_from(web.alice.get("/courses").text), "name": "P1"}
    )
    course_id = made.headers["location"].rsplit("/", 1)[-1]
    response = post_file(web.alice, "ch.pdf", make_pdf(12), course=course_id, exam="on", keep="on")
    upload_id = response.headers["location"].rsplit("/", 1)[-1]
    choose(web, upload_id, "1-4\n5-8")
    jobs = jobs_of(web)
    assert {j.course_id for j in jobs} == {course_id}
    assert {j.exam_mode for j in jobs} == {True} and {j.retention_mode for j in jobs} == {"week"}


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
    assert first.status_code == 200 and "Discard chapter2.pdf?" in first.text
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


def test_a_waiting_file_does_not_count_toward_the_documents_a_person_keeps(web):
    keep_big(web, 12)
    assert jobs_of(web) == []
    from readablestem.web.intake import live_job_count

    with session_scope(web.ctx.factory) as session:
        user_id = session.scalar(select(User.id).where(User.email == ALICE))
        assert live_job_count(session, user_id, utcnow()) == 0


def test_the_parts_helpers_agree_with_the_chooser(web):
    """What the page suggests is what the parser accepts."""
    upload_id = keep_big(web, 40)
    page = web.alice.get(f"/uploads/{upload_id}").text
    area = re.search(r'<textarea[^>]*id="parts"[^>]*>(.*?)</textarea>', page, re.S)
    assert area is not None
    parts = ranges.parse_parts(unescape(area.group(1)), total=40, limit=5)
    assert sum(len(p.pages) for p in parts) == 40


def test_the_known_limits_and_privacy_pages_say_what_happens_to_a_large_file(web):
    limits = text_of(web.alice.get("/limits").text)
    assert "Up to 5 pages and 1 MB per upload." in limits
    assert "A bigger file, up to 40 pages and 5 MB, is kept for a day so you can choose" in limits
    assert "the parts are not joined into one file" in limits
    privacy = text_of(web.alice.get("/privacy").text)
    assert "A PDF that is too large to convert at once is kept, as uploaded" in privacy
    assert "Nothing is sent to an AI service until you choose the pages" in privacy


def test_without_the_bigger_limits_the_limits_page_still_says_to_split(tmp_path):
    env = WebEnv(tmp_path)  # the test defaults: no keeping of larger files
    with env.running() as client:
        assert "Split longer documents." in text_of(client.get("/limits").text)
