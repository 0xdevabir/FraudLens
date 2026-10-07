"""Analysts' verdicts as training labels.

A closed case labels every alert attached to it: `confirmed_fraud` makes them
fraud, `legitimate` makes them clean, `inconclusive` labels nothing. The features
are the ones that were served, stored with each decision, so a retrained model
learns from exactly what the live one saw.

Only what was alerted can be reviewed, so these labels are a biased sample: they
say where the model was right or wrong among its alerts and nothing about the
fraud it let through. They are added to the training data, never used in its place.

A customer appeal a reviewer approved ("this is a genuine payment", checked by a
person) labels that one payment `legitimate`, unless its case reached a verdict:
the case looked at more and outranks it. A rejected appeal labels nothing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..features import FEATURES
from ..platform.models import Appeal, Case, Decision, Transaction

LABELS = {"confirmed_fraud": 1, "legitimate": 0}

# An approved appeal whose payment has no case verdict to outrank it.
_APPEAL_ONLY = and_(
    Appeal.status == "approved",
    or_(Decision.case_id.is_(None), Case.verdict.is_(None), Case.verdict.not_in(LABELS)),
)


def labels(s: Session) -> pd.DataFrame:
    """One row per reviewed alert, in `models.train.FEEDBACK_COLUMNS` plus case, verdict
    and where the label came from (`case` or `appeal`)."""
    columns = (Decision.txn_id, Transaction.ts, Transaction.type, Case.id, Decision.features)
    reviewed = s.execute(
        select(*columns, Case.verdict)
        .join(Transaction, Transaction.txn_id == Decision.txn_id)
        .join(Case, Case.id == Decision.case_id)
        .where(Case.verdict.in_(LABELS))
    ).all()
    appealed = s.execute(
        select(*columns)
        .join(Transaction, Transaction.txn_id == Decision.txn_id)
        .join(Appeal, Appeal.txn_id == Decision.txn_id)
        .outerjoin(Case, Case.id == Decision.case_id)
        .where(_APPEAL_ONLY)
    ).all()
    rows = sorted(
        [(*row, "case") for row in reviewed] + [(*row, "legitimate", "appeal") for row in appealed],
        key=lambda row: row[0],
    )
    # A decision made by code with another feature list cannot be trained on.
    rows = [row for row in rows if len(row[4]) == len(FEATURES)]
    frame = pd.DataFrame(
        {
            "txn_id": np.array([row[0] for row in rows], dtype=np.int64),
            # Naive UTC, as in the dataset the training frame is read from.
            "ts": pd.to_datetime([row[1] for row in rows], utc=True).tz_localize(None),
            "type": [row[2] for row in rows],
            "case_id": pd.array([row[3] for row in rows], dtype="Int64"),
            "verdict": [row[5] for row in rows],
            "source": [row[6] for row in rows],
        }
    )
    frame["y"] = frame["verdict"].map(LABELS).astype("int8")
    # A confirmed payment into a wallet is what the mule model learns from.
    frame["y_mule"] = (frame["y"] * (frame["type"] == "SEND_MONEY")).astype("int8")
    features = np.array([row[4] for row in rows], dtype=np.float64).reshape(-1, len(FEATURES))
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
    appeals = s.scalar(
        select(func.count())
        .select_from(Appeal)
        .join(Decision, Decision.txn_id == Appeal.txn_id)
        .outerjoin(Case, Case.id == Decision.case_id)
        .where(_APPEAL_ONLY)
    )
    return {
        "cases": {verdict: cases.get(verdict, 0) for verdict in (*LABELS, "inconclusive")},
        "labels": {
            "fraud": alerts.get("confirmed_fraud", 0),
            "legitimate": alerts.get("legitimate", 0) + appeals,
        },
        "approved_appeals": appeals,
        "last_verdict_at": s.scalar(select(func.max(Case.closed_at))),
        "false_alarm_reasons": false_alarm_reasons(s),
    }


def false_alarm_reasons(s: Session) -> list[dict]:
    """Why reviewers called alerts legitimate, most common first. For reading, not training:
    a reason code never changes a label or a weight. A code that keeps coming back points
    at a rule or threshold worth a look in the next policy review."""
    rows = s.execute(
        select(Case.reason_code, func.count(Case.id.distinct()), func.count(Decision.txn_id))
        .outerjoin(Decision, Decision.case_id == Case.id)
        .where(Case.verdict == "legitimate")
        .group_by(Case.reason_code)
        .order_by(func.count(Case.id.distinct()).desc())
    ).all()
    total = sum(cases for _, cases, _ in rows) or 1
    return [
        {
            "reason_code": code or "not_given",
            "cases": cases,
            "alerts": alerts,
            "share": cases / total,
        }
        for code, cases, alerts in rows
    ]
