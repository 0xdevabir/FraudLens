"""From one transaction's features to a decision a person can read and question.

    score (models) -> tier (policy thresholds + rules) -> reasons, message, next steps

The engine recommends and, at most, pauses: its strongest action is to hold money
for an analyst. It never refuses a payment or freezes a wallet by itself. If the
model cannot be used, it keeps working on rules alone and says so in the result.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import numpy as np

from ..features import FEATURES, FeatureEngine, Txn
from ..models.bundle import ModelBundle, logit
from .narrative import mask_id, template
from .policy import ALERT_TIERS, RANK, Policy, apply_policy, evaluate
from .reasons import explain
from .similar import SimilarCases

log = logging.getLogger(__name__)

RISK_SCALE = 100
_SCORE_ANCHORS = (40.0, 60.0, 80.0)  # display score at the warn, step-up and hold thresholds
_BANDS = {"allow": "low", "warn": "elevated", "step_up": "high", "hold": "very high"}


def display_score(risk: float, thresholds: Mapping[str, float]) -> int:
    """Risk on a 0-100 scale for screens: 40, 60 and 80 are the tier thresholds.

    The raw probability is a good ranking but not a well calibrated percentage, so
    it is not shown. This scale is a monotonic relabelling of it, anchored to the
    points where the system's behaviour changes.
    """
    knots = [1e-6, *(thresholds[tier] for tier in ALERT_TIERS), 1 - 1e-6]
    value = np.interp(
        logit(np.array([risk])), logit(np.array(knots)), [0.0, *_SCORE_ANCHORS, 100.0]
    )
    return int(math.floor(value[0] + 1e-9))


def context_from_engine(engine: FeatureEngine, txn: Txn) -> dict[str, float]:
    """Facts the policy needs that are not model features: confirmed-fraud flags."""
    return {
        "sender_flagged": float(txn.sender_id in engine.flagged),
        "recipient_flagged": float(txn.receiver_id in engine.flagged),
    }


@dataclass(frozen=True)
class Decision:
    txn_id: int
    txn_type: str
    tier: str
    action: str
    requires_review: bool  # a person must decide before anything further happens
    mode: str  # "model" or "rules_only"
    decided_by: str
    model_tier: str | None
    risk: float | None  # model probability, for thresholds and audit; not for display
    risk_score: int | None  # 0-100 display scale
    risk_band: str
    scores: dict
    model_version: str | None
    policy_version: str
    scenario: str | None
    rule_trace: tuple[dict, ...]
    fallback_signals: tuple[str, ...]
    reasons: tuple[dict, ...]
    customer_message: dict | None
    recommended_actions: tuple[dict, ...]
    similar_cases: tuple[dict, ...]
    evidence: dict
    narrative: str | None

    def to_dict(self) -> dict:
        return asdict(self)


class DecisionEngine:
    def __init__(
        self,
        policy: Policy,
        bundle: ModelBundle | None = None,
        similar: SimilarCases | None = None,
    ) -> None:
        self.policy = policy
        self.bundle = bundle
        self.similar = similar
        self.thresholds = policy.resolve_thresholds(bundle.manifest) if bundle else None
        self.mule_threshold = float(bundle.manifest["mule_wallet_threshold"]) if bundle else None

    def decide(
        self, txn: Txn, features: Sequence[float], context: Mapping[str, float] | None = None
    ) -> Decision:
        """Decide on `txn` given its feature vector (FEATURES order, computed before the
        transaction is applied) and the context facts from `context_from_engine`."""
        x = np.asarray(features, dtype=np.float64)
        if x.shape != (len(FEATURES),):
            raise ValueError(f"expected {len(FEATURES)} features, got shape {x.shape}")
        fields: dict[str, float] = dict(zip(FEATURES, x.tolist(), strict=True))
        fields.update(context or {})

        scores = self._score(x)
        risk = None
        if scores is not None:
            risk = scores["risk"]
            if scores["mule"] is not None:
                fields["recipient_mule_alert"] = float(scores["mule"] >= self.mule_threshold)

        outcome = apply_policy(self.policy, txn.type, fields, risk, self.thresholds)
        tier, spec = outcome.tier, self.policy.tiers[outcome.tier]
        alert = tier != "allow"

        contribution = self._contribution(x) if alert and scores is not None else None
        model_reasons = explain(x.tolist(), contribution) if contribution is not None else []
        reasons = [_rule_reason(rule) for rule in outcome.fired] + model_reasons

        scenario = self._scenario(txn.type, outcome.fired, fields) if alert else None
        message = self.policy.messages[scenario][tier].model_dump() if alert else None
        actions = [
            {
                "id": rec.id,
                "en": rec.en,
                "bn": rec.bn,
                "needs_second_approver": rec.needs_second_approver,
            }
            for rec in self.policy.recommendations
            if RANK[rec.min_tier] <= RANK[tier]
            and txn.type in rec.applies_to
            and evaluate(rec.when, fields) == "fired"
        ]
        similar = (
            self.similar.query(contribution)
            if self.similar is not None and contribution is not None
            else []
        )

        risk_score = display_score(risk, self.thresholds) if risk is not None else None
        band = _BANDS[outcome.model_tier] if outcome.model_tier else "not scored"
        evidence = {
            "transaction": {
                "id": txn.txn_id,
                "type": txn.type,
                "amount": float(txn.amount),
                "time": datetime.fromtimestamp(txn.ts, UTC).strftime("%Y-%m-%d %H:%M"),
                "channel": txn.channel,
                "district": txn.district,
                "sender": mask_id(txn.sender_id),
                "receiver": mask_id(txn.receiver_id),
            },
            "decision": {
                "tier": tier,
                "action": spec.action,
                "mode": outcome.mode,
                "decided_by": outcome.decided_by,
                "risk_band": band,
                "risk_score": risk_score,
                "risk_scale": RISK_SCALE,
                "human_review": spec.human_review,
                "cooling_off_minutes": spec.cooling_off_minutes,
                "review_sla_minutes": spec.review_sla_minutes,
                "fallback_signals": list(outcome.fallback_signals),
            },
            "reasons": [
                {k: r[k] for k in ("code", "source", "direction", *_TEXT_KEYS)} for r in reasons
            ],
            "similar_cases": similar,
            "recommended_actions": actions,
        }

        return Decision(
            txn_id=txn.txn_id,
            txn_type=txn.type,
            tier=tier,
            action=spec.action,
            requires_review=spec.human_review,
            mode=outcome.mode,
            decided_by=outcome.decided_by,
            model_tier=outcome.model_tier,
            risk=risk,
            risk_score=risk_score,
            risk_band=band,
            scores=scores or {},
            model_version=self.bundle.version if scores is not None else None,
            policy_version=self.policy.version,
            scenario=scenario,
            rule_trace=outcome.trace,
            fallback_signals=outcome.fallback_signals,
            reasons=tuple(reasons),
            customer_message=message,
            recommended_actions=tuple(actions),
            similar_cases=tuple(similar),
            evidence=evidence,
            narrative=template(evidence) if alert else None,
        )

    # ------------------------------------------------------------- internals

    def _scenario(self, txn_type: str, fired, fields: Mapping[str, float]) -> str:
        """Which customer message fits: a scam on the customer, or someone else in their wallet."""
        for rule in fired:
            if rule.scenario:
                return rule.scenario
        if evaluate(self.policy.takeover_when, fields) == "fired":
            return "takeover"
        return "cash_out" if txn_type == "CASH_OUT" else "scam"

    def _score(self, x: np.ndarray) -> dict | None:
        """Model scores, or None when there is no usable model (rules-only mode)."""
        if self.bundle is None:
            return None
        try:
            parts = self.bundle.score(x)
            scores = {name: float(values[0]) for name, values in parts.items()}
        except Exception:
            # A payment must never fail because scoring did. Fall back to rules and
            # leave the evidence in the log for whoever is on call.
            log.exception("scoring failed; deciding on rules only")
            return None
        if math.isnan(scores["risk"]):
            log.error("model returned NaN risk; deciding on rules only")
            return None
        if math.isnan(scores["mule"]):
            scores["mule"] = None  # cash-outs have no recipient wallet
        return scores

    def _contribution(self, x: np.ndarray) -> np.ndarray | None:
        try:
            return self.bundle.contributions(x)[0][0]
        except Exception:
            log.exception("explanation failed; the decision stands without model reasons")
            return None


_TEXT_KEYS = ("title_en", "title_bn", "detail_en", "detail_bn")


def _rule_reason(rule) -> dict:
    return {
        "code": rule.reason,
        "source": "rule",
        "direction": "raises" if rule.effect == "raise_to" else "lowers",
        "rule_id": rule.id,
        "title_en": f"Policy rule {rule.id}",
        "title_bn": f"নীতিমালার নিয়ম {rule.id}",
        "detail_en": rule.description.rstrip("."),
        "detail_bn": rule.description_bn.rstrip("।"),
    }
