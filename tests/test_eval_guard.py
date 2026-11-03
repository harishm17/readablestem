"""scripts/eval_guard.py: a model or prompt change needs its eval report."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("eval_guard", ROOT / "scripts" / "eval_guard.py")
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

CONFIG = """
MAIN_MODEL = "gpt-6.1-sol"
ANTHROPIC_MAIN_MODEL = "claude-opus-5-5"  # production
class Settings:
    anthropic_cheap_model: str = ANTHROPIC_CHEAP_MODEL
    claude_check_model: str = "claude-sonnet-5-5"
    page_math_model: str | None = None
    retries: int = 3
"""
PROMPT = "src/readablestem/models/prompts/equation.md"
CFG = "src/readablestem/config.py"


def test_unchanged_config_has_no_changed_models() -> None:
    assert guard.changed_model_names(CONFIG, CONFIG) == []


def test_value_change_comment_change_and_other_lines() -> None:
    commented = CONFIG.replace("# production", "# the production model").replace("= 3", "= 4")
    assert guard.changed_model_names(CONFIG, commented) == []
    bumped = CONFIG.replace("claude-opus-5-5", "claude-opus-5-6")
    assert guard.changed_model_names(CONFIG, bumped) == ["anthropic_main_model"]
    field = CONFIG.replace("claude-sonnet-5-5", "claude-sonnet-5-6")
    assert guard.changed_model_names(CONFIG, field) == ["claude_check_model"]


def test_added_and_removed_answer_settings_count() -> None:
    added = CONFIG + '    anthropic_effort_figure: str = "high"\n'
    assert guard.changed_model_names(CONFIG, added) == ["anthropic_effort_figure"]
    assert guard.changed_model_names(added, CONFIG) == ["anthropic_effort_figure"]


RUNTIME_KNOBS = """
class Settings:
    model_timeout_s: float = Field(default=60.0, gt=0)
    model_max_retries: int = Field(default=2, ge=0, le=5)
    model_concurrency: int = Field(default=8, ge=1, le=16)
    max_model_calls_per_job: int = Field(default=400, gt=0)
    fake_models: bool = False
    preload_table_models: bool = True
    model_config = SettingsConfigDict(env_file=".env")
