"""Who judges: the figure judge and the invented-content judge on OpenAI or Claude (offline).

The Claude client is the real `AnthropicVisionClient` on a scripted transport where the
request on the wire matters; elsewhere a stub records how the judge was built.
"""

from __future__ import annotations

import json

import pytest

from readablestem import config as app_config
from readablestem.cli import eval_cli
from readablestem.cli.main import main
from readablestem.config import ConfigError
from readablestem.core.budget import InMemorySpendGuard, JobBudget
from readablestem.eval import judge, pipeline_eval
from readablestem.models import anthropic_client, openai_client
from readablestem.models.anthropic_client import AnthropicVisionClient
from readablestem.models.fake import FakeVisionClient
from readablestem.models.prompts import load_prompt
from readablestem.pipeline import figure_step
from test_eval_support import (
    FakeMedia,
    fake_describe,
    fake_png,
    figure,
    settings,
    write_gold,
)
from test_models_anthropic_client import Wire, message_body

OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"
VERDICT = '{"conveys_key_information": true, "factual_error": false, "score": 4, "confidence": 0.8}'


def claude_settings(**kw):
    return settings(model_provider="anthropic", anthropic_api_key="sk-ant-test", **kw)


def budget(cap: float = 5.0) -> JobBudget:
    return JobBudget(InMemorySpendGuard(cap_usd=cap), max_usd=cap, max_calls=100)


# ------------------------------------------------------------------ the settings


def test_openai_run_keeps_its_own_main_model_and_client():
    base = settings()
    assert judge.judge_settings(base) is base
    run_client = FakeVisionClient()
    chosen = judge.make_judge(base, budget(), run_client=run_client)
    assert chosen.client is run_client and chosen.describe() == {
        "provider": "openai",
        "model": "gpt-6.1-sol",
    }


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


def test_provider_and_model_can_be_chosen_for_the_judge():
    claude_judge = judge.judge_settings(settings(), "anthropic")
    assert (claude_judge.model_provider, claude_judge.main_model) == ("anthropic", OPUS)
    sonnet = judge.judge_settings(settings(), "anthropic", SONNET)
    assert sonnet.main_model == SONNET
    gpt = judge.judge_settings(claude_settings(), "openai")
    assert (gpt.model_provider, gpt.main_model) == ("openai", "gpt-6.1-sol")
    other = judge.judge_settings(settings(), "openai", "gpt-6-luna")
    assert other.main_model == "gpt-6-luna"


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


def test_a_missing_key_of_the_judge_provider_is_named_before_any_call():
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY is not set"):
        judge.make_judge(settings(), budget(), run_client=FakeVisionClient(), provider="anthropic")
    with pytest.raises(ConfigError, match="OPENAI_API_KEY is not set"):
        judge.make_judge(claude_settings(), budget(), run_client=None, provider="openai")


def test_fake_runs_judge_with_the_fake_and_still_name_the_judge():
    run_client = FakeVisionClient()
    chosen = judge.make_judge(
        claude_settings(), budget(), run_client=run_client, provider="anthropic", fake=True
    )
    assert chosen.client is run_client and chosen.model == OPUS
    alone = judge.make_judge(settings(), budget(), run_client=None, fake=True)
    assert isinstance(alone.client, FakeVisionClient)


# ------------------------------------------------------------------ on the wire


def test_claude_judge_sends_the_same_prompt_and_reads_the_verdict(monkeypatch):
    wire = Wire(message_body(text=VERDICT, model=OPUS))
    real = AnthropicVisionClient

    def with_wire(used, spend, **kw):
        return real(used, spend, http_client=wire.client(), **kw)

    monkeypatch.setattr(anthropic_client, "AnthropicVisionClient", with_wire)
    chosen = judge.make_judge(
        claude_settings(), budget(), run_client=FakeVisionClient(), provider="anthropic"
    )
    judgement = judge.judge_alt(
        "A ball on a ramp.",
        "A ball rolls down a ramp.",
        "Figure 1.1",
        client=chosen.client,
        settings=chosen.settings,
        image=fake_png("x"),
    )
    assert judgement.passed is True and judgement.call.model == OPUS
    assert judgement.call.cost_usd > 0
    (request,) = wire.requests
    assert request.url.path == "/v1/messages"  # the plain endpoint: no fallback beta
    assert "anthropic-beta" not in request.headers
    body = wire.body()
    assert body["model"] == OPUS and "fallbacks" not in body
    # the prompt is the one the OpenAI judge gets, unchanged
    assert body["system"][0]["text"] == load_prompt("judge_figure").text
    contents = body["messages"][0]["content"]
    assert contents[0]["type"] == "image"
    assert "<candidate>\nA ball on a ramp.\n</candidate>" in contents[1]["text"]
    assert body["output_config"]["format"]["type"] == "json_schema"


