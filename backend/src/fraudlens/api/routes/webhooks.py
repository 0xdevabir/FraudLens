"""Webhook endpoints and the delivery log: where the platform tells other systems what happened."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ...platform import notify
from ...platform.audit import WorkflowError, audit
from ...platform.models import Delivery, WebhookEndpoint
from ..deps import Db, Oversight, Plat, RowId
from ..schemas import WebhookCreate

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _endpoint(e: WebhookEndpoint, **extra) -> dict:
    return {
        "id": e.id,
        "url": e.url,
        "description": e.description,
        "events": e.events,
        "active": e.active,
        "consecutive_failures": e.consecutive_failures,
        "created_at": e.created_at,
        **extra,
    }


def _delivery(d: Delivery) -> dict:
    return {
        "id": d.id,
        "kind": d.kind,
        "endpoint_id": d.endpoint_id,
        "event_type": d.event_type,
        "event_id": d.event_id,
        "status": d.status,
        "attempts": d.attempts,
        "next_attempt_at": d.next_attempt_at,
        "last_status_code": d.last_status_code,
        "last_error": d.last_error,
        "delivered_at": d.delivered_at,
        "created_at": d.created_at,
    }


def _own(s, endpoint_id: int) -> WebhookEndpoint:
    endpoint = s.get(WebhookEndpoint, endpoint_id)
    if endpoint is None:
        raise WorkflowError(404, "endpoint_not_found", f"no webhook endpoint {endpoint_id}")
    return endpoint


@router.get("/events")
def event_types(ctx: Oversight) -> dict:
    """The events an endpoint can subscribe to."""
    return {"events": [e for e in notify.EVENTS if e != "test.ping"]}


@router.get("")
def list_endpoints(s: Db, ctx: Oversight) -> list[dict]:
    pending = dict(
        s.execute(
            select(Delivery.endpoint_id, func.count())
            .where(Delivery.status == "pending", Delivery.endpoint_id.is_not(None))
            .group_by(Delivery.endpoint_id)
        ).all()
    )
    dead = dict(
        s.execute(
            select(Delivery.endpoint_id, func.count())
            .where(Delivery.status == "dead", Delivery.endpoint_id.is_not(None))
            .group_by(Delivery.endpoint_id)
        ).all()
    )
    rows = s.scalars(select(WebhookEndpoint).order_by(WebhookEndpoint.id)).all()
    return [_endpoint(e, pending=pending.get(e.id, 0), dead=dead.get(e.id, 0)) for e in rows]


@router.post("", status_code=201)
def create_endpoint(body: WebhookCreate, p: Plat, s: Db, ctx: Oversight) -> dict:
    """Register an endpoint. The signing secret is in this response and nowhere else."""
    try:
        notify.check_url(body.url, p.settings.webhook_allow_private)
    except notify.UnsafeUrl as exc:
        raise WorkflowError(422, "unsafe_url", str(exc)) from None
    endpoint = WebhookEndpoint(
        url=body.url, description=body.description, secret=notify.new_secret(),
        events=sorted(set(body.events)), created_by=ctx.user_id,
    )  # fmt: skip
    s.add(endpoint)
    s.flush()
    audit(s, ctx, "webhook.create", "webhook", endpoint.id, events=endpoint.events)
    s.commit()
    return _endpoint(endpoint, secret=endpoint.secret)


@router.post("/{endpoint_id}/rotate-secret")
def rotate_secret(endpoint_id: RowId, s: Db, ctx: Oversight) -> dict:
    endpoint = _own(s, endpoint_id)
    endpoint.secret = notify.new_secret()
    audit(s, ctx, "webhook.rotate_secret", "webhook", endpoint.id)
    s.commit()
    return _endpoint(endpoint, secret=endpoint.secret)


@router.post("/{endpoint_id}/test")
def send_test(endpoint_id: RowId, p: Plat, s: Db, ctx: Oversight) -> dict:
    """Send a `test.ping` now and report what the receiver answered."""
    endpoint = _own(s, endpoint_id)
    notify.enqueue(s, "test.ping", {"note": "a test from FraudLens"}, [endpoint])
    audit(s, ctx, "webhook.test", "webhook", endpoint.id)
    s.commit()
    if p.dispatcher is not None:
        p.dispatcher.run_once()
    s.expire_all()
    last = s.scalar(
        select(Delivery)
        .where(Delivery.endpoint_id == endpoint.id, Delivery.event_type == "test.ping")
        .order_by(Delivery.id.desc())
    )
    return _delivery(last)


@router.post("/{endpoint_id}/{action}")
def set_active(
    endpoint_id: RowId, action: Literal["enable", "disable"], s: Db, ctx: Oversight
) -> dict:
    endpoint = _own(s, endpoint_id)
    endpoint.active = action == "enable"
    if endpoint.active:
        endpoint.consecutive_failures = 0
    audit(s, ctx, f"webhook.{action}", "webhook", endpoint.id)
    s.commit()
    return _endpoint(endpoint)


@router.get("/deliveries")
def deliveries(
    s: Db,
    ctx: Oversight,
    status: Literal["pending", "delivered", "dead"] | None = None,
    endpoint_id: Annotated[int | None, Query(ge=1, le=2**31 - 1)] = None,
    kind: Literal["webhook", "sms"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> dict:
    where = []
    if status:
        where.append(Delivery.status == status)
    if endpoint_id:
        where.append(Delivery.endpoint_id == endpoint_id)
    if kind:
        where.append(Delivery.kind == kind)
    rows = s.scalars(
        select(Delivery).where(*where).order_by(Delivery.id.desc()).limit(limit).offset(offset)
    ).all()
    total = s.scalar(select(func.count()).select_from(Delivery).where(*where))
    return {"total": total, "deliveries": [_delivery(d) for d in rows]}


@router.post("/deliveries/{delivery_id}/replay")
def replay(delivery_id: int, s: Db, ctx: Oversight) -> dict:
    """Send a dead or delivered message again. The receiver sees the same event id."""
    d = s.get(Delivery, delivery_id)
    if d is None:
        raise WorkflowError(404, "delivery_not_found", f"no delivery {delivery_id}")
    if d.status == "pending":
        raise WorkflowError(409, "already_pending", "the delivery is already waiting to be sent")
    d.status, d.attempts, d.last_error = "pending", 0, None
    d.next_attempt_at = func.now()
    audit(s, ctx, "webhook.replay", "delivery", d.id, event_type=d.event_type)
    s.commit()
    s.refresh(d)
    return _delivery(d)
