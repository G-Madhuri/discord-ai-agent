# Discord Wiring & Architecture

This document details the Discord interface, bot command execution flow, security layers, and operational modes.

## 1. Slash Command Reference

All commands are thin wrappers around the FastAPI backend and enforce server (guild) isolation by passing `discord_guild_id`.

| Command | Arguments | Description & Example |
|---|---|---|
| `/project create` | `<name> [description]` | Creates a new project in the guild. <br>`/project create name:"Phase4 Demo"` |
| `/project info` | `<name>` | Displays project status, task counts, and team members. <br>`/project info name:"Phase4 Demo"` |
| `/project member add` | `<project> <user> [role]` | Explicitly rosters a server member into a project. <br>`/project member add project:"P4DEMO" user:@Rahul role:"Frontend Lead"` |
| `/project knowledge` | `<project>` | Lists indexed documents for the project. <br>`/project knowledge project:"Phase4 Demo"` |
| `/task create` | `<project> <title> [description]` | Creates a task for a project. <br>`/task create project:"Phase4 Demo" title:"Build Dashboard"` |
| `/task list` | `<project>` | Lists all tasks and assignees in a project. <br>`/task list project:"Phase4 Demo"` |
| `/member add` | `<user> [role] [skills]` | Registers a team member in the server directory with role and skills. <br>`/member add user:@Rahul role:"Frontend" skills:"React, JavaScript"` |
| `/member profile` | `<user>` | Displays member profile, availability, and skills. <br>`/member profile user:@Rahul` |
| `/member skills` | `<user> [skills...]` | Updates skills for a member. <br>`/member skills user:@Rahul skills:"React, TypeScript"` |
| `/assign` | `<task_id> [--reassign]` | Ranks team candidates and assigns task with evidence bullets. <br>`/assign task_id:"TASK-101"` |
| `/assignment history` | `<task_id>` | Displays the complete audit log of assignments and decision modes. <br>`/assignment history task_id:"TASK-101"` |

## 2. Why Project Membership is Explicit

In accordance with system design principles (Brief §4), the server team directory (`MemberProfile`) and project team rosters (`ProjectMember`) are decoupled. Adding a member to a Discord server via `/member add` registers their profile, capabilities, and availability in the server directory, but does not automatically assign them to every project in that server. A member must be explicitly assigned to a project via `/project member add` (or `POST /projects/{key}/members`) to appear on that project's roster. The assignment scoring engine evaluates candidates strictly against the project's active `ProjectMember` roster, guaranteeing cross-project isolation within the same server and preventing unallocated server members from being assigned project tasks.

## 2. Operating Modes: Gateway vs. HTTP Interactions

### Gateway Mode (Dev / Local)
- **File**: `backend/app/discord/bot.py`
- **Mechanism**: Maintains a persistent WebSocket connection to Discord Gateway via `discord.py`.
- **Usage**: Local development and instant dev guild command sync (`DISCORD_GUILD_ID`).

### HTTP Interactions Mode (Production / Cloud Run)
- **File**: `backend/app/api/routes/discord.py`
- **Endpoint**: `POST /discord/interactions`
- **Mechanism**: Serverless HTTP webhook path required for stateless Cloud Run deployment (no long-lived gateway connection).
- **Execution**: Dispatches requests to `app.discord.command_handler.handle_command`, sharing 100% of execution logic with Gateway mode.

## 3. Security & Validation

### Signature Verification (PyNaCl)
- Discord signs all HTTP interaction payloads using Ed25519.
- `POST /discord/interactions` checks `X-Signature-Ed25519` and `X-Signature-Timestamp` against `DISCORD_PUBLIC_KEY` using PyNaCl (`nacl.signing.VerifyKey`).
- Invalid or missing signatures return `401 Unauthorized` without processing.

### Backend Auth Guard
- `backend/app/api/deps.py` enforces `verify_internal_token` on all non-health backend routes (`/api/v1/*`).
- Requests must include header `X-Internal-Token: <INTERNAL_API_TOKEN>`.
- Missing or invalid tokens return `401 Unauthorized` with generic error details. Secrets are never logged.

### Server (Guild) Isolation
- Every command extracts `discord_guild_id` from the Discord interaction and injects it into `ServerContext`.
- Queries are strictly scoped by `server_id`. Cross-guild requests or unregistered guilds return clean 404/Refused responses without exposing data.

## 4. What is NOT Implemented Yet

- **Planning Mode / Replanning**: The plan guard remains strictly enforced (`assignment-only`).
- **Pinecone / RAG Hybrid**: Pinecone and vector store RAG tuning are reserved for Phase 5.
- **Agent LLM Default**: `AGENT_MODE` defaults to `deterministic`.
