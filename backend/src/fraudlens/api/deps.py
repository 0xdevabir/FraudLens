"""Request dependencies: the platform's shared objects, a database session, the caller."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Path, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis import Redis
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..intel.text import TextModel
from ..platform.audit import Ctx, WorkflowError
from ..platform.graph import Graph
from ..platform.models import User
from ..platform.scoring import Scorer
from ..platform.security import RateLimiter, TokenError, is_revoked, read_token
from ..platform.stream import Worker

REVIEWERS = ("analyst", "supervisor")

# Path parameters are bounded so that no value can overflow a database column.
TxnId = Annotated[int, Path(ge=0, lt=2**62)]
RowId = Annotated[int, Path(ge=1, le=2**31 - 1)]


@dataclass
class Platform:
    """Everything the process shares, built once at start-up."""

    settings: Settings
    db: Engine
    sessions: sessionmaker[Session]
    redis: Redis
    scorer: Scorer
    graph: Graph
    worker: Worker
    narrator: Callable | None  # the language model for case notes, when enabled
    login_limit: RateLimiter
    login_ip_limit: RateLimiter
    check_limit: RateLimiter
    report_limit: RateLimiter
    intel: TextModel | None  # the scam-message classifier, when it has been trained
    message_limit: RateLimiter
    proof_limit: RateLimiter


def platform(request: Request) -> Platform:
    return request.app.state.platform


Plat = Annotated[Platform, Depends(platform)]


def get_session(p: Plat) -> Iterator[Session]:
    with p.sessions() as session:
        yield session


Db = Annotated[Session, Depends(get_session)]

_bearer = HTTPBearer(auto_error=False, description="Access token from POST /v1/auth/login")


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def current_user(
    request: Request,
    p: Plat,
    session: Db,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    denied = WorkflowError(401, "unauthenticated", "a valid access token is required")
    if credentials is None:
        raise denied
    try:
        claims = read_token(credentials.credentials, p.settings)
        user = session.get(User, int(claims["sub"]))
    except (TokenError, ValueError):
        raise denied from None
    # The token is only as good as the account is now: a disabled account or a
    # changed role takes effect at once, not when the token expires.
    if user is None or not user.is_active or user.role != claims.get("role"):
        raise denied
    if is_revoked(p.redis, claims):  # signed out
        raise denied
    request.state.claims = claims
    return user


def require(*roles: str) -> Callable[..., Ctx]:
    """A dependency that admits only these roles and returns who is acting."""

    def dependency(request: Request, user: Annotated[User, Depends(current_user)]) -> Ctx:
        if user.role not in roles:
            raise WorkflowError(
                403, "forbidden", f"this needs one of the roles: {', '.join(roles)}"
            )
        return Ctx(user.id, user.username, user.role, request.state.request_id, client_ip(request))

    return dependency


Reviewer = Annotated[Ctx, Depends(require(*REVIEWERS))]
Supervisor = Annotated[Ctx, Depends(require("supervisor"))]
Service = Annotated[Ctx, Depends(require("service"))]
Staff = Annotated[Ctx, Depends(require("analyst", "supervisor", "admin"))]
Oversight = Annotated[Ctx, Depends(require("supervisor", "admin"))]
