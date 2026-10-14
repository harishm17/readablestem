#!/usr/bin/env bash
# Deploy ReadableSTEM to Google Cloud Run. Walkthrough: docs/deploy.md.
#
#   ./deploy/deploy.sh            # everything, in order (safe to re-run)
#   ./deploy/deploy.sh STEP ...   # only some steps: apis iam registry build database
#                                 # secrets migrate service cron monitoring budget
#
# Idempotent: every step checks what exists and only creates or updates what is missing,
# so a re-run after a failure (or a code change) picks up where it left off. Every value
# comes from environment variables (see deploy/deploy.env.example); nothing is hard-coded
# to Harish's project. Secret values are read from the environment or a silent prompt,
# piped to Secret Manager on stdin, and never printed.
#
# Use a dedicated Google Cloud project for this app: the budget alert, IAM grants and
# "delete the project" clean-up then cover exactly this app and nothing else.
#
# Commands checked against the gcloud reference (docs.cloud.google.com/sdk/gcloud/
# reference) on 2026-10-07. `gcloud beta` is used only for notification channels, which
# have no GA command.
#
# Bash 3.2 compatible (macOS default shell).

set -euo pipefail

# ------------------------------------------------------------------ helpers

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }

require() {
  local name
  for name in "$@"; do
    [ -n "${!name:-}" ] || die "$name is not set (see deploy/deploy.env.example)"
  done
}

gc() { gcloud --project="$PROJECT_ID" --quiet "$@"; }
exists() { gc "$@" >/dev/null 2>&1; }

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# ------------------------------------------------------------------ configuration

