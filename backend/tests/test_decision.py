import json
import math
from types import SimpleNamespace

import anthropic
import httpx
import numpy as np
import pandas as pd
import pytest
import yaml

from fraudlens.decision import (
    DecisionEngine,
    LLMNarrator,
    PolicyError,
    SimilarCases,
    apply_policy,
    check_grounding,
    context_from_engine,
    display_score,
    load_policy,
    mask_id,
    narrate,
    template,
)
from fraudlens.decision import evaluate as policy_eval
from fraudlens.decision.policy import POLICY_DIR, Policy, tier_for
from fraudlens.decision.reasons import GROUPS, PHRASES, explain, fact
from fraudlens.features import FEATURES, FeatureEngine
from fraudlens.features.build import iter_txns
from fraudlens.models import registry
from fraudlens.models.data import load_frame

THRESHOLDS = {"warn": 0.01, "step_up": 0.05, "hold": 0.2}
LOW, HIGH = 0.001, 0.9


@pytest.fixture(scope="module")
def policy():
    return load_policy("v1")


def _variant(**changes) -> dict:
    """The v1 policy as plain data, with top-level keys replaced."""
    raw = yaml.safe_load((POLICY_DIR / "v1.yaml").read_text(encoding="utf-8"))
    return raw | changes


def _rule(**changes) -> dict:
    base = {
        "id": "X01",
        "description": "test rule",
        "description_bn": "পরীক্ষার নিয়ম",
        "when": [{"field": "amount", "op": "<", "value": 200}],
        "effect": "raise_to",
        "tier": "warn",
        "reason": "AMOUNT",
    }
    return base | changes


# -------------------------------------------------------------- the policy file


def test_v1_policy_loads_and_keeps_a_person_in_the_loop(policy):
    assert policy.version == "v1"
    assert policy.tiers["hold"].action == "hold_for_review" and policy.tiers["hold"].human_review
    assert policy.tiers["allow"].action == "proceed"
    assert [r.id[:3] for r in policy.rules] == ["R01", "R02", "R03", "R04"]
    # Thresholds come from the model version unless the policy overrides them.
    manifest = {"thresholds": THRESHOLDS}
    assert policy.resolve_thresholds(manifest) == THRESHOLDS
    with pytest.raises(PolicyError, match="no manifest"):
        policy.resolve_thresholds(None)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"rules": [_rule(when=[{"field": "no_such", "op": "<", "value": 1}])]}, "unknown field"),
        ({"rules": [_rule(when=[{"field": "amount", "op": "~", "value": 1}])]}, "op"),
        ({"rules": [_rule(), _rule()]}, "unique"),
        ({"rules": [_rule(effect="cap_at", hard=True)]}, "cap cannot be hard"),
        ({"rules": [_rule(reason="MADE_UP")]}, "unknown reason"),
        ({"rules": [_rule(tier="allow")]}, "does nothing"),
        ({"rules": [_rule(applies_to=["PAYMENT"])]}, "unknown transaction types"),
        ({"rules": [_rule(expression="amount < 200")]}, "Extra inputs"),
        ({"surprise": 1}, "Extra inputs"),
        ({"thresholds": {"source": "policy", "overrides": {"hold": 0.5}}}, "all three"),
        ({"thresholds": {"overrides": {"hold": 1.5}}}, "thresholds must satisfy"),
    ],
)
def test_policy_validation_rejects_bad_files(changes, message):
    with pytest.raises(ValueError, match=message):
        Policy.model_validate(_variant(**changes))


def test_policy_cannot_remove_human_review_from_holds():
    tiers = _variant()["tiers"]
    tiers["hold"] = {"action": "hold_for_review", "human_review": False}
    with pytest.raises(ValueError, match="human_review"):
        Policy.model_validate(_variant(tiers=tiers))


def test_policy_fallback_and_messages_must_be_complete():
    fallback = _variant()["fallback"] | {"points": {"warn": 5, "step_up": 4}}
    with pytest.raises(ValueError, match="1 <= warn <= step_up"):
        Policy.model_validate(_variant(fallback=fallback))
    messages = _variant()["messages"]
    del messages["cash_out"]["hold"]
    with pytest.raises(ValueError, match="messages.cash_out"):
        Policy.model_validate(_variant(messages=messages))


def test_load_policy_reports_missing_malformed_and_mislabelled_files(tmp_path):
    with pytest.raises(PolicyError, match="no policy file"):
        load_policy("v9", tmp_path)
    (tmp_path / "bad.yaml").write_text("rules: [unclosed", encoding="utf-8")
    with pytest.raises(PolicyError, match="invalid policy"):
        load_policy("bad", tmp_path)
    (tmp_path / "v2.yaml").write_text(
        yaml.safe_dump(_variant(), allow_unicode=True), encoding="utf-8"
    )
    with pytest.raises(PolicyError, match="declares version 'v1'"):
        load_policy("v2", tmp_path)


