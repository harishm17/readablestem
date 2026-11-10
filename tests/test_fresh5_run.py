"""The fresh5 parts of `scripts/realworld_run.py`: the registered arms and settings, offline."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


run = _script("realworld_run")


def test_the_two_readablestem_arms_differ_only_in_the_new_equation_settings() -> None:
    control = run.FRESH5_SETTINGS["fresh5-control"]
    candidate = run.FRESH5_SETTINGS["fresh5-candidate"]
    differ = {k for k in control if control[k] != candidate.get(k)}
    assert differ == {"EQUATION_GLYPH_CHECKS", "FLAG_POLICY", "EQUATION_GLYPH_RECONCILE"}
    assert all(candidate[k] == "true" for k in differ)
    assert all(control[k] == "false" for k in differ)
    for settings in (control, candidate):
        assert settings["FEATURE_TABLES"] == "true"
        assert settings["FEATURE_NOTATION_MEMORY"] == "true"
        assert settings["EQUATION_LINE_CONTEXT"] == "true"
        assert settings["FEATURE_PAGE_WITNESS"] == "false"
        assert settings["LAYOUT_DISPLAY_GROUPING"] == "false"
        assert settings["ANTHROPIC_MAIN_MODEL"] == "claude-opus-5-5"
        assert settings["ANTHROPIC_CHEAP_MODEL"] == "claude-haiku-5-5"


def test_registered_settings_refuse_a_stray_setting(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FEATURE_PAGE_MATH_AGREEMENT", "x")  # not a setting: ignored
    monkeypatch.setenv("MAX_USD_PER_JOB", "6")  # a setting the preset does not name
    with pytest.raises(SystemExit, match="MAX_USD_PER_JOB"):
        run.apply_registered_settings("fresh5-candidate")


def test_answers_shared_by_two_runs_are_counted_once(tmp_path) -> None:
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text('{"k": "1", "call": {"cost_usd": 0.5}}\n{"k": "2", "call": {"cost_usd": 0.25}}\n')
    b.write_text('{"k": "1", "call": {"cost_usd": 0.5}}\n{"k": "3", "call": {"cost_usd": 1.0}}\n')
    assert run.answers_spent([a, b, tmp_path / "missing.jsonl"]) == pytest.approx(1.75)