load_config() {
  command -v gcloud >/dev/null || die "gcloud is not installed (https://cloud.google.com/sdk)"
  command -v openssl >/dev/null || die "openssl is needed to generate secrets"

  require PROJECT_ID REGION BASE_URL DAILY_SPEND_CAP_USD PER_USER_DAILY_PAGE_QUOTA \
    GOOGLE_CLIENT_ID CONTACT_EMAIL ADMIN_EMAILS ALERT_EMAIL

  case "$BASE_URL" in
    https://?*) ;;
    *) die "BASE_URL must be https://<host> (the app refuses to start otherwise)" ;;
  esac
  [[ "$DAILY_SPEND_CAP_USD" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "DAILY_SPEND_CAP_USD must be a number"
  [[ "$PER_USER_DAILY_PAGE_QUOTA" =~ ^[0-9]+$ ]] || die "PER_USER_DAILY_PAGE_QUOTA must be a whole number"

  SERVICE=${SERVICE:-readablestem}
  AR_REPO=${AR_REPO:-readablestem}
  DB_MODE=${DB_MODE:-cloudsql}
  SQL_INSTANCE=${SQL_INSTANCE:-readablestem-db}
  SQL_TIER=${SQL_TIER:-db-f1-micro}
  SQL_DATABASE=${SQL_DATABASE:-a11y}
  SQL_USER=${SQL_USER:-a11y}
  # Daily backups kept. Each one holds every document present when it was taken, so a
  # purged document survives in backups until they roll over (docs/operations.md,
  # "Retention"). One keeps that to about a day and still allows a restore.
  SQL_RETAINED_BACKUPS=${SQL_RETAINED_BACKUPS:-1}
  # About 1.5x the fixed monthly cost of this setup (docs/deploy.md, "What it costs"),
  # so the alert means something; a Cloud Billing budget does not include OpenAI.
  BUDGET_USD=${BUDGET_USD:-90}
  CRON_SCHEDULE=${CRON_SCHEDULE:-0 * * * *}
  # Cloud Run sizing, from the measurements in docs/deploy.md ("Sizing"): a 30-page dense
  # document takes about 20 CPU-seconds and 0.5 GB at its peak, one job at a time.
  CPU=${CPU:-1}
  MEMORY=${MEMORY:-1Gi}
  MIN_INSTANCES=${MIN_INSTANCES:-1}
  MAX_INSTANCES=${MAX_INSTANCES:-2}
  CONCURRENCY=${CONCURRENCY:-20}
  REQUEST_TIMEOUT=${REQUEST_TIMEOUT:-300}
  [[ "$MIN_INSTANCES" =~ ^[1-9][0-9]*$ ]] ||
    die "MIN_INSTANCES must be at least 1 (the worker thread lives in the instance)"
  [[ "$MAX_INSTANCES" =~ ^[1-9][0-9]*$ ]] || die "MAX_INSTANCES must be a whole number"
  case "$DB_MODE" in
    cloudsql | external) ;;
    *) die "DB_MODE must be cloudsql or external" ;;
  esac
  if ! [[ "$SQL_RETAINED_BACKUPS" =~ ^[1-9][0-9]*$ ]] || [ "$SQL_RETAINED_BACKUPS" -gt 365 ]; then
    die "SQL_RETAINED_BACKUPS must be a whole number from 1 to 365"
  fi

  RUN_SA="$SERVICE-run@$PROJECT_ID.iam.gserviceaccount.com"
  if [ -z "${IMAGE_TAG:-}" ]; then
    IMAGE_TAG=$(git rev-parse --short=12 HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S)
    if ! git diff --quiet HEAD -- 2>/dev/null; then
      IMAGE_TAG="$IMAGE_TAG-dirty-$(date -u +%Y%m%d%H%M%S)"
    fi
  fi
  IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$AR_REPO/$SERVICE:$IMAGE_TAG"
  # Which company's models answer: openai (the default) or anthropic (docs/deploy.md,
  # "Using Claude"). Only the selected provider's key is a secret of the service.
  MODEL_PROVIDER=${MODEL_PROVIDER:-openai}
  case "$MODEL_PROVIDER" in
    openai) SECRETS="OPENAI_API_KEY=openai-api-key:latest" ;;
    anthropic) SECRETS="ANTHROPIC_API_KEY=anthropic-api-key:latest" ;;
    *) die "MODEL_PROVIDER must be openai or anthropic" ;;
  esac
  SECRETS="$SECRETS,SESSION_SECRET=session-secret:latest"
  SECRETS="$SECRETS,GOOGLE_CLIENT_SECRET=google-client-secret:latest,CRON_TOKEN=cron-token:latest"
  SECRETS="$SECRETS,DATABASE_URL=database-url:latest"
  # The content features are true/false; anything else the app would refuse at start.
  local flag
  for flag in FEATURE_TABLES FEATURE_LONG_DESCRIPTIONS FEATURE_COLOUR FEATURE_NOTATION_MEMORY \
    DECISIONS_FIGURE_VERIFY DECISIONS_EQUATION_SHADOW DECISIONS_CANARY EVENTS_ENABLED \
    FEEDBACK_QUESTIONS_ENABLED; do
    case "${!flag:-}" in
      "" | true | false) ;;
      *) die "$flag must be true or false" ;;
    esac
  done
  TRACING_ENABLED=${TRACING_ENABLED:-false}
  if [ "$TRACING_ENABLED" = true ]; then
    require LANGFUSE_PUBLIC_KEY
    SECRETS="$SECRETS,LANGFUSE_SECRET_KEY=langfuse-secret-key:latest"
  fi
  # Microsoft sign-in is optional: on when MS_CLIENT_ID is set, and then it needs the
  # directories allowed to use it (the app refuses to start with half of the settings).
  MS_CLIENT_ID=${MS_CLIENT_ID:-}
  if [ -n "$MS_CLIENT_ID" ]; then
    require MS_ALLOWED_TENANTS
    local tenant
    for tenant in ${MS_ALLOWED_TENANTS//,/ }; do
      [[ "$tenant" =~ ^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$ ]] ||
        die "MS_ALLOWED_TENANTS must list directory (tenant) ids, comma-separated"
    done
    SECRETS="$SECRETS,MS_CLIENT_SECRET=ms-client-secret:latest"
  fi
}

# The spend limits against the provider's worst-case reservation (ops/limits.py): the same
# check the app makes at start-up, run first so a bad number fails here and not as a service
# that never becomes ready. Skipped (with a note) where uv or the checkout is unavailable.
preflight_limits() {
  local out status=0
  command -v uv >/dev/null || { note "uv not found: spend limits not checked here"; return 0; }
  out=$(MODEL_PROVIDER="$MODEL_PROVIDER" uv run --quiet --no-sync python -m readablestem.ops.limits 2>&1) ||
    status=$?
  case "$status" in
    0) [ -z "$out" ] || printf '%s\n' "$out" | sed 's/^/    /' ;;
    3)
      printf '%s\n' "$out" | sed 's/^/    /' >&2
      die "the spend limits do not fit MODEL_PROVIDER=$MODEL_PROVIDER (docs/deploy.md, 'Spend limits')"
      ;;
    *) note "spend limits not checked (run 'uv sync' first): $status" ;;
  esac
}

