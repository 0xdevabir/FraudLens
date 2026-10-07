"""PaySim: the FraudLens approach on an external mobile-money simulation.

PaySim (Lopez-Rojas et al., 2016) was calibrated on a month of logs from a real
mobile-money service in an African country. One `step` is one hour (743 steps).
Fraud there is account takeover: the fraudster TRANSFERs the victim's balance to
another account and CASH_OUTs. Only TRANSFER and CASH_OUT rows are ever fraud, so
those are the rows scored, as in most work on PaySim.

What maps onto FraudLens features, computed only from earlier rows (file order
within an hour is taken as time order):

- receiver-centric: earlier incoming payments to the receiving account (all time
  and last 24 hours), their value, hours since it last received, hours since it was
  first seen, earlier incoming TRANSFERs, amount against its usual incoming amount;
- velocity: the 24-hour counts above; the sender's earlier payments;
- cash-out timing: hours since the *sender* last received money, and the amount
  against what it received (FraudLens' "fast exit"). In PaySim the account that
  receives a fraudulent TRANSFER is never the one that cashes out (0 of 4,097), so
  this cannot see the fraud chain; it is kept because the mapping is the honest one.

Not mapped (no equivalent in PaySim): devices, agents, districts, wallet age,
confirmed-fraud flags, scam typologies, social engineering.

Balance fields are a known PaySim artefact: 97.8% of frauds move exactly the
sender's whole balance and no legitimate TRANSFER or CASH_OUT does. The main model
leaves balances out; a second model adds the pre-transaction balances, to show the
size of the artefact. Post-transaction balances are never used.

    uv run python -m fraudlens.external.paysim
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

SCORED_TYPES = ("TRANSFER", "CASH_OUT")
TRAIN_LAST_STEP, VAL_LAST_STEP = 300, 400  # train 1-300, val 301-400, test 401-743
FPRS = (0.005, 0.01, 0.05)
CI_FPR = 0.005  # the FraudLens headline false-alert rate is about 0.5%
WINDOW = 24  # hours

BEHAVIOUR_FEATURES = (
    "amount",
    "is_transfer",
    "hour_of_day",
    "r_prior_in",
    "r_prior_in_24h",
    "r_prior_in_value_24h",
    "r_hours_since_last_in",
    "r_hours_since_first_seen",
    "r_prior_in_transfers",
    "amount_vs_r_mean_in",
    "s_prior_out",
    "s_hours_since_received",
    "amount_vs_s_received",
)
BALANCE_FEATURES = ("oldbalanceOrg", "oldbalanceDest", "amount_vs_balance")


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        usecols=[
            "step",
            "type",
            "amount",
            "nameOrig",
            "oldbalanceOrg",
            "nameDest",
            "oldbalanceDest",
            "isFraud",
            "isFlaggedFraud",
        ],
    )
    return df.sort_values("step", kind="stable").reset_index(drop=True)


def _prefix(values: np.ndarray) -> np.ndarray:
    return np.concatenate([[0.0], np.cumsum(values, dtype=np.float64)])


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Causal features: each row sees only rows before it (earlier hour or earlier in file)."""
    n = len(df)
    step = df["step"].to_numpy(np.int64)
    amount = df["amount"].to_numpy(np.float64)
    is_transfer = (df["type"] == "TRANSFER").to_numpy()
    codes, _ = pd.factorize(pd.concat([df["nameDest"], df["nameOrig"]], ignore_index=True))
    dest, orig = codes[:n], codes[n:]
    out = df.copy()

    # --- receiver: rows sorted by (receiver, time)
    order = np.lexsort((np.arange(n), dest))
    sd, st, sa, stf = dest[order], step[order], amount[order], is_transfer[order]
    idx = np.arange(n)
    new = np.r_[True, sd[1:] != sd[:-1]]
    start = np.maximum.accumulate(np.where(new, idx, 0))
    prior = idx - start
    pa, pt = _prefix(sa), _prefix(stf.astype(np.float64))
    key = sd.astype(np.int64) * 1000 + st  # steps are below 1000
    first_in_window = np.searchsorted(key, key - WINDOW, side="right")
    prev_step = np.where(new, np.nan, np.r_[np.nan, st[:-1]])
    prior_value = pa[idx] - pa[start]
    feats = {
        "r_prior_in": prior,
        "r_prior_in_24h": idx - first_in_window,
        "r_prior_in_value_24h": pa[idx] - pa[first_in_window],
        "r_hours_since_last_in": st - prev_step,
        "r_hours_since_first_seen": st - st[start],
        "r_prior_in_transfers": pt[idx] - pt[start],
        "amount_vs_r_mean_in": np.where(
            prior > 0, sa / np.maximum(prior_value / np.maximum(prior, 1), 1e-9), np.nan
        ),
    }
    for name, values in feats.items():
        col = np.empty(n, dtype=np.float64)
        col[order] = values
        out[name] = col

    # --- sender: earlier payments sent, and the last money it received before this row
    out["s_prior_out"] = out.groupby(orig).cumcount().to_numpy(np.float64)
    receipts = pd.DataFrame({"account": dest, "t": idx, "r_step": step, "r_amount": amount})
    queries = pd.DataFrame({"account": orig, "t": idx})
    last = pd.merge_asof(
        queries, receipts, on="t", by="account", allow_exact_matches=False, direction="backward"
    )
    out["s_hours_since_received"] = step - last["r_step"].to_numpy(np.float64)
    out["amount_vs_s_received"] = amount / last["r_amount"].to_numpy(np.float64)

    out["is_transfer"] = is_transfer.astype(np.float64)
    out["hour_of_day"] = (step % 24).astype(np.float64)
    out["amount_vs_balance"] = np.where(
        df["oldbalanceOrg"] > 0, amount / df["oldbalanceOrg"].clip(lower=1e-9), np.nan
    )
    return out


