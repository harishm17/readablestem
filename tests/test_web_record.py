"""The review record (T3-6): the page and the CSV list what was decided, by whom and when,
what was not converted and what was not checked; owner only; never called a certification."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from html import unescape

import pytest

from a11y_stem.web import record as rec
from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, token


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        env.allow(BOB)
        login(client, ALICE)
        env.browser = client
        env.converter.output = review_output()
        response = client.post(
            "/jobs",
            data={"csrf_token": token(client)},
            files={"pdf": ("secret-notes.pdf", review_pdf(), "application/pdf")},
        )
        env.job_id = job_id_from(response)
        env.run_worker()
        yield env


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def decide(env, target: str, **fields) -> None:
    data = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review")} | fields
    response = env.browser.post(f"/jobs/{env.job_id}/review/{target}", data=data)
    assert response.status_code == 303, response.text


def table(body: str) -> list[dict[str, str]]:
    text = body.lstrip("\ufeff")
    return list(csv.DictReader(io.StringIO(text)))


# --------------------------------------------------------------------------- the page


def test_the_page_says_what_it_is_and_is_not_a_certification(web):
    page = web.browser.get(f"/jobs/{web.job_id}/record")
    assert page.status_code == 200
    words = text_of(page.text)
    assert rec.STATEMENT in words and rec.KEEP_NOTE in words
    assert "certif" not in words.lower() and "complian" not in words.lower()
    assert "<h1>Review record</h1>" in page.text
    assert page.headers["cache-control"] == "no-store"


def test_a_decision_is_listed_with_who_when_and_what_the_tool_had(web):
    decide(web, "p1-e2", action="accept")
    decide(web, "p2-e2", action="reject")
    words = text_of(web.browser.get(f"/jobs/{web.job_id}/record").text)
    assert "Decisions made by a person" in words
    assert "Accepted" in words and "Rejected, original kept" in words
    assert "Asked for a decision" in words  # what the tool had for the first
    assert ALICE in words  # by whom
    assert re.search(r"\d{1,2} \w+ 20\d\d, \d\d:\d\d UTC", words)  # when
    assert "Equation 1" in words


def test_what_is_open_and_what_was_not_checked_are_said(web):
    words = text_of(web.browser.get(f"/jobs/{web.job_id}/record").text)
    assert "Waiting for a decision" in words
    assert "What was not checked" in words
    assert "Nobody has listened to it with NVDA" in words
    assert "were accepted by the tool after its own automatic checks" in words
    assert "decisions were still open" in words


def test_a_part_shown_as_a_picture_is_listed_with_its_page(web):
    decide(web, "p1-e6", action="reject")
    words = text_of(web.browser.get(f"/jobs/{web.job_id}/record").text)
    assert "Parts shown as pictures" in words
    assert re.search(r"Figure \d, page 1", words)


def test_the_job_page_links_to_the_record(web):
    page = web.browser.get(f"/jobs/{web.job_id}").text
    assert f'href="/jobs/{web.job_id}/record"' in page
    assert f'href="/jobs/{web.job_id}/record.csv"' in page
    assert "It is a record, not a verdict." in text_of(page)


# --------------------------------------------------------------------------- the CSV


def test_the_csv_has_the_same_facts(web):
    decide(web, "p1-e2", action="accept")
    response = web.browser.get(f"/jobs/{web.job_id}/record.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="review-record-' in response.headers["content-disposition"]
    assert response.content.startswith(b"\xef\xbb\xbf")  # a byte order mark, for Excel
    rows = table(response.content.decode("utf-8"))
    assert list(rows[0]) == list(rec.CSV_HEADER)
    sections = {r["section"] for r in rows}
    assert {"document", "summary", "decision", "open", "check", "not_checked"} <= sections
    (decision,) = [r for r in rows if r["section"] == "decision"]
    assert decision["by"] == ALICE and decision["decision"] == "Accepted"
    assert decision["tool_had"] == "Asked for a decision" and decision["item"] == "Equation 1"
    when = datetime.strptime(decision["when_utc"], "%Y-%m-%dT%H:%M:%SZ")
    assert when.year >= 2026
    document = {r["item"]: r for r in rows if r["section"] == "document"}
    assert document["File name"]["detail"] == "secret-notes.pdf"
    assert "a11y-stem" in document["Tool"]["detail"]
    assert "certif" not in response.text.lower()


def test_a_cell_that_would_run_as_a_formula_is_quoted():
    assert rec.safe_cell("=1+1") == "'=1+1"
    assert rec.safe_cell("+cmd") == "'+cmd" and rec.safe_cell("@x") == "'@x"
    assert rec.safe_cell("-1") == "'-1" and rec.safe_cell("\tx") == "'\tx"
    assert rec.safe_cell("Equation 3") == "Equation 3" and rec.safe_cell(7) == "7"


# --------------------------------------------------------------------------- who may ask


def test_only_the_owner_gets_a_record(web):
    bob = web.client()
    login(bob, BOB)
    for path in ("record", "record.csv"):
        mine = web.browser.get(f"/jobs/{web.job_id}/{path}")
        stranger = bob.get(f"/jobs/{web.job_id}/{path}")
        missing = bob.get(f"/jobs/{'0' * 32}/{path}")
        assert mine.status_code == 200
        assert stranger.status_code == missing.status_code == 404
        assert stranger.text == missing.text


def test_asking_for_a_record_changes_nothing(web):
    before = web.job(web.job_id)
    web.browser.get(f"/jobs/{web.job_id}/record")
    web.browser.get(f"/jobs/{web.job_id}/record.csv")
    after = web.job(web.job_id)
    assert (after.retain_until, after.result_version) == (
        before.retain_until,
        before.result_version,
    )


def test_the_record_is_not_ready_for_a_job_that_is_still_running(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        response = client.post(
            "/jobs",
            data={"csrf_token": token(client)},
            files={"pdf": ("a.pdf", review_pdf(), "application/pdf")},
        )
        job_id = job_id_from(response)
        assert client.get(f"/jobs/{job_id}/record").status_code == 409


def test_the_vendor_sheet_does_not_claim_certification_or_compliance():
    from pathlib import Path

    sheet = (Path(__file__).parent.parent / "docs" / "vendor-information.md").read_text()
    lowered = sheet.lower()
    assert "certif" not in lowered and "complian" not in lowered
    for heading in ("## What it does", "## What happens to a document", "## Where it falls short"):
        assert heading in sheet
    assert len(sheet.split()) < 900  # one page
