"""The decision policy: a versioned file of business rules, kept apart from the models.

The models produce a risk score. The policy says what happens at each level of
risk and holds the rules that apply whatever the score is (a confirmed-fraud
wallet, a handset linked to fraud). Rules are data, not code: a condition is
`field op number`, checked by a fixed comparison table, so changing the policy
never executes anything and every decision can list the rules it went through.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..features import BEHAVIOUR_FIELDS, FEATURES, SCORED_TYPES
from .reasons import RULE_CODES

Tier = Literal["allow", "warn", "step_up", "hold"]
TIERS: tuple[Tier, ...] = ("allow", "warn", "step_up", "hold")
RANK = {tier: i for i, tier in enumerate(TIERS)}
ALERT_TIERS = TIERS[1:]

# Facts that are not model features: they come from the case system, the mule model
# and the sender's behaviour profile (usual places and networks).
CONTEXT_FIELDS = (
    "sender_flagged",
    "recipient_flagged",
    "recipient_mule_alert",
    *BEHAVIOUR_FIELDS,
)
FIELDS = frozenset(FEATURES) | frozenset(CONTEXT_FIELDS)

POLICY_DIR = Path(__file__).parent / "policies"
DEFAULT_POLICY = "v2"

_OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    ">": operator.gt,
    "<=": operator.le,
    "<": operator.lt,
}


class PolicyError(ValueError):
    """The policy file is missing, malformed or inconsistent."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Condition(_Model):
    field: str
    op: Literal["==", "!=", ">=", ">", "<=", "<"]
    value: float

    @model_validator(mode="after")
    def _known_field(self) -> Condition:
        if self.field not in FIELDS:
            raise ValueError(f"unknown field {self.field!r}")
        return self

    def test(self, fields: Mapping[str, float]) -> bool | None:
        """True or False, or None when the input is missing and nothing can be said."""
        observed = fields.get(self.field)
        if observed is None or math.isnan(observed):
            return None
        return bool(_OPS[self.op](observed, self.value))


def evaluate(conditions: tuple[Condition, ...], fields: Mapping[str, float]) -> str:
    """'fired' if all hold, 'not_fired' if any fails, 'not_evaluated' if undecidable."""
    results = [c.test(fields) for c in conditions]
    if any(r is False for r in results):
        return "not_fired"
    return "not_evaluated" if any(r is None for r in results) else "fired"


class Rule(_Model):
    id: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    description: str = Field(min_length=1)
    description_bn: str = Field(min_length=1)
    applies_to: tuple[str, ...] = SCORED_TYPES
    when: tuple[Condition, ...] = Field(min_length=1)
    effect: Literal["raise_to", "cap_at"]
    tier: Tier
    hard: bool = False  # a hard rule cannot be lowered by a cap
    reason: str
    # which customer message it implies
    scenario: Literal["scam", "takeover", "unusual_access"] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Rule:
        if unknown := set(self.applies_to) - set(SCORED_TYPES):
            raise ValueError(f"rule {self.id}: unknown transaction types {sorted(unknown)}")
        if self.reason not in RULE_CODES:
            raise ValueError(f"rule {self.id}: unknown reason code {self.reason!r}")
        if self.effect == "raise_to" and self.tier == "allow":
            raise ValueError(f"rule {self.id}: raising to 'allow' does nothing")
        if self.effect == "cap_at" and (self.hard or self.tier == "hold"):
            raise ValueError(f"rule {self.id}: a cap cannot be hard or cap at 'hold'")
        return self


class TierSpec(_Model):
    action: Literal["proceed", "show_warning", "step_up_auth", "hold_for_review"]
    human_review: bool = False
    cooling_off_minutes: int | None = Field(default=None, ge=0)
    review_sla_minutes: int | None = Field(default=None, gt=0)


