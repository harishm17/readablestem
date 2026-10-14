"""`readablestem convert`: happy path with --fake, spend flags, and error exits (offline)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from latex2mathml.converter import convert

from readablestem import models
from readablestem.cli import convert as convert_cli
from readablestem.cli.main import build_parser, main
from readablestem.config import ConfigError, Settings
from readablestem.core.budget import JobBudget
from readablestem.core.types import (
    Check,
    Element,
    EquationFix,
    Fix,
    HeadingFix,
    Kind,
    Status,
)
from readablestem.models.base import VisionRequest
from readablestem.models.fake import FakeVisionClient
from readablestem.models.pricing import UnknownModelError
from readablestem.pipeline import equation_step, figure_step, heading_step, html_checks, layout
from readablestem.pipeline.layout import Layout, LayoutError

TITLE = "Private lecture notes on kinematics"
PARAGRAPH = "Confidential paragraph text from the instructor."
TEST_KEY = "sk-test-not-a-real-key"


def cli_settings(**kw: Any) -> Settings:
    """Settings for a CLI run: no .env, a dummy API key unless the test says otherwise."""
    kw.setdefault("openai_api_key", TEST_KEY)
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


class LazyKeyClient:
    """Shaped like WP1's OpenAIVisionClient: construction never needs a key; the first
    `run()` reads it (`settings.require_openai_key()`) and raises ConfigError."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runs = 0

    def run(self, request, schema, *, is_fallback=False):
        self.runs += 1
        self.settings.require_openai_key()
        raise AssertionError("a real request was attempted")


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    for n in range(2):
        doc.new_page().insert_text((72, 72), f"Page {n + 1}")
    path = tmp_path / "notes.pdf"
    doc.save(path)
    doc.close()
    return path


class Pipeline:
    """Fake layout and steps; the equation step calls whatever client the CLI built."""

    def __init__(self) -> None:
        self.analyze_pages: list[int] | None = None
        self.max_calls_seen: int | None = None

    def analyze(self, pdf_bytes, *, pages=None, max_pages=None) -> Layout:
        self.analyze_pages = pages
        page = (pages or [0])[0]
        box = (72.0, 72.0, 200.0, 90.0)
        elements = [
            Element(id="p1-e0", kind=Kind.HEADING, page=page, bbox=box, order=0, text=TITLE),
            Element(id="p1-e1", kind=Kind.PARAGRAPH, page=page, bbox=box, order=1, text=PARAGRAPH),
            Element(id="p1-e2", kind=Kind.EQUATION, page=page, bbox=box, order=2, display="block"),
            Element(id="p1-e3", kind=Kind.EQUATION, page=page, bbox=box, order=3, display="block"),
        ]
        return Layout(elements=elements, page_count=2, pages=[page], title=TITLE)

    def crop(self, pdf_bytes, element, **kw) -> bytes:
        return b"\x89PNG\r\n\x1a\n" + element.id.encode()

    def heading_fixes(self, elements) -> list[Fix]:
        return [
            Fix(
                element_id=e.id,
                kind=Kind.HEADING,
                status=Status.ACCEPTED,
                heading=HeadingFix(level=1, text=e.text or ""),
            )
            for e in elements
            if e.kind is Kind.HEADING
        ]

    def transcribe(self, element, png, *, client, settings, context_text=None) -> Fix:
        request = VisionRequest(
            step="equation",
            model=settings.main_model,
            prompt_version="equation-test",
            instructions="i",
            images=[png],
            context_text=context_text,
        )
        _, call = client.run(request, Check)  # schema irrelevant: the result is ignored
        return Fix(
            element_id=element.id,
            kind=Kind.EQUATION,
            status=Status.ACCEPTED,
            confidence=0.9,
            equation=EquationFix(latex="x^2", mathml=convert("x^2"), speech="x squared"),
            calls=[call],
        )


