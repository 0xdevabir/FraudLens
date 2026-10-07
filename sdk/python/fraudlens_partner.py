"""FraudLens partner client: sign ingest requests, verify decision callbacks.

One file, standard library only, so a core-banking team can vendor it. The
protocol is in docs/INGEST.md; this file is its reference implementation.

    from fraudlens_partner import Client, credit_transfer, pacs008

    client = Client("https://fraudlens.example", key_id="upay-20261007-77d1", secret="...")
    message = pacs008("UPAY-0001", [credit_transfer(...)])
    status, answer = client.send(message, wait_for_decision=True)

A callback receiver checks what FraudLens sends with `verify(...)` before
trusting it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import urlsplit

KEY_HEADER = "X-FraudLens-Key-Id"
TIMESTAMP_HEADER = "X-FraudLens-Timestamp"
NONCE_HEADER = "X-FraudLens-Nonce"
SIGNATURE_HEADER = "X-FraudLens-Signature"
WINDOW_SECONDS = 300


def canonical(timestamp: str, nonce: str, method: str, path: str, body: bytes) -> bytes:
    return f"{timestamp}\n{nonce}\n{method.upper()}\n{path}\n".encode() + body


def sign(secret: str, timestamp: str, nonce: str, method: str, path: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), canonical(timestamp, nonce, method, path, body), "sha256")
    return "v1=" + digest.hexdigest()


def signed_headers(
    key_id: str, secret: str, method: str, path: str, body: bytes, now: float | None = None
) -> dict[str, str]:
    """Fresh headers for one attempt. A retry needs new ones: the nonce is single use."""
    timestamp = str(int(time.time() if now is None else now))
    nonce = secrets.token_hex(16)
    return {
        KEY_HEADER: key_id,
        TIMESTAMP_HEADER: timestamp,
        NONCE_HEADER: nonce,
        SIGNATURE_HEADER: sign(secret, timestamp, nonce, method, path, body),
        "Content-Type": "application/json",
    }


def verify(
    secrets_by_kid: Mapping[str, str],
    headers: Mapping[str, str],
    method: str,
    path: str,
    body: bytes,
    seen_nonce=None,
    now: float | None = None,
) -> bool:
    """Is this callback from FraudLens? `secrets_by_kid` holds your current and, during
    a rotation, previous key. `seen_nonce(nonce) -> bool` should record the nonce and
    say whether it was already used (pass one backed by your cache to stop replays)."""
    lower = {k.lower(): v for k, v in headers.items()}
    kid = lower.get(KEY_HEADER.lower(), "")
    timestamp = lower.get(TIMESTAMP_HEADER.lower(), "")
    nonce = lower.get(NONCE_HEADER.lower(), "")
    offered = lower.get(SIGNATURE_HEADER.lower(), "")
    secret = secrets_by_kid.get(kid)
    if not (secret and timestamp.isdigit() and nonce and offered):
        return False
    if abs((time.time() if now is None else now) - int(timestamp)) > WINDOW_SECONDS:
        return False
    expected = sign(secret, timestamp, nonce, method, path, body)
    if not hmac.compare_digest(offered, expected):
        return False
    return not (seen_nonce and seen_nonce(nonce))


# ------------------------------------------------------------------ pacs.008


def credit_transfer(
    *,
    txn_id: int,
    end_to_end_id: str,
    accepted_at: datetime,
    purpose: str,
    amount: float,
    debtor: tuple[str, str],
    creditor: tuple[str, str],
    channel: str,
    district: str,
    debtor_balance_before: float | None = None,
    device_id: str = "",
    ip: str | None = None,
) -> dict:
    """One CdtTrfTxInf. `debtor` and `creditor` are (account id, kind), the kind being
    wallet, agent, bank, merchant, telco or biller."""
    envelope = {"Chanl": channel, "Dstrct": district, "DvcId": device_id}
    if debtor_balance_before is not None:
        envelope["DbtrBalBefore"] = debtor_balance_before
    if ip:
        envelope["IPAddr"] = ip

    def account(party: tuple[str, str]) -> dict:
        return {"Id": {"Othr": {"Id": party[0], "SchmeNm": {"Prtry": party[1]}}}}

    return {
        "PmtId": {"EndToEndId": end_to_end_id, "TxId": str(txn_id)},
        "PmtTpInf": {"CtgyPurp": {"Prtry": purpose}},
        "IntrBkSttlmAmt": {"Ccy": "BDT", "Amt": amount},
        "AccptncDtTm": accepted_at.isoformat(),
        "DbtrAcct": account(debtor),
        "CdtrAcct": account(creditor),
        "SplmtryData": {"Envlp": envelope},
    }


def pacs008(msg_id: str, transfers: list[dict]) -> dict:
    return {
        "GrpHdr": {
            "MsgId": msg_id,
            "CreDtTm": datetime.now(UTC).isoformat(),
            "NbOfTxs": len(transfers),
        },
        "CdtTrfTxInf": transfers,
    }


# ------------------------------------------------------------------ client


class Client:
    def __init__(self, base_url: str, key_id: str, secret: str, timeout: float = 10.0) -> None:
        self.base_url, self.key_id, self.secret = base_url.rstrip("/"), key_id, secret
        self.timeout = timeout

    def request(self, path: str, body: bytes, idempotency_key: str | None) -> tuple[int, dict]:
        signed_path = urlsplit(self.base_url).path + path
        headers = signed_headers(self.key_id, self.secret, "POST", signed_path, body)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        req = urllib.request.Request(self.base_url + path, body, headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def send(
        self,
        message: dict,
        wait_for_decision: bool = False,
        idempotency_key: str | None = None,
        attempts: int = 4,
    ) -> tuple[int, dict]:
        """POST a message, retrying network errors, 409 in_progress and 5xx with backoff.
        Each attempt is signed afresh; the idempotency key (default: the MsgId) makes
        the retries safe."""
        batch = len(message["CdtTrfTxInf"]) > 1
        path = "/v1/ingest/transactions" + ("/batch" if batch else "")
        if wait_for_decision and not batch:
            path += "?wait=decision"
        body = json.dumps(message, separators=(",", ":")).encode()
        key = idempotency_key or message["GrpHdr"]["MsgId"]
        for attempt in range(attempts):
            try:
                status, answer = self.request(path, body, key)
            except (urllib.error.URLError, TimeoutError):
                if attempt == attempts - 1:
                    raise
            else:
                code = answer.get("error", {}).get("code")
                if status < 500 and code != "in_progress" or attempt == attempts - 1:
                    return status, answer
            time.sleep(min(30.0, 0.5 * 2**attempt))
        raise AssertionError("unreachable")