class Thresholds(_Model):
    # "model": use the thresholds the model version was validated with (its manifest).
    source: Literal["model", "policy"] = "model"
    overrides: dict[Literal["warn", "step_up", "hold"], float] = Field(default_factory=dict)


class Segment(_Model):
    """A group of payments scored against its own cut-offs, e.g. to a wallet under 30 days old.

    The cut-offs are the model's own times `scale`, so they follow the model version
    they are resolved against. A segment changes only the model's tier: rules are
    applied afterwards exactly as for any other payment, so a hard rule still holds.
    Guards, applied when the thresholds are resolved: the segment's warn cut-off is
    capped at the policy's hold cut-off, so a payment held for anyone is at least
    warned in every segment; the hold cut-off stays below 1, so every segment can
    still be held for a person; and a file whose cut-offs still break
    `0 < warn <= step_up <= hold < 1` (a scale below 1) is refused.
    """

    id: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    description: str = Field(min_length=1)
    applies_to: tuple[str, ...] = SCORED_TYPES
    when: tuple[Condition, ...] = Field(min_length=1)
    scale: dict[Literal["warn", "step_up", "hold"], float]
    # How the scale was chosen, so the file says where its numbers come from.
    fitted: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> Segment:
        if unknown := set(self.applies_to) - set(SCORED_TYPES):
            raise ValueError(f"segment {self.id}: unknown transaction types {sorted(unknown)}")
        if set(self.scale) != set(ALERT_TIERS):
            raise ValueError(f"segment {self.id}: scale needs each of {list(ALERT_TIERS)}")
        if not all(0 < value < 1_000 for value in self.scale.values()):
            raise ValueError(f"segment {self.id}: scales must be positive and below 1000")
        return self

    def thresholds(self, base: Mapping[str, float]) -> dict[str, float]:
        """This segment's cut-offs, given the policy's resolved ones; refused if unsafe."""
        scaled = {tier: float(base[tier]) * self.scale[tier] for tier in ALERT_TIERS}
        # The same scales are resolved against every model version (a shadow model uses
        # the served policy), and their spacing differs. So rather than the file being
        # refused for one model and accepted for another: the warn cut-off is capped at
        # the policy's hold cut-off; the hold cut-off keeps at least 1/scale of the
        # policy's room below 1 (so something can always be held); step-up stays between.
        scaled["warn"] = min(scaled["warn"], float(base["hold"]))
        if self.scale["hold"] > 1:
            room = (1 - float(base["hold"])) / self.scale["hold"]
            scaled["hold"] = min(scaled["hold"], 1 - room)
        scaled["step_up"] = min(max(scaled["step_up"], scaled["warn"]), scaled["hold"])
        try:
            check_thresholds(scaled)
        except PolicyError as exc:
            raise PolicyError(f"segment {self.id}: {exc}") from exc
        return scaled

    def matches(self, txn_type: str, fields: Mapping[str, float]) -> bool:
        return txn_type in self.applies_to and evaluate(self.when, fields) == "fired"


class Signal(_Model):
    id: str
    when: tuple[Condition, ...] = Field(min_length=1)


class Fallback(_Model):
    """What to do when no model score is available: count simple risk signals."""

    signals: tuple[Signal, ...] = Field(min_length=1)
    points: dict[Literal["warn", "step_up"], int]
    max_tier: Literal["warn", "step_up"] = "step_up"

    @model_validator(mode="after")
    def _ordered(self) -> Fallback:
        if set(self.points) != {"warn", "step_up"}:
            raise ValueError("fallback.points needs both 'warn' and 'step_up'")
        if not 1 <= self.points["warn"] <= self.points["step_up"] <= len(self.signals):
            raise ValueError("fallback.points must satisfy 1 <= warn <= step_up <= signals")
        return self


class Text(_Model):
    en: str = Field(min_length=1)
    bn: str = Field(min_length=1)