@pytest.fixture
def pipeline(monkeypatch) -> Pipeline:
    fake = Pipeline()
    monkeypatch.setattr(layout, "analyze", fake.analyze)
    monkeypatch.setattr(layout, "crop", fake.crop)
    monkeypatch.setattr(heading_step, "heading_fixes", fake.heading_fixes)
    monkeypatch.setattr(equation_step, "transcribe", fake.transcribe)
    monkeypatch.setattr(figure_step, "describe", fake.transcribe)
    monkeypatch.setattr(convert_cli, "get_settings", cli_settings)
    monkeypatch.delattr(models, "make_vision_client", raising=False)
    return fake


def run(*args: Any) -> int:
    return main(["convert", *map(str, args)])


# --------------------------------------------------------------------------- happy path


def test_convert_with_fake_writes_html_report_and_a_content_free_summary(
    pdf, tmp_path, pipeline, capsys
):
    out, report = tmp_path / "out" / "notes.html", tmp_path / "out" / "notes.json"
    assert run(pdf, "--out", out, "--fake", "--report", report) == 0
    captured = capsys.readouterr()

    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>") and PARAGRAPH in html
    assert html.count('<math display="block">') == 2
    # Placeholder answers must not pass for a conversion: the file says so first.
    assert html.index(convert_cli.FAKE_NOTICE) < html.index(PARAGRAPH)
    assert '<main>\n<p class="notice readablestem" role="note">' in html

    data = json.loads(report.read_text(encoding="utf-8"))
    assert set(data) == {"result", "checks", "axe"}
    assert "html" not in data["result"] and data["axe"] is None
    assert data["result"]["stats"]["model_calls"] == 2
    assert all(c["passed"] for c in data["checks"])

    for line in (
        "Pages:       1",
        "Elements:    4 (1 headings, 1 paragraphs, 0 list items, 2 equations, 0 figures)",
        "Model calls: 2",
        "(fake client, nothing billed)",
        "Checks:      static 15/15 passed",
        f"wrote {out}",
        f"wrote {report}",
    ):
        assert line in captured.out
    assert "accepted" in captured.out  # fix table header
    assert re.search(
        r"^Review:      \d+ decisions? needed.*, \d+ optional checks?$", captured.out, re.M
    )
    assert TITLE not in captured.out + captured.err  # never prints document text
    assert PARAGRAPH not in captured.out + captured.err
    assert "page 1/1 done" in captured.err


@pytest.mark.parametrize(("pages", "expected"), [("2", [1]), ("2,1,2", [0, 1])])
def test_pages_are_one_based_and_in_document_order(pdf, tmp_path, pipeline, pages, expected):
    assert run(pdf, "--out", tmp_path / "o.html", "--fake", "--pages", pages) == 0
    assert pipeline.analyze_pages == expected


def test_uses_the_model_client_factory_when_present(pdf, tmp_path, pipeline, monkeypatch, capsys):
    seen: dict[str, Any] = {}

    def factory(settings, budget, *, fake):
        seen.update(settings=settings, budget=budget, fake=fake)
        return FakeVisionClient(budget=budget)

    monkeypatch.setattr(models, "make_vision_client", factory, raising=False)
    assert run(pdf, "--out", tmp_path / "o.html", "--max-usd", "0.5") == 0
    assert seen["fake"] is False and isinstance(seen["budget"], JobBudget)
    assert seen["budget"].guard.cap_usd == 0.5
    assert seen["budget"].calls == 2  # the steps' calls went through this budget
    assert "Cost:        $" in capsys.readouterr().out
    assert 'class="notice' not in (tmp_path / "o.html").read_text(encoding="utf-8")


def test_budget_stop_is_reported(pdf, tmp_path, pipeline, monkeypatch, capsys):
    monkeypatch.setattr(
        convert_cli, "get_settings", lambda: cli_settings(max_model_calls_per_job=1)
    )
    assert run(pdf, "--out", tmp_path / "o.html", "--fake") == 0
    out = capsys.readouterr().out
    assert (
        "Budget:      stopped early (budget_job_calls: MAX_MODEL_CALLS_PER_JOB); "
        "1 item(s) kept as originals"
    ) in out
    assert "Model calls: 1" in out


