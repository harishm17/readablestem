#!/usr/bin/env bash
# Deploy a11y-stem to Google Cloud Run. Walkthrough: docs/deploy.md.
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

  SERVICE=${SERVICE:-a11y-stem}
  AR_REPO=${AR_REPO:-a11y-stem}
  DB_MODE=${DB_MODE:-cloudsql}
  SQL_INSTANCE=${SQL_INSTANCE:-a11y-stem-db}
  SQL_TIER=${SQL_TIER:-db-f1-micro}
  SQL_DATABASE=${SQL_DATABASE:-a11y}
  SQL_USER=${SQL_USER:-a11y}
  BUDGET_USD=${BUDGET_USD:-25}
  CRON_SCHEDULE=${CRON_SCHEDULE:-0 * * * *}
  case "$DB_MODE" in
    cloudsql | external) ;;
    *) die "DB_MODE must be cloudsql or external" ;;
  esac

  RUN_SA="$SERVICE-run@$PROJECT_ID.iam.gserviceaccount.com"
  if [ -z "${IMAGE_TAG:-}" ]; then
    IMAGE_TAG=$(git rev-parse --short=12 HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S)
    if ! git diff --quiet HEAD -- 2>/dev/null; then
      IMAGE_TAG="$IMAGE_TAG-dirty-$(date -u +%Y%m%d%H%M%S)"
    fi
  fi
  IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$AR_REPO/$SERVICE:$IMAGE_TAG"
  SECRETS="OPENAI_API_KEY=openai-api-key:latest,SESSION_SECRET=session-secret:latest"
  SECRETS="$SECRETS,GOOGLE_CLIENT_SECRET=google-client-secret:latest,CRON_TOKEN=cron-token:latest"
  SECRETS="$SECRETS,DATABASE_URL=database-url:latest"
  TRACING_ENABLED=${TRACING_ENABLED:-false}
  if [ "$TRACING_ENABLED" = true ]; then
    require LANGFUSE_PUBLIC_KEY
    SECRETS="$SECRETS,LANGFUSE_SECRET_KEY=langfuse-secret-key:latest"
  fi
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
    billingbudgets.googleapis.com iam.googleapis.com
}

step_iam() {
  step "Service accounts"
  if exists iam service-accounts describe "$RUN_SA"; then
    note "$RUN_SA exists"
  else
    gc iam service-accounts create "$SERVICE-run" --display-name="a11y-stem Cloud Run"
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
      --location="$REGION" --description="a11y-stem images"
  fi
}

step_build() {
  step "Building $IMAGE with Cloud Build"
  # The upload skips what .gitignore lists (.env, private-inputs/, .local/); the Docker
  # build then applies .dockerignore.
  gc builds submit --region="$REGION" --tag="$IMAGE" .
}

step_database() {
  if [ "$DB_MODE" = external ]; then
    step "Database: external Postgres (DATABASE_URL secret, see the secrets step)"
    return
  fi
  step "Cloud SQL instance $SQL_INSTANCE ($SQL_TIER)"
  if exists sql instances describe "$SQL_INSTANCE"; then
    note "exists"
  else
    # Shared-core tiers need the Enterprise edition; Postgres 16+ defaults to Enterprise Plus.
    note "creating (takes about ten minutes)"
    gc sql instances create "$SQL_INSTANCE" --database-version=POSTGRES_16 \
      --edition=enterprise --tier="$SQL_TIER" --region="$REGION" \
      --storage-size=10 --storage-auto-increase --availability-type=zonal \
      --backup-start-time=08:00
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
  ensure_secret openai-api-key OPENAI_API_KEY ask
  ensure_secret google-client-secret GOOGLE_CLIENT_SECRET ask
  ensure_secret session-secret SESSION_SECRET generate
  ensure_secret cron-token CRON_TOKEN generate
  ensure_database_url
  if [ "$TRACING_ENABLED" = true ]; then
    ensure_secret langfuse-secret-key LANGFUSE_SECRET_KEY ask
  fi
}

cloudsql_flag() { # $1: --set-cloudsql-instances flag form for services and jobs
  if [ "$DB_MODE" = cloudsql ]; then
    printf '%s=%s' "$1" "$(sql_connection_name)"
  fi
}

step_migrate() {
  step "Database migration (Cloud Run job $SERVICE-migrate: a11y-stem db upgrade)"
  local sql
  sql=$(cloudsql_flag --set-cloudsql-instances)
  # shellcheck disable=SC2086  # $sql is empty or one flag
  gc run jobs deploy "$SERVICE-migrate" --image="$IMAGE" --region="$REGION" \
    --service-account="$RUN_SA" --set-secrets=DATABASE_URL=database-url:latest \
    --command=a11y-stem --args=db,upgrade --max-retries=0 --task-timeout=10m $sql
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
    for name in MAX_USD_PER_JOB MAX_MODEL_CALLS_PER_JOB MAX_PAGES_PER_JOB MAX_UPLOAD_MB \
      RETENTION_HOURS OPT_IN_RETENTION_DAYS SOURCE_URL LOG_LEVEL LANGFUSE_PUBLIC_KEY \
      LANGFUSE_HOST; do
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
    --cpu=1 --memory=1Gi --min-instances=1 --max-instances=2 --no-cpu-throttling \
    --concurrency=20 --timeout=900 \
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
    channel=$(gc beta monitoring channels create --display-name="a11y-stem alerts" \
      --type=email --channel-labels="email_address=$ALERT_EMAIL" --format='value(name)')
  fi
  printf '%s' "$channel"
}

step_monitoring() {
  step "Uptime check on /healthz with an email alert to $ALERT_EMAIL"
  local channel check host name="$SERVICE healthz" policy_file
  channel=$(alert_channel)
  host=${UPTIME_HOST:-$(service_url)}
  host=${host#https://}
  host=${host%%/*}
  check=$(gc monitoring uptime list-configs --filter="displayName=\"$name\"" \
    --format='value(name)' | head -n 1)
  if [ -z "$check" ]; then
    check=$(gc monitoring uptime create "$name" --resource-type=uptime-url \
      --resource-labels="host=$host,project_id=$PROJECT_ID" --protocol=https \
      --path=/healthz --period=5 --timeout=10 --status-codes=200 --format='value(name)')
    note "uptime check created for https://$host/healthz"
  else
    note "uptime check exists ($check)"
  fi
  if [ -n "$(gc monitoring policies list --filter="displayName=\"$name failing\"" \
    --format='value(name)')" ]; then
    note "alert policy exists"
    return
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
  gc monitoring policies create --policy-from-file="$policy_file" >/dev/null
  note "alert policy created"
}

step_budget() {
  step "Billing budget alert (${BUDGET_USD} USD per month)"
  if [ -z "${BILLING_ACCOUNT_ID:-}" ]; then
    note "BILLING_ACCOUNT_ID not set: skipped (docs/deploy.md, step 9)"
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
