"""Analysts' verdicts as training labels.

A closed case labels every alert attached to it: `confirmed_fraud` makes them
fraud, `legitimate` makes them clean, `inconclusive` labels nothing. The features
are the ones that were served, stored with each decision, so a retrained model
learns from exactly what the live one saw.

Only what was alerted can be reviewed, so these labels are a biased sample: they
say where the model was right or wrong among its alerts and nothing about the
fraud it let through. They are added to the training data, never used in its place.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..features import FEATURES
from ..platform.models import Case, Decision, Transaction

LABELS = {"confirmed_fraud": 1, "legitimate": 0}


def labels(s: Session) -> pd.DataFrame:
    """One row per reviewed alert, in `models.train.FEEDBACK_COLUMNS` plus case and verdict."""
    rows = s.execute(
        select(
            Decision.txn_id, Transaction.ts, Transaction.type,
            Case.id, Case.verdict, Decision.features,
        )
        .join(Transaction, Transaction.txn_id == Decision.txn_id)
        .join(Case, Case.id == Decision.case_id)
        .where(Case.verdict.in_(LABELS))
        .order_by(Decision.txn_id)
    ).all()  # fmt: skip
    # A decision made by code with another feature list cannot be trained on.
    rows = [row for row in rows if len(row.features) == len(FEATURES)]
    frame = pd.DataFrame(
        {
            "txn_id": np.array([row.txn_id for row in rows], dtype=np.int64),
            # Naive UTC, as in the dataset the training frame is read from.
            "ts": pd.to_datetime([row.ts for row in rows], utc=True).tz_localize(None),
            "type": [row.type for row in rows],
            "case_id": [row.id for row in rows],
            "verdict": [row.verdict for row in rows],
        }
    )
    frame["y"] = frame["verdict"].map(LABELS).astype("int8")
    # A confirmed payment into a wallet is what the mule model learns from.
    frame["y_mule"] = (frame["y"] * (frame["type"] == "SEND_MONEY")).astype("int8")
    features = np.array([row.features for row in rows], dtype=np.float64).reshape(-1, len(FEATURES))
    return pd.concat([frame, pd.DataFrame(features, columns=list(FEATURES))], axis=1)


def summary(s: Session) -> dict:
    """How many labels the review work has produced so far."""
    cases = dict(
        s.execute(
            select(Case.verdict, func.count())
            .where(Case.verdict.is_not(None))
            .group_by(Case.verdict)
        ).all()
    )
    alerts = dict(
        s.execute(
            select(Case.verdict, func.count())
            .join(Decision, Decision.case_id == Case.id)
            .where(Case.verdict.in_(LABELS))
            .group_by(Case.verdict)
        ).all()
    )
    return {
        "cases": {verdict: cases.get(verdict, 0) for verdict in (*LABELS, "inconclusive")},
        "labels": {
            "fraud": alerts.get("confirmed_fraud", 0),
            "legitimate": alerts.get("legitimate", 0),
        },
        "last_verdict_at": s.scalar(select(func.max(Case.closed_at))),
    }
