"""CLI commands: readablestem ops aggregate|purge|cron|launch-check|backup-check|restore-drill|
report|storage-report|export-opted-in|decisions-canary|claude-canary|replay-erasures|
sandbox-probe|profile-check|image-facts|accuracy|lookup.

The same tasks the web app runs on its hourly cron call, for running by hand or as the
Railway `cron` service (`readablestem ops cron`). Output is counts only, as `key=value` pairs on
one line; database errors are reported by type, because their messages can include
connection details.
`backup-check` and `restore-drill` (ops/backup.py) say whether a database copy can run the
app and whether dump-and-restore works; they print table names and counts only.
`decisions-canary` is the Decisions API drift canary that the HTTP cron route runs once a
day (ops/canary.py; `ops cron` does not); by hand it needs no database, only a key and
`--max-usd`.
`claude-canary` is the same for the Claude second check (ops/claude_canary.py). Nothing in
production calls either, because production runs `ops cron`: run them by hand.
`profile-check` compares the settings of this environment with the production profile
(ops/profile.py) and prints the names that differ, never a value; `image-facts --write` is run
by the image build to record what the image carries in the profile's `[image_facts]` table.
`accuracy` prints what reviewers did with each release's output (`web.events.outcome_cells`,
small counts withheld); `lookup` shows one account for support (`ops.support`). Neither prints
content.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from readablestem.ops.retention import PurgeStats
from readablestem.web import events


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
    replay = sub.add_parser(
        "replay-erasures",
        help="after restoring a backup: erase again every account in the erasure ledger "
        "(first merging --ledger-csv, the ledger exported from the database before the restore)",
    )
    replay.add_argument(
        "--ledger-csv",
        type=Path,
        help="CSV of user_hash,erased_at exported from the current database before the restore",
    )
    replay.set_defaults(func=_cmd_replay_erasures)
    sub.add_parser(
        "launch-check",
        help="list what is unfinished before real users: draft pages, demo, eval baseline, "
        "production settings and spend limits",
    ).set_defaults(func=_cmd_launch_check)
    probe = sub.add_parser(
        "sandbox-probe",
        help="check on this machine that the sandbox for uploaded TeX works (bubblewrap, user "
        "namespaces, no network, caps) and time a tiny LaTeXML run; prints PASS or FAIL",
    )
    probe.add_argument(
        "--require-latexml",
        action="store_true",
        help="fail when LaTeXML is not installed (the default only reports it)",
    )
    probe.set_defaults(func=_cmd_sandbox_probe)
    sub.add_parser(
        "profile-check",
        help="compare this environment's settings with the production profile; print the "
        "profile's hash and the names of the settings that differ (exit 1 if any)",
    ).set_defaults(func=_cmd_profile_check)
    facts = sub.add_parser(
        "image-facts",
        help="print what this machine carries (TeX, LaTeXML, pandoc, LibreOffice, lxml); "
        "with --write, record it in the shipped profile (the image build does this)",
    )
    facts.add_argument(
        "--write", action="store_true", help="write the facts into the profile's [image_facts]"
    )
    facts.set_defaults(func=_cmd_image_facts)
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
    stored = sub.add_parser(
        "storage-report",
        help="bytes stored per table and per finished document, beside the database's own "
        "size figure (no content)",
    )
    stored.add_argument(
        "--largest", type=int, default=10, help="list this many of the largest documents"
    )
    stored.set_defaults(func=_cmd_storage_report)
    steps = sub.add_parser(
        "step-report",
        help="model calls, tokens, dollars and seconds of finished jobs, by step and by "
        "stage (no content)",
    )
    steps.add_argument(
        "--job", action="append", default=[], metavar="ID", help="only this job (repeatable)"
    )
    steps.add_argument(
        "--days", type=float, default=7.0, help="jobs finished in the last DAYS days (default 7)"
    )
    steps.add_argument("--last", type=int, help="at most this many jobs, the newest first")
    steps.add_argument("--json", action="store_true", help="the report as JSON")
    steps.set_defaults(func=_cmd_step_report)
    sub.add_parser(
        "accuracy",
        help="false flags per true flag, silent misses found and decisions a page, by release, "
        "from review outcomes (counts only; a cell with too few accounts is withheld)",
    ).set_defaults(func=_cmd_accuracy)
    lookup = sub.add_parser(
        "lookup",
        help="one account for support: tier, state, today's use, spend, its documents' "
        "content-free facts and three review counts (no titles, no content)",
    )
    lookup.add_argument("email", help="the account's address")
    lookup.set_defaults(func=_cmd_lookup)
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
    from readablestem.config import get_settings
    from readablestem.observability.logging import configure_logging
    from readablestem.web.db import make_engine, make_sessionmaker

    settings = get_settings()
    configure_logging(settings)
    return make_sessionmaker(make_engine(settings.database_url))


def _db_error(exc: SQLAlchemyError) -> int:
    print(
        f"error: database error ({type(exc).__name__}); check DATABASE_URL and that the "
        "schema is migrated (readablestem db upgrade)",
        file=sys.stderr,
    )
    return 1


def _purge_line(stats: PurgeStats) -> str:
    return (
        f"jobs_deleted={stats.jobs_deleted} blobs_deleted={stats.blobs_deleted} "
        f"unaggregated_deleted={stats.unaggregated_deleted}"
    )


def _cmd_aggregate(args: argparse.Namespace) -> int:
    from readablestem.ops.aggregate import aggregate_finished

    try:
        jobs = aggregate_finished(_factory())
    except SQLAlchemyError as exc:
        return _db_error(exc)
    print(f"aggregated_jobs={jobs}")
    return 0


def _cmd_purge(args: argparse.Namespace) -> int:
    from readablestem.ops.retention import purge_expired

    try:
        stats = purge_expired(_factory(), source="cli")
    except SQLAlchemyError as exc:
        return _db_error(exc)
    print(_purge_line(stats))
    return 0


def _cmd_cron(args: argparse.Namespace) -> int:
    """Exit 1 if either half failed, after the purge has had its chance to run."""
    from readablestem.ops.retention import aggregate_and_purge

    try:
        factory = _factory()
        aggregated, stats = aggregate_and_purge(factory)
    except SQLAlchemyError as exc:
        return _db_error(exc)
    shown = "failed" if aggregated is None else str(aggregated)
    print(f"aggregated_jobs={shown} {_purge_line(stats)}")
    if aggregated is None:
        return 1
    # Both halves worked: the two daily canaries (once per UTC day each, fail-open), then tell
    # /healthz/ops. A failure to write it is a failed run.
    from readablestem.config import get_settings
    from readablestem.ops import canary, heartbeat
    from readablestem.web.db import session_scope
    from readablestem.web.spend import DbSpendGuard

    settings = get_settings()
    guard = DbSpendGuard(
        factory, settings.daily_spend_cap_usd, open_cap_usd=settings.open_tier_daily_spend_cap_usd
    )
    canaries = canary.run_daily(factory, settings, guard)
    note = f"decisions_canary={canaries['decisions']} claude_canary={canaries['claude']}"
    try:
        with session_scope(factory) as session:
            heartbeat.beat(
                session,
                heartbeat.CRON,
                f"aggregated={aggregated} jobs_deleted={stats.jobs_deleted} {note}",
            )
    except SQLAlchemyError as exc:
        return _db_error(exc)
    return 0


def _read_ledger_csv(path: Path) -> list[tuple[str, datetime]]:
    """Rows of `user_hash,erased_at` (no header needed; a header line is skipped)."""
    import csv
    from datetime import UTC

    rows: list[tuple[str, datetime]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for record in csv.reader(handle):
            if len(record) < 2 or record[0] == "user_hash":
                continue
            digest = record[0].strip()
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("a ledger row does not start with a SHA-256 hash")
            moment = datetime.fromisoformat(record[1].strip())
            rows.append((digest, moment if moment.tzinfo else moment.replace(tzinfo=UTC)))
    return rows


def _cmd_replay_erasures(args: argparse.Namespace) -> int:
    """Erase again the accounts the ledger names that exist in this database (a restored
    backup brings back accounts erased after it was made). Counts only."""
    from readablestem.ops.retention import import_ledger, replay_erasures
    from readablestem.web.db import session_scope

    try:
        imported_rows = _read_ledger_csv(args.ledger_csv) if args.ledger_csv else []
    except (OSError, ValueError) as exc:
        print(f"error: cannot read the ledger file ({type(exc).__name__})", file=sys.stderr)
        return 2
    try:
        with session_scope(_factory()) as session:
            imported = import_ledger(session, imported_rows)
            erased = replay_erasures(session)
    except SQLAlchemyError as exc:
        return _db_error(exc)
    print(f"ledger_imported={imported} accounts_erased={erased}")
    return 0


def _cmd_sandbox_probe(args: argparse.Namespace) -> int:
    """Exit 1 unless the sandbox for uploaded TeX works here (ops.sandbox_probe)."""
    from readablestem.ops.sandbox_probe import probe

    report = probe(require_latexml=args.require_latexml)
    for line in report.lines():
        print(line)
    return 0 if report.passed else 1


def _cmd_profile_check(args: argparse.Namespace) -> int:
    """Exit 1 when a behaviour setting differs from the production profile (names only)."""
    from readablestem.config import get_settings
    from readablestem.ops import profile

    shipped = profile.load()
    settings = get_settings()
    differing = shipped.differences(settings)
    print(
        f"profile={shipped.name} hash={shipped.short_hash} "
        f"confirmed={str(shipped.confirmed).lower()} differs={len(differing)}"
    )
    for name in differing:
        print(f"differs: {name}")
    for name in shipped.counted_off(settings):
        print(f"counted_off: {name} (the image facts lack its program)")
    return 1 if differing else 0


def _cmd_image_facts(args: argparse.Namespace) -> int:
    """Print what this machine carries; with --write, record it in the shipped profile."""
    from readablestem.ops import profile

    facts = profile.measure_image_facts()
    for key in profile.IMAGE_FACTS:
        print(f"{key}={facts[key]}")
    if args.write:
        # The TeX distribution's names, beside `source.support` (names only), so the stop
        # sentence can tell a TeX package from the instructor's own.
        from readablestem.source import support

        support.write_distribution_names(support.distribution_names())
        profile.write_image_facts(profile.PROFILE_PATH, facts)
        print(f"written: {profile.PROFILE_PATH} hash={profile.load().short_hash}")
    return 0


def _cmd_launch_check(args: argparse.Namespace) -> int:
    """Exit 1 while anything is unfinished (ops.launch); no database needed."""
    from readablestem.cli.main import project_root
    from readablestem.config import get_settings
    from readablestem.ops.launch import launch_blockers

    items = launch_blockers(project_root(), settings=get_settings())
    for item in items:
        print(f"- {item}")
    print(f"launch_blockers={len(items)}")
    return 1 if items else 0


def _cmd_backup_check(args: argparse.Namespace) -> int:
    """Exit 1 if the database could not run the app (ops.backup.DatabaseCheck.problems)."""
    from readablestem.config import get_settings
    from readablestem.ops.backup import check_database
    from readablestem.web.db import make_engine

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
    from readablestem.config import get_settings
    from readablestem.ops.backup import DrillError, restore_drill

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

    from readablestem.ops.report import weekly_report
    from readablestem.web.db import session_scope

    try:
        with session_scope(_factory()) as session:
            text = weekly_report(session, days=args.days, today=datetime.now(UTC).date())
    except SQLAlchemyError as exc:
        return _db_error(exc)
    print(text, end="")
    return 0


def _cmd_storage_report(args: argparse.Namespace) -> int:
    """Bytes stored per table and per finished document, from the database itself (counts
    and random ids only). The tables' total is set beside the database's own size figure:
    the rest is its catalogs and free pages."""
    import statistics

    from readablestem.web import storage
    from readablestem.web.db import session_scope

    try:
        with session_scope(_factory()) as session:
            tables, database = storage.table_bytes(session)
            jobs = storage.finished_job_bytes(session)
    except SQLAlchemyError as exc:
        return _db_error(exc)
    lines = ["Bytes stored per table (with its indexes and large values)"]
    for name, size in sorted(tables.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"  {name:<22}{size:>14,}")
    if tables:
        lines.append(f"  {'tables together':<22}{sum(tables.values()):>14,}")
    if database is not None:
        share = f" ({sum(tables.values()) / database:.1%} of it)" if tables and database else ""
        lines.append(f"  {'the database itself':<22}{database:>14,}{share}")
    lines.append("")
    lines.append(f"Finished documents: {len(jobs)}")
    if jobs:
        totals = sorted(job.total for job in jobs)
        lines.append(
            f"  bytes per document: median {int(statistics.median(totals)):,}, "
            f"p90 {totals[int(0.9 * (len(totals) - 1))]:,}, largest {totals[-1]:,}, "
            f"all together {sum(totals):,}"
        )
        for part in ("upload", "crops", "html", "result", "steps", "exports", "carry"):
            sizes = [getattr(job, part) for job in jobs]
            lines.append(
                f"  {part:<8} median {int(statistics.median(sizes)):>12,}  "
                f"all together {sum(sizes):>14,}"
            )
        lines.append(f"The {min(args.largest, len(jobs))} largest:")
        for job in sorted(jobs, key=lambda j: -j.total)[: args.largest]:
            lines.append(
                f"  {job.job_id}  {job.total:>12,}  (upload {job.upload:,}, crops {job.crops:,}, "
                f"html {job.html:,}, result {job.result:,}, steps {job.steps:,}, "
                f"exports {job.exports:,}, carry {job.carry:,})"
            )
    print("\n".join(lines))
    return 0


def _cmd_step_report(args: argparse.Namespace) -> int:
    """Prints codes, counts, seconds and dollars only (`ops.step_report`)."""
    import json
    from datetime import UTC, datetime, timedelta

    from readablestem.ops import step_report
    from readablestem.web import step_facts
    from readablestem.web.db import session_scope

    try:
        with session_scope(_factory()) as session:
            jobs = step_facts.measured(
                session,
                job_ids=args.job or None,
                since=None if args.job else datetime.now(UTC) - timedelta(days=args.days),
                last=args.last,
            )
    except SQLAlchemyError as exc:
        return _db_error(exc)
    made = step_report.report(jobs)
    print(json.dumps(made.as_dict(), indent=2) if args.json else step_report.render(made))
    return 0


def _cmd_lookup(args: argparse.Namespace) -> int:
    """One account for support (`ops.support`): exit 1 when nobody has that address."""
    from datetime import UTC, datetime

    from readablestem.ops import support
    from readablestem.web.db import session_scope
    from readablestem.web.logsafe import log_event

    try:
        with session_scope(_factory()) as session:
            found = support.lookup(session, args.email, today=datetime.now(UTC).date())
    except SQLAlchemyError as exc:
        return _db_error(exc)
    if found is None:
        print("Nobody has an account under that address.")
        return 1
    log_event(logging.getLogger(__name__), "support_lookup", account=found.pseudonym)
    for line in support.render(found):
        print(line)
    return 0


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _cell_lines(cell: events.OutcomeCell) -> list[str]:
    """One lane of one release in words. A withheld cell says so and gives no number."""
    name = "All lanes" if cell.lane == events.ALL_LANES else cell.lane
    if not cell.shown:
        more = (
            f"needs {_plural(cell.needs_accounts, 'more account')}"
            if cell.needs_accounts
            else f"needs {events.MIN_DOCUMENTS} documents, and no one account over "
            f"{events.MAX_SHARE:.0%} of it"
        )
        return [f"  {name}: too few to show ({more})"]
    lines = [
        f"  {name}: {_plural(cell.accounts, 'account')}, {_plural(cell.documents, 'document')}, "
        f"{cell.pages} pages, {cell.decisions} decisions"
    ]
    if cell.false_per_true is not None:
        flags = cell.false_flags + cell.true_flags
        lines.append(
            f"    {cell.false_per_true:.1f} false flags per true flag ({flags} flags; "
            f"of which kept in bulk: {cell.kept_in_bulk})"
        )
    else:
        lines.append("    false flags per true flag: no ratio (no doubt flag was true yet)")
    lines.append(
        f"    required by a rule: {cell.required}, of which {cell.required_changed} changed"
    )
    if cell.not_classified:
        lines.append(f"    not classified: {cell.not_classified} (in no ratio)")
    if cell.unflagged_per_100_pages is not None:
        lines.append(
            f"    found by a reviewer with no flag: {cell.unflagged_per_100_pages:.1f} per "
            f"100 pages, of which added by hand: {cell.added} (a floor, not the rate)"
        )
    if cell.decisions_per_page_median is not None:
        lines.append(
            f"    decisions per page, median over {_plural(cell.documents, 'document')}: "
            f"{cell.decisions_per_page_median:.2f}"
        )
    return lines


def _cmd_accuracy(args: argparse.Namespace) -> int:
    """Review outcomes by release (`web.events.outcome_cells`): the same small-count rule as
    every other reader, whoever runs it. Counts only; no account and no document."""
    from readablestem.config import get_settings
    from readablestem.web import events
    from readablestem.web.db import session_scope

    if not get_settings().events_enabled:
        print("Usage events are off, so there are no numbers from use.")
        return 0
    try:
        with session_scope(_factory()) as session:
            releases = events.outcome_releases(session)
            cells = {release: events.outcome_cells(session, release) for release in releases}
    except SQLAlchemyError as exc:
        return _db_error(exc)
    print(
        f"A cell needs {events.MIN_ACCOUNTS} accounts and {events.MIN_DOCUMENTS} documents, "
        f"and no one account over {events.MAX_SHARE:.0%} of it; admin accounts and sample "
        "documents are left out. Withheld cells still count in All lanes."
    )
    if not releases:
        print("No review outcomes have been recorded.")
    for release in releases:
        print(f"release {release}")
        for cell in cells[release]:
            for line in _cell_lines(cell):
                print(line)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    """Opted-in jobs only; prints counts, never content (ops.export)."""
    from datetime import UTC, datetime

    from readablestem.ops.export import export_opted_in, prune_exports
    from readablestem.web.db import session_scope

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
    print("delete them by their EXPIRES date: readablestem ops export-opted-in --prune DIR")
    return 0


def _cmd_claude_canary(args: argparse.Namespace) -> int:
    """Exit 0: the check got the known cases right (within the tolerated misses); 1: drift
    (also an ERROR log line); 2: could not run (no key, a case without an answer)."""
    from readablestem.config import ConfigError, get_settings
    from readablestem.core.budget import InMemorySpendGuard, JobBudget
    from readablestem.models.claude_verifier import make_claude_verifier
    from readablestem.observability.logging import configure_logging
    from readablestem.ops import claude_canary

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

    from readablestem.config import ConfigError, get_settings
    from readablestem.core.budget import InMemorySpendGuard, JobBudget
    from readablestem.models.decisions_client import DecisionsClient
    from readablestem.observability.logging import configure_logging
    from readablestem.ops import canary

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
