"""Where a redirect points is decided by the server's own rows, never by what the request
carried (CodeQL `py/url-redirection`): the id in a Location is the owned job's, and a view,
filter or item the form names can choose among the fixed destinations but is not copied
into the address."""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from fixtures.web.support import BOB, token
from readablestem.web.db import Job, session_scope
from test_web_review_bulk_accept import EQUATION_CANDIDATES, scaled, version  # noqa: F401

CRAFTED = "//evil.example/\\evil?x=1#y"


def where(response) -> str:
    assert response.status_code == 303, response.text[:200]
    return response.headers["location"]


def on_site(location: str, job_id: str) -> None:
    """A path on this site, inside the job's own pages: no scheme, no host."""
    parts = urlsplit(location)
    assert not parts.scheme and not parts.netloc
    assert location.startswith(f"/jobs/{job_id}")
    assert "evil" not in location


def form(env, **fields) -> dict[str, str]:
    return {"csrf_token": token(env.browser, f"/jobs/{env.job_id}/review")} | fields


@pytest.mark.parametrize("view", ["list", "split", CRAFTED])
def test_a_decision_redirects_inside_the_job_whatever_the_form_says(scaled, view):  # noqa: F811
    response = scaled.browser.post(
        f"/jobs/{scaled.job_id}/review/{EQUATION_CANDIDATES[0]}",
        data=form(scaled, action="accept", view=view, flt=CRAFTED),
    )
    on_site(where(response), scaled.job_id)


@pytest.mark.parametrize("view", ["list", "split"])
def test_a_group_and_a_bulk_accept_redirect_inside_the_job(scaled, view):  # noqa: F811
    group = scaled.browser.post(
        f"/jobs/{scaled.job_id}/review/hg-p1-e3",
        data=form(scaled, action="accept", view=view, flt=CRAFTED),
    )
    on_site(where(group), scaled.job_id)
    bulk = scaled.browser.post(
        f"/jobs/{scaled.job_id}/accept/equations",
        data=form(scaled, view=CRAFTED if view == "list" else view, version=version(scaled)),
    )
    on_site(where(bulk), scaled.job_id)


@pytest.mark.parametrize("path", ["title", "contact", "keep", "language"])
def test_the_job_page_redirect_is_the_owned_jobs_address(scaled, path):  # noqa: F811
    data = form(scaled, title="A new title", contact="", lang="en")
    response = scaled.browser.post(f"/jobs/{scaled.job_id}/{path}", data=data)
    assert where(response) == f"/jobs/{scaled.job_id}"


def test_undo_with_a_crafted_view_goes_to_the_jobs_review(scaled):  # noqa: F811
    decided = scaled.browser.post(
        f"/jobs/{scaled.job_id}/review/{EQUATION_CANDIDATES[0]}",
        data=form(scaled, action="accept", view="list"),
    )
    assert decided.status_code == 303
    for view in ("list", "split", CRAFTED):
        data = form(scaled, view=view, flt=CRAFTED, version=version(scaled))
        on_site(where(scaled.browser.post(f"/jobs/{scaled.job_id}/undo", data=data)), scaled.job_id)
    stale = form(scaled, view=CRAFTED, flt=CRAFTED, version="0")
    on_site(where(scaled.browser.post(f"/jobs/{scaled.job_id}/undo", data=stale)), scaled.job_id)


def test_a_job_id_that_is_not_the_stored_one_gets_no_redirect(scaled):  # noqa: F811
    """Another spelling of the id (a trailing newline is accepted by the id pattern) finds no
    row, so nothing is redirected to: it is a 404, not a Location built from the path."""
    response = scaled.browser.post(
        f"/jobs/{scaled.job_id}%0A/keep", data=form(scaled), follow_redirects=False
    )
    assert response.status_code == 404
    assert "location" not in response.headers


def test_someone_elses_job_gets_no_redirect(scaled):  # noqa: F811
    scaled.allow(BOB)
    from fixtures.web.support import login

    login(scaled.browser, BOB)
    response = scaled.browser.post(
        f"/jobs/{scaled.job_id}/review/{EQUATION_CANDIDATES[0]}",
        data={"csrf_token": token(scaled.browser, "/app"), "action": "accept", "view": "list"},
    )
    assert response.status_code == 404
    assert "location" not in response.headers
    with session_scope(scaled.ctx.factory) as session:
        assert session.get(Job, scaled.job_id) is not None
