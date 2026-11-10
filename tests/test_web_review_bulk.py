"""Review screen at document scale: grouping and order, stable numbering, where the
reviewer lands after a decision, and that there is no bulk accept.

Uses `rich_output()` from test_web_review.py: three pages with every status in every
group, stored in scrambled order.
"""

from __future__ import annotations

import logging

import pytest

from fixtures.web.support import ALICE, WebEnv, job_id_from, login, make_pdf, token
from fixtures.web.support import upload as upload_pdf
from readablestem.core.types import Check
from readablestem.web import review
from test_web_review import edits, install_fake_pipeline, result, rich_output


@pytest.fixture
def rich(tmp_path, monkeypatch):
    fake = install_fake_pipeline(monkeypatch)
    env = WebEnv(tmp_path)
    env.converter.output = rich_output()
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        env.pipeline = fake
        env.job_id = job_id_from(upload_pdf(client, make_pdf(3)))
        env.run_worker()
        yield env


def page_of(env) -> str:
    response = env.browser.get(f"/jobs/{env.job_id}/review/list")
    assert response.status_code == 200
    return response.text


def post(env, target: str, **fields):
    data = {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review"), "view": "list"} | fields
    return env.browser.post(f"/jobs/{env.job_id}/review/{target}", data=data)


def statuses(env) -> dict[str, str]:
    return {f.element_id: f.status.value for f in result(env).fixes}


def group(page: str, slug: str) -> str:
    start = page.index(f'<section id="group-{slug}"')
    return page[start : page.index("</section>", start)]


# --------------------------------------------------------------------------- order (rules)


# --------------------------------------------------------------------------- order (page)


# --------------------------------------------------------------------------- landing


# --------------------------------------------------------------------------- no bulk accept


@pytest.mark.parametrize(
    ("slug", "fields"),
    [
        ("headings", {"action": "accept_all"}),
        ("equations", {"action": "accept_confident", "min_confidence": "50"}),
        ("figures", {"action": "accept"}),
    ],
)
def test_a_bulk_post_is_not_found_and_changes_nothing(rich, slug, fields, caplog):
    caplog.set_level(logging.DEBUG)
    before = statuses(rich)
    response = post(rich, slug, **fields)
    assert response.status_code == 404
    assert statuses(rich) == before and edits(rich) == []
    assert rich.pipeline.rendered == []
    assert "review_bulk" not in caplog.text


def test_a_bulk_post_needs_the_csrf_token_first(rich):
    response = rich.browser.post(
        f"/jobs/{rich.job_id}/review/headings",
        data={"csrf_token": "forged", "action": "accept_all"},
    )
    assert response.status_code == 403


def test_a_check_code_shared_by_two_checks_gets_the_right_sentence(rich):
    empty_latex = Check(name="latex.not_empty", passed=False, code="empty")
    empty_alt = Check(name="alt.not_empty", passed=False, code="empty")
    assert "transcription is empty" in review.describe_failed([empty_latex])
    assert "alt text is empty" in review.describe_failed([empty_alt])
    assert review.describe_failed([empty_alt]).endswith("(alt.not_empty: empty)")
