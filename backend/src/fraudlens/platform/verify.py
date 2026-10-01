"""Check that the platform decided the replayed test period exactly as the offline evaluation.

    uv run python -m fraudlens.platform.verify

The numbers in the model card and the policy report come from a batch replay.
This compares them, transaction by transaction, with what the running platform
stored after receiving the same events through the stream and the API: the tier,
the risk and the full feature vector. Any difference means the served system is
not the evaluated one.
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd
from sqlalchemy import Engine, select

from ..config import Settings
from ..decision import load_policy
from ..decision.evaluate import add_context, decide_batch
from ..features import FEATURES
from ..models import registry
from ..models.data import load_frame
from .db import make_engine
from .models import Decision, Transaction

REPORT = "replay_verification.json"


def verify(engine: Engine, settings: Settings) -> dict:
    data_dir = settings.dataset_dir
    bundle = registry.load(settings.model_version, settings.models_dir)
    policy = load_policy(settings.policy_version)
    frame = load_frame(data_dir)
    frame = add_context(frame, pd.read_parquet(data_dir / "wallet_flags.parquet"))
    test = frame[frame["fold"] == "test"].reset_index(drop=True)

    with engine.connect() as connection:
        rows = connection.execute(
            select(Decision.txn_id, Decision.tier, Decision.risk, Decision.mode, Decision.features)
            .join(Transaction, Transaction.txn_id == Decision.txn_id)
            .where(Transaction.source == "replay")
        ).all()
    served = pd.DataFrame(rows, columns=["txn_id", "tier", "risk", "mode", "features"])
    served = served.set_index("txn_id")

    both = test[test["txn_id"].isin(served.index)].reset_index(drop=True)
    report = {
        "model_version": bundle.version,
        "policy_version": policy.version,
        "test_scored_transactions": len(test),
        "served_decisions": len(served),
        "compared": len(both),
        "not_replayed": int(len(test) - len(both)),
        "served_but_not_in_test_period": int(len(served) - len(both)),
    }
    if both.empty:
        return {**report, "ok": False, "problem": "nothing to compare: replay the test period"}

    outcomes, risk = decide_batch(policy, both, bundle)
    served = served.loc[both["txn_id"]]
    offline_tier = np.array([o.tier for o in outcomes])
    tier_differs = offline_tier != served["tier"].to_numpy()
    offline_x = both[list(FEATURES)].to_numpy(dtype=np.float64)
    served_x = np.array(served["features"].tolist(), dtype=np.float64)
    same = (offline_x == served_x) | (np.isnan(offline_x) & np.isnan(served_x))
    rows_differ = ~same.all(axis=1)
    worst = np.array(FEATURES)[~same.all(axis=0)]
    risk_diff = np.abs(risk - served["risk"].to_numpy(dtype=np.float64))
    report |= {
        "tier_mismatches": int(tier_differs.sum()),
        "tier_mismatch_examples": both.loc[tier_differs, "txn_id"].head(5).tolist(),
        "feature_rows_differing": int(rows_differ.sum()),
        "features_differing": worst.tolist()[:10],
        "max_abs_risk_difference": float(np.nanmax(risk_diff)),
        "served_in_rules_only_mode": int((served["mode"] != "model").sum()),
        "tiers_served": served["tier"].value_counts().to_dict(),
    }
    report["ok"] = (
        report["tier_mismatches"] == 0
        and report["feature_rows_differing"] == 0
        and report["max_abs_risk_difference"] < 1e-9
        and report["served_in_rules_only_mode"] == 0
        and report["served_but_not_in_test_period"] == 0
    )
    return report


def main() -> None:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    settings = Settings()
    report = verify(make_engine(settings.database_url), settings)
    reports = settings.artifacts_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / REPORT).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
