"""Shadow mode: a challenger model scored next to the served one.

The challenger sees the same feature vector as the served model and its answer
is written to `shadow_scores`. Nothing reads that table to decide anything, so a
new model can be watched on real traffic before anyone is affected by it.

Two ways in. Live: set FRAUDLENS_SHADOW_MODEL_VERSION and the scorer records the
challenger on every decision. Afterwards: this module scores the feature vectors
already stored with past decisions, which gives the same numbers.

    uv run python -m fraudlens.mlops.shadow --version v3

The comparison is tier against tier of the two models alone (`model_tier`, before
any rule), each with its own thresholds. Outcomes are only known for alerts an
analyst has closed, so what the challenger would have caught that the served
model missed is reported as a count with no outcome.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..config import Settings
from ..decision.policy import TIERS, Policy, load_policy, tier_for
from ..features import FEATURES
from ..models import registry
from ..models.bundle import ModelBundle
from ..platform.db import make_engine, make_sessions
from ..platform.models import Case, Decision, ShadowScore

log = logging.getLogger(__name__)

BATCH = 5_000


class Shadow:
    """A challenger and the cut-offs the policy would give it."""

    def __init__(self, bundle: ModelBundle, policy: Policy) -> None:
        self.bundle = bundle
        self.thresholds = policy.resolve_thresholds(bundle.manifest)

    @property
    def version(self) -> str:
        return self.bundle.version

    @classmethod
    def load(cls, version: str, root: Path, policy: Policy) -> Shadow:
        return cls(registry.load(version, root), policy)

    def risk(self, features: np.ndarray) -> np.ndarray:
        return self.bundle.score(features)["risk"]

    def score(self, features) -> tuple[float, str]:
        risk = float(self.risk(np.asarray(features, dtype=np.float64))[0])
        return risk, tier_for(risk, self.thresholds)


def backfill(s: Session, shadow: Shadow, limit: int | None = None) -> int:
    """Score stored decisions the challenger has not seen yet. Returns how many."""
    done = 0
    scored = select(ShadowScore.txn_id).where(ShadowScore.model_version == shadow.version)
    while limit is None or done < limit:
        size = BATCH if limit is None else min(BATCH, limit - done)
        rows = s.execute(
            select(Decision.txn_id, Decision.features)
            .where(Decision.risk.is_not(None), Decision.txn_id.not_in(scored))
            .order_by(Decision.txn_id)
            .limit(size)
        ).all()
        if not rows:
            break
        usable = [row for row in rows if len(row.features) == len(FEATURES)]
        if len(usable) < len(rows):
            raise ValueError("stored decisions were made with a different feature list")
        risk = shadow.risk(np.array([row.features for row in usable], dtype=np.float64))
        s.execute(
            pg_insert(ShadowScore).on_conflict_do_nothing(),
            [
                {
                    "txn_id": row.txn_id,
                    "model_version": shadow.version,
                    "risk": float(r),
                    "tier": tier_for(float(r), shadow.thresholds),
                    "latency_ms": None,
                }
                for row, r in zip(usable, risk, strict=True)
            ],
        )
        s.commit()
        done += len(rows)
    return done


def versions(s: Session) -> list[str]:
    return list(s.scalars(select(ShadowScore.model_version).distinct().order_by("model_version")))


def compare(s: Session, version: str) -> dict:
    """The challenger against the served model on the decisions both have scored."""
    both = (
        select(Decision.txn_id)
        .join(ShadowScore, ShadowScore.txn_id == Decision.txn_id)
        .where(ShadowScore.model_version == version, Decision.model_tier.is_not(None))
    )
    pairs = s.execute(
        select(Decision.model_tier, ShadowScore.tier, func.count())
        .join(ShadowScore, ShadowScore.txn_id == Decision.txn_id)
        .where(ShadowScore.model_version == version, Decision.model_tier.is_not(None))
        .group_by(Decision.model_tier, ShadowScore.tier)
    ).all()
    total = sum(n for _, _, n in pairs)
    if not total:
        return {"model_version": version, "decisions": 0}
    matrix = {served: dict.fromkeys(TIERS, 0) for served in TIERS}
    for served, challenger, n in pairs:
        matrix[served][challenger] = n

    def rates(side: str) -> dict:
        count = {
            tier: sum(n for a, b, n in pairs if (a if side == "served" else b) == tier)
            for tier in TIERS
        }
        return {
            "tiers": count,
            "alert_rate": round(1 - count["allow"] / total, 5),
            "hold_rate": round(count["hold"] / total, 5),
        }

    # Alerts an analyst has closed are the only decisions with a known outcome.
    reviewed = {"confirmed_fraud": dict.fromkeys(TIERS, 0), "legitimate": dict.fromkeys(TIERS, 0)}
    for verdict, tier, n in s.execute(
        select(Case.verdict, ShadowScore.tier, func.count())
        .join(Decision, Decision.case_id == Case.id)
        .join(ShadowScore, ShadowScore.txn_id == Decision.txn_id)
        .where(ShadowScore.model_version == version, Case.verdict.in_(reviewed))
        .group_by(Case.verdict, ShadowScore.tier)
    ):
        reviewed[verdict][tier] = n
    fraud, clean = reviewed["confirmed_fraud"], reviewed["legitimate"]

    live = s.execute(
        select(
            func.count(),
            func.avg(ShadowScore.latency_ms),
            func.percentile_cont(0.95).within_group(ShadowScore.latency_ms),
            func.avg(Decision.latency_ms),
        )
        .join(Decision, Decision.txn_id == ShadowScore.txn_id)
        .where(ShadowScore.model_version == version, ShadowScore.latency_ms.is_not(None))
    ).one()
    served_version = s.execute(
        select(Decision.model_version, func.count())
        .where(Decision.txn_id.in_(both))
        .group_by(Decision.model_version)
        .order_by(func.count().desc())
        .limit(1)
    ).first()
    agree = sum(matrix[tier][tier] for tier in TIERS)
    return {
        "model_version": version,
        "served_version": served_version[0] if served_version else None,
        "decisions": total,
        "agreement": round(agree / total, 5),
        "matrix": matrix,  # matrix[served tier][challenger tier]
        "served": rates("served"),
        "challenger": rates("challenger"),
        "challenger_only_alerts": sum(matrix["allow"][tier] for tier in TIERS[1:]),
        "served_only_alerts": sum(matrix[tier]["allow"] for tier in TIERS[1:]),
        "reviewed": {
            "confirmed_fraud": {
                "alerts": sum(fraud.values()),
                "challenger_also_alerts": sum(fraud.values()) - fraud["allow"],
                "challenger_holds": fraud["hold"],
                "by_tier": fraud,
            },
            "false_positive": {
                "alerts": sum(clean.values()),
                "challenger_would_allow": clean["allow"],
                "by_tier": clean,
            },
        },
        "latency": {
            "scored_live": live[0],
            "challenger_mean_ms": None if live[1] is None else round(float(live[1]), 3),
            "challenger_p95_ms": None if live[2] is None else round(float(live[2]), 3),
            "served_mean_ms": None if live[3] is None else round(float(live[3]), 3),
        },
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Score past decisions with a challenger model")
    parser.add_argument("--version", required=True, help="the challenger, for example v3")
    parser.add_argument("--limit", type=int, default=None, help="score at most this many")
    args = parser.parse_args()
    settings = Settings()
    shadow = Shadow.load(args.version, settings.models_dir, load_policy(settings.policy_version))
    engine = make_engine(settings.database_url)
    with make_sessions(engine)() as s:
        log.info("scored %d decisions with %s", backfill(s, shadow, args.limit), shadow.version)
        print(json.dumps(compare(s, shadow.version), indent=2, default=str))


if __name__ == "__main__":
    main()