sql_connection_name() {
  gc sql instances describe "$SQL_INSTANCE" --format='value(connectionName)'
}

# ------------------------------------------------------------------ steps

step_apis() {
  step "Enabling APIs"
  gc services enable run.googleapis.com artifactregistry.googleapis.com \
    cloudbuild.googleapis.com compute.googleapis.com secretmanager.googleapis.com \
    sqladmin.googleapis.com cloudscheduler.googleapis.com monitoring.googleapis.com \
    logging.googleapis.com billingbudgets.googleapis.com iam.googleapis.com
}

step_iam() {
  step "Service accounts"
  if exists iam service-accounts describe "$RUN_SA"; then
    note "$RUN_SA exists"
  else
    gc iam service-accounts create "$SERVICE-run" --display-name="ReadableSTEM Cloud Run"
  fi
  if [ "$DB_MODE" = cloudsql ]; then
    gc projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$RUN_SA" \
      --role=roles/cloudsql.client --condition=None >/dev/null
  fi
  # New projects build with the Compute Engine default service account; this role lets
  # it read the uploaded source, write logs and push to Artifact Registry.
  local number
  number=$(gc projects describe "$PROJECT_ID" --format='value(projectNumber)')
  gc projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$number-compute@developer.gserviceaccount.com" \
    --role=roles/cloudbuild.builds.builder --condition=None >/dev/null
  note "roles granted (re-granting an existing role is a no-op)"
}

step_registry() {
  step "Artifact Registry repository $AR_REPO"
  if exists artifacts repositories describe "$AR_REPO" --location="$REGION"; then
    note "exists"
  else
    gc artifacts repositories create "$AR_REPO" --repository-format=docker \
      --location="$REGION" --description="ReadableSTEM images"
  fi
}

step_build() {
  step "Building $IMAGE with Cloud Build"
  # The upload skips what .gitignore lists (.env, private-inputs/, .local/); the Docker
  # build then applies .dockerignore. --tag runs a plain `docker build` in Cloud Build's
  # docker 20.10 image, which may use the legacy builder: the Dockerfile must not use
  # BuildKit-only syntax (CI builds it with DOCKER_BUILDKIT=0 to check).
  gc builds submit --region="$REGION" --tag="$IMAGE" .
}

step_database() {
  if [ "$DB_MODE" = external ]; then
    step "Database: external Postgres (DATABASE_URL secret, see the secrets step)"
    return
  fi
  step "Cloud SQL instance $SQL_INSTANCE ($SQL_TIER)"
  local kept
  if exists sql instances describe "$SQL_INSTANCE"; then
    note "exists"
    # An instance created before this setting keeps Cloud SQL's default of 7 backups;
    # bring it down. Backup retention changes do not restart the instance.
    kept=$(gc sql instances describe "$SQL_INSTANCE" \
      --format='value(settings.backupConfiguration.backupRetentionSettings.retainedBackups)')
    if [ "$kept" != "$SQL_RETAINED_BACKUPS" ]; then
      gc sql instances patch "$SQL_INSTANCE" --retained-backups-count="$SQL_RETAINED_BACKUPS"
      note "daily backups kept: ${kept:-unknown} -> $SQL_RETAINED_BACKUPS"
    fi
  else
    # Shared-core tiers need the Enterprise edition; Postgres 16+ defaults to Enterprise Plus.
    # Point-in-time recovery stays off (the gcloud default for the Enterprise edition): its
    # transaction logs would keep deleted documents for days.
    note "creating (takes about ten minutes)"
    gc sql instances create "$SQL_INSTANCE" --database-version=POSTGRES_16 \
      --edition=enterprise --tier="$SQL_TIER" --region="$REGION" \
      --storage-size=10 --storage-auto-increase --availability-type=zonal \
      --backup-start-time=08:00 --retained-backups-count="$SQL_RETAINED_BACKUPS"
  fi
  if exists sql databases describe "$SQL_DATABASE" --instance="$SQL_INSTANCE"; then
    note "database $SQL_DATABASE exists"
  else
    gc sql databases create "$SQL_DATABASE" --instance="$SQL_INSTANCE"
  fi
}

