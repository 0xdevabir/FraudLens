"""Which scam pattern a customer-facing screen should name.

The policy's customer message is a fixed text per scenario and tier. A good
warning also asks the one question that fits the scam in front of the customer
("did someone claiming to be from your bank ask you to send this?"). This picks
that question's key from what the decision already recorded: the rules that
fired, its scenario, and the typology of the past cases it most resembles.

Nothing is scored here, and the key says no more than the message already does:
no score, no reason detail, nothing about the receiving wallet beyond what the
recipient check would say.
"""

from __future__ import annotations

from collections import defaultdict

CUES = (
    "reported_recipient",  # the receiving wallet is confirmed fraud (rule R01)
    "impersonation",  # someone posing as upay, a bank or an official
    "prize",  # a prize or lottery that needs a fee
    "investment",  # an investment that promises high returns
    "wrong_send",  # "I sent you money by mistake, send it back"
    "not_you",  # account takeover, or use from an unusual place or network
    "generic",
)

_TYPOLOGY = {
    "impersonation": "impersonation",
    "lottery_fee": "prize",
    "investment_scam": "investment",
    "wrong_send": "wrong_send",
    "account_takeover": "not_you",
}


def cue(tier: str, scenario: str | None, detail: dict | None) -> str | None:
    """The pattern key for an alert, or None for a payment that went straight through."""
    if tier == "allow":
        return None
    detail = detail or {}
    fired = {r.get("id") for r in detail.get("rule_trace") or [] if r.get("status") == "fired"}
    if "R01_RECIPIENT_CONFIRMED_FRAUD" in fired:
        return "reported_recipient"
    if scenario in ("takeover", "unusual_access"):
        return "not_you"
    weight: dict[str, float] = defaultdict(float)
    for case in detail.get("similar_cases") or []:
        if case.get("typology"):
            weight[case["typology"]] += case.get("similarity") or 0.0
    if weight:
        return _TYPOLOGY.get(max(weight, key=weight.__getitem__), "generic")
    return "generic"
