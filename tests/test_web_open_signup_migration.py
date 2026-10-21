"""The open sign-up migration (`a6c2e9f4b713`) on SQLite with real rows, up and down.

(The Postgres variant is in test_web_postgres.py.) The migration is additive with server
defaults, so every account that exists before it is `invited`, the spend and quota rows keep
their numbers, and a release from before it can still insert rows after it ran.
"""

from __future__ import annotations

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from readablestem.web import db
from readablestem.web.database import alembic_config

BEFORE = "d3f8a1c5b920"
REVISION = "a6c2e9f4b713"


def migrate(url: str, revision: str, *, down: bool = False) -> None:
    config = alembic_config(url)
    config.attributes["configure_logger"] = False  # keep pytest's logging setup intact
    (command.downgrade if down else command.upgrade)(config, revision)


def columns(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def seed_before(url: str) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for n, (email, admin) in enumerate((("a@example.org", True), ("b@example.org", False))):
                conn.execute(
                    text(
                        "INSERT INTO users (id, email, is_allowed, is_admin, created_at) "
                        f"VALUES ('u{n}', '{email}', true, {str(admin).lower()}, "
                        "'2026-10-08 00:00:00')"
                    )
                )
            conn.execute(
                text(
                    "INSERT INTO users (id, email, is_allowed, is_admin, created_at) "
                    "VALUES ('u9', 'removed@example.org', false, false, '2026-10-08 00:00:00')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO spend_days (day, spent_usd, held_usd) "
                    "VALUES ('2026-10-20', 1.5, 0.25)"
                )
            )
            conn.execute(
                text("INSERT INTO quota_days (user_id, day, pages) VALUES ('u0', '2026-10-20', 7)")
            )
    finally:
        engine.dispose()


def test_existing_accounts_become_invited_and_existing_numbers_are_kept(tmp_path):
    url = f"sqlite:///{tmp_path}/open.db"
    migrate(url, BEFORE)
    seed_before(url)
    migrate(url, "head")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            tiers = dict(conn.execute(text("SELECT email, access_tier FROM users")).all())
            assert set(tiers.values()) == {"invited"} and len(tiers) == 3  # the removed one too
            spend = conn.execute(
                text("SELECT spent_usd, held_usd, open_spent_usd, open_held_usd FROM spend_days")
            ).one()
            assert tuple(spend) == (1.5, 0.25, 0.0, 0.0)
            quota = conn.execute(text("SELECT pages, rereads FROM quota_days")).one()
            assert tuple(quota) == (7, 0)
            assert conn.execute(text("SELECT count(*) FROM signup_days")).scalar_one() == 0
            assert compare_metadata(MigrationContext.configure(conn), db.Base.metadata) == []
    finally:
        engine.dispose()


def test_an_older_release_can_still_write_after_the_migration(tmp_path):
    """Traffic rollback between `migrate` and `service` (docs/deploy.md): the old code inserts
    users, spend days and quota rows without the new columns; the defaults fill them in."""
    url = f"sqlite:///{tmp_path}/rollback.db"
    migrate(url, "head")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, email, is_allowed, is_admin, created_at) "
                    "VALUES ('u1', 'old@example.org', true, false, '2026-10-21 00:00:00')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO spend_days (day, spent_usd, held_usd) VALUES ('2026-10-21', 0, 0)"
                )
            )
            conn.execute(
                text("INSERT INTO quota_days (user_id, day, pages) VALUES ('u1', '2026-10-21', 3)")
            )
            assert conn.execute(text("SELECT access_tier FROM users")).scalar_one() == "invited"
            assert conn.execute(text("SELECT open_held_usd FROM spend_days")).scalar_one() == 0
            assert conn.execute(text("SELECT rereads FROM quota_days")).scalar_one() == 0
    finally:
        engine.dispose()


def test_the_migration_downgrades_and_upgrades_again(tmp_path):
    url = f"sqlite:///{tmp_path}/roundtrip.db"
    migrate(url, "head")
    migrate(url, BEFORE, down=True)
    engine = create_engine(url)
    try:
        assert "access_tier" not in columns(engine, "users")
        assert not columns(engine, "spend_days") & {"open_spent_usd", "open_held_usd"}
        assert "rereads" not in columns(engine, "quota_days")
        tables = set(inspect(engine).get_table_names())
        assert not tables & {"signup_days", "spend_holds", "deleted_accounts"}
    finally:
        engine.dispose()
    migrate(url, "head")
    engine = create_engine(url)
    try:
        assert "access_tier" in columns(engine, "users")
        assert {"open_spent_usd", "open_held_usd"} <= columns(engine, "spend_days")
        assert "rereads" in columns(engine, "quota_days")
        tables = set(inspect(engine).get_table_names())
        assert tables >= {"signup_days", "spend_holds", "deleted_accounts"}
        with engine.connect() as conn:  # both start empty
            assert conn.execute(text("SELECT count(*) FROM spend_holds")).scalar_one() == 0
            assert conn.execute(text("SELECT count(*) FROM deleted_accounts")).scalar_one() == 0
        with engine.connect() as conn:
            assert compare_metadata(MigrationContext.configure(conn), db.Base.metadata) == []
    finally:
        engine.dispose()
