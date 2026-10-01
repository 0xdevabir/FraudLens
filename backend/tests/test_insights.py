"""Impact sweep, drift and fairness tables for the dashboards."""

import json

import numpy as np
import pandas as pd
import pytest

from fraudlens.decision import insights
from fraudlens.decision.insights import _group_table, drift_status, psi
from fraudlens.features import FEATURES


def test_psi_is_zero_for_the_same_distribution_and_grows_with_a_shift():
    rng = np.random.default_rng(0)
    reference = rng.normal(size=20_000)
    assert psi(reference, reference) == 0.0
    assert psi(reference, rng.normal(size=20_000)) < 0.01
    small, large = psi(reference, reference + 0.3), psi(reference, reference + 1.5)
    assert 0.01 < small < large and large > insights.PSI_SHIFTED


def test_psi_counts_missing_values_as_their_own_bin():
    reference = np.arange(1000, dtype=float)
    current = reference.copy()
    current[:400] = np.nan
    assert psi(reference, current) > insights.PSI_SHIFTED
    assert psi(np.full(10, np.nan), np.full(10, np.nan)) == 0.0


def test_psi_handles_a_constant_feature_and_rejects_empty_input():
    assert psi(np.zeros(100), np.zeros(50)) == 0.0
    assert psi(np.zeros(100), np.ones(50)) > insights.PSI_SHIFTED
    with pytest.raises(ValueError):
        psi(np.array([]), np.zeros(3))


def test_drift_status_limits():
    assert [drift_status(v) for v in (0.0, 0.0999, 0.1, 0.2499, 0.25, 3.0)] == [
        "stable", "stable", "watch", "watch", "shifted", "shifted",
    ]  # fmt: skip


def test_group_table_rates_and_ratio():
    # Group a: 4 legitimate, 1 alerted. Group b: 4 legitimate, 3 alerted, plus a caught victim.
    test = pd.DataFrame({"y": [0] * 8 + [1], "y_loss": [0] * 8 + [1]})
    groups = np.array(["a"] * 4 + ["b"] * 5)
    rank = np.array([1, 0, 0, 0, 3, 3, 1, 0, 3])
    a, b = _group_table(groups, test, rank)
    assert (a["false_alert_rate"], a["false_hold_rate"], a["false_alerts"]) == (0.25, 0.0, 1)
    assert (b["false_alert_rate"], b["false_hold_rate"], b["false_alerts"]) == (0.75, 0.5, 3)
    assert (a["ratio_to_overall"], b["ratio_to_overall"]) == (0.5, 1.5)
    assert a["victim_transfers_alerted"] is None and b["victim_transfers_alerted"] == 1.0
    assert a["too_small"] and b["too_small"]


def test_insights_on_the_small_world(trained):
    data_dir, models_root, _ = trained
    report = insights.run(data_dir, models_root=models_root)
    version = report["model_version"]
    assert json.loads((models_root / version / insights.INSIGHTS_FILE).read_text()) == report

    sweep = report["impact"]
    assert len(sweep) == insights.SWEEP_POINTS
    thresholds = [point["threshold"] for point in sweep]
    assert thresholds == sorted(thresholds, reverse=True)
    # Lowering the threshold can only alert more and catch more.
    for key in ("alerts_per_day", "case_recall", "taka_stopped", "legit_customers_alerted"):
        values = [point[key] for point in sweep]
        assert values == sorted(values), key

    assert sum(day["scored"] for day in report["daily"]) == report["rows"]
    for day in report["daily"]:
        assert day["scored"] == day["allow"] + day["warn"] + day["step_up"] + day["hold"]
        assert day["taka_held"] <= day["taka_stopped"] <= day["victim_taka"]
        assert day["true_alerts"] + day["false_alerts"] == day["scored"] - day["allow"]

    drift = report["drift"]
    assert {f["feature"] for f in drift["features"]} == set(FEATURES)
    assert sum(drift["feature_status"].values()) == len(FEATURES)
    assert all(set(f["psi"]) == set(drift["periods"]) for f in drift["features"])
    assert drift["score"][-1]["period"] == "test" and drift["score"][-1]["rows"] == report["rows"]

    fair = report["fairness"]
    legit = sum(row["legitimate"] for row in fair["sender"]["segment"])
    false_alerts = sum(row["false_alerts"] for row in fair["sender"]["segment"])
    assert fair["overall"]["false_alert_rate"] == pytest.approx(false_alerts / legit, abs=1e-5)
    for column in insights.GROUP_COLUMNS:
        assert sum(row["transactions"] for row in fair["sender"][column]) == report["rows"]
