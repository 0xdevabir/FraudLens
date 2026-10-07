"""The cross-provider mule-intelligence consortium (simulated; see docs/CONSORTIUM.md).

Read-only views of partner feeds, matches and the hub's audit chain, a receiver
lookup, and the dispute path. The state comes from `artifacts/consortium/`, written
by `python -m fraudlens.consortium.simulate`; without it these routes answer 404 and
nothing else in the platform changes.
"""

from __future__ import annotations

import threading
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel, Field

from ...consortium.demo import ConsortiumService
from ...consortium.protocol import ProtocolError
from ...platform.audit import WorkflowError, audit
from ...platform.events import Identifier
from ...platform.scoring import clean
from ..deps import Db, Plat, Reviewer, Supervisor

router = APIRouter(prefix="/consortium", tags=["consortium"])

_lock = threading.Lock()
_services: dict[str, ConsortiumService | None] = {}


def service(p: Plat) -> ConsortiumService:
    key = str(p.settings.artifacts_dir)
    with _lock:
        if key not in _services:
            _services[key] = ConsortiumService.load(p.settings.artifacts_dir)
    svc = _services[key]
    if svc is None:
        raise WorkflowError(404, "consortium_not_built",
                            "run `python -m fraudlens.consortium.simulate` first")  # fmt: skip
    return svc


class Lookup(BaseModel):
    wallet_id: Identifier


class DisputeIn(BaseModel):
    listing_id: str = Field(pattern=r"^[a-z]{2,16}:\d{1,8}$")
    raised_by: str = Field(pattern=r"^[a-z]{2,16}$")
    reason: str = Field(min_length=3, max_length=500)


class Resolve(BaseModel):
    outcome: Literal["upheld", "withdrawn"]


def _protocol(exc: ProtocolError) -> WorkflowError:
    return WorkflowError(409, "consortium_refused", str(exc))


@router.get("")
def overview(p: Plat, ctx: Reviewer) -> dict:
    """Members, their latest signed feeds, the privacy guarantees and the measured results."""
    return clean(service(p).overview())


@router.get("/matches")
def matches(p: Plat, ctx: Reviewer,
            limit: Annotated[int, Query(ge=1, le=500)] = 200) -> list[dict]:  # fmt: skip
    """Test-period receivers that matched a partner listing (one row per wallet)."""
    return clean(service(p).matches(limit))


@router.get("/audit")
def hub_audit(p: Plat, ctx: Reviewer,
              limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict]:  # fmt: skip
    """The hub's hash-chained log, newest first: counts and ids, never identifiers."""
    return clean(service(p).audit(limit))


@router.post("/lookup")
def lookup(body: Lookup, p: Plat, s: Db, ctx: Reviewer) -> dict:
    svc = service(p)
    try:
        out = svc.lookup(body.wallet_id)
    except ProtocolError as exc:
        raise WorkflowError(404, "not_in_consortium", str(exc)) from exc
    audit(s, ctx, "consortium.lookup", "wallet", body.wallet_id,
          matches=len(out["matches"]), signal=out["signal"])  # fmt: skip
    s.commit()
    return clean(out)


@router.post("/disputes", status_code=201)
def open_dispute(body: DisputeIn, p: Plat, s: Db, ctx: Reviewer) -> dict:
    """Contest a listing: it stops counting for every member at once."""
    try:
        out = service(p).open_dispute(body.raised_by, body.listing_id, body.reason)
    except ProtocolError as exc:
        raise _protocol(exc) from exc
    audit(s, ctx, "consortium.dispute", "listing", body.listing_id, dispute=out["dispute_id"])
    s.commit()
    return clean(out)


@router.post("/disputes/{dispute_id}/resolve")
def resolve(
    dispute_id: Annotated[str, Path(pattern=r"^D\d{4}$")],
    body: Resolve,
    p: Plat,
    s: Db,
    ctx: Supervisor,
) -> dict:
    """Decide a dispute for the listing member: keep the listing, or withdraw it for good."""
    try:
        out = service(p).resolve_dispute(dispute_id, body.outcome)
    except ProtocolError as exc:
        raise _protocol(exc) from exc
    audit(s, ctx, "consortium.resolve", "dispute", dispute_id, outcome=body.outcome)
    s.commit()
    return clean(out)
