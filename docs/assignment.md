# The assignment engine

## Workflow

`app/services/assignment/service.py` runs these steps for every `/assign`:

| # | Step | Where |
|---|---|---|
| 1 | Identify the Discord server | `server_service.resolve_server` |
| 2 | Identify the project | `project_service` (explicit key → channel binding → the single project) |
| 3–4 | Identify and read the existing task | `task_service.require_task` |
| 5 | Retrieve relevant project knowledge | `knowledge_service.search_knowledge` |
| 6–7 | Retrieve eligible members with skills, role, experience | `project_service.list_project_members` |
| 8 | Retrieve current workload | `project_service.count_active_assignments` |
| 9 | Check dependencies | `task_service.check_dependencies` |
| 10–11 | Evaluate and select | `engine.evaluate_candidates` |
| 12–13 | Assign and persist with evidence | `persist_assignment` |
| 14 | Return a concise explanation | `AssignmentResult.message` |
| 15 | Send to Discord | the bot posts the message |

Step 2 refuses to guess: if a server has several projects and none is bound to
the channel, it asks which one rather than risk acting on the wrong project.

## Scoring

Five weighted components, each in `[0, 1]`. Weights are configurable via
`ASSIGNMENT_WEIGHT_*` environment variables and are recorded on every decision.

| Component | Default | What it measures |
|---|---|---|
| `skill` | 0.45 | weighted coverage of the task's declared skills, by proficiency and years |
| `role` | 0.15 | member role / project role against the task's role hint |
| `experience` | 0.15 | completed tasks in this project needing the same skills, plus tenure |
| `knowledge` | 0.10 | the member's skills appearing in retrieved project documentation |
| `workload` | 0.15 | `1 − active_tasks / capacity` |

```
score = Σ(weight × component) / Σ(weights) × availability_multiplier
```

**Hard exclusions.** `on_leave` and `inactive` members are removed before
scoring and recorded in `excluded` with the reason — they are not silently
dropped.

**Busy members** stay eligible with a 0.85 multiplier.

**Ties** break deterministically: score, then fewer active tasks, then name.

**No signal stays neutral.** A task with no declared skills scores every
candidate 0.5 on that component rather than inventing a match. A member whose
role is unknown gets 0.5 on role, not 0.

**Dependencies do not block.** An unresolved blocker is recorded as evidence
and surfaced in the reply ("Warning — unresolved dependency: TASK-104"), but
work can still be assigned ahead of it. Set
`REQUIRE_DEPENDENCIES_RESOLVED=true` to revisit this policy.

## What gets stored

`assignments.evidence` holds the entire `AssignmentEvaluation` as JSON:

- every candidate's score and its five components;
- matched skills with proficiency, and explicitly missing skills;
- role match quality;
- workload at decision time, with the active task keys;
- knowledge excerpts with the matched terms and chunk ids;
- prior related work;
- dependency status;
- **the candidates that were not chosen**, and the ones excluded and why;
- the weights and engine version in force.

`assignment_history` is append-only: one row per state change
(`created`, `reassigned`, `revoked`, `completed`), never updated. Together they
answer "why did this get assigned to this person, on that day, with that data"
long after the fact — including when the engine's weights have since changed,
because the weights used are part of the record.

## Example

```
✅ TASK-101 assigned to Rahul.
Why:
• Skill match: React, JavaScript (top proficiency 5/5)
• Role matches the task: Frontend Engineer
• Project knowledge: 'Frontend Architecture' mentions React
• Current workload: 2 active task(s) of 3
• No unresolved dependency blocking the task
```

## Extending it

The engine is pure and its inputs are dataclasses, so new signals are cheap:
add a component function, a weight and an evidence field. Keep the component in
`[0, 1]`, return evidence alongside the number, and add a case to
`tests/unit/test_assignment_engine.py` — a score without evidence is not usable
for an audit.