# ------------------------------------------------------------------ the figures eval


def figures_gold(tmp_path):
    return write_gold(tmp_path, [figure("p-1", 0, "A ball on a ramp.", "Figure 1.1 A ball.")])


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


def ok_judge(request):
    return {"conveys_key_information": True, "factual_error": False, "score": 5, "confidence": 0.9}


def test_the_figures_eval_judges_with_the_judge_client_and_records_it(monkeypatch, tmp_path):
    gold = figures_gold(tmp_path)
    url = "https://openstax.org/apps/archive/20990101.000000/resources/p-1-0"
    by_image = {("figure", pipeline_eval_key(fake_png(url))): {"alt": "A ramp.", "confidence": 0.9}}
    run_client = FakeVisionClient(by_image=by_image)
    judge_client = FakeVisionClient(responders={"judge": ok_judge})
    monkeypatch.setattr(figure_step, "describe", fake_describe)
    setup = judge.JudgeSetup(judge_client, judge.judge_settings(settings(), "anthropic"))
    result = pipeline_eval.run_pipeline_eval(config(gold, client=run_client, judge=setup))
    assert [r.step for r in judge_client.requests] == ["judge"]
    assert judge_client.requests[0].model == OPUS
    assert "judge" not in {r.step for r in run_client.requests}
    report = result.report
    assert report["models"]["judge"] == OPUS
    assert report["judge_client"] == {"provider": "anthropic", "model": OPUS}
    assert report["metrics"]["judge_pass_rate"] == 1.0


def test_without_a_chosen_judge_the_run_judges_itself_as_before(monkeypatch, tmp_path):
    gold = figures_gold(tmp_path)
    url = "https://openstax.org/apps/archive/20990101.000000/resources/p-1-0"
    by_image = {("figure", pipeline_eval_key(fake_png(url))): {"alt": "A ramp.", "confidence": 0.9}}
    run_client = FakeVisionClient(by_image=by_image, responders={"judge": ok_judge})
    monkeypatch.setattr(figure_step, "describe", fake_describe)
    report = pipeline_eval.run_pipeline_eval(config(gold, client=run_client)).report
    assert [r.step for r in run_client.requests] == ["figure", "judge"]
    assert report["models"]["judge"] == "gpt-6.1-sol" and "judge_client" not in report


def pipeline_eval_key(png: bytes) -> str:
    from readablestem.models.fake import image_key

    return image_key(png)


# ------------------------------------------------------------------ the CLI


@pytest.fixture
def cli_claude(monkeypatch):
    """Keys for both providers; every client built is a recording fake."""
    built: list[tuple[str, object, object]] = []

    def stub(name):
        class Recorder(FakeVisionClient):
            def __init__(self, used, spend, **kw):
                super().__init__(budget=spend)
                if kw.get("count_calls") is False:
                    return  # the Claude second check's own client, not one the run chose
                built.append((name, used, spend))

        return Recorder

    monkeypatch.setattr(anthropic_client, "AnthropicVisionClient", stub("anthropic"))
    monkeypatch.setattr(openai_client, "OpenAIVisionClient", stub("openai"))
    keys = {"anthropic_api_key": "sk-ant-test", "openai_api_key": "sk-test"}
    monkeypatch.setattr(eval_cli, "_settings", lambda: settings(**keys))
    monkeypatch.setattr(app_config, "get_settings", lambda: settings(**keys))
    return built


def probes(tmp_path, *extra):
    gold = write_gold(tmp_path, [figure("p", 0, "A ball.", "Fig 1"), figure("p", 1, "A car.", "F")])
    out = tmp_path / "probes.json"
    args = ["eval", "calibrate", "--probes", "--text-only", "--gold", str(gold), "--out", str(out)]
    return [*args, *extra], out


