"""Create the demo accounts.

    uv run python -m fraudlens.platform.seed

Passwords come from FRAUDLENS_SEED_PASSWORD, or are generated and printed once.
Existing accounts are left alone unless --reset-passwords is given. Refuses to
run in production: real deployments get their users from the identity provider.
"""

from __future__ import annotations

import argparse
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from .db import make_engine, make_sessions
from .models import User
from .security import hash_password

# Two analysts and two supervisors, so the two-person freeze approval can be shown.
ACCOUNTS = (
    ("analyst1", "Nusrat Jahan (analyst)", "analyst"),
    ("analyst2", "Tanvir Ahmed (analyst)", "analyst"),
    ("supervisor1", "Farhana Rahman (supervisor)", "supervisor"),
    ("supervisor2", "Imran Hossain (supervisor)", "supervisor"),
    ("admin", "Platform admin", "admin"),
    ("upay-core", "upay core platform (service account)", "service"),
    # Stands in for a week of analysts' work: see `fraudlens.mlops.review`.
    ("review-sim", "Queue review (batch)", "supervisor"),
)


def seed_users(
    session: Session, settings: Settings, reset_passwords: bool = False
) -> dict[str, str]:
    """Create missing accounts. Returns {username: password} for the ones it set."""
    if settings.production:
        raise RuntimeError("demo accounts are not created in production")
    shared = settings.seed_password.get_secret_value() if settings.seed_password else None
    issued: dict[str, str] = {}
    existing = {u.username: u for u in session.scalars(select(User))}
    for username, display_name, role in ACCOUNTS:
        user = existing.get(username)
        if user is not None and not reset_passwords:
            continue
        password = shared or secrets.token_urlsafe(16)
        if user is None:
            user = User(username=username, display_name=display_name, role=role)
            session.add(user)
        user.password_hash = hash_password(password)
        issued[username] = password
    session.commit()
    return issued


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the FraudLens demo accounts")
    parser.add_argument("--reset-passwords", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    engine = make_engine(settings.database_url)
    with make_sessions(engine)() as session:
        issued = seed_users(session, settings, args.reset_passwords)
    if not issued:
        print("all demo accounts already exist (use --reset-passwords to issue new passwords)")
    elif settings.seed_password:
        print(f"set FRAUDLENS_SEED_PASSWORD on: {', '.join(issued)}")
    else:
        print("generated passwords (shown once, not stored):")
        for username, password in issued.items():
            print(f"  {username:<12} {password}")


if __name__ == "__main__":
    main()

