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
from a11y_stem.pipeline import layout, page_type
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


def test_the_safety_net_keeps_writing_read_into_nothing_under_a_typed_title():
    # Writing the ink checks reject, on paper ruled from top to bottom, under a short and a
    # long typed title: the titles are read, and each page keeps its image with a decision.
    doc = layout.analyze(data("ink_titled.pdf"))
    assert doc.kept_pages == {0: "unread_page", 1: "unread_page"}
    assert [(e.page, e.kind) for e in doc.elements] == [(0, Kind.PARAGRAPH), (1, Kind.PARAGRAPH)]
    assert doc.elements[0].text == "Lecture 14 notes"


def test_a_short_note_under_a_typed_title_keeps_its_page():
    # Five, three and five pen strokes (the last written one segment per path) under a
    # short typed title: too few to be called handwriting under a title (they could be
    # machine-drawn curves), too small to be a figure. The titles are read and each page
    # keeps its image with a decision: never "Everything was converted".
    doc = layout.analyze(data("note_titled.pdf"))
    assert [doc.page_types[n].kind for n in range(3)] == ["vector_no_text"] * 3
    assert doc.kept_pages == {0: "unread_page", 1: "unread_page", 2: "unread_page"}
    assert [(e.page, e.text) for e in doc.elements] == [
        (0, "Week 6 notes"),
        (1, "Week 7 notes"),
        (2, "Week 5 notes, Oct 3"),
    ]
    result = convert("note_titled.pdf").result
    assert [f.reason for f in result.fixes] == ["unread_page"] * 3
    assert result.stats.required_decisions == 1 and result.stats.model_calls == 0
    assert result_outcome(result) == ("done", "pages_not_converted")


def test_writing_not_analysed_in_time_under_a_title_is_kept(monkeypatch):
    # The ink budget ran out (a long document): the pages may be handwritten, so they are
    # kept as images, whatever the length of their typed title.
    real = page_type.InkBudget
    monkeypatch.setattr(page_type, "InkBudget", lambda: real(0))
    doc = layout.analyze(data("ink_titled.pdf"))
    assert [doc.page_types[n].kind for n in (0, 1)] == ["vector_no_text", "born_digital"]
    assert doc.kept_pages == {0: "unread_page", 1: "unread_page"}
    # The titles are still read; nothing drawn goes to the figure model.
    assert [(e.page, e.kind) for e in doc.elements] == [(0, Kind.PARAGRAPH), (1, Kind.PARAGRAPH)]


def test_drawings_that_fill_the_page_are_figures():
    doc = layout.analyze(data("big_drawings.pdf"))
    assert doc.kept_pages == {}
    figures = [e for e in doc.elements if e.kind is Kind.FIGURE]
    assert [f.page for f in figures] == [0, 1, 2]
    # Page 1: a plot inside a drawn page border; the border is not part of it.
    border, plot = (24, 24, 588, 768), figures[0].bbox
    assert plot[2] - plot[0] < 0.5 * (border[2] - border[0])
    assert figures[0].caption == "Figure 1: height of the ball against time."
    assert "Problem 3." in " ".join(e.text or "" for e in doc.elements if e.page == 0)
    # Pages 2 and 3: a full-page plot with its caption, a slide's chart under its title.
    assert figures[1].caption == "Figure 2: displacement against time (s)."
    assert [e.kind for e in doc.elements if e.page == 2] == [Kind.HEADING, Kind.FIGURE]


def test_template_pages_with_nothing_to_read_are_not_decisions():
    doc = layout.analyze(data("template_pages.pdf"))
    assert doc.kept_pages == {}
    assert {e.page for e in doc.elements} == {2}
    result = convert("template_pages.pdf").result
    assert result.stats.required_decisions == 0
    assert "Not converted" not in result.html


def test_machine_drawn_diagrams_under_a_title_are_figures():
    doc = layout.analyze(data("diagram_slides.pdf"))
    assert doc.kept_pages == {}
    assert [e.kind for e in doc.elements if e.page == 0] == [Kind.PARAGRAPH, Kind.FIGURE]
    assert sum(e.kind is Kind.FIGURE for e in doc.elements if e.page == 1) == 5
    timing = layout.analyze(data("timing_diagram.pdf"))
    assert [e.kind for e in timing.elements] == [Kind.FIGURE] and timing.kept_pages == {}


