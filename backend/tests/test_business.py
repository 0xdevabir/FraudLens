"""The business case: the threshold sweep priced in taka."""

import json

import pytest
from pydantic import ValidationError

from fraudlens.config import settings
from fraudlens.decision import business
from fraudlens.decision.business import ASSUMPTIONS, Assumptions, business_case, sensitivity
from fraudlens.models import registry


def _row(threshold, alert_rate, precision, recall, recall_exit):
    return {
        "threshold": threshold,
        "alert_rate": alert_rate,
        "precision": precision,
        "taka_recall": recall,
        "taka_recall_with_exit_holds": recall_exit,
    }


@pytest.fixture
def report():
    """1,000 payments, ৳10,000 of scam money; tiers at 0.1 / 0.5 / 0.9."""
    hold = _row(0.9, 0.004, 1.0, 0.5, 0.6)
    step_up = _row(0.5, 0.006, 0.8, 0.7, 0.8)
    warn = {**_row(0.1, 0.010, 0.6, 0.8, 0.9), "taka_at_risk": 10_000}
    return {
        "model_version": "v9",
        "policy_version": "v1",
        "rows": 1_000,
        "days": 10,
        "thresholds": {"warn": 0.1, "step_up": 0.5, "hold": 0.9},
        "at_thresholds": {"warn": warn, "step_up": step_up, "hold": hold},
        "impact": [_row(0.95, 0.002, 1.0, 0.3, 0.35), warn, _row(0.01, 0.05, 0.1, 0.9, 0.95)],
    }


def _plain(**changes) -> Assumptions:
    """Round numbers so the arithmetic can be checked by hand."""
    values = dict(
        monthly_payments=1_000_000,
        avg_payment_taka=1_000,
        scam_loss_bps=100,  # ৳10 a payment, the same as the report: scale 1
        stop_warn=0.2,
        stop_step_up=0.5,
        stop_hold=1.0,
        abandon_rate=0.1,
        abandon_cost_taka=10,
        contacts_warn=0.0,
        contacts_step_up=0.0,
        contacts_hold=1.0,
        contact_cost_taka=5,
        review_minutes=6,
        analyst_monthly_cost_taka=50_000,
        analyst_hours_per_month=100,
        min_analysts=0,
        platform_monthly_cost_taka=100_000,
        reimbursement_share=0.5,
        reputation_per_taka=0.0,
    )
    return Assumptions(**(values | changes))


def test_the_policy_point_adds_up_by_hand(report):
    case = business_case(report, _plain())
    p = case["policy"]
    assert case["synthetic"]["scale"] == 1.0 and case["scam_loss_at_risk"] == 10_000_000
    # Bands of recall: warn 0.8-0.7, step-up 0.7-0.5, hold 0.5; exit holds 0.6-0.5.
    assert p["prevented"] == {
        "warn": 200_000,
        "step_up": 1_000_000,
        "hold": 5_000_000,
        "mule_cash_out_held": 1_000_000,
    }
    assert p["prevented_total"] == 7_200_000 and p["missed"] == 2_800_000
    # Honest: warn band 0.004-0.0012, step-up 0.0012-0, hold 0 → 4,000 a month.
    assert p["honest_interrupted"] == 4_000 and p["honest_per_10k"] == 40
    assert p["interrupted"] == {"warn": 4_000, "step_up": 2_000, "hold": 4_000}
    assert p["friction"] == {"abandoned_payments": 4_000, "support_contacts": 20_000}
    # 4,000 holds x 6 minutes = 400 hours = 4 analysts.
    assert (p["review_hours"], p["analysts"], p["analyst_cost"]) == (400, 4, 200_000)
    assert p["operating_cost"] == 324_000
    assert p["net_benefit"] == 7_200_000 - 324_000
    assert p["provider_net_benefit"] == 3_600_000 - 324_000
    assert p["prevented_per_taka_cost"] == round(7_200_000 / 324_000, 2)


