# Operations

How the deployed app looks after itself, what it records, and what to do when something
goes wrong. First deploy, rollback and secret rotation: [deploy.md](deploy.md).

## What runs on a schedule

| What | When | Where |
|---|---|---|
| Aggregate, then purge | Hourly, Cloud Scheduler job `a11y-stem-cron` | `POST /internal/cron/aggregate-and-purge` (needs the `X-Cron-Token` header) |
| Uptime check of `/healthz` | Every 5 minutes, Cloud Monitoring | Emails `ALERT_EMAIL` when it fails for 10 minutes |
| Outside-in check of `/healthz` and `/demo` | Weekly, GitHub Actions `uptime.yml` | Skips unless the repo variable `LIVE_URL` is set |
| Dependency updates | Weekly, Dependabot | One grouped PR per ecosystem (uv, actions, Docker) |

The same tasks run by hand:

```sh
gcloud scheduler jobs run a11y-stem-cron --location="$REGION" --project="$PROJECT_ID"

# or from a machine that can reach the database (prints counts only):
DATABASE_URL=postgresql+psycopg://... uv run a11y-stem ops cron
uv run a11y-stem ops aggregate
uv run a11y-stem ops purge
```

`ops cron` exits 1 if aggregation failed, but only after the purge has run.

## Retention

Each job gets `retain_until` when it is uploaded: `RETENTION_HOURS` (24) later, or
`OPT_IN_RETENTION_DAYS` (30) when the instructor opted in. The hourly purge deletes every
job past that time together with everything derived from the document: the uploaded PDF,
the crops, the result JSON, the HTML, the filename and the review edits. It deletes the
child rows itself and does not rely on the database cascade alone.

Deletion does not wait for statistics. If a job reaches its deadline before it was
aggregated (aggregation kept failing, say), it is deleted anyway and counted in
`unaggregated_deleted`. A non-zero value there means some counts are missing; it never
means a document was kept.

What survives deletion: users and the allowlist, the daily spend and quota counters, and
the content-free aggregates below.

## Error aggregates

`error_aggregates` has one row per (UTC day, step, category) with a count. The day is the
day the job finished. Categories:

| Step | Category | Counts |
|---|---|---|
| `equation`, `figure`, `heading` | `status:<status>` | fixes by status (`accepted`, `needs_review`, `abstained`, `failed`) |
| same | `check:<check name>:<code>` | failed checks, e.g. `check:latex.allowed_commands:unknown_command` |
| same | `reason:<code>` | `Fix.reason`, e.g. `reason:low_confidence`, `reason:budget_daily` |
| same | `call:<model>:outcome:<outcome>` | every model call attempt, e.g. `call:gpt-6-luna:outcome:timeout` |
| same | `call:fallback` | calls made on the other model after a failure |
| same | `call:escalated` | fixes re-asked on a different model without a failure (e.g. a low-confidence `gpt-6-luna` answer sent to `gpt-6.1-sol`) |
| same | `review:<action>` | reviewer decisions (`accept`, `edit`, `reject`), counted when the job is deleted, because reviews can happen until then |
| `job` | `status:done`, `status:failed` | finished jobs |
| `job` | `error:<code>` | `Job.error_code`, e.g. `error:encrypted` |
| `job` | `pages` | pages converted (a sum, not a count of jobs) |
| `job` | `aggregate:invalid_result` | stored results that no longer parse (schema drift) |

Every part of a category must look like a code (lower case letters, digits, `_ . -`, at
most 48 characters). Anything else is stored as `other`, so even a bug that puts text in
a check code cannot write that text into this table. A rising `other` count means some
code upstream is emitting something that is not a category code.

Aggregation is idempotent: a job's counts and its `aggregated` flag are written in one
transaction, and on Postgres the job rows are locked with `FOR UPDATE SKIP LOCKED`, so two
overlapping cron runs never count a job twice.

Reading them (Cloud SQL Studio in the console, or `psql` through the Cloud SQL Auth
Proxy):

```sql
-- Most frequent failed checks in the last 7 days
SELECT step, category, sum(count) AS n
FROM error_aggregates
WHERE day >= current_date - 7 AND category LIKE 'check:%'
GROUP BY step, category ORDER BY n DESC LIMIT 20;

-- Share of equation fixes needing review, per day
SELECT day,
       sum(count) FILTER (WHERE category = 'status:needs_review')::float
         / nullif(sum(count) FILTER (WHERE category LIKE 'status:%'), 0) AS review_rate
FROM error_aggregates WHERE step = 'equation' GROUP BY day ORDER BY day;

-- Model reliability: outcomes per model
SELECT category, sum(count) FROM error_aggregates
WHERE category LIKE 'call:%:outcome:%' GROUP BY category ORDER BY 2 DESC;
```

## Logs

Every line on stdout is one JSON object. Cloud Logging reads `severity`, `message` and
`time` (UTC) from it; everything else lands in `jsonPayload`.

```json
{"method": "POST", "route": "/jobs/{job_id}/review", "status": 303, "latency_ms": 41,
 "logger": "a11y_stem.http", "time": "2026-10-07T18:02:11.204518Z",
 "severity": "INFO", "message": "request"}
```

Useful Logs Explorer queries (add `resource.type="cloud_run_revision" AND
resource.labels.service_name="a11y-stem"`):

| Question | Query |
|---|---|
| Anything wrong? | `severity>=WARNING` |
| Server errors | `jsonPayload.message="request" AND jsonPayload.status>=500` |
| Slow pages | `jsonPayload.message="request" AND jsonPayload.latency_ms>5000` |
| Hourly cleanup | `jsonPayload.message=("aggregate_done" OR "purge_done" OR "aggregate_failed")` |
| Tracing problems | `jsonPayload.message=("tracing_disabled" OR "trace_failed")` |

