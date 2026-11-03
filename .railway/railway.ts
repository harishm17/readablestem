// Railway project "readablestem": web (the app), cron (hourly purge), backup (daily pg_dump
// with a restore check), Postgres, and the two volumes. Imported from the live production
// project on 2026-10-21 with `railway config pull`; `railway config plan` reports no changes.
//
// Edit this file, run `railway config plan`, review, then `railway config apply`
// (docs/deploy-railway.md, "Changing the infrastructure"). Code still ships with
// `railway up --service web|cron`; this file never deploys code.
//
// Variables: the project is "managed", so a variable that exists in Railway but is not listed
// here shows up in the plan as a deletion. Every variable below is `preserve()`: its value
// stays whatever it is in Railway and never enters the repository (the app's secrets, and
// settings such as SIGNUP_MODE that are changed in the dashboard or with `railway variable set`).
// The cron service holds the same names; its values are references to the web service
// (`${{web.NAME}}`) and `preserve()` keeps them as they are.
//
// Deliberately not set, so the plan stays empty: build watch patterns (they make `railway up`
// skip builds), the builder (the Dockerfile at the repository root is used) and the restart
// policy (the platform default).
import { defineRailway, image, postgres, preserve, project, service, volume } from "railway/iac";

const preserved = (...names: string[]) => Object.fromEntries(names.map((name) => [name, preserve()]));

// The backup service's start command, kept readable here and passed to `bash -c` (the image
// postgres:18 cannot see files of this repository). Rules for editing it: no dollar-brace and
// no backtick (this is a template literal). tests/test_railway_config.py extracts it, runs
// `bash -n` and shellcheck on it, and runs it against stub pg_dump and psql programs.
//
// A dump is moved into /backups only after it has been proved: gzip reads it back, it ends
// with pg_dump's completion line, it restores into the scratch database restore_check with
// errors fatal, every public table of the live database exists in the restore, and the
// alembic version matches. Any failure removes the half-made file, prints
// backup_failed reason=..., and exits 1 (the run shows as failed). Only then does the backup
// record its heartbeat (ops_heartbeat, read by /healthz/ops).
const backupScript = String.raw`set -euo pipefail
d=$(date -u +%Y-%m-%d)
f=/backups/readablestem-$d.sql.gz
part=$f.part

fail() {
  echo "backup_failed reason=$1"
  rm -f "$part"
  # Best effort: do not leave a full copy of the data in the scratch database.
  psql -q "$admin" -c "DROP DATABASE IF EXISTS restore_check" > /dev/null 2>&1 || true
  exit 1
}

# The same server and options as $PGURL, another database name.
# shellcheck disable=SC2001
db_url() { echo "$PGURL" | sed "s#/[^/?]*\([?].*\)\{0,1\}\$#/$1\1#"; }
public_tables() { psql -tA "$1" -c "select tablename from pg_tables where schemaname='public' order by 1"; }
alembic_version() { psql -tA "$1" -c "select version_num from alembic_version"; }
admin=$(db_url postgres)
check=$(db_url restore_check)

# 1. Dump to a .part file; pipefail makes a failing pg_dump fail the pipeline.
pg_dump --no-owner --no-privileges "$PGURL" | gzip > "$part" || fail dump
# 2. The file reads back as gzip.
gzip -t "$part" || fail gzip_test
# 3. pg_dump finished: its last lines say so (newer versions add a line after it).
tail_lines=$(gunzip -c "$part" | tail -n 10) || fail dump_read
case "$tail_lines" in
  *"-- PostgreSQL database dump complete"*) ;;
  *) fail dump_incomplete ;;
esac
# 4. It restores into a scratch database without a single error.
psql -q "$admin" -c "DROP DATABASE IF EXISTS restore_check" || fail restore_prepare
psql -q "$admin" -c "CREATE DATABASE restore_check" || fail restore_prepare
gunzip -c "$part" | psql -q -v ON_ERROR_STOP=1 "$check" > /dev/null || fail restore
# 5. Every public table of the live database is in the restore, at the same schema version.
live_tables=$(public_tables "$PGURL") || fail read_live
restored_tables=$(public_tables "$check") || fail read_restore
[ -n "$live_tables" ] || fail no_tables
missing=$(printf '%s\n' "$live_tables" | grep -vxF -f <(printf '%s\n' "$restored_tables") || true)
[ -z "$missing" ] || fail missing_tables
live_version=$(alembic_version "$PGURL") || fail read_live
restored_version=$(alembic_version "$check") || fail read_restore
if [ -z "$live_version" ] || [ "$live_version" != "$restored_version" ]; then fail alembic_mismatch; fi
tables=$(printf '%s\n' "$live_tables" | wc -l)

# Proved: put the dump in place.
mv "$part" "$f" || fail move
# Cleanup is not a reason to lose a verified dump: warn and go on (the next run drops it).
psql -q "$admin" -c "DROP DATABASE IF EXISTS restore_check" || echo "backup_warning reason=restore_cleanup"
echo "backup_ok file=$f tables=$tables alembic=$live_version"

# Tell /healthz/ops. The dump stays; a failure here is a failed run.
psql -q "$PGURL" -c "insert into ops_heartbeat (name, last_ok_at, detail) values ('backup', now(), 'tables=$tables alembic=$live_version') on conflict (name) do update set last_ok_at = excluded.last_ok_at, detail = excluded.detail" || { echo "backup_failed reason=heartbeat"; exit 1; }

# Keep five days; remove leftovers of runs that died before they could clean up.
find /backups -name 'readablestem-*.sql.gz' -mtime +5 -delete
find /backups -name 'readablestem-*.sql.gz.part' -mtime +1 -delete
ls -l /backups
`;

