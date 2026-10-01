import math

import numpy as np
import pytest

from fraudlens.features import FEATURES, RECIPIENT_FEATURES, SCORED_TYPES, FeatureEngine, Txn
from fraudlens.features.build import LABEL_COLUMNS, Replayer, build_features, iter_txns, new_engine

HOUR, DAY = 3600.0, 86_400.0
T0 = 1_767_225_600.0  # 2026-01-01 00:00:00


def make_txn(i, ts, typ, s, r, amount, bal=50_000.0, device="", district="Dhaka"):
    kinds = {
        "SEND_MONEY": ("wallet", "wallet"),
        "CASH_OUT": ("wallet", "agent"),
        "CASH_IN": ("agent", "wallet"),
        "PAYMENT": ("wallet", "merchant"),
    }[typ]
    channel = "agent" if typ == "CASH_IN" else ("app" if device else "ussd")
    return Txn(i, T0 + ts, typ, s, kinds[0], r, kinds[1], amount, bal, device, channel, district)


def feats(engine, t):
    return dict(zip(FEATURES, engine.features(t), strict=True))


def run(engine, txns):
    """Feature dict for every scored transaction, computing before updating."""
    out = []
    for t in txns:
        if t.type in SCORED_TYPES:
            out.append(feats(engine, t))
        engine.update(t)
    return out


# ------------------------------------------------------------ hand-built scenarios


def test_first_transaction_sees_no_history_of_itself():
    f = feats(FeatureEngine(), make_txn(0, 100, "SEND_MONEY", "A", "B", 500, bal=1000))
    assert f["s_n_money_out"] == 0 and f["s_cnt_24h"] == 0 and f["s_sum_24h"] == 0
    assert f["pair_prior_count"] == 0 and f["r_in_cnt_24h"] == 0 and f["r_fan_in_7d"] == 0
    assert math.isnan(f["s_amount_z"]) and math.isnan(f["r_dwell_secs"])
    assert f["amount_to_balance"] == 0.5 and f["balance_after"] == 500


def test_recipient_fan_in_counts_distinct_senders_within_windows():
    e = FeatureEngine()
    run(e, [make_txn(i, i * HOUR, "SEND_MONEY", f"S{i}", "R", 1000) for i in range(3)])
    run(e, [make_txn(3, 3 * HOUR, "SEND_MONEY", "S0", "R", 1000)])  # repeat sender
    f = feats(e, make_txn(4, 4 * HOUR, "SEND_MONEY", "X", "R", 1000))
    assert f["r_fan_in_24h"] == 3 and f["r_fan_in_7d"] == 3
    assert f["r_in_cnt_24h"] == 4 and f["r_in_sum_24h"] == 4000
    assert f["r_new_sender_share_7d"] == 0.75
    later = feats(e, make_txn(5, 2 * DAY, "SEND_MONEY", "X", "R", 1000))
    assert later["r_fan_in_24h"] == 0 and later["r_fan_in_7d"] == 3
    gone = feats(e, make_txn(6, 9 * DAY, "SEND_MONEY", "X", "R", 1000))
    assert gone["r_fan_in_7d"] == 0 and gone["r_in_cnt_7d"] == 0


def test_pair_history_and_reciprocity():
    e = FeatureEngine()
    run(
        e,
        [make_txn(0, 0, "SEND_MONEY", "A", "B", 100), make_txn(1, 60, "SEND_MONEY", "A", "B", 100)],
    )
    f = feats(e, make_txn(2, 120, "SEND_MONEY", "A", "B", 100))
    assert f["pair_prior_count"] == 2 and f["pair_reverse_count"] == 0
    back = feats(e, make_txn(3, 180, "SEND_MONEY", "B", "A", 100))
    assert back["pair_prior_count"] == 0 and back["pair_reverse_count"] == 2
    run(e, [make_txn(3, 180, "SEND_MONEY", "B", "A", 100)])
    # B has one sender (A) and has paid A back: fully reciprocal.
    assert feats(e, make_txn(4, 240, "SEND_MONEY", "C", "B", 100))["r_reciprocity"] == 1.0


def test_sender_amount_deviation_uses_own_history():
    e = FeatureEngine()
    run(e, [make_txn(i, i * DAY, "SEND_MONEY", "A", "B", 500) for i in range(5)])
    usual = feats(e, make_txn(5, 6 * DAY, "SEND_MONEY", "A", "B", 500))
    big = feats(e, make_txn(5, 6 * DAY, "SEND_MONEY", "A", "Z", 20_000))
    assert usual["s_amount_z"] == 0 and usual["s_amount_vs_max"] == 1
    assert big["s_amount_z"] > 50 and big["s_amount_vs_max"] == 40
    assert big["s_new_recipients_24h"] == 0 and big["s_n_money_out"] == 5


