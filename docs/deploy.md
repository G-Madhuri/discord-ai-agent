# Cloud Run Deployment Guide

This document details the production containerization, Google Cloud Run architecture, Secret Manager integration, and operational workflows for the Discord AI Assignment Agent.

---

## 1. Prerequisites

Before deploying, ensure the following GCP components are configured:
- **GCP Project**: `discord-assignment-agent`
- **Region**: `us-central1`
- **Active GCP APIs**:
  - `run.googleapis.com` (Cloud Run Admin API)
  - `cloudbuild.googleapis.com` (Cloud Build API)
  - `artifactregistry.googleapis.com` (Artifact Registry API)
  - `aiplatform.googleapis.com` (Vertex AI API)
  - `secretmanager.googleapis.com` (Secret Manager API)
- **Local Authentication**: `gcloud auth application-default login` / `gcloud auth login`
- **Billing**: Active Google Cloud billing account linked to the project.

---

## 2. Production Dockerfile Security

The application is containerized using `Dockerfile` (and `infra/Dockerfile`):
- **Base Image**: `python:3.12-slim`
- **Non-Root Execution**: Runs as non-root user `appuser` (UID 1000) for security hardening.
- **No Baked Secrets**: Zero credentials or tokens are baked into the image. All configurations are injected dynamically at runtime via environment variables from Secret Manager.
- **Port**: Listens on `$PORT` (Cloud Run default: `8080`).

---

## 3. Image Build & Push (Artifact Registry)

Build images using Cloud Build and store them in Artifact Registry:

```bash
# Create repository (if not existing)
gcloud artifacts repositories create discord-agent \
  --repository-format=docker \
  --location=us-central1 \
  --description="Discord Assignment Agent Docker repository"

# Build and push container image
gcloud builds submit --tag us-central1-docker.pkg.dev/discord-assignment-agent/discord-agent/app:v2 .
```

- **Repository URI**: `us-central1-docker.pkg.dev/discord-assignment-agent/discord-agent`
- **Image URI**: `us-central1-docker.pkg.dev/discord-assignment-agent/discord-agent/app:v2`

---

## 4. Secret Manager Mapping

All sensitive credentials and environment configs are stored in GCP Secret Manager and mounted as environment variables:

| Environment Variable | Secret Manager Name | Purpose |
|---|---|---|
| `DATABASE_URL` | `DATABASE_URL` | Pooled Neon PostgreSQL connection URL |
| `DIRECT_URL` | `DIRECT_URL` | Direct Neon PostgreSQL host for DDL migrations |
| `INTERNAL_API_TOKEN` | `INTERNAL_API_TOKEN` | Shared internal API token for Bot -> Backend auth guard |
| `DISCORD_BOT_TOKEN` | `DISCORD_BOT_TOKEN` | Discord bot authentication token |
| `DISCORD_APPLICATION_ID` | `DISCORD_APPLICATION_ID` | Discord Application ID |
| `DISCORD_PUBLIC_KEY` | `DISCORD_PUBLIC_KEY` | Ed25519 Public Key for HTTP interaction signature verification |
| `DISCORD_GUILD_ID` | `DISCORD_GUILD_ID` | Development Discord Guild ID |
| `GOOGLE_CLOUD_PROJECT` | `GOOGLE_CLOUD_PROJECT` | GCP Project ID (`discord-assignment-agent`) |
| `GOOGLE_CLOUD_LOCATION` | `GOOGLE_CLOUD_LOCATION` | GCP Region (`us-central1`) |
| `GEMINI_MODEL` | `GEMINI_MODEL` | Gemini LLM model identifier (`gemini-2.5-flash`) |
| `GOOGLE_GENAI_USE_VERTEXAI` | `GOOGLE_GENAI_USE_VERTEXAI` | Vertex AI client flag (`TRUE`) |
| `AGENT_MODE` | `AGENT_MODE` | Execution mode (`deterministic`) |
| `ENV` | `ENV` | Environment identifier (`production`) |
| `LOG_LEVEL` | `LOG_LEVEL` | Logging level (`INFO`) |

---

## 5. Service Account & Least-Privilege IAM Roles

A dedicated service account is bound to the Cloud Run service:
- **Service Account**: `discord-agent-run@discord-assignment-agent.iam.gserviceaccount.com`
- **Granted IAM Roles**:
  - `roles/aiplatform.user` (Vertex AI prediction calls)
  - `roles/secretmanager.secretAccessor` (Read access to Secret Manager secrets)

---

## 6. Cloud Run Service Configuration

Deploy to Cloud Run using the following command:

