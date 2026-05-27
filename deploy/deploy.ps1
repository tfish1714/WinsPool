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

Write-Host "[BUILD] Building Docker Image for project $PROJECT_ID..." -ForegroundColor Cyan
gcloud builds submit --tag $IMAGE_TAG

Write-Host "[DEPLOY] Deploying to Google Cloud Run..." -ForegroundColor Cyan
gcloud run deploy winspool `
    --image $IMAGE_TAG `
    --platform managed `
    --region us-east1 `
    --allow-unauthenticated `
    --set-env-vars "USE_LOCAL_DATA=False" `
    --set-env-vars "DEBUG_PAGE_LOAD=False" `
    --set-env-vars "SMTP_SERVER=smtp.gmail.com" `
    --set-env-vars "SMTP_PORT=587" `
    --set-env-vars "SMTP_USER=your_email@gmail.com" `
    --set-env-vars "FROM_EMAIL=your_email@gmail.com" `
    --set-secrets "FIREBASE_CREDENTIALS=FIREBASE_CREDENTIALS:latest" `
    --set-secrets "GEMINI_API_KEY=GEMINI_API_KEY:latest" `
    --set-secrets "SMTP_PASSWORD=SMTP_PASSWORD:latest" `
    --set-secrets "JWT_SECRET=JWT_SECRET:latest" `
    --set-env-vars "ROOM_CODE=your_room_code"

if ($LASTEXITCODE -eq 0) {
    Write-Host "[SUCCESS] Cloud Run Deployment Complete!" -ForegroundColor Green
    
    # Optional Functions Deployment
    $deployFuncs = Read-Host "Do you want to deploy Cloud Functions? (y/n)"
    if ($deployFuncs -eq "y") {
        .\deploy\deploy_functions.ps1
    }
    
    Write-Host "[FINISH] Deployment process finished." -ForegroundColor Green
}
else {
    Write-Host "[ERROR] Cloud Run Deployment Failed. See errors above." -ForegroundColor Red
}
