"""Numbers for the dashboards, computed once on the held-out test period.

Three questions the console has to answer without re-scoring anything at request time:

- impact: what does moving a threshold cost and save (alerts a day, false alerts,
  scams caught, taka stopped)?
- drift: have the model's inputs and its score moved away from what it was trained on?
- fairness: does the friction fall more heavily on some groups of customers?

Everything here uses labels, so it describes the back-test, not live traffic.

    uv run python -m fraudlens.decision.insights
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import settings
from ..features import FEATURES
from ..models import registry
from ..models.data import load_frame
from ..models.metrics import alert_outcomes
from ..simulator.world import DIVISIONS
from .evaluate import add_context, decide_batch
from .policy import ALERT_TIERS, DEFAULT_POLICY, RANK, TIERS, load_policy

INSIGHTS_FILE = "insights.json"
DRIFT_REFERENCE_FILE = "drift_reference.json"
SWEEP_POINTS = 40
SWEEP_RATES = (0.0005, 0.05)  # share of transactions alerted, lowest to highest
PSI_BINS = 10
PSI_WATCH, PSI_SHIFTED = 0.1, 0.25  # the usual rule of thumb for the index
WEEK_DAYS = 7
MIN_GROUP_ROWS = 200  # smaller groups are reported but marked as too small to compare
GROUP_COLUMNS = ("segment", "area_type", "channel")
# (upper limit, label), lowest first; the last band has no upper limit.
AGE_BANDS = (
    (30.0, "under 30 days"),
    (180.0, "30-179 days"),
    (365.0, "180-364 days"),
    (np.inf, "1 year or more"),
)
BALANCE_BANDS = (
    (1_000.0, "under 1,000 BDT"),
    (10_000.0, "1,000-9,999 BDT"),
    (np.inf, "10,000 BDT or more"),
)
_SWEEP_KEYS = (
    "alerts_per_day",
    "false_alerts_per_day",
    "alert_rate",
    "precision",
    "case_recall",
    "loss_txn_recall",
    "taka_recall",
    "taka_recall_with_exit_holds",
    "taka_stopped",
)


def psi(reference: np.ndarray, current: np.ndarray, bins: int = PSI_BINS) -> float:
    """Population stability index of `current` against `reference`.

    Bin edges are the reference's quantiles plus its smallest and largest value. A
    value exactly on an edge gets a bin of its own, so a feature that only ever takes
    a few values (a flag, a count) is compared value by value. Missing values get a
    bin too, so a feature that starts arriving empty shows up as drift.
    """
    return psi_against(reference_bins(reference, bins), current)


def _shares(edges: np.ndarray, values: np.ndarray) -> np.ndarray:
    missing = np.isnan(values)
    present = values[~missing]
    # 2k for a value between edges k-1 and k, 2k+1 for a value equal to edge k.
    index = np.searchsorted(edges, present, "left") + np.searchsorted(edges, present, "right")
    counts = np.bincount(index, minlength=2 * len(edges) + 1).astype(np.float64)
    counts = np.append(counts, missing.sum())
    return np.clip(counts / len(values), 1e-4, None)


def reference_bins(reference: np.ndarray, bins: int = PSI_BINS) -> dict:
    """The reference side of `psi`, small enough to keep: bin edges and the share in each.

    Saved next to the model so that live traffic can be compared with the training
    period without the training data being at hand.
    """
    reference = np.asarray(reference, dtype=np.float64)
    if len(reference) == 0:
        raise ValueError("psi needs at least one value on each side")
    known = reference[~np.isnan(reference)]
    edges = np.unique(np.quantile(known, np.linspace(0, 1, bins + 1))) if len(known) else []
    edges = np.asarray(edges, dtype=np.float64)
    return {"edges": edges.tolist(), "shares": _shares(edges, reference).tolist()}


def psi_against(reference: dict, current: np.ndarray) -> float:
    """`psi` of `current` against a reference kept by `reference_bins`."""
    current = np.asarray(current, dtype=np.float64)
    if len(current) == 0:
        raise ValueError("psi needs at least one value on each side")
    p = np.asarray(reference["shares"], dtype=np.float64)
    q = _shares(np.asarray(reference["edges"], dtype=np.float64), current)
    return round(float(np.sum((q - p) * np.log(q / p))), 4)


def drift_status(value: float) -> str:
    if value >= PSI_SHIFTED:
        return "shifted"
    return "watch" if value >= PSI_WATCH else "stable"


def impact_sweep(test: pd.DataFrame, risk: np.ndarray) -> list[dict]:
    """Outcomes of alerting everything at or above a threshold, across a range of thresholds."""
    legit_sender = test["sender_id"].to_numpy()
    legit = test["y"].to_numpy() == 0
    points = []
    for rate in np.geomspace(*SWEEP_RATES, SWEEP_POINTS):
        threshold = float(np.quantile(risk, 1 - rate))
        flagged = risk >= threshold
        outcome = alert_outcomes(test, flagged)
        points.append(
            {
                "threshold": threshold,
                **{key: outcome[key] for key in _SWEEP_KEYS},
                # Honest customers who would see a warning, a delay or a hold.
                "legit_customers_alerted": int(len(np.unique(legit_sender[flagged & legit]))),
            }
        )
    return points


def daily(test: pd.DataFrame, tiers: np.ndarray) -> list[dict]:
    rank = np.array([RANK[t] for t in tiers])
    frame = pd.DataFrame(
        {
            "day": test["day"].to_numpy(),
            "date": test["ts"].dt.strftime("%Y-%m-%d").to_numpy(),
            "fraud": test["y"].to_numpy() == 1,
            "alert": rank > 0,
            "victim_taka": np.where(test["y_loss"] == 1, test["amount"], 0.0),
            **{tier: rank == RANK[tier] for tier in TIERS},
        }
    )
    frame["true_alerts"] = frame["alert"] & frame["fraud"]
    frame["false_alerts"] = frame["alert"] & ~frame["fraud"]
    frame["taka_stopped"] = np.where(frame["alert"], frame["victim_taka"], 0.0)
    frame["taka_held"] = np.where(frame["hold"], frame["victim_taka"], 0.0)
    frame["scored"] = 1
    columns = ["scored", *TIERS, "fraud", "true_alerts", "false_alerts"]
    money = ["victim_taka", "taka_stopped", "taka_held"]
    grouped = frame.groupby("day").agg(
        date=("date", "first"),
        **{c: (c, "sum") for c in columns},
        **{c: (c, "sum") for c in money},
    )
    rows = []
    for day, row in grouped.iterrows():
        rows.append(
            {
                "day": int(day),
                "date": row["date"],
                **{c: int(row[c]) for c in columns},
                **{c: round(float(row[c])) for c in money},
            }
        )
    return rows


def _periods(frame: pd.DataFrame) -> list[tuple[str, np.ndarray]]:
    """Validation folds, then the test period a week at a time."""
    periods = [(fold, (frame["fold"] == fold).to_numpy()) for fold in ("val_a", "val_b")]
    test = (frame["fold"] == "test").to_numpy()
    days = frame["day"].to_numpy()
    start, end = int(days[test].min()), int(days[test].max())
    for first in range(start, end + 1, WEEK_DAYS):
        last = min(first + WEEK_DAYS - 1, end)
        periods.append((f"test days {first}-{last}", test & (days >= first) & (days <= last)))
    periods.append(("test", test))
    return periods


def drift(frame: pd.DataFrame, risk: np.ndarray, thresholds: dict[str, float]) -> dict:
    """Feature drift against the training period; score drift against val_b.

    The score's reference is val_b, the period its thresholds were fitted on: on the
    training rows the model has seen the answers, so its scores there are not typical.
    """
    periods = _periods(frame)
    train = (frame["fold"] == "train").to_numpy()
    features = []
    for name in FEATURES:
        values = frame[name].to_numpy(dtype=np.float64)
        by_period = {label: psi(values[train], values[mask]) for label, mask in periods}
        features.append(
            {
                "feature": name,
                "psi": by_period,
                "status": drift_status(by_period["test"]),
                "missing_train": round(float(np.isnan(values[train]).mean()), 4),
                "missing_test": round(float(np.isnan(values[periods[-1][1]]).mean()), 4),
            }
        )
    features.sort(key=lambda f: f["psi"]["test"], reverse=True)

    reference = (frame["fold"] == "val_b").to_numpy()
    score = []
    for label, mask in periods:
        if label in ("val_a", "val_b"):
            continue
        score.append(
            {
                "period": label,
                "psi": psi(risk[reference], risk[mask]),
                "rows": int(mask.sum()),
                "alert_rate": round(float((risk[mask] >= thresholds["warn"]).mean()), 5),
                "hold_rate": round(float((risk[mask] >= thresholds["hold"]).mean()), 5),
                "fraud_rate": round(float(frame["y"].to_numpy()[mask].mean()), 5),
            }
        )
    counts = {s: sum(f["status"] == s for f in features) for s in ("stable", "watch", "shifted")}
    return {
        "reference": {"features": "train", "score": "val_b"},
        "limits": {"watch": PSI_WATCH, "shifted": PSI_SHIFTED},
        "periods": [label for label, _ in periods],
        "feature_status": counts,
        "features": features,
        "score": score,
        "validation_alert_rate": round(float((risk[reference] >= thresholds["warn"]).mean()), 5),
    }


def drift_reference(frame: pd.DataFrame, risk: np.ndarray, thresholds: dict[str, float]) -> dict:
    """What live traffic is compared with: the same references as `drift`, as bins."""
    train = (frame["fold"] == "train").to_numpy()
    val_b = (frame["fold"] == "val_b").to_numpy()
    return {
        "reference": {"features": "train", "score": "val_b"},
        "rows": {"features": int(train.sum()), "score": int(val_b.sum())},
        "features": {
            name: reference_bins(frame[name].to_numpy(dtype=np.float64)[train]) for name in FEATURES
        },
        "score": reference_bins(risk[val_b]),
        "validation_alert_rate": round(float((risk[val_b] >= thresholds["warn"]).mean()), 5),
        "validation_hold_rate": round(float((risk[val_b] >= thresholds["hold"]).mean()), 5),
    }


def _group_table(
    groups: np.ndarray, test: pd.DataFrame, rank: np.ndarray, order: tuple[str, ...] | None = None
) -> list[dict]:
    legit = test["y"].to_numpy() == 0
    victim = test["y_loss"].to_numpy() == 1
    alert, hold = rank > 0, rank == RANK["hold"]
    overall = float(alert[legit].mean())
    rows = []
    present = set(pd.unique(groups))
    # Bands (age, balance) keep their natural order; names are sorted.
    listed = [g for g in order if g in present] if order else []
    for group in [*listed, *sorted(present - set(listed))]:
        mask = groups == group
        n_legit, n_victim = int((mask & legit).sum()), int((mask & victim).sum())
        false_rate = float(alert[mask & legit].mean()) if n_legit else None
        rows.append(
            {
                "group": str(group),
                "transactions": int(mask.sum()),
                "legitimate": n_legit,
                "false_alert_rate": _round(false_rate),
                "false_hold_rate": _round(float(hold[mask & legit].mean()) if n_legit else None),
                "false_alerts": int((mask & legit & alert).sum()),
                # Above 1: this group's honest customers are interrupted more than average.
                "ratio_to_overall": _round(
                    false_rate / overall if false_rate is not None and overall else None, 2
                ),
                "victim_transfers": n_victim,
                "victim_transfers_alerted": _round(
                    float(alert[mask & victim].mean()) if n_victim else None
                ),
                "too_small": n_legit < MIN_GROUP_ROWS,
            }
        )
    return rows


def _round(value: float | None, digits: int = 5) -> float | None:
    return None if value is None else round(value, digits)


def _labels(bands: tuple[tuple[float, str], ...]) -> tuple[str, ...]:
    return tuple(label for _, label in bands)


def _band(values: np.ndarray, bands: tuple[tuple[float, str], ...]) -> np.ndarray:
    """The band each value falls in; `bands` is (upper limit, label), lowest first."""
    limits = np.array([limit for limit, _ in bands[:-1]])
    labels = np.array([*_labels(bands), "unknown"])
    index = np.searchsorted(limits, values, side="right")
    return labels[np.where(np.isnan(values), len(bands), index)]


def fairness(test: pd.DataFrame, tiers: np.ndarray, wallets: pd.DataFrame) -> dict:
    """False-alert rates by customer group, for the sender and for the receiving wallet.

    The sender is the customer who meets the warning or the delay; the receiver is the
    wallet that comes under suspicion. Groups are the synthetic population's attributes.
    """
    rank = np.array([RANK[t] for t in tiers])
    attributes = wallets.set_index("wallet_id")[[*GROUP_COLUMNS, "district"]]
    legit = test["y"].to_numpy() == 0
    out = {
        "overall": {
            "false_alert_rate": round(float((rank > 0)[legit].mean()), 5),
            "false_hold_rate": round(float((rank == RANK["hold"])[legit].mean()), 5),
        },
        "min_group_rows": MIN_GROUP_ROWS,
        "sender": {},
        "receiver": {},
    }
    sends = (test["type"] == "SEND_MONEY").to_numpy()
    for column in GROUP_COLUMNS:
        sender = test["sender_id"].map(attributes[column]).fillna("unknown").to_numpy(str)
        out["sender"][column] = _group_table(sender, test, rank)
        receiver = test.loc[sends, "receiver_id"].map(attributes[column]).fillna("unknown")
        out["receiver"][column] = _group_table(
            receiver.to_numpy(str), test[sends].reset_index(drop=True), rank[sends]
        )

    # Region, account age and balance: the three a regulator asks about first.
    sent, sent_rank = test[sends].reset_index(drop=True), rank[sends]
    for side, rows, ranks, party, age in (
        ("sender", test, rank, "sender_id", "s_age_days"),
        ("receiver", sent, sent_rank, "receiver_id", "r_age_days"),
    ):
        region = rows[party].map(attributes["district"]).map(DIVISIONS).fillna("unknown")
        out[side]["region"] = _group_table(region.to_numpy(str), rows, ranks)
        out[side]["account_age"] = _group_table(
            _band(rows[age].to_numpy(dtype=np.float64), AGE_BANDS), rows, ranks, _labels(AGE_BANDS)
        )
    # What the sender held before paying. The receiver's balance is not an input.
    before = (test["balance_after"] + test["amount"]).to_numpy(dtype=np.float64)
    out["sender"]["balance_tier"] = _group_table(
        _band(before, BALANCE_BANDS), test, rank, _labels(BALANCE_BANDS)
    )
    for side in ("sender", "receiver"):
        ratios = [
            row["ratio_to_overall"]
            for table in out[side].values()
            for row in table
            if not row["too_small"] and row["ratio_to_overall"] is not None
        ]
        out[side + "_largest_ratio"] = max(ratios) if ratios else None
    return out


def run(
    data_dir: Path,
    policy_version: str = DEFAULT_POLICY,
    model: str | None = None,
    models_root: Path | None = None,
) -> dict:
    policy = load_policy(policy_version)
    bundle = registry.load(model, models_root)
    model_dir = (models_root or registry.models_dir()) / bundle.version
    thresholds = policy.resolve_thresholds(bundle.manifest)

    frame = load_frame(data_dir)
    frame = frame[~frame["ambiguous"]].reset_index(drop=True)
    frame = add_context(frame, pd.read_parquet(data_dir / "wallet_flags.parquet"))
    risk = bundle.score(frame[list(FEATURES)].to_numpy(dtype=np.float64))["risk"]

    is_test = (frame["fold"] == "test").to_numpy()
    test = frame[is_test].reset_index(drop=True)
    outcomes, test_risk = decide_batch(policy, test, bundle)
    tiers = np.array([o.tier for o in outcomes])

    report = {
        "policy_version": policy.version,
        "model_version": bundle.version,
        "thresholds": thresholds,
        "rows": len(test),
        "days": int(test["day"].nunique()),
        "at_thresholds": {
            tier: {
                key: value
                for key, value in alert_outcomes(test, test_risk >= thresholds[tier]).items()
                if key != "by_typology"
            }
            for tier in ALERT_TIERS
        },
        "impact": impact_sweep(test, test_risk),
        "daily": daily(test, tiers),
        "drift": drift(frame, risk, thresholds),
        "fairness": fairness(test, tiers, pd.read_parquet(data_dir / "wallets.parquet")),
    }
    (model_dir / INSIGHTS_FILE).write_text(json.dumps(report, indent=2))
    reference = {"model_version": bundle.version, **drift_reference(frame, risk, thresholds)}
    (model_dir / DRIFT_REFERENCE_FILE).write_text(json.dumps(reference))
    return report


def print_summary(report: dict) -> None:
    print(f"policy {report['policy_version']} on model {report['model_version']}, test period")
    sweep = pd.DataFrame(report["impact"])[
        ["threshold", "alerts_per_day", "precision", "case_recall", "taka_recall"]
    ]
    print(sweep.iloc[:: max(len(sweep) // 8, 1)].to_string(index=False))
    drift_report = report["drift"]
    print("\nfeature drift:", json.dumps(drift_report["feature_status"]))
    for feature in drift_report["features"][:6]:
        print(f"  {feature['feature']}: {feature['psi']['test']} ({feature['status']})")
    print("score drift:", json.dumps(drift_report["score"]))
    fair = report["fairness"]
    print("\nfalse-alert rate overall:", fair["overall"]["false_alert_rate"])
    for side in ("sender", "receiver"):
        print(f"  largest {side} group ratio: {fair[side + '_largest_ratio']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Impact, drift and fairness on the test period")
    parser.add_argument("--small", action="store_true")
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--policy", default=DEFAULT_POLICY)
    parser.add_argument("--model", default=None, help="model version (default: current)")
    args = parser.parse_args()
    data_dir = args.data or settings.data_dir / ("small" if args.small else "full")
    print_summary(run(data_dir, args.policy, args.model))


if __name__ == "__main__":
    main()