"""


def test_runtime_knobs_are_not_answer_settings() -> None:
    changed = (
        RUNTIME_KNOBS.replace("60.0", "90.0")
        .replace("= 8", "= 4")
        .replace("400", "800")
        .replace("False", "True")
        .replace('".env"', '".env.local"')
    )
    assert guard.model_assignments(RUNTIME_KNOBS) == {}
    assert guard.changed_model_names(RUNTIME_KNOBS, changed) == []


@pytest.mark.parametrize(
    "name",
    [
        "MAIN_MODEL",
        "claude_check_model",
        "claude_check_effort",
        "anthropic_effort_main",
        "anthropic_effort_image_pages",
        "page_math_max_output_tokens",
        "figure_thinking_budget",
        "openai_reasoning_effort",
    ],
)
def test_answer_settings_are_recognised(name: str) -> None:
    assert guard.is_answer_setting(name)


def test_multi_line_values_compare_by_parsed_value() -> None:
    wrapped = (
        "class S:\n"
        "    page_math_max_output_tokens: int = Field(\n"
        "        default=4096,\n"
        "        ge=1024,\n"
        "    )\n"
    )
    same = (
        "class S:\n    page_math_max_output_tokens: int = Field(default=4096, ge=1024, le=32000)\n"
    )
    assert guard.changed_model_names(wrapped, same) == []  # a bound is not the value
    other = wrapped.replace("default=4096", "default=8192")
    assert guard.changed_model_names(wrapped, other) == ["page_math_max_output_tokens"]
    multi = 'class S:\n    anthropic_effort_main = (\n        "medium"\n    )\n'
    assert guard.changed_model_names(multi, multi.replace("medium", "high")) == [
        "anthropic_effort_main"
    ]
    reflowed = 'class S:\n    anthropic_effort_main = "medium"\n'
    assert guard.changed_model_names(multi, reflowed) == []


def test_prompt_in_a_subfolder_counts() -> None:
    path = "src/readablestem/models/prompts/extra/equation.md"
    assert guard.prompt_name(path) == "equation"
    code, lines = guard.check([path], "", "")
    assert code == 1 and "latest-equations.json" in "\n".join(lines)


def test_trailer_must_be_a_real_trailer() -> None:
    body = "Explain\n\nWe use Eval-Unchanged: reason in the body text.\n\nMore."
    assert guard.opt_out([], [body]) is None
    assert guard.opt_out([], ["Eval-Unchanged: in the subject line only"]) is None
    mid = "Subject\n\nEval-Unchanged: not last\n\nSigned-off-by: someone"
    assert guard.opt_out([], [mid]) is None
    last = "Subject\n\nbody\n\nSigned-off-by: someone\nEval-Unchanged: comment only\n"
    assert guard.opt_out([], [last]) == "commit trailer Eval-Unchanged: comment only"


def test_prompt_name_only_for_prompt_markdown() -> None:
    assert guard.prompt_name(PROMPT) == "equation"
    assert guard.prompt_name("src/readablestem/models/prompts/__init__.py") is None
    assert guard.prompt_name("docs/equation.md") is None
    assert guard.prompt_name("src/readablestem/models/prompts/sub/x.md") == "x"


def test_nothing_relevant_passes() -> None:
    code, lines = guard.check(["README.md", "src/readablestem/web/app.py"], "", "")
    assert code == 0 and "nothing to check" in lines[0]


def test_config_without_model_change_passes() -> None:
    changed = CONFIG.replace("= 3", "= 5")
    assert guard.check([CFG], CONFIG, changed)[0] == 0


def test_prompt_change_without_report_fails_and_points_to_docs() -> None:
    code, lines = guard.check([PROMPT], "", "")
    text = "\n".join(lines)
    assert code == 1
    assert "latest-equations.json" in text and "latest-document.json" in text
    assert "How to rerun" in text and "eval-unchanged" in text and "Eval-Unchanged:" in text


def test_prompt_change_with_all_matching_reports_passes() -> None:
    files = [PROMPT, "data/eval/latest-equations.json", "data/eval/latest-document.json"]
    assert guard.check(files, "", "")[0] == 0


def test_one_of_two_reports_is_not_enough() -> None:
    code, lines = guard.check([PROMPT, "data/eval/latest-equations.json"], "", "")
    assert code == 1
    assert "latest-document.json" in "\n".join(lines)
    assert "latest-equations.json:" not in "\n".join(lines)


def test_baseline_does_not_count_as_a_latest_report() -> None:
    assert guard.check([PROMPT, "data/eval/baseline-equations.json"], "", "")[0] == 1


def test_unlisted_prompt_needs_the_document_report() -> None:
    path = "src/readablestem/models/prompts/page_math.md"
    code, lines = guard.check([path], "", "")
    assert code == 1 and "latest-document.json" in "\n".join(lines)
    assert guard.check([path, "data/eval/latest-document.json"], "", "")[0] == 0


@pytest.mark.parametrize("name", ["image_page", "image_page_crops", "image_page_notation"])
def test_an_image_page_prompt_needs_the_image_pages_report(name: str) -> None:
    path = f"src/readablestem/models/prompts/{name}.md"
    code, lines = guard.check([path], "", "")
    text = "\n".join(lines)
    assert code == 1 and "latest-image-pages.json" in text and "latest-document.json" not in text
    assert guard.check([path, "data/eval/latest-image-pages.json"], "", "")[0] == 0


def test_the_image_pages_model_and_effort_need_the_image_pages_report() -> None:
    for name in ("image_pages_model", "anthropic_effort_image_pages"):
        assert guard.required_reports([], [name]) == {
            "latest-image-pages.json": [f"{guard.CONFIG}: {name} changed"]
        }


def test_claude_model_change_needs_every_claude_report() -> None:
    bumped = CONFIG.replace("claude-opus-5-5", "claude-opus-5-6")
    code, lines = guard.check([CFG], CONFIG, bumped)
    text = "\n".join(lines)
    assert code == 1
    for report in guard.ANTHROPIC_REPORTS:
        assert report in text
    assert "latest-tables-local.json" not in text
    files = [CFG, *(f"data/eval/{r}" for r in guard.ANTHROPIC_REPORTS)]
    assert guard.check(files, CONFIG, bumped)[0] == 0


def test_label_waives_with_a_visible_note() -> None:
    code, lines = guard.check([PROMPT], "", "", labels=["bug", "Eval-Unchanged"])
    assert code == 0
    assert "waived by label eval-unchanged" in lines[0]
    assert "latest-equations.json" in "\n".join(lines)


def test_trailer_waives_only_with_a_reason() -> None:
    ok = "Fix typo\n\nbody\n\nEval-Unchanged: whitespace in a comment line of the prompt\n"
    code, lines = guard.check([PROMPT], "", "", commit_messages=["other", ok])
    assert code == 0 and "whitespace in a comment" in lines[0]
    assert guard.check([PROMPT], "", "", commit_messages=["x\n\nEval-Unchanged:\n"])[0] == 1
    assert guard.check([PROMPT], "", "", labels=["eval"], commit_messages=["no trailer"])[0] == 1


def test_updated_reports_need_no_waiver_even_with_label() -> None:
    files = [PROMPT, "data/eval/latest-equations.json", "data/eval/latest-document.json"]
    code, lines = guard.check(files, "", "", labels=["eval-unchanged"])
    assert code == 0 and "every report this change needs is updated" in lines[0]


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    for rel, text in ((CFG, CONFIG), (PROMPT, "ask\n"), ("data/eval/latest-equations.json", "{}")):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "switch", "-q", "-c", "topic")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_main_against_a_real_repository(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / PROMPT).write_text("ask differently\n")
    _git(repo, "commit", "-qam", "change the prompt")
    assert guard.main(["--base", "main"]) == 1
    assert "latest-equations.json" in capsys.readouterr().out

    (repo / "data/eval/latest-equations.json").write_text('{"a": 1}')
    (repo / "data/eval/latest-document.json").write_text("{}")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "re-run")
    assert guard.main(["--base", "main"]) == 0


def test_main_reads_labels_and_trailers(repo: Path) -> None:
    (repo / PROMPT).write_text("same meaning\n")
    _git(repo, "commit", "-qam", "reflow")
    assert guard.main(["--base", "main"]) == 1
    assert guard.main(["--base", "main", "--labels-json", '["eval-unchanged"]']) == 0
    (repo / PROMPT).write_text("same meaning again\n")
    _git(repo, "commit", "-qam", "tweak\n\nEval-Unchanged: line wrapping only")
    assert guard.main(["--base", "main"]) == 0


def test_main_model_constant_change_in_a_real_repository(repo: Path) -> None:
    (repo / CFG).write_text(CONFIG.replace("gpt-6.1-sol", "gpt-6.2-sol"))
    _git(repo, "commit", "-qam", "bump the OpenAI model")
    assert guard.main(["--base", "main"]) == 1


def test_history_replay_package_rename_is_not_flagged() -> None:
    """27eab47 renamed the package folder (config.py R099, prompts R100) and changed no
    setting and no prompt text: the guard must pass it."""
    sha = "27eab47"
    probe = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--verify", "-q", f"{sha}^{{commit}}"],
        capture_output=True,
    )
    if probe.returncode != 0:
        pytest.skip("history before the package rename is not in this checkout")
    files, config_path = guard.changed_files(f"{sha}^", sha, cwd=ROOT)
    assert config_path == "src/a11y_stem/config.py"
    assert not any(f.endswith(".md") and "/prompts/" in f for f in files)  # R100 moves
    base = _git(ROOT, "show", f"{sha}^:{config_path}")
    head = _git(ROOT, "show", f"{sha}:{guard.CONFIG}")
    assert guard.changed_model_names(base, head) == []
    code, lines = guard.check(files, base, head)
    assert code == 0, lines
