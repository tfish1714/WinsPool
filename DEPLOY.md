# WinsPool Deployment Guide

## Standard Deploy (Google Cloud Run)

Deployment is handled by PowerShell scripts in the `deploy/` folder.

### Deploy the app
```powershell
.\deploy\deploy.ps1
```

This script will:
1. Check / prompt for `gcloud` authentication
2. Build the Docker image via Cloud Build and push to `gcr.io/fishbone-wins-pool/winspool`
3. Deploy to Cloud Run (`winspool` service, `us-east1`, project `fishbone-wins-pool`)
4. Rebuild the `winspool-sync`/`winspool-predict` images (`cloudbuild-sync.yaml`/`cloudbuild-predict.yaml`) and update the 4 scheduled Cloud Run Jobs to use them — see CLAUDE.md's **Scheduled Jobs** section for what those are. This step does NOT repeat one-time GCP setup (API enablement, IAM, the Cloud Tasks queue, Cloud Scheduler triggers) — see `docs/superpowers/plans/completed/2026-08-19-scheduled-jobs.md` Task 9 for that.

**Service URL:** `https://winspool-1045965963135.us-east1.run.app`

---

## Prerequisites

- [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed and on PATH
- Authenticated: `gcloud auth login`
- Project set: `gcloud config set project fishbone-wins-pool`
- Required secrets already stored in Secret Manager (used by both the `winspool`
  service and all 4 scheduled Cloud Run Jobs — see CLAUDE.md's Scheduled Jobs
  section):
  - `FIREBASE_CREDENTIALS` — base64-encoded service account JSON
  - `GEMINI_API_KEY`
  - `SMTP_PASSWORD` — legacy fallback; Resend is the primary email path now
  - `RESEND_API_KEY` — Resend, used for recaps, MFA codes, and job-failure alerts
  - `JWT_SECRET` — signs the session token issued by `/api/login`

### One-time secret setup
```bash
# Firebase credentials
python -c "import base64; print(base64.b64encode(open('firebase_credentials.json','rb').read()).decode())" | gcloud secrets create FIREBASE_CREDENTIALS --data-file=-

# Gemini + SMTP + Resend + JWT
echo -n "YOUR_KEY" | gcloud secrets create GEMINI_API_KEY --data-file=-
echo -n "YOUR_PASSWORD" | gcloud secrets create SMTP_PASSWORD --data-file=-
echo -n "YOUR_RESEND_KEY" | gcloud secrets create RESEND_API_KEY --data-file=-
echo -n "YOUR_JWT_SECRET" | gcloud secrets create JWT_SECRET --data-file=-
```

---

## Environment Variables

The following are set directly in `deploy.ps1`:

| Variable | Value |
|---|---|
| `USE_LOCAL_DATA` | `False` |
| `DEBUG_PAGE_LOAD` | `False` |
| `SMTP_SERVER` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | configured in script |
| `FROM_EMAIL` | configured in script |

Secrets (`FIREBASE_CREDENTIALS`, `GEMINI_API_KEY`, `SMTP_PASSWORD`, `JWT_SECRET`, `RESEND_API_KEY`) are injected via `--set-secrets`.

---

## Development

```bash
# Local server (uses .local_db/ pickles)
uvicorn main:app --reload

# Docker local test
docker build -t winspool .
docker run -p 8000:8080 -e USE_LOCAL_DATA=True winspool
```

---

## Scheduled Jobs (Cloud Scheduler + Cloud Tasks)

Data sync, live scores, prediction regen, and kickoff-time scheduling run as
4 separate Cloud Run Jobs (**not** HTTP endpoints on the `winspool` service)
— `winspool-sync-daily`, `winspool-predict-daily`, `winspool-live-scores`,
`winspool-schedule-kickoffs`. Cloud Scheduler triggers hit the Cloud Run Jobs
Admin API's `:run` endpoint (OAuth-authenticated via a dedicated
`winspool-scheduler` service account with `run.invoker`), not a route in
this app. `winspool-schedule-kickoffs` additionally enqueues one-off Cloud
Tasks for precise, per-game pre-kickoff timing.

Full schedule table, alerting design, and the two job Docker images
(`Dockerfile.sync` / `Dockerfile.predict`) are documented in CLAUDE.md's
**Scheduled Jobs** section — that's the source of truth, not this file.
One-time GCP provisioning (APIs, IAM, the Cloud Tasks queue, the Scheduler
triggers themselves) is `docs/superpowers/plans/completed/2026-08-19-scheduled-jobs.md`
Task 9; `deploy.ps1` only rebuilds/redeploys the job *images* on each run.
