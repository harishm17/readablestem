"""`GET /jobs/{id}/listen` and `GET /demo/listen`: who can open the listening transcript, what it
says, that LaTeX never reaches it, and that it is not in the file students download."""

from __future__ import annotations

import re

import pytest

from fixtures.web.support import ALICE, BOB, WebEnv, job_id_from, login, make_pdf, upload
from readablestem.web import listen
from readablestem.web.db import Job, session_scope

CAROL = "carol@example.org"

HTML = (
    '<!doctype html><html lang="en"><head><title>Kinematics notes</title></head><body><main>'
    "<h1>Kinematics</h1><p>The speed is "
    '<math id="p1-e2"><mi>v</mi><mo>=</mo><mi>d</mi><mo>/</mo><mi>t</mi></math>.</p>'
    '<div class="equation" id="p1-e3"><math display="block" alttext="\\frac{x}{2}"><mi>x</mi>'
    "<mo>+</mo><mn>1</mn></math></div></main></body></html>"
)


@pytest.fixture
def site(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as owner:
        for email in (ALICE, BOB, CAROL):
            env.allow(email)
        login(owner, ALICE)
        viewer, stranger = env.client(), env.client()
        login(viewer, CAROL)
        login(stranger, BOB)
        env.owner, env.viewer, env.stranger = owner, viewer, stranger
        env.job_id = job_id_from(upload(owner, make_pdf(1)))
        assert env.run_worker() == env.job_id
        with session_scope(env.ctx.factory) as session:
            job = session.get(Job, env.job_id)
            job.html = HTML
            job.html_stale = False
        from test_job_shares import give

        env.who = {"owner": owner}
        assert give(env, CAROL, "view").status_code in {200, 303}
        yield env
        viewer.close()
        stranger.close()


def transcript_text(response) -> str:
    start = response.text.index("data-listen-transcript")
    return response.text[start:]


def test_the_owner_hears_the_converted_page(site):
    listen.CACHE.clear()
    response = site.owner.get(f"/jobs/{site.job_id}/listen")
    assert response.status_code == 200
    body = response.text
    assert "<h1>Listen as a student</h1>" in body
    assert (
        "Roughly what a screen reader says.</strong> NVDA, JAWS and VoiceOver each speak "
        "differently; test with one before you rely on it." in body
    )
    assert "Heading level 1" in body and "Kinematics" in body
    assert "The speed is v equals d over t." in body
    assert "Equation" in body and "x plus 1" in body  # the stored reading of p1-e3
    assert 'data-listen="play"' in body and 'data-listen="stop"' in body
    assert f"/jobs/{site.job_id}/view" in body
    assert re.search(r"/static/listen\.[0-9a-f]{12}\.js", body)
    assert "<script>" not in body  # script-src 'self': no inline script


def test_latex_never_appears_in_the_transcript(site):
    body = transcript_text(site.owner.get(f"/jobs/{site.job_id}/listen"))
    assert "\\frac" not in body and "SECRETLATEX" not in body
    assert not re.search(r"[\\^_]", re.sub(r"data-[a-z-]+=\"[^\"]*\"", "", body.split("</ol>")[0]))


def test_a_person_it_was_shared_with_can_open_it_and_a_stranger_gets_the_404(site):
    path = f"/jobs/{site.job_id}/listen"
    assert site.viewer.get(path).status_code == 200
    stranger = site.stranger.get(path)
    missing = site.stranger.get(f"/jobs/{'0' * 32}/listen")
    assert stranger.status_code == missing.status_code == 404
    assert "Kinematics" not in stranger.text
    assert site.owner.post(path, data={}).status_code in {403, 405}


def test_signed_out_visitors_are_sent_to_sign_in(site):
    with site.client() as anonymous:
        response = anonymous.get(f"/jobs/{site.job_id}/listen")
        assert response.status_code in {303, 401, 403}
        assert "Kinematics" not in response.text


def test_the_transcript_is_kept_per_result_version(site):
    listen.CACHE.clear()
    path = f"/jobs/{site.job_id}/listen"
    site.owner.get(path)
    assert len(listen.CACHE._items) == 1
    site.owner.get(path)
    assert len(listen.CACHE._items) == 1


def test_the_downloaded_file_has_no_transcript(site):
    file = site.owner.get(f"/jobs/{site.job_id}/download").text
    assert "listen" not in file.lower().replace("listening", "")


def test_the_job_page_offers_it_next_to_the_preview(site):
    for client in (site.owner, site.viewer):
        page = client.get(f"/jobs/{site.job_id}").text
        assert f'href="/jobs/{site.job_id}/listen"' in page


def test_the_demo_is_public(site):
    with site.client() as anonymous:
        response = anonymous.get("/demo/listen")
        assert response.status_code == 200
        assert "Equation 2.4: capital delta bold d over capital delta t" in response.text
        assert "\\frac" not in response.text
        assert 'content="noindex"' in response.text
        assert anonymous.post("/demo/listen").status_code in {403, 405}
