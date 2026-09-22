"""Tests for the scoring engine — pure domain, no database."""

from __future__ import annotations

import uuid

from app.models.enums import AvailabilityStatus, DependencyType, TaskStatus
from app.schemas.task import DependencyRead
from app.services.assignment.engine import (
    CandidateInput,
    CandidateSkill,
    DependencyState,
    EvaluationInput,
    KnowledgeSnippet,
    SkillRequirement,
    TaskInput,
    evaluate_candidates,
)


def skill(slug: str, name: str, proficiency: int = 4, years: float | None = 3.0) -> CandidateSkill:
    return CandidateSkill(slug=slug, name=name, proficiency=proficiency, years_experience=years)


def candidate(name: str, **kwargs) -> CandidateInput:
    return CandidateInput(
        member_id=uuid.uuid4(),
        discord_user_id=f"discord-{name.lower()}",
        display_name=name,
        **kwargs,
    )


def dashboard_task() -> TaskInput:
    return TaskInput(
        key="TASK-101",
        title="Build React dashboard",
        description="Dashboard with charts for the admin area",
        role_hint="Frontend",
        requirements=(
            SkillRequirement(slug="react", name="React", weight=2.0),
            SkillRequirement(slug="javascript", name="JavaScript", weight=1.0),
        ),
    )


def team() -> tuple[CandidateInput, ...]:
    """The scenario from the brief: four members, four different specialities."""
    return (
        candidate(
            "Rahul",
            role="Frontend Engineer",
            skills=(skill("react", "React", 5), skill("javascript", "JavaScript", 5)),
        ),
        candidate(
            "Ananya",
            role="ML Engineer",
            skills=(skill("python", "Python", 5), skill("nlp", "NLP", 4)),
        ),
        candidate(
            "Madhuri",
            role="Backend Engineer",
            skills=(skill("fastapi", "FastAPI", 5), skill("python", "Python", 4)),
        ),
        candidate(
            "Arjun",
            role="Backend Engineer",
            skills=(skill("postgresql", "PostgreSQL", 5), skill("sql", "SQL", 4)),
        ),
    )


def evaluate(task: TaskInput, candidates, **kwargs):
    return evaluate_candidates(
        EvaluationInput(task=task, project_key="PROJ", candidates=tuple(candidates), **kwargs)
    )


def test_picks_the_member_whose_skills_match_the_task():
    result = evaluate(dashboard_task(), team())

    assert result.best is not None
    assert result.best.display_name == "Rahul"
    assert {s.slug for s in result.best.evidence.matched_skills} == {"react", "javascript"}
    assert result.best.score > result.ranked[1].score


def test_workload_fallback_when_no_skill_matches():
    """Issue 1: Task requires Rust. Members have only Python and React.
    Assert task IS assigned to one of them, with the workload-fallback warning in evidence.
    """
    rust_task = TaskInput(
        key="TASK-RUST",
        title="Implement Rust core module",
        description="High performance Rust library",
        requirements=(SkillRequirement(slug="rust", name="Rust", weight=1.0),),
    )
    members = (
        candidate("Alice", role="Python Dev", skills=(skill("python", "Python", 5),), active_task_keys=("T-1",)),
        candidate("Bob", role="React Dev", skills=(skill("react", "React", 5),), active_task_keys=()),
    )
    result = evaluate(rust_task, members)

    assert result.best is not None
    # Bob has lower active task count (0 vs 1), so Bob wins tiebreaker
    assert result.best.display_name == "Bob"
    assert "⚠️ Assigned via workload fallback (no skill match; balanced load)" in result.best.reasons


def test_every_candidate_is_scored_and_explained():
    result = evaluate(dashboard_task(), team())

    assert len(result.ranked) == 4
    for scored in result.ranked:
        assert scored.reasons, "every candidate must carry its evidence"
        assert 0.0 <= scored.score <= 1.0
    # Members without the required skills are recorded as gaps, not guesses.
    ananya = next(c for c in result.ranked if c.display_name == "Ananya")
    assert {s.slug for s in ananya.evidence.missing_skills} == {"react", "javascript"}
    assert ananya.evidence.matched_skills == []


