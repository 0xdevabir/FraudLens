"""The threshold sweep turned into a monthly profit and loss in taka.

`insights.py` says what a threshold does on the test period: alerts, false alerts,
victims' money stopped. This module puts a price on each of those at a provider's
own volume, so the question "is it worth running, and with how many people?" has a
number for every threshold in the sweep:

- money kept from scammers: the scam money alerted in each tier, times the share
  that tier actually stops (a warning is often clicked through; a hold rarely is);
- friction: honest payments abandoned after an interruption, and the calls to the
  contact centre that interruptions cause;
- review: analyst time for every held payment, with a floor for round-the-clock cover;
- what is left: net benefit a month, prevented loss per taka of operating cost,
  analysts needed, honest customers interrupted per 10,000 payments.

Every assumption is a field of `Assumptions`, with its source or the reasoning
behind it in `ASSUMPTIONS`. The model numbers come from `insights.json` only, so
nothing is re-scored. Rates per payment are taken from the back-test and multiplied
by the provider's volume; the scam money and the true alerts are rescaled from the
simulator's scam rate (about 93 basis points of payment value, far denser than real
traffic) to the assumed real one. False alerts are not rescaled: honest customers
look the same whatever the scam rate.

    uv run python -m fraudlens.decision.business
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from ..config import settings
from ..models import registry
from .insights import INSIGHTS_FILE

TIERS = ("warn", "step_up", "hold")


class Assumptions(BaseModel):
    """What the business case assumes. Every field can be overridden; bounds keep it sane."""

    model_config = ConfigDict(extra="forbid")

    monthly_payments: float = Field(20_000_000, ge=10_000, le=2_000_000_000)
    avg_payment_taka: float = Field(2_800, ge=100, le=25_000)
    scam_loss_bps: float = Field(2.0, ge=0.01, le=100)
    stop_warn: float = Field(0.25, ge=0, le=1)
    stop_step_up: float = Field(0.50, ge=0, le=1)
    stop_hold: float = Field(0.90, ge=0, le=1)
    abandon_rate: float = Field(0.05, ge=0, le=1)
    abandon_cost_taka: float = Field(25, ge=0, le=5_000)
    contacts_warn: float = Field(0.02, ge=0, le=5)
    contacts_step_up: float = Field(0.10, ge=0, le=5)
    contacts_hold: float = Field(0.25, ge=0, le=5)
    contact_cost_taka: float = Field(50, ge=0, le=5_000)
    review_minutes: float = Field(8, ge=0.5, le=240)
    analyst_monthly_cost_taka: float = Field(60_000, ge=1_000, le=2_000_000)
    analyst_hours_per_month: float = Field(132, ge=20, le=250)
    min_analysts: int = Field(5, ge=0, le=500)
    platform_monthly_cost_taka: float = Field(500_000, ge=0, le=1_000_000_000)
    reimbursement_share: float = Field(0.25, ge=0, le=1)
    reputation_per_taka: float = Field(0.25, ge=0, le=10)


@dataclass(frozen=True)
class Spec:
    label: str
    unit: str
    low: float  # the range the sensitivity table tries
    high: float
    source: str


ASSUMPTIONS: dict[str, Spec] = {
    "monthly_payments": Spec(
        "Send-money and cash-out payments a month",
        "payments",
        10_000_000,
        40_000_000,
        "Order of magnitude for a mid-sized provider (upay scale). Bangladesh Bank's monthly "
        "MFS statistics put the whole industry in the hundreds of millions of transactions a "
        "month; a bKash-scale provider is roughly 300 million scored payments. Replace with "
        "the provider's own MIS figure.",
    ),
    "avg_payment_taka": Spec(
        "Average payment",
        "BDT",
        1_500,
        4_000,
        "The simulator's scored payments average ৳2,830 (send-money ৳2,247, cash-out "
        "৳3,990); rounded down. Bangladesh Bank's industry value-per-transaction is in the "
        "same range.",
    ),
    "scam_loss_bps": Spec(
        "Scam losses, share of payment value",
        "basis points",
        0.5,
        8,
        "Assumed. The simulator runs at 93 bp, deliberately dense so the model has cases to "
        "learn from. UK push-payment scam losses are about 1-2 bp of Faster Payments value "
        "(UK Finance, Pay.UK); MFS in Bangladesh is assumed more exposed. No public "
        "Bangladeshi figure exists, so this is the assumption to replace first.",
    ),
    "stop_warn": Spec(
        "Scam money stopped by a warning",
        "share",
        0.10,
        0.50,
        "Assumed. A coached victim often clicks through a warning; studies of payment "
        "warnings report partial effect. The customer decides, so the share is low.",
    ),
    "stop_step_up": Spec(
        "Scam money stopped by a step-up check",
        "share",
        0.30,
        0.75,
        "Assumed. Re-authentication defeats most account takeovers, and the 30-minute "
        "cooling-off breaks the urgency a scam script relies on; a determined victim still pays.",
    ),
    "stop_hold": Spec(
        "Scam money stopped by a hold",
        "share",
        0.75,
        0.98,
        "Assumed. The money stays in the wallet while an analyst calls the customer; some "
        "victims insist. Also applied to mule cash-outs held after the victim's payment.",
    ),
    "abandon_rate": Spec(
        "Honest payments abandoned after an interruption",
        "share",
        0.01,
        0.15,
        "Assumed. Most honest customers read the warning and continue; a step-up or a hold "
        "loses some of them to another channel.",
    ),
    "abandon_cost_taka": Spec(
        "Cost of one abandoned honest payment",
        "BDT",
        10,
        60,
        "Lost fee revenue and goodwill: cash-out pays about 1.85% (৳74 on ৳4,000), "
        "app send-money ৳0-5; many abandoned payments are retried later.",
    ),
    "contacts_warn": Spec(
        "Support contacts per warning",
        "contacts",
        0.0,
        0.05,
        "Assumed: a warning rarely leads to a call.",
    ),
    "contacts_step_up": Spec(
        "Support contacts per step-up check",
        "contacts",
        0.05,
        0.25,
        "Assumed: a paused payment brings some customers to the helpline.",
    ),
    "contacts_hold": Spec(
        "Support contacts per hold",
        "contacts",
        0.10,
        0.50,
        "Assumed: the analyst calls the customer anyway (counted as review time); this is "
        "the extra inbound calls.",
    ),
    "contact_cost_taka": Spec(
        "Cost of one support contact",
        "BDT",
        25,
        100,
        "About six minutes of a contact-centre agent at roughly ৳35,000 a month loaded, "
        "plus telephony.",
    ),
    "review_minutes": Spec(
        "Analyst minutes per held payment",
        "minutes",
        4,
        20,
        "The impact page's default: read the case note, call the customer, decide.",
    ),
    "analyst_monthly_cost_taka": Spec(
        "Analyst cost a month, loaded",
        "BDT",
        40_000,
        100_000,
        "A fraud-operations analyst in Dhaka at about ৳45,000 a month, plus a third for "
        "benefits, seat and supervision.",
    ),
    "analyst_hours_per_month": Spec(
        "Hours of case work per analyst a month",
        "hours",
        110,
        150,
        "Six hours of case work in an eight-hour shift, 22 shifts a month.",
    ),
    "min_analysts": Spec(
        "Minimum analysts for round-the-clock cover",
        "people",
        3,
        10,
        "Holds have a 30-minute review target at any hour: one seat staffed 24x7 takes "
        "about five people.",
    ),
    "platform_monthly_cost_taka": Spec(
        "Running the platform a month",
        "BDT",
        250_000,
        1_500_000,
        "Assumed: API and stream nodes, Postgres, Redis, and part of an engineer's time "
        "for monitoring and retraining.",
    ),
    "reimbursement_share": Spec(
        "Share of scam losses the provider reimburses",
        "share",
        0.0,
        1.0,
        "Assumed. Bangladesh has no mandatory reimbursement rule; the UK has required 100% "
        "(up to £85,000) since October 2024, the upper end of the range.",
    ),
    "reputation_per_taka": Spec(
        "Reputational cost per taka a customer loses",
        "BDT per BDT",
        0.0,
        1.0,
        "Assumed: victims and the people they tell use the wallet less, and the regulator "
        "pays attention. The hardest number here to defend; shown separately.",
    ),
}

_RATE_KEYS = ("alert_rate", "precision", "taka_recall", "taka_recall_with_exit_holds")


def _cumulative(point: dict) -> dict[str, float]:
    """Per-payment rates of everything at or above one threshold."""
    rate, precision = float(point["alert_rate"]), float(point["precision"])
    return {
        "true": rate * precision,
        "false": rate * (1 - precision),
        "recall": float(point["taka_recall"]),
        "recall_exit": float(point["taka_recall_with_exit_holds"]),
    }


def _loss_per_payment(a: Assumptions) -> float:
    return a.avg_payment_taka * a.scam_loss_bps / 10_000


def _synthetic_loss_per_payment(insights: dict) -> float:
    return insights["at_thresholds"]["warn"]["taka_at_risk"] / insights["rows"]


def _scale(insights: dict, a: Assumptions) -> float:
    """Real scam money per payment over the simulator's: rescales the true alerts."""
    synthetic = _synthetic_loss_per_payment(insights)
    return _loss_per_payment(a) / synthetic if synthetic else 0.0


