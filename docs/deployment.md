# Deployment Overview

The **TaskPilot Discord AI Agent** is fully portable and supports deployment on both **Google Cloud Run** and **Railway**.

```
Cloud Run / Railway Container (FastAPI + Discord Interactions Agent)
  ↓
Neon PostgreSQL (Async Driver + pgvector)
  ↓
Google Vertex AI (Gemini 2.5 Flash + Text Embeddings)
```

---

## 🎯 Supported Deployment Targets

### 1. ☁️ Google Cloud Run
For production deployments requiring zero cold starts, continuous background CPU allocation (`--no-cpu-throttling`), and native Secret Manager integration.

👉 **[Google Cloud Run Deployment Guide](cloud-run-deploy.md)**

### 2. 🚂 Railway
For containerized deployments with hard budget limits and automatic GitHub integration.

👉 **[Railway Deployment Guide](railway-deploy.md)**

---

## 🔐 Environment Variables & Secrets

Key runtime variables include:
- `DATABASE_URL` / `DIRECT_URL`: Neon PostgreSQL connection strings with `ssl=require`.
- `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION`: Google Vertex AI platform settings.
- `DISCORD_BOT_TOKEN` / `DISCORD_PUBLIC_KEY`: Discord application credentials and Ed25519 signature verification key.
- `INTERNAL_API_TOKEN`: Shared token for internal backend endpoints.

See [`.env.example`](../.env.example) for a complete template.

