"""Label realism: what training only on reported scams costs, and what wins it back.

uv run python -m fraudlens.models.label_realism                # backend/data/full, 5 seeds
uv run python -m fraudlens.models.label_realism --seeds 7 11   # fewer seeds

The served models are trained on the simulator's ground truth. A real wallet only
learns about the scams its victims report. This experiment trains the transaction
model the way `train.fit_booster` does (same parameters, train fold, early stopping
on val_a) under several label regimes and scores every one on the same ground-truth
test period, at the false-positive rate the served model runs at.

Regimes:

- ground_truth: every fraud-chain transaction labelled (what the served model had).
- reported_uniform: a case is labelled only if its victim reported it, with the
  simulator's own process (50%, independent of anything).
- reported: the same 50% on average, but larger losses, urban victims, long-held
  accounts and unauthorised takeovers report more, shame-heavy scams less.
- reported_pu: reported, then positive-unlabelled learning (Elkan & Noto 2008):
  each unlabelled transaction counts as fraud with the weight its propensity implies.
- reported_propagated: reported, then soft labels through the mule network: other
  money into or out of a wallet a reported scam paid, near the reported transfers.
- reported_feedback: reported, then the analyst-verdict loop: a model trained on
  earlier reports alerts on the last training weeks, analysts label the alerts, and
  the verdicts join the training data through `train.add_feedback`, the path
  `mlops/feedback.py` feeds in production.
- reported_propagated_feedback: both.

A reported case labels every transaction tagged with it (the victim's transfers and
the mule's forwards and cash-outs), as an investigation following the money would.
Reports arrive after a delay; only those in by the start of the test period, when
the model is trained, count. Unreported fraud is labelled clean, as it would be.
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
from sklearn.model_selection import StratifiedKFold

from ..config import settings
from ..features import FEATURES
from . import metrics, registry
from .data import load_frame
from .train import FEEDBACK_COLUMNS, LGB_PARAMS, add_feedback

REPORT_FILE = "label_realism.json"
SEEDS = (7, 11, 13, 17, 19)
DAY = pd.Timedelta(days=1)

# Report propensity, as log-odds terms. Synthetic assumptions, not measurements: the
# intercept is solved so that the average matches the simulator's `report_rate`.
# The simulator has no person age, so account tenure stands in for an older customer.
REPORT_LOGIT = {"log_loss": 0.6, "urban": 0.5, "tenure_1y": 0.4}
TYPOLOGY_LOGIT = {
    "account_takeover": 1.0,  # unauthorised: the victim disputes it with the provider
    "impersonation": 0.0,
    "wrong_send": 0.0,
    "lottery_fee": -0.5,  # victims who paid a "fee" are embarrassed to report
    "investment_scam": -0.8,  # and those who believed in the returns report late, if at all
}
# Report delay, as in the simulator: lognormal around a median, plus a few hours.
DELAY_MEDIAN_DAYS = {"investment_scam": 5.0}
DELAY_MEDIAN_DEFAULT_DAYS = 1.5

PROPAGATION_WEIGHT = 0.7  # fraud weight of a transfer that touched a reported mule
PROPAGATION_WINDOW = 3 * DAY  # around the wallet's first and last reported transfer
PU_FOLDS = 3
REVIEW_BUDGET = 0.01  # analysts review the top 1% of the review window, the headline budget
SERVED_TIER = "warn"  # the operating point compared at: its false-positive rate on test
REGIMES = (
    "ground_truth",
    "reported_uniform",
    "reported",
    "reported_pu",
    "reported_pu_known_rate",
    "reported_propagated",
    "reported_feedback",
    "reported_propagated_feedback",
)


# ---------------------------------------------------------------- reporting


def case_table(data_dir: Path) -> pd.DataFrame:
    """Cases with what their reporting depends on: loss, victim's area and tenure."""
    cases = pd.read_parquet(data_dir / "cases.parquet")
    wallets = pd.read_parquet(data_dir / "wallets.parquet").set_index("wallet_id")
    victim = wallets.reindex(cases["victim_id"])
    cases["urban"] = (victim["area_type"].to_numpy() == "urban").astype(float)
    tenure = (cases["started_at"] - victim["created_at"].to_numpy()) / DAY
    cases["tenure_days"] = tenure.to_numpy()
    return cases


