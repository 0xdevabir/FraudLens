"""Shared fitting and scoring for the external datasets.

Every score is judged the same way: thresholds are fixed on the validation period
at a target false-positive rate, then recall and the false-positive rate actually
reached are measured on the later test period. PR-AUC and ROC-AUC are threshold-free.
"""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..models.train import LGB_PARAMS, SEED

BOOTSTRAP_REPLICATES = 500


def threshold_at_fpr(y: np.ndarray, score: np.ndarray, fpr: float) -> float:
    """Lowest threshold whose false-positive rate on (y, score) is at most `fpr`."""
    negatives = np.sort(score[y == 0])[::-1]
    k = int(np.floor(fpr * len(negatives)))  # negatives allowed at or above the threshold
    if k >= len(negatives):
        return float(negatives[-1])
    # Alert on score > negatives[k]: exactly the k highest negatives (ties go below).
    return float(np.nextafter(negatives[k], np.inf))


def judge(
    y_val: np.ndarray,
    s_val: np.ndarray,
    y_test: np.ndarray,
    s_test: np.ndarray,
    fprs: tuple[float, ...],
) -> dict:
    out = {
        "pr_auc": round(float(average_precision_score(y_test, s_test)), 4),
        "roc_auc": round(float(roc_auc_score(y_test, s_test)), 4),
    }
    for fpr in fprs:
        thr = threshold_at_fpr(y_val, s_val, fpr)
        flagged = s_test >= thr
        # The literature (and the BAF benchmark) usually sets the threshold on the test
        # set itself; reported separately, because no deployment can do that.
        exact = s_test >= threshold_at_fpr(y_test, s_test, fpr)
        out[f"at_fpr_{fpr:g}"] = {
            "recall": round(float(flagged[y_test == 1].mean()), 4),
            "fpr_on_test": round(float(flagged[y_test == 0].mean()), 5),
            "precision": round(float(y_test[flagged].mean()), 4) if flagged.any() else None,
            "recall_threshold_set_on_test": round(float(exact[y_test == 1].mean()), 4),
        }
    return out


def bootstrap_ci(
    y_val: np.ndarray,
    s_val: np.ndarray,
    y_test: np.ndarray,
    s_test: np.ndarray,
    units: np.ndarray,
    fpr: float,
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = SEED,
) -> dict:
    """Percentile 95% intervals for test recall at `fpr` and PR-AUC.

    `units` are resampled with replacement (e.g. hours, so that transactions of the
    same hour stay together). The validation threshold is held fixed.
    """
    thr = threshold_at_fpr(y_val, s_val, fpr)
    flagged = s_test >= thr
    codes, inverse = np.unique(units, return_inverse=True)
    rng = np.random.default_rng(seed)
    recall, pr_auc = [], []
    pos = y_test == 1
    for _ in range(replicates):
        counts = np.bincount(rng.integers(0, len(codes), len(codes)), minlength=len(codes))
        w = counts[inverse].astype(np.float64)
        recall.append((w * (flagged & pos)).sum() / max((w * pos).sum(), 1e-12))
        keep = w > 0
        pr_auc.append(average_precision_score(y_test[keep], s_test[keep], sample_weight=w[keep]))

    def ci(values: list[float]) -> list[float]:
        return [round(float(np.quantile(values, q)), 4) for q in (0.025, 0.975)]

    return {
        "units": int(len(codes)),
        "replicates": replicates,
        f"recall_at_fpr_{fpr:g}": ci(recall),
        "pr_auc": ci(pr_auc),
    }


def fit_lightgbm(
    x_train: pd.DataFrame, y_train: np.ndarray, x_val: pd.DataFrame, y_val: np.ndarray
) -> lgb.Booster:
    """The FraudLens transaction-model recipe: same parameters, early stopping on val."""
    cols = list(x_train.columns)
    dtrain = lgb.Dataset(x_train.to_numpy(np.float64), y_train, feature_name=cols)
    dval = lgb.Dataset(x_val.to_numpy(np.float64), y_val, reference=dtrain)
    booster = lgb.train(
        LGB_PARAMS,
        dtrain,
        num_boost_round=2000,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(100, verbose=False)],
    )
    return lgb.Booster(model_str=booster.model_to_string(num_iteration=booster.best_iteration))


def fit_logistic(x_train: pd.DataFrame, y_train: np.ndarray):
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=1.0))
    return model.fit(finite(x_train), y_train)


def fit_isolation_forest(x_train: pd.DataFrame) -> IsolationForest:
    sample = x_train.sample(min(len(x_train), 200_000), random_state=SEED)
    return IsolationForest(n_estimators=100, random_state=SEED, n_jobs=-1).fit(finite(sample))


def anomaly_score(model: IsolationForest, x: pd.DataFrame) -> np.ndarray:
    return -model.score_samples(finite(x))


def finite(x: pd.DataFrame) -> np.ndarray:
    """Missing values (e.g. no earlier transfer) as -1, which no real value takes."""
    return np.nan_to_num(x.to_numpy(np.float64), nan=-1.0, posinf=-1.0, neginf=-1.0)


def top_gain(booster: lgb.Booster, n: int = 8) -> list[dict]:
    gain = booster.feature_importance("gain")
    share = gain / gain.sum() if gain.sum() else gain
    order = np.argsort(-share)[:n]
    names = booster.feature_name()
    return [{"feature": names[i], "gain_share": round(float(share[i]), 3)} for i in order]
