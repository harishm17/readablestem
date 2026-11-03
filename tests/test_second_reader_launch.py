"""The second reader must be able to work before a production start is allowed
(`ops.launch.second_reader_problem`, `Settings.check_production`, launch-check), and its counts
reach the operator's log from the conversion child's result."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from readablestem.config import ConfigError, Settings
from readablestem.core.types import (
    ImagePagesSummary,
    SecondReaderSummary,
)
from readablestem.models.math_readers.local_weights import FILES
from readablestem.observability.logging import FIELDS_ATTR
from readablestem.ops import launch
from test_ops_limits import PROD


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def local(tmp_path: Path, *, files: bool) -> dict:
    if files:
        for name in FILES:
            (tmp_path / name).write_bytes(b"x")
    return {
        "math_reader_local_enabled": True,
        "math_reader_local_dir": tmp_path,
        "image_pages_second_reader": "local",
    }


def test_off_needs_nothing():
    assert launch.second_reader_problem(settings()) is None
    settings(**PROD).check_production()


def test_the_local_reader_needs_its_model_files(tmp_path):
    assert launch.second_reader_problem(settings(**local(tmp_path, files=True))) is None
    problem = launch.second_reader_problem(settings(**local(tmp_path / "none", files=False)))
    assert problem is not None and "model files are not in MATH_READER_LOCAL_DIR" in problem
    unset = settings(math_reader_local_enabled=True, image_pages_second_reader="local")
    assert "MATH_READER_LOCAL_DIR is not set" in str(launch.second_reader_problem(unset))


def test_production_refuses_a_local_reader_without_files_and_accepts_one_with(tmp_path):
    with pytest.raises(ConfigError, match="model files are not in MATH_READER_LOCAL_DIR"):
        settings(**PROD, **local(tmp_path / "none", files=False)).check_production()
    settings(**PROD, **local(tmp_path, files=True)).check_production()


@pytest.mark.parametrize("name", ["claude", "openai"])
def test_production_refuses_a_reader_nothing_can_pass_to_the_child(name):
    s = settings(**PROD, **{f"math_reader_{name}_enabled": True, "image_pages_second_reader": name})
    with pytest.raises(ConfigError, match=f"IMAGE_PAGES_SECOND_READER={name} is not built"):
        s.check_production()
    # elsewhere it is accepted (the CLI and tests pass readers in)
    assert launch.second_reader_problem(s) is not None
    settings(**{f"math_reader_{name}_enabled": True, "image_pages_second_reader": name})


def test_launch_check_reports_it(tmp_path):
    s = settings(**PROD, **local(tmp_path / "none", files=False))
    assert any(
        "model files are not in MATH_READER_LOCAL_DIR" in i
        for i in launch.launch_blockers(settings=s)
    )
    good = settings(**PROD, **local(tmp_path, files=True))
    assert not any("MATH_READER_LOCAL_DIR" in i for i in launch.launch_blockers(settings=good))


def test_a_reader_that_was_not_built_is_logged_and_nothing_is_read(caplog):
    from image_page_support import block, scan_pdf
    from readablestem.core.budget import InMemorySpendGuard, JobBudget
    from readablestem.models.fake import FakeVisionClient
    from readablestem.pipeline import run

    s = settings(
        feature_image_pages=True,
        image_pages_recover_omissions=False,
        math_reader_claude_enabled=True,
        anthropic_api_key="k",
        image_pages_second_reader="claude",
    )
    page = {"blocks": [block("equation", latex="v = d", y0=200, y1=260)]}
    budget = JobBudget(guard=InMemorySpendGuard(cap_usd=5.0), max_usd=5.0, max_calls=100)
    fake = FakeVisionClient(
        responders={
            "image_page": lambda r: page,
            "equation": lambda r: {"is_equation": True, "latex": "v = d", "confidence": 1.0},
        },
        budget=budget,
    )
    with caplog.at_level(logging.WARNING):
        result = run.convert_pdf(scan_pdf(), settings=s, client=fake).result
    assert "second_reader_not_built: claude" in caplog.text
    assert result.image_pages is not None and result.image_pages.second_reader is None
    assert not [n for n in result.notices if n.code.startswith("second_reader")]


def test_the_parent_logs_the_childs_counts_without_content(tmp_path, caplog):
    from fixtures.web.review_doc import review_output, review_pdf
    from fixtures.web.support import ALICE, WebEnv, job_id_from, login, upload

    output = review_output()
    output.result.image_pages = ImagePagesSummary(
        second_reader=SecondReaderSummary(
            reader="local", considered=5, read=4, agreed=2, differed=2, with_choices=1,
            unavailable=1, seconds=7.5,
        )
    )  # fmt: skip
    env = WebEnv(tmp_path)
    env.converter.output = output
    with env.running() as client, caplog.at_level(logging.INFO):
        env.allow(ALICE)
        login(client, ALICE)
        job_id_from(upload(client, review_pdf()))
        env.run_worker()
    [record] = [r for r in caplog.records if r.getMessage() == "second_reader_job"]
    fields = getattr(record, FIELDS_ATTR)
    assert record.levelno == logging.INFO
    assert fields["considered"] == 5 and fields["read"] == 4 and fields["seconds"] == 7.5
    assert set(fields) >= {"job_id", "reader", "agreed", "differed", "unavailable", "over_budget"}
    assert all(isinstance(v, str | int | float) for v in fields.values())  # counts and codes
