"""What is sent to a model, stated from one list (`web.recipients.sent_material`).

An honesty claim, so the words are checked as a visitor reads them: each setting that sends a
kind of material, turned on alone, changes every page that says what is sent (the vendor line on
the upload page and the landing page, the privacy notice, the offices page, its summary and
drawing, the account deletion notes), and a production start is refused while a sending
setting is on that the rendered pages do not state with the day it was first stated. The
privacy notice changes with every setting that sends or keeps something new.
"""

from __future__ import annotations

import dataclasses
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from fixtures.web.support import ALICE, WebEnv, login, make_settings
from readablestem.config import ConfigError
from readablestem.ops import launch
from readablestem.web import offices, recipients
from readablestem.web.recipients import MATERIALS, html_text, notice_text, vendor_sentence

ROOT = Path(__file__).resolve().parents[1]
SWITCHES = [s for m in MATERIALS for s in m.switches]
ONLY_CUTOUTS = (
    "Pictures of the equations and figures in your file, with a little nearby text, go to "
    "Anthropic's Claude models to propose readings. They are not used to train models."
)
PROD = {
    "env": "prod",
    "dev_login_enabled": False,
    "database_url": "postgresql+psycopg://u:p@127.0.0.1:1/never",
    "base_url": "https://beta.example.org",
    "session_secret": "s" * 40,
    "google_client_id": "client-id",
    "google_client_secret": "client-secret",
    "openai_api_key": "sk-test",
    "anthropic_api_key": "sk-ant-test",
}


def places(env: WebEnv) -> dict[str, str]:
    """Every page that says what is sent, as text."""
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        pages = {
            "upload": client.get("/app").text.split('id="vendor-line"', 1)[1],
            "landing": client.get("/").text,
            "privacy": client.get("/privacy").text,
            "offices": client.get(offices.PAGE_PATH).text,
            "summary": client.get(offices.SUMMARY_PATH).text,
            "account": client.get("/account").text,
        }
    return {name: html_text(page) for name, page in pages.items()}


@pytest.mark.parametrize("switch", SWITCHES)
def test_each_sending_setting_moves_every_place_together(tmp_path, switch):
    base_env = WebEnv(tmp_path / "base", model_provider="anthropic")
    on_env = WebEnv(tmp_path / "on", model_provider="anthropic", **{switch: True})
    base, on = places(base_env), places(on_env)
    row = next(m for m in recipients.sent_material(on_env.settings) if switch in m.switches)
    line = vendor_sentence(on_env.settings)
    assert line != vendor_sentence(base_env.settings)
    for name in ("upload", "landing"):
        assert line in on[name], name
        assert ONLY_CUTOUTS not in on[name], name  # it no longer says only cut-outs go
    for name in ("privacy", "offices", "summary"):
        assert row.what in on[name] and row.what not in base[name], name
    # The one-line notes: the drawing's description and the account deletion notes.
    short = f"{row.short} " if row.short else ""
    assert short and short in on["offices"] and short not in base["offices"]
    assert short in on["account"].lower() and short not in base["account"].lower()
    for name, text in on.items():
        assert "never the whole PDF" not in text, name
    if row.whole_pages:
        assert "pictures of whole pages are" in on["summary"]
        assert "pictures of whole pages are" in on["offices"]


def test_the_scanned_pages_box_says_where_the_pictures_go(tmp_path):
    env = WebEnv(tmp_path, model_provider="anthropic", feature_image_pages=True)
    with env.running() as client:
        env.allow(ALICE)
        login(client, ALICE)
        page = html_text(client.get("/app").text)
    assert "A picture of each such page goes to Anthropic." in page


# ------------------------------------------------------------------ the production gate


def prod(tmp_path, **kw):
    return make_settings(tmp_path, **{**PROD, **kw})


def dated(monkeypatch, key: str, stated: date | None) -> None:
    rows = tuple(dataclasses.replace(m, stated=stated) if m.key == key else m for m in MATERIALS)
    monkeypatch.setattr(recipients, "MATERIALS", rows)


def silent_page(tmp_path, monkeypatch) -> None:
    """The real templates, with the privacy page's list of what is sent taken out."""
    folder = tmp_path / "templates"
    shutil.copytree(launch.TEMPLATES_DIR, folder)
    page = folder / "privacy.html"
    text = page.read_text(encoding="utf-8")
    page.write_text(text.replace("{{ m.what }} {{ m.stored }}", ""), encoding="utf-8")
    monkeypatch.setattr(launch, "TEMPLATES_DIR", folder)


@pytest.mark.parametrize(
    "case,match",
    [
        ("no sentence", "FEATURE_TABLES sends pictures of tables but the privacy notice does not"),
        ("no date", "FEATURE_PAGE_WITNESS sends .* has no date it was first stated"),
        ("future date", "FEATURE_TABLES sends .* dated .*, a day still to come"),
    ],
)
def test_production_refuses_a_sending_setting_the_pages_do_not_state(
    tmp_path, monkeypatch, case, match
):
    # The shipped settings start: every row they turn on is stated and dated.
    profile = prod(tmp_path, cron_token="c" * 40, daily_spend_cap_usd=5, contact_email="a@b.c")
    profile.production_profile().check_production()
    settings = prod(tmp_path, feature_tables=True)
    if case == "no sentence":
        silent_page(tmp_path, monkeypatch)
    elif case == "no date":
        settings = prod(tmp_path, feature_page_witness=True)
    else:
        dated(monkeypatch, "tables", date.today() + timedelta(days=2))
    with pytest.raises(ConfigError, match=f"production start refused: .*{match}"):
        settings.check_production()
    assert any(match.split(" ")[0] in item for item in launch.launch_blockers(settings=settings))


def test_production_refuses_a_storage_statement_that_does_not_match_its_setting(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(offices, "BACKUP_DAYS", 9)  # backups kept longer than the page says
    with pytest.raises(ConfigError, match="does not say 'kept for about 9 days'"):
        prod(tmp_path).check_production()


# ------------------------------------------------------------------ the privacy notice


FLAGS = [{s: True} for s in SWITCHES] + [
    {"feature_latex_source": True},
    {"analytics_enabled": True},
    {"events_enabled": False},
]


@pytest.mark.parametrize("flag", FLAGS, ids=lambda f: next(iter(f)))
def test_each_flag_that_sends_or_keeps_something_changes_the_privacy_page(tmp_path, flag):
    base = notice_text(make_settings(tmp_path))
    assert base is not None
    assert notice_text(make_settings(tmp_path, **flag)) != base


def test_the_privacy_page_says_what_the_review_outcome_notes_hold(tmp_path):
    text = notice_text(make_settings(tmp_path))
    assert text is not None
    assert (
        "what you did (kept, edited, replaced, rejected, removed or added)" in text
        and "Totals of these notes are read only when they come from at least 5 accounts and "
        "10 documents"
        in text
    )
    off = notice_text(make_settings(tmp_path, events_enabled=False))
    assert off is not None and "notes also record" not in off


# ------------------------------------------------------------------ the documents a buyer gets


def test_the_vendor_sheet_and_security_doc_name_every_sending_setting():
    for doc in ("docs/vendor-information.md", "docs/security.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        for switch in SWITCHES:
            assert switch.upper() in text, (doc, switch)
        assert "never the whole PDF" not in text, doc
