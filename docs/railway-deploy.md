# Railway Deployment Guide

This guide provides step-by-step instructions for deploying the **TaskPilot Discord AI Agent** on [Railway](https://railway.app/).

---

## 🚀 Overview & Cost Estimate

Railway provides serverless and dedicated container hosting with automatic GitHub integration.

- **Estimated Cost**: ~$0.00 – $5.00 / month (with Serverless sleep mode and hard budget limits).
- **Advantages over Cloud Run**:
  - **No Background CPU Throttling**: Background tasks (LLM plan generation, pgvector document chunking/embeddings, bulk database commits) run at full speed without CPU starvation after HTTP responses finish.
  - **Simpler Secret Management**: Direct environment variable configuration in the Railway UI dashboard.
  - **Automatic Deployments**: Continuous deployment triggered on `git push origin main`.

---

## 📋 Step-by-Step Deployment Instructions

### 1. Account & Project Setup
1. Sign up or log in to [Railway](https://railway.app/).
2. Click **New Project** → **Deploy from GitHub repo**.
3. Select your repository (`discord-ai-agent`).
4. Railway will automatically detect `railway.json` and use the root [`Dockerfile`](../Dockerfile).

---

### 2. Environment Variables Configuration
In your Railway service dashboard, navigate to the **Variables** tab and add the required environment variables (see [`.env.example`](../.env.example)):

#### Application & Service
- `ENV=production`
- `ENVIRONMENT=production`
- `LOG_LEVEL=INFO`
- `API_PREFIX=/api/v1`
- `API_HOST=0.0.0.0`

#### Database (Neon PostgreSQL)
- `DATABASE_URL=postgresql+asyncpg://user:password@host-pooler/dbname?ssl=require` (Pooled connection)
- `DIRECT_URL=postgresql+asyncpg://user:password@host/dbname?ssl=require` (Direct host for migrations)

#### Vertex AI / Google Cloud
- `GOOGLE_CLOUD_PROJECT=<YOUR_GOOGLE_CLOUD_PROJECT>`
- `GOOGLE_CLOUD_LOCATION=us-central1`
- `GOOGLE_GENAI_USE_VERTEXAI=TRUE`
- `GEMINI_MODEL=gemini-2.5-flash`

#### Discord Integration
- `DISCORD_BOT_TOKEN=<YOUR_DISCORD_BOT_TOKEN>`
- `DISCORD_APPLICATION_ID=<YOUR_DISCORD_APPLICATION_ID>`
- `DISCORD_PUBLIC_KEY=<YOUR_DISCORD_PUBLIC_KEY>`
- `DISCORD_GUILD_ID=<YOUR_DISCORD_GUILD_ID>`
- `INTERNAL_API_TOKEN=<YOUR_INTERNAL_API_TOKEN>`

#### RAG & Vector Engine
- `RAG_VECTOR_STORE=postgres`
- `RAG_RETRIEVAL_MODE=hybrid`
- `RAG_CHUNK_METHOD=semantic`
- `RAG_EMBEDDING_MODEL=text-embedding-004`

---

### 3. Usage & Budget Safety Limit
To prevent unexpected charges:
1. Navigate to **Project Settings** → **Usage Limits**.
2. Set a hard limit (e.g. **$5.00 / month**).

---

### 4. Deploying & Registering Discord Endpoint

1. Wait for Railway to complete the build and deploy.
2. In Railway **Settings** → **Networking**, click **Generate Domain** to get your public URL (e.g., `https://discord-agent-production.up.railway.app`).
3. Verify service health:
   ```bash
   curl https://<YOUR_RAILWAY_URL>/health
   # Expected response: {"status":"ok"}
   ```
4. Update your Discord Application Interaction Endpoint:
   - Open the [Discord Developer Portal](https://discord.com/developers/applications).
   - Select your application.
   - Set **Interactions Endpoint URL** to:
     `https://<YOUR_RAILWAY_URL>/discord/interactions`
   - Discord will send a test `PING` signature check. Railway will respond with HTTP 200 `PONG`.

---

## 🔄 Updates, Rollbacks & Troubleshooting

- **Updating Secrets**: Go to the **Variables** tab in Railway, update the key, and Railway will automatically trigger a zero-downtime redeploy.
- **Rollbacks**: If a deployment fails, go to the **Deployments** tab in Railway, locate the previous successful deployment, and click **Rollback**.
- **Historical Reference**: For historical details on the initial Google Cloud Run deployment, see [`docs/archive/gcp-deploy.md`](archive/gcp-deploy.md).
