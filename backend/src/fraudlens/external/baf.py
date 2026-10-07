"""Bank Account Fraud (BAF, NeurIPS 2022), Base variant: the FraudLens approach on it.

BAF is generated from a real bank's account-opening data by a privacy-preserving
tabular generator: 1,000,000 applications over 8 months, 1.1% fraudulent. It is not
payments data. The closest FraudLens task is the mule-wallet model: spotting a
wallet that was opened to receive scam money. What maps onto FraudLens ideas:

- velocity: applications in the last 6 hours, 24 hours and 4 weeks
  (`velocity_6h`, `velocity_24h`, `velocity_4w`), applications from the same
  postcode and branch (`zip_count_4w`, `bank_branch_count_8w`);
- shared devices and identities (FraudLens' shared-handset signal):
  `device_distinct_emails_8w`, `date_of_birth_distinct_emails_4w`;
- account age and history: `bank_months_count`, `prev_address_months_count`.

Temporal split as in the benchmark (months 0-5 train, 6-7 test), with month 5
held out of training to stop boosting and fix thresholds. The benchmark's own
metric is recall at 5% false-positive rate; 0.5% and 1% are reported too, because
those are the alert rates FraudLens works at. Every remaining column is used as
given; categorical columns become integer codes fitted on the training months.

    uv run python -m fraudlens.external.baf
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import settings
from . import download
from .evaluate import (
    anomaly_score,
    bootstrap_ci,
    finite,
    fit_isolation_forest,
    fit_lightgbm,
    fit_logistic,
    judge,
    top_gain,
)

TARGET = "fraud_bool"
TRAIN_LAST_MONTH, VAL_MONTH = 4, 5  # test: months 6 and 7
FPRS = (0.005, 0.01, 0.05)
CI_FPR = 0.05  # the benchmark's metric
VELOCITY_FEATURES = ("velocity_6h", "velocity_24h", "velocity_4w")


def load(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df[TARGET] = df[TARGET].astype(int)
    return df


def encode(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Integer codes for text columns, from the training months' categories."""
    out = df.copy()
    train = df["month"] <= TRAIN_LAST_MONTH
    for col in df.columns:
        if col != TARGET and not pd.api.types.is_numeric_dtype(df[col]):
            categories = sorted(df.loc[train, col].astype(str).unique())
            codes = {value: i for i, value in enumerate(categories)}
            out[col] = df[col].astype(str).map(codes).fillna(-1).astype(int)
    features = [c for c in out.columns if c not in (TARGET, "month")]
    return out, features


def evaluate(df: pd.DataFrame, replicates: int) -> dict:
    data, features = encode(df)
    folds = {
        "train": data[data["month"] <= TRAIN_LAST_MONTH],
        "val": data[data["month"] == VAL_MONTH],
        "test": data[data["month"] > VAL_MONTH],
    }
    y = {k: v[TARGET].to_numpy() for k, v in folds.items()}
    scores: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    # A velocity-only rule: the sum of the ranks of the three velocity counts.
    def velocity_rank(part: pd.DataFrame) -> np.ndarray:
        ref = folds["train"]
        return sum(
            np.searchsorted(np.sort(ref[c].to_numpy()), part[c].to_numpy()) / len(ref)
            for c in VELOCITY_FEATURES
        )

    scores["velocity_rule"] = tuple(velocity_rank(folds[k]) for k in ("val", "test"))
    scores["credit_risk_score_only"] = tuple(
        folds[k]["credit_risk_score"].to_numpy(np.float64) for k in ("val", "test")
    )
    logistic = fit_logistic(folds["train"][features], y["train"])
    scores["logistic_regression"] = tuple(
        logistic.predict_proba(finite(folds[k][features]))[:, 1] for k in ("val", "test")
    )
    forest = fit_isolation_forest(folds["train"][features])
    scores["isolation_forest"] = tuple(
        anomaly_score(forest, folds[k][features]) for k in ("val", "test")
    )
    booster = fit_lightgbm(folds["train"][features], y["train"], folds["val"][features], y["val"])
    scores["lightgbm"] = tuple(
        booster.predict(folds[k][features].to_numpy(np.float64)) for k in ("val", "test")
    )

    results = {
        name: judge(y["val"], s_val, y["test"], s_test, FPRS)
        for name, (s_val, s_test) in scores.items()
    }
    s_val, s_test = scores["lightgbm"]
    # No time finer than a month: applications are resampled one by one.
    results["lightgbm"]["bootstrap_by_application"] = bootstrap_ci(
        y["val"], s_val, y["test"], s_test, np.arange(len(s_test)), CI_FPR, replicates
    )
    results["lightgbm"]["trees"] = booster.num_trees()
    results["lightgbm"]["top_features"] = top_gain(booster)

    return {
        "dataset": "Bank Account Fraud, Base (Jesus et al., NeurIPS 2022)",
        "source": download.SOURCES["baf"].url,
        "sha256": download.SOURCES["baf"].sha256,
        "rows_total": int(len(df)),
        "split_by_month": {"train": "0-4", "val": "5", "test": "6-7"},
        "folds": {
            k: {
                "rows": int(len(v)),
                "fraud": int(v[TARGET].sum()),
                "base_rate": round(float(v[TARGET].mean()), 5),
            }
            for k, v in folds.items()
        },
        "features": features,
        "thresholds": "fixed on val at each target FPR, applied unchanged to test",
        "models": results,
    }


def run(path: Path | None = None, out_dir: Path | None = None, replicates: int = 500) -> dict:
    t0 = time.perf_counter()
    report = evaluate(load(path or download.fetch("baf")), replicates)
    report["seconds"] = round(time.perf_counter() - t0, 1)
    out = out_dir or settings.artifacts_dir / "external"
    out.mkdir(parents=True, exist_ok=True)
    (out / "baf.json").write_text(json.dumps(report, indent=2))
    return report


def print_summary(report: dict) -> None:
    print(f"\n{report['dataset']}: folds {json.dumps(report['folds'])}")
    rows = {
        name: {
            "pr_auc": m["pr_auc"],
            "roc_auc": m["roc_auc"],
            **{f"recall@{f:g}": m[f"at_fpr_{f:g}"]["recall"] for f in FPRS},
            **{f"testfpr@{f:g}": m[f"at_fpr_{f:g}"]["fpr_on_test"] for f in FPRS},
        }
        for name, m in report["models"].items()
    }
    print(pd.DataFrame(rows).T.to_string())
    print("lightgbm", json.dumps(report["models"]["lightgbm"]["bootstrap_by_application"]))
    print("  top features:", report["models"]["lightgbm"]["top_features"])


def main() -> None:
    parser = argparse.ArgumentParser(description="FraudLens approach on BAF")
    parser.add_argument("--replicates", type=int, default=500)
    print_summary(run(replicates=parser.parse_args().replicates))


if __name__ == "__main__":
    main()
