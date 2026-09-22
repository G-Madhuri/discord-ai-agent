# Deployment Overview

The **TaskPilot Discord AI Agent** is configured for deployment on [Railway](https://railway.app/) using containerized runtime deployment.

```
Railway Container (FastAPI + Discord Interactions Agent)
  ↓
Neon PostgreSQL (Async Driver + pgvector)
  ↓
Google Vertex AI (Gemini 2.5 Flash + Text Embeddings)
```

---

## 🚀 Active Deployment Target: Railway

For full step-by-step instructions on setting up Railway continuous deployment, configuring environment variables, setting budget limits, and linking Discord interaction webhooks, please refer to:

👉 **[Railway Deployment Guide](railway-deploy.md)**

---

## 🔐 Environment Variables & Secrets

Environment variables are passed directly into the container via Railway's dashboard (or `.env` in local development).

Key runtime variables include:
- `DATABASE_URL` / `DIRECT_URL`: Neon PostgreSQL connection strings with `ssl=require`.
- `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION`: Google Vertex AI platform settings.
- `DISCORD_BOT_TOKEN` / `DISCORD_PUBLIC_KEY`: Discord application credentials and Ed25519 signature verification key.
- `INTERNAL_API_TOKEN`: Shared token for internal backend endpoints.

See [`.env.example`](../.env.example) for a complete template.

---

## 📜 Historical Reference: Google Cloud Run

> [!NOTE]
> The service was originally deployed on Google Cloud Run. While Cloud Run provides robust scale-to-zero capabilities, its default CPU throttling after HTTP responses complete caused background execution delays for long-running LLM project planning and RAG embedding workflows. Disabling CPU throttling required continuous baseline container allocation (`--no-cpu-throttling --min-instances=1`), incurring ~$45/month in baseline hosting costs.
>
> Railway was chosen for active deployment to eliminate background CPU throttling while maintaining low costs with serverless sleeping and hard budget limits.
>
> For historical GCP Cloud Run deployment configurations and IAM permissions, see [`docs/archive/gcp-deploy.md`](archive/gcp-deploy.md).
