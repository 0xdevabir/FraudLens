"""Request bodies. Unknown fields are rejected and every string has a length limit."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    IPvAnyAddress,
    StringConstraints,
    model_validator,
)

from ..platform.events import EventIn, Identifier

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=2000)]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Body):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    password: str = Field(min_length=1, max_length=256)


class DemoLogin(Body):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")


class Events(Body):
    events: list[EventIn] = Field(min_length=1, max_length=500)


class OpenCase(Body):
    wallet_id: Identifier
    reason: Reason


class Assign(Body):
    assignee_id: int | None = Field(
        default=None, ge=1, le=2**31 - 1, description="default: yourself"
    )


class Note(Body):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class Escalate(Body):
    reason: Reason


class Verdict(Body):
    verdict: Literal["confirmed_fraud", "legitimate", "inconclusive"]
    note: Reason


class FreezeRequestIn(Body):
    reason: Reason
    case_id: int | None = Field(default=None, ge=1, le=2**31 - 1)


class FreezeDecision(Body):
    note: Reason


class Unfreeze(Body):
    reason: Reason


class RecipientCheck(Body):
    sender_id: Identifier
    receiver_id: Identifier


class CustomerResponse(Body):
    wallet_id: Identifier
    action: Literal["proceed", "cancel"]
    step_up_passed: bool = False  # the channel re-authenticated the customer (PIN/OTP)


class DemoPayment(Body):
    sender_id: Identifier
    receiver_id: Identifier
    amount: float = Field(gt=0, le=10_000_000, allow_inf_nan=False)
    # Where the demo customer is and how they are connected; the app would know both.
    district: str | None = Field(default=None, min_length=1, max_length=40)
    ip: IPvAnyAddress | None = None


class DemoResponse(Body):
    txn_id: int = Field(ge=0, lt=2**62)
    action: Literal["proceed", "cancel"]
    step_up_passed: bool = False


class DemoClock(Body):
    minutes: int = Field(ge=1, le=60)


class Reveal(Body):
    object_type: Literal["wallet", "device", "agent"]
    object_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    case_id: int | None = Field(default=None, ge=1, le=2**31 - 1)


class ScamReport(Body):
    reporter_id: Identifier
    reported_wallet_id: Identifier
    txn_id: int | None = Field(default=None, ge=0, lt=2**62)
    category: Literal[
        "impersonation", "prize_or_lottery", "investment", "account_takeover", "wrong_send",
        "fake_payment", "merchant_or_marketplace", "phishing_link_or_app", "job_or_loan", "other",
    ]  # fmt: skip
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]


AppealReason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)
]
Relation = Literal[
    "family", "friend", "business", "seller", "landlord", "employer", "other", "none"
]


class AppealIn(Body):
    """'This is a genuine payment.' Typed by the customer: shown masked, never read to decide."""

    wallet_id: Identifier
    relation: Relation
    reason: AppealReason


class DemoAppeal(Body):
    txn_id: int = Field(ge=0, lt=2**62)
    relation: Relation
    reason: AppealReason


class AppealDecision(Body):
    note: Reason


class RefundDecline(Body):
    note: Reason


class MessageCheck(Body):
    """A message the customer received and wants checked. It is read, never stored."""

    wallet_id: Identifier
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class PaymentVerify(Body):
    """'Someone says they paid me.' At least one of the three must say which payment."""

    wallet_id: Identifier  # the wallet that was told it received money
    txn_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{1,32}$")
    amount: float | None = Field(default=None, gt=0, le=10_000_000, allow_inf_nan=False)
    message: str = Field(default="", max_length=2000)  # the SMS or caption they were shown

    @model_validator(mode="after")
    def _something_to_check(self) -> PaymentVerify:
        if self.txn_id is None and self.amount is None and not self.message.strip():
            raise ValueError("give a transaction ID, an amount or the message you were shown")
        return self
