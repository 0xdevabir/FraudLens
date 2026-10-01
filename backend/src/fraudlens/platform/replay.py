"""Replay the held-out test period into the running platform, as live traffic would arrive.

    uv run python -m fraudlens.platform.replay --via stream            # the whole test period
    uv run python -m fraudlens.platform.replay --via http --from-day 119
    uv run python -m fraudlens.platform.replay --what-if 2000 --concurrency 8

`stream` writes events to the Redis stream the API's worker consumes. `http`
calls POST /v1/score one transaction at a time, as a payment switch would, and
records the latency of every call. `--what-if` is a read-only load test against
the dry-run endpoint. All of them need the API to be running.

Replayed events are marked `source: "replay"`: they are recorded history, so
the state moves on whatever the decision (see `platform.scoring`). Confirmed-fraud
flags from the test period are sent in between, at the time they were raised.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from redis import Redis

from ..config import Settings
from ..features import SCORED_TYPES
from ..features.build import epoch_seconds
from .events import moment
from .load import TXN_COLUMNS, history_cutoff

SERVICE_USER = "upay-core"
MAX_QUEUE = 20_000  # stop writing while the scorer is this far behind


def test_events(
    data_dir: Path, from_day: int | None = None, to_day: int | None = None, source: str = "replay"
) -> Iterator[dict]:
    """Test-period transactions in order, with each flag just before the first
    transaction at or after the time it was raised (the offline replay does the same)."""
    txns = pd.read_parquet(
        data_dir / "transactions.parquet", columns=[*TXN_COLUMNS, "split", "day"]
    )
    cutoff = history_cutoff(txns)
    test = txns[txns["split"] == "test"]
    if from_day is not None:
        test = test[test["day"] >= from_day]
    if to_day is not None:
        test = test[test["day"] <= to_day]
    flags = pd.read_parquet(data_dir / "wallet_flags.parquet")
    flags = flags[flags["flagged_at"] > cutoff].sort_values("flagged_at", kind="stable")
    pending = list(
        zip(epoch_seconds(flags["flagged_at"]).tolist(), flags["wallet_id"], flags["flag_reason"],
            strict=True)
    )  # fmt: skip
    j = 0
    seconds = epoch_seconds(test["ts"]).tolist()
    for ts, row in zip(seconds, test[list(TXN_COLUMNS)].itertuples(index=False), strict=True):
        while j < len(pending) and pending[j][0] <= ts:
            flagged_at, wallet_id, reason = pending[j]
            yield {
                "kind": "flag",
                "wallet_id": wallet_id,
                "flagged_at": moment(flagged_at).isoformat(),
                "reason": reason,
                "source": "replay",
            }
            j += 1
        balance = row.sender_balance_before
        yield {
            "kind": "txn",
            "txn_id": int(row.txn_id),
            "ts": moment(ts).isoformat(),
            "type": row.type,
            "sender_id": row.sender_id,
            "sender_type": row.sender_type,
            "receiver_id": row.receiver_id,
            "receiver_type": row.receiver_type,
            "amount": float(row.amount),
            "sender_balance_before": None if math.isnan(balance) else float(balance),
            "device_id": row.device_id,
            "channel": row.channel,
            "district": row.district,
            "source": source,
        }


def via_stream(
    redis: Redis, stream: str, events: Iterator[dict], rate: float = 0.0, wait: bool = True
) -> dict:
    """Write events to the stream, never letting the queue grow past MAX_QUEUE.

    With `wait`, a worker is assumed to be consuming: writing pauses while the
    queue is full and the call returns once the stream is empty.
    """
    t0 = time.perf_counter()
    sent, pipe = 0, redis.pipeline(transaction=False)
    for event in events:
        pipe.xadd(stream, {"data": json.dumps(event)})
        sent += 1
        if sent % 1000 == 0:
            pipe.execute()
            while wait and redis.xlen(stream) > MAX_QUEUE:
                time.sleep(0.05)
            if rate and (ahead := sent / rate - (time.perf_counter() - t0)) > 0:
                time.sleep(ahead)
            if sent % 20_000 == 0:
                print(f"  {sent:,} events written, {redis.xlen(stream):,} queued", flush=True)
    pipe.execute()
    deadline = time.monotonic() + 600
    while (queued := redis.xlen(stream)) and wait and time.monotonic() < deadline:
        time.sleep(0.1)
    seconds = time.perf_counter() - t0
    return {
        "transport": "stream",
        "events": sent,
        "left_in_stream": int(queued),
        "seconds": round(seconds, 1),
        "events_per_second": round(sent / seconds) if seconds else None,
    }


def _percentiles(values: list[float]) -> dict:
    if not values:
        return {}
    p50, p95, p99 = np.percentile(values, [50, 95, 99])
    return {
        "p50": round(float(p50), 2),
        "p95": round(float(p95), 2),
        "p99": round(float(p99), 2),
        "max": round(float(max(values)), 2),
    }


def login(client: httpx.Client, username: str, password: str) -> None:
    response = client.post("/v1/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


def via_http(client: httpx.Client, events: Iterator[dict]) -> dict:
    """One call per event, in order, the way a payment switch would call the scorer."""
    round_trip, scored_round_trip, server, statuses, tiers = [], [], [], Counter(), Counter()
    t0 = time.perf_counter()
    n = 0
    for event in events:
        kind = event.pop("kind")
        path = "/v1/score" if kind == "txn" else "/v1/wallet-flags"
        t1 = time.perf_counter()
        response = client.post(path, json=event)
        elapsed = (time.perf_counter() - t1) * 1000
        n += 1
        if kind != "txn":
            response.raise_for_status()
            continue
        statuses[response.status_code] += 1
        if response.status_code != 200:
            continue
        body = response.json()
        round_trip.append(elapsed)
        if body["scored"] and not body["duplicate"]:
            scored_round_trip.append(elapsed)
            server.append(body["decision"]["latency_ms"])
            tiers[body["decision"]["tier"]] += 1
    seconds = time.perf_counter() - t0
    return {
        "transport": "http, sequential, one connection",
        "events": n,
        "http_status": dict(statuses),
        "seconds": round(seconds, 1),
        "requests_per_second": round(n / seconds, 1) if seconds else None,
        "tiers": dict(tiers),
        "round_trip_ms_all_transactions": _percentiles(round_trip),
        "round_trip_ms_scored": {"n": len(scored_round_trip), **_percentiles(scored_round_trip)},
        "feature_and_decision_ms_scored": _percentiles(server),
    }


def what_if_load(base_url: str, token: str, events: list[dict], concurrency: int) -> dict:
    """Concurrent read-only decisions. Nothing is stored and the state does not move."""

    def worker(chunk: list[dict]) -> tuple[list[float], Counter]:
        took, statuses = [], Counter()
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            client.headers["Authorization"] = token
            for event in chunk:
                t1 = time.perf_counter()
                response = client.post("/v1/score/what-if", json=event)
                took.append((time.perf_counter() - t1) * 1000)
                statuses[response.status_code] += 1
        return took, statuses

    for event in events:
        event.pop("kind"), event.pop("source")
    chunks = [events[i::concurrency] for i in range(concurrency)]
    t0 = time.perf_counter()
    with ThreadPoolExecutor(concurrency) as pool:
        parts = list(pool.map(worker, chunks))
    seconds = time.perf_counter() - t0
    took = [ms for part, _ in parts for ms in part]
    statuses = sum((s for _, s in parts), Counter())
    return {
        "transport": f"http what-if, {concurrency} concurrent connections",
        "requests": len(took),
        "http_status": dict(statuses),
        "seconds": round(seconds, 1),
        "requests_per_second": round(len(took) / seconds, 1),
        "round_trip_ms": _percentiles(took),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay the test period into the platform")
    parser.add_argument("--via", choices=("stream", "http"), default="stream")
    parser.add_argument("--from-day", type=int, default=None, help="first simulation day")
    parser.add_argument("--to-day", type=int, default=None, help="last simulation day")
    parser.add_argument("--rate", type=float, default=0.0, help="events per second (0: no limit)")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many events")
    parser.add_argument("--api", default="http://127.0.0.1:8010")
    parser.add_argument("--what-if", type=int, default=0, metavar="N", help="dry-run load test")
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    settings = Settings()

    events = test_events(settings.dataset_dir, args.from_day, args.to_day)
    if args.limit:
        events = (e for _, e in zip(range(args.limit), events, strict=False))
    reports = settings.artifacts_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    if args.via == "stream" and not args.what_if:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        report = via_stream(redis, settings.events_stream, events, args.rate)
        print(json.dumps(report, indent=2))
        return

    if settings.seed_password is None:  # read from the environment or backend/.env
        raise SystemExit("set FRAUDLENS_SEED_PASSWORD to the service account's password")
    password = settings.seed_password.get_secret_value()
    with httpx.Client(base_url=args.api, timeout=30.0) as client:
        login(client, SERVICE_USER, password)
        if args.what_if:
            scored = (e for e in events if e.get("type") in SCORED_TYPES)
            sample = [e for _, e in zip(range(args.what_if), scored, strict=False)]
            token = client.headers["Authorization"]
            report = what_if_load(args.api, token, sample, args.concurrency)
            name = "latency_what_if.json"
        else:
            report = via_http(client, events)
            name = "latency.json"
    (reports / name).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
