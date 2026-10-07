"""A flagged message, then a payment: the two read together.

A customer who asks "is this a scam?" and is told yes or maybe, and then pays the
wallet that message named, or the amount it asked for, within half an hour, is
doing what the message told them to. Each signal is weak alone; together they are
the scam. `MessageMemory` keeps just enough of each flagged message to see it:

- the wallet IDs, phone numbers and amounts the message named;
- the level and categories it was given; when it was checked.

Never the text. Entries live in this process only, for `WINDOW` minutes, a few per
wallet, and are gone on restart: this is a short reminder, not a record.

`link` is what the scorer asks for each payment; `explain` is the reason shown.
"""

from __future__ import annotations

import re
import threading
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

WINDOW = timedelta(minutes=30)
PER_WALLET = 5
AMOUNT_TOLERANCE = 1.0  # taka

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_WALLET = re.compile(r"\b([A-Za-z]\d{4,})\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?88)?(01\d{9})(?!\d)")
# As in `proof.py`, with the spellings a scam message uses for taka.
_AMOUNT = re.compile(
    r"(?:tk\.?|bdt|৳)\s*(\d[\d,]*(?:\.\d+)?)"
    r"|(\d[\d,]*(?:\.\d+)?)\s*(?:টাকা|taka|takaa|tk|/-)(?!\w)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Flagged:
    at: datetime
    level: str
    categories: tuple[str, ...]
    wallets: frozenset[str]
    phones: frozenset[str]
    amounts: tuple[float, ...]


def targets(text: str) -> tuple[frozenset[str], frozenset[str], tuple[float, ...]]:
    """The wallet IDs, phone numbers and amounts a message names."""
    text = unicodedata.normalize("NFKC", text).translate(_BN_DIGITS)
    wallets = frozenset(m.upper() for m in _WALLET.findall(text))
    phones = frozenset(_PHONE.findall(text))
    amounts = []
    for m in _AMOUNT.finditer(text):
        try:
            value = float((m.group(1) or m.group(2)).replace(",", ""))
        except ValueError:
            continue
        if value > 0:
            amounts.append(value)
    return wallets, phones, tuple(dict.fromkeys(amounts))


class MessageMemory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_wallet: dict[str, deque[Flagged]] = defaultdict(lambda: deque(maxlen=PER_WALLET))

    def remember(self, wallet_id: str, text: str, found: dict, now: datetime) -> bool:
        """Keep what a checked message named, if it was flagged and named anything."""
        if found.get("level", "none") == "none":
            return False
        wallets, phones, amounts = targets(text)
        if not (wallets or phones or amounts):
            return False
        entry = Flagged(
            now, found["level"], tuple(c["id"] for c in found.get("categories", ())),
            wallets, phones, amounts,
        )  # fmt: skip
        with self._lock:
            self._by_wallet[wallet_id].append(entry)
        return True

    def link(
        self, sender_id: str, receiver_id: str, amount: float | None, now: datetime
    ) -> dict | None:
        """The most recent flagged message this payment follows, and how it matches."""
        with self._lock:
            entries = list(self._by_wallet.get(sender_id, ()))
        for entry in reversed(entries):
            age = now - entry.at
            if age < timedelta(0) or age > WINDOW:
                continue
            matched = []
            if receiver_id.upper() in entry.wallets:
                matched.append("receiver")
            if amount is not None and any(
                abs(amount - a) <= AMOUNT_TOLERANCE for a in entry.amounts
            ):
                matched.append("amount")
            if matched:
                return {
                    "matched": matched,
                    "minutes_ago": int(age.total_seconds() // 60),
                    "level": entry.level,
                    "categories": list(entry.categories),
                }
        return None

    def forget(self, wallet_id: str) -> None:
        with self._lock:
            self._by_wallet.pop(wallet_id, None)


def escalate(decision, link: dict, policy):
    """The decision with the message link added: an allowed payment becomes at least
    a warning (the customer still decides), an alert keeps its tier and gains the reason."""
    reason = explain(link)
    evidence = dict(decision.evidence) | {"message_link": link}
    changes: dict = {
        "reasons": (reason, *decision.reasons),
        "evidence": evidence,
    }
    if decision.tier == "allow":
        changes |= {
            "tier": "warn",
            "action": policy.tiers["warn"].action,
            "scenario": "scam",
            "customer_message": policy.messages["scam"]["warn"].model_dump(),
            "decided_by": "message_link",
        }
    return replace(decision, **changes)


def explain(link: dict) -> dict:
    """The reason shown with the payment, in the shape the decision engine uses."""
    what_en = " and ".join(
        {"receiver": "the wallet", "amount": "the amount"}[m] for m in link["matched"]
    )
    what_bn = " ও ".join({"receiver": "ওয়ালেট", "amount": "টাকার পরিমাণ"}[m] for m in link["matched"])
    minutes = link["minutes_ago"]
    return {
        "code": "follows_flagged_message",
        "source": "message",
        "direction": "raises",
        "title_en": "Follows a message flagged as a likely scam",
        "title_bn": "সম্ভাব্য প্রতারণার বার্তার পরেই এই পেমেন্ট",
        "detail_en": (
            f"{minutes} minutes ago you checked a message that looked like a scam; "
            f"this payment goes to {what_en} it named"
        ),
        "detail_bn": (
            f"{minutes} মিনিট আগে যাচাই করা একটি বার্তা প্রতারণার মতো ছিল; "
            f"এই পেমেন্ট সেই বার্তায় লেখা {what_bn} অনুযায়ী যাচ্ছে"
        ),
    }
