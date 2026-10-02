"""Retrain with the analysts' verdicts and register the result as a challenger.

    uv run python -m fraudlens.mlops.retrain

The new version is saved next to the others and is NOT promoted. The steps after
this are deliberate and manual: look at its report, run it in shadow mode
(`fraudlens.mlops.shadow`), and only then `make promote VERSION=vN`.

The last `--holdout-days` of decisions give no labels to the model, so there is a
period after every label on which it can be compared with the served version.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, timedelta

from sqlalchemy import func, select

from ..config import Settings
from ..decision import evaluate, insights
from ..models.train import FEEDBACK_COLUMNS, train
from ..platform.db import make_engine, make_sessions
from ..platform.models import Decision
from . import feedback

HOLDOUT_DAYS = 7


def run(settings: Settings, holdout_days: int = HOLDOUT_DAYS) -> dict:
    engine = make_engine(settings.database_url)
    try:
        with make_sessions(engine)() as s:
            labels = feedback.labels(s)
            latest = s.scalar(select(func.max(Decision.decided_at)))
    finally:
        engine.dispose()
    if latest is not None:
        # Labels carry naive UTC, like the dataset.
        cutoff = (latest.astimezone(UTC) - timedelta(days=holdout_days)).replace(tzinfo=None)
        labels = labels[labels["ts"] <= cutoff]
    if labels.empty:
        raise SystemExit("no verdicts old enough to train on: close some cases first")
    report = train(
        settings.dataset_dir,
        settings.models_dir,
        promote=False,
        feedback=labels[list(FEEDBACK_COLUMNS)].reset_index(drop=True),
    )
    version = report["version"]
    # The reports the console reads for any version, and its drift reference.
    evaluate.run(settings.dataset_dir, settings.policy_version, version, settings.models_dir)
    insights.run(settings.dataset_dir, settings.policy_version, version, settings.models_dir)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrain with analyst verdicts as extra labels")
    parser.add_argument("--holdout-days", type=int, default=HOLDOUT_DAYS)
    args = parser.parse_args()
    if args.holdout_days < 1:
        parser.error("--holdout-days must be at least 1")
    report = run(Settings(), args.holdout_days)
    print(f"registered {report['version']} (not promoted) in {report['seconds']}s")
    print(json.dumps(report["feedback"], indent=2, default=str))


if __name__ == "__main__":
    main()
