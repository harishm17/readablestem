"""T3-5, T3-11, T3-16 with T3-4, T3-6 and the exports: every new edit is on the review record
(who, when, what), marks the HTML file out of date, and every way out of the app that gives
the file (download, view, Canvas zip, Common Cartridge, "download all", the record's own
checks) makes it again first.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from html import unescape

import pytest

from a11y_stem.models.fake import FakeVisionClient
from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, token, upload
from fixtures.web.table_doc import MATHS_LATEX, table_pdf
from test_web_edit_tables import clean_output, set_cell_latex

PLAIN = "p1-e4"
REGION = {"left": "10", "top": "75", "right": "60", "bottom": "90"}  # empty on page 1
WORDS = "Discuss vectors and scalars with your own class."
ALT = "A sketch of a cart on a ramp."
EQUATION_ANSWER = {"is_equation": True, "latex": "x^2 + 1", "confidence": 0.99}


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def with_model(env, answer):
    def factory(settings, budget):
        return FakeVisionClient(budget=budget, defaults={"equation": answer})

    env.ctx.client_factory = factory


@pytest.fixture
def review(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = review_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, review_pdf()))
        env.run_worker()
        with_model(env, EQUATION_ANSWER)
        yield env


@pytest.fixture
def tables(tmp_path):
    env = WebEnv(tmp_path)
    env.converter.output = clean_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.job_id = job_id_from(upload(client, table_pdf()))
        env.run_worker()
        yield env


def version(env) -> str:
    return str(env.job(env.job_id).result_version or 0)


def post(env, path: str, **fields):
    page = f"/jobs/{env.job_id}/review"
    data = {"csrf_token": token(env.browser, page), "version": version(env), "view": "split"}
    response = env.browser.post(f"/jobs/{env.job_id}{path}", data=data | fields)
    assert response.status_code == 303, (path, response.text[:300])
    return response


def make_all_the_edits(env) -> None:
    post(env, f"/edit/{PLAIN}", action="text", text=WORDS)
    post(env, "/edit/p2-e1", action="heading", level="2")
    post(env, "/pages/1/add", kind="picture", alt=ALT, **REGION)
    post(env, "/pages/1/add", kind="equation", **REGION | {"top": "40", "bottom": "60"})
    post(env, "/review/p2-e2", action="reject")
    post(env, "/review/p2-e2", action="describe", description="v equals d over t")
    post(env, "/pages/2/omit")


# --------------------------------------------------------------------------- the record


def record_rows(env) -> list[dict[str, str]]:
    body = env.browser.get(f"/jobs/{env.job_id}/record.csv").content.decode("utf-8")
    return list(csv.DictReader(io.StringIO(body.lstrip("\ufeff"))))


def test_the_record_lists_every_new_kind_of_decision_with_who_and_when(review):
    make_all_the_edits(review)
    rows = [r for r in record_rows(review) if r["section"] == "decision"]
    by_decision = {r["decision"]: r for r in rows}
    wanted = {
        "Changed the words of the text": "Paragraph",
        "Made a heading of a paragraph": None,
        "Added a picture the tool missed, with the person's own alt text": None,
        "Marked an equation the tool missed; the tool read it and a person checked": None,
        "Described the image in words": None,
        "Left the page out of the student file and of the review": "Page 2",
    }
    for decision, item in wanted.items():
        row = by_decision.get(decision)
        assert row is not None, (decision, sorted(by_decision))
        assert row["by"] == ALICE and re.fullmatch(
            r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ", row["when_utc"]
        )
        if item:
            assert row["item"] == item or item in row["item"]
    assert by_decision["Left the page out of the student file and of the review"]["page"] == "2"
    assert (
        by_decision["Added a picture the tool missed, with the person's own alt text"]["tool_had"]
        == "Not found by the tool"
    )
    # What the instructor did by hand is counted, and what the tool did not check is said.
    summary = {r["item"]: r["detail"] for r in record_rows(review) if r["section"] == "summary"}
    assert summary["Pictures added by hand"] == "1" and summary["Equations marked by hand"] == "1"
    assert summary["Images described in words"] == "1"
    assert summary["Pages left out of the student file"] == "2"
    words = text_of(review.browser.get(f"/jobs/{review.job_id}/record").text)
    assert "Page 2 was left out by the person" in words
    assert "described in words by the person" in words
    assert "Changed the words of the text" in words


def test_putting_a_page_back_and_undoing_are_on_the_record_too(review):
    post(review, "/pages/2/omit")
    post(review, "/pages/2/restore")
    post(review, "/undo")
    decisions = [r["decision"] for r in record_rows(review) if r["section"] == "decision"]
    assert decisions[:2] == [
        "Left the page out of the student file and of the review",
        "Put the page back into the student file and the review",
    ]
    assert decisions[2].startswith("Undid")


def test_a_decision_on_an_item_of_a_page_left_out_is_still_named(review):
    post(review, "/review/p2-e2", action="reject")
    post(review, "/pages/2/omit")
    rows = [r for r in record_rows(review) if r["section"] == "decision"]
    assert rows[0]["item"].startswith("Equation") and rows[0]["page"] == "2"


def test_clean_tables_accepted_together_and_a_cell_read_by_the_model_are_marked(tables):
    post(tables, "/tables/accept-clean")
    set_cell_latex(tables, "v = d")
    with_model(tables, {"is_equation": True, "latex": MATHS_LATEX, "confidence": 0.99})
    post(tables, "/tables/p1-e1/read-cell", row="3", col="1")
    rows = [r for r in record_rows(tables) if r["section"] == "decision"]
    decisions = [r["decision"] for r in rows]
    bulk = "Accepted, together with the other clean tables, in one action"
    assert decisions.count(bulk) == 2
    assert "Table cell read by the AI service at the person's request, then saved" in decisions
    assert all(r["by"] == ALICE for r in rows)


def test_carried_decisions_say_what_was_carried():
    from types import SimpleNamespace

    from a11y_stem.web import record

    row = SimpleNamespace(action="carried", after={"carried_edit": "picture"})
    assert record._decision_words(row) == (
        "Carried over from the earlier version: picture added by hand"
    )
    plain = SimpleNamespace(action="carried", after={})
    assert record._decision_words(plain) == "Carried over from the earlier version of the document"


# --------------------------------------------------------------------------- html_stale

EDITS = [
    ("/edit/p1-e4", {"action": "text", "text": WORDS}),
    ("/edit/p2-e1", {"action": "heading", "level": "2"}),
    ("/pages/1/add", {"kind": "picture", "alt": ALT, **REGION}),
    ("/pages/1/add", {"kind": "equation", **REGION}),
    ("/pages/2/omit", {}),
    ("/review/p2-e2", {"action": "reject"}),
    ("/review/p1-e2", {"action": "accept"}),
]


@pytest.mark.parametrize(
    ("path", "fields"), EDITS, ids=[p + str(f.get("kind", f.get("action", ""))) for p, f in EDITS]
)
def test_every_edit_route_marks_the_html_out_of_date_and_renders_nothing(review, path, fields):
    html_before = review.job(review.job_id).html
    post(review, path, **fields)
    job = review.job(review.job_id)
    assert job.html_stale is True and job.html == html_before  # nothing rendered on the click


def test_describe_restore_and_undo_mark_it_too(review):
    post(review, "/review/p2-e2", action="reject")
    post(review, "/pages/2/omit")
    for path, fields in (
        ("/pages/2/restore", {}),
        ("/review/p2-e2", {"action": "describe", "description": "v equals d over t"}),
        ("/undo", {}),
    ):
        with review_session(review) as session:
            session.get_job(review.job_id).html_stale = False
        post(review, path, **fields)
        assert review.job(review.job_id).html_stale is True, path


def test_the_table_shortcuts_mark_it_too(tables):
    for path, fields in (
        ("/tables/accept-clean", {}),
        ("/tables/p1-e1/read-cell", {"row": "3", "col": "1"}),
    ):
        if "read-cell" in path:
            set_cell_latex(tables, "v = d")
            with_model(tables, {"is_equation": True, "latex": MATHS_LATEX, "confidence": 0.99})
        with review_session(tables) as session:
            session.get_job(tables.job_id).html_stale = False
        post(tables, path, **fields)
        assert tables.job(tables.job_id).html_stale is True, path


class review_session:
    """A transaction on the app's database, with `get_job`."""

    def __init__(self, env) -> None:
        from a11y_stem.web.db import session_scope

        self.scope = session_scope(env.ctx.factory)

    def __enter__(self):
        self.session = self.scope.__enter__()
        return self

    def __exit__(self, *exc):
        return self.scope.__exit__(*exc)

    def get_job(self, job_id: str):
        from a11y_stem.web.db import Job

        job = self.session.get(Job, job_id)
        assert job is not None
        return job


