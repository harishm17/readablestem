"""The dev runner for the page witness experiment: arms and the environment it runs in."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load():
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("eq_dev_run", SCRIPTS / "eq_dev_run.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_environment_keeps_the_key_and_loses_every_setting(monkeypatch):
    module = load()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("FEATURE_PAGE_WITNESS", "true")
    monkeypatch.setenv("PAGE_WITNESS_MODEL", "9")
    monkeypatch.setenv("UNRELATED_VARIABLE", "x")
    removed = module.scrub_environment()
    import os

    assert "FEATURE_PAGE_WITNESS" in removed and "PAGE_WITNESS_MODEL" in removed
    assert os.environ["ANTHROPIC_API_KEY"] == "k"
    assert os.environ["UNRELATED_VARIABLE"] == "x"
    assert "FEATURE_PAGE_WITNESS" not in os.environ


def test_the_arms_differ_in_the_witness_only_and_start_from_earlier_answers():
    module = load()
    control, opus, sonnet = (module.ARMS[k] for k in ("control", "witness-opus", "witness-sonnet"))
    assert control[1] is False and opus[1] is True and sonnet[1] is True
    assert opus[2] == "claude-opus-5-5" and sonnet[2] == "claude-sonnet-5-5"
    assert len({control[0], opus[0], sonnet[0]}) == 3
