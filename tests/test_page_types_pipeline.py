"""Hidden text and kept pages through layout, the pipeline, the student file and the app.

The real layout and pipeline with the offline model client, on the synthetic fixtures of
tests/fixtures/handwriting (no real handwriting, scan or OCR output). What must hold:
hidden text never becomes content; a handwritten or scanned page is kept as its page
image, marked "Not converted", with one required decision per reason for the whole
document; a typed page keeps its typed text; a PDF of only such pages is refused at
upload before any quota is spent; nothing is ever said to be converted later.
"""

from __future__ import annotations

import copy
import re
from html import unescape
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import select

from a11y_stem.config import Settings
from a11y_stem.core import triage
from a11y_stem.core.types import Kind, Status
from a11y_stem.models import OFFLINE_DEFAULTS
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.pipeline import layout
from a11y_stem.pipeline.run import convert_pdf
from a11y_stem.web.db import Job, User, session_scope
from a11y_stem.web.worker import Worker, result_outcome
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, upload

HANDWRITING = Path(__file__).parent / "fixtures" / "handwriting"
TEXT_KINDS = (Kind.PARAGRAPH, Kind.HEADING, Kind.LIST_ITEM, Kind.EQUATION, Kind.TABLE)
# Words of the fixtures' hidden layers and covered lines: none may reach the output.
HIDDEN = ("Hidden phrase", "render mode three", "zero opacity", "Answer key")


def data(name: str) -> bytes:
    return (HANDWRITING / name).read_bytes()


def convert(name: str):
    client = FakeVisionClient(defaults=copy.deepcopy(OFFLINE_DEFAULTS))
    return convert_pdf(data(name), settings=Settings(_env_file=None), client=client)  # type: ignore[call-arg]


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


# --------------------------------------------------------------------------- layout


def test_layout_reads_no_hidden_text_and_keeps_the_clip_mode_title():
    doc = layout.analyze(data("typed_hidden.pdf"))
    text = " ".join(e.text or "" for e in doc.elements)
    assert not any(phrase in text for phrase in HIDDEN)
    assert "Momentum and energy" in text  # render mode 7 over bands: visible, kept
    assert "Displacement is the change in position" in text
    assert [(n.code, n.page, n.count, n.review) for n in doc.notices] == [
        ("hidden_text_ignored", 0, 112, "optional")
    ]
    assert doc.kept_pages == {}


def test_handwritten_and_scanned_pages_have_no_elements_and_are_kept():
    doc = layout.analyze(data("mixed_course.pdf"))
    assert {e.page for e in doc.elements} == {0}
    assert doc.kept_pages == {1: "handwritten_page", 2: "handwritten_page", 3: "scanned_page"}
    assert [doc.page_types[n].kind for n in range(4)] == [
        "born_digital",
        "handwritten_ocr",
        "handwritten",
        "scan_ocr",
    ]
    assert [(n.code, n.page, n.review) for n in doc.notices] == [
        ("ocr_text_layer_ignored", 1, "none"),
        ("ocr_text_layer_ignored", 3, "none"),
    ]


def test_handwriting_on_a_typed_page_is_cut_out_and_the_text_stays():
    doc = layout.analyze(data("typed_with_ink.pdf"))
    ink = [e for e in doc.elements if "handwriting" in e.reasons]
    assert len(ink) == 1 and ink[0].kind is Kind.FIGURE and ink[0].text is None
    paragraphs = [e for e in doc.elements if e.kind is Kind.PARAGRAPH]
    assert len(paragraphs) == 3
    assert all(p.bbox[3] <= ink[0].bbox[1] + 1 for p in paragraphs)  # the ink is below
    assert doc.kept_pages == {}


def test_the_safety_net_keeps_a_painted_page_layout_read_nothing_from():
    doc = layout.analyze(data("drawn_page.pdf"))
    assert doc.page_types[1].kind == "vector_no_text"  # not handwriting, not a scan ...
    assert doc.kept_pages == {1: "unread_page"}  # ... but painted, and nothing read


def test_photo_slides_keep_their_titles_and_photos():
    doc = layout.analyze(data("photo_slides.pdf"))
    assert doc.kept_pages == {}
    assert [e.kind for e in doc.elements if e.page == 0] == [Kind.PARAGRAPH, Kind.FIGURE]
    assert doc.elements[0].text == "Lab setup"


def test_text_under_see_through_overlays_is_read():
    doc = layout.analyze(data("overlays.pdf"))
    assert doc.kept_pages == {}
    assert {e.page for e in doc.elements if e.kind is Kind.PARAGRAPH} == {0, 1}


# --------------------------------------------------------------------------- pipeline