def test_run_cap_is_named_as_the_max_usd_cap(pdf, tmp_path, pipeline, monkeypatch, capsys):
    monkeypatch.setattr(
        models,
        "make_vision_client",
        lambda settings, budget, *, fake: FakeVisionClient(budget=budget),
        raising=False,
    )
    assert run(pdf, "--out", tmp_path / "o.html", "--max-usd", "0.0001") == 0
    out = capsys.readouterr().out
    assert "stopped early (budget_daily: the --max-usd cap for this run); 2 item(s)" in out
    assert "Model calls: 0" in out


def test_a_small_run_cap_suggests_fewer_steps_at_once(pdf, tmp_path, pipeline, monkeypatch, capsys):
    """Each step in flight holds a worst-case reservation against --max-usd, so with a
    small cap the reservations, not the spend, stop the run. Without --concurrency the
    run takes only as many steps as the cap can hold, so it needs no hint; one asked for
    explicitly is used as it is."""
    monkeypatch.setattr(
        models,
        "make_vision_client",
        lambda settings, budget, *, fake: FakeVisionClient(budget=budget),
        raising=False,
    )
    assert run(pdf, "--out", tmp_path / "o.html", "--max-usd", "0.0001") == 0
    assert "try --concurrency" not in capsys.readouterr().out  # already one at a time
    args = ("--max-usd", "0.0001", "--concurrency", "4")
    assert run(pdf, "--out", tmp_path / "q.html", *args) == 0
    assert "with a small --max-usd try --concurrency 1" in capsys.readouterr().out
    args = ("--max-usd", "0.0001", "--concurrency", "1")
    assert run(pdf, "--out", tmp_path / "p.html", *args) == 0
    assert "try --concurrency" not in capsys.readouterr().out


def test_concurrency_is_passed_to_the_pipeline(pdf, tmp_path, pipeline, monkeypatch):
    from readablestem.pipeline import run as run_module

    seen: list[int] = []
    real = run_module.convert_pdf

    def recording(pdf_bytes, **kwargs):
        seen.append(kwargs["concurrency"])
        return real(pdf_bytes, **kwargs)

    monkeypatch.setattr(run_module, "convert_pdf", recording)
    assert run(pdf, "--out", tmp_path / "a.html", "--fake") == 0
    assert run(pdf, "--out", tmp_path / "b.html", "--fake", "--concurrency", "1") == 0
    assert run(pdf, "--out", tmp_path / "c.html", "--fake", "--concurrency", "16") == 0
    assert seen == [8, 1, 16]  # MODEL_CONCURRENCY by default, as asked when asked


@pytest.mark.parametrize("value", ["0", "-1", "17", "two", "1.5"])
def test_concurrency_must_be_a_small_positive_number(pdf, tmp_path, value, capsys):
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(
            ["convert", str(pdf), "--out", str(tmp_path / "o.html"), "--fake",
             "--concurrency", value]
        )  # fmt: skip
    assert exit_info.value.code == 2
    assert "--concurrency" in capsys.readouterr().err


def test_list_items_are_counted_in_the_summary(pdf, tmp_path, pipeline, monkeypatch, capsys):
    analyze = pipeline.analyze

    def with_list(pdf_bytes, *, pages=None, max_pages=None) -> Layout:
        found = analyze(pdf_bytes, pages=pages, max_pages=max_pages)
        box = (72.0, 100.0, 200.0, 110.0)
        found.elements += [
            Element(id=f"p1-e{4 + i}", kind=Kind.LIST_ITEM, page=0, bbox=box, order=4 + i,
                    text=f"item {i}", list_marker="\u2022", list_ordered=False, list_level=1)
            for i in range(3)
        ]  # fmt: skip
        return found

    monkeypatch.setattr(layout, "analyze", with_list)
    assert run(pdf, "--out", tmp_path / "o.html", "--fake") == 0
    out = capsys.readouterr().out
    assert "Elements:    7 (1 headings, 1 paragraphs, 3 list items, 2 equations, 0 figures)" in out
    assert "item 0" not in out  # counts only, never the text


def test_axe_flag_runs_axe_and_summarises_rule_ids(pdf, tmp_path, pipeline, monkeypatch, capsys):
    violations = [Check(name="axe.region", passed=False, severity="warning", code="region")]
    monkeypatch.setattr(html_checks, "axe_checks", lambda html: violations)
    assert run(pdf, "--out", tmp_path / "o.html", "--fake", "--axe") == 0
    assert "axe-core 1 rule(s) violated, 0 serious or critical: region" in capsys.readouterr().out