```bash
gcloud run deploy discord-agent \
  --image=us-central1-docker.pkg.dev/discord-assignment-agent/discord-agent/app:v2 \
  --region=us-central1 \
  --service-account=discord-agent-run@discord-assignment-agent.iam.gserviceaccount.com \
  --allow-unauthenticated \
  --memory=512Mi \
  --cpu=1 \
  --min-instances=0 \
  --max-instances=3 \
  --concurrency=40 \
  --timeout=60s \
  --port=8080 \
  --set-secrets="DATABASE_URL=DATABASE_URL:latest,DIRECT_URL=DIRECT_URL:latest,INTERNAL_API_TOKEN=INTERNAL_API_TOKEN:latest,DISCORD_BOT_TOKEN=DISCORD_BOT_TOKEN:latest,DISCORD_APPLICATION_ID=DISCORD_APPLICATION_ID:latest,DISCORD_PUBLIC_KEY=DISCORD_PUBLIC_KEY:latest,DISCORD_GUILD_ID=DISCORD_GUILD_ID:latest,GOOGLE_CLOUD_PROJECT=GOOGLE_CLOUD_PROJECT:latest,GOOGLE_CLOUD_LOCATION=GOOGLE_CLOUD_LOCATION:latest,GEMINI_MODEL=GEMINI_MODEL:latest,GOOGLE_GENAI_USE_VERTEXAI=GOOGLE_GENAI_USE_VERTEXAI:latest,AGENT_MODE=AGENT_MODE:latest,ENV=ENV:latest,LOG_LEVEL=LOG_LEVEL:latest"
```

- **Live Service URL**: `https://discord-agent-499979613721.us-central1.run.app`
- **Public Health URL**: `https://discord-agent-499979613721.us-central1.run.app/health`
- **Discord Interactions Endpoint**: `https://discord-agent-499979613721.us-central1.run.app/discord/interactions`

---

## 7. Operational Workflows

### How to Update Secrets
1. Add a new secret version:
   ```bash
   echo "NEW_SECRET_VALUE" | gcloud secrets versions add DISCORD_BOT_TOKEN --data-file=-
   ```
2. Redeploy the service revision to pick up `:latest` secret versions:
   ```bash
   gcloud run deploy discord-agent --image=us-central1-docker.pkg.dev/discord-assignment-agent/discord-agent/app:v2 --region=us-central1
   ```

### Post-Deployment Verification Script
Run the automated post-deployment smoke test to verify health, auth guard, Ed25519 signature checks, slash command workflow, and cross-guild isolation against the live Cloud Run deployment:

```bash
python backend/scripts/smoke_test_prod.py [SERVICE_URL]
```

### How to Roll Back Revisions
If a revision causes issues, instantly route 100% of traffic back to a previous revision:
```bash
gcloud run services update-traffic discord-agent --region=us-central1 --to-revisions=discord-agent-00001-qnb=100
```

---

## 8. Cost & Scaling Considerations

- **Scale-to-Zero**: `min-instances=0` ensures $0 cost when idle.
- **Secret Manager Costs**: $0.06 / secret / month + $0.03 / 10k access operations.
- **Artifact Registry**: Standard GCS storage pricing for container layers.

---

## 9. Cost & Latency Trade-offs

- **Current configuration**:
  - `cpu-throttling = true` (ON, default)
  - `min-instances = 0` (scale-to-zero)
  - Background tasks are CPU-throttled after the initial HTTP response (`{"type": 5}`) is returned
  - → ~10s command execution latency after immediate acknowledgement

- **Alternative configuration (NOT used, for cost reasons)**:
  - `--no-cpu-throttling` + `--min-instances=1`
  - → ~2s command execution latency
  - → ~$45/month in Cloud Run compute credits
  - → Only justified at high production traffic levels with strict SLAs

- **Cost estimate for current config**:
  - Idle compute: ~$0 / month (scale-to-zero)
  - Active traffic spend: <$1 / month
  - Cloud Scheduler: ~$0.10 / month
  - Secret Manager: ~$0.06 / month
  - Artifact Registry: ~$0.10 / month (few images)
  - **Total**: <$1 / month

- **Why the ~10s latency is acceptable**:
  - Discord immediately displays *"TaskPilot is thinking..."* (< 15ms)
  - The formatted command reply arrives shortly after via webhook PATCH
  - This project prioritizes minimal operating cost (<$1/month) over low-latency SLAs

- **When to revisit**:
  - For live interactive demonstrations, temporarily set `--no-cpu-throttling --min-instances=1`, perform the demo, and immediately revert back to `cpu-throttling=true` and `min-instances=0`.
  - Never leave `--no-cpu-throttling` or `--min-instances=1` running indefinitely.

---

## 10. Scope Exclusions (Not Deployed Yet)

- **Pinecone / Hybrid RAG**: Pinecone integration is reserved for future tuning.
- **Planning Mode**: Planning mode / replanning remains strictly disabled via the plan guard.
