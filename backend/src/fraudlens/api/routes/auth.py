"""Sign-in and sign-out. Tokens are short-lived; every request re-checks the account."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from ...platform.audit import Ctx, WorkflowError, audit
from ...platform.models import User
from ...platform.security import issue_token, revoke_token, verify_password
from ...platform.seed import ACCOUNTS
from ..deps import Db, Plat, client_ip, current_user
from ..schemas import DemoLogin, Login
from ..views import user_view

router = APIRouter(prefix="/auth", tags=["auth"])

# The seeded people who use the console; `upay-core` and `review-sim` are not offered.
DEMO_ACCOUNTS = [a for a in ACCOUNTS if a[2] != "service" and a[0] != "review-sim"]


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
    return _session(user, p)


def _session(user: User, p: Plat) -> dict:
    return {
        "access_token": issue_token(user.id, user.role, p.settings),
        "token_type": "bearer",
        "expires_in": p.settings.jwt_ttl_minutes * 60,
        "user": user_view(user),
    }


def _demo_login(p: Plat) -> bool:
    return p.settings.demo_login and not p.settings.production


@router.get("/demo")
def demo_accounts(p: Plat) -> dict:
    """The accounts the sign-in page offers as one-click buttons. Empty unless enabled."""
    accounts = [
        {"username": username, "display_name": display_name, "role": role}
        for username, display_name, role in DEMO_ACCOUNTS
    ]
    return {"accounts": accounts if _demo_login(p) else []}


@router.post("/demo-login")
def demo_login(body: DemoLogin, request: Request, p: Plat, s: Db) -> dict:
    """Sign in as a demo account without its password. Off unless FRAUDLENS_DEMO_LOGIN is set."""
    if not _demo_login(p):
        raise WorkflowError(404, "not_found", "demo sign-in is not enabled")
    # Only the people the seed creates: never a service account or a real user.
    known = body.username in {username for username, _, _ in DEMO_ACCOUNTS}
    user = s.scalar(select(User).where(User.username == body.username)) if known else None
    if user is None or not user.is_active:
        raise WorkflowError(404, "not_found", "no such demo account")
    ctx = Ctx(user.id, user.username, user.role, request.state.request_id, client_ip(request))
    audit(s, ctx, "auth.login", demo=True)
    s.commit()
    return _session(user, p)


@router.post("/logout")
def logout(request: Request, p: Plat, s: Db, user: Annotated[User, Depends(current_user)]) -> dict:
    """Sign out: this token stops working now, on every device, not when it expires."""
    revoke_token(p.redis, request.state.claims)
    ctx = Ctx(user.id, user.username, user.role, request.state.request_id, client_ip(request))
    audit(s, ctx, "auth.logout")
    s.commit()
    return {"signed_out": True}


@router.get("/me")
def me(user: Annotated[User, Depends(current_user)]) -> dict:
    return user_view(user)
