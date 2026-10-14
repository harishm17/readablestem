"""CLI: `a11y-stem serve`, `a11y-stem users allow|deny|link|unlink|delete|list`,
`a11y-stem db upgrade`.

- serve: runs the web app with uvicorn (`create_app` as a factory, so settings are read
  at start-up, and `--reload` works).
- users: manages the sign-in allowlist in the database named by DATABASE_URL. In dev the
  tables are created on first use; in production the schema must be migrated first.
  `link` adds another Google address for an allowed instructor and `unlink` takes it away
  again (`web.linked_emails`). `deny` removes someone from the allowlist and stops their
  queued and running jobs;
  `delete` also erases them (a data-deletion request, `ops.retention.erase_user`): every
  job with everything derived from it (through `delete_jobs`, so counts are kept), their
  review edits, courses and notation, usage events, sign-in identities, daily page counts
  and their account row. Only content-free aggregates and daily spend remain.
- db upgrade: applies the Alembic migrations in migrations/ (`alembic upgrade head`).
"""

from __future__ import annotations

import argparse
import os
import sys

GRACEFUL_SHUTDOWN_S = 5


def register(
    group: argparse._SubParsersAction, top: argparse._SubParsersAction | None = None
) -> None:
    """Add this module's subcommands to `group` (top-level). Called by cli.main."""
    serve = group.add_parser("serve", help="run the web app (uvicorn)")
    serve.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    serve.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8000")),
        help="port (default: $PORT or 8000)",
    )
    serve.add_argument("--reload", action="store_true", help="restart on code changes (dev)")
    serve.set_defaults(func=_cmd_serve)

    users = group.add_parser("users", help="manage the sign-in allowlist").add_subparsers(
        dest="users_command", required=True
    )
    allow = users.add_parser("allow", help="allow an email address to sign in")
    allow.add_argument("email")
    allow.add_argument("--admin", action="store_true", help="also make them an admin")
    allow.set_defaults(func=_cmd_users_allow)
    deny = users.add_parser("deny", help="remove an email address from the allowlist")
    deny.add_argument("email")
    deny.set_defaults(func=_cmd_users_deny)
    link = users.add_parser(
        "link", help="let an allowed instructor also sign in with another Google address"
    )
    link.add_argument("email", help="the instructor's own address, already on the allowlist")
    link.add_argument("other", help="the other Google address")
    link.set_defaults(func=_cmd_users_link)
    unlink = users.add_parser("unlink", help="remove a linked Google address")
    unlink.add_argument("other", help="the linked address")
    unlink.set_defaults(func=_cmd_users_unlink)
    erase = users.add_parser(
        "delete",
        help="erase someone: their account, every document and everything made from it",
    )
    erase.add_argument("email")
    erase.add_argument(
        "--yes", action="store_true", help="confirm: this cannot be undone (backups age out)"
    )
    erase.set_defaults(func=_cmd_users_delete)
    users.add_parser("list", help="list allowed users").set_defaults(func=_cmd_users_list)

    db = group.add_parser("db", help="database schema").add_subparsers(
        dest="db_command", required=True
    )
    db.add_parser("upgrade", help="apply migrations (alembic upgrade head)").set_defaults(
        func=_cmd_db_upgrade
    )


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from a11y_stem.observability.logging import uvicorn_log_options

    uvicorn.run(
        "a11y_stem.web.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        proxy_headers=True,
        server_header=False,
        # Cloud Run stops the process 10 s after SIGTERM. uvicorn waits this long for open
        # connections (an htmx poll or a slow upload), then runs the app's shutdown, which
        # stops the worker and ends the running job's lease (web.app.WORKER_STOP_S).
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S,
        **uvicorn_log_options(),
    )
    return 0


def _factory():
    """Session factory for DATABASE_URL, with the schema prepared for the environment."""
    from a11y_stem.config import get_settings
    from a11y_stem.web.database import prepare_database
    from a11y_stem.web.db import make_engine, make_sessionmaker

    settings = get_settings()
    engine = make_engine(settings.database_url)
    prepare_database(engine, settings)
    return make_sessionmaker(engine)


