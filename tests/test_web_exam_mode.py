"""Pipeline hooks in the web app (tier 1 pipeline items on the web side).

- "Exam or quiz: describe figures without giving answers" on the upload form: stored on the
  job (`Job.exam_mode`) and passed to the conversion as `ConversionOptions.exam_mode`
  (`web.notation.conversion_options`), with or without a course's notation.
- The reviewer's sentence for the note `equation.single_reading` / `single_reading`.
- What the review screen says screen readers hear for an equation kept as an image.
"""

from __future__ import annotations

import re
from html import unescape

import pytest

from fixtures.web.support import ALICE, WebEnv, job_id_from, login, make_pdf, upload
from readablestem.core.types import ConversionOptions


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


@pytest.fixture
def web(tmp_path):
    env = WebEnv(tmp_path)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


@pytest.fixture
def with_notation(tmp_path):
    env = WebEnv(tmp_path, feature_notation_memory=True)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        env.browser = client
        yield env


# --------------------------------------------------------------------------- the box


def test_a_ticked_box_is_stored_and_the_conversion_runs_in_exam_mode(web):
    job_id = job_id_from(upload(web.browser, make_pdf(1), exam=True))
    assert web.job(job_id).exam_mode is True
    web.run_worker()
    options = web.converter.calls[-1]["options"]
    assert isinstance(options, ConversionOptions)
    assert options.exam_mode is True
    assert options.notation == []  # no course: the exam choice alone
    # The deployment's flags still apply (from_settings), as for a course's options.
    assert options.tables == web.settings.feature_tables


# --------------------------------------------------------------------------- the note


# --------------------------------------------------------------------------- the reject line
