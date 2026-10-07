"""Partner API keys: made, listed, limited, revoked, and how much each is used."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from ...platform import apikeys
from ...platform.audit import WorkflowError, audit
from ...platform.models import ApiKey
from ..deps import Db, Oversight, Plat, RowId
from ..schemas import ApiKeyCreate

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _view(k: ApiKey, today: int | None = None, **extra) -> dict:
    return {
        "id": k.id,
        "name": k.name,
        "prefix": k.prefix,
        "scopes": k.scopes,
        "sandbox": k.sandbox,
        "rate_per_minute": k.rate_per_minute,
        "daily_quota": k.daily_quota,
        "created_at": k.created_at,
        "last_used_at": k.last_used_at,
        "revoked_at": k.revoked_at,
        "requests_today": today,
        **extra,
    }


def _key(s, key_id: int) -> ApiKey:
    key = s.get(ApiKey, key_id)
    if key is None:
        raise WorkflowError(404, "key_not_found", f"no API key {key_id}")
    return key


@router.get("")
def list_keys(p: Plat, s: Db, ctx: Oversight) -> list[dict]:
    rows = s.scalars(select(ApiKey).order_by(ApiKey.id.desc())).all()
    return [_view(k, apikeys.usage(p.redis, k.id, 1)[0]["requests"]) for k in rows]


@router.post("", status_code=201)
def create_key(body: ApiKeyCreate, s: Db, ctx: Oversight) -> dict:
    """Make a key. The key itself is in this response and nowhere else."""
    raw, prefix = apikeys.generate()
    key = ApiKey(
        name=body.name, prefix=prefix, key_hash=apikeys.hash_key(raw),
        scopes=sorted(set(body.scopes)), sandbox=body.sandbox,
        rate_per_minute=body.rate_per_minute, daily_quota=body.daily_quota,
        created_by=ctx.user_id,
    )  # fmt: skip
    s.add(key)
    s.flush()
    audit(
        s, ctx, "apikey.create", "apikey", key.id,
        scopes=key.scopes, sandbox=key.sandbox, rate_per_minute=key.rate_per_minute,
    )  # fmt: skip
    s.commit()
    return _view(key, key=raw)


@router.post("/{key_id}/revoke")
def revoke(key_id: RowId, s: Db, ctx: Oversight) -> dict:
    key = _key(s, key_id)
    if key.revoked_at is not None:
        raise WorkflowError(409, "already_revoked", "the key was already revoked")
    key.revoked_at = datetime.now(UTC)
    audit(s, ctx, "apikey.revoke", "apikey", key.id, prefix=key.prefix)
    s.commit()
    return _view(key)


@router.get("/{key_id}/usage")
def key_usage(
    key_id: RowId, p: Plat, s: Db, ctx: Oversight, days: Annotated[int, Query(ge=1, le=40)] = 14
) -> dict:
    key = _key(s, key_id)
    return {"key": _view(key), "days": apikeys.usage(p.redis, key.id, days)}
