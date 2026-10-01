import json

import numpy as np
import pandas as pd
import pytest

from fraudlens.features import FEATURES, RECIPIENT_FEATURES, FeatureEngine, Txn
from fraudlens.models import metrics, registry
from fraudlens.models.agents import METRICS, MIN_CASHOUTS, score_agents
from fraudlens.models.bundle import ModelBundle
from fraudlens.models.data import load_frame
from fraudlens.models.rings import find_rings
from fraudlens.models.train import TIER_POLICY, choose_thresholds

# ------------------------------------------------------------------- metrics


def _alerts_frame():
    # Two scams. Case 1: two victim transfers then a mule cash-out. Case 2: one transfer.
    rows = [
        # y, y_loss, amount, role, typology, case, day
        (0, 0, 500, "", "", -1, 0),
        (1, 1, 1000, "victim_transfer", "impersonation", 1, 0),
        (1, 1, 3000, "victim_transfer", "impersonation", 1, 0),
        (1, 0, 3900, "mule_cashout", "impersonation", 1, 0),
        (1, 1, 2000, "victim_transfer", "wrong_send", 2, 1),
        (0, 0, 700, "", "", -1, 1),
    ]
    cols = ["y", "y_loss", "amount", "fraud_role", "typology", "case_id", "day"]
    return pd.DataFrame(rows, columns=cols)


def test_alert_outcomes_counts_scams_and_taka():
    df = _alerts_frame()
    # Alert the second victim transfer of case 1 and one clean transaction.
    out = metrics.alert_outcomes(df, np.array([1, 0, 1, 0, 0, 0], dtype=bool))
    assert out["alerts"] == 2 and out["precision"] == 0.5
    assert out["alerts_per_day"] == 1.0 and out["false_alerts_per_day"] == 0.5
    assert out["loss_txn_recall"] == pytest.approx(1 / 3, abs=1e-4)
    assert out["case_recall"] == 0.5
    assert out["taka_at_risk"] == 6000 and out["taka_stopped"] == 3000
    assert out["taka_recall"] == 0.5 == out["taka_recall_if_case_stopped"]
    assert out["by_typology"]["wrong_send"]["case_recall"] == 0.0
    assert out["by_typology"]["impersonation"]["taka_recall"] == 0.75


def test_stopping_the_first_transfer_saves_the_rest_of_the_scam():
    df = _alerts_frame()
    out = metrics.alert_outcomes(df, np.array([0, 1, 0, 0, 0, 0], dtype=bool))
    assert out["taka_recall"] == pytest.approx(1000 / 6000, abs=1e-4)
    assert out["taka_recall_if_case_stopped"] == pytest.approx(4000 / 6000, abs=1e-4)


def test_holding_the_mule_cash_out_recovers_money_but_never_more_than_was_lost():
    df = _alerts_frame()
    exit_only = metrics.alert_outcomes(df, np.array([0, 0, 0, 1, 0, 0], dtype=bool))
    assert exit_only["taka_recall"] == 0.0
    assert exit_only["taka_recall_with_exit_holds"] == pytest.approx(3900 / 6000, abs=1e-4)
    # Both the transfer and the cash-out alerted: the same money is not counted twice.
    both = metrics.alert_outcomes(df, np.array([0, 1, 1, 1, 0, 0], dtype=bool))
    assert both["taka_recall_with_exit_holds"] == pytest.approx(4000 / 6000, abs=1e-4)


def test_no_alerts_gives_no_precision_not_a_crash():
    out = metrics.alert_outcomes(_alerts_frame(), np.zeros(6, dtype=bool))
    assert out["alerts"] == 0 and out["precision"] is None and out["taka_recall"] == 0.0


def test_thresholds_meet_precision_targets_and_are_ordered():
    rng = np.random.default_rng(0)
    y = (rng.random(20_000) < 0.02).astype(int)
    risk = np.clip(0.6 * y + rng.normal(0.2, 0.15, len(y)), 0, 1)
    thresholds = choose_thresholds(y, risk)
    assert thresholds["hold"] >= thresholds["step_up"] >= thresholds["warn"]
    for tier, rule in TIER_POLICY.items():
        alerted = risk >= thresholds[tier]
        assert y[alerted].mean() >= rule["min_precision"] - 1e-9
        assert alerted.mean() <= rule["max_alert_rate"] + 1e-9


