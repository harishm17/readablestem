# ADR 0004: One FastAPI service

| Field | Value |
|---|---|
| Status | **Accepted** (Harish, 2026-10-07) |
| Date | 2026-10-07 |
| Decider | Harish |

## Decision

One Python service on Cloud Run (min-instances=1, CPU always allocated):

- FastAPI with server-rendered, accessible Jinja templates; htmx only enhances (pages
  work without JavaScript).
- A worker thread in the same process claims queued jobs from Postgres
  (`FOR UPDATE SKIP LOCKED`, with a lease so a crashed job is retried).
- Postgres holds users/allowlist, jobs, blobs (uploads and crops), review edits, the
  daily spend counter, per-user quotas and content-free daily aggregates. No separate
  object store: deleting a job deletes everything derived from the document.
- Google sign-in (OIDC via Authlib) plus an allowlist in the database.
- Cloud Scheduler calls `/internal/cron/aggregate-and-purge` (shared-secret header).

Rejected: Next.js front end + separate FastAPI workers + Cloud Tasks (two deploys, more
surface for a 3-5 instructor beta).

## Consequences

TODO(Harish)
