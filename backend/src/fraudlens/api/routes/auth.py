"""Sign-in. Tokens are short-lived bearer tokens; every request re-checks the account."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from ...platform.audit import Ctx, WorkflowError, audit
from ...platform.models import User
from ...platform.security import issue_token, verify_password
from ..deps import Db, Plat, client_ip, current_user
from ..schemas import Login
from ..views import user_view

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(body: Login, request: Request, p: Plat, s: Db) -> dict:
    ip = client_ip(request) or "unknown"
    key = f"{body.username.lower()}:{ip}"
    if p.login_limit.blocked(key) or p.login_ip_limit.blocked(ip):
        raise WorkflowError(
            429, "too_many_attempts", "too many failed sign-ins; try again later",
            retry_after_seconds=p.settings.login_window_seconds,
        )  # fmt: skip
    user = s.scalar(select(User).where(User.username == body.username))
    active = user is not None and user.is_active
    # Always verifies a hash, so an unknown username takes as long as a wrong password.
    ok = verify_password(user.password_hash if active else None, body.password)
    request_id = request.state.request_id
    if not ok:
        p.login_limit.hit(key)
        p.login_ip_limit.hit(ip)
        audit(s, Ctx(None, body.username, None, request_id, ip), "auth.login_failed")
        s.commit()
        raise WorkflowError(401, "invalid_credentials", "wrong username or password")
    p.login_limit.reset(key)
    audit(s, Ctx(user.id, user.username, user.role, request_id, ip), "auth.login")
    s.commit()
    return {
        "access_token": issue_token(user.id, user.role, p.settings),
        "token_type": "bearer",
        "expires_in": p.settings.jwt_ttl_minutes * 60,
        "user": user_view(user),
    }


@router.get("/me")
def me(user: Annotated[User, Depends(current_user)]) -> dict:
    return user_view(user)
