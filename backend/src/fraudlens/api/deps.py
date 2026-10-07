"""Request dependencies: the platform's shared objects, a database session, the caller."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, Path, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis import Redis
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..intel.text import TextModel
from ..platform import apikeys
from ..platform.audit import Ctx, WorkflowError
from ..platform.callbacks import Dispatcher
from ..platform.graph import Graph
from ..platform.models import User
from ..platform.notify import Dispatcher as Notifier
from ..platform.scoring import Scorer
from ..platform.security import (
    RateLimiter,
    TokenError,
    client_address,
    is_revoked,
    read_token,
)
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
    callbacks: Dispatcher | None = None  # decision callbacks to ingest partners, when configured
    public_ip_limit: RateLimiter | None = None
    public_ref_limit: RateLimiter | None = None
    dispatcher: Notifier | None = None  # sends webhooks and SMS; None when it is off


def platform(request: Request) -> Platform:
    return request.app.state.platform


Plat = Annotated[Platform, Depends(platform)]


def get_session(p: Plat) -> Iterator[Session]:
    with p.sessions() as session:
        yield session


Db = Annotated[Session, Depends(get_session)]

_bearer = HTTPBearer(auto_error=False, description="Access token from POST /v1/auth/login")


def client_ip(request: Request) -> str | None:
    """The caller's address, through the reverse proxies in FRAUDLENS_TRUSTED_PROXIES."""
    peer = request.client.host if request.client else None
    trusted = request.app.state.platform.settings.trusted_proxies
    return client_address(peer, request.headers.getlist("x-forwarded-for"), trusted)


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


def partner(scope: str) -> Callable[..., Ctx]:
    """Admits a partner's API key (`X-API-Key`) that carries `scope`, or, as before, the
    service account's access token. A key is rate limited and counted; a sandbox key sets
    `request.state.sandbox` so the route answers with canned decisions."""

    def dependency(
        request: Request,
        p: Plat,
        session: Db,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
        x_api_key: Annotated[str | None, Header(description="A partner API key")] = None,
    ) -> Ctx:
        request.state.sandbox = False
        if x_api_key is None:
            user = current_user(request, p, session, credentials)
            if user.role != "service":
                raise WorkflowError(
                    403, "forbidden", "this needs the service account or an API key"
                )
            return Ctx(
                user.id, user.username, user.role, request.state.request_id, client_ip(request)
            )
        key = apikeys.authenticate(session, x_api_key)
        if scope not in key.scopes:
            raise WorkflowError(403, "forbidden", f"this key does not have the {scope!r} scope")
        request.state.api_key_id = key.id  # before the limits: a refused request is an error too
        apikeys.check_limits(p.redis, key)
        request.state.sandbox = key.sandbox
        now = datetime.now(UTC)
        if key.last_used_at is None or (now - key.last_used_at).total_seconds() > 60:
            key.last_used_at = now  # at most once a minute: a write per request would be a cost
            session.commit()
        return Ctx(
            None, f"key:{key.prefix}", "service", request.state.request_id, client_ip(request)
        )

    return dependency


ScoreCaller = Annotated[Ctx, Depends(partner("score"))]
EventsCaller = Annotated[Ctx, Depends(partner("events"))]