def test_axe_flag_says_when_it_was_skipped(pdf, tmp_path, pipeline, monkeypatch, capsys):
    monkeypatch.setattr(html_checks, "axe_checks", lambda html: [html_checks.AXE_SKIPPED])
    assert run(pdf, "--out", tmp_path / "o.html", "--fake", "--axe") == 0
    assert "axe-core skipped (needs the browser group" in capsys.readouterr().out


def test_axe_error_is_not_reported_as_a_violation(pdf, tmp_path, pipeline, monkeypatch, capsys):
    error = Check(name="axe", passed=False, severity="warning", code="axe_error")
    monkeypatch.setattr(html_checks, "axe_checks", lambda html: [error])
    assert run(pdf, "--out", tmp_path / "o.html", "--fake", "--axe") == 0
    out = capsys.readouterr().out
    assert "axe-core could not run (browser error" in out and "violated" not in out


def test_help_lists_convert(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--help"])
    assert "convert" in capsys.readouterr().out


# --------------------------------------------------------------------------- errors


def test_max_usd_without_a_model_client_is_an_error(pdf, tmp_path, pipeline, capsys):
    assert run(pdf, "--out", tmp_path / "o.html", "--max-usd", "1") == 2
    assert "error: no model client in this build" in capsys.readouterr().err
    assert not (tmp_path / "o.html").exists()


def test_missing_api_key_is_an_error(pdf, tmp_path, pipeline, monkeypatch, capsys):
    # WP1's real factory never raises: its client reads the key on the first request.
    clients: list[LazyKeyClient] = []

    def factory(settings, budget, *, fake):
        clients.append(LazyKeyClient(settings))
        return clients[-1]

    monkeypatch.setattr(models, "make_vision_client", factory, raising=False)
    monkeypatch.setattr(convert_cli, "get_settings", lambda: cli_settings(openai_api_key=None))
    assert run(pdf, "--out", tmp_path / "o.html", "--max-usd", "1") == 2
    err = capsys.readouterr().err
    assert err.startswith("error: OPENAI_API_KEY is not set")
    assert "step failed" not in err and not (tmp_path / "o.html").exists()
    assert sum(c.runs for c in clients) == 0  # caught before any element was processed


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (ConfigError("OPENAI_API_KEY is not set (in .env)"), "OPENAI_API_KEY is not set (in .env)"),
        (UnknownModelError("no price for model 'gpt-x'"), "no price for model 'gpt-x'"),
    ],
)
def test_client_setup_error_on_the_first_request_is_an_error(
    pdf, tmp_path, pipeline, monkeypatch, capsys, error, message
):
    runs: list[str] = []

    class SetupFails:
        def run(self, request, schema, *, is_fallback=False):
            runs.append(request.step)
            raise error

    monkeypatch.setattr(
        models, "make_vision_client", lambda s, b, *, fake: SetupFails(), raising=False
    )
    assert run(pdf, "--out", tmp_path / "o.html", "--max-usd", "1") == 2
    captured = capsys.readouterr()
    assert f"error: {message}\n" in captured.err and "Fixes:" not in captured.out
    assert not (tmp_path / "o.html").exists()  # no file with every item flagged
    assert len(runs) == 1  # the run stopped at the first element


def test_unpriced_model_is_an_error_before_converting(pdf, tmp_path, pipeline, monkeypatch, capsys):
    monkeypatch.setattr(
        models,
        "make_vision_client",
        lambda settings, budget, *, fake: FakeVisionClient(budget=budget),
        raising=False,
    )
    monkeypatch.setattr(convert_cli, "get_settings", lambda: cli_settings(main_model="gpt-x"))
    assert run(pdf, "--out", tmp_path / "o.html", "--max-usd", "1") == 2
    assert capsys.readouterr().err.startswith("error: no price for model 'gpt-x'")
    assert pipeline.analyze_pages is None and not (tmp_path / "o.html").exists()