def nearest(points: list[dict], threshold: float) -> int:
    """The sweep point closest to `threshold`, on a log scale (as the impact page does)."""
    return min(range(len(points)), key=lambda i: abs(math.log(points[i]["threshold"] / threshold)))


def _point(insights: dict, point: dict, a: Assumptions, scale: float) -> dict:
    """The monthly money of alerting at `point` as the warn threshold.

    Step-up and hold keep the policy's thresholds, or follow the slider once it passes
    them. Each tier's band is the difference between two cumulative sweep rows.
    """
    t = float(point["threshold"])
    here = _cumulative(point)
    at = insights["at_thresholds"]
    limits = insights["thresholds"]
    s = here if t >= limits["step_up"] else _cumulative(at["step_up"])
    h = here if t >= limits["hold"] else _cumulative(at["hold"])
    cum = {"warn": here, "step_up": s, "hold": h}
    following = {"warn": s, "step_up": h, "hold": None}
    bands = {}
    for tier in TIERS:
        upper, lower = cum[tier], following[tier]
        bands[tier] = {key: max(upper[key] - (lower[key] if lower else 0.0), 0.0) for key in upper}

    volume = a.monthly_payments
    at_risk = volume * a.avg_payment_taka * a.scam_loss_bps / 10_000
    stop = {"warn": a.stop_warn, "step_up": a.stop_step_up, "hold": a.stop_hold}
    contacts = {"warn": a.contacts_warn, "step_up": a.contacts_step_up, "hold": a.contacts_hold}

    prevented = {tier: at_risk * bands[tier]["recall"] * stop[tier] for tier in TIERS}
    # Only a hold stops a mule's cash-out: a warning shown to a mule changes nothing.
    exit_share = max(h["recall_exit"] - h["recall"], 0.0)
    prevented["mule_cash_out_held"] = at_risk * exit_share * a.stop_hold
    prevented_total = sum(prevented.values())

    interrupted = {
        tier: volume * (bands[tier]["true"] * scale + bands[tier]["false"]) for tier in TIERS
    }
    honest = {tier: volume * bands[tier]["false"] for tier in TIERS}
    abandoned = sum(honest.values()) * a.abandon_rate
    support_contacts = sum(interrupted[tier] * contacts[tier] for tier in TIERS)
    friction = {
        "abandoned_payments": abandoned * a.abandon_cost_taka,
        "support_contacts": support_contacts * a.contact_cost_taka,
    }

    held = interrupted["hold"]
    review_hours = held * a.review_minutes / 60
    workload_fte = review_hours / a.analyst_hours_per_month
    analysts = max(math.ceil(workload_fte - 1e-9), a.min_analysts)
    analyst_cost = analysts * a.analyst_monthly_cost_taka

    operating = sum(friction.values()) + analyst_cost + a.platform_monthly_cost_taka
    missed = at_risk - prevented_total
    return {
        "threshold": t,
        "alert_rate": float(point["alert_rate"]),
        "interrupted": {tier: round(n) for tier, n in interrupted.items()},
        "interrupted_total": round(sum(interrupted.values())),
        "honest_interrupted": round(sum(honest.values())),
        "honest_per_10k": round(sum(bands[t]["false"] for t in TIERS) * 10_000, 2),
        "held_for_review": round(held),
        "prevented": {k: round(v) for k, v in prevented.items()},
        "prevented_total": round(prevented_total),
        "prevented_share": round(prevented_total / at_risk, 4) if at_risk else 0.0,
        "missed": round(missed),
        "friction": {k: round(v) for k, v in friction.items()},
        "friction_total": round(sum(friction.values())),
        "abandoned_payments": round(abandoned),
        "support_contacts": round(support_contacts),
        "review_hours": round(review_hours),
        "workload_fte": round(workload_fte, 1),
        "analysts": int(analysts),
        "analyst_cost": round(analyst_cost),
        "platform_cost": round(a.platform_monthly_cost_taka),
        "operating_cost": round(operating),
        # Customers and provider together: every taka a customer keeps counts, plus the
        # reputational cost avoided.
        "net_benefit": round(prevented_total * (1 + a.reputation_per_taka) - operating),
        # The provider's own cash: only what it would have reimbursed, plus reputation.
        "provider_net_benefit": round(
            prevented_total * (a.reimbursement_share + a.reputation_per_taka) - operating
        ),
        "prevented_per_taka_cost": round(prevented_total / operating, 2) if operating else None,
        "missed_cost_provider": round(missed * (a.reimbursement_share + a.reputation_per_taka)),
    }


