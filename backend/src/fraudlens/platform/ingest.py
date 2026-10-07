"""The signed ingest webhook: a core-banking system pushes transactions to FraudLens.

The protocol is in docs/INGEST.md. In short, every request carries

    X-FraudLens-Key-Id:    upay-20261007-77d1      which partner key signed it
    X-FraudLens-Timestamp: 1791364502              Unix seconds, within ±5 minutes of ours
    X-FraudLens-Nonce:     6f1c0e5d9a2b4c7e8f90    16-64 characters, used once
    X-FraudLens-Signature: v1=<hex HMAC-SHA256>    over timestamp, nonce, method, path, body

and the body is a pacs.008-shaped JSON message (ISO 20022 FI-to-FI customer
credit transfer). Each `CdtTrfTxInf` maps onto the same `TxnIn` that /v1/score
takes, so a transaction is validated by exactly one set of rules whichever way
it arrives. The same signature scheme, with the partner's newest key, signs the
decision callbacks going the other way.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, ValidationError
from redis import Redis

from .audit import WorkflowError
from .events import TxnIn
from .keys import Key, Keyring

KEY_HEADER = "X-FraudLens-Key-Id"
TIMESTAMP_HEADER = "X-FraudLens-Timestamp"
NONCE_HEADER = "X-FraudLens-Nonce"
SIGNATURE_HEADER = "X-FraudLens-Signature"
IDEMPOTENCY_HEADER = "Idempotency-Key"
SCHEME = "v1"

_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SIGNATURE = re.compile(r"^[0-9a-f]{64}$")
IDEMPOTENCY_TTL = 24 * 3600
SEEN_TXN_TTL = 7 * 24 * 3600
MAX_BATCH = 500
_PREFIX = "fraudlens:ingest"

# ---------------------------------------------------------------- signatures


def canonical(timestamp: str, nonce: str, method: str, path: str, body: bytes) -> bytes:
    """What is signed. The method and path (with its query) are in it, so a request
    signed for one endpoint, or for a queued answer, cannot be replayed at another."""
    return f"{timestamp}\n{nonce}\n{method.upper()}\n{path}\n".encode() + body


def sign(secret: str, timestamp: str, nonce: str, method: str, path: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), canonical(timestamp, nonce, method, path, body), "sha256")
    return f"{SCHEME}={mac.hexdigest()}"


def signed_headers(key: Key, method: str, path: str, body: bytes, nonce: str) -> dict[str, str]:
    """Headers for a request this side sends (the decision callback)."""
    timestamp = str(int(time.time()))
    return {
        KEY_HEADER: key.kid,
        TIMESTAMP_HEADER: timestamp,
        NONCE_HEADER: nonce,
        SIGNATURE_HEADER: sign(key.secret, timestamp, nonce, method, path, body),
        "Content-Type": "application/json",
    }


def _denied(code: str, message: str) -> WorkflowError:
    return WorkflowError(401, code, message)


def verify(
    ring: Keyring,
    headers: Mapping[str, str],
    method: str,
    path: str,
    body: bytes,
    window: int,
    now: float | None = None,
) -> Key:
    """The partner key that signed this request, or a 401 saying what is wrong.
    The answers name the check that failed and never what was expected."""
    kid, timestamp = headers.get(KEY_HEADER), headers.get(TIMESTAMP_HEADER)
    nonce, signature = headers.get(NONCE_HEADER), headers.get(SIGNATURE_HEADER)
    if not (kid and timestamp and nonce and signature):
        raise _denied(
            "unsigned", f"{KEY_HEADER}, {TIMESTAMP_HEADER}, {NONCE_HEADER} and "
            f"{SIGNATURE_HEADER} are required",
        )  # fmt: skip
    key = ring.verifying(kid)
    if key is None or key.partner is None:
        raise _denied("unknown_key", "the key id is unknown or retired")
    if not timestamp.isdigit() or len(timestamp) > 12:
        raise _denied("stale_timestamp", "the timestamp must be Unix seconds")
    now = time.time() if now is None else now
    if abs(now - int(timestamp)) > window:
        raise _denied("stale_timestamp", f"the timestamp must be within {window} seconds of now")
    if not _NONCE.fullmatch(nonce):
        raise _denied("bad_nonce", "the nonce must be 16 to 64 letters, digits, - or _")
    expected = sign(key.secret, timestamp, nonce, method, path, body)
    offered = [s.strip() for s in signature.split(",")]  # several during a scheme change
    if not any(
        s.startswith(f"{SCHEME}=")
        and _SIGNATURE.fullmatch(s[len(SCHEME) + 1 :])
        and hmac.compare_digest(s, expected)
        for s in offered
    ):
        raise _denied("bad_signature", "the signature does not match the request")
    return key


def claim_nonce(redis: Redis, key: Key, nonce: str, window: int) -> None:
    """A signed request is accepted once. The nonce is remembered for twice the
    timestamp window, after which the timestamp check refuses the request anyway."""
    if not redis.set(f"{_PREFIX}:nonce:{key.kid}:{nonce}", "1", nx=True, ex=2 * window):
        raise WorkflowError(409, "replayed", "this signed request has already been received")


# ---------------------------------------------------------------- idempotency


class Idempotency:
    """`Idempotency-Key`: a retry with the same key and body gets the first answer back.

    The key is per partner. The same key with a different body is refused, so a key
    cannot be reused by mistake for another payment."""

    def __init__(self, redis: Redis, partner: str, value: str | None, body: bytes) -> None:
        if value is not None and not _IDEMPOTENCY_KEY.fullmatch(value):
            raise WorkflowError(
                400, "bad_idempotency_key", "Idempotency-Key must be 1-128 of A-Z a-z 0-9 _ . : -"
            )
        self.redis = redis
        self.key = f"{_PREFIX}:idem:{partner}:{value}" if value else None
        self.digest = hashlib.sha256(body).hexdigest()

    def begin(self) -> tuple[int, dict] | None:
        """The stored answer to replay, or None when this request is the first."""
        if self.key is None:
            return None
        marker = json.dumps({"digest": self.digest})
        if self.redis.set(self.key, marker, nx=True, ex=IDEMPOTENCY_TTL):
            return None
        stored = json.loads(self.redis.get(self.key) or marker)
        if stored["digest"] != self.digest:
            raise WorkflowError(
                422, "idempotency_key_reused", "this Idempotency-Key was used for another body"
            )
        if "status" not in stored:
            raise WorkflowError(
                409, "in_progress", "a request with this Idempotency-Key is being processed",
                retry_after_seconds=1,
            )  # fmt: skip
        return stored["status"], stored["body"]

    def finish(self, status: int, body: dict) -> None:
        if self.key is not None:
            record = {"digest": self.digest, "status": status, "body": body}
            self.redis.set(self.key, json.dumps(record, default=str), ex=IDEMPOTENCY_TTL)

    def abandon(self) -> None:
        """The request failed before an answer worth keeping: a retry may run it again."""
        if self.key is not None:
            self.redis.delete(self.key)


def first_sightings(redis: Redis, txn_ids: list[int]) -> list[bool]:
    """For each transaction id, whether this is the first time ingest has seen it."""
    pipe = redis.pipeline(transaction=False)
    for txn_id in txn_ids:
        pipe.set(f"{_PREFIX}:txn:{txn_id}", "1", nx=True, ex=SEEN_TXN_TTL)
    return [bool(new) for new in pipe.execute()]


def forget_sightings(redis: Redis, txn_ids: list[int]) -> None:
    if txn_ids:
        redis.delete(*(f"{_PREFIX}:txn:{txn_id}" for txn_id in txn_ids))


# ---------------------------------------------------------------- pacs.008


Max35Text = Field(pattern=r"^[A-Za-z0-9/:._-]{1,35}$")


class _Iso(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GroupHeader(_Iso):
    MsgId: str = Max35Text
    CreDtTm: datetime
    NbOfTxs: int | None = Field(default=None, ge=1, le=MAX_BATCH)


class PaymentId(_Iso):
    EndToEndId: str = Max35Text
    TxId: str = Field(pattern=r"^[0-9]{1,18}$")  # the platform's numeric transaction id


class Proprietary(_Iso):
    Prtry: str = Field(max_length=35)


class CategoryPurpose(_Iso):
    CtgyPurp: Proprietary  # SEND_MONEY, CASH_OUT, ...


class Amount(_Iso):
    Ccy: Literal["BDT"]
    Amt: float = Field(gt=0, le=10_000_000, allow_inf_nan=False)


class SchemeName(_Iso):
    Prtry: Literal["wallet", "agent", "bank", "merchant", "telco", "biller"]


class OtherId(_Iso):
    Id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    SchmeNm: SchemeName


class AccountId(_Iso):
    Othr: OtherId


class Account(_Iso):
    Id: AccountId


class Envelope(_Iso):
    """Context a credit transfer does not carry, but the channel knows."""

    DbtrBalBefore: float | None = Field(default=None, ge=0, le=1e10, allow_inf_nan=False)
    DvcId: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")
    Chanl: Literal["app", "ussd", "agent", "bank"]
    Dstrct: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z' .-]+$")
    IPAddr: IPvAnyAddress | None = None


class SupplementaryData(_Iso):
    Envlp: Envelope


class CreditTransfer(_Iso):
    PmtId: PaymentId
    PmtTpInf: CategoryPurpose
    IntrBkSttlmAmt: Amount
    AccptncDtTm: datetime  # when the customer confirmed: the transaction's time
    DbtrAcct: Account
    CdtrAcct: Account
    SplmtryData: SupplementaryData

    def txn(self) -> TxnIn:
        """The platform's own event. Raises ValidationError with the platform's rules."""
        debtor, creditor = self.DbtrAcct.Id.Othr, self.CdtrAcct.Id.Othr
        extra = self.SplmtryData.Envlp
        return TxnIn.model_validate(
            {
                "txn_id": int(self.PmtId.TxId),
                "ts": self.AccptncDtTm,
                "type": self.PmtTpInf.CtgyPurp.Prtry,
                "sender_id": debtor.Id,
                "sender_type": debtor.SchmeNm.Prtry,
                "receiver_id": creditor.Id,
                "receiver_type": creditor.SchmeNm.Prtry,
                "amount": self.IntrBkSttlmAmt.Amt,
                "sender_balance_before": extra.DbtrBalBefore,
                "device_id": extra.DvcId,
                "channel": extra.Chanl,
                "district": extra.Dstrct,
                "ip": extra.IPAddr,
                "source": "live",
            }
        )


# TxnIn field -> where it sits in a CdtTrfTxInf. docs/INGEST.md has the same table.
ISO_PATH = {
    "txn_id": ("PmtId", "TxId"),
    "ts": ("AccptncDtTm",),
    "type": ("PmtTpInf", "CtgyPurp", "Prtry"),
    "sender_id": ("DbtrAcct", "Id", "Othr", "Id"),
    "sender_type": ("DbtrAcct", "Id", "Othr", "SchmeNm", "Prtry"),
    "receiver_id": ("CdtrAcct", "Id", "Othr", "Id"),
    "receiver_type": ("CdtrAcct", "Id", "Othr", "SchmeNm", "Prtry"),
    "amount": ("IntrBkSttlmAmt", "Amt"),
    "sender_balance_before": ("SplmtryData", "Envlp", "DbtrBalBefore"),
    "device_id": ("SplmtryData", "Envlp", "DvcId"),
    "channel": ("SplmtryData", "Envlp", "Chanl"),
    "district": ("SplmtryData", "Envlp", "Dstrct"),
    "ip": ("SplmtryData", "Envlp", "IPAddr"),
}


class Pacs008(_Iso):
    """FIToFICstmrCdtTrf, in JSON, with the elements FraudLens uses."""

    GrpHdr: GroupHeader
    CdtTrfTxInf: list[CreditTransfer] = Field(min_length=1, max_length=MAX_BATCH)


def parse(body: bytes, single: bool) -> tuple[Pacs008, list[TxnIn]]:
    """The message and its transactions, or the `ValidationError`s with their place
    in the message. Every transaction is checked before any is accepted."""
    message = Pacs008.model_validate_json(body)
    count = len(message.CdtTrfTxInf)
    problems: list[dict] = []
    if single and count != 1:
        problems.append(
            {"loc": ("CdtTrfTxInf",), "msg": "exactly one transaction; use /batch for more"}
        )
    if message.GrpHdr.NbOfTxs is not None and message.GrpHdr.NbOfTxs != count:
        problems.append({"loc": ("GrpHdr", "NbOfTxs"), "msg": "does not match CdtTrfTxInf"})
    txns = []
    for i, transfer in enumerate(message.CdtTrfTxInf):
        if int(transfer.PmtId.TxId) >= 2**62:
            problems.append({"loc": ("CdtTrfTxInf", i, "PmtId", "TxId"), "msg": "too large"})
            continue
        try:
            txns.append(transfer.txn())
        except ValidationError as exc:
            for e in exc.errors():  # reported where the partner put it, not by our name
                where = ISO_PATH.get(str(e["loc"][0]), ()) if e["loc"] else ()
                problems.append({"loc": ("CdtTrfTxInf", i, *where), "msg": e["msg"]})
    if problems:
        raise IngestInvalid(problems)
    return message, txns


class IngestInvalid(Exception):
    def __init__(self, errors: list[dict]) -> None:
        super().__init__(f"{len(errors)} problems")
        self.errors = errors
