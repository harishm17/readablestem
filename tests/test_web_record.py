"""The review record (T3-6): the page and the CSV list what was decided, by whom and when,
what was not converted and what was not checked; owner only; never called a certification."""

from __future__ import annotations

import csv
import io
import re
from html import unescape

import pytest

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, token
from readablestem.web import record as rec


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
    data = {
        "csrf_token": token(env.browser, f"/jobs/{env.job_id}/review"),
        "acknowledge": "on",
    } | fields
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
    assert re.search(r"\w+ \d{1,2}, 20\d\d, \d\d:\d\d UTC", words)  # when
    assert "Equation 1" in words


# --------------------------------------------------------------------------- the CSV


def test_a_cell_that_would_run_as_a_formula_is_quoted():
    assert rec.safe_cell("=1+1") == "'=1+1"
    assert rec.safe_cell("+cmd") == "'+cmd" and rec.safe_cell("@x") == "'@x"
    assert rec.safe_cell("-1") == "'-1" and rec.safe_cell("\tx") == "'\tx"
    assert rec.safe_cell("Equation 3") == "Equation 3" and rec.safe_cell(7) == "7"


def test_the_owner_is_named_once_and_a_reader_gets_no_address_no_actor_and_no_links(web):
    decide(web, "p1-e2", action="accept")
    decide(web, "p2-e2", action="reject")
    owner = web.browser
    owner.post(
        f"/jobs/{web.job_id}/share",
        data={
            "csrf_token": token(owner, f"/jobs/{web.job_id}/share"),
            "email": BOB,
            "role": "view",
        },
    )
    bob = web.client()
    login(bob, BOB)
    mine = owner.get(f"/jobs/{web.job_id}/record").text
    # The page's own header names the signed-in person; the record names them once more.
    assert mine.split("<h1>Review record</h1>")[1].count(ALICE) == 1
    assert "/review?item=p1-e2" in mine  # an item is a link to its card, for who may open it
    csv_owner = owner.get(f"/jobs/{web.job_id}/record.csv").text
    assert "by" in table(csv_owner)[0] and csv_owner.count(ALICE) == 1

    theirs = bob.get(f"/jobs/{web.job_id}/record")
    assert ALICE not in theirs.text and "Equation 1" in text_of(theirs.text)
    assert "/review?item=" not in theirs.text  # a reader would be refused the review screen
    csv_reader = bob.get(f"/jobs/{web.job_id}/record.csv").text
    assert "by" not in table(csv_reader)[0] and ALICE not in csv_reader


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
