"""CLI wiring for eval pipeline/calibrate/render and demo build (offline)."""

from __future__ import annotations

import json
from typing import ClassVar

import pytest

from readablestem.cli import eval_cli
from readablestem.cli.main import main
from readablestem.eval import pipeline_eval, render
from readablestem.models.fake import FakeVisionClient
from readablestem.pipeline import equation_step
from test_eval_support import (
    EQ_A,
    EQ_F,
    EQ_V,
    EQ_X,
    FakeMedia,
    equation,
    fake_transcribe,
    settings,
    write_gold,
)


@pytest.fixture(autouse=True)
def offline_settings(monkeypatch):
    """No .env, no API key: every test here must stay offline."""
    monkeypatch.setattr(eval_cli, "_settings", lambda: settings())


@pytest.fixture
def gold(tmp_path):
    return write_gold(
        tmp_path,
        [
            equation("p-1", 0, EQ_V, "block"),
            equation("p-1", 1, EQ_X, "inline"),
            equation("p-2", 0, EQ_A, "block"),
            equation("p-2", 1, EQ_F, "block"),
        ],
    )


class MediaFactory:
    """Stands in for CachedMedia (same constructor), handing out FakeMedia."""

    built: ClassVar[list[tuple]] = []

    def __init__(self, gold_dir, cache_dir, fetcher, *, paper="Letter") -> None:
        MediaFactory.built.append((gold_dir, cache_dir, paper))
        self.media = FakeMedia()

    def __enter__(self):
        return self.media

    def __exit__(self, *exc):
        return None


@pytest.fixture
def fake_inputs(monkeypatch):
    MediaFactory.built = []
    monkeypatch.setattr(pipeline_eval, "CachedMedia", MediaFactory)
    monkeypatch.setattr(equation_step, "transcribe", fake_transcribe)


def pipeline_args(gold, tmp_path, *extra) -> list[str]:
    return [
        "eval",
        "pipeline",
        "--task",
        "equations",
        "--gold",
        str(gold),
        "--out",
        str(tmp_path / "report.json"),
        "--cache",
        str(tmp_path / "cache"),
        "--date",
        "2026-10-07",
        *extra,
    ]


def test_real_runs_require_a_spend_cap(gold, tmp_path, fake_inputs, capsys):
    assert main(pipeline_args(gold, tmp_path)) == 2
    assert "--max-usd is required" in capsys.readouterr().err
    assert not (tmp_path / "report.json").exists()


def test_max_usd_builds_the_openai_client_with_the_cap(gold, tmp_path, fake_inputs, monkeypatch):
    built = []

    class StubOpenAIVisionClient(FakeVisionClient):
        def __init__(self, settings, budget, *, client=None, verifier=None):
            super().__init__(budget=budget)
            built.append(budget)
            verifiers.append(verifier)

    verifiers: list = []

    from readablestem.cli import eval_cli
    from readablestem.config import Settings
    from readablestem.models import openai_client

    monkeypatch.setattr(openai_client, "OpenAIVisionClient", StubOpenAIVisionClient)
    monkeypatch.setattr(
        eval_cli, "_settings", lambda: Settings(_env_file=None, openai_api_key="sk-test")
    )
    assert main(pipeline_args(gold, tmp_path, "--max-usd", "1.5", "--max-calls", "7")) == 0
    (budget,) = built
    assert (budget.max_usd, budget.max_calls, budget.guard.cap_usd) == (1.5, 7, 1.5)
    assert json.loads((tmp_path / "report.json").read_text())["client"] == "openai"
    # The second automated check rides on the same client (and so the same budget).
    from readablestem.models.verifier import DecisionsVerifier

    (verifier,) = verifiers
    assert isinstance(verifier, DecisionsVerifier)


# ---------------------------------------------------------------------- calibrate


def sample_args(gold, tmp_path, *extra) -> list[str]:
    return [
        "eval",
        "sample",
        "--task",
        "equations",
        "--gold",
        str(gold),
        "--cache",
        str(tmp_path / "cache"),
        "--fake",
        *extra,
    ]


def test_weekly_sample_scores_a_seeded_share_of_the_gold_set(gold, tmp_path, fake_inputs, capsys):
    out = tmp_path / "w41.json"
    args = sample_args(gold, tmp_path, "--fraction", "0.5", "--week", "2026-W41", "--out", str(out))
    assert main(args) == 0
    assert "weekly sample 2026-W41: 2 of 4 equations units" in capsys.readouterr().out
    report = json.loads(out.read_text())
    sample = report["sample"]
    assert (sample["units_sampled"], sample["units_total"], sample["seed"]) == (2, 4, "2026-W41")
    assert report["n"] == 2 and report["complete"]
    assert "sample 2026-W41" in report["dataset"]["gold_dir"]
    assert main(args) == 0  # the same week draws the same items
    assert json.loads(out.read_text())["sample"]["ids"] == sample["ids"]


def test_promote_copies_only_complete_real_and_current_reports(tmp_path, capsys):
    from readablestem.eval.gate import code_versions

    def write(name: str, **changes) -> str:
        data = {
            "task": "equations",
            "complete": True,
            "client": "openai",
            "prompt_versions": {"equation": code_versions()["equation"]},
            "models": {"main": "gpt-6.1-sol", "cheap": "gpt-6-luna"},
        } | changes
        path = tmp_path / name
        path.write_text(json.dumps(data))
        return str(path)

    target = ["--out-dir", str(tmp_path / "data")]
    assert main(["eval", "promote", write("ok.json"), "--as", "latest", *target]) == 0
    assert (tmp_path / "data" / "latest-equations.json").is_file()
    assert main(["eval", "promote", write("fake.json", client="fake"), "--as", "baseline"]) == 2
    assert main(["eval", "promote", write("part.json", complete=False), "--as", "baseline"]) == 2
    stale = write("stale.json", prompt_versions={"equation": "equation-00000000"})
    capsys.readouterr()
    assert main(["eval", "promote", stale, "--as", "latest", *target]) == 2
    assert "equation prompt equation-00000000" in capsys.readouterr().err
    assert main(["eval", "promote", stale, "--as", "baseline", *target]) == 0  # history is fine


# ------------------------------------------------------------------------- render


def page_source() -> render.PageSource:
    return render.PageSource(
        book_slug="physics",
        book_title="Physics",
        page_slug="2-2-speed-and-velocity",
        page_title="2.2 Speed and Velocity",
        page_url="https://openstax.org/books/physics/pages/2-2-speed-and-velocity",
        content_url="https://openstax.org/apps/archive/x/contents/b@v:p.json",
        license_name="Creative Commons Attribution License",
        license_url="http://creativecommons.org/licenses/by/4.0/",
        xhtml="<html/>",
        authors=["Ada Author"],
    )


# --------------------------------------------------------------------------- demo


# ------------------------------------------------------------ the Anthropic provider


def test_an_unpriced_model_is_an_error_not_a_traceback(gold, tmp_path, fake_inputs, capsys):
    args = pipeline_args(
        gold, tmp_path, "--provider", "anthropic", "--main-model", "gpt-6", "--fake"
    )
    assert main(args) == 2
    err = capsys.readouterr().err
    assert "invalid model settings" in err and "no price for ANTHROPIC_MAIN_MODEL" in err
