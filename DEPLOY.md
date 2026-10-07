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

Before deploying, also run `pytest tests_e2e/ -v` (the Playwright browser
suite) alongside `pytest tests/` — see `.claude/commands/deploy.md` for the
full pre-flight sequence and CLAUDE.md's **Tests** section for the e2e suite's
setup requirements.

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

### VAPID private key (Secret Manager)

`deploy.ps1` now injects `VAPID_PRIVATE_KEY` from Secret Manager
(`--set-secrets VAPID_PRIVATE_KEY=vapid-private-key:latest`) instead of a plain
env var. Before the next deploy the operator must:

```bash
# 1. Create the secret from the value currently in .env
echo -n "YOUR_VAPID_PRIVATE_KEY" | gcloud secrets create vapid-private-key --data-file=-

# 2. Let the Cloud Run service account read it
gcloud secrets add-iam-policy-binding vapid-private-key \
  --member="serviceAccount:<CLOUD_RUN_SERVICE_ACCOUNT>" \
  --role="roles/secretmanager.secretAccessor"

# 3. One-time: drop the old plain env var (Cloud Run rejects a name used as both env var and secret)
gcloud run services update winspool --region us-east1 --remove-env-vars=VAPID_PRIVATE_KEY
```

Deferred decision: whether to rotate the VAPID keypair now that the old private
key has lived as a plain env var. Rotation invalidates every existing browser
push subscription, so it is not done automatically.

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

## Scaling and cost guardrails

`deploy/deploy.ps1` pins `--max-instances=1` and `--concurrency=80` on the `winspool` web service.

Live settings, verified read-only on 2026-09-25: `autoscaling.knative.dev/maxScale: '1'`, `containerConcurrency: 80`, `timeoutSeconds: 3600`, cpu `1000m` (1 vCPU), memory `512Mi`.

**Why max-instances must stay 1.** The live draft room's WebSocket state (`ConnectionManager` / `connected_players`) and the auth rate limiter (`services/rate_limit_service.py`) live in process memory. A second instance would split a draft room in two and double every per-IP limit. It also bounds worst-case spend. Do not raise it until that state is moved out of process.

**Service-level vs revision-level maxScale.** The service also carries a service-level `maxScale` annotation of 20, while the revision-level `maxScale` is 1. The effective cap is the lower value (1).

**Auth rate limiting env vars** (all optional, read at startup):

- `TRUSTED_PROXY_HOPS` (default `1`): how many positions from the right of `X-Forwarded-For` is the real client IP. Correct for direct Cloud Run access; set it to `2` if Firebase Hosting or a load balancer is ever put in front.
- `AUTH_RATE_LIMIT_PER_MINUTE` (default `5`): per-IP limit shared by `/api/login`, `/api/set_password` and `/api/profile/update`.
- `AUTH_LOOKUP_RATE_LIMIT_PER_MINUTE` (default `30`): per-IP limit for `/api/check_player`.

**Post-deploy verification.** Make 6 bad logins from your own machine; the 6th should return 429. The log warning `auth rate limit hit: ip=` should show your public IP, matching `httpRequest.remoteIp` in the Cloud Run request log. If it shows a different address (for example a Google proxy), adjust `TRUSTED_PROXY_HOPS`.

**Re-verify:**

```
gcloud run services describe winspool --region us-east1 --project fishbone-wins-pool --format="yaml(spec.template.metadata.annotations,spec.template.spec.containerConcurrency,spec.template.spec.timeoutSeconds)"
```

The scheduled jobs (`winspool-sync-daily`, `winspool-live-scores`, `winspool-schedule-kickoffs`, `winspool-predict-daily`) are separate Cloud Run Jobs with their own task limits; these service flags do not apply to them.

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

---

## Kickoff queue safeguards (Cloud Tasks)

`winspool-schedule-kickoffs` enqueues per-game tasks into the `winspool-kickoff-triggers`
queue. Two safeguards (one-time, operator-run; not part of `deploy.ps1`):

```powershell
# Retry limits: the default is 100 attempts backing off up to an hour, which let one
# permanently failing task (missing run.jobs.runWithOverrides) retry 64 times unnoticed.
# A resimulate task is worthless after kickoff, so stop retrying after 30 minutes.
gcloud tasks queues update winspool-kickoff-triggers --location=us-east1 --project=fishbone-wins-pool `
  --max-attempts=5 --min-backoff=30s --max-backoff=300s --max-doublings=3 --max-retry-duration=1800s

# Alert when queue dispatch attempts keep failing (uses the existing email channel).
gcloud alpha monitoring policies create --project=fishbone-wins-pool `
  --policy-from-file=deploy/alerts/kickoff-queue-attempt-failures.json `
  --notification-channels=projects/fishbone-wins-pool/notificationChannels/3572266000520947858
```

IAM the scheduler account needs on the `winspool-predict-daily` job: `roles/run.invoker`
plus `roles/run.jobsExecutorWithOverrides` (the resimulate task overrides container args;
plain invoker returns 403 for that request).

The alert metric label `response_code` was checked in Cloud Monitoring's Metrics Explorer
(`cloudtasks.googleapis.com/queue/task_attempt_count`, grouped by `response_code`): the values
are lowercase (`ok`, `unavailable`), so the policy filter is `response_code != "ok"`. If a policy
was created from an earlier copy of the file that said `"OK"`, update it in place:
`gcloud alpha monitoring policies update <POLICY_ID> --project=fishbone-wins-pool --policy-from-file=deploy/alerts/kickoff-queue-attempt-failures.json`.

## Weekly standings push (one-time job config)

`winspool-sync-daily` runs `scripts/send_weekly_standings_push.py` as a non-required step. The
job needs the Web Push VAPID settings, which the web service already has. `deploy.ps1` only
swaps images, so this is a one-time setup (these commands have not been run by the repo
change; run them yourself). Get `<JOB_SA>` with
`gcloud run jobs describe winspool-sync-daily --region=us-east1 --format="value(spec.template.spec.template.spec.serviceAccountName)"`.
Without this, the step logs a warning ("Web push is not configured") and exits 0.

```powershell
gcloud run jobs update winspool-sync-daily --region=us-east1 --project=fishbone-wins-pool `
  --update-env-vars VAPID_PUBLIC_KEY=<public key>,VAPID_CLAIMS_EMAIL=<mailto:you@example.com> `
  --update-secrets VAPID_PRIVATE_KEY=vapid-private-key:latest
gcloud secrets add-iam-policy-binding vapid-private-key --project=fishbone-wins-pool `
  --member=serviceAccount:<JOB_SA> --role=roles/secretmanager.secretAccessor
```
