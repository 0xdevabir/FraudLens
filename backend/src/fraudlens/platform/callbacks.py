"""Decision callbacks: tell the partner what was decided on a transaction it queued.

Optional, per partner (`callback_url` in the ingest keyring). A queued transaction
puts a job on a Redis sorted set, scored by when it is next due. Any API process
may deliver it: removing the job from the set is the claim, so each attempt
happens once. Until the transaction has been decided the job waits; once it has,
the outcome is POSTed, signed like an ingest request with the partner's newest
key. A failed delivery is retried with exponential backoff and jitter, and after
the last attempt it goes to a dead list for an operator to look at.

Delivery is at least once (a crash after the POST and before the claim is
dropped repeats it), so the partner should treat `txn_id` as the idempotency key.
"""

from __future__ import annotations

import json
import logging
import random
import secrets
import threading
import time
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
from redis import Redis
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from .ingest import signed_headers
from .keys import load_keyring
from .models import Decision, Transaction

log = logging.getLogger(__name__)

SCHEDULE = "fraudlens:callbacks"
DEAD = "fraudlens:callbacks:dead"
DEAD_MAXLEN = 10_000
MAX_ATTEMPTS = 8
BASE_DELAY = 2.0  # seconds; doubled each attempt
MAX_DELAY = 600.0
POLL_DELAY = 2.0  # while the transaction is still in the stream
DECISION_WAIT = 600.0  # after this, report it as not processed (dead-lettered in the stream)
TIMEOUT = 5.0

Post = Callable[[str, dict[str, str], bytes], int]


def schedule(redis: Redis, partner: str, txn_id: int, end_to_end_id: str, now: float) -> None:
    job = {"partner": partner, "txn_id": txn_id, "e2e": end_to_end_id, "queued": now, "try": 0}
    redis.zadd(SCHEDULE, {json.dumps(job, sort_keys=True): now + POLL_DELAY})


def backoff(attempt: int) -> float:
    """Seconds before attempt `attempt + 1`: 2, 4, 8 ... capped, with ±20% jitter."""
    return min(MAX_DELAY, BASE_DELAY * 2**attempt) * random.uniform(0.8, 1.2)


def _http_post(url: str, headers: dict[str, str], body: bytes) -> int:
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
        return client.post(url, content=body, headers=headers).status_code


class Dispatcher(threading.Thread):
    def __init__(
        self,
        settings: Settings,
        sessions: sessionmaker[Session],
        redis: Redis,
        post: Post | None = None,
        interval: float = 1.0,
    ) -> None:
        super().__init__(name="fraudlens-callbacks", daemon=True)
        self.settings, self.sessions, self.redis = settings, sessions, redis
        self.post = post or _http_post
        self.interval = interval
        self.delivered = self.failed = self.dead = 0
        self._stopping = threading.Event()

    def outcome(self, txn_id: int) -> dict | None:
        """What the partner is told, or None while the transaction is not decided."""
        with self.sessions() as s:
            txn = s.get(Transaction, txn_id)
            if txn is None:
                return None
            decision = s.get(Decision, txn_id)
            summary = None
            if decision is not None:  # what to do, not why: as /v1/score tells the service
                summary = {
                    "tier": decision.tier,
                    "action": decision.action,
                    "requires_review": decision.requires_review,
                    "risk_score": decision.risk_score,
                    "risk_band": decision.risk_band,
                    "mode": decision.mode,
                    "model_version": decision.model_version,
                    "policy_version": decision.policy_version,
                    "decided_at": decision.decided_at.isoformat(),
                }
            return {
                "status": txn.status,
                "status_reason": txn.status_reason,
                "scored": decision is not None,
                "decision": summary,
            }

    def _later(self, job: dict, at: float) -> None:
        self.redis.zadd(SCHEDULE, {json.dumps(job, sort_keys=True): at})

    def _deliver(self, job: dict, now: float) -> None:
        outcome = self.outcome(job["txn_id"])
        if outcome is None:
            if now - job["queued"] < DECISION_WAIT:
                self._later(job, now + POLL_DELAY)
                return
            outcome = {"status": "not_processed", "scored": False, "decision": None}
        ring = load_keyring(self.settings.ingest_keyring)
        url, key = ring.callback_url(job["partner"]), ring.signing(job["partner"])
        if not url or key is None:  # the callback was switched off, or every key retired
            log.info("callback for %s dropped: no url or key", job["partner"])
            return
        payload = {"txn_id": job["txn_id"], "end_to_end_id": job["e2e"], **outcome}
        body = json.dumps(payload, separators=(",", ":")).encode()
        parts = urlsplit(url)
        path = parts.path or "/"
        path += f"?{parts.query}" if parts.query else ""
        headers = signed_headers(key, "POST", path, body, secrets.token_hex(16))
        try:
            status = self.post(url, headers, body)
        except httpx.HTTPError as exc:
            status, error = 0, type(exc).__name__
        else:
            error = f"HTTP {status}"
        if 200 <= status < 300:
            self.delivered += 1
            return
        self.failed += 1
        job = job | {"try": job["try"] + 1, "error": error}
        if job["try"] >= MAX_ATTEMPTS:
            self.dead += 1
            log.warning(
                "callback for txn %s to %s gave up: %s", job["txn_id"], job["partner"], error
            )
            pipe = self.redis.pipeline(transaction=False)
            pipe.lpush(DEAD, json.dumps(job | {"payload": payload}, sort_keys=True))
            pipe.ltrim(DEAD, 0, DEAD_MAXLEN - 1)
            pipe.execute()
            return
        self._later(job, now + backoff(job["try"]))

    def run_once(self, now: float | None = None) -> int:
        """Handle the jobs that are due. Returns how many were taken."""
        now = time.time() if now is None else now
        due = self.redis.zrangebyscore(SCHEDULE, "-inf", now, start=0, num=50)
        taken = 0
        for member in due:
            if not self.redis.zrem(SCHEDULE, member):
                continue  # another process claimed it
            taken += 1
            job = json.loads(member)
            try:
                self._deliver(job, now)
            except Exception:
                log.exception("callback for txn %s failed; retrying", job.get("txn_id"))
                self._later(job, now + backoff(job.get("try", 0)))
        return taken

    def run(self) -> None:
        while not self._stopping.is_set():
            try:
                self.run_once()
            except Exception:
                log.exception("callback dispatcher error")
            self._stopping.wait(self.interval)

    def stop(self) -> None:
        self._stopping.set()
