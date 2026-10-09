"""Page images for the review screen: rendered on demand from the stored upload, in the
upload probe's child process, never stored.

- the routes (`/jobs/{id}/pages/{n}.png`, `/jobs/{id}/regions/{element_id}.png`,
  `/jobs/{id}/review/marks.css`): owner only (404 for anyone else, admins included), 404
  once the job is past its retention time or deleted, `Cache-Control: private, no-store`,
  a 503 (and a content-free log line) when the child fails, and the review screen still
  works when it does;
- the child (`web.pdf_probe render | sizes`): sizes, caps, the outline of a flagged
  symbol, and a clean failure on bad input.
"""

from __future__ import annotations

import logging
import struct
import threading
from datetime import timedelta

import pymupdf
import pytest

from a11y_stem.web import page_images, pdf_probe
from a11y_stem.web.db import Job, session_scope, utcnow
from fixtures.web.review_doc import PAGE, review_output, review_pdf
from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, make_pdf, upload

CONTENT = ("Teacher", "Vectors", "car on a road", "ω", "rigid body")


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = review_output()
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        yield env


def png_size(png: bytes) -> tuple[int, int]:
    assert png.startswith(pdf_probe.PNG_MAGIC)
    width, height = struct.unpack(">II", png[16:24])
    return width, height


def urls(env) -> list[str]:
    base = f"/jobs/{env.job_id}"
    return [
        f"{base}/pages/1.png",
        f"{base}/pages/2.png",
        f"{base}/regions/p1-e2.png",
        f"{base}/regions/p1-e3.png",
        f"{base}/regions/p1-e6.png",
        f"{base}/review/marks.css",
    ]


# --------------------------------------------------------------------------- routes


def test_page_and_region_images_are_rendered_for_the_owner_and_never_cached(web):
    for url in urls(web):
        response = web.browser.get(url)
        assert response.status_code == 200, url
        assert response.headers["cache-control"] == "private, no-store", url
        if url.endswith(".png"):
            assert response.headers["content-type"] == "image/png"
    page = web.browser.get(f"/jobs/{web.job_id}/pages/1.png").content
    # 144 dpi: a Letter page is 1224 x 1584 px.
    assert png_size(page) == (round(PAGE[0] * page_images.PAGE_ZOOM),
                              round(PAGE[1] * page_images.PAGE_ZOOM))  # fmt: skip
    region = web.browser.get(f"/jobs/{web.job_id}/regions/p1-e2.png").content
    width, height = png_size(region)
    assert width > height  # the omega with the words on each side


def test_nobody_else_gets_the_images(web):
    bob = web.client()
    login(bob, BOB)
    for url in urls(web):
        assert bob.get(url).status_code == 404, url
    assert bob.get(f"/jobs/{web.job_id}/review").status_code == 404


def test_signed_out_visitors_are_sent_to_sign_in(web):
    anonymous = web.client()
    for url in urls(web):
        response = anonymous.get(url)
        assert response.status_code == 303 and response.headers["location"] == "/login", url


def test_past_its_retention_time_a_job_has_no_images(web):
    with session_scope(web.ctx.factory) as session:
        job = session.get(Job, web.job_id)
        assert job is not None
        job.retain_until = utcnow() - timedelta(minutes=1)
    for url in urls(web):
        assert web.browser.get(url).status_code == 404, url


def test_a_deleted_job_has_no_images(web):
    from a11y_stem.ops.retention import delete_jobs

    with session_scope(web.ctx.factory) as session:
        delete_jobs(session, [web.job_id])
    for url in urls(web):
        assert web.browser.get(url).status_code == 404, url


@pytest.mark.parametrize("number", ["0", "3", "x", "1.5", "-1", "01x"])
def test_pages_outside_the_document_are_not_found(web, number):
    response = web.browser.get(f"/jobs/{web.job_id}/pages/{number}.png")
    assert response.status_code == 404


def test_regions_of_unknown_or_unfixed_elements_are_not_found(web):
    for element_id in ("p9-e9", "p1-e1", "p1-e4", "bad%20id"):  # paragraphs have no fix
        assert web.browser.get(f"/jobs/{web.job_id}/regions/{element_id}.png").status_code == 404


