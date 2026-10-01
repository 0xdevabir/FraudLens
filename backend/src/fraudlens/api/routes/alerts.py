"""The alert queue, the full record behind one decision, its case note, the live feed."""

from __future__ import annotations

import contextlib
import json
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError
from sqlalchemy import func, or_, select

from ...decision.narrative import narrate
from ...platform.audit import WorkflowError
from ...platform.events import Identifier
from ...platform.models import Decision, Transaction
from ...platform.stream import alert_feed
from ..deps import Db, Plat, Reviewer, TxnId
from ..views import alert_view, decision_view

router = APIRouter(tags=["alerts"])

NOTE_TTL_SECONDS = 86_400
Tier = Literal["warn", "step_up", "hold"]
Status = Literal["completed", "pending_customer", "held", "cancelled", "blocked", "rejected"]


@router.get("/alerts")
def list_alerts(
    s: Db,
    ctx: Reviewer,
    tier: Annotated[list[Tier] | None, Query()] = None,
    status: Annotated[list[Status] | None, Query()] = None,
    case_id: Annotated[int | None, Query(ge=1, le=2**31 - 1)] = None,
    wallet_id: Identifier | None = None,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> dict:
    where = [Decision.tier != "allow"]
    if tier:
        where.append(Decision.tier.in_(tier))
    if status:
        where.append(Transaction.status.in_(status))
    if case_id is not None:
        where.append(Decision.case_id == case_id)
    if wallet_id is not None:
        where.append(or_(Transaction.sender_id == wallet_id, Transaction.receiver_id == wallet_id))
    if since is not None:
        where.append(Decision.decided_at >= since)
    joined = select(Transaction, Decision).join(Decision, Decision.txn_id == Transaction.txn_id)
    rows = s.execute(
        joined.where(*where)
        .order_by(Decision.decided_at.desc(), Decision.txn_id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    total = s.scalar(
        select(func.count())
        .select_from(Decision)
        .join(Transaction, Decision.txn_id == Transaction.txn_id)
        .where(*where)
    )
    return {"total": total, "alerts": [alert_view(t, d) for t, d in rows]}


def _decision(s, txn_id: int) -> tuple[Transaction, Decision]:
    decision = s.get(Decision, txn_id)
    if decision is None:
        raise WorkflowError(404, "decision_not_found", f"no decision for transaction {txn_id}")
    return s.get(Transaction, txn_id), decision


@router.get("/decisions/{txn_id}")
def get_decision(txn_id: TxnId, s: Db, ctx: Reviewer) -> dict:
    return decision_view(*_decision(s, txn_id))


@router.get("/decisions/{txn_id}/narrative")
def narrative(
    txn_id: TxnId, p: Plat, s: Db, ctx: Reviewer, lang: Literal["en", "bn"] = "en"
) -> dict:
    """The case note. Written by the language model only if that is enabled and its
    text passes the grounding check; otherwise the fixed template."""
    _, decision = _decision(s, txn_id)
    evidence = decision.detail.get("evidence")
    if evidence is None:
        raise WorkflowError(404, "no_alert", "an allowed transaction has no case note")
    if p.narrator is None:
        return narrate(evidence, None, lang)
    key = f"fraudlens:note:{txn_id}:{lang}"
    try:
        if cached := p.redis.get(key):
            return json.loads(cached)
    except RedisError:
        pass  # the cache is an optimisation
    note = narrate(evidence, p.narrator, lang)
    if note["source"] == "llm":
        with contextlib.suppress(RedisError):
            p.redis.set(key, json.dumps(note, ensure_ascii=False), ex=NOTE_TTL_SECONDS)
    return note


@router.get("/stream/alerts")
async def stream_alerts(request: Request, ctx: Reviewer) -> StreamingResponse:
    """Server-sent events: new alerts as they are decided."""
    settings = request.app.state.platform.settings
    feed = alert_feed(request.app.state.aredis, settings.alerts_channel)
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(feed, media_type="text/event-stream", headers=headers)
