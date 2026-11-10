"""Who judges: the figure judge and the invented-content judge on OpenAI or Claude (offline).

The Claude client is the real `AnthropicVisionClient` on a scripted transport where the
request on the wire matters; elsewhere a stub records how the judge was built.
"""

from __future__ import annotations

import pytest

from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.eval import judge, pipeline_eval
from readablestem.models import anthropic_client
from readablestem.models.fake import FakeVisionClient
from test_eval_support import (
    FakeMedia,
    figure,
    settings,
    write_gold,
)

OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"
VERDICT = '{"conveys_key_information": true, "factual_error": false, "score": 4, "confidence": 0.8}'


def claude_settings(**kw):
    return settings(model_provider="anthropic", anthropic_api_key="sk-ant-test", **kw)


def budget(cap: float = 5.0) -> JobBudget:
    return JobBudget(InMemorySpendGuard(cap_usd=cap), max_usd=cap, max_calls=100)


# ------------------------------------------------------------------ the settings


def test_claude_run_is_judged_by_opus_whatever_its_own_main_model_is():
    for main_model in (OPUS, SONNET):
        run = judge.judge_settings(
            settings(
                model_provider="anthropic",
                anthropic_api_key="sk-ant-test",
                anthropic_main_model=main_model,
                anthropic_cheap_model=SONNET if main_model == OPUS else "claude-haiku-5-5",
            )
        )
        assert (run.model_provider, run.main_model) == ("anthropic", OPUS)
        # the judge never changes model inside a call
        assert run.anthropic_refusal_fallback is False


def test_a_model_without_a_price_is_refused_by_the_settings():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="no price for ANTHROPIC_MAIN_MODEL"):
        judge.judge_settings(settings(), "anthropic", "claude-unknown-9")


def test_the_claude_judge_gets_its_own_client_on_the_runs_budget(monkeypatch):
    built = []

    class Recorder(FakeVisionClient):
        def __init__(self, used, spend, **kw):
            super().__init__(budget=spend)
            built.append((used, spend))

    monkeypatch.setattr(anthropic_client, "AnthropicVisionClient", Recorder)
    spend = budget()
    run_client = FakeVisionClient()
    chosen = judge.make_judge(claude_settings(), spend, run_client=run_client)
    assert chosen.client is not run_client
    ((used, shared),) = built
    assert shared is spend  # one cap for the run and its judge
    assert (used.main_model, used.anthropic_refusal_fallback) == (OPUS, False)


# ------------------------------------------------------------------ on the wire


# ------------------------------------------------------------------ the figures eval


def config(gold, **kw) -> pipeline_eval.EvalConfig:
    return pipeline_eval.EvalConfig(
        task="figures",
        settings=kw.pop("run_settings", settings()),
        client=kw.pop("client"),
        media=FakeMedia(),
        date="2026-10-13",
        gold_dir=gold,
        client_label="fake",
        gold_label="gold",
        **kw,
    )


# ------------------------------------------------------------------ the CLI


def probes(tmp_path, *extra):
    gold = write_gold(tmp_path, [figure("p", 0, "A ball.", "Fig 1"), figure("p", 1, "A car.", "F")])
    out = tmp_path / "probes.json"
    args = ["eval", "calibrate", "--probes", "--text-only", "--gold", str(gold), "--out", str(out)]
    return [*args, *extra], out


# ------------------------------------------------------------------ the invented-content judge
