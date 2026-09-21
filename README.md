# Discord AI Assignment Agent

An agent that assigns **existing** tasks to the **right existing person** in a
Discord server, using what is actually known about the team, the project and
the work — and that can explain and defend every decision afterwards.

```
✅ TASK-101 assigned to Rahul.
Why:
• Skill match: React, JavaScript (top proficiency 5/5)
• Role matches the task: Frontend Engineer
• Project knowledge: 'Frontend Architecture' mentions React
• Current workload: 2 active task(s) of 3
• No unresolved dependency blocking the task
```

This is not a project planner. If a plan exists, the agent leaves it alone.

## What it does

`/assign TASK-101` runs one workflow: find the task, gather the evidence, rank
the team, assign the task, persist the decision, explain it. It considers task
requirements, member skills and roles, relevant project documentation, current
workload, existing assignments, task dependencies and availability.

Four guarantees shape the design:

* **It does not invent.** Skills, members, tasks and documents come from the
  database. If something is not recorded, the agent says so instead of guessing.
* **It does not plan.** No tool can create or modify a project plan; the plan
  guard refuses implicit replanning.
* **Every decision is auditable.** The full evaluation — including the
  candidates that lost and why — is stored with the assignment.
* **Servers are isolated.** Guild A's data cannot reach guild B, even if a
  prompt asks for it.

## Quick start

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

```bash
pip install -r requirements-dev.txt
```

```bash
copy .env.example .env
```

Start the API:

```bash
cd backend && uvicorn app.main:app --reload --port 8000
```

```bash
curl http://localhost:8000/health
```

```json
{ "status": "ok" }
```

`/health` never touches the database, so it answers before PostgreSQL exists.
`/readyz` reports whether the database is actually reachable, and
`http://localhost:8000/docs` lists the API.

### With a database (PostgreSQL)

Set `DATABASE_URL` (pooled connection) and `DIRECT_URL` (direct host, bypassing PgBouncer transaction mode for Alembic DDL) in `.env`:

```env
DATABASE_URL=postgresql+asyncpg://user:password@host-pooler/dbname?ssl=require
DIRECT_URL=postgresql+asyncpg://user:password@host/dbname?ssl=require
```

Run Alembic migrations (uses `DIRECT_URL` automatically):

```bash
cd backend && alembic upgrade head
```

Seed the demo server:

```bash
python scripts/seed_demo.py
```

The seed script creates the example server — Rahul, Ananya, Madhuri and Arjun,
with TASK-101 to TASK-104 and a technical overview document — so the assignment
flow can be exercised without Discord.

### Tests

```bash
pytest
```

Runs the complete test suite (37+ baseline unit/integration tests and PostgreSQL schema constraint regression tests).

### Run the Discord bot locally

1. Ensure `.env` contains your secrets and configurations:
   ```env
   DISCORD_BOT_TOKEN=your_discord_bot_token_here
   DISCORD_APPLICATION_ID=your_discord_application_id_here
   DISCORD_PUBLIC_KEY=your_discord_public_key_here
   DISCORD_GUILD_ID=your_discord_guild_id_here
   INTERNAL_API_TOKEN=your_internal_api_token_here
   ```

2. Start the FastAPI backend:
   ```bash
   uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
   ```

3. Start the Discord Gateway Bot process:
   ```bash
   python -m app.discord.bot
   ```

4. Invite the bot to your test Discord server using the OAuth2 URL:
   [Bot Invite Link](https://discord.com/oauth2/authorize?client_id=1551280703633629266&permissions=68608&integration_type=0&scope=bot+applications.commands)

## Commands

| Command | Purpose |
|---|---|
| `/assign TASK-ID` | **the important one** — assign an existing task |
| `/assignment_history` | audit trail of decisions |
| `/project create`, `/project info`, `/project knowledge` | project setup and status |
| `/member add`, `/member profile`, `/member skills` | team data |
| `/task create`, `/task list` | task data |

## Configuration

Everything is environment-based; see [`.env.example`](.env.example). Nothing is
hardcoded and `.env` is git-ignored.

| Variable | Default | Notes |
|---|---|---|
| `AGENT_MODE` | `deterministic` | `llm` enables Gemini via Google ADK |
| `EMBEDDING_PROVIDER` | `hashing` | offline dev embeddings; `vertex` for production |
| `VECTOR_STORE` | `postgres` | `memory` for tests |
| `ASSIGNMENT_WEIGHT_*` | see docs | tune the scoring components |

`AGENT_MODE=deterministic` is the default and needs no Google credentials: the
scoring engine decides and the service persists. `llm` puts Gemini in front of
the same tools and the same persistence path.

## Deploy to Cloud Run

The application is deployed to Google Cloud Run with secret resolution via Secret Manager:

- **Production Health URL**: `https://discord-agent-499979613721.us-central1.run.app/health`
- **Discord Interactions URL**: `https://discord-agent-499979613721.us-central1.run.app/discord/interactions`

For step-by-step instructions, container specs, IAM roles, and secret mappings, see [`docs/deploy.md`](docs/deploy.md).

## Layout

```
backend/app/
  api/        HTTP routes, deps, error mapping
  agents/     ADK agent, instructions, mode dispatch
  tools/      the agent's only capability surface
  services/   domain logic (assignment engine + workflow)
  rag/        chunking, embeddings, vector stores
  models/     SQLAlchemy models
  schemas/    Pydantic contracts
  discord/    bot + backend client
backend/tests/
backend/alembic/
infra/        Dockerfile, docker-compose
docs/
scripts/
```

## Status

**Implemented:** data model, assignment engine and workflow, agent tool surface, knowledge/RAG pipeline, REST API, Discord command surface (Gateway + HTTP Interactions), plan guard, server isolation, audit trail, Google Cloud Run deployment, Secret Manager integration, tests.

**Not implemented yet:** project planning (guard only — deliberately), Pinecone vector search, hybrid RAG tuning.

## Documentation

* [Architecture](docs/architecture.md) — layers, isolation, why the LLM cannot write to the database
* [Assignment engine](docs/assignment.md) — the workflow, the scoring, what gets stored for audit
* [Discord Wiring](docs/discord.md) — slash commands, Gateway vs. HTTP Interactions, Ed25519 signature verification
* [Cloud Run Deployment](docs/deploy.md) — GCP setup, Artifact Registry, Secret Manager mapping, container specs