def test_thresholds_when_no_score_is_precise_enough():
    y = np.array([0, 0, 0, 1])
    risk = np.array([0.9, 0.8, 0.7, 0.1])
    assert choose_thresholds(y, risk) == {"hold": 0.9, "step_up": 0.9, "warn": 0.9}


# --------------------------------------------------------------------- agents


def _agent_rows(n=60, seed=0):
    rng = np.random.default_rng(seed)
    table = pd.DataFrame(
        {
            "agent_id": [f"A{i:05d}" for i in range(n)],
            "district": "Dhaka",
            "n_cashouts": 500,
            **{m: rng.normal(0.10, 0.02, n).clip(0) for m in METRICS},
        }
    )
    table["avg_cashout"] = rng.normal(3000, 300, n)
    return table


def test_agent_far_from_peers_on_several_metrics_ranks_first():
    table = _agent_rows()
    table.loc[7, ["fast_exit_share", "young_share", "flagged_customer_rate"]] = [0.8, 0.7, 0.3]
    scored = score_agents(table)
    top = scored.iloc[0]
    assert top["agent_id"] == "A00007" and top["risk"] > 5 * scored["risk"].iloc[1]
    assert set(top["reasons"]) == {"fast_exit_share", "young_share", "flagged_customer_rate"}
    assert scored["reasons"].iloc[-1] == []


def test_agent_with_little_history_is_not_scored():
    table = _agent_rows()
    table.loc[3, "n_cashouts"] = MIN_CASHOUTS - 1
    table.loc[3, "fast_exit_share"] = 1.0
    scored = score_agents(table).set_index("agent_id")
    assert scored.loc["A00003", "risk"] == 0 and not scored.loc["A00003", "eligible"]


# ---------------------------------------------------------------------- rings


def _send(engine, i, ts, s, r, device=""):
    channel = "app" if device else "ussd"
    engine.update(
        Txn(i, ts, "SEND_MONEY", s, "wallet", r, "wallet", 100.0, 1e6, device, channel, "Dhaka")
    )


def test_ring_links_shared_handsets_and_transfers_and_separates_takeover_victims():
    e = FeatureEngine()
    # M1 and M2 were opened on handset D1; M3 only receives from M1; U was opened on D1
    # but never scored; V has its own phone and later appears on D1 (taken over).
    _send(e, 0, 10, "M1", "X", "D1")
    _send(e, 1, 20, "M2", "X", "D1")
    _send(e, 2, 30, "M1", "M3")
    _send(e, 3, 40, "U", "Y", "D1")
    _send(e, 4, 50, "V", "Y", "DV")
    _send(e, 5, 60, "V", "M1", "D1")
    _send(e, 6, 70, "LONER", "Y", "DL")
    e.flag_wallet("M1", 80)
    rings = find_rings(e, {"M2": 0.9, "M3": 0.8, "LONER": 0.95})
    assert len(rings) == 1
    ring = rings[0]
    assert ring["wallets"] == ["M1", "M2", "M3", "U"] and ring["ring_id"] == "R001"
    assert ring["confirmed"] == ["M1"] and ring["linked_only"] == ["U"]
    assert ring["takeover_victims"] == ["V"]
    assert ring["shared_devices"] == ["D1"] and ring["transfer_links"] == 1


def test_small_groups_are_not_rings():
    e = FeatureEngine()
    _send(e, 0, 10, "M1", "M2")
    assert find_rings(e, {"M1": 0.9, "M2": 0.9}) == []


# ------------------------------------------------- the whole pipeline, small world


def test_folds_are_ordered_in_time_and_targets_are_consistent(trained):
    frame = load_frame(trained[0])
    ends = frame.groupby("fold")["ts"].agg(["min", "max"])
    assert ends.loc["train", "max"] < ends.loc["val_a", "min"]
    assert ends.loc["val_a", "max"] < ends.loc["val_b", "min"]
    assert ends.loc["val_b", "max"] < ends.loc["test", "min"]
    assert (frame.loc[frame["y_loss"] == 1, "y"] == 1).all()
    assert not frame.loc[frame["ambiguous"], "y"].any()
    assert (frame.loc[frame["y_mule"] == 1, "type"] == "SEND_MONEY").all()
    # Every victim transfer to a wallet lands on a wallet labelled as a mule at that time.
    to_wallet = frame[(frame["y_loss"] == 1) & (frame["type"] == "SEND_MONEY")]
    assert (to_wallet["y_mule"] == 1).all()


