"""Segment thresholds for young wallets: the guards, the decision path and the fit."""

import json

import numpy as np
import pandas as pd
import pytest
import yaml

from fraudlens.decision import PolicyError, apply_policy, load_policy
from fraudlens.decision import mitigation as mit
from fraudlens.decision.policy import POLICY_DIR, Policy

BASE = {"warn": 0.002, "step_up": 0.008, "hold": 0.05}
MANIFEST = {"thresholds": BASE}


def _segment(**changes) -> dict:
    base = {
        "id": "S01_YOUNG_RECEIVER",
        "description": "test segment",
        "applies_to": ["SEND_MONEY"],
        "when": [{"field": "r_age_days", "op": "<", "value": 30}],
        "scale": {"warn": 10, "step_up": 2, "hold": 2},
    }
    return base | changes


def _policy(*segments: dict) -> Policy:
    raw = yaml.safe_load((POLICY_DIR / "v2.yaml").read_text(encoding="utf-8"))
    return Policy.model_validate(raw | {"segments": list(segments)})


# ------------------------------------------------------------------ the guards


@pytest.mark.parametrize(
    "scale",
    [
        {"warn": 10, "step_up": 2, "hold": 0.1},  # hold below warn
    ],
)
def test_an_unsafe_segment_is_refused_when_the_thresholds_are_resolved(scale):
    policy = _policy(_segment(scale=scale))
    with pytest.raises(PolicyError, match="segment S01_YOUNG_RECEIVER: thresholds must satisfy"):
        policy.resolve_thresholds(MANIFEST)


def test_a_payment_held_for_anyone_is_at_least_warned_in_every_segment():
    # warn x30 would be 0.06, above the policy's hold cut-off: capped there, and
    # step-up kept between warn and the segment's hold.
    segment = _policy(_segment(scale={"warn": 30, "step_up": 1, "hold": 3})).segments[0]
    assert segment.thresholds(BASE) == pytest.approx({"warn": 0.05, "step_up": 0.05, "hold": 0.15})
    # A hold scale that would pass 1 keeps 1/scale of the room below 1 instead.
    high = {"warn": 0.99, "step_up": 0.995, "hold": 0.999}
    assert segment.thresholds(high)["hold"] == pytest.approx(1 - 0.001 / 3)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"scale": {"warn": 10, "hold": 2}}, "scale needs each of"),
        ({"scale": {"warn": 0, "step_up": 2, "hold": 2}}, "positive"),
        ({"applies_to": ["PAY_BILL"]}, "unknown transaction types"),
        ({"when": []}, "at least 1"),
        ({"id": "lower"}, "pattern"),
    ],
)
def test_segment_validation_rejects_bad_entries(changes, message):
    with pytest.raises(ValueError, match=message):
        _policy(_segment(**changes))


def test_segment_ids_must_be_unique():
    with pytest.raises(ValueError, match="segment"):
        _policy(_segment(), _segment())


# ------------------------------------------------------------ the decision path

YOUNG = {"r_age_days": 3.0, "s_age_days": 400.0, "recipient_flagged": 0.0, "sender_flagged": 0.0}


def test_a_segment_applies_its_own_cut_offs_and_says_so():
    policy = _policy(_segment())
    thresholds = policy.resolve_thresholds(MANIFEST)
    risk = 0.01  # step-up for anyone; below this segment's warn cut-off (0.02)
    plain = apply_policy(policy, "SEND_MONEY", YOUNG | {"r_age_days": 300.0}, risk, thresholds)
    young = apply_policy(policy, "SEND_MONEY", YOUNG, risk, thresholds)
    assert (plain.tier, plain.segment) == ("step_up", None)
    assert young.tier == "allow" and young.segment.id == "S01_YOUNG_RECEIVER"
    assert young.thresholds["warn"] == pytest.approx(0.02)
    # A cash-out has no receiving wallet: the segment does not apply.
    assert apply_policy(policy, "CASH_OUT", YOUNG, risk, thresholds).segment is None


