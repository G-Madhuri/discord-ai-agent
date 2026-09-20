"""Domain errors shared by services, tools and the API layer."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for expected, user-correctable failures."""

    status_code: int = 400
    code: str = "domain_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"


class ValidationError(DomainError):
    status_code = 422
    code = "validation_error"


class ServerScopeError(DomainError):
    """Raised when an operation would cross Discord server boundaries.

    This is a hard isolation guarantee: entities from one guild must never be
    read or written while acting on behalf of another guild.
    """

    status_code = 403
    code = "server_scope_violation"


class PlanImmutableError(ConflictError):
    """Raised when something tries to (re)generate a plan that already exists.

    Planning is only allowed when the caller explicitly asked for planning or
    replanning; assignment flows must never touch an existing plan.
    """

    code = "plan_immutable"


class NoEligibleCandidateError(DomainError):
    status_code = 409
    code = "no_eligible_candidate"
