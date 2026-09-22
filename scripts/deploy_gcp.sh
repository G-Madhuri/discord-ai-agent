#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="discord-assignment-agent"
REGION="us-central1"
SERVICE_NAME="discord-agent"
IMAGE_TAG="us-central1-docker.pkg.dev/${PROJECT_ID}/${SERVICE_NAME}/app:v16"
SERVICE_ACCOUNT="discord-agent-run@${PROJECT_ID}.iam.gserviceaccount.com"

SECRETS="DATABASE_URL=DATABASE_URL:latest,\
DIRECT_URL=DIRECT_URL:latest,\
INTERNAL_API_TOKEN=INTERNAL_API_TOKEN:latest,\
DISCORD_BOT_TOKEN=DISCORD_BOT_TOKEN:latest,\
DISCORD_APPLICATION_ID=DISCORD_APPLICATION_ID:latest,\
DISCORD_PUBLIC_KEY=DISCORD_PUBLIC_KEY:latest,\
DISCORD_GUILD_ID=DISCORD_GUILD_ID:latest,\
GOOGLE_CLOUD_PROJECT=GOOGLE_CLOUD_PROJECT:latest,\
GOOGLE_CLOUD_LOCATION=GOOGLE_CLOUD_LOCATION:latest,\
GOOGLE_GENAI_USE_VERTEXAI=GOOGLE_GENAI_USE_VERTEXAI:latest,\
GEMINI_MODEL=GEMINI_MODEL:latest,\
AGENT_MODE=AGENT_MODE:latest,\
ENV=ENV:latest,\
LOG_LEVEL=LOG_LEVEL:latest"

echo "Building and submitting container image..."
gcloud builds submit --tag "${IMAGE_TAG}" --project="${PROJECT_ID}" .

echo "Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
  --image="${IMAGE_TAG}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --no-cpu-throttling \
  --min-instances=1 \
  --memory=512Mi --cpu=1 \
  --allow-unauthenticated \
  --service-account="${SERVICE_ACCOUNT}" \
  --set-secrets="${SECRETS}"

SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" --region="${REGION}" --project="${PROJECT_ID}" --format="value(status.url)")

echo "=========================================="
echo "Deployment successful!"
echo "Service URL: ${SERVICE_URL}"
echo "Interactions Endpoint URL: ${SERVICE_URL}/discord/interactions"
echo "=========================================="
