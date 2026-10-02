"""Drift on what is being served, against the reference kept with the model.

`decision.insights` measures drift on the back-test. This is the same index on
live decisions: their stored feature vectors and scores against the training
period's bins. No labels are needed, so it works from the first day.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..decision.insights import (
    DRIFT_REFERENCE_FILE,
    PSI_SHIFTED,
    PSI_WATCH,
    drift_status,
    psi_against,
)
from ..features import FEATURES
from ..platform.models import Decision

MIN_ROWS = 200  # fewer decisions than this say nothing about a distribution
DEFAULT_ROWS = 20_000


def load_reference(model_dir: Path) -> dict | None:
    """The bins written by `decision.insights`, or None if that step has not been run."""
    try:
        reference = json.loads((model_dir / DRIFT_REFERENCE_FILE).read_text())
    except (OSError, ValueError):
        return None
    return reference if set(reference.get("features", ())) == set(FEATURES) else None


def measure(
    reference: dict, features: np.ndarray, risk: np.ndarray, thresholds: dict[str, float]
) -> dict:
    """Feature and score drift of a sample: `features` is [n, len(FEATURES)], `risk` is [n]."""
    features = np.asarray(features, dtype=np.float64)
    risk = np.asarray(risk, dtype=np.float64)
    if features.ndim != 2 or features.shape[1] != len(FEATURES) or len(risk) != len(features):
        raise ValueError("drift needs one feature vector and one score per decision")
    if len(features) < MIN_ROWS:
        raise ValueError(f"drift needs at least {MIN_ROWS} decisions")
    rows = []
    for i, name in enumerate(FEATURES):
        value = psi_against(reference["features"][name], features[:, i])
        rows.append(
            {
                "feature": name,
                "psi": value,
                "status": drift_status(value),
                "missing": round(float(np.isnan(features[:, i]).mean()), 4),
            }
        )
    rows.sort(key=lambda row: row["psi"], reverse=True)
    score = psi_against(reference["score"], risk)
    return {
        "rows": int(len(features)),
        "limits": {"watch": PSI_WATCH, "shifted": PSI_SHIFTED},
        "feature_status": {
            status: sum(row["status"] == status for row in rows)
            for status in ("stable", "watch", "shifted")
        },
        "features": rows,
        "score": {
            "psi": score,
            "status": drift_status(score),
            "alert_rate": round(float((risk >= thresholds["warn"]).mean()), 5),
            "hold_rate": round(float((risk >= thresholds["hold"]).mean()), 5),
            "validation_alert_rate": reference["validation_alert_rate"],
            "validation_hold_rate": reference["validation_hold_rate"],
        },
    }


def live(
    s: Session, reference: dict, thresholds: dict[str, float], version: str, rows: int
) -> dict:
    """Drift over the latest `rows` decisions made by model `version`."""
    found = s.execute(
        select(Decision.decided_at, Decision.risk, Decision.features)
        .where(Decision.model_version == version, Decision.risk.is_not(None))
        .order_by(Decision.decided_at.desc())
        .limit(rows)
    ).all()
    found = [row for row in found if len(row.features) == len(FEATURES)]
    if len(found) < MIN_ROWS:
        return {"status": "not_enough_data", "rows": len(found), "needed": MIN_ROWS}
    features = np.array([row.features for row in found], dtype=np.float64)
    risk = np.array([row.risk for row in found], dtype=np.float64)
    report = measure(reference, features, risk, thresholds)

    # The score day by day, so a shift can be seen arriving.
    days = np.array([row.decided_at.date().isoformat() for row in found])
    daily = []
    for day in sorted(set(days)):
        mask = days == day
        if mask.sum() < MIN_ROWS:
            continue
        value = psi_against(reference["score"], risk[mask])
        daily.append(
            {
                "date": day,
                "rows": int(mask.sum()),
                "psi": value,
                "status": drift_status(value),
                "alert_rate": round(float((risk[mask] >= thresholds["warn"]).mean()), 5),
            }
        )
    return {
        "status": "ok",
        "model_version": version,
        "reference": reference["reference"],
        "from": found[-1].decided_at,
        "to": found[0].decided_at,
        **report,
        "daily": daily,
    }
