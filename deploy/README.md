# deploy/

The current deploy is Railway ([`docs/deploy-railway.md`](../docs/deploy-railway.md)): `railway.json` at the
repository root is the web service, `railway.cron.json` here is the cron service. The files below are the
Google Cloud Run alternative.


| File | What it is |
|---|---|
| `deploy.sh` | Idempotent Google Cloud deploy: APIs, service account, Artifact Registry, Cloud Build, Cloud SQL, Secret Manager, migration job, Cloud Run service, Cloud Scheduler, uptime alert, budget |
| `backups.sh` | `status` (are the Cloud SQL backups running and fresh, any manual one left over), `now` (one on-demand backup), `list` |
| `railway.cron.json` | Config file for the Railway cron service (`readablestem ops cron`, hourly) |
| `deploy.env.example` | Every setting the script reads. Copy to `deploy/.env.deploy` (git-ignored) |

Step-by-step guide (it opens with Harish's first-deploy checklist), including the parts that are not scripted (model provider's spend limit, Google
sign-in client, custom domain, rollback): [`docs/deploy.md`](../docs/deploy.md).
Day-to-day running: [`docs/operations.md`](../docs/operations.md).
