import numpy as np
import pandas as pd
import pytest

from fraudlens.models import label_realism as L


def _cases(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    typologies = ["account_takeover", "lottery_fee", "wrong_send", "impersonation"]
    return pd.DataFrame(
        {
            # Case ids are not positions: some cases never produced a transfer.
            "case_id": np.arange(n) * 3 + 5,
            "typology": [typologies[i % 4] for i in range(n)],
            "loss": rng.lognormal(8, 1, n),
            "urban": (rng.random(n) < 0.5).astype(float),
            "tenure_days": rng.uniform(10, 900, n),
            "started_at": pd.Timestamp("2026-01-01") + pd.to_timedelta(np.arange(n), unit="h"),
        }
    )


def test_biased_reporting_averages_the_simulator_rate_and_favours_takeovers():
    cases = _cases()
    p = np.asarray(L.report_probability(cases, 0.5))
    assert p.mean() == pytest.approx(0.5, abs=1e-6)
    by_type = pd.Series(p).groupby(cases["typology"]).mean()
    assert by_type["account_takeover"] > by_type["impersonation"] > by_type["lottery_fee"]
    big = cases["loss"] > cases["loss"].median()
    assert p[big].mean() > p[~big].mean()
    assert np.all(np.asarray(L.report_probability(cases, 0.5, biased=False)) == 0.5)


def test_reports_stay_with_their_case_when_case_ids_are_not_positions():
    cases = _cases()
    p = np.where(cases["typology"] == "account_takeover", 1.0, 0.0)
    at = L.draw_reports(cases, p, np.random.default_rng(1))
    assert list(at.index) == list(cases["case_id"])
    reported = at.notna().to_numpy()
    assert np.array_equal(reported, cases["typology"].to_numpy() == "account_takeover")
    assert (at.dropna() > cases.set_index("case_id")["started_at"].reindex(at.dropna().index)).all()


def test_only_reports_in_by_the_cutoff_label_their_case():
    ts = pd.Timestamp("2026-02-01")
    report_at = pd.Series([ts, ts + pd.Timedelta(days=5), pd.NaT], index=[10, 11, 12])
    frame = pd.DataFrame({"y": [1, 1, 1, 0], "case_id": [10, 11, 12, -1]})
    y = L.observed_labels(frame, report_at, ts + pd.Timedelta(days=1))
    assert y.tolist() == [True, False, False, False]


def test_propagation_reaches_other_money_through_a_reported_mule_within_the_window():
    t = pd.Timestamp("2026-02-01")
    frame = pd.DataFrame(
        {
            "type": ["SEND_MONEY", "SEND_MONEY", "CASH_OUT", "SEND_MONEY", "SEND_MONEY"],
            "sender_id": ["V1", "V2", "M1", "V3", "X"],
            "receiver_id": ["M1", "M1", "A1", "M1", "Y"],
            "ts": [t, t + pd.Timedelta(hours=5), t + pd.Timedelta(hours=6),
                   t + pd.Timedelta(days=10), t],
        }
    )  # fmt: skip
    y_obs = np.array([True, False, False, False, False])
    hit = L.propagate(frame, y_obs)
    # Another victim's transfer and the mule's cash-out; not the one 10 days later,
    # not unrelated traffic, and never the already-labelled row.
    assert hit.tolist() == [False, True, True, False, False]


def test_elkan_noto_weights():
    g = np.array([0.4, 0.4, 0.2, 0.01])
    s = np.array([1.0, 1.0, 0.0, 0.0])
    w, c = L.elkan_noto(g, s)
    assert c == pytest.approx(0.4)
    assert np.isnan(w[:2]).all()
    assert w[2] == pytest.approx(1.5 * 0.2 / 0.8)
    assert 0 <= w[3] < w[2] <= 1
    w_known, c_known = L.elkan_noto(g, s, 0.5)
    assert c_known == 0.5 and w_known[2] == pytest.approx(0.25)


def test_soft_rows_are_split_into_weighted_fraud_and_clean_copies():
    x = np.arange(6, dtype=float).reshape(3, 2)
    y = np.array([1.0, 0.0, 0.0])
    xs, ys, ws = L.with_soft_labels(x, y, np.array([np.nan, 0.7, np.nan]))
    assert len(xs) == 4 and ys.tolist() == [1.0, 0.0, 1.0, 0.0]
    assert ws.tolist() == pytest.approx([1.0, 1.0, 0.7, 0.3])
    assert L.with_soft_labels(x, y, None)[2] is None


def test_experiment_runs_every_regime_at_the_served_false_positive_rate(trained):
    data_dir, models_root, _ = trained
    report = L.run(data_dir, models_root, seeds=(7,))
    assert list(report["summary"]["regimes"]) == list(L.REGIMES)
    target = report["operating_point"]["fpr"]
    for result in report["runs"]["7"]["regimes"].values():
        assert result["fpr"] <= target + 1e-3
        assert 0 <= result["pr_auc"] <= 1
    labels = report["runs"]["7"]["labels"]
    assert labels["ground_truth"]["case_label_rate"] == 1.0
    assert labels["reported"]["fraud_rows_labelled"] < labels["ground_truth"]["fraud_rows"]
    assert labels["reported"]["clean_rows_labelled_fraud"] == 0