has_version() {
  [ -n "$(gc secrets versions list "$1" --filter='state=ENABLED' --limit=1 \
    --format='value(name)' 2>/dev/null)" ]
}

add_version() { # name, value (value never echoed)
  printf '%s' "$2" | gc secrets versions add "$1" --data-file=- >/dev/null
  note "$1: new version added"
}

# ensure_secret NAME ENV_VAR MODE: MODE is "generate" (random hex) or "ask" (env or prompt).
ensure_secret() {
  local name=$1 var=$2 mode=$3 value=""
  exists secrets describe "$name" ||
    gc secrets create "$name" --replication-policy=automatic >/dev/null
  gc secrets add-iam-policy-binding "$name" --member="serviceAccount:$RUN_SA" \
    --role=roles/secretmanager.secretAccessor >/dev/null
  if has_version "$name"; then
    note "$name: has a version (kept; rotate with docs/deploy.md, 'Rotate a secret')"
    return
  fi
  if [ -n "${!var:-}" ]; then
    value=${!var}
  elif [ "$mode" = generate ]; then
    value=$(openssl rand -hex 32)
  elif [ -t 0 ]; then
    read -rsp "    value for $name ($var, input hidden): " value
    printf '\n'
  else
    die "$name has no version: export $var or run this script in a terminal"
  fi
  [ -n "$value" ] || die "empty value for $name"
  add_version "$name" "$value"
}

ensure_database_url() {
  local name=database-url value password connection
  exists secrets describe "$name" ||
    gc secrets create "$name" --replication-policy=automatic >/dev/null
  gc secrets add-iam-policy-binding "$name" --member="serviceAccount:$RUN_SA" \
    --role=roles/secretmanager.secretAccessor >/dev/null
  if has_version "$name"; then
    note "$name: has a version (kept)"
    return
  fi
  if [ "$DB_MODE" = external ]; then
    value=${EXTERNAL_DATABASE_URL:-}
    if [ -z "$value" ] && [ -t 0 ]; then
      read -rsp "    external DATABASE_URL (postgresql+psycopg://..., hidden): " value
      printf '\n'
    fi
    case "$value" in
      postgresql+psycopg://*) ;;
      *) die "EXTERNAL_DATABASE_URL must start with postgresql+psycopg://" ;;
    esac
  else
    # A new password for the app's database user, stored only in the secret. The value
    # appears in this machine's process list while gcloud runs (gcloud sql users create
    # has no stdin option); it is never printed.
    password=$(openssl rand -hex 24)
    if [ -n "$(gc sql users list --instance="$SQL_INSTANCE" --filter="name=$SQL_USER" \
      --format='value(name)')" ]; then
      gc sql users set-password "$SQL_USER" --instance="$SQL_INSTANCE" \
        --password="$password" >/dev/null
    else
      gc sql users create "$SQL_USER" --instance="$SQL_INSTANCE" \
        --password="$password" >/dev/null
    fi
    connection=$(sql_connection_name)
    value="postgresql+psycopg://$SQL_USER:$password@/$SQL_DATABASE?host=/cloudsql/$connection"
  fi
  add_version "$name" "$value"
}

step_secrets() {
  step "Secrets (Secret Manager)"
  if [ "$MODEL_PROVIDER" = anthropic ]; then
    ensure_secret anthropic-api-key ANTHROPIC_API_KEY ask
  else
    ensure_secret openai-api-key OPENAI_API_KEY ask
  fi
  ensure_secret google-client-secret GOOGLE_CLIENT_SECRET ask
  ensure_secret session-secret SESSION_SECRET generate
  ensure_secret cron-token CRON_TOKEN generate
  ensure_database_url
  if [ "$TRACING_ENABLED" = true ]; then
    ensure_secret langfuse-secret-key LANGFUSE_SECRET_KEY ask
  fi
  if [ -n "$MS_CLIENT_ID" ]; then
    ensure_secret ms-client-secret MS_CLIENT_SECRET ask
  fi
}

cloudsql_flag() { # $1: --set-cloudsql-instances flag form for services and jobs
  if [ "$DB_MODE" = cloudsql ]; then
    printf '%s=%s' "$1" "$(sql_connection_name)"
  fi
}

