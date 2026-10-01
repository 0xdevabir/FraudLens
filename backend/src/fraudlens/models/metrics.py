"""Evaluation in the terms the business cares about: alerts raised, scams caught, taka stopped."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score


def ranking(y: np.ndarray, score: np.ndarray) -> dict:
    y = np.asarray(y)
    return {
        "pr_auc": round(float(average_precision_score(y, score)), 4),
        "roc_auc": round(float(roc_auc_score(y, score)), 4),
        "positives": int(y.sum()),
        "rows": int(len(y)),
        "base_rate": round(float(y.mean()), 5),
    }


def alert_outcomes(df: pd.DataFrame, flagged: np.ndarray) -> dict:
    """What a set of alerts achieves.

    `df` needs: y (any fraud-chain transaction), y_loss (money leaving a victim),
    amount, fraud_role, typology, case_id, day; rows in time order.

    - precision: share of alerts that are part of a fraud chain
    - loss_txn_recall / taka_recall: victim transfers alerted, by count and by value
    - case_recall: scams where at least one victim transfer was alerted
    - taka_recall_if_case_stopped: value from the first alerted transfer of each scam
      onward, i.e. what is saved if acting on the first alert ends that scam
    - taka_recall_with_exit_holds: victim transfers alerted, plus money alerted later
      as it was forwarded or cashed out by the mule
    """
    flagged = np.asarray(flagged, dtype=bool)
    n_days = max(int(df["day"].nunique()), 1)
    loss = df["y_loss"].to_numpy() == 1
    amount = df["amount"].to_numpy()
    n_alerts = int(flagged.sum())
    true_alerts = int((flagged & (df["y"].to_numpy() == 1)).sum())

    victim = pd.DataFrame(
        {
            "case_id": df["case_id"].to_numpy()[loss],
            "typology": df["typology"].to_numpy()[loss],
            "amount": amount[loss],
            "hit": flagged[loss],
        }
    )
    victim["hit_or_later"] = victim.groupby("case_id")["hit"].cummax()
    total_taka = float(victim["amount"].sum())

    # Second chance: money that got past the victim's transfer but was alerted as it left
    # the mule wallet. Forwarding and cash-out move the same money, so take the larger of
    # the two per scam, and never count more than what was still unrecovered.
    exits = (df["y"].to_numpy() == 1) & ~loss & flagged
    held = (
        pd.DataFrame(
            {
                "case_id": df["case_id"].to_numpy()[exits],
                "role": df["fraud_role"].to_numpy()[exits],
                "amount": amount[exits],
            }
        )
        .groupby(["case_id", "role"])["amount"]
        .sum()
        .groupby("case_id")
        .max()
    )
    per_case = victim.groupby("case_id").agg(
        typology=("typology", "first"),
        loss=("amount", "sum"),
        stopped=("amount", lambda a: a[victim.loc[a.index, "hit"]].sum()),
    )
    per_case["held"] = held.reindex(per_case.index).fillna(0.0)
    per_case["recovered"] = per_case["stopped"] + np.minimum(
        per_case["loss"] - per_case["stopped"], per_case["held"]
    )

    def summarise(v: pd.DataFrame, c: pd.DataFrame) -> dict:
        taka = float(v["amount"].sum())
        cases = v.groupby("case_id")["hit"].any()
        return {
            "loss_txns": int(len(v)),
            "cases": int(len(cases)),
            "loss_txn_recall": _ratio(v["hit"].sum(), len(v)),
            "case_recall": _ratio(cases.sum(), len(cases)),
            "taka_recall": _ratio(v.loc[v["hit"], "amount"].sum(), taka),
            "taka_recall_if_case_stopped": _ratio(v.loc[v["hit_or_later"], "amount"].sum(), taka),
            "taka_recall_with_exit_holds": _ratio(c["recovered"].sum(), taka),
        }

    return {
        "alerts": n_alerts,
        "alert_rate": _ratio(n_alerts, len(df), 5),
        "alerts_per_day": round(n_alerts / n_days, 1),
        "false_alerts_per_day": round((n_alerts - true_alerts) / n_days, 1),
        "precision": _ratio(true_alerts, n_alerts),
        **summarise(victim, per_case),
        "taka_at_risk": round(total_taka),
        "taka_stopped": round(float(victim.loc[victim["hit"], "amount"].sum())),
        "by_typology": {
            str(t): summarise(v, per_case[per_case["typology"] == t])
            for t, v in victim.groupby("typology")
        },
    }


def at_budgets(df: pd.DataFrame, score: np.ndarray, budgets: tuple[float, ...]) -> dict:
    """Outcomes when the top `budget` share of transactions is alerted."""
    out = {}
    for budget in budgets:
        threshold = float(np.quantile(score, 1 - budget))
        out[f"{budget:.2%}"] = {
            "threshold": threshold,
            **alert_outcomes(df, score >= threshold),
        }
    return out


def calibration(y: np.ndarray, prob: np.ndarray, bins: int = 10) -> dict:
    """Brier score and a reliability table over equal-count bins of the predicted risk."""
    y, prob = np.asarray(y), np.asarray(prob)
    order = np.argsort(prob, kind="stable")
    table = []
    for chunk in np.array_split(order, bins):
        table.append(
            {
                "predicted": round(float(prob[chunk].mean()), 5),
                "observed": round(float(y[chunk].mean()), 5),
                "rows": int(len(chunk)),
            }
        )
    high = prob >= 0.5
    return {
        "brier": round(float(brier_score_loss(y, prob)), 6),
        "mean_predicted": round(float(prob.mean()), 5),
        "observed_rate": round(float(y.mean()), 5),
        "above_0.5": {
            "rows": int(high.sum()),
            "predicted": round(float(prob[high].mean()), 4) if high.any() else None,
            "observed": round(float(y[high].mean()), 4) if high.any() else None,
        },
        "reliability": table,
    }


def _ratio(a: float, b: float, digits: int = 4) -> float | None:
    return round(float(a) / float(b), digits) if b else None
