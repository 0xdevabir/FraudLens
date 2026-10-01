"""The events the platform accepts, validated the same way from HTTP and from the stream."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from ..features import SCORED_TYPES, Txn

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,32}$")]

# transaction type -> (sender kind, receiver kind)
PARTIES = {
    "SEND_MONEY": ("wallet", "wallet"),
    "CASH_OUT": ("wallet", "agent"),
    "CASH_IN": ("agent", "wallet"),
    "ADD_MONEY": ("bank", "wallet"),
    "PAYMENT": ("wallet", "merchant"),
    "RECHARGE": ("wallet", "telco"),
    "BILL_PAY": ("wallet", "biller"),
}


def epoch(moment: datetime) -> float:
    """Epoch seconds. A time without a zone is taken as UTC, like the dataset's."""
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp()


def moment(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


@dataclass(frozen=True)
class TxnEvent:
    txn: Txn
    source: str = "live"  # "live": waits for the customer or an analyst; "replay": recorded


@dataclass(frozen=True)
class FlagEvent:
    wallet_id: str
    ts: float
    reason: str
    source: str = "upstream"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TxnIn(_In):
    kind: Literal["txn"] = "txn"
    txn_id: int = Field(ge=0, lt=2**62)
    ts: datetime
    type: Literal[
        "SEND_MONEY", "CASH_OUT", "CASH_IN", "ADD_MONEY", "PAYMENT", "RECHARGE", "BILL_PAY"
    ]
    sender_id: Identifier
    sender_type: Literal["wallet", "agent", "bank"]
    receiver_id: Identifier
    receiver_type: Literal["wallet", "agent", "merchant", "telco", "biller"]
    amount: float = Field(gt=0, le=10_000_000, allow_inf_nan=False)
    sender_balance_before: float | None = Field(default=None, ge=0, le=1e10, allow_inf_nan=False)
    device_id: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")
    channel: Literal["app", "ussd", "agent", "bank"]
    district: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z' .-]+$")
    source: Literal["live", "replay"] = "live"

    @model_validator(mode="after")
    def _consistent(self) -> TxnIn:
        if (self.sender_type, self.receiver_type) != PARTIES[self.type]:
            raise ValueError(f"{self.type} goes from {' to '.join(PARTIES[self.type])}")
        if self.sender_id == self.receiver_id:
            raise ValueError("sender and receiver are the same")
        if self.type in SCORED_TYPES and self.sender_balance_before is None:
            raise ValueError(f"{self.type} needs sender_balance_before")
        return self

    def event(self) -> TxnEvent:
        balance = self.sender_balance_before
        txn = Txn(
            self.txn_id,
            epoch(self.ts),
            self.type,
            self.sender_id,
            self.sender_type,
            self.receiver_id,
            self.receiver_type,
            self.amount,
            float("nan") if balance is None else balance,
            self.device_id,
            self.channel,
            self.district,
        )
        return TxnEvent(txn, self.source)


class FlagIn(_In):
    """A wallet confirmed as fraud by an investigation outside this platform."""

    kind: Literal["flag"] = "flag"
    wallet_id: Identifier
    flagged_at: datetime
    reason: str = Field(pattern=r"^[a-z_]{1,40}$")
    source: Literal["upstream", "replay"] = "upstream"

    def event(self) -> FlagEvent:
        return FlagEvent(self.wallet_id, epoch(self.flagged_at), self.reason, self.source)


EventIn = Annotated[TxnIn | FlagIn, Field(discriminator="kind")]
event_adapter: TypeAdapter[TxnIn | FlagIn] = TypeAdapter(EventIn)