const shellQuote = (text: string) => "'" + text.replace(/'/g, "'\\''") + "'";

// Settings shared by the web and cron services (cron has no PORT).
// DECISIONS_CANARY, DECISIONS_EQUATION_SHADOW and DECISIONS_FIGURE_VERIFY do nothing while
// MODEL_PROVIDER=anthropic: they only control OpenAI's Decisions API check, which is skipped
// with Claude (`models.verifier.any_check_enabled`). They are kept so the plan stays empty;
// removing them is an infrastructure change for a separate PR.
const appVariables = [
  "ADMIN_EMAILS",
  "ANTHROPIC_API_KEY",
  "BASE_URL",
  "CONTACT_EMAIL",
  "CRON_TOKEN",
  "DAILY_SPEND_CAP_USD",
  "DATABASE_URL",
  "DECISIONS_CANARY",
  "DECISIONS_EQUATION_SHADOW",
  "DECISIONS_FIGURE_VERIFY",
  "ENV",
  "FEATURE_COLOUR",
  "FEATURE_LONG_DESCRIPTIONS",
  "FEATURE_NOTATION_MEMORY",
  "FEATURE_TABLES",
  "GOOGLE_CLIENT_ID",
  "GOOGLE_CLIENT_SECRET",
  "MAX_NEW_ACCOUNTS_PER_DAY",
  "MODEL_PROVIDER",
  "OPEN_TIER_DAILY_PAGE_QUOTA",
  "OPEN_TIER_DAILY_REREADS",
  "OPEN_TIER_DAILY_SPEND_CAP_USD",
  "PER_USER_DAILY_PAGE_QUOTA",
  "SESSION_SECRET",
  "SIGNUP_MODE",
  "TRACING_ENABLED",
  "TRUSTED_PROXY_HOPS",
];

// Settings only the web service holds (the image-page switch and the per-page and per-job
// spend caps added 2026-10-22; the worker thread runs inside web). MS_* are the Microsoft
// sign-in settings (docs/deploy-railway.md, "Microsoft sign-in on Railway"): declared before
// they have values so a later `railway variable set` is not deleted by the next apply; the
// app treats an unset variable as "Microsoft sign-in off", and a half-set group refuses to
// start. Only web serves sign-in, so cron does not hold them.
const webOnlyVariables = [
  "EQUATION_GLYPH_CHECKS",
  "EQUATION_LINE_CONTEXT",
  "FEATURE_IMAGE_PAGES",
  "MAX_USD_PER_JOB",
  "MAX_USD_PER_PAGE",
  "MS_ALLOWED_TENANTS",
  "MS_CLIENT_ID",
  "MS_CLIENT_SECRET",
  "PORT",
];

export default defineRailway(() => {
  const Postgres = postgres("Postgres", { region: "iad" });
  Postgres.networking = { privateNetworkEndpoint: "postgres" };
  const postgresVolume = volume("postgres-volume", {
    alerts: { usage: { "100": {}, "80": {}, "95": {} } },
    allowOnlineResize: true,
    region: "iad",
    sizeMB: 5000,
  });
  // Holds the daily dumps, mounted on the backup service at /backups.
  const backupVolume = volume("backup-volume", {
    alerts: { usage: { "100": {}, "80": {}, "95": {} } },
    allowOnlineResize: true,
    region: "iad",
    sizeMB: 5000,
  });

  // The web app and its worker thread. Migrates before each deploy; one replica only (rate
  // limits and the upload gate are per process). The image's CMD serves on PORT 8080.
  const web = service("web", {
    build: { buildEnvironment: "V3", builder: "RAILPACK" },
    healthcheck: "/healthz",
    healthcheckTimeout: 300,
    // SIGTERM to SIGKILL on a deploy. The shutdown needs about 8 s: uvicorn drains for up to
    // 5 s (GRACEFUL_SHUTDOWN_S), then the worker stop waits up to 3 s (WORKER_STOP_S).
    deploy: { drainingSeconds: 10 },
    preDeploy: "readablestem db upgrade",
    replicas: { iad: 1 },
    domains: ["readablestem.com", "www.readablestem.com"],
    env: preserved(...appVariables, ...webOnlyVariables),
  });

  // Hourly at minute 7: aggregate finished jobs, then purge past retention. No pre-deploy
  // command (two services migrating at once would race) and no health check.
  const cron = service("cron", {
    build: { buildEnvironment: "V3", builder: "RAILPACK" },
    start: "readablestem ops cron",
    replicas: { iad: 1 },
    deploy: { cronSchedule: "7 * * * *", restartPolicyType: "NEVER" },
    env: preserved(...appVariables),
  });

  // Daily at 08:17 UTC: pg_dump to /backups, restore into a scratch database to prove it, keep
  // the dump only if the proof holds, record the heartbeat (`backupScript` above),
  // delete dumps older than 5 days. PGURL is a reference to the Postgres service.
  const backup = service("backup", {
    source: image("postgres:18"),
    build: { buildEnvironment: "V3", builder: "RAILPACK" },
    start: "bash -c " + shellQuote(backupScript),
    replicas: { iad: 1 },
    deploy: { cronSchedule: "17 8 * * *", restartPolicyType: "NEVER" },
    volumeMounts: { "/backups": backupVolume },
    env: preserved("PGURL"),
  });

  return project("readablestem", {
    variables: { managed: true },
    resources: [web, Postgres, cron, backup, postgresVolume, backupVolume],
  });
});
