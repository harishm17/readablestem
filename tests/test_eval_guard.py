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


def test_added_and_removed_model_settings_count() -> None:
    added = CONFIG + '    extra_model: str = "x"\n'
    assert guard.changed_model_names(CONFIG, added) == ["extra_model"]
    assert guard.changed_model_names(added, CONFIG) == ["extra_model"]


def test_prompt_name_only_for_prompt_markdown() -> None:
    assert guard.prompt_name(PROMPT) == "equation"
    assert guard.prompt_name("src/readablestem/models/prompts/__init__.py") is None
    assert guard.prompt_name("docs/equation.md") is None
    assert guard.prompt_name("src/readablestem/models/prompts/sub/x.md") is None


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
    ok = "Fix typo\n\nEval-Unchanged: whitespace in a comment line of the prompt\n"
    code, lines = guard.check([PROMPT], "", "", commit_messages=["other", ok])
    assert code == 0 and "whitespace in a comment" in lines[0]
    assert guard.check([PROMPT], "", "", commit_messages=["Eval-Unchanged:\n"])[0] == 1
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