def test_an_ios_style_page_never_yields_text_and_always_yields_a_decision():
    result = convert("ios_notes.pdf").result
    assert not [e for e in result.elements if e.kind in TEXT_KINDS]
    (fix,) = result.fixes
    assert fix.kind is Kind.FIGURE and fix.status is Status.FAILED
    assert fix.reason == "handwritten_page" and fix.review == "required"
    assert fix.group_key == "pg-p1-e0" and fix.group_size == 1
    assert result.stats.required_decisions == 1 and result.stats.model_calls == 0
    assert [t.kind for t in result.page_types] == ["handwritten_ocr"]
    assert result.image_only_pages == [0]
    page_notices = [n for n in result.notices if not n.code.startswith("lang_")]
    assert [(n.code, n.page, n.count, n.review) for n in page_notices] == [
        ("ocr_text_layer_ignored", 0, 120, "none"),
        ("handwritten_page", 0, 1, "required"),
    ]
    html = result.html
    assert 'data-reason="handwritten_page"' in html
    assert 'alt="Page 1: handwritten notes, not converted to text."' in html
    assert "Page 1 is a picture of a handwritten or scanned page and was not converted." in html
    # Nothing of the hidden layer is in the file: its only text is the tool's own words.
    with pymupdf.open(HANDWRITING / "ios_notes.pdf") as doc:
        layer = [w[4] for w in doc[0].get_text("words") if len(w[4]) >= 4]
    assert len(layer) > 10 and not any(f" {word} " in f" {text_of(html)} " for word in layer)
    assert result_outcome(result) == ("failed", "handwritten")


def test_kept_pages_are_one_decision_per_reason_and_typed_pages_convert():
    output = convert("mixed_course.pdf")
    result = output.result
    kept = [f for f in result.fixes if f.reason in {"handwritten_page", "scanned_page"}]
    assert [(f.element_id, f.reason, f.group_key, f.group_size) for f in kept] == [
        ("p2-e0", "handwritten_page", "pg-p2-e0", 2),
        ("p3-e0", "handwritten_page", "pg-p2-e0", 2),
        ("p4-e0", "scanned_page", "pg-p4-e0", 1),
    ]
    assert triage.required_decisions(result.fixes) == 2  # handwritten, scanned
    assert all(e.id in output.crops for e in result.elements if e.page > 0)  # page images
    assert result.image_only_pages == [1, 2, 3]
    html = result.html
    assert "Displacement is the change in position" in html
    assert "Pages 2, 3 and 4 are pictures of handwritten or scanned pages" in html
    assert html.count("Not converted.") == 3
    assert result_outcome(result) == ("done", "pages_not_converted")


def test_handwriting_on_a_typed_page_is_its_own_decision():
    result = convert("typed_with_ink.pdf").result
    (ink,) = [f for f in result.fixes if f.kind is Kind.FIGURE]
    assert ink.reason == "handwriting_on_page" and ink.review == "required"
    assert ink.calls == []  # never sent to the figure model
    assert "Handwriting on page 1, not converted to text." in result.html
    assert "Displacement is the change in position" in result.html


def test_the_safety_net_page_is_kept_with_a_decision():
    result = convert("drawn_page.pdf").result
    (kept,) = [f for f in result.fixes if f.reason == "unread_page"]
    assert kept.review == "required" and kept.status is Status.FAILED
    assert 'alt="Page 2: not converted to text."' in result.html


def test_hidden_text_never_reaches_the_student_file():
    html = convert("typed_hidden.pdf").result.html
    assert not any(phrase in html for phrase in HIDDEN)
    assert "Momentum and energy" in html


# --------------------------------------------------------------------------- the app


@pytest.fixture
def env(tmp_path):
    env = WebEnv(tmp_path, max_pages_per_job=30)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


def pages_used(env) -> int:
    with session_scope(env.ctx.factory) as session:
        user = session.scalar(select(User).where(User.email == ALICE))
        return sum(j.page_count for j in session.scalars(select(Job).where(Job.user_id == user.id)))


@pytest.mark.parametrize(
    ("name", "says"),
    [
        ("ios_notes.pdf", "cannot convert it. The PDF also holds hidden text"),
        ("ink_no_layer.pdf", "cannot convert it. It has no text this tool can use"),
        ("scan_ocr.pdf", "Running OCR again will not help"),
        ("scan_text_under_image.pdf", "is scanned pages, so this tool cannot convert it"),
    ],
)  # fmt: skip
def test_a_pdf_of_only_handwritten_or_scanned_pages_is_refused_before_any_quota(env, name, says):
    response = upload(env.browser, data(name), filename=name)
    assert response.status_code == 422
    text = text_of(response.text)
    assert says in text
    assert "no pages were taken from your daily allowance" in text
    assert "yet" not in text.split("cannot convert it")[1][:5]  # no promise of later
    with session_scope(env.ctx.factory) as session:
        assert session.scalar(select(Job.id)) is None
    assert pages_used(env) == 0