def report_probability(cases: pd.DataFrame, rate: float, biased: bool = True) -> np.ndarray:
    """Probability that a case is reported, averaging `rate` over the cases."""
    if not biased:
        return np.full(len(cases), rate)
    z = (
        REPORT_LOGIT["log_loss"] * np.log(cases["loss"].clip(lower=1.0) / cases["loss"].median())
        + REPORT_LOGIT["urban"] * cases["urban"].to_numpy()
        + REPORT_LOGIT["tenure_1y"] * (cases["tenure_days"].to_numpy() >= 365)
        + cases["typology"].map(TYPOLOGY_LOGIT).fillna(0.0).to_numpy()
    )
    lo, hi = -20.0, 20.0
    for _ in range(100):  # mean of a logistic is monotone in the intercept
        mid = (lo + hi) / 2
        if (1 / (1 + np.exp(-(z + mid)))).mean() < rate:
            lo = mid
        else:
            hi = mid
    return 1 / (1 + np.exp(-(z + (lo + hi) / 2)))


def draw_reports(cases: pd.DataFrame, p: np.ndarray, rng: np.random.Generator) -> pd.Series:
    """When each case's report arrives (NaT if never), indexed by case id."""
    reported = rng.random(len(cases)) < p
    median = cases["typology"].map(DELAY_MEDIAN_DAYS).fillna(DELAY_MEDIAN_DEFAULT_DAYS)
    days = rng.lognormal(np.log(median.to_numpy()), 0.8) + rng.uniform(1 / 24, 0.5, len(cases))
    at = cases["started_at"] + pd.to_timedelta(days, unit="D")
    return pd.Series(at.where(reported).to_numpy(), index=cases["case_id"].to_numpy())


def observed_labels(frame: pd.DataFrame, report_at: pd.Series, cutoff: pd.Timestamp) -> np.ndarray:
    """A fraud-chain row is labelled fraud only if its case was reported before `cutoff`."""
    known = report_at[report_at <= cutoff].index
    return (frame["y"].to_numpy() == 1) & frame["case_id"].isin(known).to_numpy()


# ---------------------------------------------------------------- recovery


def propagate(frame: pd.DataFrame, y_obs: np.ndarray, window: pd.Timedelta = PROPAGATION_WINDOW):
    """Rows labelled clean that moved money into or out of a reported mule wallet.

    A wallet that received a reported scam transfer is a known mule from then on.
    Other transfers it received, and the money it sent on, within `window` of its
    reported transfers inherit a soft fraud label. Recruited mules are ordinary
    customers too, so some of what this labels is legitimate.
    """
    paid = frame[y_obs & (frame["type"] == "SEND_MONEY").to_numpy()]
    span = paid.groupby("receiver_id")["ts"].agg(["min", "max"])
    lo, hi = span["min"] - window, span["max"] + window
    hit = np.zeros(len(frame), dtype=bool)
    for side in ("receiver_id", "sender_id"):
        start = frame[side].map(lo).to_numpy()
        end = frame[side].map(hi).to_numpy()
        ts = frame["ts"].to_numpy()
        hit |= (ts >= start) & (ts <= end)
    return hit & ~y_obs


def labelling_propensity(
    x: np.ndarray, s: np.ndarray, x_val: np.ndarray, s_val: np.ndarray, seed: int
) -> np.ndarray:
    """g(x) = P(labelled | x), fitted out-of-fold so no row is scored by a model that saw it."""
    g = np.zeros(len(s))
    folds = StratifiedKFold(PU_FOLDS, shuffle=True, random_state=seed)
    for fit_idx, held_idx in folds.split(x, s):
        booster = fit_weighted(x[fit_idx], s[fit_idx], None, x_val, s_val, None, seed)
        g[held_idx] = booster.predict(x[held_idx])
    return g


