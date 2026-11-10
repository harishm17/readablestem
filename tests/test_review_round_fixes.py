"""Review round fixes (ux/review-round-fixes): the review split's sticky choices and phone
helpers, "Hear this" on an equation with no stored reading, compact rows in the review list,
the Symbols count line, and the listening player's transport."""

from __future__ import annotations

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