def test_probes_on_a_claude_run_use_a_claude_judge_and_record_it(tmp_path, cli_claude):
    args, out = probes(tmp_path, "--provider", "anthropic", "--main-model", SONNET)
    assert main([*args, "--max-usd", "2"]) == 0
    (run_built, judge_built) = cli_claude
    assert run_built[1].main_model == SONNET and judge_built[1].main_model == OPUS
    assert run_built[2] is judge_built[2]  # one budget
    report = json.loads(out.read_text())
    assert report["judge"] == {"provider": "anthropic", "model": OPUS}


def test_probes_can_be_judged_by_another_provider_than_the_run(tmp_path, cli_claude):
    args, out = probes(tmp_path, "--provider", "anthropic")
    assert main([*args, "--max-usd", "2", "--judge-provider", "openai"]) == 0
    assert [name for name, _, _ in cli_claude] == ["anthropic", "openai"]
    assert json.loads(out.read_text())["judge"] == {"provider": "openai", "model": "gpt-6.1-sol"}


def test_probes_default_to_the_runs_provider(tmp_path, cli_claude):
    args, out = probes(tmp_path)
    assert main([*args, "--max-usd", "2"]) == 0
    assert [name for name, _, _ in cli_claude] == ["openai"]  # one client: the run's is the judge
    assert json.loads(out.read_text())["judge"]["provider"] == "openai"


def test_judge_model_choice_is_validated(tmp_path, cli_claude, capsys):
    args, _ = probes(tmp_path)
    assert (
        main([*args, "--max-usd", "2", "--judge-provider", "anthropic", "--judge-model", "x"]) == 2
    )
    assert "invalid judge settings: no price for ANTHROPIC_MAIN_MODEL" in capsys.readouterr().err


def test_agreement_mode_calls_no_judge_so_the_option_is_refused(tmp_path, capsys):
    args = ["eval", "calibrate", "--judge", "j.jsonl", "--labels", "l.csv"]
    assert main([*args, "--judge-provider", "anthropic"]) == 2
    assert "go with --probes" in capsys.readouterr().err


def test_fake_probes_record_the_judge_that_was_asked_for(tmp_path):
    args, out = probes(tmp_path, "--fake", "--judge-provider", "anthropic")
    assert main(args) == 0
    assert json.loads(out.read_text())["judge"] == {"provider": "anthropic", "model": OPUS}


# ------------------------------------------------------------------ the invented-content judge


def page_with_figure(tmp_path):
    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (6, 4), "red").save(buffer, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    path = tmp_path / "figs.html"
    path.write_text(
        f"<!doctype html><html lang='en'><body><figure><img src='{uri}' alt='A graph.'>"
        "<figcaption>Figure 1.1 X.</figcaption></figure></body></html>"
    )
    return path


def invented_judge(tmp_path, *extra):
    out = tmp_path / "judged.json"
    args = ["eval", "invented", "judge", "--output", str(page_with_figure(tmp_path)), "--fake"]
    assert main([*args, "--out", str(out), *extra]) == 0
    return json.loads(out.read_text())


def test_invented_judge_defaults_to_the_configured_provider(tmp_path, cli_claude):
    data = invented_judge(tmp_path)
    assert (data["judge_provider"], data["judge_model"]) == ("openai", "gpt-6.1-sol")


def test_invented_judge_can_be_claude(tmp_path, cli_claude):
    data = invented_judge(tmp_path, "--judge-provider", "anthropic")
    assert (data["judge_provider"], data["judge_model"]) == ("anthropic", OPUS)
    data = invented_judge(tmp_path, "--judge-provider", "anthropic", "--judge-model", SONNET)
    assert data["judge_model"] == SONNET


def test_invented_judge_real_claude_run_needs_a_cap_and_builds_a_claude_client(
    tmp_path, cli_claude, capsys
):
    page = page_with_figure(tmp_path)
    base = ["eval", "invented", "judge", "--output", str(page), "--judge-provider", "anthropic"]
    assert main(base) == 2
    assert "--max-usd is required" in capsys.readouterr().err
    assert main([*base, "--max-usd", "1"]) == 0
    ((name, used, _),) = cli_claude
    assert (name, used.model_provider, used.main_model) == ("anthropic", "anthropic", OPUS)


def test_a_judge_option_on_a_task_without_a_judge_is_refused(tmp_path, capsys):
    gold = write_gold(tmp_path, [figure("p", 0, "A ball.", "Fig 1")])
    args = ["eval", "pipeline", "--task", "headings", "--gold", str(gold), "--fake"]
    assert main([*args, "--judge-provider", "anthropic"]) == 2
    assert "apply to the figures task, not headings" in capsys.readouterr().err