step_migrate() {
  step "Database migration (Cloud Run job $SERVICE-migrate: readablestem db upgrade)"
  local sql
  sql=$(cloudsql_flag --set-cloudsql-instances)
  # shellcheck disable=SC2086  # $sql is empty or one flag
  gc run jobs deploy "$SERVICE-migrate" --image="$IMAGE" --region="$REGION" \
    --service-account="$RUN_SA" --set-secrets=DATABASE_URL=database-url:latest \
    --command=readablestem --args=db,upgrade --max-retries=0 --task-timeout=10m $sql
  gc run jobs execute "$SERVICE-migrate" --region="$REGION" --wait
}

yaml_line() { # KEY VALUE -> KEY: 'VALUE' (single quotes doubled, as YAML requires)
  local escaped=${2//\'/\'\'}
  printf "%s: '%s'\n" "$1" "$escaped"
}

write_env_file() {
  local file="$TMP_DIR/env.yaml" name
  {
    yaml_line ENV prod
    yaml_line BASE_URL "$BASE_URL"
    yaml_line DAILY_SPEND_CAP_USD "$DAILY_SPEND_CAP_USD"
    yaml_line PER_USER_DAILY_PAGE_QUOTA "$PER_USER_DAILY_PAGE_QUOTA"
    yaml_line GOOGLE_CLIENT_ID "$GOOGLE_CLIENT_ID"
    yaml_line CONTACT_EMAIL "$CONTACT_EMAIL"
    yaml_line ADMIN_EMAILS "$ADMIN_EMAILS"
    yaml_line RUN_WORKER true
    yaml_line TRACING_ENABLED "$TRACING_ENABLED"
    if [ "$MODEL_PROVIDER" = anthropic ]; then
      yaml_line MODEL_PROVIDER anthropic
      for name in ANTHROPIC_WORKSPACE_ID ANTHROPIC_MAIN_MODEL ANTHROPIC_CHEAP_MODEL; do
        if [ -n "${!name:-}" ]; then yaml_line "$name" "${!name}"; fi
      done
    fi
    if [ -n "$MS_CLIENT_ID" ]; then
      yaml_line MS_CLIENT_ID "$MS_CLIENT_ID"
      yaml_line MS_ALLOWED_TENANTS "$MS_ALLOWED_TENANTS"
    fi
    # Content features (docs/deploy.md, "Content features"): the beta defaults are set in
    # deploy.env.example, and an unset name here leaves the app's own default (off).
    for name in FEATURE_TABLES FEATURE_LONG_DESCRIPTIONS FEATURE_COLOUR \
      FEATURE_NOTATION_MEMORY FEEDBACK_URL FEEDBACK_QUESTIONS_ENABLED EVENTS_ENABLED; do
      if [ -n "${!name:-}" ]; then yaml_line "$name" "${!name}"; fi
    done
    # The second check is OpenAI's: written out as off with Claude so it is plain in the
    # service's settings (the app ignores it for that provider either way).
    if [ "$MODEL_PROVIDER" = anthropic ]; then
      yaml_line DECISIONS_FIGURE_VERIFY false
      yaml_line DECISIONS_EQUATION_SHADOW false
      yaml_line DECISIONS_CANARY false
    else
      for name in DECISIONS_MODEL DECISIONS_TIMEOUT_S DECISIONS_FIGURE_VERIFY \
        DECISIONS_FIGURE_FLAG_BELOW DECISIONS_EQUATION_SHADOW DECISIONS_EQUATION_FLAG_AT \
        DECISIONS_CANARY; do
        if [ -n "${!name:-}" ]; then yaml_line "$name" "${!name}"; fi
      done
    fi
    for name in ANTHROPIC_EFFORT_MAIN ANTHROPIC_EFFORT_CHEAP ANTHROPIC_REFUSAL_FALLBACK \
      MODEL_TIMEOUT_S MODEL_MAX_RETRIES; do
      if [ -n "${!name:-}" ]; then yaml_line "$name" "${!name}"; fi
    done
    for name in MAX_USD_PER_JOB MAX_USD_PER_PAGE MAX_MODEL_CALLS_PER_JOB \
      MAX_MODEL_CALLS_PER_PAGE MAX_PAGES_PER_JOB MAX_UPLOAD_MB MAX_LARGE_UPLOAD_MB \
      MAX_SOURCE_PAGES MAX_CONCURRENT_UPLOADS \
      RETENTION_HOURS OPT_IN_RETENTION_DAYS WORKER_POLL_S SOURCE_URL LOG_LEVEL \
      LANGFUSE_PUBLIC_KEY LANGFUSE_HOST; do
      if [ -n "${!name:-}" ]; then yaml_line "$name" "${!name}"; fi
    done
  } >"$file"
  printf '%s' "$file"
}

step_service() {
  step "Cloud Run service $SERVICE"
  local env_file sql
  env_file=$(write_env_file)
  sql=$(cloudsql_flag --set-cloudsql-instances)
  # One instance always warm (the worker thread lives in it), at most two; CPU stays
  # allocated between requests so the worker keeps running.
  # shellcheck disable=SC2086  # $sql is empty or one flag
  gc run deploy "$SERVICE" --image="$IMAGE" --region="$REGION" \
    --service-account="$RUN_SA" --allow-unauthenticated --port=8080 \
    --cpu="$CPU" --memory="$MEMORY" --min-instances="$MIN_INSTANCES" \
    --max-instances="$MAX_INSTANCES" --no-cpu-throttling \
    --concurrency="$CONCURRENCY" --timeout="$REQUEST_TIMEOUT" \
    --startup-probe=httpGet.path=/healthz,httpGet.port=8080,periodSeconds=5,timeoutSeconds=3,failureThreshold=24 \
    --env-vars-file="$env_file" --set-secrets="$SECRETS" $sql
  note "URL: $(service_url)"
}

service_url() {
  gc run services describe "$SERVICE" --region="$REGION" --format='value(status.url)'
}

step_cron() {
  step "Cloud Scheduler: hourly aggregate-and-purge"
  local url token job="$SERVICE-cron"
  url="$(service_url)/internal/cron/aggregate-and-purge"
  token=$(gc secrets versions access latest --secret=cron-token)
  if exists scheduler jobs describe "$job" --location="$REGION"; then
    gc scheduler jobs update http "$job" --location="$REGION" --schedule="$CRON_SCHEDULE" \
      --time-zone=Etc/UTC --uri="$url" --http-method=post \
      --update-headers="X-Cron-Token=$token" --attempt-deadline=600s >/dev/null
    note "$job updated"
  else
    gc scheduler jobs create http "$job" --location="$REGION" --schedule="$CRON_SCHEDULE" \
      --time-zone=Etc/UTC --uri="$url" --http-method=post \
      --headers="X-Cron-Token=$token" --attempt-deadline=600s --max-retry-attempts=1 \
      >/dev/null
    note "$job created"
  fi
}

alert_channel() {
  local channel
  channel=$(gc beta monitoring channels list \
    --filter="type=\"email\" AND labels.email_address=\"$ALERT_EMAIL\"" \
    --format='value(name)' | head -n 1)
  if [ -z "$channel" ]; then
    channel=$(gc beta monitoring channels create --display-name="ReadableSTEM alerts" \
      --type=email --channel-labels="email_address=$ALERT_EMAIL" --format='value(name)')
  fi
  printf '%s' "$channel"
}

# ensure_log_metric NAME DESCRIPTION FILTER: a counter of matching log entries.
ensure_log_metric() {
  local name=$1 description=$2 filter=$3
  if exists logging metrics describe "$name"; then
    gc logging metrics update "$name" --description="$description" --log-filter="$filter" \
      >/dev/null
    note "log metric $name updated"
  else
    gc logging metrics create "$name" --description="$description" --log-filter="$filter" \
      >/dev/null
    note "log metric $name created"
  fi
}

# ensure_policy DISPLAY_NAME FILE: create the alert policy unless one has that name.
ensure_policy() {
  local display=$1 file=$2
  if [ -n "$(gc monitoring policies list --filter="displayName=\"$display\"" \
    --format='value(name)')" ]; then
    note "alert policy \"$display\" exists"
    return
  fi
  gc monitoring policies create --policy-from-file="$file" >/dev/null
  note "alert policy \"$display\" created"
}

step_monitoring() {
  local channel
  channel=$(alert_channel)
  uptime_alert "$channel"
  retention_alerts "$channel"
}

# The hourly cron carries the privacy promise (aggregate, then purge), and /healthz stays
# 200 when it breaks, so it gets alerts of its own (docs/operations.md, "When something
# goes wrong"):
# - no `cron_done` for 2 hours: the scheduler is not reaching the app (paused or deleted
#   job, 403 after a cron-token rotation, 500s). The worker's hourly purge backstop
#   keeps deleting meanwhile, and logs as `purge_done source=worker`, which is why this
#   watches `cron_done` and not `purge_done`;
# - any `cron_refused`, `aggregate_failed`, `purge_backstop_deleted` (the backstop found
#   overdue jobs) or `purge_backstop_failed` line;
# - any failed attempt of the Cloud Scheduler job itself.
retention_alerts() {
  local channel=$1 run_filter cron_job="$SERVICE-cron" file
  step "Alerts on the hourly aggregate-and-purge (email to $ALERT_EMAIL)"
  run_filter="resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\""
  ensure_log_metric "$SERVICE-cron-done" "Hourly aggregate-and-purge finished" \
    "$run_filter AND jsonPayload.message=\"cron_done\""
  ensure_log_metric "$SERVICE-cron-problems" "Cron refused or failed; purge backstop acted" \
    "$run_filter AND jsonPayload.message=(\"cron_refused\" OR \"aggregate_failed\" OR \"purge_backstop_deleted\" OR \"purge_backstop_failed\")"
  ensure_log_metric "$SERVICE-scheduler-errors" "Cloud Scheduler job $cron_job failed" \
    "resource.type=\"cloud_scheduler_job\" AND resource.labels.job_id=\"$cron_job\" AND severity>=ERROR"

  file="$TMP_DIR/cron-absent.json"
  cat >"$file" <<EOF
{
  "displayName": "$SERVICE cron not running",
  "combiner": "OR",
  "conditions": [
    {
      "displayName": "No cron_done log line for 2 hours",
      "conditionAbsent": {
        "filter": "metric.type=\"logging.googleapis.com/user/$SERVICE-cron-done\" AND resource.type=\"cloud_run_revision\"",
        "aggregations": [
          {
            "alignmentPeriod": "300s",
            "perSeriesAligner": "ALIGN_SUM",
            "crossSeriesReducer": "REDUCE_SUM"
          }
        ],
        "duration": "7200s",
        "trigger": {"count": 1}
      }
    }
  ],
  "notificationChannels": ["$channel"]
}
EOF
  ensure_policy "$SERVICE cron not running" "$file"

  file="$TMP_DIR/cron-problems.json"
  cat >"$file" <<EOF
{
  "displayName": "$SERVICE cron or purge problem",
  "combiner": "OR",
  "conditions": [
    {
      "displayName": "Cron refused or failed, or the purge backstop acted",
      "conditionThreshold": {
        "filter": "metric.type=\"logging.googleapis.com/user/$SERVICE-cron-problems\" AND resource.type=\"cloud_run_revision\"",
        "aggregations": [
          {
            "alignmentPeriod": "300s",
            "perSeriesAligner": "ALIGN_SUM",
            "crossSeriesReducer": "REDUCE_SUM"
          }
        ],
        "comparison": "COMPARISON_GT",
        "thresholdValue": 0,
        "duration": "0s",
        "trigger": {"count": 1}
      }
    },
    {
      "displayName": "Cloud Scheduler job failed",
      "conditionThreshold": {
        "filter": "metric.type=\"logging.googleapis.com/user/$SERVICE-scheduler-errors\" AND resource.type=\"cloud_scheduler_job\"",
        "aggregations": [
          {
            "alignmentPeriod": "300s",
            "perSeriesAligner": "ALIGN_SUM",
            "crossSeriesReducer": "REDUCE_SUM"
          }
        ],
        "comparison": "COMPARISON_GT",
        "thresholdValue": 0,
        "duration": "0s",
        "trigger": {"count": 1}
      }
    }
  ],
  "notificationChannels": ["$channel"]
}
EOF
  ensure_policy "$SERVICE cron or purge problem" "$file"
}

uptime_alert() {
  local channel=$1 check host name="$SERVICE readyz" policy_file
  step "Uptime check on /readyz (database, worker, model provider) with an email alert to $ALERT_EMAIL"
  host=${UPTIME_HOST:-$(service_url)}
  host=${host#https://}
  host=${host%%/*}
  check=$(gc monitoring uptime list-configs --filter="displayName=\"$name\"" \
    --format='value(name)' | head -n 1)
  if [ -z "$check" ]; then
    check=$(gc monitoring uptime create "$name" --resource-type=uptime-url \
      --resource-labels="host=$host,project_id=$PROJECT_ID" --protocol=https \
      --path=/readyz --period=5 --timeout=10 --status-codes=200 --format='value(name)')
    note "uptime check created for https://$host/readyz"
  else
    note "uptime check exists ($check)"
  fi
  policy_file="$TMP_DIR/policy.json"
  # Condition from the Cloud Monitoring uptime-check policy example (policies-in-json).
  cat >"$policy_file" <<EOF
{
  "displayName": "$name failing",
  "combiner": "OR",
  "conditions": [
    {
      "displayName": "Uptime check failing",
      "conditionThreshold": {
        "filter": "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.label.check_id=\"${check##*/}\" AND resource.type=\"uptime_url\"",
        "aggregations": [
          {
            "alignmentPeriod": "1200s",
            "perSeriesAligner": "ALIGN_NEXT_OLDER",
            "crossSeriesReducer": "REDUCE_COUNT_FALSE",
            "groupByFields": ["resource.label.*"]
          }
        ],
        "comparison": "COMPARISON_GT",
        "thresholdValue": 1,
        "duration": "600s",
        "trigger": {"count": 1}
      }
    }
  ],
  "notificationChannels": ["$channel"]
}
EOF
  ensure_policy "$name failing" "$policy_file"
}

