"""Operations: health, readiness, the audit trail, headline numbers, what is being served."""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Response
from sqlalchemy import Date, cast, func, select, text

from ...decision.evaluate import REPORT_FILE as POLICY_REPORT_FILE
from ...decision.insights import INSIGHTS_FILE
from ...models import registry
from ...platform.audit import WorkflowError
from ...platform.models import AuditLog, Case, Decision, FreezeRequest, Transaction
from ..deps import Db, Oversight, Plat, Staff

router = APIRouter(tags=["operations"])
health = APIRouter(tags=["operations"])


@health.get("/health")
def alive() -> dict:
    """The process is up. Says nothing about its dependencies; see /ready."""
    return {"status": "ok"}


@health.get("/ready")
def ready(p: Plat, response: Response) -> dict:
    checks = {"scorer": p.scorer.ready}
    try:
        with p.db.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        checks["database"] = False
    try:
        checks["redis"] = bool(p.redis.ping())
    except Exception:
        checks["redis"] = False
    ok = all(checks.values())
    if not ok:
        response.status_code = 503
    return {"status": "ready" if ok else "not_ready", "checks": checks, "mode": p.scorer.mode}


@router.get("/audit")
def audit_log(
    s: Db,
    ctx: Oversight,
    actor: Annotated[str | None, Query(max_length=64)] = None,
    action: Annotated[str | None, Query(max_length=48)] = None,
    object_type: Annotated[str | None, Query(max_length=24)] = None,
    object_id: Annotated[str | None, Query(max_length=64)] = None,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> list[dict]:
    """Who did what. Append-only: the database rejects any change to these rows."""
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    for column, value in (
        (AuditLog.actor, actor),
        (AuditLog.action, action),
        (AuditLog.object_type, object_type),
        (AuditLog.object_id, object_id),
    ):
        if value is not None:
            query = query.where(column == value)
    if since is not None:
        query = query.where(AuditLog.at >= since)
    if before_id is not None:
        query = query.where(AuditLog.id < before_id)
    return [
        {
            "id": row.id,
            "at": row.at,
            "actor": row.actor,
            "role": row.role,
            "action": row.action,
            "object_type": row.object_type,
            "object_id": row.object_id,
            "detail": row.detail,
            "request_id": row.request_id,
            "ip": row.ip,
        }
        for row in s.scalars(query)
    ]


@router.get("/metrics/summary")
def summary(p: Plat, s: Db, ctx: Staff, since: datetime | None = None) -> dict:
    """Headline numbers for the dashboard, over decisions made on this platform."""
    window = [Decision.decided_at >= since] if since is not None else []
    tiers = dict(
        s.execute(select(Decision.tier, func.count()).where(*window).group_by(Decision.tier)).all()
    )
    modes = dict(
        s.execute(select(Decision.mode, func.count()).where(*window).group_by(Decision.mode)).all()
    )
    waiting = {
        status: {"count": n, "amount": float(amount)}
        for status, n, amount in s.execute(
            select(Transaction.status, func.count(), func.sum(Transaction.amount))
            .where(Transaction.status.in_(("held", "pending_customer")))
            .group_by(Transaction.status)
        )
    }
    outcomes = {
        status: {"count": n, "amount": float(amount)}
        for status, n, amount in s.execute(
            select(Transaction.status, func.count(), func.sum(Transaction.amount))
            .join(Decision, Decision.txn_id == Transaction.txn_id)
            .where(Decision.tier != "allow", *window)
            .group_by(Transaction.status)
        )
    }
    now = p.scorer.now()
    cases = dict(s.execute(select(Case.status, func.count()).group_by(Case.status)).all())
    overdue = s.scalar(
        select(func.count()).select_from(Case).where(Case.status != "closed", Case.sla_due_at < now)
    )
    verdicts = dict(
        s.execute(
            select(Case.verdict, func.count())
            .where(Case.verdict.is_not(None))
            .group_by(Case.verdict)
        ).all()
    )
    p50, p95, p99 = s.execute(
        select(
            *(func.percentile_cont(q).within_group(Decision.latency_ms) for q in (0.5, 0.95, 0.99))
        ).where(*window)
    ).one()
    try:
        backlog = p.worker.backlog()
    except Exception:
        backlog = None
    return {
        "as_of": now,
        "decisions": {"by_tier": tiers, "by_mode": modes, "total": sum(tiers.values())},
        "alert_outcomes": outcomes,
        "waiting": waiting,
        "cases": {"by_status": cases, "overdue": overdue, "verdicts": verdicts},
        "freeze_requests_pending": s.scalar(
            select(func.count()).select_from(FreezeRequest).where(FreezeRequest.status == "pending")
        ),
        "wallets_frozen": len(p.scorer.frozen),
        "wallets_confirmed_fraud": len(p.scorer.engine.flagged),
        "decision_latency_ms": {
            "p50": p50 and round(p50, 2),
            "p95": p95 and round(p95, 2),
            "p99": p99 and round(p99, 2),
        },
        "stream": {
            "worker_running": p.worker.is_alive(),
            "processed": p.worker.processed,
            "dead_lettered": p.worker.dead_lettered,
            "failures": p.worker.failures,
            "backlog": backlog,
        },
    }


@router.get("/model")
def model(p: Plat, ctx: Staff) -> dict:
    """Exactly which model and policy are deciding, for traceability."""
    scorer, policy = p.scorer, p.scorer.policy
    manifest = scorer.bundle.manifest if scorer.bundle is not None else {}
    return {
        "mode": scorer.mode,
        "model": {
            "version": manifest.get("version"),
            "created_at": manifest.get("created_at"),
            "features": len(manifest.get("features", ())),
            "risk_source": manifest.get("risk_source"),
            "mule_wallet_threshold": manifest.get("mule_wallet_threshold"),
        },
        "policy": {
            "version": policy.version,
            "description": policy.description,
            "thresholds": scorer.decision.thresholds,
            "tiers": {tier: spec.model_dump() for tier, spec in policy.tiers.items()},
            "rules": [
                {
                    "id": rule.id,
                    "description": rule.description,
                    "applies_to": rule.applies_to,
                    "effect": rule.effect,
                    "tier": rule.tier,
                    "hard": rule.hard,
                }
                for rule in policy.rules
            ],
            "fallback": {
                "signals": [signal.id for signal in policy.fallback.signals],
                "points": policy.fallback.points,
                "max_tier": policy.fallback.max_tier,
            },
        },
    }


@router.get("/metrics/daily")
def daily(s: Db, ctx: Staff, since: datetime | None = None) -> list[dict]:
    """Decisions per day by tier, and what became of the money in each day's alerts."""
    window = [Decision.decided_at >= since] if since is not None else []
    day = cast(Decision.decided_at, Date)
    days: dict = {}
    for date, tier, n, amount in s.execute(
        select(day, Decision.tier, func.count(), func.sum(Transaction.amount))
        .join(Transaction, Transaction.txn_id == Decision.txn_id)
        .where(*window)
        .group_by(day, Decision.tier)
    ):
        row = days.setdefault(date, {"date": date, "decisions": {}, "amount": {}, "alerts": {}})
        row["decisions"][tier] = n
        row["amount"][tier] = float(amount)
    for date, status, n, amount in s.execute(
        select(day, Transaction.status, func.count(), func.sum(Transaction.amount))
        .join(Transaction, Transaction.txn_id == Decision.txn_id)
        .where(Decision.tier != "allow", *window)
        .group_by(day, Transaction.status)
    ):
        days[date]["alerts"][status] = {"count": n, "amount": float(amount)}
    return [days[date] for date in sorted(days)]


@lru_cache(maxsize=8)
def _read_json(path: Path, modified: float) -> dict:
    return json.loads(path.read_text())


def _report(directory: Path, name: str) -> dict | None:
    """A report written next to the model, or None if that step has not been run."""
    path = directory / name
    try:
        return _read_json(path, path.stat().st_mtime)
    except (OSError, ValueError):
        return None


@router.get("/model/report")
def model_report(
    p: Plat, ctx: Staff, version: Annotated[str | None, Query(pattern=r"^v\d{1,6}$")] = None
) -> dict:
    """How a model version (default: the served one) and the policy did on the test period.

    A back-test with known labels: `model` is the model card's numbers, `policy` the
    effect of the rules, `insights` the threshold sweep, drift and fairness tables.
    """
    if version is None and p.scorer.bundle is not None:
        version = p.scorer.bundle.version
    if version is None:
        return {"model_version": None, "model": None, "policy": None, "insights": None}
    if version not in registry.versions(p.settings.models_dir):
        raise WorkflowError(404, "model_not_found", f"no model version {version}")
    directory = p.settings.models_dir / version
    return {
        "model_version": version,
        "model": _report(directory, "report.json"),
        "policy": _report(directory, POLICY_REPORT_FILE),
        "insights": _report(directory, INSIGHTS_FILE),
    }


@router.get("/policy")
def policy(p: Plat, ctx: Staff) -> dict:
    """The whole policy as served: tiers, rules with their conditions, texts, fallback."""
    return {
        **p.scorer.policy.model_dump(),
        "resolved_thresholds": p.scorer.decision.thresholds,
        "mode": p.scorer.mode,
    }
