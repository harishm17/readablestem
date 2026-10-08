# deploy/

| File | What it is |
|---|---|
| `deploy.sh` | Idempotent Google Cloud deploy: APIs, service account, Artifact Registry, Cloud Build, Cloud SQL, Secret Manager, migration job, Cloud Run service, Cloud Scheduler, uptime alert, budget |
| `deploy.env.example` | Every setting the script reads. Copy to `deploy/.env.deploy` (git-ignored) |

Step-by-step guide, including the parts that are not scripted (OpenAI spend limit, Google
sign-in client, custom domain, rollback): [`docs/deploy.md`](../docs/deploy.md).
Day-to-day running: [`docs/operations.md`](../docs/operations.md).
