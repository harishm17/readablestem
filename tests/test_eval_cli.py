"""CLI wiring for eval pipeline/calibrate/render and demo build (offline)."""

from __future__ import annotations

import json
from typing import ClassVar

import pytest

from a11y_stem.cli import eval_cli
from a11y_stem.cli.main import build_parser, main
from a11y_stem.core.types import (
    ConversionOutput,
    DocumentResult,
    DocumentStats,
    Element,
    Fix,
    Kind,
    Status,
)
from a11y_stem.eval import pipeline_eval, render
from a11y_stem.models.fake import FakeVisionClient
from a11y_stem.pipeline import equation_step, run
from test_eval_support import (
    EQ_A,
    EQ_F,
    EQ_V,
    EQ_X,
    FakeMedia,
    eq_fix,
    equation,
    fake_transcribe,
    figure,
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


def test_cli_task_list_matches_the_library():
    assert eval_cli.TASKS == pipeline_eval.TASKS


def test_parser_wires_eval_and_demo_commands():
    parser = build_parser()
    args = parser.parse_args(["eval", "pipeline", "--task", "figures", "--fake", "--limit", "3"])
    assert (args.task, args.fake, args.limit, args.max_usd) == ("figures", True, 3, None)
    args = parser.parse_args(["demo", "build", "--page", "2-2-speed", "--max-usd", "0.5"])
    assert (args.page, args.book, args.max_usd) == ("2-2-speed", "physics", 0.5)
    for bad in (["--max-usd", "0"], ["--max-usd", "-1"], ["--date", "07/10/2026"]):
        with pytest.raises(SystemExit):
            parser.parse_args(["eval", "pipeline", "--task", "equations", *bad])


def test_real_runs_require_a_spend_cap(gold, tmp_path, fake_inputs, capsys):
    assert main(pipeline_args(gold, tmp_path)) == 2
    assert "--max-usd is required" in capsys.readouterr().err
    assert not (tmp_path / "report.json").exists()


def test_fake_pipeline_run_writes_report_items_and_table(gold, tmp_path, fake_inputs, capsys):
    items = tmp_path / "items.jsonl"
    assert main(pipeline_args(gold, tmp_path, "--fake", "--items", str(items))) == 0
    out = capsys.readouterr().out
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["client"] == "fake" and report["date"] == "2026-10-07"
    assert (report["n"], report["complete"]) == (4, True)
    # The fake client has no recorded answers: a plumbing run, every call refused.
    assert report["metrics"]["exact_lenient"] == 0.0
    assert report["metrics"]["by_status"]["failed"]["n"] == 4
    assert len(items.read_text().splitlines()) == 4
    assert "## Eval: equations" in out and "wrote" in out
    assert MediaFactory.built[0][1] == tmp_path / "cache"


def test_fake_run_with_a_cap_stops_cleanly(gold, tmp_path, fake_inputs, capsys):
    # Worst-case reservations: ~$0.020 per main-model call, ~$0.001 per cheap call.
    assert main(pipeline_args(gold, tmp_path, "--fake", "--max-usd", "0.022")) == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert (report["n"], report["n_planned"], report["complete"]) == (2, 4, False)
    assert report["stopped_reason"] == "budget_job_usd"
    assert "PARTIAL (budget_job_usd)" in capsys.readouterr().out


def test_max_usd_builds_the_openai_client_with_the_cap(gold, tmp_path, fake_inputs, monkeypatch):
    built = []

    class StubOpenAIVisionClient(FakeVisionClient):
        def __init__(self, settings, budget, *, client=None, verifier=None):
            super().__init__(budget=budget)
            built.append(budget)
            verifiers.append(verifier)

    verifiers: list = []

    from a11y_stem.cli import eval_cli
    from a11y_stem.config import Settings
    from a11y_stem.models import openai_client

    monkeypatch.setattr(openai_client, "OpenAIVisionClient", StubOpenAIVisionClient)
    monkeypatch.setattr(
        eval_cli, "_settings", lambda: Settings(_env_file=None, openai_api_key="sk-test")
    )
    assert main(pipeline_args(gold, tmp_path, "--max-usd", "1.5", "--max-calls", "7")) == 0
    (budget,) = built
    assert (budget.max_usd, budget.max_calls, budget.guard.cap_usd) == (1.5, 7, 1.5)
    assert json.loads((tmp_path / "report.json").read_text())["client"] == "openai"
    # The second automated check rides on the same client (and so the same budget).
    from a11y_stem.models.verifier import DecisionsVerifier

    (verifier,) = verifiers
    assert isinstance(verifier, DecisionsVerifier)


def test_missing_api_key_is_explained(gold, tmp_path, fake_inputs, monkeypatch, capsys):
    from a11y_stem.cli import eval_cli
    from a11y_stem.config import Settings

    monkeypatch.setattr(eval_cli, "_settings", lambda: Settings(_env_file=None))
    assert main(pipeline_args(gold, tmp_path, "--max-usd", "1")) == 2
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().err


def test_stub_steps_are_reported_not_crashed(gold, tmp_path, fake_inputs, monkeypatch, capsys):
    def stub(*args, **kwargs):
        raise NotImplementedError("WP3")

    monkeypatch.setattr(equation_step, "transcribe", stub)
    assert main(pipeline_args(gold, tmp_path, "--fake")) == 2
    assert "still a stub (WP3)" in capsys.readouterr().err


def test_missing_gold_dir(tmp_path, capsys):
    assert main(pipeline_args(tmp_path / "nope", tmp_path, "--fake")) == 2
    assert "gold dir not found" in capsys.readouterr().err


# ---------------------------------------------------------------------- calibrate


def test_calibrate_template_then_agreement(tmp_path, capsys):
    judged = tmp_path / "judged.jsonl"
    rows = [
        {"id": "a", "judge_outcome": "judged", "judge_pass": True},
        {"id": "b", "judge_outcome": "judged", "judge_pass": False},
    ]
    judged.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    sheet = tmp_path / "labels.csv"
    assert main(["eval", "calibrate", "--judge", str(judged), "--write-template", str(sheet)]) == 0
    assert "wrote 2 rows" in capsys.readouterr().out
    labels = tmp_path / "labels.jsonl"
    labels.write_text('{"id": "a", "human_pass": "yes"}\n{"id": "b", "human_pass": "no"}\n')
    out_json = tmp_path / "agreement.json"
    args = ["eval", "calibrate", "--judge", str(judged), "--labels", str(labels)]
    assert main([*args, "--n-boot", "100", "--out", str(out_json)]) == 0
    assert "| raw agreement | 1.000 |" in capsys.readouterr().out
    assert json.loads(out_json.read_text())["n"] == 2


def test_calibrate_needs_inputs(tmp_path, capsys):
    assert main(["eval", "calibrate"]) == 2
    assert "--judge and --labels" in capsys.readouterr().err
    sheet = str(tmp_path / "labels.csv")
    assert main(["eval", "calibrate", "--write-template", sheet]) == 2
    assert "needs --judge" in capsys.readouterr().err
    missing = str(tmp_path / "nope.jsonl")
    assert main(["eval", "calibrate", "--judge", missing, "--write-template", sheet]) == 2
    assert "file not found" in capsys.readouterr().err


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


def test_the_sample_always_has_at_least_one_unit(gold):
    from datetime import date

    from a11y_stem.eval.pipeline_eval import load_gold
    from a11y_stem.eval.sample import iso_week, sample_units

    assert len(sample_units("equations", load_gold(gold), fraction=0.05, seed="w")) == 1
    assert iso_week(date(2026, 10, 8)) == "2026-W41"
    with pytest.raises(ValueError):
        sample_units("equations", load_gold(gold), fraction=0, seed="w")


def test_promote_copies_only_complete_real_and_current_reports(tmp_path, capsys):
    from a11y_stem.eval.gate import code_versions

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


def test_calibrate_probes_with_the_fake_client(tmp_path, capsys):
    gold = write_gold(tmp_path, [figure("p", 0, "A ball.", "Fig 1"), figure("p", 1, "A car.", "F")])
    out = tmp_path / "probes.json"
    args = ["eval", "calibrate", "--probes", "--fake", "--gold", str(gold), "--out", str(out)]
    assert main(args) == 0
    report = json.loads(out.read_text())
    assert (report["n"], report["complete"]) == (8, True)
    assert "Judge bias probes" in capsys.readouterr().out


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


def test_render_page_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(eval_cli, "_page_source", lambda book, slug, gold, fetcher: page_source())

    def fake_pdf(page, out_path=None, **kwargs):
        out_path.write_bytes(b"%PDF-1.4 fake")
        return b"%PDF-1.4 fake"

    monkeypatch.setattr(render, "render_page_pdf", fake_pdf)
    out = tmp_path / "page.pdf"
    assert main(["eval", "render", "--page", "2-2-speed-and-velocity", "--out", str(out)]) == 0
    assert out.read_bytes().startswith(b"%PDF")
    assert main(["eval", "render"]) == 2
    assert "--page SLUG or --equations" in capsys.readouterr().err


def test_page_source_prefers_gold_records(tmp_path, monkeypatch):
    gold = write_gold(tmp_path, [equation("p-1", 0, EQ_V, "block")])
    seen = {}

    def from_records(records, fetcher, *, authors=None):
        seen["records"], seen["authors"] = records, authors
        return "from-gold"

    monkeypatch.setattr(render, "page_source_from_records", from_records)
    monkeypatch.setattr(render, "page_source_from_openstax", lambda *a: "live")
    assert eval_cli._page_source("physics", "p-1", gold, fetcher=None) == "from-gold"
    assert seen["authors"] == ["Ada Author"] and len(seen["records"]) == 1
    assert eval_cli._page_source("physics", "other-page", gold, fetcher=None) == "live"


# --------------------------------------------------------------------------- demo


CONVERTED = (
    '<!doctype html><html lang="en"><head><title>2.2 Speed</title></head>'
    "<body><main><h1>2.2 Speed and Velocity</h1></main></body></html>"
)


def fake_convert(budget_failures: int = 0):
    def convert_pdf(pdf_bytes, *, settings, client, **_):
        assert pdf_bytes == b"%PDF-demo"
        fixes = [eq_fix("p1-e1", "v=d/t")]
        fixes += [
            Fix(
                element_id=f"p1-e{9 + i}",
                kind=Kind.EQUATION,
                status=Status.FAILED,
                reason="budget_daily",
            )
            for i in range(budget_failures)
        ]
        element = Element(id="p1-e1", kind=Kind.EQUATION, page=0, bbox=(0, 0, 1, 1), order=0)
        result = DocumentResult(
            pipeline_version=run.PIPELINE_VERSION,
            elements=[element],
            fixes=fixes,
            stats=DocumentStats(pages=2, cost_usd=0.0123, latency_ms=900, model_calls=3),
            html=CONVERTED,
        )
        return ConversionOutput(result=result, crops={"p1-e1": b"png"})

    return convert_pdf


@pytest.fixture
def demo_fakes(monkeypatch):
    monkeypatch.setattr(eval_cli, "_page_source", lambda book, slug, gold, fetcher: page_source())

    def fake_pdf(page, out_path=None, **kwargs):
        out_path.write_bytes(b"%PDF-demo")
        return b"%PDF-demo"

    monkeypatch.setattr(render, "render_page_pdf", fake_pdf)


def test_demo_build_writes_wrapped_html_report_and_source(
    tmp_path, monkeypatch, demo_fakes, capsys
):
    monkeypatch.setattr(run, "convert_pdf", fake_convert())
    out = tmp_path / "demo"
    args = ["demo", "build", "--page", "2-2-speed-and-velocity", "--fake", "--out", str(out)]
    assert main([*args, "--date", "2026-10-07"]) == 0
    html = (out / "index.html").read_text()
    assert html.index("<body>") < html.index("Precomputed demo.") < html.index("<main>")
    assert "Access for free at openstax.org" in html and "compliant" not in html.lower()
    report = json.loads((out / "report.json").read_text())
    assert report["demo"]["page_url"].endswith("2-2-speed-and-velocity")
    assert report["demo"]["client"] == "fake" and report["demo"]["date"] == "2026-10-07"
    assert "html" not in report["result"]
    assert report["result"]["stats"]["cost_usd"] == 0.0123
    assert (out / "source.pdf").read_bytes() == b"%PDF-demo"
    printed = capsys.readouterr().out
    assert "2 PDF pages, 3 model calls, $0.0123" in printed and "warning" not in printed


def test_demo_build_warns_when_the_cap_cut_it_short(tmp_path, monkeypatch, demo_fakes, capsys):
    monkeypatch.setattr(run, "convert_pdf", fake_convert(budget_failures=2))
    out = tmp_path / "demo"
    assert (
        main(["demo", "build", "--page", "x", "--max-usd", "0.01", "--fake", "--out", str(out)])
        == 0
    )
    assert "2 fixes were not attempted" in capsys.readouterr().out


def test_demo_build_requires_a_cap_or_fake(tmp_path, capsys):
    assert main(["demo", "build", "--page", "x", "--out", str(tmp_path)]) == 2
    assert "--max-usd is required" in capsys.readouterr().err


# ------------------------------------------------------------ the Anthropic provider


def test_provider_anthropic_runs_with_claude_and_the_report_records_it(
    gold, tmp_path, fake_inputs, monkeypatch
):
    built = []

    class StubAnthropicVisionClient(FakeVisionClient):
        def __init__(self, settings, budget, **kw):
            super().__init__(budget=budget)
            built.append((settings, budget))

    from a11y_stem.models import anthropic_client

    monkeypatch.setattr(anthropic_client, "AnthropicVisionClient", StubAnthropicVisionClient)
    monkeypatch.setattr(eval_cli, "_settings", lambda: settings(anthropic_api_key="sk-ant-test"))
    args = pipeline_args(gold, tmp_path, "--provider", "anthropic", "--max-usd", "1.5")
    assert main(args) == 0
    ((used, budget),) = built
    assert (used.model_provider, used.main_model, used.cheap_model) == (
        "anthropic",
        "claude-opus-5-5",
        "claude-haiku-5-5",
    )
    assert (budget.max_usd, budget.guard.cap_usd) == (1.5, 1.5)
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["client"] == "anthropic"
    assert report["models"] == {"main": "claude-opus-5-5", "cheap": "claude-haiku-5-5"}


def test_models_can_be_chosen_per_run(gold, tmp_path, fake_inputs, capsys):
    args = pipeline_args(
        gold,
        tmp_path,
        *("--provider", "anthropic", "--cheap-model", "claude-sonnet-5-5", "--fake"),
    )
    assert main(args) == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["client"] == "fake"
    assert report["models"] == {"main": "claude-opus-5-5", "cheap": "claude-sonnet-5-5"}


def test_an_unpriced_model_is_an_error_not_a_traceback(gold, tmp_path, fake_inputs, capsys):
    args = pipeline_args(
        gold, tmp_path, "--provider", "anthropic", "--main-model", "gpt-6", "--fake"
    )
    assert main(args) == 2
    err = capsys.readouterr().err
    assert "invalid model settings" in err and "no price for ANTHROPIC_MAIN_MODEL" in err


def test_provider_anthropic_without_a_key_names_its_key(gold, tmp_path, fake_inputs, capsys):
    args = pipeline_args(gold, tmp_path, "--provider", "anthropic", "--max-usd", "1")
    assert main(args) == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err


def test_a_claude_report_is_promoted_only_beside_the_openai_baselines(tmp_path, capsys):
    from a11y_stem.eval.gate import code_versions

    data = {
        "task": "equations",
        "complete": True,
        "client": "anthropic",
        "prompt_versions": {"equation": code_versions()["equation"]},
        "models": {"main": "claude-opus-5-5", "cheap": "claude-haiku-5-5"},
    }
    report = tmp_path / "claude.json"
    report.write_text(json.dumps(data))
    assert main(["eval", "promote", str(report), "--as", "baseline"]) == 2
    assert "give --out-dir" in capsys.readouterr().err
    target = ["--out-dir", str(tmp_path / "apart")]
    assert main(["eval", "promote", str(report), "--as", "latest", *target]) == 0
    # A Claude report is current against the Claude models, not the OpenAI ones.
    data["models"]["cheap"] = "claude-sonnet-5-5"
    report.write_text(json.dumps(data))
    assert main(["eval", "promote", str(report), "--as", "latest", *target]) == 2
    assert "cheap model claude-sonnet-5-5" in capsys.readouterr().err
