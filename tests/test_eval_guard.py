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


def test_prompt_change_without_report_fails_and_points_to_docs() -> None:
    code, lines = guard.check([PROMPT], "", "")
    text = "\n".join(lines)
    assert code == 1
    assert "latest-equations.json" in text and "latest-document.json" in text
    assert "How to rerun" in text and "eval-unchanged" in text and "Eval-Unchanged:" in text


def test_one_of_two_reports_is_not_enough() -> None:
    code, lines = guard.check([PROMPT, "data/eval/latest-equations.json"], "", "")
    assert code == 1
    assert "latest-document.json" in "\n".join(lines)
    assert "latest-equations.json:" not in "\n".join(lines)


def test_unlisted_prompt_needs_the_document_report() -> None:
    path = "src/readablestem/models/prompts/page_math.md"
    code, lines = guard.check([path], "", "")
    assert code == 1 and "latest-document.json" in "\n".join(lines)
    assert guard.check([path, "data/eval/latest-document.json"], "", "")[0] == 0


@pytest.mark.parametrize(
    "name",
    ["image_page", "image_page_crops", "image_page_notation", "math_reader", "math_reader_claude"],
)
def test_an_image_page_prompt_needs_the_image_pages_report(name: str) -> None:
    path = f"src/readablestem/models/prompts/{name}.md"
    code, lines = guard.check([path], "", "")
    text = "\n".join(lines)
    assert code == 1 and "latest-image-pages.json" in text and "latest-document.json" not in text
    assert guard.check([path, "data/eval/latest-image-pages.json"], "", "")[0] == 0


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


def test_main_model_constant_change_in_a_real_repository(repo: Path) -> None:
    (repo / CFG).write_text(CONFIG.replace("gpt-6.1-sol", "gpt-6.2-sol"))
    _git(repo, "commit", "-qam", "bump the OpenAI model")
    assert guard.main(["--base", "main"]) == 1