def test_device_features():
    e = FeatureEngine()
    first = make_txn(0, 0, "SEND_MONEY", "A", "B", 100, device="D1")
    assert feats(e, first)["s_new_device"] == 0  # nothing to compare a first device against
    run(e, [first])
    same = feats(e, make_txn(1, 600, "SEND_MONEY", "A", "B", 100, device="D1"))
    assert same["s_new_device"] == 0 and same["s_device_age_secs"] == 600
    new = feats(e, make_txn(2, 900, "SEND_MONEY", "A", "B", 100, device="D2"))
    assert new["s_new_device"] == 1 and new["s_device_age_secs"] == 0
    # A second wallet on A's handset: the device is shared.
    shared = feats(e, make_txn(3, 950, "SEND_MONEY", "C", "B", 100, device="D1"))
    assert shared["s_device_other_wallets"] == 1 and shared["s_device_flagged"] == 0
    e.flag_wallet("A", T0 + 960)
    assert feats(e, make_txn(4, 970, "SEND_MONEY", "C", "B", 100, device="D1"))["s_device_flagged"]


def test_links_to_flagged_wallets():
    e = FeatureEngine()
    run(e, [make_txn(0, 0, "SEND_MONEY", "A", "MULE", 100)])
    before = feats(e, make_txn(1, 60, "SEND_MONEY", "X", "A", 100))
    assert before["r_flagged_neighbors"] == 0
    e.flag_wallet("MULE", T0 + 100)
    e.flag_wallet("MULE", T0 + 200)  # repeated flag must not double count
    assert feats(e, make_txn(2, 300, "SEND_MONEY", "A", "B", 100))["s_flagged_neighbors"] == 1
    assert feats(e, make_txn(3, 300, "SEND_MONEY", "X", "A", 100))["r_flagged_neighbors"] == 1
    # An edge created after the flag is linked too, once.
    run(e, [make_txn(4, 400, "SEND_MONEY", "C", "MULE", 100)] * 2)
    assert feats(e, make_txn(5, 500, "SEND_MONEY", "C", "B", 100))["s_flagged_neighbors"] == 1


def test_dwell_time_and_pass_through():
    e = FeatureEngine()
    run(
        e,
        [
            make_txn(0, 0, "SEND_MONEY", "V", "R", 5000),
            make_txn(1, 600, "CASH_OUT", "R", "AG1", 4900, bal=5000),
        ],
    )
    f = feats(e, make_txn(2, 700, "SEND_MONEY", "V2", "R", 3000))
    assert f["r_dwell_secs"] == 600 and f["r_fast_exit_share"] == 1.0
    assert f["r_cashout_share"] == pytest.approx(4900 / 5001)
    assert f["r_out_in_ratio_24h"] == pytest.approx(4900 / 5001)
    out = feats(e, make_txn(3, 800, "CASH_OUT", "R", "AG1", 100, bal=100))
    assert out["s_secs_since_last_in"] == 800 and out["s_fan_in_7d"] == 1
    assert out["pair_prior_count"] == 1  # R has used this agent once


def test_funding_just_before_sending():
    e = FeatureEngine()
    run(e, [make_txn(0, 0, "CASH_IN", "AG1", "A", 4000)])
    f = feats(e, make_txn(1, 300, "SEND_MONEY", "A", "B", 4000, bal=4100))
    assert f["s_secs_since_funding"] == 300 and f["s_funded_share_1h"] == 1.0
    late = feats(e, make_txn(1, 2 * HOUR, "SEND_MONEY", "A", "B", 4000, bal=4100))
    assert late["s_funded_share_1h"] == 0


def test_agent_features_need_history_and_count_risky_customers():
    e = FeatureEngine()
    e.register_wallet("YOUNG", T0 - 2 * DAY, "Sylhet")
    e.register_agent("AG1", "Dhaka")
    cash_out = make_txn(0, 2 * HOUR, "CASH_OUT", "YOUNG", "AG1", 100, bal=10_000_000)
    f = feats(e, cash_out)
    assert f["a_n_cashouts"] == 0 and math.isnan(f["a_young_share"])
    assert all(math.isnan(f[name]) for name in RECIPIENT_FEATURES)
    run(e, [cash_out] * 20)
    f = feats(e, cash_out)
    assert f["a_n_cashouts"] == 20 and f["a_young_share"] == 1.0
    assert f["a_out_district_share"] == 1.0 and f["a_night_share"] == 1.0
    assert f["a_flagged_customers"] == 0
    e.flag_wallet("YOUNG", T0 + 3 * HOUR)
    assert feats(e, cash_out)["a_flagged_customers"] == 1


def test_unscored_types_are_rejected_but_update_state():
    e = FeatureEngine()
    payment = make_txn(0, 0, "PAYMENT", "A", "M1", 100)
    with pytest.raises(ValueError):
        e.features(payment)
    e.update(payment)
    f = feats(e, make_txn(1, 90, "SEND_MONEY", "A", "B", 100))
    assert f["s_secs_since_last_txn"] == 90 and f["s_n_money_out"] == 0