def test_workload_breaks_a_tie_between_equal_candidates():
    equal_skills = (skill("react", "React", 5), skill("javascript", "JavaScript", 5))
    busy = candidate(
        "Busy",
        role="Frontend Engineer",
        skills=equal_skills,
        active_task_keys=("TASK-1", "TASK-2"),
        capacity=3,
    )
    free = candidate(
        "Free", role="Frontend Engineer", skills=equal_skills, active_task_keys=(), capacity=3
    )

    result = evaluate(dashboard_task(), [busy, free])

    assert result.best.display_name == "Free"
    assert result.best.evidence.workload.active_task_count == 0


def test_unavailable_members_are_excluded_with_a_reason():
    on_leave = candidate(
        "OnLeave",
        role="Frontend Engineer",
        skills=(skill("react", "React", 5),),
        availability=AvailabilityStatus.ON_LEAVE,
    )
    available = candidate(
        "Available", role="Frontend Engineer", skills=(skill("react", "React", 3),)
    )

    result = evaluate(dashboard_task(), [on_leave, available])

    assert [c.display_name for c in result.ranked] == ["Available"]
    assert len(result.excluded) == 1
    assert result.excluded[0].display_name == "OnLeave"
    assert "leave" in result.excluded[0].reason


def test_busy_members_are_ranked_lower_but_still_eligible():
    busy = candidate(
        "Busy",
        role="Frontend Engineer",
        skills=(skill("react", "React", 5), skill("javascript", "JavaScript", 5)),
        availability=AvailabilityStatus.BUSY,
    )
    available = candidate(
        "Available",
        role="Frontend Engineer",
        skills=(skill("react", "React", 5), skill("javascript", "JavaScript", 5)),
    )

    result = evaluate(dashboard_task(), [busy, available])

    assert result.best.display_name == "Available"
    assert [c.display_name for c in result.ranked] == ["Available", "Busy"]


def test_project_knowledge_is_used_as_evidence():
    snippet = KnowledgeSnippet(
        document_title="Frontend Architecture",
        chunk_id=uuid.uuid4(),
        content="The frontend uses React + TypeScript and the dashboard uses Recharts.",
        score=0.82,
    )
    rahul = candidate("Rahul", role="Frontend Engineer", skills=(skill("react", "React", 5),))
    arjun = candidate(
        "Arjun", role="Backend Engineer", skills=(skill("postgresql", "PostgreSQL", 5),)
    )

    result = evaluate(dashboard_task(), [rahul, arjun], knowledge=(snippet,))

    best = result.best
    assert best.display_name == "Rahul"
    assert best.components.knowledge > 0
    assert best.evidence.knowledge[0].document_title == "Frontend Architecture"
    assert "React" in best.evidence.knowledge[0].matched_terms
    # The backend member's skills are not mentioned in that document.
    arjun_scored = next(c for c in result.ranked if c.display_name == "Arjun")
    assert arjun_scored.components.knowledge == 0


def test_unresolved_dependencies_are_reported_not_hidden():
    blocker = DependencyRead(
        task_key="TASK-100",
        title="Design API contract",
        status=TaskStatus.IN_PROGRESS,
        dependency_type=DependencyType.BLOCKS,
        is_resolved=False,
    )

    result = evaluate(
        dashboard_task(),
        team(),
        dependencies=DependencyState(unresolved=(blocker,)),
    )

    assert result.dependencies.is_blocked is True
    assert result.dependencies.unresolved[0].task_key == "TASK-100"
    assert any("TASK-100" in reason for reason in result.best.reasons)


def test_no_dependency_produces_a_clean_bullet():
    result = evaluate(dashboard_task(), team())
    assert any("No unresolved dependency" in reason for reason in result.best.reasons)


def test_scoring_is_deterministic():
    task, members = dashboard_task(), team()
    first = evaluate(task, members)
    second = evaluate(task, members)

    assert [c.display_name for c in first.ranked] == [c.display_name for c in second.ranked]
    assert [c.score for c in first.ranked] == [c.score for c in second.ranked]


def test_task_without_declared_skills_stays_neutral_instead_of_guessing():
    bare = TaskInput(key="TASK-999", title="Unspecified work")
    result = evaluate(bare, team())

    assert result.best is not None
    for scored in result.ranked:
        assert scored.components.skill == 0.5
        assert scored.evidence.matched_skills == []
        assert scored.evidence.missing_skills == []


def test_empty_candidate_list_yields_no_selection():
    result = evaluate(dashboard_task(), [])
    assert result.ranked == []
    assert result.best is None
