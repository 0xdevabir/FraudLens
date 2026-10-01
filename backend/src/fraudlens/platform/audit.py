"""The audit trail: who did what to which object, written with the change itself."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .models import AuditLog


@dataclass(frozen=True)
class Ctx:
    """Who is acting, and on which request."""

    user_id: int | None
    username: str
    role: str | None
    request_id: str | None = None
    ip: str | None = None


class WorkflowError(Exception):
    """A request that cannot be carried out; maps to an HTTP status and a stable code."""

    def __init__(self, status: int, code: str, message: str, **extra) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def audit(
    s: Session,
    ctx: Ctx,
    action: str,
    object_type: str | None = None,
    object_id: str | int | None = None,
    **detail,
) -> None:
    """Add an audit row to the caller's transaction: it commits or fails with the change."""
    s.add(
        AuditLog(
            actor_id=ctx.user_id,
            actor=ctx.username[:64],
            role=ctx.role,
            action=action,
            object_type=object_type,
            object_id=None if object_id is None else str(object_id),
            detail=detail,
            request_id=ctx.request_id,
            ip=ctx.ip,
        )
    )
