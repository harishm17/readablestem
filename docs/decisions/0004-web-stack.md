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

Drafted from what the repository shows; the author confirms or edits.

- **Easier.** One deploy, one process and no queue service. Deleting a job deletes
  everything made from it, because uploads and crops live in the same database rather than
  in a second store. Server-rendered pages work without JavaScript, which suits an
  accessibility tool.
- **Harder.** The worker shares a process and a CPU allowance with the web pages, and
  min-instances=1 with CPU always allocated is a standing cost. Postgres holds the
  uploaded documents, so its backups hold them too; the retention window and the privacy
  page have to agree (`docs/deploy.md`). Scaling past a few instructors would mean moving
  the worker out of the web process.
- **When to revisit.** When conversions slow the pages down, or use outgrows a handful of
  instructors.
