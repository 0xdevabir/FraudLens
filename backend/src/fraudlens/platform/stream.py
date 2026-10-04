"""The event stream: transactions in through a Redis stream, alerts out to a live feed.

Delivery is at least once. An entry is acknowledged only after its batch has
been committed, and scoring is idempotent on the transaction id, so a crash
between the two means the entry is read again and recognised as a duplicate.
An event that cannot be processed (malformed, too far behind the stream) goes
to a dead-letter stream with the reason; it never blocks the events behind it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import AsyncIterator, Sequence

from pydantic import BaseModel, ValidationError
from redis import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy.exc import InterfaceError, OperationalError

from ..config import Settings
from .events import event_adapter
from .scoring import Scorer

log = logging.getLogger(__name__)

GROUP = "scorer"
CONSUMER = "scorer-1"  # one scorer owns the state, so there is one consumer
DEAD_SUFFIX = ":dead"
DEAD_MAXLEN = 10_000
MAX_BACKLOG = 200_000
# The database or Redis is unavailable: leave the entries pending and try again.
RETRYABLE = (OperationalError, InterfaceError, RedisError)


def enqueue(redis: Redis, stream: str, events: Sequence[BaseModel]) -> int:
    pipe = redis.pipeline(transaction=False)
    for event in events:
        pipe.xadd(stream, {"data": event.model_dump_json()})
    pipe.execute()
    return len(events)


class Worker(threading.Thread):
    def __init__(
        self, scorer: Scorer, redis: Redis, settings: Settings, batch: int = 200, block_ms=1000
    ) -> None:
        super().__init__(name="fraudlens-stream-worker", daemon=True)
        self.scorer, self.redis = scorer, redis
        self.stream = settings.events_stream
        self.dead = settings.events_stream + DEAD_SUFFIX
        self.batch, self.block_ms = batch, block_ms
        self.processed = self.dead_lettered = self.failures = 0
        self._stopping = threading.Event()
        self._caught_up = False  # entries delivered before a crash are handled first
        self.sla_every = settings.sla_sweep_seconds
        self.sla_escalates = settings.sla_auto_escalate
        self._swept_at = time.monotonic()

    def ensure_group(self) -> None:
        try:
            self.redis.xgroup_create(self.stream, GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def backlog(self) -> int:
        return int(self.redis.xlen(self.stream))

    def _read(self, block_ms: int | None) -> list[tuple[str, dict]]:
        if not self._caught_up:
            reply = self.redis.xreadgroup(GROUP, CONSUMER, {self.stream: "0"}, count=self.batch)
            entries = reply[0][1] if reply else []
            if entries:
                return entries
            self._caught_up = True
        reply = self.redis.xreadgroup(
            GROUP, CONSUMER, {self.stream: ">"}, count=self.batch, block=block_ms
        )
        return reply[0][1] if reply else []

    def _bury(self, pipe, entry_id: str, fields: dict, reason: str) -> None:
        self.dead_lettered += 1
        log.warning("event %s dead-lettered: %s", entry_id, reason)
        pipe.xadd(
            self.dead,
            {"id": entry_id, "reason": reason[:500], "data": str(fields.get("data", ""))[:4000]},
            maxlen=DEAD_MAXLEN,
            approximate=True,
        )

    def run_once(self, block_ms: int | None = None) -> int:
        """Read one batch, score it, acknowledge it. Returns the number of entries handled."""
        entries = self._read(block_ms)
        if not entries:
            return 0
        pipe = self.redis.pipeline(transaction=False)
        ids, events = [], []
        for entry_id, fields in entries:
            # An entry deleted while pending comes back with no fields.
            try:
                event = event_adapter.validate_json(fields["data"]).event()
            except (KeyError, TypeError, ValidationError) as exc:
                reason = f"invalid event: {type(exc).__name__}"
                if isinstance(exc, ValidationError):
                    reason = "invalid event: " + "; ".join(
                        f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:3]
                    )
                self._bury(pipe, entry_id, fields or {}, reason)
                continue
            ids.append((entry_id, fields))
            events.append(event)

        try:
            results = self.scorer.process(events) if events else []
        except RETRYABLE:
            raise
        except Exception:
            # One bad event must not take the batch with it: go through them singly.
            log.exception("batch of %d failed; processing its events one by one", len(events))
            results = []
            for (entry_id, fields), event in zip(ids, events, strict=True):
                try:
                    results.extend(self.scorer.process([event]))
                except RETRYABLE:
                    raise
                except Exception as exc:
                    self.failures += 1
                    self._bury(pipe, entry_id, fields, f"processing failed: {type(exc).__name__}")
                    results.append(None)
        for (entry_id, fields), result in zip(ids, results, strict=True):
            if result is not None and result.status == "stale":
                self._bury(pipe, entry_id, fields, "stale: too far behind the stream")
        done = [entry_id for entry_id, _ in entries]
        pipe.xack(self.stream, GROUP, *done)
        pipe.xdel(self.stream, *done)
        pipe.execute()
        self.processed += len(done)
        return len(done)

    def run(self) -> None:
        while not self._stopping.is_set():
            try:
                self.ensure_group()
                while not self._stopping.is_set():
                    self.run_once(self.block_ms)
                    self._sweep()
            except Exception:
                self.failures += 1
                self._caught_up = False  # whatever was delivered is still pending
                log.exception("stream worker error; retrying in 2s")
                self._stopping.wait(2.0)

    def _sweep(self) -> None:
        """Now and then, record cases that have missed their review deadline."""
        if not self.sla_every or time.monotonic() - self._swept_at < self.sla_every:
            return
        self._swept_at = time.monotonic()
        try:
            from .cases import sweep_sla

            sweep_sla(self.scorer, self.sla_escalates)
        except Exception:
            log.exception("SLA sweep failed; it will run again")

    def stop(self) -> None:
        self._stopping.set()

    def drain(self) -> int:
        """Process everything currently in the stream (tests and the replay tool)."""
        self.ensure_group()
        self._caught_up = False
        total = 0
        while handled := self.run_once(None):
            total += handled
        return total


async def alert_feed(redis, channel: str, heartbeat: float = 15.0) -> AsyncIterator[str]:
    """Server-sent events: one `alert` event per alert, and a comment line as a keep-alive.

    `redis` is a `redis.asyncio` client. The feed carries only new alerts; a console
    loads the current list from the API and then listens here.
    """
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    try:
        # Wait for Redis to confirm, so nothing published after "connected" is missed.
        await pubsub.get_message(timeout=5.0)
        yield ": connected\n\n"
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=heartbeat)
            if message is None:
                yield ": keep-alive\n\n"
                continue
            data = message["data"]
            if isinstance(data, bytes):
                data = data.decode()
            yield f"event: alert\ndata: {data}\n\n"
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