def elkan_noto(g: np.ndarray, s: np.ndarray, c: float | None = None) -> tuple[np.ndarray, float]:
    """Elkan & Noto (2008): how likely each unlabelled row is fraud, and the label rate c.

    c = P(labelled | fraud) is the mean of g over labelled rows unless it is given. An
    unlabelled row is fraud with weight (1 - c)/c * g/(1 - g), clipped to [0, 1];
    labelled rows get NaN (they keep their hard label). This assumes labels are
    missing at random among frauds, which biased reporting breaks.
    """
    if c is None:
        c = float(g[s == 1].mean())
    g = np.clip(g, 1e-6, 1 - 1e-6)
    w = np.clip((1 - c) / c * g / (1 - g), 0.0, 1.0)
    return np.where(s == 1, np.nan, w), c


def with_soft_labels(x: np.ndarray, y: np.ndarray, soft: np.ndarray | None):
    """Rows whose `soft` is set appear twice: as fraud with that weight, as clean with the rest."""
    if soft is None:
        return x, y, None
    m = ~np.isnan(soft) & (soft > 1e-4)
    keep = ~m
    w = soft[m]
    return (
        np.vstack([x[keep], x[m], x[m]]),
        np.concatenate([y[keep], np.ones(m.sum()), np.zeros(m.sum())]),
        np.concatenate([np.ones(keep.sum()), w, 1 - w]),
    )


def arrays(part: pd.DataFrame):
    """Features, labels and weights of a labelled frame, `soft` expanded by `with_soft_labels`."""
    soft = part["soft"].to_numpy(dtype=float) if "soft" in part else None
    if soft is not None and np.isnan(soft).all():
        soft = None
    return with_soft_labels(part[list(FEATURES)].to_numpy(), part["y"].to_numpy(float), soft)


def fit_weighted(x, y, w, x_val, y_val, w_val, seed: int) -> lgb.Booster:
    """`train.fit_booster`, with sample weights and a seed."""
    params = {**LGB_PARAMS, "seed": seed}
    dtrain = lgb.Dataset(x, y, weight=w, feature_name=list(FEATURES))
    dval = lgb.Dataset(x_val, y_val, weight=w_val, reference=dtrain)
    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=2000,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(100, verbose=False)],
    )
    return lgb.Booster(model_str=booster.model_to_string(num_iteration=booster.best_iteration))


# ---------------------------------------------------------------- feedback loop


def review_window(cfg: dict) -> tuple[int, int, int]:
    """Days (fit until, review from, review until) for the model whose alerts get reviewed."""
    train_end = cfg["train_end_day"]
    es = (train_end + cfg["val_end_day"]) // 2 - train_end  # as long as val_a
    review = max(1, round(0.2 * train_end))
    return train_end - review - es, train_end - review, train_end


