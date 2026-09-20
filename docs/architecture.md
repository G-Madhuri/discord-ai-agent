# Architecture

## Flow

```
Discord
  ↓  slash command (/assign TASK-101)
Discord Bot                         app/discord/
  ↓  HTTP + guild context
FastAPI                             app/api/
  ↓
Assignment Agent                    app/agents/
  ↓  function calls only
Agent Tools                         app/tools/toolset.py
  ├── project tools
  ├── task tools
  ├── team/member tools
  ├── assignment tools
  ├── knowledge/RAG tools
  └── discord reply (returned to the bot)
  ↓
Domain services                     app/services/
  ↓
PostgreSQL  +  Project knowledge (RAG)
```

The bot holds no domain logic and no database credentials. It forwards the
guild, channel and user ids with every call; the backend owns everything else.

## The LLM never touches the database

The model's only capability is calling the functions in
[`app/tools/toolset.py`](../backend/app/tools/toolset.py). Those functions
validate their input, filter by the bound server, call a service and return
JSON. There is no SQL tool, no raw query tool and no schema access.

Of the fourteen tools, thirteen are read-only. `assign_task` is the single
mutation, and it runs the same service the deterministic path uses — so a
decision made by the model is persisted, scored and audited exactly like one
made without it.

## Two decision modes, one persistence path

| | `AGENT_MODE=deterministic` (default) | `AGENT_MODE=llm` |
|---|---|---|
| Who chooses | scoring engine | Gemini via Google ADK |
| Model call | none | one agent turn |
| Evidence stored | full evaluation | full evaluation |
| Reproducible | yes | the evidence is; the wording is not |

The engine (`app/services/assignment/engine.py`) is pure: no database, no
network, no model. It takes a task, candidates and retrieved knowledge and
returns a ranked list with the reasoning attached to each candidate. The LLM
mode reads that same ranking through `evaluate_task_candidates` — it reasons
*about* evidence rather than producing it, which is what stops it inventing
skills or people.

## Server isolation

`Server` (one Discord guild) is the root of every scope. The isolation holds at
three levels:

1. **Schema** — every server/project-scoped table carries `server_id`.
2. **Query** — services take `server_id` as a required argument and filter on
   it. `document_chunks` is filtered on `server_id` *and* `project_id`, not on
   project alone.
3. **Context** — the LLM cannot supply a server id. `ToolContext` binds it once
   from the incoming guild, and every tool closes over that value.

Covered by [`tests/integration/test_server_isolation.py`](../backend/tests/integration/test_server_isolation.py),
which gives two guilds the same project key, the same task key and the same
skill vocabulary, then checks that nothing crosses.

## Structured facts vs. unstructured knowledge

| Structured (PostgreSQL) | Unstructured (knowledge layer) |
|---|---|
| members, roles, skills, proficiency | requirements documents |
| projects, tasks, dependencies | architecture notes |
| assignments, workload, history | meeting notes, captured discussions |

Team and task facts are never inferred from retrieved text. Knowledge is used
to *connect* the two: documentation saying "the dashboard uses React +
Recharts" is what lets a React member's profile count as evidence for a
dashboard task.

## Layers

| Path | Responsibility |
|---|---|
| `app/api/` | HTTP surface, request/response schemas, error mapping |
| `app/agents/` | ADK agent, instructions, mode dispatch |
| `app/tools/` | the agent's callable surface; scope binding |
| `app/services/` | domain logic; the only place that writes |
| `app/services/assignment/` | scoring engine + assignment workflow |
| `app/rag/` | chunking, embeddings, vector stores |
| `app/models/` | SQLAlchemy models |
| `app/schemas/` | Pydantic contracts |
| `app/discord/` | bot and backend client |

Dependencies point inward: API → services → models. `app/tools` is the seam
between the agent and the domain, and the engine at the centre depends on
nothing but schemas.
