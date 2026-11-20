"""The production profile (ops/production.profile.toml, WS0.9): it covers every setting of
config.py, railway.ts names each of its settings, its hash follows values and image facts and
not the file's order, and a production start that differs from it is named and refused only
once the profile is confirmed."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from readablestem.config import ConfigError, Settings
from readablestem.observability.logging import FIELDS_ATTR
from readablestem.ops import profile

ROOT = Path(__file__).resolve().parents[1]
PROD = {
    "env": "prod",
    "daily_spend_cap_usd": 10,
    "per_user_daily_page_quota": 300,
    "session_secret": "x" * 40,
    "google_client_id": "id",
    "google_client_secret": "secret",
    "cron_token": "t" * 40,
    "contact_email": "a@example.org",
    "base_url": "https://example.org",
    "database_url": "postgresql+psycopg://u:p@h/db",
    "anthropic_api_key": "sk-a",
}


def test_every_setting_is_in_the_profile_or_named_as_operations_and_railway_names_each():
    shipped = profile.load()
    fields = set(Settings.model_fields)
    assert set(shipped.settings) | shipped.operations == fields
    assert not set(shipped.settings) & shipped.operations
    assert shipped.settings.keys() == shipped.sources.keys()
    assert all((ROOT / path).exists() for path in shipped.evals.values() if path != "none")
    declared = set(re.findall(r'"([A-Z][A-Z0-9_]+)"', (ROOT / ".railway/railway.ts").read_text()))
    assert {name.upper() for name in shipped.settings} <= declared
    # Until the owner has read the dashboard, the values are the repository's own statements.
    assert shipped.confirmed is False
    assert all(shipped.image_facts[fact] == profile.UNKNOWN for fact in profile.IMAGE_FACTS)


def test_the_hash_follows_values_and_image_facts_and_not_the_order(tmp_path):
    text = profile.PROFILE_PATH.read_text(encoding="utf-8")
    shipped = profile.parse(text)
    lines = text.splitlines(keepends=True)
    start = lines.index("[settings]\n") + 1
    end = lines.index("\n", start)
    reordered = lines[:start] + lines[start:end][::-1] + lines[end:]
    assert profile.parse("".join(reordered)).sha256 == shipped.sha256
    changed = text.replace("FEATURE_TABLES = { value = true", "FEATURE_TABLES = { value = false")
    assert profile.parse(changed).sha256 != shipped.sha256

    # The build writes the image facts; a program they name counts, unknown does not.
    copy = tmp_path / "production.profile.toml"
    copy.write_text(text, encoding="utf-8")
    under = Settings(_env_file=None).production_profile()  # type: ignore[call-arg]
    assert profile.load(copy).counted_off(under) == ["FEATURE_LATEX_SOURCE", "OFFICE_INPUT_ENABLED"]
    facts = dict.fromkeys(profile.IMAGE_FACTS, profile.ABSENT) | {"latexml": "0.8.8"}
    profile.write_image_facts(copy, facts)
    built = profile.load(copy)
    assert built.sha256 != shipped.sha256 and built.settings == shipped.settings
    assert built.counted_off(under) == ["OFFICE_INPUT_ENABLED"]


@pytest.mark.parametrize("confirmed", [False, True])
def test_a_production_start_that_differs_from_the_profile(confirmed, tmp_path, caplog):
    text = profile.PROFILE_PATH.read_text(encoding="utf-8")
    copy = tmp_path / "production.profile.toml"
    copy.write_text(
        text.replace("confirmed = false", f"confirmed = {str(confirmed).lower()}"),
        encoding="utf-8",
    )
    wanted = profile.load(copy)
    production = Settings(_env_file=None, **PROD).production_profile()  # type: ignore[call-arg]
    production.check_production(wanted)  # the profile's own settings start either way

    differing = production.model_copy(update={"feature_tables": False})
    caplog.set_level(logging.WARNING, logger="readablestem.ops.profile")
    if confirmed:
        with pytest.raises(ConfigError, match="differ from the production profile: FEATURE_TABLES"):
            differing.check_production(wanted)
    else:
        differing.check_production(wanted)  # a wrong guess in the file must not stop production
    (line,) = [r for r in caplog.records if r.getMessage() == "profile_mismatch"]
    # The setting's name, never its value.
    assert getattr(line, FIELDS_ATTR) == {
        "setting": "FEATURE_TABLES",
        "profile": "production",
        "confirmed": confirmed,
    }