def verdicts(
    clean: pd.DataFrame,
    report_at: pd.Series,
    cfg: dict,
    start: pd.Timestamp,
    cutoff: pd.Timestamp,
    seed: int,
) -> tuple[pd.DataFrame, dict]:
    """Analysts' verdicts on the alerts of a model trained on earlier reports.

    The model is fitted on reports known when the review window opens, alerts on the
    top REVIEW_BUDGET of the window, and every alert is closed with the true answer
    (the same simulated, always-right reviewer as `make review`).
    """
    fit_until, review_from, review_until = review_window(cfg)
    opens = start + review_from * DAY
    y_then = observed_labels(clean, report_at, opens)
    day = clean["day"].to_numpy()
    fit, stop = day < fit_until, (day >= fit_until) & (day < review_from)
    x = clean[list(FEATURES)].to_numpy()
    booster = fit_weighted(x[fit], y_then[fit], None, x[stop], y_then[stop], None, seed)
    window = clean[(day >= review_from) & (day < review_until)]
    score = booster.predict(window[list(FEATURES)].to_numpy())
    alerted = window[score >= np.quantile(score, 1 - REVIEW_BUDGET)].copy()
    alerted["y_mule"] = (alerted["y"] * (alerted["type"] == "SEND_MONEY")).astype("int8")
    rows = alerted[list(FEEDBACK_COLUMNS)]
    truth = alerted["y"].to_numpy() == 1
    unreported = int((truth & ~observed_labels(alerted, report_at, cutoff)).sum())
    return rows, {
        "window_days": [review_from, review_until],
        "reviewed": int(len(rows)),
        "fraud": int(truth.sum()),
        "fraud_never_reported_by_training": unreported,
    }


# ---------------------------------------------------------------- evaluation


def operating_fpr(test: pd.DataFrame, models_root: Path) -> dict:
    """The served version's false-positive rate on test at its SERVED_TIER threshold."""
    version = registry.current_version(models_root)
    if version is None:
        raise SystemExit("no served model: run `make train` first")
    directory = models_root / version
    threshold = json.loads((directory / "manifest.json").read_text())["thresholds"][SERVED_TIER]
    scores = pd.read_parquet(directory / "test_scores.parquet", columns=["txn_id", "risk"])
    risk = test[["txn_id", "y"]].merge(scores, on="txn_id", how="inner")
    clean = risk[risk["y"] == 0]
    return {
        "version": version,
        "tier": SERVED_TIER,
        "threshold": threshold,
        "fpr": float((clean["risk"] >= threshold).mean()),
    }


def evaluate(test: pd.DataFrame, score: np.ndarray, fpr: float) -> dict:
    """Ground-truth outcomes when as many clean rows are alerted as the served model alerts."""
    y = test["y"].to_numpy()
    threshold = float(np.quantile(score[y == 0], 1 - fpr))
    flagged = score > threshold
    out = metrics.alert_outcomes(test, flagged)
    return {
        "pr_auc": metrics.ranking(y, score)["pr_auc"],
        "fpr": round(float(flagged[y == 0].mean()), 5),
        "precision": out["precision"],
        "loss_txn_recall": out["loss_txn_recall"],
        "case_recall": out["case_recall"],
        "taka_recall": out["taka_recall"],
        "by_typology": {
            t: {"loss_txn_recall": v["loss_txn_recall"], "case_recall": v["case_recall"]}
            for t, v in out["by_typology"].items()
        },
    }


def label_summary(part: pd.DataFrame, y_obs: np.ndarray) -> dict:
    """How much of the true fraud the training labels contain, by typology."""
    y = part["y"].to_numpy() == 1
    fraud = part[y].assign(labelled=y_obs[y])
    cases = fraud.groupby("case_id").agg(
        typology=("typology", "first"), labelled=("labelled", "max")
    )
    return {
        "fraud_rows": int(y.sum()),
        "fraud_rows_labelled": int(y_obs[y].sum()),
        "clean_rows_labelled_fraud": int((y_obs & ~y).sum()),
        "case_label_rate": metrics._ratio(cases["labelled"].sum(), len(cases)),
        "case_label_rate_by_typology": {
            str(t): metrics._ratio(g["labelled"].sum(), len(g))
            for t, g in cases.groupby("typology")
        },
    }


# ---------------------------------------------------------------- experiment


