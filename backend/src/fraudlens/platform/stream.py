"""The event stream: transactions in through a Redis stream, alerts out to a live feed.

Delivery is at least once. An entry is acknowledged only after its decision has
been committed, and scoring is idempotent on the transaction id, so a crash
between the two means the entry is read again and recognised as a duplicate.
An event that cannot be processed (malformed, too far behind the stream) goes
to a dead-letter stream with the reason; it never blocks the events behind it.

Any number of workers share one consumer group (docs/SCALING.md). Each batch
goes through two steps:

1. In stream order, one batch at a time across all workers: the state changes
   and the transaction rows (`Scorer.sequence`). A batch starts only when the
   entry before it has been through this step: the cursor `<stream>:sequenced`
   holds the last entry that has.
2. In parallel: the decisions, cases and alerts (`Scorer.finish`). Then the
   entries are acknowledged and deleted, so the stream's length is the work
   still to do, which is what the autoscaler watches.

Entries a stopped worker held are taken over with XAUTOCLAIM once idle for
`claim_idle_ms`, by any worker, including one waiting for them.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Sequence

from pydantic import BaseModel, ValidationError
from redis import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy.exc import InterfaceError, OperationalError

from ..config import Settings
from .events import event_adapter
from .scoring import FROZEN_VERSION_SUFFIX, MULE_SUFFIX, Result, Scored, Scorer

log = logging.getLogger(__name__)

GROUP = "scorer"
DEAD_SUFFIX = ":dead"
CURSOR_SUFFIX = ":sequenced"
# Keys that describe the stream's contents, deleted with it when the database is reset.
RESET_SUFFIXES = ("", DEAD_SUFFIX, CURSOR_SUFFIX, FROZEN_VERSION_SUFFIX, MULE_SUFFIX)
DEAD_MAXLEN = 10_000
MAX_BACKLOG = 200_000
LATENCY_SAMPLES = 500_000
# The database or Redis is unavailable: leave the entries pending and try again.
RETRYABLE = (OperationalError, InterfaceError, RedisError)

# The cursor only moves forward: entry ids compare as (milliseconds, sequence).
_ADVANCE = """
local cur = redis.call('GET', KEYS[1])
if cur then
  local cm, cs = string.match(cur, '(%d+)-(%d+)')
  local nm, ns = string.match(ARGV[1], '(%d+)-(%d+)')
  cm, cs, nm, ns = tonumber(cm), tonumber(cs), tonumber(nm), tonumber(ns)
  if cm > nm or (cm == nm and cs >= ns) then return 0 end