def test_features_are_read_only():
    e = FeatureEngine()
    run(e, [make_txn(i, i * 500, "SEND_MONEY", f"S{i % 3}", "R", 100 + i) for i in range(12)])
    t = make_txn(99, 7000, "SEND_MONEY", "S1", "R", 900)
    first, second = e.features(t), e.features(t)
    np.testing.assert_array_equal(first, second)
    assert len(first) == len(FEATURES) == len(set(FEATURES))


# -------------------------------------------------------------- on simulated data


@pytest.fixture(scope="module")
def features(small_tables):
    return build_features(small_tables)


def _matrix(df):
    return df[list(FEATURES)].to_numpy()


def _replay(tables, txns, flags=None):
    replayer = Replayer(
        new_engine(tables["wallets"], tables["agents"]),
        tables["wallet_flags"] if flags is None else flags,
    )
    return replayer, replayer.run(txns)[1]


def test_one_row_per_scored_transaction(features, small_tables):
    t = small_tables["transactions"]
    scored = t[t["type"].isin(SCORED_TYPES)]
    assert features["txn_id"].tolist() == scored["txn_id"].tolist()
    assert list(features.columns) == list(LABEL_COLUMNS) + list(FEATURES)
    x = _matrix(features)
    assert not np.isinf(x).any()
    # Recipient features exist exactly for wallet-to-wallet transfers.
    is_send = (features["type"] == "SEND_MONEY").to_numpy()
    assert not np.isnan(features.loc[is_send, "r_age_days"]).any()
    assert np.isnan(features.loc[~is_send, "r_age_days"]).all()
    assert (features["s_age_days"] >= 0).all()
    assert (features.loc[is_send, "r_age_days"] >= 0).all()


def test_engine_never_receives_labels(small_tables):
    labels = {"is_fraud", "fraud_role", "typology", "case_id", "cell_id", "split", "day"}
    assert not labels & set(Txn._fields)
    assert not labels & set(FEATURES)
    first = next(iter_txns(small_tables["transactions"]))
    assert isinstance(first, Txn) and len(first) == len(Txn._fields)


def test_no_leakage_from_the_future(features, small_tables):
    """Features computed with the future cut off equal those from the full replay."""
    t = small_tables["transactions"]
    for share in (0.25, 0.6):
        cut = int(len(t) * share)
        _, x = _replay(small_tables, t.iloc[:cut])
        full = _matrix(features[features["txn_id"] < t["txn_id"].iloc[cut]])
        np.testing.assert_array_equal(x, full)


def test_flags_only_affect_transactions_after_they_are_raised(features, small_tables):
    t, flags = small_tables["transactions"], small_tables["wallet_flags"]
    _, without = _replay(small_tables, t, flags=flags.iloc[:0])
    full = _matrix(features)
    before = (features["ts"] < flags["flagged_at"].min()).to_numpy()
    assert before.any() and not before.all()
    np.testing.assert_array_equal(without[before], full[before])
    changed = ~np.all((without == full) | (np.isnan(without) & np.isnan(full)), axis=1)
    assert changed.any() and not changed[before].any()


def test_snapshot_restore_gives_identical_online_features(features, small_tables, tmp_path):
    """Offline batch replay and a restored engine scoring one event at a time agree exactly."""
    t = small_tables["transactions"]
    cut = len(t) // 2
    replayer, _ = _replay(small_tables, t.iloc[:cut])
    path = tmp_path / "engine.pkl"
    replayer.engine.save(path)
    engine = FeatureEngine.load(path)
    assert engine.last_ts == replayer.engine.last_ts
    assert len(engine.wallets) == len(replayer.engine.wallets)

    live = Replayer(engine, small_tables["wallet_flags"])
    rows = [live.run(t.iloc[i : i + 1])[1] for i in range(cut, len(t))]
    online = np.concatenate(rows)
    offline = _matrix(features[features["txn_id"] >= t["txn_id"].iloc[cut]])
    np.testing.assert_array_equal(online, offline)


def test_snapshot_load_rejects_other_objects(tmp_path):
    import pickle

    path = tmp_path / "bad.pkl"
    path.write_bytes(pickle.dumps({"not": "an engine"}))
    with pytest.raises(TypeError):
        FeatureEngine.load(path)


def test_scam_transfers_look_different_from_normal_ones(features):
    """Sanity check that the signal the product relies on is present in the data."""
    send = features[features["type"] == "SEND_MONEY"]
    scam = send[send["fraud_role"] == "victim_transfer"]
    normal = send[~send["is_fraud"] & (send["fraud_role"] == "")]
    assert len(scam) > 20
    # Mule wallets are young and empty fast. Fan-in is deliberately not asserted:
    # mules are rotated after a few victims, while honest sellers have far more senders.
    assert scam["r_age_days"].median() < normal["r_age_days"].median() / 5
    assert scam["r_dwell_secs"].median() < normal["r_dwell_secs"].median() / 3
    assert scam["amount_to_balance"].median() > normal["amount_to_balance"].median()
    takeover = features[features["fraud_role"] == "ato_transfer"]
    assert takeover["s_new_device"].mean() > 20 * normal["s_new_device"].mean()
