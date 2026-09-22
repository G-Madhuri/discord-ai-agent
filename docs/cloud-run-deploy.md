# Google Cloud Run Deployment Guide

This guide details deploying the **TaskPilot Discord AI Agent** to Google Cloud Run with low latency, background CPU allocation, and secure Secret Manager integration.

---

## 🏗️ Architecture & Configuration

| Parameter | Value |
| --- | --- |
| **GCP Project** | `discord-assignment-agent` |
| **Region** | `us-central1` |
| **Artifact Registry Repository** | `us-central1-docker.pkg.dev/discord-assignment-agent/discord-agent` |
| **Service Account** | `discord-agent-run@discord-assignment-agent.iam.gserviceaccount.com` |
| **Service Name** | `discord-agent` |
| **CPU / Memory** | 1 vCPU / 512Mi |
| **Performance Flags** | `--no-cpu-throttling --min-instances=1` |

---

## ⚡ Performance Optimization

Cloud Run is configured with `--no-cpu-throttling` and `--min-instances=1`:
1. **Zero Cold Starts**: Always-warm instance handles Discord interaction PING and Slash command requests instantly (< 3 seconds).
2. **Unthrottled Background Processing**: Asynchronous tasks (LLM planning, pgvector RAG embeddings, background DB commits) execute at full CPU speed after Discord HTTP interaction responses return.

---

## 🔐 Mounted Secrets (Secret Manager)

The service mounts 14 runtime secrets directly from GCP Secret Manager:

- `DATABASE_URL`
- `DIRECT_URL`
- `INTERNAL_API_TOKEN`
- `DISCORD_BOT_TOKEN`
- `DISCORD_APPLICATION_ID`
- `DISCORD_PUBLIC_KEY`
- `DISCORD_GUILD_ID`
- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`
- `GOOGLE_GENAI_USE_VERTEXAI`
- `GEMINI_MODEL`
- `AGENT_MODE`
- `ENV`
- `LOG_LEVEL`

---

## 🚀 Deployment Commands

### 1. Build and Push Image to Artifact Registry

```bash
gcloud builds submit \
  --tag us-central1-docker.pkg.dev/discord-assignment-agent/discord-agent/app:v16 \
  --project=discord-assignment-agent .
```

### 2. Deploy to Cloud Run

```bash
gcloud run deploy discord-agent \
  --image=us-central1-docker.pkg.dev/discord-assignment-agent/discord-agent/app:v16 \
  --region=us-central1 \
  --project=discord-assignment-agent \
  --no-cpu-throttling \
  --min-instances=1 \
  --memory=512Mi --cpu=1 \
  --allow-unauthenticated \
  --service-account=discord-agent-run@discord-assignment-agent.iam.gserviceaccount.com \
  --set-secrets="DATABASE_URL=DATABASE_URL:latest,DIRECT_URL=DIRECT_URL:latest,INTERNAL_API_TOKEN=INTERNAL_API_TOKEN:latest,DISCORD_BOT_TOKEN=DISCORD_BOT_TOKEN:latest,DISCORD_APPLICATION_ID=DISCORD_APPLICATION_ID:latest,DISCORD_PUBLIC_KEY=DISCORD_PUBLIC_KEY:latest,DISCORD_GUILD_ID=DISCORD_GUILD_ID:latest,GOOGLE_CLOUD_PROJECT=GOOGLE_CLOUD_PROJECT:latest,GOOGLE_CLOUD_LOCATION=GOOGLE_CLOUD_LOCATION:latest,GOOGLE_GENAI_USE_VERTEXAI=GOOGLE_GENAI_USE_VERTEXAI:latest,GEMINI_MODEL=GEMINI_MODEL:latest,AGENT_MODE=AGENT_MODE:latest,ENV=ENV:latest,LOG_LEVEL=LOG_LEVEL:latest"
```

### 3. Verify Deployment & Get Service URL

```bash
gcloud run services describe discord-agent \
  --region=us-central1 \
  --project=discord-assignment-agent \
  --format="value(status.url)"
```

### 4. Test Health Endpoint

```bash
curl -s <SERVICE_URL>/health
```
Expected response: `{"status":"ok"}`
