# Deployment (Google Cloud)

**Nothing is deployed yet.** This is the intended target and the configuration
that exists to support it.

```
Cloud Run (FastAPI + agent)
  ↓
Vertex AI / Gemini
  ↓
Cloud SQL for PostgreSQL
  ↓
Project knowledge (chunks + vectors in PostgreSQL)
```

## Secrets

No service-account JSON file is created or needed.

* **Cloud Run** uses its attached service account for Vertex AI —
  Application Default Credentials, no key file.
* **Everything else** (Discord token, database URL) lives in **Secret Manager**
  and is injected as an environment variable with `--set-secrets`. The
  application reads plain environment variables, so no Secret Manager client
  code is required.

```bash
printf '%s' "$DISCORD_TOKEN" | gcloud secrets create discord-bot-token --data-file=-
printf '%s' "$DB_URL"        | gcloud secrets create database-url      --data-file=-
```

Grant the runtime service account `roles/secretmanager.secretAccessor`,
`roles/aiplatform.user` and `roles/cloudsql.client`.

## Build and deploy

```bash
gcloud builds submit --tag gcr.io/$PROJECT/discord-assignment-agent
```

```bash
gcloud run deploy discord-assignment-agent \
  --image gcr.io/$PROJECT/discord-assignment-agent \
  --region $REGION \
  --no-allow-unauthenticated \
  --add-cloudsql-instances $PROJECT:$REGION:$INSTANCE \
  --set-env-vars ENVIRONMENT=production,GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=$REGION,AGENT_MODE=llm,EMBEDDING_PROVIDER=vertex,VECTOR_STORE=postgres \
  --set-secrets DATABASE_URL=database-url:latest,DISCORD_BOT_TOKEN=discord-bot-token:latest,INTERNAL_API_TOKEN=internal-api-token:latest
```

Notes:

* The container reads `PORT` from Cloud Run; the Dockerfile already does.
* Keep the service private (`--no-allow-unauthenticated`). `INTERNAL_API_TOKEN`
  is a second layer, not the only one.
* Over a Cloud SQL unix socket the URL is
  `postgresql+asyncpg://USER:PASS@/DB?host=/cloudsql/PROJECT:REGION:INSTANCE`.

## The Discord bot process

`discord.py` holds a persistent gateway websocket, which does not fit Cloud
Run's request-scoped model. Two options, neither implemented yet:

1. **Cloud Run with `--min-instances=1` and `--no-cpu-throttling`**, running
   `python -m app.discord.run_bot` as a second service. Simple; costs one
   always-on instance.
2. **Discord HTTP interactions** — Discord POSTs signed interaction payloads to
   an endpoint, which suits Cloud Run's scale-to-zero. This needs Ed25519
   signature verification using `DISCORD_PUBLIC_KEY`; the variable is reserved
   for it, the verification is **not written yet**.

Start with option 1.

## Migrations

Alembic does not run automatically at startup. Run it as a one-off job (Cloud
Run job or Cloud Build step) before promoting a new revision:

```bash
alembic upgrade head
```

## Before production

- [ ] Generate and review the initial migration against PostgreSQL
- [ ] Switch `EMBEDDING_PROVIDER` to `vertex` and re-ingest existing documents
      (hashed dev vectors are not comparable with Vertex ones)
- [ ] Decide the bot hosting option above
- [ ] Replace the in-Python similarity search if a project's corpus grows past
      a few thousand chunks (see `app/rag/stores/postgres.py`)
