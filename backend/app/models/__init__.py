"""SQLAlchemy models. Importing this package registers every table on Base."""

from app.models.assignment import Assignment, AssignmentHistory
from app.models.base import Base
from app.models.enums import (
    AssignmentEvent,
    AssignmentStatus,
    AvailabilityStatus,
    DecisionMode,
    DependencyType,
    DocumentSourceType,
    PlanStatus,
    ProjectStatus,
    SkillSource,
    TaskPriority,
    TaskStatus,
)
from app.models.member import MemberProfile
from app.models.project import DocumentChunk, Project, ProjectDocument, ProjectMember
from app.models.server import Server
from app.models.skill import MemberSkill, Skill
from app.models.task import Task, TaskDependency, TaskSkill
from app.models.user import User

__all__ = [
    "Assignment",
    "AssignmentEvent",
    "AssignmentHistory",
    "AssignmentStatus",
    "AvailabilityStatus",
    "Base",
    "DecisionMode",
    "DependencyType",
    "DocumentChunk",
    "DocumentSourceType",
    "MemberProfile",
    "MemberSkill",
    "PlanStatus",
    "Project",
    "ProjectDocument",
    "ProjectMember",
    "ProjectStatus",
    "Server",
    "Skill",
    "SkillSource",
    "Task",
    "TaskDependency",
    "TaskPriority",
    "TaskSkill",
    "TaskStatus",
    "User",
]
