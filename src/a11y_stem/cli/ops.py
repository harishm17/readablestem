"""CLI commands: a11y-stem ops aggregate|purge|cron|launch-check|report|export-opted-in.

The same tasks the web app runs on Cloud Scheduler's hourly call, for running by hand or
from a Cloud Run job. Output is counts only, as `key=value` pairs on one line; database
errors are reported by type, because their messages can include connection details.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from a11y_stem.ops.retention import PurgeStats


def register(
    group: argparse._SubParsersAction, top: argparse._SubParsersAction | None = None
) -> None:
    """Add this module's subcommands to `group` (top-level). Called by cli.main."""
    ops = group.add_parser("ops", help="cron tasks: error aggregation and retention")
    sub = ops.add_subparsers(dest="ops_command", required=True)
    sub.add_parser(
        "aggregate", help="count finished jobs into daily error aggregates (idempotent)"
    ).set_defaults(func=_cmd_aggregate)
    sub.add_parser(
        "purge", help="delete jobs past their retention time, with uploads, crops and edits"
    ).set_defaults(func=_cmd_purge)
    sub.add_parser(
        "cron", help="aggregate, then purge (purge runs even if aggregation fails)"
    ).set_defaults(func=_cmd_cron)
    sub.add_parser(
        "launch-check",
        help="list what is unfinished before real users: draft pages, demo, eval baseline",
    ).set_defaults(func=_cmd_launch_check)
    report = sub.add_parser(
        "report", help="weekly error-analysis tables from the aggregates (no content)"
    )
    report.add_argument("--days", type=int, default=7, help="UTC days back, today included")
    report.set_defaults(func=_cmd_report)
    export = sub.add_parser(
        "export-opted-in",
        help="copy opted-in jobs (crops, results, review labels) for error analysis",
    )
    mode = export.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out", type=Path, help="write one directory per job here")
    mode.add_argument("--prune", type=Path, help="delete exported jobs past their retention date")
    export.set_defaults(func=_cmd_export)


def _factory() -> sessionmaker[Session]:
    from a11y_stem.config import get_settings
    from a11y_stem.observability.logging import configure_logging
    from a11y_stem.web.db import make_engine, make_sessionmaker

    settings = get_settings()
    configure_logging(settings)
    return make_sessionmaker(make_engine(settings.database_url))


def _db_error(exc: SQLAlchemyError) -> int:
    print(
        f"error: database error ({type(exc).__name__}); check DATABASE_URL and that the "
        "schema is migrated (a11y-stem db upgrade)",
        file=sys.stderr,
    )
    return 1


def _purge_line(stats: PurgeStats) -> str:
    return (
        f"jobs_deleted={stats.jobs_deleted} blobs_deleted={stats.blobs_deleted} "
        f"unaggregated_deleted={stats.unaggregated_deleted}"
    )


def _cmd_aggregate(args: argparse.Namespace) -> int:
    from a11y_stem.ops.aggregate import aggregate_finished

    try:
        jobs = aggregate_finished(_factory())
    except SQLAlchemyError as exc:
        return _db_error(exc)
    print(f"aggregated_jobs={jobs}")
    return 0


def _cmd_purge(args: argparse.Namespace) -> int:
    from a11y_stem.ops.retention import purge_expired

    try:
        stats = purge_expired(_factory(), source="cli")
    except SQLAlchemyError as exc:
        return _db_error(exc)
    print(_purge_line(stats))
    return 0


def _cmd_cron(args: argparse.Namespace) -> int:
    """Exit 1 if either half failed, after the purge has had its chance to run."""
    from a11y_stem.ops.retention import aggregate_and_purge

    try:
        aggregated, stats = aggregate_and_purge(_factory())
    except SQLAlchemyError as exc:
        return _db_error(exc)
    shown = "failed" if aggregated is None else str(aggregated)
    print(f"aggregated_jobs={shown} {_purge_line(stats)}")
    return 1 if aggregated is None else 0


def _cmd_launch_check(args: argparse.Namespace) -> int:
    """Exit 1 while anything is unfinished (ops.launch); no database needed."""
    from a11y_stem.cli.main import project_root
    from a11y_stem.ops.launch import launch_blockers

    items = launch_blockers(project_root())
    for item in items:
        print(f"- {item}")
    print(f"launch_blockers={len(items)}")
    return 1 if items else 0


def _cmd_report(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from a11y_stem.ops.report import weekly_report
    from a11y_stem.web.db import session_scope

    try:
        with session_scope(_factory()) as session:
            text = weekly_report(session, days=args.days, today=datetime.now(UTC).date())
    except SQLAlchemyError as exc:
        return _db_error(exc)
    print(text, end="")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    """Opted-in jobs only; prints counts, never content (ops.export)."""
    from datetime import UTC, datetime

    from a11y_stem.ops.export import export_opted_in, prune_exports
    from a11y_stem.web.db import session_scope

    now = datetime.now(UTC)
    if args.prune is not None:
        print(f"pruned_jobs={prune_exports(args.prune, now=now)}")
        return 0
    try:
        with session_scope(_factory()) as session:
            stats = export_opted_in(session, args.out, now=now)
    except SQLAlchemyError as exc:
        return _db_error(exc)
    print(f"jobs={stats.jobs} crops={stats.crops} labels={stats.labels}")
    print("delete them by their EXPIRES date: a11y-stem ops export-opted-in --prune DIR")
    return 0
