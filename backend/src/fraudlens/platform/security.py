"""Passwords, access tokens, rate limits and the checks that guard production."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from redis import Redis

from ..config import DEV_JWT_SECRET, Settings

ALGORITHM = "HS256"
ISSUER = "fraudlens"
MIN_PASSWORD_LENGTH = 12

_hasher = PasswordHasher()
# Verified against when the username does not exist, so a wrong username costs
# the same time as a wrong password and the two cannot be told apart by timing.
_DUMMY_HASH = _hasher.hash("no-such-user")


class TokenError(Exception):
    """The access token is missing, malformed, expired or signed with another key."""


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (Argon2Error, InvalidHashError):
        return False


def issue_token(user_id: int, role: str, settings: Settings, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "role": role,
        "iss": ISSUER,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)


def read_token(token: str, settings: Settings) -> dict:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM],  # never trust the algorithm named in the token
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc


class RateLimiter:
    """Fixed-window counter in Redis: at most `limit` hits per `window` seconds per key."""

    def __init__(self, redis: Redis, name: str, limit: int, window: int) -> None:
        self.redis, self.name, self.limit, self.window = redis, name, limit, window

    def _key(self, key: str) -> str:
        return f"fraudlens:limit:{self.name}:{key}"

    def blocked(self, key: str) -> bool:
        count = self.redis.get(self._key(key))
        return count is not None and int(count) >= self.limit

    def hit(self, key: str) -> bool:
        """Count one attempt. False when the limit was already reached."""
        pipe = self.redis.pipeline()
        pipe.incr(self._key(key))
        pipe.expire(self._key(key), self.window, nx=True)
        count, _ = pipe.execute()
        return count <= self.limit

    def reset(self, key: str) -> None:
        self.redis.delete(self._key(key))


def check_production(settings: Settings) -> None:
    """Refuse to run a production service on development defaults."""
    if not settings.production:
        return
    secret = settings.jwt_secret.get_secret_value()
    if secret == DEV_JWT_SECRET or len(secret) < 32:
        raise RuntimeError("FRAUDLENS_JWT_SECRET must be a private key of 32+ characters")
    if "fraudlens_dev" in settings.database_url:
        raise RuntimeError("FRAUDLENS_DATABASE_URL still uses the development password")
    if "*" in settings.cors_origins:
        raise RuntimeError("FRAUDLENS_CORS_ORIGINS must list the allowed origins")