def test_overridden_thresholds_must_stay_ordered():
    overridden = Policy.model_validate(_variant(thresholds={"overrides": {"hold": 0.02}}))
    with pytest.raises(PolicyError, match="thresholds must satisfy"):
        overridden.resolve_thresholds({"thresholds": THRESHOLDS})  # hold below step_up
    assert overridden.resolve_thresholds({"thresholds": THRESHOLDS | {"step_up": 0.015}}) == {
        "warn": 0.01,
        "step_up": 0.015,
        "hold": 0.02,
    }


# ------------------------------------------------------------ tiers and rules


@pytest.mark.parametrize(
    ("risk", "tier"),
    [
        (0.0, "allow"),
        (0.0099, "allow"),
        (0.01, "warn"),
        (0.0499, "warn"),
        (0.05, "step_up"),
        (0.2, "hold"),
        (1.0, "hold"),
    ],
)
def test_model_tier_follows_the_thresholds(policy, risk, tier):
    assert tier_for(risk, THRESHOLDS) == tier
    outcome = apply_policy(policy, "SEND_MONEY", {}, risk, THRESHOLDS)
    assert (outcome.tier, outcome.model_tier, outcome.decided_by) == (tier, tier, "model")
    assert outcome.mode == "model" and not outcome.fired


@pytest.mark.parametrize(
    ("rule_id", "txn_type", "fields", "tier"),
    [
        ("R01_RECIPIENT_CONFIRMED_FRAUD", "SEND_MONEY", {"recipient_flagged": 1.0}, "hold"),
        ("R02_SENDER_CONFIRMED_FRAUD", "SEND_MONEY", {"sender_flagged": 1.0}, "hold"),
        ("R02_SENDER_CONFIRMED_FRAUD", "CASH_OUT", {"sender_flagged": 1.0}, "hold"),
        (
            "R03_FRAUD_HANDSET_NEW_ON_WALLET",
            "CASH_OUT",
            {"s_new_device": 1.0, "s_device_flagged": 1.0},
            "step_up",
        ),
        ("R04_RECIPIENT_LOOKS_LIKE_MULE", "SEND_MONEY", {"recipient_mule_alert": 1.0}, "warn"),
    ],
)
def test_each_rule_raises_a_low_score_to_its_tier(policy, rule_id, txn_type, fields, tier):
    outcome = apply_policy(policy, txn_type, fields, LOW, THRESHOLDS)
    assert outcome.tier == tier and outcome.model_tier == "allow"
    assert outcome.decided_by == f"rule:{rule_id}"
    assert [r.id for r in outcome.fired] == [rule_id]
    entry = next(t for t in outcome.trace if t["id"] == rule_id)
    assert entry["status"] == "fired" and entry["inputs"] == fields


def test_rule_trace_lists_every_rule_with_what_it_saw(policy):
    fields = {"sender_flagged": 0.0, "s_new_device": 1.0, "s_device_flagged": float("nan")}
    outcome = apply_policy(policy, "CASH_OUT", fields, LOW, THRESHOLDS)
    status = {t["id"]: t["status"] for t in outcome.trace}
    assert status == {
        "R01_RECIPIENT_CONFIRMED_FRAUD": "not_applicable",  # cash-outs have no recipient wallet
        "R02_SENDER_CONFIRMED_FRAUD": "not_fired",
        "R03_FRAUD_HANDSET_NEW_ON_WALLET": "not_evaluated",  # one input missing
        "R04_RECIPIENT_LOOKS_LIKE_MULE": "not_applicable",
    }
    by_id = {t["id"]: t for t in outcome.trace}
    assert by_id["R03_FRAUD_HANDSET_NEW_ON_WALLET"]["inputs"] == {
        "s_new_device": 1.0,
        "s_device_flagged": None,
    }
    assert outcome.tier == "allow" and not outcome.fired


def test_a_missing_input_never_fires_a_rule(policy):
    for fields in ({}, {"recipient_flagged": float("nan")}):
        outcome = apply_policy(policy, "SEND_MONEY", fields, LOW, THRESHOLDS)
        assert outcome.tier == "allow"
        assert {t["status"] for t in outcome.trace} == {"not_evaluated"}


def test_rules_never_lower_what_the_model_decided(policy):
    outcome = apply_policy(policy, "SEND_MONEY", {"recipient_mule_alert": 1.0}, HIGH, THRESHOLDS)
    assert outcome.tier == "hold" and outcome.decided_by == "model"
    assert [r.id for r in outcome.fired] == ["R04_RECIPIENT_LOOKS_LIKE_MULE"]


