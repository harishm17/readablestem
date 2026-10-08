"""The documented local quick start works from a verbatim copy of `.env.example`.

docs/web.md says: copy `.env.example` to `.env`, allow yourself, serve. The example leaves
the money settings blank on purpose (Harish sets them), and a blank `NAME=` used to fail
validation of every optional number, so every command crashed before doing anything.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from a11y_stem.cli.main import main
from a11y_stem.config import Settings, get_settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def clean_env(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def test_env_example_loads_verbatim(clean_env):
    settings = Settings(_env_file=ROOT / ".env.example")  # type: ignore[call-arg]
    assert settings.daily_spend_cap_usd is None  # blank means "not set", not an error
    assert settings.per_user_daily_page_quota is None
    assert settings.openai_api_key is None
    assert settings.fake_models is False
    assert settings.max_usd_per_page > 0 and settings.max_model_calls_per_page > 0


def test_every_example_setting_is_a_real_setting():
    names = {
        line.split("=", 1)[0].strip().lower()
        for line in (ROOT / ".env.example").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert names <= set(Settings.model_fields), names - set(Settings.model_fields)


def test_quick_start_allow_command_works_from_a_copied_env_example(clean_env, tmp_path, capsys):
    shutil.copy(ROOT / ".env.example", tmp_path / ".env")
    clean_env.chdir(tmp_path)
    assert main(["users", "allow", "you@example.org", "--admin"]) == 0
    assert "allowed you@example.org (admin)" in capsys.readouterr().out
    assert (tmp_path / ".local" / "a11y-stem.db").is_file()