def test_training_writes_a_complete_version(trained):
    _, root, report = trained
    assert report["version"] == "v1" and registry.current_version(root) == "v1"
    files = {p.name for p in (root / "v1").iterdir()}
    assert files >= {"txn.lgb", "mule.lgb", "anomaly.joblib", "manifest.json", "report.json",
                     "rings.json", "agent_scores.parquet", "test_scores.parquet"}  # fmt: skip
    manifest = json.loads((root / "v1" / "manifest.json").read_text())
    assert manifest["features"] == list(FEATURES)
    t = manifest["thresholds"]
    assert 1 >= t["hold"] >= t["step_up"] >= t["warn"] > 0
    assert report["selection_on_val_b"]["served_score"] in ("txn", "fused")
    assert set(report["test"]["ablation_at_1.00%"]) == {
        "rules_baseline", "anomaly_only", "mule_model_only", "transaction_model", "fusion",
    }  # fmt: skip


def test_model_beats_the_rule_baseline_on_the_test_period(trained):
    ablation = trained[2]["test"]["ablation_at_1.00%"]
    assert ablation["transaction_model"]["pr_auc"] > 2 * ablation["rules_baseline"]["pr_auc"]


def test_saved_scores_are_reproduced_by_a_reloaded_bundle(trained):
    data_dir, root, _ = trained
    bundle = registry.load(root=root)
    frame = load_frame(data_dir)
    saved = pd.read_parquet(root / "v1" / "test_scores.parquet")
    rows = frame[frame["txn_id"].isin(saved["txn_id"])]
    scores = bundle.score(rows[list(FEATURES)].to_numpy())
    np.testing.assert_allclose(scores["risk"], saved["risk"].to_numpy(), rtol=1e-9)
    np.testing.assert_allclose(scores["mule"], saved["mule"].to_numpy(), rtol=1e-9)
    assert ((scores["risk"] >= 0) & (scores["risk"] <= 1)).all()
    # One row at a time (as the API scores) gives the same answer as the batch.
    one = bundle.score(rows[list(FEATURES)].to_numpy()[5])
    assert one["risk"][0] == pytest.approx(scores["risk"][5], rel=1e-9)


def test_contributions_add_up_to_the_model_output(trained):
    data_dir, root, _ = trained
    bundle = registry.load(root=root)
    x = load_frame(data_dir)[list(FEATURES)].to_numpy()[:200]
    txn_contrib, mule_contrib = bundle.contributions(x)
    assert txn_contrib.shape == (200, len(FEATURES))
    assert mule_contrib.shape == (200, len(RECIPIENT_FEATURES))
    full = bundle.txn.predict(x, pred_contrib=True)
    np.testing.assert_allclose(full.sum(axis=1), bundle.txn.predict(x, raw_score=True), atol=1e-6)
    np.testing.assert_array_equal(full[:, :-1], txn_contrib)
    is_cash_out = x[:, FEATURES.index("is_cash_out")] == 1
    assert is_cash_out.any() and not mule_contrib[is_cash_out].any()
    assert np.isnan(bundle.components(x)["mule"][is_cash_out]).all()


def test_recipient_risk_needs_only_recipient_features(trained):
    data_dir, root, _ = trained
    bundle = registry.load(root=root)
    frame = load_frame(data_dir)
    send = frame[frame["type"] == "SEND_MONEY"].head(50)
    direct = bundle.recipient_risk(send[list(RECIPIENT_FEATURES)].to_numpy())
    np.testing.assert_allclose(direct, bundle.score(send[list(FEATURES)].to_numpy())["mule"])


def test_registry_versions_and_promotion(trained, tmp_path):
    data_dir, root, _ = trained
    assert registry.versions(root) == ["v1"] and registry.next_version(root) == "v2"
    with pytest.raises(ValueError):
        registry.promote("v9", root)
    assert registry.versions(tmp_path) == [] and registry.next_version(tmp_path) == "v1"
    with pytest.raises(FileNotFoundError):
        registry.load(root=tmp_path)


def test_bundle_refuses_a_model_trained_on_other_features(trained, tmp_path):
    _, root, _ = trained
    copy = tmp_path / "v1"
    copy.mkdir()
    for f in (root / "v1").iterdir():
        if f.suffix in (".lgb", ".joblib"):
            (copy / f.name).write_bytes(f.read_bytes())
    manifest = json.loads((root / "v1" / "manifest.json").read_text())
    manifest["features"] = manifest["features"][:-1]
    (copy / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="different feature list"):
        ModelBundle.load(copy)