def test_a_cap_lowers_the_tier_unless_a_hard_rule_fired(policy):
    cap = _rule(id="C01_SMALL_AMOUNT", effect="cap_at", tier="warn", reason="SMALL_AMOUNT")
    capped = Policy.model_validate(_variant(rules=[*_variant()["rules"], cap]))

    small = apply_policy(capped, "SEND_MONEY", {"amount": 150.0}, HIGH, THRESHOLDS)
    assert (small.tier, small.model_tier) == ("warn", "hold")
    assert small.decided_by == "cap:C01_SMALL_AMOUNT"

    large = apply_policy(capped, "SEND_MONEY", {"amount": 5000.0}, HIGH, THRESHOLDS)
    assert large.tier == "hold" and large.decided_by == "model"

    # A cap never raises, and a non-hard rule above the cap is capped too.
    low = apply_policy(capped, "SEND_MONEY", {"amount": 150.0}, LOW, THRESHOLDS)
    assert low.tier == "allow"
    soft = {"amount": 150.0, "s_new_device": 1.0, "s_device_flagged": 1.0}
    assert apply_policy(capped, "SEND_MONEY", soft, LOW, THRESHOLDS).tier == "warn"

    # Confirmed fraud is a hard rule: no cap applies.
    hard = apply_policy(
        capped, "SEND_MONEY", {"amount": 150.0, "recipient_flagged": 1.0}, LOW, THRESHOLDS
    )
    assert hard.tier == "hold" and hard.decided_by == "rule:R01_RECIPIENT_CONFIRMED_FRAUD"


def test_apply_policy_refuses_unscored_types_and_nan_risk(policy):
    with pytest.raises(ValueError, match="not scored"):
        apply_policy(policy, "PAYMENT", {}, LOW, THRESHOLDS)
    with pytest.raises(ValueError, match="NaN"):
        apply_policy(policy, "SEND_MONEY", {}, float("nan"), THRESHOLDS)


# ------------------------------------------------------- rules-only fallback

SIGNALS = {
    "pair_prior_count": 0.0,
    "r_age_days": 3.0,
    "amount_to_balance": 0.9,
    "s_new_device": 1.0,
    "amount": 9000.0,
    "r_fast_exit_share": 0.8,
    "s_device_age_secs": 0.0,
}


@pytest.mark.parametrize(
    ("n_signals", "tier"), [(0, "allow"), (3, "allow"), (4, "warn"), (5, "step_up"), (7, "step_up")]
)
def test_fallback_counts_signals_and_never_holds_on_points(policy, n_signals, tier):
    fields = dict(list(SIGNALS.items())[:n_signals])
    outcome = apply_policy(policy, "SEND_MONEY", fields, None, None)
    assert outcome.mode == "rules_only" and outcome.model_tier is None
    assert outcome.tier == tier and outcome.decided_by == "fallback"
    assert len(outcome.fallback_signals) == n_signals


def test_fallback_still_holds_confirmed_fraud_but_caps_soft_rules(policy):
    hard = apply_policy(policy, "SEND_MONEY", {"recipient_flagged": 1.0}, None, None)
    assert hard.tier == "hold" and hard.decided_by == "rule:R01_RECIPIENT_CONFIRMED_FRAUD"

    soft_hold = _rule(id="X02", tier="hold")
    soft = Policy.model_validate(_variant(rules=[soft_hold]))
    outcome = apply_policy(soft, "SEND_MONEY", {"amount": 100.0}, None, None)
    assert outcome.tier == "step_up"  # the fallback ceiling
    assert apply_policy(soft, "SEND_MONEY", {"amount": 100.0}, LOW, THRESHOLDS).tier == "hold"


# -------------------------------------------------------------------- reasons


def test_facts_state_the_actual_value_in_both_languages():
    one = fact("pair_prior_count", 1.0)
    assert one["en"] == "the sender has paid this receiver 1 time before"
    assert fact("pair_prior_count", 3.0)["en"].endswith("3 times before")
    never = fact("pair_prior_count", 0.0)
    assert never["en"] == "the sender has never paid this receiver before"
    assert never["bn"] == "প্রেরক এই প্রাপককে আগে কখনো টাকা দেননি"

    amount = fact("amount", 12500.0)
    assert amount["en"] == "the amount is ৳12,500" and "৳১২,৫০০" in amount["bn"]
    assert fact("r_dwell_secs", 400.0)["en"] == "money typically leaves it 7 min after arriving"
    assert "৭ মিনিট" in fact("r_dwell_secs", 400.0)["bn"]
    assert fact("r_fast_exit_share", 0.82)["en"].startswith("82% of the transfers")
    assert fact("r_age_days", 4.0)["en"] == "the receiving wallet was opened 4 days ago"
    assert fact("s_new_device", 1.0)["en"].startswith("first transaction from a handset")
    assert fact("s_new_device", 0.0)["en"] == "not a first-time handset"
    assert fact("r_age_days", float("nan")) is None and fact("is_cash_out", 1.0) is None


@pytest.mark.parametrize("value", [0.0, 1.0, 2.5, 4000.0])
def test_every_feature_has_a_complete_phrase(value):
    for name in PHRASES:
        rendered = fact(name, value)
        for lang in ("en", "bn"):
            text = rendered[lang]
            assert text and not set(text) & set("{}[]|"), (name, text)
        assert not any(ch.isdigit() and ch.isascii() for ch in rendered["bn"]), rendered["bn"]


