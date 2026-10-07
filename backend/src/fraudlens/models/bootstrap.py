"""Bootstrap 95% confidence intervals for the headline test-period metrics.

The headline numbers (scams caught at the warn tier, the false-alert rate on
legitimate payments, PR-AUC) are point estimates from one test period of 25 days
and 360 scams. This puts an interval around each, two ways:

- by day: whole days are resampled. Days are the coarsest unit the data has, so
  this keeps the correlation between transactions of the same day and the same scam.
- by scam: each scam (all its fraud-chain transactions) is one unit, and each
  legitimate transaction is one unit. Scam and legitimate units are resampled
  separately, so every replicate has the same number of each.

Both are percentile intervals. Nothing is refitted: the model, its calibration and
its thresholds are fixed, so the intervals describe the uncertainty of the test
measurement, not of training.

    uv run python -m fraudlens.models.bootstrap            # served model, data/full
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

from ..config import settings
from . import registry
from .data import load_frame

OUTPUT_FILE = "bootstrap_ci.json"
REPLICATES = 1000
SEED = 7


def point_metrics(
    y: np.ndarray,
    loss: np.ndarray,
    case: np.ndarray,
    risk: np.ndarray,
    alert: np.ndarray,
    w: np.ndarray | None = None,
) -> dict[str, float]:
    """Scam recall, false-alert rate and PR-AUC, with optional row weights.

    A scam counts as caught when at least one of its victim transfers is alerted
    (the model card's "scams caught"). With weights, a scam's weight is the weight
    of its victim transfers (all equal under both resampling schemes).
    """
    w = np.ones(len(y)) if w is None else w
    legit = y == 0
    fpr = float((w * (legit & alert)).sum() / max((w * legit).sum(), 1e-12))
    victim = pd.DataFrame({"case": case[loss], "hit": alert[loss], "w": w[loss]})
    per_case = victim.groupby("case").agg(hit=("hit", "any"), w=("w", "first"))
    recall = float((per_case["w"] * per_case["hit"]).sum() / max(per_case["w"].sum(), 1e-12))
    keep = w > 0
    pr_auc = float(average_precision_score(y[keep], risk[keep], sample_weight=w[keep]))
    return {"scam_recall": recall, "false_alert_rate": fpr, "pr_auc": pr_auc}


def _unit_weights(units: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Row weights from resampling the distinct units with replacement."""
    codes, inverse = np.unique(units, return_inverse=True)
    counts = np.bincount(rng.integers(0, len(codes), len(codes)), minlength=len(codes))
    return counts[inverse].astype(np.float64)


def bootstrap(
    test: pd.DataFrame,
    risk: np.ndarray,
    alert: np.ndarray,
    replicates: int = REPLICATES,
    seed: int = SEED,
) -> dict:
    """Point estimates and percentile intervals under both schemes.

    `test` needs y, y_loss, case_id (negative or missing for legitimate rows) and day.
    """
    y = test["y"].to_numpy().astype(int)
    loss = test["y_loss"].to_numpy() == 1
    case = test["case_id"].fillna(-1).to_numpy().astype(np.int64)
    day = test["day"].to_numpy()
    alert = np.asarray(alert, dtype=bool)
    risk = np.asarray(risk, dtype=np.float64)
    rng = np.random.default_rng(seed)

    # By scam: fraud rows grouped by scam, legitimate rows on their own; the two
    # strata are drawn separately.
    fraud_rows = case >= 0
    row_ids = np.arange(len(y))

    draws: dict[str, list[dict]] = {"by_day": [], "by_scam": []}
    for _ in range(replicates):
        draws["by_day"].append(point_metrics(y, loss, case, risk, alert, _unit_weights(day, rng)))
        w = np.zeros(len(y))
        w[fraud_rows] = _unit_weights(case[fraud_rows], rng)
        w[~fraud_rows] = _unit_weights(row_ids[~fraud_rows], rng)
        draws["by_scam"].append(point_metrics(y, loss, case, risk, alert, w))

    point = point_metrics(y, loss, case, risk, alert)
    out = {
        "point": {k: round(v, 5) for k, v in point.items()},
        "n": {
            "rows": int(len(y)),
            "legitimate": int((y == 0).sum()),
            "scams": int(len(np.unique(case[loss]))),
            "days": int(len(np.unique(day))),
        },
    }
    for scheme, rows in draws.items():
        frame = pd.DataFrame(rows)
        out[scheme] = {
            metric: {
                "low": round(float(frame[metric].quantile(0.025)), 5),
                "high": round(float(frame[metric].quantile(0.975)), 5),
                "std": round(float(frame[metric].std()), 5),
            }
            for metric in frame.columns
        }
    return out


def run(
    data_dir: Path,
    model: str | None = None,
    models_root: Path | None = None,
    replicates: int = REPLICATES,
) -> dict:
    from ..decision.evaluate import add_context, decide_batch
    from ..decision.policy import DEFAULT_POLICY, RANK, load_policy

    root = models_root or registry.models_dir()
    version = model or registry.current_version(root)
    model_dir = root / version
    manifest = json.loads((model_dir / "manifest.json").read_text())
    scores = pd.read_parquet(model_dir / "test_scores.parquet")

    frame = load_frame(data_dir)
    test = frame[~frame["ambiguous"] & (frame["fold"] == "test")].reset_index(drop=True)
    if not (scores["txn_id"].to_numpy() == test["txn_id"].to_numpy()).all():
        raise ValueError(f"{model_dir / 'test_scores.parquet'} does not match the test rows")
    risk = scores["risk"].to_numpy()
    threshold = float(manifest["thresholds"]["warn"])

    # The served decision: the policy's tiers (model thresholds plus its rules). The
    # headline false-alert rate comes from insights.json, so use the policy it used.
    insights = model_dir / "insights.json"
    policy_version = (
        json.loads(insights.read_text())["policy_version"] if insights.exists() else DEFAULT_POLICY
    )
    policy = load_policy(policy_version)
    bundle = registry.load(version, root)
    context = add_context(test, pd.read_parquet(data_dir / "wallet_flags.parquet"))
    outcomes, _ = decide_batch(policy, context, bundle)
    policy_alert = np.array([RANK[o.tier] > 0 for o in outcomes])

    report = {
        "model_version": version,
        "data_dir": str(data_dir),
        "replicates": replicates,
        "seed": SEED,
        "interval": "percentile, 2.5% to 97.5%",
        "model_warn_threshold": {
            "threshold": threshold,
            **bootstrap(test, risk, risk >= threshold, replicates),
        },
        "policy_warn_or_above": {
            "policy": policy.version,
            **bootstrap(test, risk, policy_alert, replicates),
        },
    }
    (model_dir / OUTPUT_FILE).write_text(json.dumps(report, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data", type=Path, default=settings.data_dir / "full")
    parser.add_argument("--model", default=None, help="version, default: the served one")
    parser.add_argument("--replicates", type=int, default=REPLICATES)
    args = parser.parse_args()
    report = run(args.data, args.model, replicates=args.replicates)
    for name in ("model_warn_threshold", "policy_warn_or_above"):
        part = report[name]
        print(f"\n{name} ({report['model_version']}, {report['replicates']} replicates)")
        for metric, value in part["point"].items():
            ci = ", ".join(
                f"{s} [{part[s][metric]['low']:.4f}, {part[s][metric]['high']:.4f}]"
                for s in ("by_day", "by_scam")
            )
            print(f"  {metric}: {value:.4f}  {ci}")


if __name__ == "__main__":
    main()