end
redis.call('SET', KEYS[1], ARGV[1])
return 1
"""

Entry = tuple[str, dict]


def entry_key(entry_id: str) -> tuple[int, int]:
    ms, _, seq = entry_id.partition("-")
    return int(ms), int(seq or 0)


def default_consumer() -> str:
    """Unique per process; stable across restarts of a container (pod name, pid 1)."""
    return f"{socket.gethostname()}-{os.getpid()}"


def enqueue(redis: Redis, stream: str, events: Sequence[BaseModel]) -> int:
    pipe = redis.pipeline(transaction=False)
    for event in events:
        pipe.xadd(stream, {"data": event.model_dump_json()})
    pipe.execute()
    return len(events)


class Worker(threading.Thread):
    def __init__(
        self,
        scorer: Scorer,
        redis: Redis,
        settings: Settings,
        batch: int = 200,
        block_ms=1000,
        consumer: str | None = None,
    ) -> None:
        super().__init__(name="fraudlens-stream-worker", daemon=True)
        self.scorer, self.redis = scorer, redis
        self.stream = settings.events_stream
        self.dead = settings.events_stream + DEAD_SUFFIX
        self.cursor = settings.events_stream + CURSOR_SUFFIX
        self.consumer = consumer or settings.worker_name or default_consumer()
        self.claim_idle_ms = settings.claim_idle_ms
        self.batch, self.block_ms = batch, block_ms
        self.processed = self.dead_lettered = self.failures = self.reclaimed = 0
        self.decided = 0  # decisions this worker recorded
        # (when recorded, seconds since the entry was added): end-to-end latency
        self.latencies: deque[tuple[float, float]] = deque(maxlen=LATENCY_SAMPLES)
        self._stopping = threading.Event()
        self._caught_up = False  # entries delivered before a crash are handled first
        # Entries this consumer holds and has not finished, in stream order. Each unit is
        # contiguous in the stream (a fresh read), or a single entry (pending, reclaimed).
        self._units: list[list[Entry]] = []
        self._last_claim = 0.0
        self._advance = redis.register_script(_ADVANCE)

    def ensure_group(self) -> None:
        try:
            self.redis.xgroup_create(self.stream, GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def backlog(self) -> int:
        return int(self.redis.xlen(self.stream))

    # ---------------------------------------------------------------- reading

    def _held(self) -> set[str]:
        return {entry_id for unit in self._units for entry_id, _ in unit}

    def _add(self, units: list[list[Entry]]) -> None:
        held = self._held()
        self._units.extend(u for u in units if u and u[0][0] not in held)
        self._units.sort(key=lambda u: entry_key(u[0][0]))

    def _fill(self, block_ms: int | None) -> None:
        if self._units:
            return
        if not self._caught_up:
            reply = self.redis.xreadgroup(
                GROUP, self.consumer, {self.stream: "0"}, count=self.batch
            )
            entries = reply[0][1] if reply else []
            if entries:
                self._add([[e] for e in entries])
                return
            self._caught_up = True
        self._claim()
        if self._units:
            return
        reply = self.redis.xreadgroup(
            GROUP, self.consumer, {self.stream: ">"}, count=self.batch, block=block_ms
        )
        if reply and reply[0][1]:
            self._add([reply[0][1]])

    def _claim(self, force: bool = False) -> None:
        """Take over entries another consumer has held too long (it stopped)."""
        now = time.monotonic()
        if not force and now - self._last_claim < self.claim_idle_ms / 2000:
            return
        self._last_claim = now
        start, claimed = "0-0", []
        while True:
            reply = self.redis.xautoclaim(
                self.stream, GROUP, self.consumer, self.claim_idle_ms, start, count=self.batch
            )
            start, entries = reply[0], reply[1]
            claimed.extend(e for e in entries if e[1] is not None)
            if start in ("0-0", b"0-0") or not entries:
                break
        held = self._held()
        fresh = [e for e in claimed if e[0] not in held]
        if fresh:
            self.reclaimed += len(fresh)
            log.warning("%s took over %d idle entries", self.consumer, len(fresh))
            self._add([[e] for e in fresh])

    # ------------------------------------------------------------- ordering

    def _sequenced_before(self, entry_id: str) -> bool:
        """Whether every entry before this one has been through the ordered step."""
        before = self.redis.xrevrange(self.stream, max="(" + entry_id, min="-", count=1)
        if not before:
            return True
        cursor = self.redis.get(self.cursor)
        return cursor is not None and entry_key(before[0][0]) <= entry_key(cursor)

    def _next_unit(self) -> list[Entry] | None:
        """The earliest unit held, once it may be sequenced. While waiting, entries
        held by a stopped consumer are taken over and done first."""
        delay = 0.001
        while not self._stopping.is_set():
            unit = self._units[0]
            if self._sequenced_before(unit[0][0]):
                return unit
            self._claim()
            if self._units[0] is unit:
                time.sleep(delay)
                delay = min(delay * 2, 0.02)
        return None

    # ------------------------------------------------------------- handling

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
        """Read (or take over) one batch, score it, acknowledge it. Returns the number
        of entries handled."""
        self._fill(block_ms)
        if not self._units:
            return 0
        unit = self._next_unit()
        if unit is None:
            return 0
        handled = self._handle(unit)
        self._units.remove(unit)
        return handled

    def _handle(self, entries: list[Entry]) -> int:
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

        results, scored = self._sequence(pipe, ids, events)
        # Everything up to the last entry of this unit is in order now.
        self._advance(keys=[self.cursor], args=[entries[-1][0]])
        self._finish(pipe, ids, results, scored)
        for (entry_id, fields), result in zip(ids, results, strict=True):
            if result is not None and result.status == "stale":
                self._bury(pipe, entry_id, fields, "stale: too far behind the stream")
        done = [entry_id for entry_id, _ in entries]
        pipe.xack(self.stream, GROUP, *done)
        pipe.xdel(self.stream, *done)
        pipe.execute()
        now = time.time()
        self.latencies.extend((now, now - entry_key(e)[0] / 1000) for e in done)
        self.processed += len(done)
        return len(done)

    def _sequence(self, pipe, ids, events) -> tuple[list[Result | None], list[Scored]]:
        if not events:
            return [], []
        try:
            return self.scorer.sequence(events)
        except RETRYABLE:
            raise
        except Exception:
            # One bad event must not take the batch with it: go through them singly.
            log.exception("batch of %d failed; processing its events one by one", len(events))
        results: list[Result | None] = []
        scored: list[Scored] = []
        for (entry_id, fields), event in zip(ids, events, strict=True):
            try:
                one, items = self.scorer.sequence([event])
            except RETRYABLE:
                raise
            except Exception as exc:
                self.failures += 1
                self._bury(pipe, entry_id, fields, f"processing failed: {type(exc).__name__}")
                results.append(None)
                continue
            results.extend(one)
            scored.extend(items)
        return results, scored

    def _finish(self, pipe, ids, results, scored: list[Scored]) -> None:
        try:
            self.decided += len(self.scorer.finish(scored))
            return
        except RETRYABLE:
            raise
        except Exception:
            log.exception("decisions for %d events failed; recording them one by one", len(scored))
        entry_of = {r.txn_id: entry for entry, r in zip(ids, results, strict=True) if r}
        for item in scored:
            try:
                self.decided += len(self.scorer.finish([item]))
            except RETRYABLE:
                raise
            except Exception as exc:
                # The transaction stays applied, with its pending row to show for it.
                self.failures += 1
                entry_id, fields = entry_of.get(item.txn.txn_id, ("?", {}))
                self._bury(pipe, entry_id, fields, f"decision failed: {type(exc).__name__}")

    # ------------------------------------------------------------ lifecycle

    def run(self) -> None:
        while not self._stopping.is_set():
            try:
                self.ensure_group()
                while not self._stopping.is_set():
                    self.run_once(self.block_ms)
            except Exception:
                self.failures += 1
                self._caught_up = False  # whatever was delivered is still pending
                self._units.clear()
                log.exception("stream worker error; retrying in 2s")
                self._stopping.wait(2.0)

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

    def metrics(self, since: float | None = None, raw: bool = False) -> dict:
        """Counters and end-to-end latency (entry added to decision recorded), over the
        samples recorded after `since` (epoch seconds)."""
        samples = sorted(lat for at, lat in list(self.latencies) if since is None or at >= since)
        out = {
            "consumer": self.consumer,
            "processed": self.processed,
            "decided": self.decided,
            "dead_lettered": self.dead_lettered,
            "failures": self.failures,
            "reclaimed": self.reclaimed,
            "latency_ms": {
                f"p{q}": round(
                    samples[min(len(samples) - 1, int(len(samples) * q / 100))] * 1000, 2
                )
                for q in (50, 95, 99)
            }
            if samples
            else {},
        }
        if raw:
            out["samples"] = [[at, lat] for at, lat in list(self.latencies)]
        return out


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