def test_the_softer_tier_steps_up_instead_of_holding_but_still_holds_above_it():
    policy = _policy(_segment())  # hold cut-off 0.1 for the segment, 0.05 for anyone
    thresholds = policy.resolve_thresholds(MANIFEST)
    assert apply_policy(policy, "SEND_MONEY", YOUNG, 0.07, thresholds).tier == "step_up"
    assert apply_policy(policy, "SEND_MONEY", YOUNG, 0.2, thresholds).tier == "hold"


def test_the_first_matching_segment_decides():
    second = _segment(id="S02_YOUNG_SENDER", applies_to=["SEND_MONEY", "CASH_OUT"])
    second["when"] = [{"field": "s_age_days", "op": "<", "value": 30}]
    policy = _policy(_segment(), second)
    both = YOUNG | {"s_age_days": 2.0}
    assert policy.segment_for("SEND_MONEY", both).id == "S01_YOUNG_RECEIVER"
    assert policy.segment_for("CASH_OUT", both).id == "S02_YOUNG_SENDER"
    assert policy.segment_for("SEND_MONEY", YOUNG | {"r_age_days": float("nan")}) is None


def test_hard_rules_still_hold_inside_a_segment():
    policy = _policy(_segment())
    thresholds = policy.resolve_thresholds(MANIFEST)
    flagged = YOUNG | {"recipient_flagged": 1.0}
    outcome = apply_policy(policy, "SEND_MONEY", flagged, 0.0001, thresholds)
    assert outcome.tier == "hold" and outcome.segment is not None
    assert policy.tiers["hold"].human_review


def test_rules_only_mode_ignores_segments():
    policy = _policy(_segment())
    outcome = apply_policy(policy, "SEND_MONEY", YOUNG, None, None)
    assert outcome.mode == "rules_only" and outcome.segment is None


def test_v3_is_v2_plus_segments_and_resolves_against_the_served_model_kind():
    v2, v3 = load_policy("v2"), load_policy("v3")
    assert v3.version == "v3" and [s.id for s in v3.segments] == [
        "S01_YOUNG_RECEIVER",
        "S02_YOUNG_SENDER",
    ]
    assert v3.rules == v2.rules and v3.tiers == v2.tiers and not v2.segments
    for segment in v3.segments:  # admissible for the thresholds it was fitted on
        assert segment.fitted
        segment.thresholds(BASE | {"warn": 0.0019854631219750926, "hold": 0.04978453405976952})


def test_v3_decisions_record_the_segment(world_v3):
    rows = world_v3.frame
    young = rows[(rows["type"] == "SEND_MONEY") & (rows["r_age_days"] < 30)].iloc[0]
    decision = world_v3.decide(young)
    assert decision.segment["id"] == "S01_YOUNG_RECEIVER"
    assert set(decision.segment["thresholds"]) == {"warn", "step_up", "hold"}
    assert decision.policy_version == "v3"


@pytest.fixture(scope="module")
def world_v3(trained):
    from types import SimpleNamespace

    from fraudlens.decision import DecisionEngine
    from fraudlens.decision import evaluate as policy_eval
    from fraudlens.features import FEATURES
    from fraudlens.features.build import iter_txns
    from fraudlens.models import registry
    from fraudlens.models.data import load_frame

    data_dir, root, _ = trained
    bundle = registry.load(root=root)
    frame = policy_eval.add_context(
        load_frame(data_dir), pd.read_parquet(data_dir / "wallet_flags.parquet")
    )
    txns = pd.read_parquet(data_dir / "transactions.parquet").set_index("txn_id")
    engine = DecisionEngine(load_policy("v3"), bundle)

    def decide(row):
        txn = next(iter_txns(txns.loc[[row["txn_id"]]].reset_index()))
        context = {k: row[k] for k in policy_eval.CONTEXT_COLUMNS}
        return engine.decide(txn, row[list(FEATURES)].to_numpy(float), context)

    return SimpleNamespace(frame=frame, decide=decide)