def test_explain_ranks_topics_by_contribution_and_keeps_directions_apart():
    values = dict.fromkeys(FEATURES, float("nan"))
    contrib = dict.fromkeys(FEATURES, 0.0)
    values |= {"r_dwell_secs": 120.0, "r_fast_exit_share": 0.9, "r_cashout_share": 0.0}
    contrib |= {"r_dwell_secs": 2.0, "r_fast_exit_share": 1.0, "r_cashout_share": -0.5}
    values |= {"r_age_days": 2.0, "amount": 8000.0, "pair_prior_count": 6.0, "hour": 14.0}
    contrib |= {"r_age_days": 1.5, "amount": 0.1, "pair_prior_count": -2.0, "hour": 0.01}
    contrib["is_cash_out"] = 5.0  # never shown

    reasons = explain(list(values.values()), list(contrib.values()))
    assert [(r["code"], r["direction"]) for r in reasons] == [
        ("RECIPIENT_PASS_THROUGH", "raises"),
        ("RECIPIENT_NEW_WALLET", "raises"),
        ("RELATIONSHIP", "lowers"),
    ]  # AMOUNT and TIME_AND_PLACE are below the 5% share floor
    top = reasons[0]
    assert top["weight"] == 2.5 and top["source"] == "model"
    assert top["share"] == pytest.approx(2.5 / 4.11, abs=1e-3)
    # Only the facts that push the same way as the topic, strongest first.
    assert [f["feature"] for f in top["facts"]] == ["r_dwell_secs", "r_fast_exit_share"]
    assert top["title_en"] == GROUPS["RECIPIENT_PASS_THROUGH"][0]
    assert top["detail_en"].startswith("money typically leaves it 2 min after arriving; 90%")
    assert top["title_bn"] and "৯০%" in top["detail_bn"]
    assert reasons[2]["detail_en"] == "the sender has paid this receiver 6 times before"

    with pytest.raises(ValueError):
        explain([1.0], [1.0])


# ------------------------------------------------------------------ grounding

EVIDENCE = {
    "transaction": {
        "id": 77,
        "type": "SEND_MONEY",
        "amount": 12500.0,
        "time": "2026-04-06 03:34",
        "channel": "app",
        "district": "Dhaka",
        "sender": "W***6468",
        "receiver": "W***0292",
    },
    "decision": {
        "tier": "hold",
        "action": "hold_for_review",
        "mode": "model",
        "decided_by": "model",
        "risk_band": "very high",
        "risk_score": 96,
        "risk_scale": 100,
        "human_review": True,
        "cooling_off_minutes": None,
        "review_sla_minutes": 30,
        "fallback_signals": [],
    },
    "reasons": [
        {
            "code": "RECIPIENT_NEW_WALLET",
            "source": "model",
            "direction": "raises",
            "title_en": "Age and own activity of the receiving wallet",
            "title_bn": "প্রাপক ওয়ালেটের বয়স ও নিজস্ব লেনদেন",
            "detail_en": "the receiving wallet was opened 4 days ago",
            "detail_bn": "প্রাপক ওয়ালেট ৪ দিন আগে খোলা হয়েছে",
        }
    ],
    "similar_cases": [
        {"case_id": 608, "typology": "account_takeover", "similarity": 0.988, "loss": 23240.0}
    ],
    "recommended_actions": [{"id": "CALL_SENDER", "en": "Call the sender.", "bn": "ফোন করুন।"}],
}


def test_template_note_answers_the_three_questions_and_is_grounded():
    note = template(EVIDENCE)
    paragraphs = note.split("\n\n")
    assert [p.split(":")[0] for p in paragraphs] == [
        "What happened",
        "Why it is risky",
        "What happens next",
    ]
    assert "W***6468 tried to send ৳12,500 to W***0292" in paragraphs[0]
    assert "96 out of 100" in paragraphs[1] and "case 608" in paragraphs[1]
    assert "until an analyst decides, within 30 minutes" in paragraphs[2]
    assert check_grounding(note, EVIDENCE).ok

    bangla = template(EVIDENCE, "bn")
    assert bangla.startswith("কী ঘটেছে:") and "৳১২,৫০০" in bangla and "কেস ৬০৮" in bangla
    assert "W***6468" in bangla and "W***0292" in bangla  # searchable codes keep their digits
    assert check_grounding(bangla, EVIDENCE).ok
    with pytest.raises(ValueError):
        template(EVIDENCE, "fr")


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("The wallet received ৳99,000 from 14 senders.", "number not in evidence: 14"),
        ("The wallet received ৳99,000 from 14 senders.", "number not in evidence: 99000"),
        ("ওয়ালেটটি ১৪ জনের কাছ থেকে টাকা পেয়েছে।", "number not in evidence: 14"),
        ("Sent to W0020292, a known mule.", "identifier not in evidence: W0020292"),
        ("Sent to W***9999.", "identifier not in evidence: W***9999"),
        ("Risk is 96.5 out of 100.", "number not in evidence: 96.5"),
        ("   ", "empty text"),
        ("word " * 700, "longer than 3000 characters"),
    ],
)
def test_grounding_rejects_text_that_goes_beyond_the_evidence(text, problem):
    result = check_grounding(text, EVIDENCE)
    assert not result.ok and problem in result.problems