def split(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    scored = df[df["type"].isin(SCORED_TYPES)]
    return {
        "train": scored[scored["step"] <= TRAIN_LAST_STEP],
        "val": scored[(scored["step"] > TRAIN_LAST_STEP) & (scored["step"] <= VAL_LAST_STEP)],
        "test": scored[scored["step"] > VAL_LAST_STEP],
    }


def evaluate(df: pd.DataFrame, replicates: int) -> dict:
    folds = split(df)
    y = {k: v["isFraud"].to_numpy() for k, v in folds.items()}
    behaviour, with_balances = list(BEHAVIOUR_FEATURES), [*BEHAVIOUR_FEATURES, *BALANCE_FEATURES]
    scores: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    # Simple baselines first.
    scores["paysim_flag_rule"] = tuple(  # PaySim's own rule: TRANSFER over 200,000
        folds[k]["isFlaggedFraud"].to_numpy(np.float64) for k in ("val", "test")
    )
    scores["amount_only"] = tuple(folds[k]["amount"].to_numpy() for k in ("val", "test"))
    logistic = fit_logistic(folds["train"][behaviour], y["train"])
    scores["logistic_regression"] = tuple(
        logistic.predict_proba(finite(folds[k][behaviour]))[:, 1] for k in ("val", "test")
    )
    forest = fit_isolation_forest(folds["train"][behaviour])
    scores["isolation_forest"] = tuple(
        anomaly_score(forest, folds[k][behaviour]) for k in ("val", "test")
    )

    # Hour of day is partly a simulator artefact too: PaySim spreads fraud evenly over
    # the day while legitimate traffic follows a daily cycle. Without it, what is left
    # is receiver-centric, velocity and amount signal.
    no_hour = [c for c in behaviour if c != "hour_of_day"]
    boosters = {}
    for name, cols in (
        ("lightgbm", behaviour),
        ("lightgbm_without_hour", no_hour),
        ("lightgbm_with_balances", with_balances),
    ):
        booster = fit_lightgbm(folds["train"][cols], y["train"], folds["val"][cols], y["val"])
        boosters[name] = booster
        scores[name] = tuple(
            booster.predict(folds[k][cols].to_numpy(np.float64)) for k in ("val", "test")
        )

    results = {
        name: judge(y["val"], s_val, y["test"], s_test, FPRS)
        for name, (s_val, s_test) in scores.items()
    }
    steps = folds["test"]["step"].to_numpy()
    for name in boosters:
        s_val, s_test = scores[name]
        results[name]["bootstrap_by_hour"] = bootstrap_ci(
            y["val"], s_val, y["test"], s_test, steps, CI_FPR, replicates
        )
        results[name]["trees"] = boosters[name].num_trees()
        results[name]["top_features"] = top_gain(boosters[name])

    return {
        "dataset": "PaySim (Lopez-Rojas et al., 2016)",
        "source": download.SOURCES["paysim"].url,
        "sha256": download.SOURCES["paysim"].sha256,
        "rows_total": int(len(df)),
        "scored_types": list(SCORED_TYPES),
        "split_by_hour": {
            "train": f"1-{TRAIN_LAST_STEP}",
            "val": f"{TRAIN_LAST_STEP + 1}-{VAL_LAST_STEP}",
            "test": f"{VAL_LAST_STEP + 1}-{int(df['step'].max())}",
        },
        "folds": {
            k: {
                "rows": int(len(v)),
                "fraud": int(v["isFraud"].sum()),
                "base_rate": round(float(v["isFraud"].mean()), 5),
            }
            for k, v in folds.items()
        },
        "features": {"behaviour": behaviour, "balances_added": list(BALANCE_FEATURES)},
        "thresholds": "fixed on val at each target FPR, applied unchanged to test",
        "models": results,
    }


def run(path: Path | None = None, out_dir: Path | None = None, replicates: int = 500) -> dict:
    t0 = time.perf_counter()
    df = add_features(load(path or download.fetch("paysim")))
    report = evaluate(df, replicates)
    report["seconds"] = round(time.perf_counter() - t0, 1)
    out = out_dir or settings.artifacts_dir / "external"
    out.mkdir(parents=True, exist_ok=True)
    (out / "paysim.json").write_text(json.dumps(report, indent=2))
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
    for name in ("lightgbm", "lightgbm_without_hour", "lightgbm_with_balances"):
        print(name, json.dumps(report["models"][name]["bootstrap_by_hour"]))
        print("  top features:", report["models"][name]["top_features"])


def main() -> None:
    parser = argparse.ArgumentParser(description="FraudLens approach on PaySim")
    parser.add_argument("--replicates", type=int, default=500)
    print_summary(run(replicates=parser.parse_args().replicates))


if __name__ == "__main__":
    main()