def test_a_picture_of_a_page_keeps_its_typed_text_and_its_image():
    # A scan cropped to its picture with a typed header, and a title slide on a photo:
    # pictures of the whole page with a little visible text and nothing hidden.
    doc = layout.analyze(data("cropped_scan.pdf"))
    assert doc.kept_pages == {0: "unread_page"}
    assert [e.text for e in doc.elements] == ["PHYS 101 Week 3 reading"]
    result = convert("title_slide.pdf").result
    assert [e.kind for e in result.elements] == [Kind.HEADING, Kind.FIGURE]
    assert "Lecture 3: Kinematics" in text_of(result.html)
    assert 'alt="Page 1: not converted to text."' in result.html
    assert "scanned" not in text_of(result.html)
    assert result.stats.required_decisions == 1


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
    assert "Page 1 is shown only as a picture of the page and was not converted." in html
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
    # A page group is not a group of repeated headings (eval reports, ops aggregates).
    assert triage.review_load(result.fixes).required_by_reason == {
        "handwritten_page": 2,
        "scanned_page": 1,
    }
    assert all(e.id in output.crops for e in result.elements if e.page > 0)  # page images
    assert result.image_only_pages == [1, 2, 3]
    html = result.html
    assert "Displacement is the change in position" in html
    assert "Pages 2, 3 and 4 are shown only as pictures of the pages" in html
    assert html.count("Not converted.") == 3
    assert result_outcome(result) == ("done", "pages_not_converted")


def test_handwriting_on_a_typed_page_is_its_own_decision():
    result = convert("typed_with_ink.pdf").result
    (ink,) = [f for f in result.fixes if f.kind is Kind.FIGURE]
    assert ink.reason == "handwriting_on_page" and ink.review == "required"
    assert ink.calls == []  # never sent to the figure model
    assert "Handwriting on page 1, not converted to text." in result.html
    assert "Displacement is the change in position" in result.html


def test_a_short_note_under_a_title_with_a_hidden_layer_is_kept_never_described():
    # The routing change for a few curves under a title (diagram slides) must not send a
    # note with a recognition layer over it to the figure model.
    result = convert("note_titled_layer.pdf").result
    assert not [e for e in result.elements if e.kind in TEXT_KINDS]
    (fix,) = result.fixes
    assert fix.reason == "handwritten_page" and fix.review == "required" and fix.calls == []
    assert result.stats.model_calls == 0
    assert result_outcome(result) == ("failed", "handwritten")


def test_notes_written_in_single_curves_under_a_hidden_layer_are_never_described():
    # Too simple for the stroke test, but under a recognition layer and with no typed
    # text: handwriting, kept as page images, not drawings for the figure model.
    result = convert("ios_single_curves.pdf").result
    assert [f.reason for f in result.fixes] == ["handwritten_page"] * 2
    assert result.stats.model_calls == 0 and result.image_only_pages == [0, 1]
    assert result_outcome(result) == ("failed", "handwritten")


def test_the_safety_net_page_is_kept_with_a_decision():
    result = convert("drawn_page.pdf").result
    (kept,) = [f for f in result.fixes if f.reason == "unread_page"]
    assert kept.review == "required" and kept.status is Status.FAILED
    assert 'alt="Page 2: not converted to text."' in result.html


def test_hidden_text_never_reaches_the_student_file():
    html = convert("typed_hidden.pdf").result.html
    assert not any(phrase in html for phrase in HIDDEN)
    assert "Momentum and energy" in html


SECRET_WORDS = ("Student 2291", "Jane Roe", "accommodation")


def test_fake_redactions_drawn_with_annotation_tools_never_reach_the_student_file():
    result = convert("annot_redactions.pdf").result
    text = text_of(result.html)
    # Five pages each with the line; only the yellow highlight's (page 5) shows.
    assert all(text.count(word) == 1 for word in SECRET_WORDS)
    assert text.count("state the units of every quantity") == 5
    doc = layout.analyze(data("annot_redactions.pdf"))
    shown = {e.page for e in doc.elements if "Jane Roe" in (e.text or "")}
    assert shown == {4}
    hidden = [(n.code, n.page) for n in doc.notices]
    assert hidden == [("hidden_text_ignored", page) for page in range(4)]