# --------------------------------------------------------------------------- the file, fresh


@pytest.fixture
def stale(review):
    """Every decision made (so the document is ready to share) and then the words of a
    paragraph changed: the stored file is out of date and says nothing of the change."""
    post(review, "/review/p1-e2", action="accept")
    post(review, "/review/p1-e6", action="accept")
    post(review, "/review/hg-p1-e3", action="accept")
    assert review.job(review.job_id).html_stale is True
    review.browser.get(f"/jobs/{review.job_id}/view")  # brings the file up to date
    post(review, f"/edit/{PLAIN}", action="text", text=WORDS)
    job = review.job(review.job_id)
    assert job.html_stale is True and "your own class" not in (job.html or "")
    return review


def fresh(env) -> bool:
    return env.job(env.job_id).html_stale is False and "your own class" in (
        env.job(env.job_id).html or ""
    )


def test_the_html_download_and_the_view_make_the_file_again(stale):
    response = stale.browser.get(f"/jobs/{stale.job_id}/download")
    assert "your own class" in response.text and fresh(stale)


def test_the_view_makes_the_file_again(stale):
    assert "your own class" in stale.browser.get(f"/jobs/{stale.job_id}/view").text


def test_the_canvas_zip_makes_the_file_again(stale):
    response = stale.browser.get(f"/jobs/{stale.job_id}/download/canvas")
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert "your own class" in archive.read("index.html").decode()
    assert fresh(stale)


def test_the_cartridge_makes_the_file_again(stale):
    response = stale.browser.get(f"/jobs/{stale.job_id}/download/cartridge")
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    pages = [
        n
        for n in archive.namelist()
        if n.endswith(".html") and "your own class" in archive.read(n).decode()
    ]
    assert pages, archive.namelist()
    assert fresh(stale)


def test_download_all_makes_the_file_again(stale):
    response = stale.browser.get("/app/download")
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    html = [n for n in archive.namelist() if n.endswith(".html")]
    assert len(html) == 1 and "your own class" in archive.read(html[0]).decode()
    assert fresh(stale)


def test_the_record_makes_the_file_again_for_its_checks_and_changes_nothing_else(stale):
    before = stale.job(stale.job_id)
    page = stale.browser.get(f"/jobs/{stale.job_id}/record")
    assert page.status_code == 200
    after = stale.job(stale.job_id)
    assert fresh(stale)
    assert (after.retain_until, after.result_version) == (
        before.retain_until,
        before.result_version,
    )
    assert "Changed the words of the text" in text_of(page.text)


def test_the_job_page_makes_the_file_again(stale):
    assert stale.browser.get(f"/jobs/{stale.job_id}").status_code == 200
    assert fresh(stale)