def test_grounding_accepts_evidence_quoted_in_other_formats():
    text = (
        "W***6468 tried to send ৳12,500.00 to W***0292 at 03:34 on 2026-04-06. Risk is 96 of "
        "100; the receiving wallet is 4 days old; case 608 lost ৳23,240 (similarity 0.99). "
        "ঝুঁকি ৯৬, ক্ষতি ৳২৩,২৪০।"
    )
    assert check_grounding(text, EVIDENCE) == check_grounding("Risk is 96.", EVIDENCE)
    assert check_grounding(text, EVIDENCE).ok


def test_narrate_uses_the_model_text_only_when_it_is_grounded():
    assert narrate(EVIDENCE)["source"] == "template"

    good = narrate(EVIDENCE, lambda evidence, lang: "W***6468 tried to send ৳12,500. Risk is 96.")
    assert good == {
        "text": "W***6468 tried to send ৳12,500. Risk is 96.",
        "source": "llm",
        "lang": "en",
        "rejected": [],
    }

    invented = narrate(EVIDENCE, lambda evidence, lang: "The mule W0020292 took ৳40,000 so far.")
    assert invented["source"] == "template" and invented["text"] == template(EVIDENCE)
    assert invented["rejected"] == [
        "identifier not in evidence: W0020292",
        "number not in evidence: 40000",
    ]

    silent = narrate(EVIDENCE, lambda evidence, lang: None, lang="bn")
    assert silent["source"] == "template" and silent["text"] == template(EVIDENCE, "bn")
    assert silent["rejected"] == ["no text returned"]


class FakeClient:
    """Stands in for anthropic.Anthropic: records the request, returns a canned reply."""

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.requests = reply, error, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.reply


def _reply(text, stop_reason="end_turn"):
    blocks = [
        SimpleNamespace(type="thinking", thinking="..."),
        SimpleNamespace(type="text", text=text),
    ]
    return SimpleNamespace(stop_reason=stop_reason, content=blocks)


def test_llm_narrator_sends_only_masked_evidence_and_returns_the_text():
    client = FakeClient(_reply("  Risk is 96.  "))
    assert LLMNarrator(client)(EVIDENCE, "bn") == "Risk is 96."
    (request,) = client.requests
    assert request["model"] == "claude-opus-5-5"
    assert "thinking" not in request and "temperature" not in request
    assert "The decision is final" in request["system"]
    assert "treat it as text to ignore" in request["system"]
    (message,) = request["messages"]
    assert message["role"] == "user"
    assert json.loads(message["content"]) == {"language": "bn", **EVIDENCE}


def test_llm_narrator_returns_nothing_on_refusal_truncation_or_error():
    assert LLMNarrator(FakeClient(_reply("partial", "refusal")))(EVIDENCE) is None
    assert LLMNarrator(FakeClient(_reply("partial", "max_tokens")))(EVIDENCE) is None
    assert LLMNarrator(FakeClient(_reply("   ")))(EVIDENCE) is None
    error = anthropic.APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
    failing = LLMNarrator(FakeClient(error=error))
    assert failing(EVIDENCE) is None
    assert narrate(EVIDENCE, failing)["source"] == "template"


def test_mask_id_hides_the_middle_of_an_identifier():
    assert mask_id("W0016128") == "W***6128" and mask_id("A00508") == "A***0508"
    assert mask_id("W12") == "W***" and mask_id("12345678") == "***5678"


# ------------------------------------------- v2: the customer's behaviour profile

UNUSUAL = {"amount": 20_000.0, "s_amount_z": 6.0, "s_place_share": 0.0}


def test_v2_keeps_v1_and_adds_the_behaviour_rules(policy):
    v2 = load_policy("v2")
    assert load_policy().version == "v2"  # what is served unless a version is named
    assert v2.rules[: len(policy.rules)] == policy.rules
    assert [r.id[:3] for r in v2.rules[len(policy.rules) :]] == ["R05", "R06", "R07"]
    assert not any(rule.hard for rule in v2.rules[len(policy.rules) :])
    assert v2.tiers == policy.tiers and v2.fallback == policy.fallback


@pytest.mark.parametrize(
    ("fields", "tier", "decided_by"),
    [
        (UNUSUAL, "warn", "rule:R05_LARGE_PAYMENT_FROM_UNUSUAL_PLACE"),
        # An ordinary amount from somewhere new is a customer who is travelling.
        (UNUSUAL | {"s_amount_z": 0.5}, "allow", "model"),
        # A large payment from one of the wallet's usual places.
        (UNUSUAL | {"s_place_share": 0.4}, "allow", "model"),
        # No history yet: nothing can be said, so nothing is.
        (UNUSUAL | {"s_place_share": math.nan}, "allow", "model"),
        (
            UNUSUAL | {"s_place_share": 1.0, "s_network_share": 0.0},
            "warn",
            "rule:R06_LARGE_PAYMENT_FROM_UNFAMILIAR_NETWORK",
        ),
        (UNUSUAL | {"s_place_share": 1.0, "s_network_share": 0.6}, "allow", "model"),
        (
            UNUSUAL | {"s_network_share": 0.0},
            "step_up",
            "rule:R07_LARGE_PAYMENT_FROM_UNUSUAL_PLACE_AND_NETWORK",
        ),
    ],
)
def test_a_large_payment_from_an_unusual_place_or_network_is_raised(fields, tier, decided_by):
    outcome = apply_policy(load_policy("v2"), "SEND_MONEY", fields, LOW, THRESHOLDS)
    assert (outcome.tier, outcome.decided_by) == (tier, decided_by)