def test_a_comment_icon_or_a_file_icon_over_a_word_hides_nothing():
    # A reader opens or moves a sticky note; it is not drawn to hide the words under it.
    line = "Momentum is conserved when no external force acts on the carts."
    doc = pymupdf.open()
    for note in ("comment", "file"):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), line, fontsize=11)
        page.insert_text((72, 130), "A second typed line, so the page is typed.", fontsize=11)
        at = page.search_for("external")[0].tl + pymupdf.Point(2, 0)
        if note == "comment":
            page.add_text_annot(at, "Say why.")
        else:
            page.add_file_annot(at, b"notes", "notes.txt")
    out = layout.analyze(doc.tobytes())
    for n in (0, 1):
        assert out.page_types[n].text.covered_chars == 0
        assert line in " ".join(e.text or "" for e in out.elements if e.page == n)


def test_text_under_a_clip_a_shading_or_a_thick_stroke_never_reaches_the_student_file():
    text = text_of(convert("painted_over.pdf").result.html)
    assert not any(word in text for word in SECRET_WORDS)
    assert text.count("A ball is thrown upward") == 3


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
        ("ios_notes.pdf", "is handwritten, so this tool cannot convert it. Its hidden text"),
        ("note_titled_layer.pdf", "is handwritten, so this tool cannot convert it."),
        ("ios_single_curves.pdf", "is handwritten, so this tool cannot convert it. Its hidden"),
        ("ink_titled_pages.pdf", "cannot convert it. It has no text this tool can use"),
        ("ink_no_layer.pdf", "cannot convert it. It has no text this tool can use"),
        ("scan_ocr.pdf", "running OCR again will not help"),
        ("scan_text_under_image.pdf", "is scanned, so this tool cannot convert it"),
    ],
)  # fmt: skip
def test_a_pdf_of_only_handwritten_or_scanned_pages_is_refused_before_any_quota(env, name, says):
    response = upload(env.browser, data(name), filename=name)
    assert response.status_code == 422
    text = text_of(response.text)
    assert says in text
    assert "no pages were taken from your daily allowance" in text
    assert "yet" not in text.split("cannot convert it")[1][:5]  # no promise of later
    # The error summary links one sentence to the field, which says the rest once.
    found = re.search(r'<div class="error-summary".*?</ul>', response.text, re.S)
    assert found and "so this tool cannot convert it." in text_of(found.group(0))
    assert "daily allowance" not in found.group(0)
    assert text.count("no pages were taken from your daily allowance") == 1
    with session_scope(env.ctx.factory) as session:
        assert session.scalar(select(Job.id)) is None
    assert pages_used(env) == 0


@pytest.mark.parametrize(
    "name", ["diagram_slides.pdf", "timing_diagram.pdf", "vector_with_layer.pdf"]
)
def test_pdfs_of_machine_drawn_diagrams_are_not_refused(env, name):
    response = upload(env.browser, data(name), filename=name)
    assert response.status_code == 303, text_of(response.text)[:300]
    job_id = job_id_from(response)
    assert _worker(env).run_once() == job_id
    assert env.job(job_id).status == "done"


def test_a_fake_redaction_never_reaches_the_job_through_the_app(env):
    job_id = job_id_from(upload(env.browser, data("annot_redactions.pdf"), filename="r.pdf"))
    assert _worker(env).run_once() == job_id
    html = text_of(env.job(job_id).html or "")
    assert html.count("Jane Roe") == 1  # the yellow highlight's line, nobody else's


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


