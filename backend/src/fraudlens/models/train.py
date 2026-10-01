"""Train every model, evaluate once on the untouched test period, save a version.

uv run python -m fraudlens.models.train            # backend/data/full
uv run python -m fraudlens.models.train --small    # backend/data/small

Fitting uses `train`; `val_a` stops boosting; `val_b` fits fusion weights,
calibration and alert thresholds. `test` is only evaluated.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict

from ..config import settings
from ..features import FEATURES, RECIPIENT_FEATURES, FeatureEngine
from . import metrics, registry
from .agents import agent_table, score_agents
from .bundle import ANOMALY_FEATURES, ModelBundle, logit
from .data import load_frame
from .rings import find_rings

SEED = 7
# Each tier alerts at the lowest score that still meets its precision target on val_b,
# and never more than its budget (share of scored transactions, cumulative across tiers).
# Precision targets express how much customer friction a tier may cause; budgets express
# how much review capacity it may use.
TIER_POLICY = {
    "hold": {"min_precision": 0.90, "max_alert_rate": 0.010},
    "step_up": {"min_precision": 0.75, "max_alert_rate": 0.020},
    "warn": {"min_precision": 0.50, "max_alert_rate": 0.040},
}
RANK_BUDGETS = (0.0025, 0.005, 0.01, 0.02)  # for comparing scores at equal alert volume
MULE_WALLET_BUDGET = 0.005  # share of transfers allowed to raise a recipient-wallet alert
HEADLINE_BUDGET = "1.00%"
FUSION_MIN_GAIN = 0.002  # fusion must beat the single model by this PR-AUC to be served

LGB_PARAMS = {
    "objective": "binary",
    "metric": "average_precision",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_child_samples": 40,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "seed": SEED,
    "deterministic": True,
    "force_row_wise": True,
    "verbosity": -1,
}


def fit_booster(train: pd.DataFrame, val: pd.DataFrame, cols: list[str], target: str):
    dtrain = lgb.Dataset(train[cols].to_numpy(), train[target].to_numpy(), feature_name=cols)
    dval = lgb.Dataset(val[cols].to_numpy(), val[target].to_numpy(), reference=dtrain)
    booster = lgb.train(
        LGB_PARAMS,
        dtrain,
        num_boost_round=2000,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(100, verbose=False)],
    )
    # Keep only the trees up to the best validation round.
    return lgb.Booster(model_str=booster.model_to_string(num_iteration=booster.best_iteration))


def choose_thresholds(y: np.ndarray, risk: np.ndarray) -> dict[str, float]:
    """Per-tier score thresholds from TIER_POLICY, measured on validation data."""
    order = np.argsort(-risk, kind="stable")
    hits = np.cumsum(y[order])
    precision = hits / np.arange(1, len(y) + 1)
    out = {}
    for tier, rule in TIER_POLICY.items():
        cap = max(int(len(y) * rule["max_alert_rate"]), 1)
        ok = np.flatnonzero(precision[:cap] >= rule["min_precision"])
        # No score level reaches the target: alert only on the single top score.
        k = int(ok[-1]) if len(ok) else 0
        out[tier] = float(risk[order][k])
    return out


def rule_baseline(df: pd.DataFrame) -> np.ndarray:
    """A typical hand-written rule set, as the non-ML reference point."""
    flags = (
        (df["pair_prior_count"] == 0).astype(int)
        + (df["r_age_days"] < 30).astype(int)
        + (df["amount_to_balance"] >= 0.5).astype(int)
        + (df["s_new_device"] == 1).astype(int)
        + (df["amount"] >= 5000).astype(int)
    )
    return flags.to_numpy() + df["amount"].to_numpy() / 1e6  # amount only breaks ties


def top_features(booster: lgb.Booster, k: int = 15) -> list[dict]:
    gain = booster.feature_importance("gain")
    total = gain.sum() or 1.0
    order = np.argsort(-gain)[:k]
    names = booster.feature_name()
    return [{"feature": names[i], "gain_share": round(float(gain[i] / total), 4)} for i in order]


def mule_wallet_report(test: pd.DataFrame, mule_score: np.ndarray, threshold: float, wallets):
    """Wallet-level view: is the receiving wallet a mule, and how early do we know?"""
    send = test[test["type"] == "SEND_MONEY"].assign(mule_score=mule_score)
    by_wallet = send.groupby("receiver_id").agg(score=("mule_score", "max"), y=("y_mule", "max"))
    out = metrics.ranking(by_wallet["y"], by_wallet["score"])
    alerted = by_wallet["score"] >= threshold
    out["threshold"] = threshold
    out["wallets_alerted"] = int(alerted.sum())
    out["precision"] = metrics._ratio((alerted & (by_wallet["y"] == 1)).sum(), alerted.sum())
    out["recall"] = metrics._ratio((alerted & (by_wallet["y"] == 1)).sum(), by_wallet["y"].sum())

    # Timing: for each mule wallet, when did its score first cross the threshold?
    mules = send[send["receiver_id"].isin(by_wallet.index[by_wallet["y"] == 1])]
    flagged_at = wallets.set_index("wallet_id")["flagged_at"]
    victims_before, lead_hours, before_report = [], [], 0
    reported = 0
    for wallet, rows in mules.groupby("receiver_id"):
        crossed = rows["mule_score"].to_numpy() >= threshold
        flag_ts = flagged_at.get(wallet)
        if pd.notna(flag_ts):
            reported += 1
        if not crossed.any():
            continue
        first = int(np.argmax(crossed))
        victims_before.append(int(rows["y_loss"].to_numpy()[:first].sum()))
        if pd.notna(flag_ts):
            lead = (flag_ts - rows["ts"].iloc[first]).total_seconds() / 3600
            lead_hours.append(lead)
            before_report += lead > 0
    out["timing"] = {
        "mule_wallets": int(mules["receiver_id"].nunique()),
        "detected": len(victims_before),
        "detected_before_any_victim_paid": int(sum(v == 0 for v in victims_before)),
        "median_victim_transfers_before_detection": (
            float(np.median(victims_before)) if victims_before else None
        ),
        "later_reported_by_victims": reported,
        "detected_before_the_report": int(before_report),
        "median_hours_ahead_of_report": (
            round(float(np.median(lead_hours)), 1) if lead_hours else None
        ),
    }
    return out


def agent_report(engine: FeatureEngine, agents: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    scored = score_agents(agent_table(engine)).merge(agents, on=["agent_id", "district"])
    scored = scored.sort_values("risk", ascending=False).reset_index(drop=True)
    y = scored["is_risky"].to_numpy()
    k = int(y.sum())
    top = scored.head(k)
    report = {
        **metrics.ranking(y, scored["risk"].to_numpy()),
        "review_list_size": k,
        "precision_at_k": metrics._ratio(top["is_risky"].sum(), k),
        "recall_by_type_in_top_k": {
            t: metrics._ratio((top["risk_type"] == t).sum(), (scored["risk_type"] == t).sum())
            for t in ("colluding", "farming")
        },
        "top_10": [
            {
                "agent_id": r.agent_id,
                "risk": round(float(r.risk), 2),
                "truth": r.risk_type or "clean",
                "reasons": list(r.reasons),
            }
            for r in scored.head(10).itertuples()
        ],
    }
    return report, scored


def ring_report(engine: FeatureEngine, suspicious: dict[str, float], wallets: pd.DataFrame):
    rings = find_rings(engine, suspicious)
    cell = wallets.set_index("wallet_id")["cell_id"]
    cell = cell[cell >= 0]  # every wallet a scam cell created or recruited, used or not
    in_rings: set[str] = set()
    summary = []
    for ring in rings:
        members = ring["wallets"]
        cells = cell.reindex(members).dropna().astype(int)
        in_rings.update(members)
        summary.append(
            {
                "ring_id": ring["ring_id"],
                "size": ring["size"],
                "true_mule_share": round(len(cells) / len(members), 3),
                "cells": int(cells.nunique()),
                "dominant_cell_share": (
                    round(float(cells.value_counts().iloc[0] / len(members)), 3)
                    if len(cells)
                    else 0.0
                ),
                "found_by_link_only": len(ring["linked_only"]),
                "confirmed": len(ring["confirmed"]),
            }
        )
    members_total = sum(r["size"] for r in summary)
    report = {
        "rings": len(rings),
        "wallets_in_rings": members_total,
        "true_mule_share": metrics._ratio(
            sum(r["true_mule_share"] * r["size"] for r in summary), members_total
        ),
        "rings_from_a_single_cell": sum(1 for r in summary if r["cells"] == 1),
        "cell_wallets_total": int(len(cell)),
        "cell_wallets_covered": int(len(in_rings & set(cell.index))),
        "largest": summary[:8],
    }
    return report, rings


def train(data_dir: Path, models_root: Path | None = None, promote: bool = True) -> dict:
    t0 = time.perf_counter()
    frame = load_frame(data_dir)
    wallets = pd.read_parquet(data_dir / "wallets.parquet")
    agents = pd.read_parquet(data_dir / "agents.parquet")
    feature_cols, recipient_cols = list(FEATURES), list(RECIPIENT_FEATURES)

    clean = frame[~frame["ambiguous"]]
    fold = {name: clean[clean["fold"] == name] for name in ("train", "val_a", "val_b", "test")}
    send = {name: part[part["type"] == "SEND_MONEY"] for name, part in fold.items()}

    # --- models
    txn = fit_booster(fold["train"], fold["val_a"], feature_cols, "y")
    mule = fit_booster(send["train"], send["val_a"], recipient_cols, "y_mule")
    behaviour = fold["train"][list(ANOMALY_FEATURES)]
    anomaly_fill = behaviour.median().to_numpy()
    sample = behaviour.fillna(behaviour.median()).sample(
        min(len(behaviour), 200_000), random_state=SEED
    )
    anomaly = IsolationForest(n_estimators=100, random_state=SEED, n_jobs=-1).fit(sample.to_numpy())

    bundle = ModelBundle(
        version="",
        txn=txn,
        mule=mule,
        anomaly=anomaly,
        anomaly_fill=anomaly_fill,
        fusion_coef=np.zeros(4),
        fusion_intercept=0.0,
        calibration=(1.0, 0.0),
        risk_source="txn",
        manifest={},
    )

    # --- fusion: fitted on val_b, which none of the component models were fitted on
    vb, te = fold["val_b"], fold["test"]
    parts_vb = bundle.components(vb[feature_cols].to_numpy())
    parts_te = bundle.components(te[feature_cols].to_numpy())
    z_vb, y_vb = bundle.fusion_inputs(parts_vb), vb["y"].to_numpy()
    fusion = LogisticRegression(C=1.0, max_iter=2000)
    cv = StratifiedKFold(5, shuffle=True, random_state=SEED)
    fused_cv = cross_val_predict(fusion, z_vb, y_vb, cv=cv, method="predict_proba")[:, 1]
    fusion.fit(z_vb, y_vb)
    bundle.fusion_coef, bundle.fusion_intercept = fusion.coef_[0], float(fusion.intercept_[0])
    val_txn = metrics.ranking(y_vb, parts_vb["txn"])["pr_auc"]
    val_fused = metrics.ranking(y_vb, fused_cv)["pr_auc"]
    bundle.risk_source = "fused" if val_fused >= val_txn + FUSION_MIN_GAIN else "txn"

    # --- calibration and thresholds, also on val_b
    raw_vb = bundle.raw_risk(parts_vb)
    platt = LogisticRegression(C=1e6, max_iter=2000).fit(logit(raw_vb).reshape(-1, 1), y_vb)
    bundle.calibration = (float(platt.coef_[0][0]), float(platt.intercept_[0]))
    risk_vb = bundle.calibrate(raw_vb)
    thresholds = choose_thresholds(y_vb, risk_vb)
    mule_vb = parts_vb["mule"][~np.isnan(parts_vb["mule"])]
    mule_threshold = float(np.quantile(mule_vb, 1 - MULE_WALLET_BUDGET))

    # --- evaluation on the test period
    risk_te = bundle.calibrate(bundle.raw_risk(parts_te))
    y_te = te["y"].to_numpy()
    fused_te = 1 / (1 + np.exp(-(bundle.fusion_inputs(parts_te) @ fusion.coef_[0]
                                 + fusion.intercept_[0])))  # fmt: skip
    candidates = {
        "rules_baseline": rule_baseline(te),
        "anomaly_only": parts_te["anomaly"],
        "mule_model_only": np.nan_to_num(parts_te["mule"], nan=0.0),
        "transaction_model": parts_te["txn"],
        "fusion": fused_te,
    }
    budgets = RANK_BUDGETS
    ablation = {}
    for name, score in candidates.items():
        at = metrics.at_budgets(te, score, budgets)[HEADLINE_BUDGET]
        ablation[name] = {
            "pr_auc": metrics.ranking(y_te, score)["pr_auc"],
            "roc_auc": metrics.ranking(y_te, score)["roc_auc"],
            "precision": at["precision"],
            "loss_txn_recall": at["loss_txn_recall"],
            "taka_recall": at["taka_recall"],
            "taka_recall_with_exit_holds": at["taka_recall_with_exit_holds"],
            "heldout_txn_recall": at["by_typology"]
            .get("investment_scam", {})
            .get("loss_txn_recall"),
        }

    # Victim transfers against clean traffic, leaving the mules' own exits out.
    victim_view = ((te["y"] == 0) | (te["y_loss"] == 1)).to_numpy()
    operating = {
        tier: {"threshold": thr, **metrics.alert_outcomes(te, risk_te >= thr)}
        for tier, thr in thresholds.items()
    }

    engine = FeatureEngine.load(data_dir / "engine_after_test.pkl")
    send_te = send["test"]
    mule_te = parts_te["mule"][(te["type"] == "SEND_MONEY").to_numpy()]
    wallet_max = pd.Series(mule_te).groupby(send_te["receiver_id"].to_numpy()).max()
    suspicious = wallet_max[wallet_max >= mule_threshold].to_dict()
    agents_out, agent_scores = agent_report(engine, agents)
    rings_out, rings = ring_report(engine, suspicious, wallets)

    report = {
        "data": {
            "rows": {name: int(len(part)) for name, part in fold.items()},
            "fraud_chain_txns": {name: int(part["y"].sum()) for name, part in fold.items()},
            "victim_loss_txns": {name: int(part["y_loss"].sum()) for name, part in fold.items()},
            "excluded_ambiguous_rows": int(frame["ambiguous"].sum()),
        },
        "selection_on_val_b": {
            "transaction_model_pr_auc": val_txn,
            "fusion_cross_validated_pr_auc": val_fused,
            "required_gain": FUSION_MIN_GAIN,
            "served_score": bundle.risk_source,
            "fusion_weights": dict(
                zip(
                    ["txn_logit", "mule_logit", "has_mule", "anomaly"],
                    np.round(fusion.coef_[0], 4).tolist(),
                    strict=True,
                )
            ),  # fmt: skip
        },
        "test": {
            "risk_score": {
                "all_fraud_chain": metrics.ranking(y_te, risk_te),
                "victim_transfers_only": metrics.ranking(
                    te["y_loss"].to_numpy()[victim_view], risk_te[victim_view]
                ),
            },
            "ablation_at_" + HEADLINE_BUDGET: ablation,
            "at_alert_budgets": metrics.at_budgets(te, risk_te, budgets),
            "at_fixed_thresholds": operating,
            "calibration": metrics.calibration(y_te, risk_te),
            "mule_wallets": mule_wallet_report(te, mule_te, mule_threshold, wallets),
            "agents": agents_out,
            "rings": rings_out,
        },
        "top_features": {"transaction_model": top_features(txn), "mule_model": top_features(mule)},
    }

    # --- save
    root = models_root or registry.models_dir()
    bundle.version = registry.next_version(root)
    bundle.manifest = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_dir": str(data_dir),
        "data_seed": json.loads((data_dir / "meta.json").read_text())["config"]["seed"],
        "trees": {"txn": txn.num_trees(), "mule": mule.num_trees()},
        "thresholds": thresholds,
        "tier_policy": TIER_POLICY,
        "mule_wallet_threshold": mule_threshold,
        "headline": {
            "pr_auc": report["test"]["risk_score"]["all_fraud_chain"]["pr_auc"],
            **{
                k: operating["warn"][k]
                for k in ("precision", "case_recall", "taka_recall_with_exit_holds")
            },
        },
    }
    out = root / bundle.version
    bundle.save(out)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (out / "rings.json").write_text(json.dumps(rings))
    agent_scores.to_parquet(out / "agent_scores.parquet", index=False)
    scores = te[["txn_id"]].assign(
        txn=parts_te["txn"], mule=parts_te["mule"], anomaly=parts_te["anomaly"], risk=risk_te
    )
    scores.to_parquet(out / "test_scores.parquet", index=False)
    if promote:
        registry.promote(bundle.version, root)
    report["version"] = bundle.version
    report["seconds"] = round(time.perf_counter() - t0, 1)
    return report


def print_summary(report: dict) -> None:
    test = report["test"]
    print(f"\nmodel {report['version']} trained in {report['seconds']}s")
    print(
        "rows:", report["data"]["rows"], "| victim-loss txns:", report["data"]["victim_loss_txns"]
    )
    print("selection on val_b:", json.dumps(report["selection_on_val_b"]))
    print("test risk score:", json.dumps(test["risk_score"]))
    print(f"\nablation at {HEADLINE_BUDGET} alert budget (test):")
    print(pd.DataFrame(test["ablation_at_" + HEADLINE_BUDGET]).T.to_string())
    print("\nat fixed thresholds chosen on val_b (test):")
    tiers = test["at_fixed_thresholds"]
    scalar = [k for k, v in tiers["warn"].items() if not isinstance(v, dict)]
    print(pd.DataFrame({t: {k: v[k] for k in scalar} for t, v in tiers.items()}).to_string())
    print("\nby typology at the warn threshold (test):")
    print(pd.DataFrame(tiers["warn"]["by_typology"]).T.to_string())
    cal = test["calibration"]
    print("\ncalibration:", {k: cal[k] for k in ("brier", "mean_predicted", "observed_rate")},
          cal["above_0.5"])  # fmt: skip
    print("mule wallets:", json.dumps(test["mule_wallets"]))
    agents = {k: v for k, v in test["agents"].items() if k != "top_10"}
    print("agents:", json.dumps(agents))
    print("rings:", json.dumps({k: v for k, v in test["rings"].items() if k != "largest"}))
    print(pd.DataFrame(test["rings"]["largest"]).to_string())
    for name, feats in report["top_features"].items():
        print(
            f"top features, {name}:", [f"{f['feature']} {f['gain_share']:.0%}" for f in feats[:8]]
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and evaluate the FraudLens models")
    parser.add_argument("--small", action="store_true")
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--no-promote", action="store_true", help="save without making it current")
    args = parser.parse_args()
    data_dir = args.data or settings.data_dir / ("small" if args.small else "full")
    print_summary(train(data_dir, promote=not args.no_promote))


if __name__ == "__main__":
    main()