# --------------------------------------------------------------------- the fit


def test_scales_keep_step_up_between_warn_and_hold_and_respect_the_guard():
    for warn in mit.WARN_SCALES:
        for hold in mit.HOLD_SCALES:
            scale = mit.scales(BASE, warn, hold)
            t = {k: BASE[k] * v for k, v in scale.items()}
            if mit.admissible(BASE, scale):
                assert t["warn"] <= t["step_up"] <= t["hold"] <= BASE["hold"] * hold
                assert t["warn"] <= BASE["hold"]
            else:
                assert t["warn"] > BASE["hold"] or t["hold"] >= 1


def test_the_fit_picks_the_smallest_gap_within_the_recall_budget():
    # Two options per segment, one segment: base (1, 1) and a cheaper but lossy one.
    options = [(1, 1), (2, 1), (4, 1)]
    names = ["largest_young_ratio_ref", "summed_young_ratio_ref", "largest_young_hold_ratio"]
    fit = {
        "largest_young_ratio_ref": np.array([10.0, 5.0, 2.0]),
        "summed_young_ratio_ref": np.array([12.0, 6.0, 3.0]),
        "largest_young_hold_ratio": np.array([3.0, 3.0, 3.0]),
        "victim_recall": np.array([0.9, 0.898, 0.8]),  # (4, 1) loses 10 points
        "taka_recall": np.array([0.9, 0.9, 0.9]),
        "victim_recall_hold": np.array([0.8, 0.8, 0.8]),
    }
    for key in mit._FRONTIER_KEYS:
        fit.setdefault(key, np.zeros(3))
    assert set(names) <= set(fit)
    out = mit.fit(fit, fit, options, BASE, ["S01"])
    assert out["chosen"]["scales"] == {"S01": {"warn": 2, "hold": 1}}
    assert out["base"]["scales"] == {"S01": {"warn": 1, "hold": 1}}
    assert out["meeting_constraints"] == 2


def test_metrics_measure_young_wallet_rates_against_the_right_rows():
    rows = pd.DataFrame(
        {
            "type": ["SEND_MONEY", "SEND_MONEY", "CASH_OUT", "SEND_MONEY"],
            "s_age_days": [400.0, 400.0, 5.0, 400.0],
            "r_age_days": [5.0, 400.0, np.nan, 400.0],
            "y": [0, 0, 0, 1],
            "y_loss": [0, 0, 0, 1],
            "amount": [100.0, 100.0, 100.0, 500.0],
        }
    )
    m = mit._metrics(mit._sums(mit._columns(rows, np.array([1, 0, 1, 3]))))
    assert m["false_alert_rate"] == pytest.approx(2 / 3)
    assert m["receiver_young_false_alert_rate"] == 1.0
    assert m["sender_young_false_alert_rate"] == 1.0
    assert m["reference_false_alert_rate"] == 0.0
    assert m["victim_recall"] == m["victim_recall_hold"] == m["taka_recall"] == 1.0


def test_the_report_runs_end_to_end_and_the_engine_agrees_with_the_grid(trained):
    data_dir, root, _ = trained
    report = mit.run(data_dir, "v3", models_root=root, reps=20)
    assert report["engine_matches_grid"]
    assert report["policy_version"] == "v3" and report["fit"]["chosen"]["admissible"]
    keys = {m["key"] for m in report["before_after"]["metrics"]}
    assert {"receiver_young_ratio", "sender_young_ratio", "victim_recall"} <= keys
    assert set(report["intersectional"]) == {"sender", "receiver"}
    written = json.loads((root / report["model_version"] / mit.MITIGATION_FILE).read_text())
    assert written["policy_version"] == "v3"
