"""The network view: wallets, their neighbours, mule rings, agent risk."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from ...platform.audit import WorkflowError, audit
from ...platform.events import Identifier
from ...platform.models import PastCase
from ...platform.scoring import clean
from ..deps import Db, Plat, Reviewer, TxnId

router = APIRouter(tags=["network"])


@router.get("/wallets/{wallet_id}")
def wallet(wallet_id: Identifier, p: Plat, s: Db, ctx: Reviewer) -> dict:
    """A wallet's profile. Looking at a customer is itself recorded."""
    view = p.graph.wallet(s, wallet_id)
    audit(s, ctx, "wallet.view", "wallet", wallet_id)
    s.commit()
    return clean(view)


@router.get("/wallets/{wallet_id}/network")
def network(
    wallet_id: Identifier,
    p: Plat,
    ctx: Reviewer,
    limit: Annotated[int, Query(ge=1, le=80)] = 40,
) -> dict:
    return clean(p.graph.network(wallet_id, limit))


@router.get("/rings")
def rings(p: Plat, ctx: Reviewer) -> list[dict]:
    """Groups of suspicious wallets tied together by shared handsets, agents or payers."""
    return clean(p.graph.rings())


@router.get("/rings/{ring_id}")
def ring(ring_id: Annotated[str, Path(pattern=r"^R-[A-Za-z0-9_-]{1,32}$")], p: Plat, ctx: Reviewer):
    return clean(p.graph.ring(ring_id))


@router.get("/agents/risk")
def agents(p: Plat, ctx: Reviewer, limit: Annotated[int, Query(ge=1, le=500)] = 50) -> list[dict]:
    """Agents ranked by how unusual their cash-out traffic is. A reason to look, not a finding."""
    return clean(p.graph.agents()[:limit])


@router.get("/agents/{agent_id}")
def agent(agent_id: Identifier, p: Plat, s: Db, ctx: Reviewer) -> dict:
    return clean(p.graph.agent(s, agent_id))


@router.get("/past-cases/{case_id}")
def past_case(case_id: TxnId, s: Db, ctx: Reviewer) -> dict:
    """A confirmed case from before go-live, as cited under 'similar past cases'."""
    case = s.get(PastCase, case_id)
    if case is None:
        raise WorkflowError(404, "past_case_not_found", f"no past case {case_id}")
    return {
        "case_id": case.case_id,
        "typology": case.typology,
        "victim_id": case.victim_id,
        "mule_id": case.mule_id,
        "started_at": case.started_at,
        "loss": case.loss,
        "transfers": case.n_txn,
    }
