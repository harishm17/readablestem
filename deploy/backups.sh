#!/usr/bin/env bash
# Look at, and take, Cloud SQL backups of the a11y-stem database. Runbook: docs/operations.md,
# "Backups and restore".
#
#   ./deploy/backups.sh status    # is the daily backup running, how old is the newest,
#                                 # are there manual backups that outlive the retention promise
#   ./deploy/backups.sh now       # one on-demand backup (before a risky change)
#   ./deploy/backups.sh list      # every backup with its type and time
#
# Uses the same settings as deploy.sh (PROJECT_ID, SQL_INSTANCE, SQL_RETAINED_BACKUPS);
# nothing is created or deleted except by `now`. Bash 3.2 compatible. `status` exits 1
# when something needs attention, so it can run from a weekly reminder or a cron.
#
# Commands checked against the gcloud reference (docs.cloud.google.com/sdk/gcloud/
# reference/sql/backups) on 2026-10-14.

set -euo pipefail

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
note() { printf '    %s\n' "$*"; }

: "${PROJECT_ID:?PROJECT_ID is not set (see deploy/deploy.env.example)}"
SQL_INSTANCE=${SQL_INSTANCE:-a11y-stem-db}
SQL_RETAINED_BACKUPS=${SQL_RETAINED_BACKUPS:-1}
# An automated backup is taken once a day; allow a missed window plus the time it takes.
MAX_BACKUP_AGE_H=${MAX_BACKUP_AGE_H:-36}
# A manual backup is kept until someone deletes it, and it holds every document that
# existed then: past this many days it breaks the retention promise.
MAX_MANUAL_AGE_DAYS=${MAX_MANUAL_AGE_DAYS:-7}

gc() { gcloud --project="$PROJECT_ID" --quiet "$@"; }

# epoch seconds of an RFC 3339 time as gcloud prints it (2026-10-14T08:12:03.123Z)
epoch() { date -u -d "${1%%.*}Z" +%s 2>/dev/null || date -u -j -f '%Y-%m-%dT%H:%M:%S' "${1%%.*}" +%s; }

cmd_list() {
  gc sql backups list --instance="$SQL_INSTANCE" \
    --format='table(id,type,status,windowStartTime,description)'
}

cmd_now() {
  note "creating an on-demand backup of $SQL_INSTANCE"
  gc sql backups create --instance="$SQL_INSTANCE" \
    --description="manual $(date -u +%Y-%m-%d): delete after the change is verified"
  note "It holds every document present now and is kept until you delete it:"
  note "  gcloud sql backups delete BACKUP_ID --instance=$SQL_INSTANCE --project=$PROJECT_ID"
}

cmd_status() {
  local problems=0 enabled retained newest now age_h manual id start
  enabled=$(gc sql instances describe "$SQL_INSTANCE" \
    --format='value(settings.backupConfiguration.enabled)')
  retained=$(gc sql instances describe "$SQL_INSTANCE" \
    --format='value(settings.backupConfiguration.backupRetentionSettings.retainedBackups)')
  case "$enabled" in
    True | true) note "automated backups: on, $retained kept (deploy.sh sets $SQL_RETAINED_BACKUPS)" ;;
    *)
      note "PROBLEM: automated backups are off for $SQL_INSTANCE"
      problems=1
      ;;
  esac

  newest=$(gc sql backups list --instance="$SQL_INSTANCE" --filter='status=SUCCESSFUL AND type=AUTOMATED' \
    --sort-by=~windowStartTime --limit=1 --format='value(windowStartTime)')
  now=$(date -u +%s)
  if [ -z "$newest" ]; then
    note "PROBLEM: no successful automated backup yet"
    problems=1
  else
    age_h=$(((now - $(epoch "$newest")) / 3600))
    if [ "$age_h" -gt "$MAX_BACKUP_AGE_H" ]; then
      note "PROBLEM: newest successful automated backup is $age_h hours old (limit $MAX_BACKUP_AGE_H)"
      problems=1
    else
      note "newest successful automated backup: $age_h hours old"
    fi
  fi

  # Manual backups ("ON_DEMAND") outlive the retention count, and so the privacy promise.
  manual=$(gc sql backups list --instance="$SQL_INSTANCE" --filter='type=ON_DEMAND' \
    --format='value(id,windowStartTime)')
  if [ -n "$manual" ]; then
    while read -r id start; do
      [ -n "$id" ] || continue
      age_h=$(((now - $(epoch "$start")) / 3600))
      if [ "$age_h" -gt $((MAX_MANUAL_AGE_DAYS * 24)) ]; then
        note "PROBLEM: manual backup $id is $((age_h / 24)) days old: delete it"
        note "  gcloud sql backups delete $id --instance=$SQL_INSTANCE --project=$PROJECT_ID"
        problems=1
      else
        note "manual backup $id is $((age_h / 24)) days old"
      fi
    done <<EOF
$manual
EOF
  fi
  [ "$problems" -eq 0 ] && note "backups: ok"
  return "$problems"
}

main() {
  command -v gcloud >/dev/null || die "gcloud is not installed (https://cloud.google.com/sdk)"
  case "${1:-status}" in
    status) cmd_status ;;
    now) cmd_now ;;
    list) cmd_list ;;
    *) die "usage: backups.sh status|now|list" ;;
  esac
}

main "$@"
