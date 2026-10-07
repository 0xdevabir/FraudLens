"""What the payment switch calls: score a transaction, send events, report a flag."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from ...platform import sandbox
from ...platform.audit import Ctx, WorkflowError, audit
from ...platform.events import FlagIn, TxnIn
from ...platform.scoring import clean
from ...platform.stream import MAX_BACKLOG, enqueue
from ..deps import REVIEWERS, EventsCaller, Plat, ScoreCaller, Service, require
from ..schemas import Events
from ..views import result_view

router = APIRouter(tags=["scoring"])


@router.post("/score")
def score(body: TxnIn, request: Request, p: Plat, ctx: ScoreCaller) -> dict:
    """Decide on one transaction, synchronously. Safe to retry: the same `txn_id`
    returns the recorded outcome with `duplicate: true`.

    Authenticate with the service account's token or a partner API key (`X-API-Key`).
    A sandbox key gets a canned decision chosen by the cents of the amount (.01 allow,
    .02 warn, .03 step_up, .04 hold) and changes nothing."""
    if request.state.sandbox:
        return sandbox.score(body, p.scorer.policy)
    [result] = p.scorer.process([body.event()])
    if result.status == "stale":
        raise WorkflowError(
            409, "stale_event", "this transaction is too far behind the stream to be scored"
        )
    return result_view(result)


@router.post("/score/what-if")
def what_if(
    body: TxnIn, p: Plat, ctx: Annotated[Ctx, Depends(require("service", *REVIEWERS))]
) -> dict:
    """The decision a transaction would get right now. Nothing is stored or changed."""
    t0 = time.perf_counter()
    try:
        decision = p.scorer.what_if(body.event().txn)
    except KeyError:
        raise WorkflowError(
            404, "unknown_party", "a what-if needs wallets and agents the platform has seen"
        ) from None
    latency_ms = round((time.perf_counter() - t0) * 1000, 2)
    spec = p.scorer.policy.tiers[decision.tier]
    summary = {
        "tier": decision.tier,
        "action": decision.action,
        "requires_review": decision.requires_review,
        "risk_score": decision.risk_score,
        "risk_band": decision.risk_band,
        "mode": decision.mode,
        "model_version": decision.model_version,
        "policy_version": decision.policy_version,
        "customer_message": decision.customer_message,
        "cooling_off_minutes": spec.cooling_off_minutes,
        "review_sla_minutes": spec.review_sla_minutes,
        "latency_ms": latency_ms,
    }
    if ctx.role == "service":  # a channel learns what to do, not why
        return {"decision": summary}
    full = clean(decision.to_dict())
    full.pop("risk"), full.pop("narrative")
    return {"decision": summary, "explanation": full}


@router.post("/events", status_code=202)
def events(body: Events, request: Request, p: Plat, ctx: EventsCaller) -> dict:
    """Queue events for asynchronous scoring, in order. Alerts appear on the live feed.
    A sandbox key is told the events were accepted and nothing is queued."""
    if request.state.sandbox:
        return {"accepted": len(body.events), "sandbox": True}
    backlog = p.worker.backlog()
    if backlog > MAX_BACKLOG:
        raise WorkflowError(
            503, "backlog_full", "the event queue is full; retry later",
            retry_after_seconds=30, backlog=backlog,
        )  # fmt: skip
    return {"accepted": enqueue(p.redis, p.settings.events_stream, body.events)}


@router.post("/wallet-flags")
def wallet_flag(body: FlagIn, p: Plat, ctx: Service, response: Response) -> dict:
    """A wallet confirmed as fraud by an investigation outside this platform."""
    known = body.wallet_id in p.scorer.engine.flagged
    event = body.event()
    with p.scorer.transaction() as s:
        if not known:
            p.scorer.flag(s, event.wallet_id, event.ts, event.reason, event.source)
            audit(
                s, ctx, "wallet.flag", "wallet", body.wallet_id,
                reason=body.reason, source=body.source,
            )  # fmt: skip
    return {"wallet_id": body.wallet_id, "status": "already_flagged" if known else "flagged"}