class Recommendation(_Model):
    id: str
    min_tier: Tier = "warn"
    applies_to: tuple[str, ...] = SCORED_TYPES
    when: tuple[Condition, ...] = ()
    en: str = Field(min_length=1)
    bn: str = Field(min_length=1)
    needs_second_approver: bool = False


Scenario = Literal["scam", "takeover", "cash_out", "unusual_access"]
# Every policy words these three; any other is needed only by the rules that name it.
SCENARIOS: tuple[Scenario, ...] = ("scam", "takeover", "cash_out")


class Policy(_Model):
    version: str = Field(min_length=1)
    description: str = ""
    thresholds: Thresholds = Thresholds()
    # Payments scored against their own cut-offs; the first segment that matches applies.
    segments: tuple[Segment, ...] = ()
    tiers: dict[Tier, TierSpec]
    rules: tuple[Rule, ...]
    fallback: Fallback
    takeover_when: tuple[Condition, ...] = Field(min_length=1)
    messages: dict[Scenario, dict[Literal["warn", "step_up", "hold"], Text]]
    recommendations: tuple[Recommendation, ...] = ()

    @model_validator(mode="after")
    def _complete(self) -> Policy:
        if set(self.tiers) != set(TIERS):
            raise ValueError(f"tiers must define exactly {list(TIERS)}")
        if self.tiers["allow"].action != "proceed":
            raise ValueError("the 'allow' tier must proceed")
        # Holding a customer's money is a high-impact action: a person has to review it.
        if not self.tiers["hold"].human_review:
            raise ValueError("the 'hold' tier must set human_review: true")
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule ids must be unique")
        segment_ids = [s.id for s in self.segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("segment ids must be unique")
        rec_ids = [r.id for r in self.recommendations]
        if len(rec_ids) != len(set(rec_ids)):
            raise ValueError("recommendation ids must be unique")
        named = (rule.scenario for rule in self.rules if rule.scenario)
        for scenario in dict.fromkeys((*SCENARIOS, *named)):
            if set(self.messages.get(scenario, {})) != set(ALERT_TIERS):
                raise ValueError(f"messages.{scenario} needs a text for each of {ALERT_TIERS}")
        overrides = self.thresholds.overrides
        if self.thresholds.source == "policy" and set(overrides) != set(ALERT_TIERS):
            raise ValueError("thresholds.source 'policy' needs all three thresholds")
        # Partial overrides are checked against the model's thresholds when they are resolved.
        given = [overrides[t] for t in ALERT_TIERS if t in overrides]
        if given != sorted(given) or not all(0 < value < 1 for value in given):
            raise ValueError(
                f"thresholds must satisfy 0 < warn <= step_up <= hold < 1: {overrides}"
            )
        return self

    def resolve_thresholds(self, manifest: Mapping | None) -> dict[str, float]:
        """Score cut-offs per tier: the model's own, with any policy overrides on top."""
        base: dict[str, float] = {}
        if self.thresholds.source == "model":
            if manifest is None or "thresholds" not in manifest:
                raise PolicyError("policy uses the model's thresholds but no manifest was given")
            base = {tier: float(manifest["thresholds"][tier]) for tier in ALERT_TIERS}
        resolved = base | dict(self.thresholds.overrides)
        check_thresholds(resolved)
        for segment in self.segments:  # refuse an unsafe segment now, not at the first payment
            segment.thresholds(resolved)
        return resolved

    def segment_for(self, txn_type: str, fields: Mapping[str, float]) -> Segment | None:
        return next((s for s in self.segments if s.matches(txn_type, fields)), None)


def check_thresholds(thresholds: Mapping[str, float]) -> None:
    warn, step_up, hold = (thresholds[t] for t in ALERT_TIERS)
    if not 0 < warn <= step_up <= hold < 1:
        raise PolicyError(f"thresholds must satisfy 0 < warn <= step_up <= hold < 1: {thresholds}")


def tier_for(risk: float, thresholds: Mapping[str, float]) -> Tier:
    for tier in reversed(ALERT_TIERS):
        if risk >= thresholds[tier]:
            return tier
    return "allow"


def load_policy(version: str = DEFAULT_POLICY, directory: Path | None = None) -> Policy:
    path = (directory or POLICY_DIR) / f"{version}.yaml"
    if not path.is_file():
        raise PolicyError(f"no policy file at {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        policy = Policy.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        raise PolicyError(f"invalid policy {path.name}: {exc}") from exc
    if policy.version != version:
        raise PolicyError(f"{path.name} declares version {policy.version!r}")
    return policy


# ------------------------------------------------------------------ evaluation


@dataclass(frozen=True)
class Outcome:
    tier: Tier
    model_tier: Tier | None  # None in rules-only mode
    mode: Literal["model", "rules_only"]
    decided_by: str  # "model", "fallback", "rule:<id>" or "cap:<id>"
    trace: tuple[dict, ...]
    fired: tuple[Rule, ...]
    fallback_signals: tuple[str, ...] = ()
    segment: Segment | None = None  # the segment whose cut-offs set the model's tier
    thresholds: Mapping[str, float] | None = None  # the cut-offs actually used


def apply_policy(
    policy: Policy,
    txn_type: str,
    fields: Mapping[str, float],
    risk: float | None,
    thresholds: Mapping[str, float] | None,
) -> Outcome:
    """Combine the model's tier with the rules. Pure: same inputs, same decision.

    With `risk` None (no model available) the fallback signals stand in for the
    score, and only a hard rule can take the result above the fallback's `max_tier`.
    """
    if txn_type not in SCORED_TYPES:
        raise ValueError(f"{txn_type} transactions are not scored")
    signals: tuple[str, ...] = ()
    segment = None
    if risk is None or thresholds is None:
        mode, model_tier = "rules_only", None
        fallback = policy.fallback
        signals = tuple(s.id for s in fallback.signals if evaluate(s.when, fields) == "fired")
        if len(signals) >= fallback.points["step_up"]:
            tier: Tier = "step_up"
        elif len(signals) >= fallback.points["warn"]:
            tier = "warn"
        else:
            tier = "allow"
        tier = min(tier, fallback.max_tier, key=RANK.__getitem__)
        decided_by = "fallback"
    else:
        if math.isnan(risk):
            raise ValueError("risk is NaN")
        mode, decided_by = "model", "model"
        # A segment moves the model's cut-offs only; the rules below see every payment alike.
        if segment := policy.segment_for(txn_type, fields):
            thresholds = segment.thresholds(thresholds)
        tier = model_tier = tier_for(risk, thresholds)

    trace, fired = [], []
    for rule in policy.rules:
        applicable = txn_type in rule.applies_to
        status = evaluate(rule.when, fields) if applicable else "not_applicable"
        trace.append(
            {
                "id": rule.id,
                "description": rule.description,
                "effect": rule.effect,
                "tier": rule.tier,
                "hard": rule.hard,
                "status": status,
                "inputs": {c.field: _plain(fields.get(c.field)) for c in rule.when}
                if applicable
                else {},
            }
        )
        if status == "fired":
            fired.append(rule)

    for rule in fired:
        if rule.effect != "raise_to":
            continue
        target = rule.tier
        if mode == "rules_only" and not rule.hard:
            target = min(target, policy.fallback.max_tier, key=RANK.__getitem__)
        if RANK[target] > RANK[tier]:
            tier, decided_by = target, f"rule:{rule.id}"
    if not any(rule.hard for rule in fired):
        for rule in fired:
            if rule.effect == "cap_at" and RANK[rule.tier] < RANK[tier]:
                tier, decided_by = rule.tier, f"cap:{rule.id}"

    return Outcome(
        tier, model_tier, mode, decided_by, tuple(trace), tuple(fired), signals, segment, thresholds
    )


def _plain(value: float | None) -> float | None:
    return None if value is None or math.isnan(value) else float(value)
