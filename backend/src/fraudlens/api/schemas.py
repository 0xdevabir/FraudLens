"""Request bodies. Unknown fields are rejected and every string has a length limit."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..platform.events import EventIn, Identifier

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=2000)]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Body):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    password: str = Field(min_length=1, max_length=256)


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


class ScamReport(Body):
    reporter_id: Identifier
    reported_wallet_id: Identifier
    txn_id: int | None = Field(default=None, ge=0, lt=2**62)
    category: Literal[
        "impersonation", "prize_or_lottery", "investment", "account_takeover", "wrong_send", "other"
    ]
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
