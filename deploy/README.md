# deploy/

The current deploy is Railway ([`docs/deploy-railway.md`](../docs/deploy-railway.md)): `.railway/railway.ts` describes the
whole project (web, cron, backup, Postgres). The files below are the
Google Cloud Run alternative.


| File | What it is |
|---|---|
| `deploy.sh` | Idempotent Google Cloud deploy: APIs, service account, Artifact Registry, Cloud Build, Cloud SQL, Secret Manager, migration job, Cloud Run service, Cloud Scheduler, uptime alert, budget |
| `backups.sh` | `status` (are the Cloud SQL backups running and fresh, any manual one left over), `now` (one on-demand backup), `list` |
| `deploy.env.example` | Every setting the script reads. Copy to `deploy/.env.deploy` (git-ignored) |

Step-by-step guide (it opens with Harish's first-deploy checklist), including the parts that are not scripted (model provider's spend limit, Google
sign-in client, custom domain, rollback): [`docs/deploy.md`](../docs/deploy.md).
Backups, alerts and fixes on this path are in the same guide; what the app records and
what its commands do is in [`docs/operations.md`](../docs/operations.md).
