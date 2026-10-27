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

// Settings shared by the web and cron services (cron has no PORT).
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
// spend caps added 2026-10-22; the worker thread runs inside web).
const webOnlyVariables = [
  "EQUATION_LINE_CONTEXT",
  "FEATURE_IMAGE_PAGES",
  "MAX_USD_PER_JOB",
  "MAX_USD_PER_PAGE",
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

  // Daily at 08:17 UTC: pg_dump to /backups, restore into a scratch database to prove it,
  // delete dumps older than 5 days. PGURL is a reference to the Postgres service.
  const backup = service("backup", {
    source: image("postgres:18"),
    build: { buildEnvironment: "V3", builder: "RAILPACK" },
    start: "sh -c 'set -eu\nd=$(date -u +%Y-%m-%d)\nf=/backups/readablestem-$d.sql.gz\npg_dump --no-owner --no-privileges \"$PGURL\" | gzip > \"$f.part\"\nmv \"$f.part\" \"$f\"\nadmin=$(echo \"$PGURL\" | sed '\\''s#/[^/?]*\\([?].*\\)\\{0,1\\}$#/postgres\\1#'\\'')\ncheck=$(echo \"$PGURL\" | sed '\\''s#/[^/?]*\\([?].*\\)\\{0,1\\}$#/restore_check\\1#'\\'')\npsql -q \"$admin\" -c \"DROP DATABASE IF EXISTS restore_check\"\npsql -q \"$admin\" -c \"CREATE DATABASE restore_check\"\ngunzip -c \"$f\" | psql -q -v ON_ERROR_STOP=1 \"$check\" > /dev/null\ntables=$(psql -tA \"$check\" -c \"select count(*) from information_schema.tables where table_schema='\\''public'\\''\")\nversion=$(psql -tA \"$check\" -c \"select version_num from alembic_version\")\npsql -q \"$admin\" -c \"DROP DATABASE restore_check\"\necho \"backup_ok file=$f tables=$tables alembic=$version\"\nfind /backups -name '\\''readablestem-*.sql.gz'\\'' -mtime +5 -delete\nls -l /backups\n'",
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