def test_fake_needs_no_api_key(pdf, tmp_path, pipeline, monkeypatch):
    monkeypatch.setattr(convert_cli, "get_settings", lambda: cli_settings(openai_api_key=None))
    assert run(pdf, "--out", tmp_path / "o.html", "--fake") == 0


@pytest.mark.parametrize(
    ("pages", "message"),
    [
        ("0", "page(s) 0 out of range"),
        ("3", "page(s) 3 out of range"),
        ("1,x", "--pages must be comma-separated page numbers"),
        (",", "out of range"),
    ],
)
def test_bad_pages_exit_2(pdf, tmp_path, pipeline, capsys, pages, message):
    assert run(pdf, "--out", tmp_path / "o.html", "--fake", "--pages", pages) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ") and message in err


def test_missing_and_unreadable_files_exit_2(tmp_path, pipeline, capsys):
    assert run(tmp_path / "nope.pdf", "--out", tmp_path / "o.html", "--fake") == 2
    assert "error: PDF not found" in capsys.readouterr().err
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"not a pdf")
    assert run(junk, "--out", tmp_path / "o.html", "--fake") == 2
    assert "error: cannot open" in capsys.readouterr().err


def test_output_path_that_is_a_directory_exits_2(pdf, tmp_path, pipeline, capsys):
    assert run(pdf, "--out", tmp_path, "--fake") == 2
    assert "is a directory" in capsys.readouterr().err


def test_never_overwrites_the_input_pdf(pdf, tmp_path, pipeline, monkeypatch, capsys):
    original = pdf.read_bytes()
    assert run(pdf, "--out", pdf, "--fake") == 2
    assert "is the input PDF" in capsys.readouterr().err
    monkeypatch.chdir(tmp_path)  # the same file through a relative path
    assert run(pdf, "--out", "notes.pdf", "--fake", "--report", "r.json") == 2
    assert run(pdf, "--out", "o.html", "--fake", "--report", "notes.pdf") == 2
    assert run(pdf, "--out", "o.html", "--fake", "--report", "o.html") == 2
    assert "must be different files" in capsys.readouterr().err
    assert pdf.read_bytes() == original


def test_layout_errors_exit_2_with_their_code(pdf, tmp_path, pipeline, monkeypatch, capsys):
    def too_long(pdf_bytes, *, pages=None, max_pages=None):
        raise LayoutError("too_many_pages", "too many pages")

    monkeypatch.setattr(layout, "analyze", too_long)
    assert run(pdf, "--out", tmp_path / "o.html", "--fake") == 2
    err = capsys.readouterr().err
    assert "too_many_pages" in err and "--pages" in err


@pytest.mark.parametrize(
    "args",
    [
        [],  # neither --max-usd nor --fake
        ["--fake", "--max-usd", "1"],  # both
        ["--max-usd", "0"],
        ["--max-usd", "-1"],
        ["--max-usd", "abc"],
        ["--max-usd", "inf"],
        ["--max-usd", "nan"],
    ],
)
def test_spend_flags_are_required_exclusive_and_positive(pdf, tmp_path, args):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["convert", str(pdf), "--out", str(tmp_path / "o.html"), *args])
    assert exc.value.code == 2


def test_out_is_required(pdf):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["convert", str(pdf), "--fake"])
    assert exc.value.code == 2


def test_tables_notices_and_the_language_are_in_the_summary(tmp_path, capsys):
    """The LaTeX tables fixture with the fake client (tables flag off): the tables are
    counted, kept as images (a row in the fix table), their notices listed by code."""
    fixture = Path(__file__).parent / "fixtures" / "layout" / "latex" / "tables.pdf"
    assert run(fixture, "--out", tmp_path / "t.html", "--fake") == 0
    out = capsys.readouterr().out
    assert re.search(r"Elements:    \d+ \(.*, 0 figures, [45] tables\)", out)
    assert re.search(r"^  table +", out, re.MULTILINE)
    assert re.search(r"^Notices:     .*table_not_rebuilt [45]", out, re.MULTILINE)
    assert re.search(r"^Language:    en \((detected|default|pdf)\)$", out, re.MULTILINE)