def _worker(env) -> Worker:
    def client(settings, budget):
        return FakeVisionClient(defaults=copy.deepcopy(OFFLINE_DEFAULTS), budget=budget)

    return Worker(env.settings, env.ctx.factory, converter=convert_pdf, client_factory=client)


def test_a_course_pdf_with_kept_pages_is_reported_and_decided_once_per_reason(env):
    job_id = job_id_from(upload(env.browser, data("mixed_course.pdf"), filename="course.pdf"))
    assert _worker(env).run_once() == job_id
    job = env.job(job_id)
    assert (job.status, job.error_code) == ("done", "pages_not_converted")

    page = text_of(env.browser.get(f"/jobs/{job_id}").text)
    assert "Finished, with pages not converted" in page
    assert "Pages 2 and 3 are handwritten, so they were not converted." in page
    assert "Page 4 is a scanned image, so it was not converted." in page
    assert "Hidden text that a tablet or a text recognition (OCR) program guessed" in page
    assert "(413 characters) was not used" in page and "(413 times)" not in page
    assert "OCR on" not in page and "run text recognition" not in page

    split = text_of(env.browser.get(f"/jobs/{job_id}/review").text)
    assert "Decision 1 of 2" in split
    assert "Pages 2 and 3 are handwritten." in split
    assert "Keep the page images, marked “Not converted”" in split
    assert "type them up (for example in LaTeX or Word)" in split
    assert "Hidden text in the PDF" in split and "52 characters" in split

    listing = text_of(env.browser.get(f"/jobs/{job_id}/review/list").text)
    assert (
        "Handwritten pages, not converted" in listing and "Scanned page, not converted" in listing
    )
    assert "Needs your decision (2)" in listing


def test_one_click_keeps_every_page_of_the_group(env):
    job_id = job_id_from(upload(env.browser, data("mixed_course.pdf"), filename="course.pdf"))
    _worker(env).run_once()
    page = env.browser.get(f"/jobs/{job_id}/review")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    version = re.search(r'name="version" value="(\d+)"', page.text)
    assert token and version
    response = env.browser.post(
        f"/jobs/{job_id}/review/pg-p2-e0",
        data={"csrf_token": token.group(1), "action": "reject", "view": "split",
              "version": version.group(1)},
        follow_redirects=False,
    )  # fmt: skip
    assert response.status_code == 303
    result = env.job(job_id).result_json
    assert result is not None
    statuses = {f["element_id"]: (f["status"], f["reviewed"]) for f in result["fixes"]}
    assert statuses["p2-e0"] == statuses["p3-e0"] == ("rejected", True)
    assert statuses["p4-e0"] == ("failed", False)  # the scan is its own decision
    after = text_of(env.browser.get(response.headers["location"]).text)
    assert "Handwritten pages, not converted: Kept 2 as images" in after
    assert "1 decision left" in after
    html = env.job(job_id).html or ""
    assert html.count('data-reason="handwritten_page"') == 2  # still kept, marked


def _post(env, job_id: str, key: str, action: str, view: str):
    page = env.browser.get(f"/jobs/{job_id}/review" + ("/list" if view == "list" else ""))
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    version = re.search(r'name="version" value="(\d+)"', page.text)
    assert token and version
    return env.browser.post(
        f"/jobs/{job_id}/review/{key}",
        data={"csrf_token": token.group(1), "action": action, "view": view,
              "version": version.group(1)},
        follow_redirects=False,
    )  # fmt: skip


def test_the_list_view_keeps_the_group_and_refuses_anything_but_keep(env):
    job_id = job_id_from(upload(env.browser, data("mixed_course.pdf"), filename="course.pdf"))
    _worker(env).run_once()
    refused = _post(env, job_id, "pg-p2-e0", "accept", "list")
    assert refused.status_code == 422
    assert "these images have nothing else to accept" in text_of(refused.text)
    assert 'href="#group-actions-pg-p2-e0"' in refused.text  # the error summary's link
    kept = _post(env, job_id, "pg-p4-e0", "reject", "list")
    assert kept.status_code == 303 and "/review/list#" in kept.headers["location"]
    listing = text_of(env.browser.get(f"/jobs/{job_id}/review/list").text)
    assert "Needs your decision (1)" in listing
    assert "Kept by you" in listing  # the scanned page, in "Your decisions"
    assert _post(env, job_id, "pg-nothing", "reject", "split").status_code == 404
