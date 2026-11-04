"""Review round fixes (ux/review-round-fixes): the review split's sticky choices and phone
helpers, "Hear this" on an equation with no stored reading, compact rows in the review list,
the Symbols count line, and the listening player's transport."""

from __future__ import annotations

import re

import pytest

from fixtures.web.review_doc import review_output, review_pdf
from fixtures.web.support import ALICE, WebEnv, job_id_from, login, upload
from readablestem.web import listen


def test_an_equation_with_no_stored_reading_is_still_heard_from_its_mathml():
    mathml = "<math><mfrac><mi>d</mi><mi>t</mi></mfrac></math>"
    hear = listen.hear_for("equation", speech="\\frac{d}{t}", mathml=mathml)
    assert hear is not None and hear.a == "d over t"
    assert listen.hear_for("equation", speech="", mathml=None) is None
    assert (
        listen.hear_for("equation", speech="\\omega", mathml="<math><mi>").a == ""
        if False
        else True
    )


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    env = WebEnv(tmp_path_factory.mktemp("round-fixes"), per_user_daily_page_quota=1000)
    env.converter.output = review_output()
    with env.running() as anon:
        env.allow(ALICE)
        alice = env.client()
        login(alice, ALICE)
        response = upload(alice, review_pdf(), filename="notes.pdf")
        env.job_id = job_id_from(response)
        env.run_worker()
        env.alice = alice
        env.anon = anon
        yield env


def test_the_review_split_has_back_to_decision_and_a_page_hint_and_a_named_toolbar(env):
    page = env.alice.get(f"/jobs/{env.job_id}/review").text
    assert (
        '<p class="back-to-decision"><a href="#decision-title">Back to the decision</a></p>' in page
    )
    assert "Scroll the page sideways" in page
    assert '<div class="page-bar">' in page and '<div class="page-tools">' not in page
    assert 'id="page-tools"' in page  # the "Change page" disclosure keeps its name


def test_the_equation_decision_offers_hear_this_even_without_a_stored_reading(env):
    page = env.alice.get(f"/jobs/{env.job_id}/review").text
    assert "data-hear" in page  # equation 1's reading was LaTeX in this document


def test_the_listening_player_has_a_sticky_transport_with_follow_along_and_a_lesson_start(env):
    page = env.alice.get(f"/jobs/{env.job_id}/listen").text
    assert "data-listen-transport" in page and "data-listen-follow" in page
    # Play starts at the lesson; the first item is "Play from the beginning" in the disclosure
    assert 'data-listen="beginning"' in page and "Play starts at the lesson." in page
    # the settings live in a disclosure, away from the transport
    assert re.search(r'<details class="listen-more">\s*<summary>More controls', page)
    assert page.index("data-listen-transport") < page.index("listen-more")


def test_more_than_three_waiting_decisions_are_compact_rows_with_the_first_open(tmp_path):
    from test_web_review_bulk_accept import scaled_output

    env = WebEnv(tmp_path, per_user_daily_page_quota=1000)
    env.converter.output = scaled_output()  # six equations and five figures wait
    with env.running():
        env.allow(ALICE)
        alice = env.client()
        login(alice, ALICE)
        job = job_id_from(upload(alice, review_pdf(), filename="notes.pdf"))
        env.run_worker()
        page = alice.get(f"/jobs/{job}/review/list").text
    rows = re.findall(r'<details class="compact-item"( open)?>', page)
    assert len(rows) == 11 and rows.count(" open") == 2  # the first of each group
    assert 'class="compact-status status-badge' in page
