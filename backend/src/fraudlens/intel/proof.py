"""Did this payment really arrive? Answered from the ledger, never from the proof.

A forged screenshot or SMS is made to look exactly like a real one, so nothing
is learned by reading it more closely. The question that settles it is whether
the transaction exists: same receiver, same amount, completed. That is a lookup.

Privacy: a wallet can only verify payments made *to it*. Asking about anyone
else's transaction gets the same answer as asking about one that does not exist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..platform.models import Transaction

RECENT = timedelta(hours=48)  # how far back a payment is looked for when no ID is given

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_TXN_ID = re.compile(r"(?:Trx|Txn)\s?ID[:\s#]*([A-Z0-9]{1,20})", re.IGNORECASE)
_AMOUNT = re.compile(
    r"(?:Tk\.?|BDT|৳)\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:টাকা|taka|tk)(?!\w)", re.IGNORECASE
)
_MAX_ID = 2**63 - 1  # the ledger's ID column is a signed 64-bit integer


@dataclass(frozen=True)
class Claim:
    wallet_id: str  # who is asking: the wallet that was told it received money
    txn_id: int | None
    amount: float | None


def read_claim(wallet_id: str, txn_id: str | None, amount: float | None, message: str) -> Claim:
    """What is being claimed, from the fields given and, failing those, the pasted text."""
    text = message.translate(_BN_DIGITS)
    raw_id = txn_id or (m.group(1) if (m := _TXN_ID.search(text)) else None)
    # A real ID is a number. A made-up one usually is not, and that is an answer in itself.
    number = int(raw_id) if raw_id and raw_id.isascii() and raw_id.isdigit() else None
    if number is not None and number > _MAX_ID:
        number = None
    if amount is None and (m := _AMOUNT.search(text)):
        amount = float((m.group(1) or m.group(2)).replace(",", ""))
    return Claim(wallet_id, number, amount)


def judge(claim: Claim, txn: Transaction | None) -> dict:
    """Compare the claim with the ledger row found for it (or with none)."""
    if txn is None or txn.receiver_id != claim.wallet_id:
        return {"status": "not_found", "checks": {"exists": False}, "transaction": None}
    checks = {
        "exists": True,
        "amount_matches": claim.amount is None or abs(txn.amount - claim.amount) < 0.01,
        "completed": txn.status == "completed",
    }
    return {
        "status": "verified" if all(checks.values()) else "mismatch",
        "checks": checks,
        "transaction": {
            "txn_id": txn.txn_id,
            "ts": txn.ts.isoformat(),
            "type": txn.type,
            "amount": txn.amount,
            "status": txn.status,
            "sender_id": txn.sender_id,
        },
    }


def find(s: Session, claim: Claim, now: datetime) -> Transaction | None:
    if claim.txn_id is not None:
        return s.get(Transaction, claim.txn_id)
    if claim.amount is None:
        return None
    # No ID: the newest completed payment of that amount into this wallet, if any.
    return s.scalars(
        select(Transaction)
        .where(
            Transaction.receiver_id == claim.wallet_id,
            Transaction.status == "completed",
            Transaction.amount.between(claim.amount - 0.01, claim.amount + 0.01),
            Transaction.ts >= now - RECENT,
            Transaction.ts <= now,
        )
        .order_by(Transaction.ts.desc())
        .limit(1)
    ).first()


def verify(s: Session, claim: Claim, now: datetime) -> dict:
    return judge(claim, find(s, claim, now))