def business_case(insights: dict, assumptions: Assumptions | None = None) -> dict:
    """The monthly money at every threshold of the sweep, under `assumptions`."""
    a = assumptions or Assumptions()
    sweep = insights.get("impact") or []
    if not sweep:
        raise ValueError("the insights report has no threshold sweep")
    scale = _scale(insights, a)
    points = [_point(insights, p, a, scale) for p in sweep]
    today = nearest(sweep, insights["thresholds"]["warn"])
    best = max(range(len(points)), key=lambda i: points[i]["net_benefit"])
    return {
        "model_version": insights.get("model_version"),
        "policy_version": insights.get("policy_version"),
        "rows": insights["rows"],
        "days": insights["days"],
        "assumptions": a.model_dump(),
        "defaults": Assumptions().model_dump(),
        "sources": [
            {
                "key": key,
                "label": s.label,
                "unit": s.unit,
                "low": s.low,
                "high": s.high,
                "source": s.source,
            }
            for key, s in ASSUMPTIONS.items()
        ],
        "synthetic": {
            "scam_loss_per_payment": round(_synthetic_loss_per_payment(insights), 2),
            "scale": round(scale, 5),
        },
        "scam_loss_at_risk": round(a.monthly_payments * _loss_per_payment(a)),
        "today_index": today,
        "best_index": best,
        # The thresholds in force, measured exactly (the sweep only comes near them).
        "policy": _point(insights, _policy_row(insights), a, scale),
        "points": points,
        "sensitivity": sensitivity(insights, a),
        "break_even_bps": {
            "net_benefit": break_even_bps(insights, a, "net_benefit"),
            "provider_net_benefit": break_even_bps(insights, a, "provider_net_benefit"),
        },
    }