def _post(env, job_id: str, key: str, action: str, view: str, **fields: str):
    page = env.browser.get(f"/jobs/{job_id}/review" + ("/list" if view == "list" else ""))
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    version = re.search(r'name="version" value="(\d+)"', page.text)
    assert token and version
    return env.browser.post(
        f"/jobs/{job_id}/review/{key}",
        data={"csrf_token": token.group(1), "action": action, "view": view,
              "version": version.group(1), **fields},
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


def test_a_kept_page_stays_a_marked_image_after_its_group_decision(env):
    job_id = job_id_from(upload(env.browser, data("mixed_course.pdf"), filename="course.pdf"))
    _worker(env).run_once()
    assert _post(env, job_id, "pg-p2-e0", "reject", "list").status_code == 303
    page = env.browser.get(f"/jobs/{job_id}/review/list").text
    # Under "Your decisions" each page says what it is now; no alt text, no Decorative.
    assert 'name="decorative"' not in page and 'name="alt"' not in page
    listing = text_of(page)
    assert "Page 2: Kept by you" in listing and "Page 2, page 2" not in listing
    assert "Kept as an image, marked “Not converted”" in listing
    # The review screen shows the page with its group, never a figure form.
    split = env.browser.get(f"/jobs/{job_id}/review?item=p3-e0").text
    assert 'name="decorative"' not in split and 'name="alt"' not in split
    for action, fields in (
        ("edit", {"alt": "", "decorative": "on"}),
        ("edit", {"alt": "Handwritten notes on kinematics."}),
        ("accept", {}),
    ):
        refused = _post(env, job_id, "p3-e0", action, "list", **fields)
        assert refused.status_code == 422
        assert "nothing else to choose" in text_of(refused.text)
        assert 'href="#actions-p3-e0"' in refused.text  # the error summary's link
    html = env.job(job_id).html or ""
    assert html.count('data-reason="handwritten_page"') == 2
    assert 'class="decorative"' not in html
    assert "Pages 2, 3 and 4 are shown only as pictures of the pages" in html


def test_kept_pages_are_named_as_pages_not_repeated_figures(env):
    job_id = job_id_from(upload(env.browser, data("mixed_course.pdf"), filename="course.pdf"))
    _worker(env).run_once()
    page = text_of(env.browser.get(f"/jobs/{job_id}").text)
    assert "repeated figures" not in page and "repeated headings" not in page
    assert "2 need your decision: the handwritten pages and the scanned page" in page
    assert (
        "The 3 waiting make 2 decisions: pages kept as images for the same reason are "
        "decided together. 3 pages are shown as images of the original."
    ) in page
    assert "Figures 0" in page and "Pages kept as images 3" in page
    listing = text_of(env.browser.get(f"/jobs/{job_id}/review/list").text)
    assert "repeated figures" not in listing and "Figures (" not in listing
    assert "2 decisions are still needed: the handwritten pages and the scanned page." in listing
    assert "Pages kept as images (2 decisions)" in listing
    assert re.search(r"Pages kept as images\s*:\s*3 not converted", listing)


def test_the_job_page_and_the_review_screen_agree_on_the_finish_line(env):
    job_id = job_id_from(upload(env.browser, data("mixed_course.pdf"), filename="course.pdf"))
    _worker(env).run_once()
    _post(env, job_id, "pg-p2-e0", "reject", "split")
    _post(env, job_id, "pg-p4-e0", "reject", "split")
    job = env.browser.get(f"/jobs/{job_id}").text
    review = env.browser.get(f"/jobs/{job_id}/review").text
    ready = re.search(r'<h2 id="ready-heading">([^<]*)', job)
    title = re.search(r'id="decision-title"[^>]*>([^<]*)', review)
    assert ready and title
    assert ready.group(1).strip() == title.group(1).strip() != "Ready to share"


def test_kept_pages_are_listed_once_each_with_one_decide_link_per_decision(env):
    job_id = job_id_from(upload(env.browser, data("mixed_course.pdf"), filename="course.pdf"))
    _worker(env).run_once()
    html = env.browser.get(f"/jobs/{job_id}").text
    start = html.index('id="not-handled-heading"')
    listed = html[start : html.index("</ul>", start)]
    assert listed.count("<li>") == 3  # pages 2, 3 and 4: the hidden text on its page's line
    assert listed.count(">Decide it<") == 2  # two decisions: handwritten, scanned
    assert "It is decided together with page 2." in text_of(listed)
    split = env.browser.get(f"/jobs/{job_id}/review").text
    strip = text_of(split[split.index('class="page-chip') - 200 :])
    for n in (2, 3, 4):  # each page of a waiting group says so
        assert f"Page {n} , 1 to decide" in strip or f"Page {n}, 1 to decide" in strip


def test_handwriting_kept_on_a_typed_page_is_named_as_handwriting(env):
    job_id = job_id_from(upload(env.browser, data("typed_with_ink.pdf"), filename="t.pdf"))
    _worker(env).run_once()
    page = text_of(env.browser.get(f"/jobs/{job_id}").text)
    assert "Finished, with handwriting not converted" in page
    assert "pages not converted" not in page


def test_a_page_kept_by_the_safety_net_is_not_told_to_be_typed_up(env):
    job_id = job_id_from(upload(env.browser, data("ink_titled.pdf"), filename="notes.pdf"))
    _worker(env).run_once()
    split = text_of(env.browser.get(f"/jobs/{job_id}/review").text)
    assert "Pages 1 and 2 have drawings or writing that this tool could not read." in split
    assert "describe it in your source file" in split and "type them up" not in split
    page = text_of(env.browser.get(f"/jobs/{job_id}").text)
    assert "Pages 1 and 2 have drawings or writing this tool could not read" in page
    html = env.job(job_id).html or ""
    assert "Lecture 14 notes" in html and html.count('data-reason="unread_page"') == 2
