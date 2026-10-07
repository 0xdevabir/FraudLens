"""External validation helpers and the bootstrap intervals (no downloads needed)."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from fraudlens.external import baf, download, paysim
from fraudlens.external.evaluate import bootstrap_ci, judge, threshold_at_fpr
from fraudlens.models.bootstrap import bootstrap, point_metrics

# ------------------------------------------------------------------- thresholds


def test_threshold_at_fpr_never_exceeds_the_target():
    rng = np.random.default_rng(0)
    y = (rng.random(5000) < 0.05).astype(int)
    score = rng.random(5000) + y
    for fpr in (0.0, 0.005, 0.01, 0.05):
        thr = threshold_at_fpr(y, score, fpr)
        reached = (score[y == 0] >= thr).mean()
        assert reached <= fpr
        assert reached >= fpr - 2 / (y == 0).sum()


def test_threshold_ties_are_not_alerted_past_the_budget():
    y = np.array([0, 0, 0, 0, 1])
    score = np.array([0.5, 0.5, 0.5, 0.1, 0.9])
    thr = threshold_at_fpr(y, score, 0.25)  # one negative allowed, but three are tied
    assert (score[y == 0] >= thr).sum() == 0
    assert score[4] >= thr


def test_judge_fixes_the_threshold_on_validation():
    y_val = np.array([0] * 100 + [1] * 10)
    s_val = np.r_[np.linspace(0, 1, 100), np.full(10, 2.0)]
    y_test = np.array([0] * 100 + [1] * 10)
    s_test = s_val + 0.5  # the test scores drift up: the validation threshold over-alerts
    out = judge(y_val, s_val, y_test, s_test, (0.01,))
    assert out["pr_auc"] == 1.0
    at = out["at_fpr_0.01"]
    assert at["recall"] == 1.0
    assert at["fpr_on_test"] > 0.01
    assert at["recall_threshold_set_on_test"] == 1.0


def test_bootstrap_ci_brackets_the_point_estimate():
    rng = np.random.default_rng(1)
    y = (rng.random(3000) < 0.1).astype(int)
    s = rng.random(3000) + 0.8 * y
    units = np.arange(3000) // 30
    ci = bootstrap_ci(y, s, y, s, units, 0.05, replicates=100)
    thr = threshold_at_fpr(y, s, 0.05)
    recall = (s[y == 1] >= thr).mean()
    low, high = ci["recall_at_fpr_0.05"]
    assert low <= recall <= high
    assert ci["units"] == 100


# ------------------------------------------------------------------- PaySim features


def _paysim_rows():
    rows = [
        # step, type, amount, nameOrig, nameDest
        (1, "CASH_IN", 100.0, "C1", "A"),
        (1, "TRANSFER", 50.0, "C2", "A"),
        (2, "PAYMENT", 10.0, "A", "M1"),  # A sends after receiving at step 1
        (30, "TRANSFER", 200.0, "C3", "A"),
        (30, "CASH_OUT", 70.0, "C4", "B"),
    ]
    df = pd.DataFrame(rows, columns=["step", "type", "amount", "nameOrig", "nameDest"])
    return df.assign(oldbalanceOrg=1000.0, oldbalanceDest=0.0, isFraud=0, isFlaggedFraud=0)


def test_paysim_features_count_only_earlier_rows():
    out = paysim.add_features(_paysim_rows())
    assert out["r_prior_in"].tolist() == [0, 1, 0, 2, 0]
    # The 24-hour window at step 30 no longer sees step 1.
    assert out["r_prior_in_24h"].tolist() == [0, 1, 0, 0, 0]
    assert out["r_prior_in_value_24h"].iloc[3] == 0.0
    assert out["r_hours_since_last_in"].iloc[3] == 29
    assert np.isnan(out["r_hours_since_last_in"].iloc[0])
    assert out["r_prior_in_transfers"].iloc[3] == 1
    assert out["amount_vs_r_mean_in"].iloc[3] == pytest.approx(200 / 75)
    # A received 50 at step 1 (the later of its two receipts) and sends at step 2.
    assert out["s_hours_since_received"].iloc[2] == 1
    assert out["amount_vs_s_received"].iloc[2] == pytest.approx(10 / 50)


def test_paysim_features_do_not_see_the_future():
    rows = _paysim_rows()
    later = pd.concat(
        [rows, pd.DataFrame([{**rows.iloc[0].to_dict(), "step": 40, "amount": 9e9}])],
        ignore_index=True,
    )
    before = paysim.add_features(rows)[list(paysim.BEHAVIOUR_FEATURES)]
    after = paysim.add_features(later)[list(paysim.BEHAVIOUR_FEATURES)].iloc[: len(rows)]
    pd.testing.assert_frame_equal(before, after)


def test_paysim_split_is_by_time_and_scores_transfers_and_cash_outs():
    df = pd.DataFrame(
        {"step": [1, 300, 301, 400, 401, 743], "type": ["TRANSFER", "CASH_OUT"] * 3}
    ).assign(isFraud=0)
    df.loc[0, "type"] = "PAYMENT"
    folds = paysim.split(df)
    assert folds["train"]["step"].tolist() == [300]
    assert folds["val"]["step"].tolist() == [301, 400]
    assert folds["test"]["step"].tolist() == [401, 743]


# ------------------------------------------------------------------- BAF


def test_baf_categories_come_from_the_training_months():
    df = pd.DataFrame(
        {
            "fraud_bool": [0, 1, 0, 0],
            "month": [0, 1, 6, 7],
            "device_os": ["linux", "windows", "windows", "macintosh"],
            "income": [0.1, 0.2, 0.3, 0.4],
        }
    )
    encoded, features = baf.encode(df)
    assert features == ["device_os", "income"]
    assert encoded["device_os"].tolist() == [0, 1, 1, -1]  # unseen in training: -1


# ------------------------------------------------------------------- download


def test_download_refuses_a_file_with_the_wrong_checksum(tmp_path, monkeypatch):
    source = download.Source("demo", "https://example.invalid/x", "x.bin", "0" * 64, 4, "")
    monkeypatch.setitem(download.SOURCES, "demo", source)
    monkeypatch.setattr(download, "external_dir", lambda: tmp_path)
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "x.bin").write_bytes(b"abcd")  # right size: no download attempted
    with pytest.raises(RuntimeError, match="does not match"):
        download.fetch("demo")
    good = download.sha256_of(tmp_path / "demo" / "x.bin")
    monkeypatch.setitem(download.SOURCES, "demo", replace(source, sha256=good))
    assert download.fetch("demo") == tmp_path / "demo" / "x.bin"


# ------------------------------------------------------------------- bootstrap


def _test_period():
    # Day 0: scam 1 (two victim transfers, one alerted), 3 legitimate (one alerted).
    # Day 1: scam 2 (one victim transfer, missed), 3 legitimate.
    rows = [
        # y, y_loss, case_id, day, risk, alert
        (1, 1, 1, 0, 0.9, True),
        (1, 1, 1, 0, 0.2, False),
        (0, 0, -1, 0, 0.8, True),
        (0, 0, -1, 0, 0.1, False),
        (0, 0, -1, 0, 0.1, False),
        (1, 1, 2, 1, 0.3, False),
        (0, 0, -1, 1, 0.05, False),
        (0, 0, -1, 1, 0.05, False),
        (0, 0, -1, 1, 0.05, False),
    ]
    df = pd.DataFrame(rows, columns=["y", "y_loss", "case_id", "day", "risk", "alert"])
    return df


def test_point_metrics_match_hand_counts():
    df = _test_period()
    m = point_metrics(
        df["y"].to_numpy(),
        df["y_loss"].to_numpy() == 1,
        df["case_id"].to_numpy(),
        df["risk"].to_numpy(),
        df["alert"].to_numpy(),
    )
    assert m["scam_recall"] == 0.5  # scam 1 caught, scam 2 missed
    assert m["false_alert_rate"] == pytest.approx(1 / 6)


def test_bootstrap_is_reproducible_and_brackets_the_point():
    df = _test_period()
    a = bootstrap(df, df["risk"].to_numpy(), df["alert"].to_numpy(), replicates=200)
    b = bootstrap(df, df["risk"].to_numpy(), df["alert"].to_numpy(), replicates=200)
    assert a == b
    assert a["n"] == {"rows": 9, "legitimate": 6, "scams": 2, "days": 2}
    for scheme in ("by_day", "by_scam"):
        for metric, value in a["point"].items():
            assert a[scheme][metric]["low"] <= value <= a[scheme][metric]["high"]
