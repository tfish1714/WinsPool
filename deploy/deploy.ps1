$PROJECT_ID = "fishbone-wins-pool"
$IMAGE_TAG = "gcr.io/$PROJECT_ID/winspool"

# Check if user is already authenticated
$activeAccount = gcloud auth list --filter=status:ACTIVE --format="value(account)"
if (-not $activeAccount) {
    Write-Host "[AUTH] No active gcloud account found. Please log in..." -ForegroundColor Yellow
    gcloud auth login
}
else {
    Write-Host "[AUTH] Already authenticated as: $activeAccount" -ForegroundColor Green
}

# Ensure the user has replaced the placeholders
if ($PROJECT_ID -eq "YOUR_PROJECT_ID") {
    Write-Host "[ERROR] Please update `$PROJECT_ID inside deploy.ps1 before running." -ForegroundColor Red
    exit 1
}

# Personal/deployment-specific values (site URL, sender/alert addresses) are
# read from .env (gitignored), not hardcoded here, so they never appear in a
# git diff of this script. A shell $env:<NAME>, if already set, takes
# priority over .env.
function Get-DotEnvValue([string]$Name) {
    $shellValue = [Environment]::GetEnvironmentVariable($Name)
    if ($shellValue) { return $shellValue }
    if (Test-Path .env) {
        $envLine = Get-Content .env | Where-Object { $_ -match "^\s*$Name\s*=" } | Select-Object -Last 1
        if ($envLine) { return ($envLine -split '=', 2)[1].Trim().Trim('"').Trim("'") }
    }
    return $null
}

$appBaseUrl = Get-DotEnvValue "APP_BASE_URL"
if (-not $appBaseUrl) {
    Write-Host "[ERROR] APP_BASE_URL is not set. Add  APP_BASE_URL=https://your-service-url  to .env (or set `$env:APP_BASE_URL for this session) and re-run." -ForegroundColor Red
    exit 1
}
$fromEmail = Get-DotEnvValue "FROM_EMAIL"     # optional -- code falls back to onboarding@resend.dev
$alertEmail = Get-DotEnvValue "ALERT_EMAIL"   # optional -- omits Reply-To on the draft-order email if unset

$envVars = @(
    "USE_LOCAL_DATA=False",
    "DEBUG_PAGE_LOAD=False",
    "APP_BASE_URL=$appBaseUrl",
    "SMTP_SERVER=smtp.gmail.com",
    "SMTP_PORT=587",
    "SMTP_USER=your_email@gmail.com"
)
if ($fromEmail) { $envVars += "FROM_EMAIL=$fromEmail" }
if ($alertEmail) { $envVars += "ALERT_EMAIL=$alertEmail" }

Write-Host "[BUILD] Building Docker Image for project $PROJECT_ID..." -ForegroundColor Cyan
gcloud builds submit --tag $IMAGE_TAG

Write-Host "[DEPLOY] Deploying to Google Cloud Run..." -ForegroundColor Cyan
gcloud run deploy winspool `
    --image $IMAGE_TAG `
    --platform managed `
    --region us-east1 `
    --allow-unauthenticated `
    --set-env-vars ($envVars -join ",") `
    --set-secrets "FIREBASE_CREDENTIALS=FIREBASE_CREDENTIALS:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest,SMTP_PASSWORD=SMTP_PASSWORD:latest,JWT_SECRET=JWT_SECRET:latest,RESEND_API_KEY=RESEND_API_KEY:latest"

if ($LASTEXITCODE -eq 0) {
    Write-Host "[SUCCESS] Cloud Run Deployment Complete!" -ForegroundColor Green
}
else {
    Write-Host "[ERROR] Cloud Run Deployment Failed. See errors above." -ForegroundColor Red
    exit 1
}

# --- Scheduled jobs (winspool-sync-daily, winspool-live-scores,
# winspool-schedule-kickoffs, winspool-predict-daily) ---
#
# This rebuilds and redeploys the two job images every run. It does NOT
# repeat any of Task 9's one-time GCP setup (API enablement, service
# account/IAM, the Cloud Tasks queue, or the Cloud Scheduler triggers) --
# `gcloud run jobs update` only swaps the image on jobs that already exist.
# If you need to create these jobs or their scheduler/queue from scratch,
# see docs/superpowers/plans/2026-08-19-scheduled-jobs.md Task 9.
$SYNC_IMAGE = "us-east1-docker.pkg.dev/$PROJECT_ID/winspool/winspool-sync:latest"
$PREDICT_IMAGE = "us-east1-docker.pkg.dev/$PROJECT_ID/winspool/winspool-predict:latest"

Write-Host "[BUILD] Building winspool-sync image..." -ForegroundColor Cyan
gcloud builds submit --config=cloudbuild-sync.yaml --project=$PROJECT_ID .
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] winspool-sync image build failed. Scheduled jobs NOT updated." -ForegroundColor Red
    exit 1
}

Write-Host "[BUILD] Building winspool-predict image (this installs TensorFlow -- can take several minutes)..." -ForegroundColor Cyan
gcloud builds submit --config=cloudbuild-predict.yaml --project=$PROJECT_ID .
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] winspool-predict image build failed. Scheduled jobs NOT updated." -ForegroundColor Red
    exit 1
}

Write-Host "[DEPLOY] Updating scheduled Cloud Run Jobs to the freshly built images..." -ForegroundColor Cyan
$syncJobs = @("winspool-sync-daily", "winspool-live-scores", "winspool-schedule-kickoffs")
foreach ($job in $syncJobs) {
    gcloud run jobs update $job --image=$SYNC_IMAGE --region=us-east1 --project=$PROJECT_ID
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to update job $job." -ForegroundColor Red
        exit 1
    }
}
gcloud run jobs update winspool-predict-daily --image=$PREDICT_IMAGE --region=us-east1 --project=$PROJECT_ID
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to update job winspool-predict-daily." -ForegroundColor Red
    exit 1
}

Write-Host "[SUCCESS] Scheduled jobs updated!" -ForegroundColor Green
Write-Host "[FINISH] Deployment process finished." -ForegroundColor Green