def test_the_behaviour_rules_never_lower_what_the_model_decided():
    outcome = apply_policy(load_policy("v2"), "CASH_OUT", UNUSUAL, HIGH, THRESHOLDS)
    assert outcome.tier == "hold" and outcome.decided_by == "model"
    assert [rule.id[:3] for rule in outcome.fired] == ["R05"]


def test_a_rule_cannot_name_a_scenario_the_policy_has_no_words_for():
    raw = _variant(rules=[_rule(scenario="unusual_access")])
    with pytest.raises(ValueError, match="messages.unusual_access"):
        Policy.model_validate(raw)


def test_the_customer_is_told_where_the_doubt_comes_from(world):
    v2 = load_policy("v2")
    engine = DecisionEngine(v2, world.bundle, world.index)
    calm = world.frame[(world.frame["type"] == "SEND_MONEY") & (world.frame["y"] == 0)]
    row = calm.sort_values("risk").iloc[0].copy()
    assert _decide(world, row, engine)[1].tier == "allow"
    row["s_amount_z"], row["s_place_share"] = 6.0, 0.0
    _, decision = _decide(world, row, engine)
    assert decision.policy_version == "v2" and decision.scenario == "unusual_access"
    assert decision.tier != "allow"
    assert decision.customer_message == v2.messages["unusual_access"][decision.tier].model_dump()
    assert "UNUSUAL_PLACE" in [reason["code"] for reason in decision.reasons]
    trace = {step["id"]: step for step in decision.rule_trace}
    assert trace["R05_LARGE_PAYMENT_FROM_UNUSUAL_PLACE"]["inputs"]["s_place_share"] == 0
    # No network came with the payment, so the network rules could not be checked.
    assert trace["R06_LARGE_PAYMENT_FROM_UNFAMILIAR_NETWORK"]["status"] == "not_evaluated"
    for lang in ("en", "bn"):
        assert check_grounding(template(decision.evidence, lang), decision.evidence).ok


# -------------------------------------------- the engine on a trained small world


@pytest.fixture(scope="module")
def world(trained, policy):
    data_dir, root, _ = trained
    bundle = registry.load(root=root)
    frame = policy_eval.add_context(
        load_frame(data_dir), pd.read_parquet(data_dir / "wallet_flags.parquet")
    )
    index = SimilarCases.build(bundle, frame, pd.read_parquet(data_dir / "cases.parquet"))
    frame = frame.assign(risk=bundle.score(frame[list(FEATURES)].to_numpy())["risk"])
    txns = pd.read_parquet(data_dir / "transactions.parquet").set_index("txn_id")
    return SimpleNamespace(
        bundle=bundle,
        frame=frame,
        index=index,
        txns=txns,
        engine=DecisionEngine(policy, bundle, index),
    )


def _decide(world, row, engine=None):
    txn = next(iter_txns(world.txns.loc[[row["txn_id"]]].reset_index()))
    context = {k: row[k] for k in policy_eval.CONTEXT_COLUMNS}
    return txn, (engine or world.engine).decide(txn, row[list(FEATURES)].to_numpy(float), context)


def _riskiest(world, txn_type):
    rows = world.frame[(world.frame["type"] == txn_type) & (world.frame["y"] == 1)]
    return rows.sort_values("risk").iloc[-1]


@pytest.mark.parametrize("txn_type", ["SEND_MONEY", "CASH_OUT"])
def test_an_alert_explains_itself(world, policy, txn_type):
    row = _riskiest(world, txn_type)
    txn, decision = _decide(world, row)
    thresholds = world.engine.thresholds

    assert decision.mode == "model" and decision.model_version == world.bundle.version
    assert decision.policy_version == "v1"
    assert decision.risk == pytest.approx(row["risk"])
    assert decision.tier == tier_for(row["risk"], thresholds) == "hold"
    assert decision.action == "hold_for_review" and decision.requires_review
    assert decision.risk_score >= 80 and decision.risk_band == "very high"
    assert (decision.scores["mule"] is None) == (txn_type == "CASH_OUT")

    assert {t["id"] for t in decision.rule_trace} == {r.id for r in policy.rules}
    raising = [r for r in decision.reasons if r["direction"] == "raises"]
    assert raising and all(r["detail_en"] and r["detail_bn"] for r in decision.reasons)
    # Model reasons quote this transaction's own feature values.
    for reason in decision.reasons:
        for stated in reason.get("facts", []):
            assert stated["value"] == pytest.approx(row[stated["feature"]], abs=1e-4)
            assert stated == fact(stated["feature"], row[stated["feature"]])

    assert set(decision.customer_message) == {"en", "bn"}
    assert decision.customer_message == policy.messages[decision.scenario]["hold"].model_dump()
    assert "RECORD_OUTCOME" in [a["id"] for a in decision.recommended_actions]
    assert decision.narrative == template(decision.evidence)
    for lang in ("en", "bn"):
        assert check_grounding(template(decision.evidence, lang), decision.evidence).ok

    # Nothing that leaves the engine as evidence carries a full wallet or agent number.
    dumped = json.dumps(decision.evidence, ensure_ascii=False)
    assert txn.sender_id not in dumped and txn.receiver_id not in dumped
    assert mask_id(txn.sender_id) in dumped
    json.dumps(decision.to_dict())  # serialisable as is