def _cmd_users_allow(args: argparse.Namespace) -> int:
    from a11y_stem.config import ConfigError
    from a11y_stem.web.auth import normalise_email, set_allowed
    from a11y_stem.web.db import session_scope

    email = normalise_email(args.email)
    if email is None:
        return _error(f"not an email address: {args.email!r}")
    try:
        factory = _factory()
    except ConfigError as exc:
        return _error(str(exc))
    from a11y_stem.web.linked_emails import LinkRefused

    try:
        with session_scope(factory) as session:
            set_allowed(session, email, allowed=True, admin=True if args.admin else None)
    except LinkRefused as refused:
        return _error(refused.message)
    print(f"allowed {email}{' (admin)' if args.admin else ''}")
    return 0


def _cmd_users_link(args: argparse.Namespace) -> int:
    from a11y_stem.config import ConfigError
    from a11y_stem.web import linked_emails
    from a11y_stem.web.auth import normalise_email
    from a11y_stem.web.db import session_scope

    owner, other = normalise_email(args.email), normalise_email(args.other)
    if owner is None or other is None:
        return _error("both arguments must be email addresses")
    try:
        factory = _factory()
    except ConfigError as exc:
        return _error(str(exc))
    try:
        with session_scope(factory) as session:
            linked_emails.link(session, owner, other)
    except linked_emails.LinkRefused as refused:
        return _error(refused.message)
    print(f"{other} can now sign in as {owner}")
    return 0


def _cmd_users_unlink(args: argparse.Namespace) -> int:
    from a11y_stem.config import ConfigError
    from a11y_stem.web import linked_emails
    from a11y_stem.web.auth import normalise_email
    from a11y_stem.web.db import session_scope

    other = normalise_email(args.other)
    if other is None:
        return _error(f"not an email address: {args.other!r}")
    try:
        factory = _factory()
    except ConfigError as exc:
        return _error(str(exc))
    with session_scope(factory) as session:
        removed = linked_emails.unlink(session, other)
    if not removed:
        return _error(f"{other} is not a linked address")
    print(f"unlinked {other}")
    return 0


def _cmd_users_deny(args: argparse.Namespace) -> int:
    from a11y_stem.config import ConfigError
    from a11y_stem.web.auth import normalise_email, set_allowed
    from a11y_stem.web.db import session_scope

    email = normalise_email(args.email)
    if email is None:
        return _error(f"not an email address: {args.email!r}")
    try:
        factory = _factory()
    except ConfigError as exc:
        return _error(str(exc))
    with session_scope(factory) as session:
        user = set_allowed(session, email, allowed=False)
    if user is None:
        return _error(f"{email} is not a known user")
    print(f"removed {email} from the allowlist")
    return 0


def _cmd_users_delete(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from a11y_stem.config import ConfigError
    from a11y_stem.ops.retention import erase_user
    from a11y_stem.web.auth import normalise_email
    from a11y_stem.web.db import User, session_scope

    email = normalise_email(args.email)
    if email is None:
        return _error(f"not an email address: {args.email!r}")
    if not args.yes:
        return _error("this erases the account and every document; add --yes to confirm")
    try:
        factory = _factory()
    except ConfigError as exc:
        return _error(str(exc))
    with session_scope(factory) as session:
        user_id = session.scalar(select(User.id).where(User.email == email))
        if user_id is None:
            return _error(f"{email} is not a known user")
        stats = erase_user(session, user_id)
    print(f"deleted {email}: jobs_deleted={stats.jobs_deleted} blobs_deleted={stats.blobs_deleted}")
    return 0


def _cmd_users_list(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from a11y_stem.config import ConfigError
    from a11y_stem.web.db import User, session_scope

    try:
        factory = _factory()
    except ConfigError as exc:
        return _error(str(exc))
    with session_scope(factory) as session:
        users = session.scalars(
            select(User).where(User.is_allowed.is_(True)).order_by(User.email)
        ).all()
        rows = [(u.email, "admin" if u.is_admin else "user", u.last_login_at) for u in users]
    if not rows:
        print("no allowed users")
        return 0
    width = max(len(email) for email, _, _ in rows) + 2
    print(f"{'email':<{width}} {'role':<6} last sign-in")
    for email, role, last in rows:
        shown = last.strftime("%Y-%m-%d %H:%M") if last else "never"
        print(f"{email:<{width}} {role:<6} {shown}")
    return 0


def _cmd_db_upgrade(args: argparse.Namespace) -> int:
    from alembic import command

    from a11y_stem.config import ConfigError, get_settings
    from a11y_stem.web.database import alembic_config

    try:
        config = alembic_config(get_settings().database_url)
    except ConfigError as exc:
        return _error(str(exc))
    command.upgrade(config, "head")
    print("database schema is at the latest migration")
    return 0