def run_seed(clean: pd.DataFrame, cases: pd.DataFrame, cfg: dict, fpr: float, seed: int) -> dict:
    start = pd.Timestamp(cfg["start"])
    cutoff = start + cfg["val_end_day"] * DAY  # the model is trained when the test period opens
    rng = np.random.default_rng(seed)
    rate = cfg["report_rate"]
    biased_at = draw_reports(cases, report_probability(cases, rate), rng)
    uniform_at = draw_reports(cases, report_probability(cases, rate, biased=False), rng)

    fold = clean["fold"].to_numpy()
    tr, va, te = fold == "train", fold == "val_a", fold == "test"
    x = clean[list(FEATURES)].to_numpy()
    test = clean[te]
    y_true = clean["y"].to_numpy()
    y_rep = observed_labels(clean, biased_at, cutoff)
    y_uni = observed_labels(clean, uniform_at, cutoff)
    spread = propagate(clean, y_rep)
    soft_prop = np.where(spread, PROPAGATION_WEIGHT, np.nan)

    out: dict = {"labels": {}, "regimes": {}, "diagnostics": {}}
    out["labels"] = {
        name: label_summary(clean[tr | va], y[tr | va])
        for name, y in (("ground_truth", y_true == 1), ("reported_uniform", y_uni),
                        ("reported", y_rep))
    }  # fmt: skip
    in_fit = tr | va
    out["diagnostics"]["propagation"] = {
        "rows_given_soft_label": int(spread[in_fit].sum()),
        "of_which_true_fraud": metrics._ratio(
            (spread & (y_true == 1))[in_fit].sum(), spread[in_fit].sum()
        ),  # fmt: skip
    }

    def labelled(y: np.ndarray, soft: np.ndarray | None = None) -> pd.DataFrame:
        return clean.assign(y=y.astype("int8"), soft=np.nan if soft is None else soft)

    def fit(part: pd.DataFrame) -> np.ndarray:
        train, val = part[part["fold"] == "train"], part[part["fold"] == "val_a"]
        booster = fit_weighted(*arrays(train), *arrays(val), seed)
        return booster.predict(x[te])

    def with_verdicts(part: pd.DataFrame) -> pd.DataFrame:
        # Verdicts replace the labels of the transactions they cover, exactly as
        # `train.add_feedback` does when `make retrain` runs.
        sub = {"train": part[part["fold"] == "train"], "test": test}
        add_feedback(sub, rows)
        sub["train"] = sub["train"].assign(fold="train")
        return pd.concat([sub["train"], part[part["fold"] == "val_a"]], ignore_index=True)

    out["regimes"]["ground_truth"] = evaluate(test, fit(labelled(y_true)), fpr)
    out["regimes"]["reported_uniform"] = evaluate(test, fit(labelled(y_uni)), fpr)
    out["regimes"]["reported"] = evaluate(test, fit(labelled(y_rep)), fpr)

    s_tr = y_rep[tr].astype(float)
    g = labelling_propensity(x[tr], s_tr, x[va], y_rep[va].astype(float), seed)
    soft = np.full(len(clean), np.nan)
    soft[tr], c = elkan_noto(g, s_tr)
    out["regimes"]["reported_pu"] = evaluate(test, fit(labelled(y_rep, soft)), fpr)
    # The same with c taken as the report rate a survey would give, instead of estimated.
    soft[tr], _ = elkan_noto(g, s_tr, rate)
    out["regimes"]["reported_pu_known_rate"] = evaluate(test, fit(labelled(y_rep, soft)), fpr)
    true_c = metrics._ratio(y_rep[tr].sum(), (y_true[tr] == 1).sum())
    out["diagnostics"]["pu"] = {"estimated_label_rate": round(c, 4), "actual_label_rate": true_c}

    propagated = labelled(y_rep, soft_prop)
    out["regimes"]["reported_propagated"] = evaluate(test, fit(propagated), fpr)

    rows, out["diagnostics"]["feedback"] = verdicts(clean, biased_at, cfg, start, cutoff, seed)
    out["regimes"]["reported_feedback"] = evaluate(test, fit(with_verdicts(labelled(y_rep))), fpr)
    out["regimes"]["reported_propagated_feedback"] = evaluate(
        test, fit(with_verdicts(propagated)), fpr
    )
    return out


