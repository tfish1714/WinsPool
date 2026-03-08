# WinsPool Deployment Guide

This guide covers three deployment options, ordered by recommendation.

---

## Option 1 — Google Cloud Run ⭐ (Recommended)

Cloud Run runs your existing `Dockerfile` on managed infrastructure.  
Zero servers to manage, scales to zero when nobody is using it, and **the free tier covers a small pool app easily**.

### Prerequisites
```bash
# Install the gcloud CLI
# https://cloud.google.com/sdk/docs/install

gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com artifactregistry.googleapis.com
```

### One-time setup: Firebase Service Account as a Secret

Never put `firebase_credentials.json` inside the Docker image.  
Instead, store it as a Cloud Secret and inject it as a base64 env var:

```bash
# Base64-encode your credentials file
python -c "import base64; print(base64.b64encode(open('firebase_credentials.json','rb').read()).decode())"
# Copy that output, then:
echo -n "PASTE_OUTPUT_HERE" | gcloud secrets create FIREBASE_CREDENTIALS --data-file=-
```

### Build & Deploy
```bash
# Build image
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/winspool

# Deploy
gcloud run deploy winspool \
  --image gcr.io/YOUR_PROJECT_ID/winspool \
  --platform managed \
  --region us-east1 \
  --allow-unauthenticated \
  --set-env-vars "USE_LOCAL_DATA=False" \
  --set-secrets "FIREBASE_CREDENTIALS=FIREBASE_CREDENTIALS:latest" \
  --set-env-vars "ROOM_CODE=your_room_code" \
  --set-env-vars "ADMIN_CODE=your_admin_code"
```

### Scheduling the sync job (Cloud Scheduler)
```bash
# Run daily_nfl_sync.py daily at 4am ET
gcloud scheduler jobs create http winspool-sync \
  --schedule "0 4 * * *" \
  --uri "https://YOUR_RUN_URL/api/trigger-sync" \
  --time-zone "America/New_York"
```

> **Note:** You can also expose a `/api/trigger-sync` endpoint in `api_routes.py` that calls `sync_nfl_data()` directly, or run `daily_nfl_sync.py` from a separate Cloud Run Job.

---

## Option 2 — PythonAnywhere (Familiar from prior app)

PythonAnywhere supports FastAPI/ASGI apps via a WSGI adapter.

### Setup
1. Create a **Web App** — choose "Manual configuration", Python 3.11
2. In the **WSGI config file** (`/var/www/yoursite_pythonanywhere_com_wsgi.py`):

```python
import sys
sys.path.insert(0, '/home/yourusername/WinsPool')

from asgiref.wsgi import WsgiToAsgi
from main import app

# PythonAnywhere needs a WSGI wrapper around the ASGI app
application = WsgiToAsgi(app)
```

3. Install dependencies:
```bash
pip install fastapi uvicorn asgiref firebase-admin pandas
```

4. Set environment variables in the **"Environment variables"** tab of the web console.

### Scheduling the sync
Use **Always-on tasks** (paid) or the **Scheduled tasks** tab (free tier: once a day):
```
0 4 * * *  /home/yourusername/WinsPool/.venv/bin/python /home/yourusername/WinsPool/scripts/daily_nfl_sync.py
```

---

## Option 3 — Fly.io (Simplest CLI Deploy)

Fly.io is the closest modern equivalent to old Heroku.

```bash
# Install flyctl: https://fly.io/docs/getting-started/installing-flyctl/
fly auth login
fly launch   # Detects the Dockerfile automatically, prompts for region/name
```

Set secrets (equivalent of env vars):
```bash
fly secrets set ROOM_CODE=your_code ADMIN_CODE=your_admin
fly secrets set FIREBASE_CREDENTIALS="$(python -c "import base64; print(base64.b64encode(open('firebase_credentials.json','rb').read()).decode())")"
```

Deploy:
```bash
fly deploy
```

Scheduled sync (Fly Machines cron):
```bash
fly machine run . --command "python scripts/daily_nfl_sync.py" --schedule daily
```

---

## Custom Domains (Google Cloud Run)

### Method A: Cloud Run Domain Mappings (Easiest)
Note: This is in "Preview" and only supported in specific regions (like `us-central1`).

1. Go to the **Cloud Run** console.
2. Click **Manage Custom Domains** at the top.
3. Click **Add Mapping**.
4. Select your service (`winspool`).
5. Follow the verification steps (via **Google Search Console** if you haven't verified the domain yet).
6. GCP will provide `CNAME` or `A` records to add to your DNS provider (GoDaddy, Namecheap, etc.).

### Method B: Firebase Hosting (Recommended for Global CDN)
If Domain Mappings aren't available for your region, or if you want Firebase integration:

1. Initialize Firebase in your project: `firebase init hosting`
2. Configure `firebase.json` to rewrite all requests to your Cloud Run service:
   ```json
   {
     "hosting": {
       "rewrites": [{ "source": "**", "run": { "serviceId": "winspool", "region": "us-east1" } }]
     }
   }
   ```
3. Deploy: `firebase deploy --only hosting`
4. Connect your domain in the **Firebase Hosting** console.

### Method C: Global Load Balancer (Enterprise/Production)
Best for production environments requiring a global IP and advanced SSL control. Requires setting up a **Backend Service** for Cloud Run behind an **HTTPS Load Balancer**.

---

## Customizing the Default (Free) URL

If you don't want to buy a custom domain, you can still make the URL look better by controlling the **Service Name** and **Project ID**.

### Cloud Run Default URL
Format: `https://[SERVICE_NAME]-[HASH]-[REGION].a.run.app`
- **To change the name**: Simply change the name in the `gcloud run deploy` command (e.g., `gcloud run deploy nfl-pool-2024`). This immediately updates the URL.

### Firebase Hosting Default URL (Branded)
Format: `https://[PROJECT_ID].web.app` or `https://[PROJECT_ID].firebaseapp.com`
- **To change the name**: This requires creating a **New Google Cloud Project** with a custom Project ID. 
- Example: If you create a project called `wins-pool-league`, your URL will be `wins-pool-league.web.app`.

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `FIREBASE_CREDENTIALS` | Prod only | Base64-encoded `firebase_credentials.json` |
| `ROOM_CODE` | Yes | Code players enter to join the draft room |
| `ADMIN_CODE` | Yes | Code for the admin dashboard |
| `USE_LOCAL_DATA` | Local only | `True` = use `.local_db/*.pkl`, `False` = Firestore |
| `PORT` | Optional | Port to listen on (default 8000) |

See `.env.example` for how to set these up locally.