step_budget() {
  step "Billing budget alert (${BUDGET_USD} USD per month)"
  if [ -z "${BILLING_ACCOUNT_ID:-}" ]; then # main() refused this unless SKIP_BUDGET=true
    note "SKIP_BUDGET=true: no budget alert (docs/deploy.md, step 9)"
    return
  fi
  local display="$SERVICE monthly budget"
  # Budgets belong to the billing account; --billing-project charges the API quota to
  # this project (where billingbudgets.googleapis.com was enabled).
  if [ -n "$(gcloud --billing-project="$PROJECT_ID" billing budgets list \
    --billing-account="$BILLING_ACCOUNT_ID" \
    --filter="displayName=\"$display\"" --format='value(name)')" ]; then
    note "budget exists"
    return
  fi
  # A budget only sends alerts; it never stops spending. The OpenAI project limit and the
  # app's DAILY_SPEND_CAP_USD are the hard stops.
  gcloud --billing-project="$PROJECT_ID" --quiet billing budgets create \
    --billing-account="$BILLING_ACCOUNT_ID" \
    --display-name="$display" --budget-amount="${BUDGET_USD}USD" \
    --filter-projects="projects/$PROJECT_ID" \
    --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 \
    --threshold-rule=percent=1.0 --threshold-rule=percent=1.0,basis=forecasted-spend \
    --notifications-rule-monitoring-notification-channels="$(alert_channel)" >/dev/null
  note "budget created"
}

# ------------------------------------------------------------------ main

main() {
  local steps step_name
  steps=${*:-apis iam registry build database secrets migrate service cron monitoring budget}
  cd "$(dirname "$0")/.." # Cloud Build uploads the repository root
  load_config
  preflight_limits
  case " $steps " in
    *" budget "*)
      if [ -z "${BILLING_ACCOUNT_ID:-}" ] && [ "${SKIP_BUDGET:-false}" != true ]; then
        die "BILLING_ACCOUNT_ID is not set (needed for the budget alert; set SKIP_BUDGET=true to deploy without one)"
      fi
      ;;
  esac
  printf 'Project %s, region %s, image %s\n' "$PROJECT_ID" "$REGION" "$IMAGE"
  for step_name in $steps; do
    case "$step_name" in
      apis | iam | registry | build | database | secrets | migrate | service | cron | monitoring | budget)
        "step_$step_name"
        ;;
      *) die "unknown step: $step_name" ;;
    esac
  done
  step "Done"
  note "Next: map the custom domain and set the OAuth redirect URI (docs/deploy.md, steps 7-8)."
}

main "$@"