def test_an_unfinished_job_has_no_images(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        job_id = job_id_from(upload(client, make_pdf(1)))  # queued, no worker run
        assert client.get(f"/jobs/{job_id}/pages/1.png").status_code == 409
        assert client.get(f"/jobs/{job_id}/review").status_code == 409


def test_a_failed_render_is_a_503_and_the_review_screen_still_works(web, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise page_images.RenderFailed()

    monkeypatch.setattr(page_images, "_child", broken)
    caplog.set_level(logging.INFO)
    for url in urls(web)[:5]:
        response = web.browser.get(url)
        assert response.status_code == 503, url
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["retry-after"] == "5"
    page = web.browser.get(f"/jobs/{web.job_id}/review")
    assert page.status_code == 200
    assert "Decision 1 of 3" in page.text
    # The page says so (with JavaScript) and points to the list.
    assert 'class="page-note page-failed" hidden' in page.text
    logged = caplog.text
    assert "page_image_failed" in logged
    assert not [word for word in CONTENT if word in logged]


def test_a_busy_instance_answers_503_instead_of_queueing_renders(web, monkeypatch):
    monkeypatch.setattr(page_images, "_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(page_images, "SLOT_WAIT_S", 0.01)
    page_images._SLOTS.acquire()  # another render holds the only slot
    try:
        response = web.browser.get(f"/jobs/{web.job_id}/pages/1.png")
    finally:
        page_images._SLOTS.release()
    assert response.status_code == 503
    assert web.browser.get(f"/jobs/{web.job_id}/pages/1.png").status_code == 200


def test_nothing_is_stored_for_the_images(web):
    from sqlalchemy import func, select

    from a11y_stem.web.db import Blob

    def blob_count() -> int:
        with session_scope(web.ctx.factory) as session:
            return int(session.scalar(select(func.count()).select_from(Blob)) or 0)

    before = blob_count()
    for url in urls(web):
        web.browser.get(url)
    assert blob_count() == before


# --------------------------------------------------------------------------- the child


def test_the_child_reports_page_sizes():
    assert page_images.page_sizes(review_pdf()) == {0: PAGE, 1: PAGE}


def test_the_child_outlines_the_flagged_symbol_and_nothing_else():
    output = review_output()
    omega = output.result.elements[2]
    assert omega.id == "p1-e2"
    x0, y0, x1, y1 = omega.bbox
    clip = (x0 - 40, y0 - 2, x1 + 40, y1 + 2)
    plain = page_images.render_region(review_pdf(), 0, zoom=6, max_side=1600, clip=clip)
    marked = page_images.render_region(
        review_pdf(), 0, zoom=6, max_side=1600, clip=clip, mark=omega.bbox, chars={"ω"}
    )
    other = page_images.render_region(
        review_pdf(), 0, zoom=6, max_side=1600, clip=clip, mark=omega.bbox, chars={"z"}
    )
    assert png_size(plain) == png_size(marked)
    assert marked != plain  # the outline is drawn
    assert other == plain  # no such glyph there: nothing drawn
    red = _red_pixels(marked)
    assert red, "no outline in the mark colour"
    xs = [x for x, _ in red]
    width = png_size(marked)[0]
    # Around the omega in the middle of the crop, not around the words on either side.
    assert 0.3 * width < min(xs) and max(xs) < 0.7 * width


def _red_pixels(png: bytes) -> list[tuple[int, int]]:
    pix = pymupdf.Pixmap(png)
    out = []
    for y in range(0, pix.height, 2):
        for x in range(0, pix.width, 2):
            r, g, b = pix.pixel(x, y)[:3]
            if r > 140 and g < 60 and b < 60:
                out.append((x, y))
    return out


def test_the_child_outlines_a_whole_box():
    output = review_output()
    heading = next(e for e in output.result.elements if e.id == "p1-e3")
    clip = (40.0, 150.0, 380.0, 220.0)
    plain = page_images.render_region(review_pdf(), 0, zoom=3, max_side=1600, clip=clip)
    boxed = page_images.render_region(
        review_pdf(), 0, zoom=3, max_side=1600, clip=clip, box=heading.bbox
    )
    assert _red_pixels(boxed) and not _red_pixels(plain)


def test_the_child_caps_the_image_size():
    png = pdf_probe.render(review_pdf(), page=0, zoom=50, max_side=99999)
    assert max(png_size(png)) <= pdf_probe.RENDER_MAX_SIDE_PX


@pytest.mark.parametrize(
    "args",
    [
        ["render", "5", "2", "2000"],  # no such page
        ["render", "0", "2", "2000", "--clip", "900,900,950,950"],  # off the page
        ["render", "x", "2", "2000"],
        ["render"],
    ],
)
def test_the_child_fails_cleanly(args, monkeypatch, capsysbinary):
    import io

    stdin = io.TextIOWrapper(io.BytesIO(review_pdf()))
    monkeypatch.setattr("sys.stdin", stdin)
    assert pdf_probe.main(args) == pdf_probe.RENDER_FAILED
    assert capsysbinary.readouterr().out == b""


def test_not_a_pdf_fails_in_the_parent_too():
    with pytest.raises(page_images.RenderFailed):
        page_images.render_page(b"%PDF-1.4 this is not really a pdf", 0)
    with pytest.raises(page_images.RenderFailed):
        page_images.page_sizes(b"<html>")


def _band_pdf() -> bytes:
    """A box title, a heading under it, and a paragraph of several lines after it."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Teacher Support", fontname="hebo", fontsize=14)
    page.insert_text((72, 124), "Teacher Support", fontname="hebo", fontsize=10.5)
    css = "p { font-family: serif; font-size: 11pt; margin: 0; }"
    words = " ".join(f"word{i}" for i in range(80))
    page.insert_htmlbox(pymupdf.Rect(72, 132, 540, 220), f"<p>{words}</p>", css=css)
    data = doc.tobytes()
    doc.close()
    return data


def test_a_headings_band_shows_whole_lines_and_whole_words():
    """The band round a heading used to slice the line above (the box title) and the last
    line, and to stop mid-word on the right. Snapped, every line is in or out whole, and
    every word the right edge crosses is painted over."""
    doc = pymupdf.open(stream=_band_pdf(), filetype="pdf")
    page = doc[0]
    heading = page.search_for("Teacher Support")[1]
    # band_clip's shape: 1.6 font sizes above, 3 below, about half the page wide.
    area = pymupdf.Rect(heading.x0 - 15, heading.y0 - 16.8, 72 + 0.55 * 612, heading.y1 + 31.5)
    snapped, hidden = pdf_probe.snap_band(page, area, heading)
    for rect in pdf_probe._ink_lines(page):
        inside = snapped.y0 <= rect.y0 and rect.y1 <= snapped.y1
        outside = rect.y1 <= snapped.y0 or rect.y0 >= snapped.y1
        assert inside or outside, (rect, snapped)  # no slice of a line
    title = min(pdf_probe._ink_lines(page), key=lambda r: r.y0)
    assert snapped.y0 <= title.y0 < area.y0 < title.y1  # the box title: cut before, whole now
    crossing = [
        pymupdf.Rect(w[:4])
        for w in page.get_text("words")
        if w[0] < snapped.x1 < w[2] and w[3] > snapped.y0 and w[1] < snapped.y1
    ]
    assert crossing, "the test's band should cut words on the right"
    assert len(hidden) == len(crossing)
    for word, cover in zip(
        sorted(crossing, key=lambda r: r.y0), sorted(hidden, key=lambda r: r.y0), strict=True
    ):
        assert cover.contains(pymupdf.Rect(max(word.x0, snapped.x0), word.y0, word.x1, word.y1))
    doc.close()
    # Rendered with --snap the child paints them over: the image differs from the plain one.
    clip = (area.x0, area.y0, area.x1, area.y1)
    plain = pdf_probe.render(_band_pdf(), page=0, zoom=2, max_side=1600, clip=clip)
    snapped_png = pdf_probe.render(_band_pdf(), page=0, zoom=2, max_side=1600, clip=clip, snap=True)
    assert plain != snapped_png


def test_a_heading_with_nothing_above_it_starts_its_band_just_above_it():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.draw_line((60, 90), (550, 90), color=(0, 0, 0), width=1)  # a box's bottom rule
    page.insert_text((72, 112), "Distance", fontname="hebo", fontsize=12)
    heading = page.search_for("Distance")[0]
    area = pymupdf.Rect(heading.x0 - 18, heading.y0 - 19.2, 400, heading.y1 + 36)
    snapped, _ = pdf_probe.snap_band(page, area, heading)
    assert snapped.y0 > 90  # the rule is not in the band
    assert snapped.y0 == pytest.approx(heading.y0 - pdf_probe.SNAP_ABOVE_PT)
    doc.close()
