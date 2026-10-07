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
from ...platform.audit import WorkflowError, audit
from ...platform.events import Identifier
from ...platform.models import Decision, Transaction
from ...platform.pii import mask_id
from ...platform.stream import alert_feed
from ..deps import Db, Plat, Reviewer, TxnId
from ..export import MAX_ROWS, check_reveal, check_too_large, csv_response
from ..views import alert_view, decision_view

router = APIRouter(tags=["alerts"])

NOTE_TTL_SECONDS = 86_400
Tier = Literal["warn", "step_up", "hold"]
Status = Literal["completed", "pending_customer", "held", "cancelled", "blocked", "rejected"]


def _alert_where(
    tier, status, case_id, wallet_id, since, until, amount_min, amount_max, district, q
) -> list:
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
    if until is not None:
        where.append(Decision.decided_at < until)
    if amount_min is not None:
        where.append(Transaction.amount >= amount_min)
    if amount_max is not None:
        where.append(Transaction.amount <= amount_max)
    if district:
        where.append(Transaction.district == district)
    if q:
        # A transaction id, or the start of a wallet id on either side.
        match = [
            Transaction.sender_id.startswith(q, autoescape=True),
            Transaction.receiver_id.startswith(q, autoescape=True),
        ]
        if q.isdigit() and len(q) < 19:
            match.append(Transaction.txn_id == int(q))
        where.append(or_(*match))
    return where


Q = Annotated[str | None, Query(max_length=32, pattern=r"^[A-Za-z0-9_-]+$")]
Money = Annotated[float | None, Query(ge=0, le=1e12)]


@router.get("/alerts")
def list_alerts(
    s: Db,
    ctx: Reviewer,
    tier: Annotated[list[Tier] | None, Query()] = None,
    status: Annotated[list[Status] | None, Query()] = None,
    case_id: Annotated[int | None, Query(ge=1, le=2**31 - 1)] = None,
    wallet_id: Identifier | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    amount_min: Money = None,
    amount_max: Money = None,
    district: Annotated[str | None, Query(max_length=40)] = None,
    q: Q = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> dict:
    where = _alert_where(
        tier, status, case_id, wallet_id, since, until, amount_min, amount_max, district, q
    )
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


ALERT_COLUMNS = (
    "txn_id", "decided_at", "tier", "status", "amount", "sender_id", "receiver_id",
    "district", "risk_score", "risk_band", "case_id", "customer_response", "headline",
)  # fmt: skip


@router.get("/alerts.csv")
def export_alerts(
    s: Db,
    ctx: Reviewer,
    tier: Annotated[list[Tier] | None, Query()] = None,
    status: Annotated[list[Status] | None, Query()] = None,
    case_id: Annotated[int | None, Query(ge=1, le=2**31 - 1)] = None,
    wallet_id: Identifier | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    amount_min: Money = None,
    amount_max: Money = None,
    district: Annotated[str | None, Query(max_length=40)] = None,
    q: Q = None,
    reveal: bool = False,
) -> StreamingResponse:
    """The alert queue as CSV, for the same filters. Identifiers are masked unless a
    supervisor passes `reveal=true`. Every export is audited."""
    check_reveal(ctx.role, reveal)
    where = _alert_where(
        tier, status, case_id, wallet_id, since, until, amount_min, amount_max, district, q
    )
    total = s.scalar(
        select(func.count())
        .select_from(Decision)
        .join(Transaction, Decision.txn_id == Transaction.txn_id)
        .where(*where)
    )
    check_too_large(total)
    rows = s.execute(
        select(Transaction, Decision)
        .join(Decision, Decision.txn_id == Transaction.txn_id)
        .where(*where)
        .order_by(Decision.decided_at.desc(), Decision.txn_id.desc())
        .limit(MAX_ROWS)
    ).all()
    show = (lambda v: v) if reveal else mask_id
    out = [
        (
            t.txn_id, d.decided_at.isoformat(), d.tier, t.status, t.amount,
            show(t.sender_id), show(t.receiver_id), t.district, d.risk_score, d.risk_band,
            d.case_id, d.customer_response, d.detail.get("headline"),
        )
        for t, d in rows
    ]  # fmt: skip
    audit(
        s, ctx, "export.alerts", "alerts", None,
        rows=len(out), revealed=reveal,
        filters={
            "tier": tier, "status": status, "case_id": case_id, "wallet_id": bool(wallet_id),
            "since": since and since.isoformat(), "until": until and until.isoformat(),
            "amount_min": amount_min, "amount_max": amount_max, "district": district,
            "q": bool(q),
        },
    )  # fmt: skip
    s.commit()
    return csv_response("fraudlens-alerts", ALERT_COLUMNS, out, total)


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
