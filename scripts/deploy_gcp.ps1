$ErrorActionPreference = "Stop"

$ProjectId = "discord-assignment-agent"
$Region = "us-central1"
$ServiceName = "discord-agent"
$ImageTag = "us-central1-docker.pkg.dev/$ProjectId/$ServiceName/app:v16"
$ServiceAccount = "discord-agent-run@$ProjectId.iam.gserviceaccount.com"

$Secrets = "DATABASE_URL=DATABASE_URL:latest,DIRECT_URL=DIRECT_URL:latest,INTERNAL_API_TOKEN=INTERNAL_API_TOKEN:latest,DISCORD_BOT_TOKEN=DISCORD_BOT_TOKEN:latest,DISCORD_APPLICATION_ID=DISCORD_APPLICATION_ID:latest,DISCORD_PUBLIC_KEY=DISCORD_PUBLIC_KEY:latest,DISCORD_GUILD_ID=DISCORD_GUILD_ID:latest,GOOGLE_CLOUD_PROJECT=GOOGLE_CLOUD_PROJECT:latest,GOOGLE_CLOUD_LOCATION=GOOGLE_CLOUD_LOCATION:latest,GOOGLE_GENAI_USE_VERTEXAI=GOOGLE_GENAI_USE_VERTEXAI:latest,GEMINI_MODEL=GEMINI_MODEL:latest,AGENT_MODE=AGENT_MODE:latest,ENV=ENV:latest,LOG_LEVEL=LOG_LEVEL:latest"

Write-Host "Building and submitting container image..."
gcloud builds submit --tag $ImageTag --project $ProjectId .

Write-Host "Deploying to Cloud Run..."
gcloud run deploy $ServiceName `
  --image $ImageTag `
  --region $Region `
  --project $ProjectId `
  --no-cpu-throttling `
  --min-instances 1 `
  --memory 512Mi --cpu 1 `
  --allow-unauthenticated `
  --service-account $ServiceAccount `
  --set-secrets $Secrets

$ServiceUrl = gcloud run services describe $ServiceName --region $Region --project $ProjectId --format "value(status.url)"

Write-Host "=========================================="
Write-Host "Deployment successful!"
Write-Host "Service URL: $ServiceUrl"
Write-Host "Interactions Endpoint URL: $ServiceUrl/discord/interactions"
Write-Host "=========================================="
