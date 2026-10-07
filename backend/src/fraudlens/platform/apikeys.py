"""Partner API keys: made once, checked by hash, limited per minute and per day.

A key looks like `flk_<8 hex>_<secret>`. Only a SHA-256 of the whole key is stored (the
key is a long random token, so a fast hash is enough), plus the visible `prefix` so a
partner can say which key they mean. A key carries scopes (`score`, `events`), a request
rate, an optional daily quota, and may be a sandbox key that gets canned answers and
touches nothing real.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, date, datetime, timedelta

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import WorkflowError
from .models import ApiKey

SCOPES = ("score", "events")
USAGE_DAYS = 40  # how long daily counters are kept
_PREFIX = "flk_"


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate() -> tuple[str, str]:
    """(the key to show once, its prefix)."""
    prefix = secrets.token_hex(4)
    return f"{_PREFIX}{prefix}_{secrets.token_urlsafe(24)}", f"{_PREFIX}{prefix}"


def authenticate(s: Session, raw: str) -> ApiKey:
    """The key behind `raw`, or a 401 that says nothing about why."""
    denied = WorkflowError(401, "unauthenticated", "a valid API key is required")
    if not raw.startswith(_PREFIX) or raw.count("_") < 2 or len(raw) > 128:
        raise denied
    prefix = "_".join(raw.split("_", 2)[:2])  # flk_<8 hex>
    key = s.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
    if key is None or key.revoked_at is not None:
        raise denied
    if not hmac.compare_digest(key.key_hash, hash_key(raw)):
        raise denied
    return key


# ------------------------------------------------------------------ usage


def _day(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y%m%d")


def _counter(key_id: int, day: str, what: str) -> str:
    return f"fraudlens:keyuse:{key_id}:{day}:{what}"


def check_limits(redis: Redis, key: ApiKey) -> None:
    """Count this request against the key's rate and its day's quota, or raise 429."""
    window = redis.pipeline()
    rate_key = f"fraudlens:keyrate:{key.id}"
    window.incr(rate_key)
    window.expire(rate_key, 60, nx=True)
    window.ttl(rate_key)
    count, _, ttl = window.execute()
    if count > key.rate_per_minute:
        raise WorkflowError(
            429, "rate_limited", "too many requests for this key",
            retry_after_seconds=max(int(ttl), 1), limit_per_minute=key.rate_per_minute,
        )  # fmt: skip
    day = _counter(key.id, _day(), "requests")
    used = redis.incr(day)
    redis.expire(day, USAGE_DAYS * 86_400, nx=True)
    if key.daily_quota is not None and used > key.daily_quota:
        redis.decr(day)  # a refused request is not usage
        midnight = (datetime.now(UTC) + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        raise WorkflowError(
            429, "quota_exceeded", "this key's daily quota is used up",
            retry_after_seconds=int((midnight - datetime.now(UTC)).total_seconds()) + 1,
            daily_quota=key.daily_quota,
        )  # fmt: skip


def count_error(redis: Redis, key_id: int) -> None:
    day = _counter(key_id, _day(), "errors")
    redis.incr(day)
    redis.expire(day, USAGE_DAYS * 86_400, nx=True)


def usage(redis: Redis, key_id: int, days: int) -> list[dict]:
    """Requests and errors per day for the last `days` days, oldest first."""
    today = datetime.now(UTC).date()
    out = []
    for back in range(days - 1, -1, -1):
        d: date = today - timedelta(days=back)
        label = d.strftime("%Y%m%d")
        requests, errors = (
            redis.get(_counter(key_id, label, "requests")),
            redis.get(_counter(key_id, label, "errors")),
        )
        out.append(
            {"day": d.isoformat(), "requests": int(requests or 0), "errors": int(errors or 0)}
        )
    return out
