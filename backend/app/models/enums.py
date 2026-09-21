from __future__ import annotations

from enum import StrEnum


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    PLANNING = "planning"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class PlanStatus(StrEnum):
    """Lifecycle of a project's plan.

    Anything other than NONE means a plan exists and must not be regenerated
    unless the user explicitly asks for planning/replanning.
    """

    NONE = "none"
    DRAFT = "draft"
    ACTIVE = "active"
    LOCKED = "locked"


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    IN_REVIEW = "in_review"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DependencyType(StrEnum):
    BLOCKS = "blocks"
    RELATES_TO = "relates_to"


class AvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    BUSY = "busy"
    ON_LEAVE = "on_leave"
    INACTIVE = "inactive"


class SkillSource(StrEnum):
    DECLARED = "declared"
    ADMIN = "admin"
    INFERRED = "inferred"


class AssignmentStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    COMPLETED = "completed"
    REVOKED = "revoked"
    REASSIGNED = "reassigned"


class AssignmentEvent(StrEnum):
    CREATED = "created"
    REASSIGNED = "reassigned"
    REVOKED = "revoked"
    COMPLETED = "completed"
    STATUS_CHANGED = "status_changed"


class DecisionMode(StrEnum):
    """How an assignment decision was produced — kept for auditability."""

    DETERMINISTIC = "deterministic"
    LLM = "llm"
    MANUAL = "manual"


class DocumentSourceType(StrEnum):
    DESCRIPTION = "description"
    REQUIREMENTS = "requirements"
    ARCHITECTURE = "architecture"
    TECHNICAL_DOC = "technical_doc"
    MEETING_NOTES = "meeting_notes"
    DISCORD_DISCUSSION = "discord_discussion"
    UPLOAD = "upload"
    OTHER = "other"