def break_even_bps(insights: dict, a: Assumptions, key: str = "net_benefit") -> float | None:
    """The scam rate (basis points of payment value) below which running the policy in
    force costs more than it saves, everything else held. None if it never pays off
    within the field's range."""
    row = _policy_row(insights)
    low, high = 0.01, 100.0  # the field's bounds

    def net(bps: float) -> float:
        changed = a.model_copy(update={"scam_loss_bps": bps})
        return _point(insights, row, changed, _scale(insights, changed))[key]

    if net(high) < 0:
        return None
    if net(low) >= 0:
        return low
    for _ in range(50):
        mid = (low + high) / 2
        low, high = (mid, high) if net(mid) < 0 else (low, mid)
    return round(high, 3)


def _policy_row(insights: dict) -> dict:
    """The warn threshold in force, as a sweep row: exact, not the nearest sweep point."""
    return {**insights["at_thresholds"]["warn"], "threshold": insights["thresholds"]["warn"]}


def sensitivity(insights: dict, assumptions: Assumptions, row: dict | None = None) -> dict:
    """Tornado table at one threshold (default: the policy's warn threshold).

    Each assumption is set to its low and then its high value with the others held;
    rows are sorted by how far that swings the net benefit, largest first. The
    provider's own net benefit is given too, since reimbursement only moves that one.
    """
    row = row or _policy_row(insights)
    base = _point(insights, row, assumptions, _scale(insights, assumptions))
    rows = []
    for key, spec in ASSUMPTIONS.items():
        out = {}
        for side, value in (("low", spec.low), ("high", spec.high)):
            changed = assumptions.model_copy(update={key: type(getattr(assumptions, key))(value)})
            point = _point(insights, row, changed, _scale(insights, changed))
            out[f"net_{side}"] = point["net_benefit"]
            out[f"provider_{side}"] = point["provider_net_benefit"]
        rows.append(
            {
                "key": key,
                "label": spec.label,
                "unit": spec.unit,
                "low": spec.low,
                "high": spec.high,
                **out,
                "swing": abs(out["net_high"] - out["net_low"]),
                "provider_swing": abs(out["provider_high"] - out["provider_low"]),
            }
        )
    rows.sort(key=lambda r: (r["swing"], r["provider_swing"]), reverse=True)
    return {
        "threshold": float(row["threshold"]),
        "net_benefit": base["net_benefit"],
        "provider_net_benefit": base["provider_net_benefit"],
        "rows": rows,
    }


