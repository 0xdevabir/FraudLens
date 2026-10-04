"""Telling people and other systems what happened, reliably.

Everything here is an outbox: a message is written to the `deliveries` table in
the same transaction as the change it reports (so it exists exactly when the
change does), and `Dispatcher` sends it afterwards, retrying with backoff. A
delivery that keeps failing becomes `dead`, where a supervisor can look at it and
send it again. Receivers get an `event_id` that stays the same across retries, so
they can ignore a repeat.

Payloads carry identifiers, never names, balances or free text. A customer SMS
carries only the fixed warning text from the policy.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from .models import Delivery, WebhookEndpoint

log = logging.getLogger(__name__)

EVENTS = (
    "decision.warn",
    "decision.step_up",
    "decision.hold",
    "case.verdict",
    "freeze.approved",
    "test.ping",
)
SMS_TIERS = ("step_up", "hold")
BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 3600
DISABLE_AFTER_FAILURES = 30
TIMEOUT_SECONDS = 5.0
SIGNATURE_TOLERANCE_SECONDS = 300


# ------------------------------------------------------------------ signing


def sign(secret: str, body: bytes, timestamp: int | None = None) -> str:
    """The `X-FraudLens-Signature` header: `t=<unix seconds>,v1=<hex HMAC-SHA256 of "t.body">`."""
    t = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify(secret: str, header: str, body: bytes, now: float | None = None) -> bool:
    """What a receiver does: recompute the signature, compare in constant time, and refuse
    a timestamp more than five minutes old so a captured delivery cannot be replayed."""
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        t = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs((time.time() if now is None else now) - t) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    return hmac.compare_digest(sign(secret, body, t), header)


def new_secret() -> str:
    return "whsec_" + secrets.token_hex(24)


# ----------------------------------------------------------- URL safety


class UnsafeUrl(ValueError):
    pass


def check_url(url: str, allow_private: bool = False) -> None:
    """Refuse a URL that would make the server call something it should not: not https,
    credentials in it, or a host that resolves to a private, loopback or link-local address."""
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise UnsafeUrl("the address must be an http or https URL")
    if parts.username or parts.password:
        raise UnsafeUrl("the address must not contain credentials")
    if allow_private:
        return
    if parts.scheme != "https":
        raise UnsafeUrl("the address must use https")
    try:
        found = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UnsafeUrl("the host name does not resolve") from None
    for *_, sockaddr in found:
        ip = ipaddress.ip_address(sockaddr[0])
        if not ip.is_global:
            raise UnsafeUrl("the address points at a private or internal network")


# ------------------------------------------------------------- enqueueing


def _event_id() -> str:
    return uuid.uuid4().hex


def endpoints_for(s: Session, event_type: str) -> list[WebhookEndpoint]:
    rows = s.scalars(select(WebhookEndpoint).where(WebhookEndpoint.active)).all()
    return [e for e in rows if event_type in e.events or "*" in e.events]


def enqueue(
    s: Session,
    event_type: str,
    data: dict,
    endpoints: list[WebhookEndpoint] | None = None,
    *,
    sms: dict | None = None,
) -> int:
    """Queue `event_type` for every endpoint subscribed to it, and an SMS if one is given
    (`{"wallet_id", "text_bn", "text_en"}`). Returns how many deliveries were queued."""
    if endpoints is None:
        endpoints = endpoints_for(s, event_type)
    made = datetime.now(UTC)
    event_id = _event_id()
    payload = {"id": event_id, "type": event_type, "created_at": made.isoformat(), "data": data}
    rows = [
        Delivery(
            kind="webhook", endpoint_id=e.id, event_type=event_type, event_id=event_id,
            payload=payload,
        )
        for e in endpoints
    ]  # fmt: skip
    if sms is not None:
        rows.append(
            Delivery(
                kind="sms", event_type=event_type, event_id=event_id,
                payload={"type": event_type, **sms},
            )
        )  # fmt: skip
    s.add_all(rows)
    return len(rows)


# ---------------------------------------------------------------- notifiers


class Notifier(Protocol):
    """Reaches a customer. An adapter turns a wallet and a fixed text into an SMS or a push."""

    def send(self, wallet_id: str, text_bn: str, text_en: str, event_type: str) -> None: ...


class ConsoleNotifier:
    """For development: writes the message to the log instead of sending it."""

    def send(self, wallet_id: str, text_bn: str, text_en: str, event_type: str) -> None:
        log.info("notification (%s) for wallet %s: %s", event_type, wallet_id[:1] + "***", text_en)


class HttpNotifier:
    """Posts to an SMS or push gateway: JSON with the wallet id and both texts."""

    def __init__(self, url: str, token: str | None, client: httpx.Client) -> None:
        self.url, self.token, self.client = url, token, client

    def send(self, wallet_id: str, text_bn: str, text_en: str, event_type: str) -> None:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        body = {"wallet_id": wallet_id, "text_bn": text_bn, "text_en": text_en, "event": event_type}
        response = self.client.post(self.url, json=body, headers=headers, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()


def make_notifier(settings: Settings, client: httpx.Client) -> Notifier | None:
    if settings.notify_adapter == "console":
        return ConsoleNotifier()
    if settings.notify_adapter == "http" and settings.sms_gateway_url:
        token = (
            settings.sms_gateway_token.get_secret_value() if settings.sms_gateway_token else None
        )
        return HttpNotifier(settings.sms_gateway_url, token, client)
    return None


# --------------------------------------------------------------- dispatcher


class Dispatcher(threading.Thread):
    """Sends due deliveries. One pass is `run_once`; the thread repeats it every few seconds."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        settings: Settings,
        client: httpx.Client | None = None,
        every: float = 2.0,
    ) -> None:
        super().__init__(name="fraudlens-dispatcher", daemon=True)
        self.sessions, self.settings, self.every = sessions, settings, every
        self.client = client or httpx.Client(follow_redirects=False)
        self.notifier = make_notifier(settings, self.client)
        self.sent = self.failed = 0
        self._stopping = threading.Event()

    def run(self) -> None:
        while not self._stopping.is_set():
            try:
                self.run_once()
            except Exception:
                log.exception("delivery pass failed; trying again shortly")
            self._stopping.wait(self.every)

    def stop(self) -> None:
        self._stopping.set()

    def run_once(self, limit: int = 50) -> int:
        """Send what is due. Returns how many deliveries were attempted."""
        now = datetime.now(UTC)
        with self.sessions() as s:
            due = s.scalars(
                select(Delivery)
                .where(Delivery.status == "pending", Delivery.next_attempt_at <= now)
                .order_by(Delivery.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            for delivery in due:
                self._attempt(s, delivery, now)
            s.commit()
        return len(due)

    def _attempt(self, s: Session, d: Delivery, now: datetime) -> None:
        endpoint = s.get(WebhookEndpoint, d.endpoint_id) if d.endpoint_id else None
        try:
            if d.kind == "webhook":
                self._webhook(d, endpoint)
            else:
                self._sms(d)
        except Exception as exc:
            self._failed(d, endpoint, now, exc)
        else:
            d.status, d.delivered_at, d.last_error = "delivered", now, None
            if endpoint is not None:
                endpoint.consecutive_failures = 0
            self.sent += 1

    def _webhook(self, d: Delivery, endpoint: WebhookEndpoint | None) -> None:
        if endpoint is None or not endpoint.active:
            raise _Dead("the endpoint is gone or disabled")
        try:
            check_url(endpoint.url, self.settings.webhook_allow_private)
        except UnsafeUrl as exc:
            raise _Dead(str(exc)) from None
        body = json.dumps(d.payload, separators=(",", ":"), ensure_ascii=False).encode()
        response = self.client.post(
            endpoint.url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-FraudLens-Event": d.event_type,
                "X-FraudLens-Delivery": d.event_id,
                "X-FraudLens-Signature": sign(endpoint.secret, body),
            },
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
        )
        d.last_status_code = response.status_code
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"the receiver answered {response.status_code}")

    def _sms(self, d: Delivery) -> None:
        if self.notifier is None:
            raise _Dead("no notification adapter is configured")
        p = d.payload
        self.notifier.send(p["wallet_id"], p["text_bn"], p["text_en"], p["type"])

    def _failed(self, d: Delivery, endpoint: WebhookEndpoint | None, now: datetime, exc: Exception):
        d.attempts += 1
        d.last_error = f"{type(exc).__name__}: {exc}"[:300]
        self.failed += 1
        if endpoint is not None:
            endpoint.consecutive_failures += 1
            if endpoint.consecutive_failures >= DISABLE_AFTER_FAILURES:
                endpoint.active = False
                log.warning("webhook endpoint %s disabled after repeated failures", endpoint.id)
        if isinstance(exc, _Dead) or d.attempts >= self.settings.delivery_max_attempts:
            d.status = "dead"
            return
        wait = min(BACKOFF_SECONDS * 2 ** (d.attempts - 1), MAX_BACKOFF_SECONDS)
        d.next_attempt_at = now + timedelta(seconds=wait)


class _Dead(Exception):
    """A failure that retrying cannot fix."""
