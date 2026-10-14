"""CLI commands: a11y-stem ops aggregate|purge|cron|launch-check|backup-check|restore-drill|
report|export-opted-in|decisions-canary|claude-canary.

The same tasks the web app runs on Cloud Scheduler's hourly call, for running by hand or
from a Cloud Run job. Output is counts only, as `key=value` pairs on one line; database
errors are reported by type, because their messages can include connection details.
`backup-check` and `restore-drill` (ops/backup.py) say whether a database copy can run the
app and whether dump-and-restore works; they print table names and counts only.
`decisions-canary` is the Decisions API drift canary the hourly cron runs once a day
(ops/canary.py); by hand it needs no database, only a key and `--max-usd`.
`claude-canary` is the same for the Claude second check (ops/claude_canary.py).
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
        help="list what is unfinished before real users: draft pages, demo, eval baseline, "
        "production settings and spend limits",
    ).set_defaults(func=_cmd_launch_check)
    sub.add_parser(
        "backup-check",
        help="read DATABASE_URL (a restored copy) and say whether the app could run on it: "
        "schema at head, tables present, an admin, how fresh the data is (exit 1 if not)",
    ).set_defaults(func=_cmd_backup_check)
    drill = sub.add_parser(
        "restore-drill",
        help="pg_dump DATABASE_URL, pg_restore into a scratch database on the same server, "
        "check it and drop it (needs the Postgres client tools and CREATE DATABASE)",
    )
    drill.add_argument("--pg-bin", type=Path, help="directory with pg_dump and pg_restore")
    drill.set_defaults(func=_cmd_restore_drill)
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
    canary = sub.add_parser(
        "decisions-canary",
        help="send the fixed Decisions API canary requests and compare with the recorded "
        "answers (about $0.001; exit 1 on drift)",
    )
    canary.add_argument(
        "--max-usd", type=_positive_usd, required=True, help="hard spend cap for this run"
    )
    canary.add_argument(
        "--record",
        action="store_true",
        help="write today's answers as the expected file (after a deliberate change)",
    )
    canary.add_argument(
        "--expected", type=Path, help="expected-answers file (default: the committed one)"
    )
    canary.set_defaults(func=_cmd_decisions_canary)
    claude = sub.add_parser(
        "claude-canary",
        help="ask the Claude second check about the fixed synthetic cases with known answers "
        "(about $0.04; exit 1 on drift)",
    )
    claude.add_argument(
        "--max-usd", type=_positive_usd, required=True, help="hard spend cap for this run"
    )
    claude.set_defaults(func=_cmd_claude_canary)


def _positive_usd(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from None
    if not 0 < number < float("inf"):
        raise argparse.ArgumentTypeError(f"must be a positive amount, got {value}")
    return number


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
    from a11y_stem.config import get_settings
    from a11y_stem.ops.launch import launch_blockers

    items = launch_blockers(project_root(), settings=get_settings())
    for item in items:
        print(f"- {item}")
    print(f"launch_blockers={len(items)}")
    return 1 if items else 0


def _cmd_backup_check(args: argparse.Namespace) -> int:
    """Exit 1 if the database could not run the app (ops.backup.DatabaseCheck.problems)."""
    from a11y_stem.config import get_settings
    from a11y_stem.ops.backup import check_database
    from a11y_stem.web.db import make_engine

    try:
        check = check_database(make_engine(get_settings().database_url))
    except SQLAlchemyError as exc:
        return _db_error(exc)
    for line in check.lines():
        print(line)
    for problem in check.problems:
        print(f"problem: {problem}")
    print(f"backup_check={'ok' if check.ok else 'failed'}")
    return 0 if check.ok else 1


def _cmd_restore_drill(args: argparse.Namespace) -> int:
    from a11y_stem.config import get_settings
    from a11y_stem.ops.backup import DrillError, restore_drill

    try:
        result = restore_drill(get_settings().database_url, pg_bin=args.pg_bin)
    except DrillError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except SQLAlchemyError as exc:
        return _db_error(exc)
    for line in result.check.lines():
        print(line)
    print(f"dump_bytes={result.dump_bytes} seconds={result.seconds:.1f}")
    for problem in result.problems:
        print(f"problem: {problem}")
    print(f"restore_drill={'ok' if result.ok else 'failed'}")
    return 0 if result.ok else 1


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


def _cmd_claude_canary(args: argparse.Namespace) -> int:
    """Exit 0: the check got the known cases right (within the tolerated misses); 1: drift
    (also an ERROR log line); 2: could not run (no key, a case without an answer)."""
    from a11y_stem.config import ConfigError, get_settings
    from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
    from a11y_stem.models.claude_verifier import make_claude_verifier
    from a11y_stem.observability.logging import configure_logging
    from a11y_stem.ops import claude_canary

    settings = get_settings()
    configure_logging(settings)
    try:
        settings.require_anthropic_key()  # the key itself is never printed
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    budget = JobBudget(InMemorySpendGuard(cap_usd=args.max_usd), max_usd=args.max_usd, max_calls=0)
    result = claude_canary.run_canary(make_claude_verifier(settings, budget), settings)
    for case in result.missed:
        print(f"missed case={case}")
    for case, reason in result.failed:
        print(f"failed case={case} reason={reason}")
    claude_canary.report(result, source="cli")
    print(
        f"cases={result.cases} answered={result.answered} missed={len(result.missed)} "
        f"failed={len(result.failed)} cost_usd={result.cost_usd:.6f} status={result.status}"
    )
    return {"ok": 0, "drift": 1}.get(result.status, 2)


def _cmd_decisions_canary(args: argparse.Namespace) -> int:
    """Exit 0: no answer moved; 1: drift (also an ERROR log line); 2: could not compare
    (no key, a case without an answer, or expectations recorded for other requests)."""
    import json
    from datetime import UTC, datetime

    from a11y_stem.config import ConfigError, get_settings
    from a11y_stem.core.budget import InMemorySpendGuard, JobBudget
    from a11y_stem.models.decisions_client import DecisionsClient
    from a11y_stem.observability.logging import configure_logging
    from a11y_stem.ops import canary

    settings = get_settings()
    configure_logging(settings)
    try:
        settings.require_openai_key()  # the key itself is never printed
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    cases = canary.load_cases()
    # Dollars only: Decisions requests are not model calls (`core.budget`).
    budget = JobBudget(InMemorySpendGuard(cap_usd=args.max_usd), max_usd=args.max_usd, max_calls=0)
    path = args.expected or canary.EXPECTED_FILE
    expected = {"cases": {}} if args.record or not path.is_file() else canary.load_expected(path)
    result = canary.run_canary(
        DecisionsClient(settings, budget),
        model=settings.decisions_model,
        cases=cases,
        expected=expected,
    )
    for case, question, delta in result.drifted:
        print(f"drift case={case} question={question} delta={delta}")
    for case, outcome in result.failed:
        print(f"failed case={case} outcome={outcome}")
    if args.record:
        try:
            document = canary.expected_document(
                result, model=settings.decisions_model, day=datetime.now(UTC).date()
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        path.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"cases={result.cases} cost_usd={result.cost_usd:.6f} recorded={path.name}")
        return 0
    canary.report(result, source="cli")
    print(
        f"cases={result.cases} compared={result.compared} drifted={len(result.drifted)} "
        f"failed={len(result.failed)} stale={len(result.stale)} "
        f"cost_usd={result.cost_usd:.6f} status={result.status}"
    )
    return {"ok": 0, "drift": 1}.get(result.status, 2)
