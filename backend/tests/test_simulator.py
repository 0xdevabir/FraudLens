import pandas as pd

from fraudlens.simulator.config import SimConfig
from fraudlens.simulator.engine import Simulation
from fraudlens.simulator.fraud import BASE_TYPOLOGIES, HELDOUT_TYPOLOGY
from fraudlens.simulator.generate import build_tables

LOSS_ROLES = ["victim_transfer", "ato_transfer"]


def test_transactions_are_chronological(small_tables):
    t = small_tables["transactions"]
    assert t["ts"].is_monotonic_increasing
    assert t["txn_id"].is_monotonic_increasing and t["txn_id"].is_unique


def test_no_wallet_overdraws(small_tables):
    t = small_tables["transactions"]
    from_wallet = t[t["sender_type"] == "wallet"]
    assert (from_wallet["sender_balance_before"] >= from_wallet["amount"]).all()


def test_wallet_transactions_respect_the_cap(small_tables, small_cfg):
    t = small_tables["transactions"]
    from_wallet = t[t["sender_type"] == "wallet"]
    assert from_wallet["amount"].max() <= small_cfg.txn_cap
    assert (t["amount"] > 0).all()


def test_heldout_typology_only_in_test_period(small_tables):
    t = small_tables["transactions"]
    heldout = t[t["typology"] == HELDOUT_TYPOLOGY]
    assert len(heldout) > 0
    assert set(heldout["split"]) == {"test"}


def test_every_base_typology_is_in_training_data(small_tables):
    t = small_tables["transactions"]
    train = t[(t["split"] == "train") & t["fraud_role"].isin(LOSS_ROLES)]
    assert set(BASE_TYPOLOGIES) <= set(train["typology"])


def test_fraud_rows_belong_to_a_case(small_tables):
    t, cases = small_tables["transactions"], small_tables["cases"]
    fraud = t[t["is_fraud"]]
    assert (fraud["case_id"] >= 0).all()
    assert set(fraud["case_id"]) <= set(cases["case_id"])
    # Booked case loss equals the sum of the victim's transfers.
    booked = t[t["fraud_role"].isin(LOSS_ROLES)].groupby("case_id")["amount"].sum()
    merged = cases.set_index("case_id")["loss"]
    pd.testing.assert_series_equal(
        booked.sort_index(), merged.sort_index(), check_names=False, check_dtype=False
    )


def test_bait_and_agent_abuse_are_not_labelled_fraud(small_tables):
    t = small_tables["transactions"]
    assert not t.loc[t["fraud_role"].isin(["bait", "agent_abuse"]), "is_fraud"].any()


def test_wallets_do_not_transact_before_creation(small_tables):
    t, w = small_tables["transactions"], small_tables["wallets"]
    created = w.set_index("wallet_id")["created_at"]
    for side in ("sender", "receiver"):
        rows = t[t[f"{side}_type"] == "wallet"]
        assert (rows["ts"] >= rows[f"{side}_id"].map(created)).all()


def test_flagged_wallets_are_blocked_afterwards(small_tables):
    t, flags = small_tables["transactions"], small_tables["wallet_flags"]
    assert len(flags) > 0
    flagged_at = flags.set_index("wallet_id")["flagged_at"]
    for side in ("sender", "receiver"):
        rows = t[t[f"{side}_id"].isin(flagged_at.index)]
        # Cash-in and add-money are initiated by the agent or bank, never by the blocked wallet.
        if side == "receiver":
            rows = rows[rows["type"] == "SEND_MONEY"]
        assert (rows["ts"] < rows[f"{side}_id"].map(flagged_at)).all()


def test_mules_are_labelled_only_if_they_handled_fraud_money(small_tables):
    t, w = small_tables["transactions"], small_tables["wallets"]
    mules = set(w.loc[w["is_mule"], "wallet_id"])
    received = set(t.loc[t["is_fraud"] & (t["receiver_type"] == "wallet"), "receiver_id"])
    assert mules == received


def test_same_seed_gives_identical_data(small_tables):
    sim = Simulation(SimConfig.small())
    sim.run()
    again = build_tables(sim)["transactions"]
    pd.testing.assert_frame_equal(again, small_tables["transactions"])