def _row(p: dict, mark: str = "") -> str:
    return (
        f"{p['threshold']:>10.5f} {p['alert_rate']:>8.4%} {p['prevented_total']:>13,} "
        f"{p['friction_total']:>11,} {p['analysts']:>8} {p['operating_cost']:>12,} "
        f"{p['net_benefit']:>13,} {p['provider_net_benefit']:>13,} "
        f"{p['prevented_per_taka_cost'] or 0:>7.2f} {p['honest_per_10k']:>10.2f}{mark}"
    )


def print_summary(case: dict) -> None:
    print(
        f"model {case['model_version']}, policy {case['policy_version']}: "
        f"{case['assumptions']['monthly_payments']:,.0f} payments a month, "
        f"scam money at risk ৳{case['scam_loss_at_risk']:,} a month "
        f"(synthetic scale {case['synthetic']['scale']})"
    )
    print(
        f"{'threshold':>10} {'alerted':>8} {'prevented':>13} {'friction':>11} {'analysts':>8} "
        f"{'operating':>12} {'net':>13} {'provider net':>13} {'per ৳1':>7} {'honest/10k':>10}"
    )
    for i, p in enumerate(case["points"]):
        mark = " <- nearest to today" if i == case["today_index"] else ""
        print(_row(p, mark + (" <- best" if i == case["best_index"] else "")))
    policy = case["policy"]
    print(_row(policy, " <- policy in force (exact)"))
    print(f"\nat the policy in force: prevented {json.dumps(policy['prevented'])}")
    print(
        f"  friction {json.dumps(policy['friction'])}, held {policy['held_for_review']:,}, "
        f"review hours {policy['review_hours']:,}, workload {policy['workload_fte']} FTE, "
        f"interrupted {json.dumps(policy['interrupted'])}"
    )
    sens = case["sensitivity"]
    print(
        f"\nsensitivity at the policy's warn threshold: net ৳{sens['net_benefit']:,}, "
        f"provider ৳{sens['provider_net_benefit']:,}"
    )
    for row in sens["rows"]:
        print(
            f"  {row['label']:<48} {row['low']:>12,} -> ৳{row['net_low']:>12,} "
            f"(provider ৳{row['provider_low']:>12,})  {row['high']:>12,} -> "
            f"৳{row['net_high']:>12,} (provider ৳{row['provider_high']:>12,})"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="The threshold sweep as a monthly P&L in taka")
    parser.add_argument("--model", default=None, help="model version (default: current)")
    parser.add_argument(
        "--set", action="append", default=[], metavar="KEY=VALUE", help="override an assumption"
    )
    parser.add_argument("--json", action="store_true", help="print the whole result as JSON")
    args = parser.parse_args()
    version = args.model or registry.current_version(settings.models_dir)
    insights = json.loads((settings.models_dir / version / INSIGHTS_FILE).read_text())
    overrides = dict(item.split("=", 1) for item in args.set)
    case = business_case(insights, Assumptions(**overrides))
    if args.json:
        print(json.dumps(case, indent=2))
    else:
        print_summary(case)


if __name__ == "__main__":
    main()
