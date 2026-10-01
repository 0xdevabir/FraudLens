"""Evaluate the decision policy end to end on the held-out test period.

Answers, with numbers: what do the rules add to the model's tiers, what does the
system achieve at each tier, how much is lost if the model is unavailable, and
does every alert come with reasons and a case note that passes the grounding
check. Also builds the similar-case index for the model version.

    uv run python -m fraudlens.decision.evaluate
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import settings
from ..features import FEATURES
from ..features.build import epoch_seconds, iter_txns
from ..models import registry
from ..models.bundle import ModelBundle
from ..models.data import load_frame
from ..models.metrics import alert_outcomes
from .engine import DecisionEngine
from .narrative import check_grounding, template
from .policy import ALERT_TIERS, DEFAULT_POLICY, RANK, TIERS, Policy, apply_policy, load_policy
from .similar import SimilarCases

REPORT_FILE = "policy_report.json"
SAMPLE_ALERTS, SAMPLE_ALLOWED = 400, 200
SEED = 7


def add_context(frame: pd.DataFrame, flags: pd.DataFrame) -> pd.DataFrame:
    """Confirmed-fraud facts as the case system would know them at each transaction."""
    flagged_at = dict(zip(flags["wallet_id"], epoch_seconds(flags["flagged_at"]), strict=True))
    ts = epoch_seconds(frame["ts"])
    out = frame.copy()
    for column, wallet in (("sender_flagged", "sender_id"), ("recipient_flagged", "receiver_id")):
        when = out[wallet].map(flagged_at).to_numpy(dtype=np.float64)  # NaN when never flagged
        out[column] = (when <= ts).astype(np.float64)
    return out


def decide_batch(
    policy: Policy, frame: pd.DataFrame, bundle: ModelBundle | None
) -> tuple[list, np.ndarray | None]:
    """Policy outcome per row. With no bundle, every row is decided on rules only."""
    x = frame[list(FEATURES)].to_numpy(dtype=np.float64)
    context = frame[["sender_flagged", "recipient_flagged"]].to_numpy()
    risk = thresholds = mule_alert = None
    if bundle is not None:
        scores = bundle.score(x)
        risk, thresholds = scores["risk"], policy.resolve_thresholds(bundle.manifest)
        mule_alert = np.where(
            np.isnan(scores["mule"]),
            np.nan,
            scores["mule"] >= bundle.manifest["mule_wallet_threshold"],
        )
    outcomes = []
    for i, txn_type in enumerate(frame["type"].tolist()):
        fields = dict(zip(FEATURES, x[i].tolist(), strict=True))
        fields["sender_flagged"], fields["recipient_flagged"] = context[i].tolist()
        if mule_alert is not None:
            fields["recipient_mule_alert"] = float(mule_alert[i])
        row_risk = float(risk[i]) if risk is not None else None
        outcomes.append(apply_policy(policy, txn_type, fields, row_risk, thresholds))
    return outcomes, risk


def _by_tier(frame: pd.DataFrame, tiers: np.ndarray) -> dict:
    """Cumulative outcomes: everything at or above each tier."""
    rank = np.array([RANK[t] for t in tiers])
    return {tier: alert_outcomes(frame, rank >= RANK[tier]) for tier in ALERT_TIERS}


def _counts(tiers) -> dict:
    counts = Counter(tiers)
    return {tier: int(counts.get(tier, 0)) for tier in TIERS}


def _rule_stats(policy: Policy, frame: pd.DataFrame, outcomes: list) -> dict:
    y, loss = frame["y"].to_numpy(), frame["y_loss"].to_numpy()
    stats = {}
    for rule in policy.rules:
        fired = np.array([rule in o.fired for o in outcomes])
        decisive = np.array([o.decided_by.endswith(f":{rule.id}") for o in outcomes])
        stats[rule.id] = {
            "effect": f"{rule.effect} {rule.tier}",
            "fired": int(fired.sum()),
            "fired_precision": _share(y[fired]),
            # decisive: the rule, not the model, set the final tier
            "decisive": int(decisive.sum()),
            "decisive_precision": _share(y[decisive]),
            "decisive_victim_transfers": int(loss[decisive].sum()),
        }
    return stats


def _share(values: np.ndarray) -> float | None:
    return round(float(values.mean()), 4) if len(values) else None


def _sample_check(
    engine: DecisionEngine,
    frame: pd.DataFrame,
    txns: pd.DataFrame,
    tiers: np.ndarray,
) -> dict:
    """Run single decisions the way the API will, and check them against the batch."""
    rng = np.random.default_rng(SEED)
    alert_pos = np.flatnonzero(tiers != "allow")
    allow_pos = np.flatnonzero(tiers == "allow")
    picked = np.concatenate(
        [
            rng.choice(alert_pos, min(SAMPLE_ALERTS, len(alert_pos)), replace=False),
            rng.choice(allow_pos, min(SAMPLE_ALLOWED, len(allow_pos)), replace=False),
        ]
    )
    sample = frame.iloc[picked]
    rows = txns.set_index("txn_id").loc[sample["txn_id"]].reset_index()
    x = sample[list(FEATURES)].to_numpy(dtype=np.float64)
    context = sample[["sender_flagged", "recipient_flagged"]].to_dict("records")

    millis, mismatches, no_reasons, ungrounded = [], 0, 0, []
    alerts = 0
    for i, txn in enumerate(iter_txns(rows)):
        t0 = time.perf_counter()
        decision = engine.decide(txn, x[i], context[i])
        millis.append((time.perf_counter() - t0) * 1000)
        mismatches += decision.tier != tiers[picked[i]]
        if decision.tier == "allow":
            continue
        alerts += 1
        no_reasons += not any(r["direction"] == "raises" for r in decision.reasons)
        for lang in ("en", "bn"):
            grounding = check_grounding(template(decision.evidence, lang), decision.evidence)
            if not grounding.ok:
                ungrounded.append({"txn_id": int(txn.txn_id), "lang": lang, **grounding.__dict__})
    millis = np.array(millis)
    return {
        "decisions": len(picked),
        "alerts": alerts,
        "tier_differs_from_batch": int(mismatches),
        "alerts_without_a_raising_reason": int(no_reasons),
        "case_notes_checked": alerts * 2,
        "case_notes_failing_grounding": len(ungrounded),
        "grounding_failures": ungrounded[:5],
        "decide_ms": {
            "p50": round(float(np.percentile(millis, 50)), 2),
            "p95": round(float(np.percentile(millis, 95)), 2),
            "max": round(float(millis.max()), 2),
        },
    }


def _similar_stats(index: SimilarCases, bundle: ModelBundle, test: pd.DataFrame) -> dict:
    """How often the nearest past case is the same kind of scam as a new victim transfer."""
    loss = test[test["y_loss"] == 1]
    contrib, _ = bundle.contributions(loss[list(FEATURES)].to_numpy())
    nearest = index.top_typology(contrib)
    actual = loss["typology"].to_numpy(str)
    known = np.isin(actual, np.unique(index.typology))
    table = pd.crosstab(pd.Series(actual, name="actual"), pd.Series(nearest, name="nearest"))
    return {
        "indexed_transfers": len(index),
        "indexed_cases": int(len(np.unique(index.case_id))),
        "indexed_typologies": sorted(np.unique(index.typology).tolist()),
        "test_victim_transfers": int(len(loss)),
        "nearest_case_same_typology": _share(nearest[known] == actual[known]),
        "typologies_never_seen_before": sorted(np.unique(actual[~known]).tolist()),
        "nearest_by_actual": {a: row[row > 0].to_dict() for a, row in table.iterrows()},
    }


def run(
    data_dir: Path,
    policy_version: str = DEFAULT_POLICY,
    model: str | None = None,
    models_root: Path | None = None,
) -> dict:
    policy = load_policy(policy_version)
    bundle = registry.load(model, models_root)
    model_dir = (models_root or registry.models_dir()) / bundle.version

    frame = load_frame(data_dir)
    frame = add_context(frame, pd.read_parquet(data_dir / "wallet_flags.parquet"))
    index = SimilarCases.build(bundle, frame, pd.read_parquet(data_dir / "cases.parquet"))
    index.save(model_dir)

    # Same rows as the model report: the test period, without the ambiguous roles.
    test = frame[(frame["fold"] == "test") & ~frame["ambiguous"]].reset_index(drop=True)
    outcomes, risk = decide_batch(policy, test, bundle)
    tiers = np.array([o.tier for o in outcomes])
    model_tiers = np.array([o.model_tier for o in outcomes])
    fallback, _ = decide_batch(policy, test, None)
    fallback_tiers = np.array([o.tier for o in fallback])

    engine = DecisionEngine(policy, bundle, index)
    txns = pd.read_parquet(data_dir / "transactions.parquet")
    report = {
        "policy_version": policy.version,
        "model_version": bundle.version,
        "thresholds": policy.resolve_thresholds(bundle.manifest),
        "rows": len(test),
        "days": int(test["day"].nunique()),
        "tier_counts": {
            "model_only": _counts(model_tiers),
            "policy": _counts(tiers),
            "rules_only_fallback": _counts(fallback_tiers),
        },
        "decided_by": dict(Counter(o.decided_by for o in outcomes)),
        "rules": _rule_stats(policy, test, outcomes),
        "outcomes": {
            "model_only": _by_tier(test, model_tiers),
            "policy": _by_tier(test, tiers),
            "rules_only_fallback": _by_tier(test, fallback_tiers),
        },
        "fallback_signals": dict(Counter(s for o in fallback for s in o.fallback_signals)),
        "similar_cases": _similar_stats(index, bundle, test),
        "single_decisions": _sample_check(engine, test, txns, tiers),
    }
    (model_dir / REPORT_FILE).write_text(json.dumps(report, indent=2))
    return report


def print_summary(report: dict) -> None:
    print(f"policy {report['policy_version']} on model {report['model_version']}, test period")
    print(pd.DataFrame(report["tier_counts"]).to_string())
    print("\ndecided by:", json.dumps(report["decided_by"]))
    print("\nrules:")
    print(pd.DataFrame(report["rules"]).T.to_string())
    keys = [
        "alerts",
        "alerts_per_day",
        "precision",
        "loss_txn_recall",
        "case_recall",
        "taka_recall",
        "taka_recall_with_exit_holds",
    ]
    for name, tiers in report["outcomes"].items():
        print(f"\n{name}, everything at or above each tier:")
        print(pd.DataFrame({t: {k: v[k] for k in keys} for t, v in tiers.items()}).to_string())
    print("\nheld-out typology at warn (case recall):")
    for name, tiers in report["outcomes"].items():
        held_out = tiers["warn"]["by_typology"].get("investment_scam", {})
        print(f"  {name}: {held_out.get('case_recall')}")
    similar = {k: v for k, v in report["similar_cases"].items() if k != "nearest_by_actual"}
    print("\nsimilar cases:", json.dumps(similar))
    print("single decisions:", json.dumps(report["single_decisions"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the decision policy on the test period")
    parser.add_argument("--small", action="store_true")
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--policy", default=DEFAULT_POLICY)
    parser.add_argument("--model", default=None, help="model version (default: current)")
    args = parser.parse_args()
    data_dir = args.data or settings.data_dir / ("small" if args.small else "full")
    print_summary(run(data_dir, args.policy, args.model))


if __name__ == "__main__":
    main()