def test_tiers_collapse_when_the_slider_passes_them(report):
    case = business_case(report, _plain())
    strict = case["points"][0]  # above the hold threshold: everything alerted is held
    assert strict["prevented"]["warn"] == strict["prevented"]["step_up"] == 0
    assert strict["interrupted"]["hold"] == 2_000 == strict["interrupted_total"]
    assert case["points"][1]["prevented_total"] == case["policy"]["prevented_total"]


def test_a_rarer_scam_rescales_true_alerts_but_not_false_ones(report):
    dense, rare = (business_case(report, _plain(scam_loss_bps=b))["policy"] for b in (100, 10))
    assert rare["prevented_total"] == dense["prevented_total"] / 10
    assert rare["honest_interrupted"] == dense["honest_interrupted"]
    assert rare["interrupted"]["hold"] == 400  # 4,000 true holds, a tenth as many
    assert rare["analysts"] == 1


def test_coverage_floor_and_friction_move_the_net_as_expected(report):
    base = business_case(report, _plain())["policy"]
    floored = business_case(report, _plain(min_analysts=10))["policy"]
    assert floored["analysts"] == 10 and floored["net_benefit"] == base["net_benefit"] - 300_000
    sticky = business_case(report, _plain(abandon_rate=0.5))["policy"]
    assert sticky["friction"]["abandoned_payments"] == 20_000


def test_sensitivity_is_sorted_and_reimbursement_moves_only_the_provider(report):
    table = sensitivity(report, _plain())
    swings = [row["swing"] for row in table["rows"]]
    assert swings == sorted(swings, reverse=True)
    assert {row["key"] for row in table["rows"]} == set(ASSUMPTIONS)
    reimburse = next(r for r in table["rows"] if r["key"] == "reimbursement_share")
    assert reimburse["swing"] == 0 and reimburse["provider_swing"] > 0


def test_break_even_is_where_the_net_crosses_zero(report):
    a = _plain()
    bps = business.break_even_bps(report, a)
    assert bps is not None and 0 < bps < 100
    at = business_case(report, a.model_copy(update={"scam_loss_bps": bps * 1.01}))["policy"]
    below = business_case(report, a.model_copy(update={"scam_loss_bps": bps * 0.99}))["policy"]
    assert at["net_benefit"] >= 0 > below["net_benefit"]


def test_assumptions_are_bounded_and_strict():
    with pytest.raises(ValidationError):
        Assumptions(abandon_rate=1.5)
    with pytest.raises(ValidationError):
        Assumptions(monthly_payments=-1)
    with pytest.raises(ValidationError):
        Assumptions(wage=1)  # unknown fields are refused, not ignored
    for key, spec in ASSUMPTIONS.items():
        assert spec.low <= getattr(Assumptions(), key) <= spec.high, key
        assert spec.source, key
    assert set(ASSUMPTIONS) == set(Assumptions.model_fields)


def test_a_report_without_a_sweep_is_refused(report):
    with pytest.raises(ValueError):
        business_case({**report, "impact": []})


def test_on_the_served_model_the_policy_pays_for_itself():
    version = registry.current_version(settings.models_dir)
    path = settings.models_dir / str(version) / "insights.json"
    if not version or not path.exists():
        pytest.skip("no evaluated model in artifacts/")
    report = json.loads(path.read_text())
    case = business_case(report)
    p = case["policy"]
    assert len(case["points"]) == len(report["impact"])
    assert 0 < p["prevented_total"] <= case["scam_loss_at_risk"]
    assert p["net_benefit"] > 0 and p["prevented_per_taka_cost"] > 1
    assert p["analysts"] >= Assumptions().min_analysts
    # Honest payments interrupted per 10,000 is the back-test's false-alert rate.
    warn = report["at_thresholds"]["warn"]
    false_rate = warn["alert_rate"] * (1 - warn["precision"])
    assert p["honest_per_10k"] == pytest.approx(false_rate * 1e4, abs=0.01)
    prevented = [point["prevented_total"] for point in case["points"]]
    assert prevented == sorted(prevented)  # looser thresholds never stop less
    # The scam rate is the assumption that matters most, and the least known.
    assert case["sensitivity"]["rows"][0]["key"] == "scam_loss_bps"