def test_a_low_risk_transaction_is_allowed_quietly(world):
    row = world.frame.sort_values("risk").iloc[0]
    _, decision = _decide(world, row)
    assert (decision.tier, decision.action, decision.requires_review) == ("allow", "proceed", False)
    assert decision.risk_score < 40 and decision.risk_band == "low"
    assert decision.customer_message is None and decision.narrative is None
    assert decision.reasons == () and decision.recommended_actions == ()
    assert len(decision.rule_trace) == 4  # the trace is kept even when nothing fires


def test_engine_falls_back_to_rules_when_the_model_is_missing_or_fails(world, policy, caplog):
    row = _riskiest(world, "SEND_MONEY")

    class Broken:
        version, manifest = "v0", world.bundle.manifest

        def score(self, x):
            raise RuntimeError("model file is corrupt")

    for engine in (DecisionEngine(policy), DecisionEngine(policy, Broken())):
        txn, decision = _decide(world, row, engine)
        assert decision.mode == "rules_only" and decision.model_version is None
        assert decision.risk is None and decision.risk_score is None
        assert decision.risk_band == "not scored" and decision.scores == {}
        assert decision.tier in ("allow", "warn", "step_up")  # never a hold on points
        fields = dict(zip(FEATURES, row[list(FEATURES)].to_numpy(float), strict=True))
        expected = apply_policy(policy, txn.type, fields, None, None)
        assert decision.tier == expected.tier
        assert decision.fallback_signals == expected.fallback_signals
        if decision.tier != "allow":
            assert "No model score was available" in decision.narrative
            assert check_grounding(decision.narrative, decision.evidence).ok
    assert "scoring failed; deciding on rules only" in caplog.text

    # Confirmed fraud is still held with no model at all.
    txn = next(iter_txns(world.txns.loc[[row["txn_id"]]].reset_index()))
    held = DecisionEngine(policy).decide(
        txn, row[list(FEATURES)].to_numpy(float), {"recipient_flagged": 1.0}
    )
    assert held.tier == "hold" and held.requires_review
    assert held.decided_by == "rule:R01_RECIPIENT_CONFIRMED_FRAUD"
    assert held.reasons[0]["source"] == "rule" and held.scenario == "scam"


def test_engine_rejects_a_feature_vector_of_the_wrong_length(world):
    txn = next(iter_txns(world.txns.iloc[[0]].reset_index()))
    with pytest.raises(ValueError, match=f"expected {len(FEATURES)} features"):
        world.engine.decide(txn, [0.0] * 5)


def test_display_score_is_anchored_to_the_tier_thresholds():
    assert [display_score(THRESHOLDS[t], THRESHOLDS) for t in THRESHOLDS] == [40, 60, 80]
    risks = np.logspace(-7, -0.0001, 200)
    scores = [display_score(r, THRESHOLDS) for r in risks]
    assert scores == sorted(scores) and scores[0] == 0 and scores[-1] <= 100
    for risk, score in zip(risks, scores, strict=True):
        assert (score >= 40) == (risk >= THRESHOLDS["warn"])
        assert (score >= 80) == (risk >= THRESHOLDS["hold"])
    assert display_score(1.0, THRESHOLDS) == 100 and display_score(0.0, THRESHOLDS) == 0


def test_context_comes_from_the_confirmed_fraud_flags(world):
    engine = FeatureEngine()
    txn = next(iter_txns(world.txns[world.txns["type"] == "SEND_MONEY"].iloc[[0]].reset_index()))
    context = context_from_engine(engine, txn)
    # The blocklist is live state: the offline frames have no column for it.
    assert set(context) - {"recipient_blocklisted"} == set(policy_eval.CONTEXT_COLUMNS)
    assert context["sender_flagged"] == 0 and context["recipient_flagged"] == 0
    assert context["recipient_blocklisted"] == 0
    listed = {txn.receiver_id: None, "W-other": 1.0}
    assert context_from_engine(engine, txn, listed)["recipient_blocklisted"] == 1
    expired = {txn.receiver_id: txn.ts - 1}
    assert context_from_engine(engine, txn, expired)["recipient_blocklisted"] == 0
    # A wallet with no history has no habits to compare against.
    assert math.isnan(context["s_place_share"]) and math.isnan(context["s_network_share"])
    engine.flagged[txn.receiver_id] = txn.ts - 60
    assert context_from_engine(engine, txn)["recipient_flagged"] == 1


# -------------------------------------------------------------- similar cases