### What can never be in a log line

Logs are about the system, not the documents. The chain in
`observability/logging.py` enforces it for every logger, ours or a library's:

- Keys that name content are dropped: `text`, `latex`, `mathml`, `alt`, `html`,
  `filename`, `email`, `content`, `caption`, `context`, `prompt`, `speech`, also as a
  word inside a longer key (`alt_text`, `user_email`, `contextText`). `prompt_version` is
  the one allowed exception.
- Values must be plain data. Objects become a type marker (`"<Element>"`), so a model or
  a path cannot leak through its `repr`. Strings are cut at 200 characters.
- Exceptions are logged as `error_type` plus a `stack` of `file:line:function` entries.
  The message is dropped: a pydantic error on model output quotes the LaTeX it rejected.
- Below WARNING, only `a11y_stem.*`, `uvicorn.error` and alembic's migration logger are
  written. Libraries that log URLs with query strings or request bodies at INFO or DEBUG
  (the OpenAI SDK's `httpx2`, `httpx`, SQLAlchemy) stay quiet even with `LOG_LEVEL=DEBUG`.
- Uvicorn's access log is off; `request_logging_middleware` logs the route template
  (`/jobs/{job_id}`), never the path, query string or headers.

Writing a log line:

```python
from a11y_stem.observability.logging import get_logger

log = get_logger(__name__)
log.info(
    "fix_done",
    job_id=job.id,
    element_id=fix.element_id,
    status=fix.status,
    reason=fix.reason,
    cost_usd=fix.cost_usd,
)
```

Pass ids, counts and category codes as keyword fields. Do not build a message string
out of values, and never pass document text under a neutral key name: the filter catches
names, not meaning.

## Tracing (Langfuse)

Off unless `TRACING_ENABLED=true` and both Langfuse keys are set (`deploy.sh` stores the
secret key in Secret Manager when tracing is enabled). If the `tracing` extra is missing
or the client cannot start, the app logs `tracing_disabled` with a `reason` and carries
on without traces. Keep it off for any run on `private-inputs/`.

What a span can carry is an allowlist (`ALLOWED_ATTRIBUTES` in
`observability/tracing.py`): ids, step, kind, model, prompt version, attempt, fallback
flag, status, outcome, reason, error type, confidence, token counts, `cost_usd`,
`latency_ms`, page numbers. String values must look like codes. It is enforced three
times: when the span is written, in the Langfuse `mask` hook (inputs and outputs become
`[masked]`), and in the export hook, which deletes any other attribute before anything
leaves the process. Spans opened with `model=` become Langfuse generations, so cost and
token views work. Exceptions are recorded as level ERROR with the exception type only.

Spans started in worker threads nest under the job's span only if the thread runs in a
copy of the caller's context (`contextvars.copy_context().run(...)`); otherwise each
step shows up as its own trace.

## When something goes wrong

| Symptom | Look at | Usual fix |
|---|---|---|
| Uptime alert email | `severity>=WARNING` logs; `gcloud run revisions list` | Roll back traffic to the last good revision (deploy.md, "Roll back") |
| `/healthz` answers 503 | The database: Cloud SQL instance state, connections | See "Too many connections" below |
| Conversions fail with `reason:budget_daily` | Spend today vs `DAILY_SPEND_CAP_USD` | Working as designed. Raise the cap only on purpose; it resets at 00:00 UTC |
| Conversions fail with provider errors | `call:<model>:outcome:api_error` counts; OpenAI dashboard | The OpenAI project limit may be reached (HTTP 429 `project_spend_limit_exceeded`) |
| Cron returns 403 | `cron_refused` in logs | The scheduler's token differs from the secret, usually after a rotation: `./deploy/deploy.sh cron` |
| Cron returns 500 | `aggregate_failed` with `error_type` and `stack` | Purge still ran. Fix the bug; jobs stay unaggregated until then and are counted in `unaggregated_deleted` if they expire first |
| `unaggregated_deleted` above 0 | The cron history | Some statistics are lost for those jobs; nothing else to do |
| Purge itself failing | `purge_done` missing for over an hour | Treat as a privacy incident: run `a11y-stem ops purge` by hand, then fix the cause |
| Too many connections | Cloud SQL connection count (25 on db-f1-micro) | Each instance pools up to 10 connections; keep `--max-instances=2` or move to `db-g1-small` (50) |
| Weekly GitHub uptime run failed | The run's log | Same as the uptime alert; also check the custom domain and certificate |

## CI and dependency updates

`ci.yml` runs on every push to `main` and every pull request:

| Job | What it checks |
|---|---|
| `lint-test` (3.12, 3.14) | `ruff check`, `ruff format --check`, `ty check src`, `pytest` (offline; Postgres tests use the embedded server on 3.12) |
| `postgres` | `pytest -m postgres` against a Postgres 16 service container |
| `browser` | `pytest -m browser` with Playwright Chromium (axe-core checks) |
| `docker` | Builds the image (no push) and checks the entry point and non-root user |
| `eval-gate` | `a11y-stem eval gate` when `data/eval/latest.json` and `baseline.json` are committed |
| `secret-scan` | gitleaks over the full history |

Every action is pinned to a commit SHA with its tag in a comment; Dependabot proposes
updates weekly. `eval-live.yml` (manual, costs money) runs the equations eval against
the live models with a `max_usd` cap and uploads the report as an artifact.