def summarise(runs: list[dict]) -> dict:
    """Mean and standard deviation across seeds of every number in the runs."""

    def walk(items: list):
        first = items[0]
        if isinstance(first, list):
            return first  # a setting, the same for every seed
        if isinstance(first, dict):
            return {k: walk([i[k] for i in items if k in i]) for k in first}
        values = np.array([np.nan if v is None else v for v in items], dtype=float)
        sd = float(np.nanstd(values, ddof=1)) if np.isfinite(values).sum() > 1 else 0.0
        return {"mean": round(float(np.nanmean(values)), 4), "sd": round(sd, 4)}

    return walk(runs)


def run(data_dir: Path, models_root: Path, seeds: tuple[int, ...] = SEEDS) -> dict:
    t0 = time.perf_counter()
    frame = load_frame(data_dir)
    cfg = json.loads((data_dir / "meta.json").read_text())["config"]
    clean = frame[~frame["ambiguous"]].reset_index(drop=True)
    cases = case_table(data_dir)
    served = operating_fpr(clean[clean["fold"] == "test"], models_root)
    runs = {}
    for seed in seeds:
        runs[seed] = run_seed(clean, cases, cfg, served["fpr"], seed)
        print(f"seed {seed}: " + ", ".join(
            f"{k} {v['case_recall']:.3f}" for k, v in runs[seed]["regimes"].items()
        ))  # fmt: skip
    return {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_dir": str(data_dir),
        "data_seed": cfg["seed"],
        "seeds": list(seeds),
        "operating_point": served,
        "assumptions": {
            "report_rate": cfg["report_rate"],
            "report_logit": REPORT_LOGIT,
            "typology_logit": TYPOLOGY_LOGIT,
            "delay_median_days": {**DELAY_MEDIAN_DAYS, "other": DELAY_MEDIAN_DEFAULT_DAYS},
            "labels_known_at_day": cfg["val_end_day"],
            "propagation_weight": PROPAGATION_WEIGHT,
            "propagation_window_days": PROPAGATION_WINDOW / DAY,
            "pu_folds": PU_FOLDS,
            "review_budget": REVIEW_BUDGET,
        },
        "regimes": list(REGIMES),
        "summary": summarise(list(runs.values())),
        "runs": {str(k): v for k, v in runs.items()},
        "seconds": round(time.perf_counter() - t0, 1),
    }


def print_summary(report: dict) -> None:
    s = report["summary"]["regimes"]
    op = report["operating_point"]
    print(f"\nat the {op['version']} {op['tier']} tier's false-positive rate, {op['fpr']:.3%}"
          f" ({len(report['seeds'])} seeds, mean ± sd):")  # fmt: skip
    keys = ("pr_auc", "loss_txn_recall", "case_recall", "taka_recall")
    rows = {r: {k: f"{s[r][k]['mean']:.3f} ± {s[r][k]['sd']:.3f}" for k in keys} for r in s}
    print(pd.DataFrame(rows).T.to_string())
    print("\nscams caught by typology:")
    typ = {
        r: {t: f"{v['case_recall']['mean']:.3f}" for t, v in s[r]["by_typology"].items()} for r in s
    }
    print(pd.DataFrame(typ).T.to_string())
    print("\ndiagnostics:", json.dumps(report["summary"]["diagnostics"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Train under realistic labels and compare")
    parser.add_argument("--small", action="store_true")
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(SEEDS))
    parser.add_argument(
        "--out", type=Path, default=settings.artifacts_dir / "reports" / REPORT_FILE
    )
    args = parser.parse_args()
    data_dir = args.data or settings.data_dir / ("small" if args.small else "full")
    report = run(data_dir, registry.models_dir(), tuple(args.seeds))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str))
    print_summary(report)
    print(f"\nwrote {args.out} in {report['seconds']}s")


if __name__ == "__main__":
    main()
