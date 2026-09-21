# Agentic Project Creation Architecture & User Flow (Phase 6)

## Overview
Phase 6 replaces manual multi-command project creation with a single agent-driven flow using Gemini (Vertex AI). 
Users submit a project brief via a Discord modal, optionally attach a spec file, review an LLM-generated plan with automated candidate assignments, and approve or reject the plan before any assignment database records are created.

---

## 1. Discord Modal Flow

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Discord as Discord Client
    participant Bot as FastAPI /discord/interactions
    participant Parser as Document Parser
    participant LLM as Gemini 2.5 Flash
    participant DB as Neon Postgres

    User->>Discord: Trigger /project create
    Discord->>Bot: POST /discord/interactions (type 2)
    Bot-->>Discord: Response type 9 (4-field Modal)
    
    User->>Discord: Submits modal (Name, Brief, Team, Constraints)
    Discord->>Bot: POST /discord/interactions (type 5 Modal Submit)
    Bot-->>Discord: Response type 5 (Deferred acknowledgment)
    Bot->>Discord: Follow-up message: "Have a spec file? Reply or click [Skip]"
    
    alt File Uploaded within 60s
        User->>Discord: Uploads .pdf / .docx / .txt file
        Discord->>Bot: Message event / Attachment received
        Bot->>Parser: Extract text (pdf/docx/txt)
    else Click [Skip File Upload] or Timeout
        User->>Discord: Clicks [Skip File Upload] button
    end
    
    Bot->>LLM: Single-pass planning prompt (Brief + File + Team + Constraints)
    LLM-->>Bot: JSON Structured Output (Tasks + Required Skills + Dependencies)
    
    Bot->>DB: Store Draft Project & Tasks in project.draft_assignments (JSONB)
    Bot->>Discord: PATCH @original message with Plan Summary & ActionRow Buttons [Approve ✅] [Reject ❌] [Edit ✏️]
    
    alt User Clicks Approve ✅
        User->>Discord: Clicks Approve ✅
        Bot->>DB: Write assignment and assignment_history rows, update project status='active'
        Bot-->>Discord: Update message: "✅ Project Approved and Activated!"
    else User Clicks Reject ❌
        User->>Discord: Clicks Reject ❌
        Bot->>DB: Set project status='archived', no assignment rows created
        Bot-->>Discord: Update message: "❌ Project Draft Rejected and Cancelled."
    end
```

---

## 2. LLM System & User Prompt Structure

### Verbatim Task Extraction & Constraint Enforcement
To guarantee task fidelity, strict prompt constraints are injected in three places:

1. **System Prompt Rules**:
   - Explicit task preservation: If the user provides explicit bullet points or numbered lists of tasks, extract them verbatim without altering, merging, or omitting titles.
   - Task count capping: Output between 1 and 20 tasks (`min_length=1, max_length=20`).

2. **User Prompt (Prefixed Section)**:
   - Contains raw brief, parsed document text, candidate team members with their current workload, and explicit constraints.

3. **Output Schema Rules (Pydantic)**:
   - `tasks`: List of `TaskPlanItem` (`min_length=1, max_length=20`).

---

## 3. Approval Flow & Deferred Persistence (Amendment 4)

- **Draft Assignments**: Running the deterministic assignment engine before approval produces evidence and scoring, but results are saved ONLY in the `project.draft_assignments` JSONB column.
- **Clean Audit Trail**: No rows are written to `assignments` or `assignment_history` tables during planning.
- **Approve Action**: Writes official `assignments` and `assignment_history` rows, activating the project (`status='active'`).
- **Reject Action**: Discards the plan without creating any assignment records.

---

## 4. Latency Breakdown & SLAs

| Phase | Operation | Expected Latency | Notes |
| :--- | :--- | :--- | :--- |
| 1 | Defer response (`type 5`) | < 100ms | Immediate response to prevent Discord 3s interaction timeout |
| 2 | LLM Planning (Gemini 2.5) | 3 – 8s | Hard timeout cap at 30s |
| 3 | Deterministic Assignment Engine | 1 – 2s | Computes skill, workload, role, experience scores |
| 4 | Webhook PATCH Follow-up | 1s | Renders summary and interactive buttons |
| **Total** | **End-to-End Execution** | **5 – 15s** | Cold start containers may add 2-3s |
