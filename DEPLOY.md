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
4. Optionally deploy Firebase Cloud Functions (prompts y/n)

**Service URL:** `https://winspool-1045965963135.us-east1.run.app`

### Deploy Cloud Functions only
```powershell
.\deploy\deploy_functions.ps1
```

Requires the Firebase CLI (`npm install -g firebase-tools`).

---

## Prerequisites

- [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed and on PATH
- Authenticated: `gcloud auth login`
- Project set: `gcloud config set project fishbone-wins-pool`
- Required secrets already stored in Secret Manager:
  - `FIREBASE_CREDENTIALS` — base64-encoded service account JSON
  - `GEMINI_API_KEY`
  - `SMTP_PASSWORD`

### One-time secret setup
```bash
# Firebase credentials
python -c "import base64; print(base64.b64encode(open('firebase_credentials.json','rb').read()).decode())" | gcloud secrets create FIREBASE_CREDENTIALS --data-file=-

# Gemini + SMTP
echo -n "YOUR_KEY" | gcloud secrets create GEMINI_API_KEY --data-file=-
echo -n "YOUR_PASSWORD" | gcloud secrets create SMTP_PASSWORD --data-file=-
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
| `ROOM_CODE` | configured in script |

Secrets (`FIREBASE_CREDENTIALS`, `GEMINI_API_KEY`, `SMTP_PASSWORD`) are injected via `--set-secrets`.

---

## Development

```bash
# Local server (uses .local_db/ pickles)
uvicorn main:app --reload

# Docker local test
docker build -t winspool .
docker run -p 8000:8080 -e USE_LOCAL_DATA=True -e ROOM_CODE=test winspool
```

---

## Scheduled Sync (Cloud Scheduler)

```bash
# Nightly Firestore sync — 4 AM ET daily
gcloud scheduler jobs create http winspool-sync \
  --schedule "0 9 * * *" \
  --uri "https://winspool-1045965963135.us-east1.run.app/api/trigger-sync" \
  --time-zone "America/New_York"
```