def test_similar_case_index_holds_past_victim_transfers_only(world, trained, tmp_path):
    frame, index = world.frame, world.index
    past = frame[(frame["y_loss"] == 1) & (frame["fold"] != "test")]
    assert len(index) == len(past) > 0
    assert set(index.case_id) == set(past["case_id"])
    assert index.day.max() < frame.loc[frame["fold"] == "test", "day"].min()
    assert np.allclose(np.linalg.norm(index.vectors, axis=1), 1.0, atol=1e-5)

    # An indexed transfer finds its own case first, and each case appears once.
    row = past.iloc[len(past) // 2]
    contribution = world.bundle.contributions(row[list(FEATURES)].to_numpy(float))[0][0]
    found = index.query(contribution, k=3)
    assert found[0]["case_id"] == row["case_id"] and found[0]["similarity"] == pytest.approx(1.0)
    assert found[0]["typology"] == row["typology"]
    assert len({f["case_id"] for f in found}) == len(found) <= 3
    assert [f["similarity"] for f in found] == sorted(
        (f["similarity"] for f in found), reverse=True
    )
    cases = pd.read_parquet(trained[0] / "cases.parquet").set_index("case_id")
    assert found[0]["loss"] == cases.loc[row["case_id"], "loss"]
    assert index.query(-contribution) == []  # nothing resembles the opposite pattern

    index.save(tmp_path)
    reloaded = SimilarCases.load(tmp_path)
    assert reloaded.query(contribution, k=3) == found
    np.savez_compressed(
        tmp_path / "similar_cases.npz", **(index.__dict__ | {"vectors": index.vectors[:, :5]})
    )
    with pytest.raises(ValueError, match="different feature list"):
        SimilarCases.load(tmp_path)


# ---------------------------------------------------------- policy evaluation


def test_policy_evaluation_runs_end_to_end(trained):
    data_dir, root, model_report = trained
    report = policy_eval.run(data_dir, models_root=root)
    saved = json.loads((root / "v1" / policy_eval.REPORT_FILE).read_text())
    assert saved["policy_version"] == "v2" and saved["model_version"] == "v1"
    assert {"R05_LARGE_PAYMENT_FROM_UNUSUAL_PLACE"} <= set(saved["rules"])
    assert (root / "v1" / "similar_cases.npz").is_file()

    counts = report["tier_counts"]
    assert sum(counts["policy"].values()) == sum(counts["model_only"].values()) == report["rows"]
    assert counts["rules_only_fallback"]["hold"] == 0
    # The model-only tiers are the ones the training report measured.
    fixed = model_report["test"]["at_fixed_thresholds"]
    for tier in ("warn", "step_up", "hold"):
        assert report["outcomes"]["model_only"][tier]["alerts"] == fixed[tier]["alerts"]
        # Rules only ever add alerts.
        assert report["outcomes"]["policy"][tier]["alerts"] >= fixed[tier]["alerts"]
    assert set(report["rules"]) == {r.id for r in load_policy().rules}
    assert sum(report["decided_by"].values()) == report["rows"]

    single = report["single_decisions"]
    assert single["decisions"] > 0 and single["tier_differs_from_batch"] == 0
    assert single["case_notes_failing_grounding"] == 0, single["grounding_failures"]
    assert single["alerts_without_a_raising_reason"] == 0
    assert not math.isnan(single["decide_ms"]["p95"])


# ------------------------------------------- v4: the blocklist


def test_v4_keeps_v3_and_adds_only_the_blocklist_rule():
    v3, v4 = load_policy("v3"), load_policy("v4")
    assert v4.rules[: len(v3.rules)] == v3.rules
    assert [r.id for r in v4.rules[len(v3.rules) :]] == ["R08_RECIPIENT_ON_BLOCKLIST"]
    assert v4.tiers == v3.tiers and v4.messages == v3.messages and v4.fallback == v3.fallback
    assert v4.segments == v3.segments
    assert not any(rule.hard for rule in v4.rules[len(v3.rules) :])


def test_a_listed_receiver_asks_for_verification_and_never_holds_on_its_own():
    v4 = load_policy("v4")
    listed = {"recipient_blocklisted": 1.0}
    outcome = apply_policy(v4, "SEND_MONEY", listed, LOW, THRESHOLDS)
    assert (outcome.tier, outcome.decided_by) == ("step_up", "rule:R08_RECIPIENT_ON_BLOCKLIST")
    # A cash-out is not a payment to the listed wallet, and a model hold is left alone.
    assert apply_policy(v4, "CASH_OUT", listed, LOW, THRESHOLDS).tier == "allow"
    assert apply_policy(v4, "SEND_MONEY", listed, HIGH, THRESHOLDS).tier == "hold"
    # Without the fact (an offline frame, an unlisted wallet) the rule says nothing.
    assert apply_policy(v4, "SEND_MONEY", {}, LOW, THRESHOLDS).tier == "allow"
    assert apply_policy(v4, "SEND_MONEY", {"recipient_blocklisted": 0.0}, LOW, THRESHOLDS).tier == (
        "allow"
    )
