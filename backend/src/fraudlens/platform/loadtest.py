"""Load test: 1, 2, 4... stream workers on the same slice of the test period.

    uv run python -m fraudlens.platform.loadtest --workers 1 2 4

For each worker count, on a database reset to the historical period:

1. start the workers (`fraudlens.platform.worker`, one process each) and wait
   until each has rebuilt its state;
2. saturation: write the first `--saturation` events at once and time how long the
   workers take to decide them all (throughput);
3. open loop: write the next `--rate-events` at a fixed `--rate` per second and
   measure end-to-end latency, from the entry being added to its decision being
   committed, as the workers record it;
4. stop the workers and compare every decision with the offline evaluation
   (`verify`): tier mismatches and differing feature rows must both be 0.

It writes the JSON to `--out` and prints a markdown table. Destructive: it resets
the database named by FRAUDLENS_DATABASE_URL. Point it at a scratch one.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from redis import Redis

from ..config import BACKEND_DIR, Settings
from . import verify
from .db import make_engine, migrate
from .load import load
from .replay import test_events
from .stream import GROUP

BASE_PORT = 18100


def _wait_ready(ports: list[int], procs: list[subprocess.Popen], timeout: float = 900) -> None:
    deadline = time.monotonic() + timeout
    pending = set(ports)
    while pending:
        if time.monotonic() > deadline:
            raise SystemExit(f"workers on {sorted(pending)} not ready after {timeout:.0f}s")
        for proc in procs:
            if proc.poll() is not None:
                raise SystemExit(f"a worker exited with code {proc.returncode}")
        for port in list(pending):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/ready", timeout=1).status_code == 200:
                    pending.discard(port)
            except httpx.HTTPError:
                pass
        time.sleep(0.5)


def _drained(redis: Redis, stream: str, timeout: float = 1800) -> float:
    """Wait until every entry is decided and deleted; returns when that was seen."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if redis.xlen(stream) == 0:
            try:
                pending = redis.xpending(stream, GROUP)["pending"]
            except Exception:
                pending = 0
            if pending == 0:
                return time.time()
        time.sleep(0.02)
    raise SystemExit("the stream did not drain")


def _write(redis: Redis, stream: str, events: list[dict], rate: float = 0.0) -> None:
    """All at once, or paced in 10 ms ticks (open loop: never waits for the workers)."""
    if not rate:
        pipe = redis.pipeline(transaction=False)
        for i, event in enumerate(events, 1):
            pipe.xadd(stream, {"data": json.dumps(event)})
            if i % 2000 == 0:
                pipe.execute()
        pipe.execute()
        return
    t0, sent = time.perf_counter(), 0
    while sent < len(events):
        due = min(len(events), int((time.perf_counter() - t0) * rate) + 1)
        if due > sent:
            pipe = redis.pipeline(transaction=False)
            for event in events[sent:due]:
                pipe.xadd(stream, {"data": json.dumps(event)})
            pipe.execute()
            sent = due
        time.sleep(0.01)


def _latency(samples: list[float]) -> dict:
    if not samples:
        return {}
    p50, p95, p99 = np.percentile(np.array(samples) * 1000, [50, 95, 99])
    return {
        "p50": round(float(p50), 1),
        "p95": round(float(p95), 1),
        "p99": round(float(p99), 1),
        "max": round(max(samples) * 1000, 1),
        "samples": len(samples),
    }


def run(settings: Settings, n: int, events: list[dict], saturation: int, rate: float) -> dict:
    stream = settings.events_stream
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    print(f"\n== {n} worker(s): resetting the database ==", flush=True)
    load(engine, redis, settings, reset=True)

    ports = [BASE_PORT + i for i in range(n)]
    env = os.environ | {"FRAUDLENS_RUN_WORKER": "false"}
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "fraudlens.platform.worker",
                "--port",
                str(port),
                "--name",
                f"loadtest-{n}-{i}",
            ],
            cwd=BACKEND_DIR,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for i, port in enumerate(ports)
    ]
    try:
        t_start = time.monotonic()
        _wait_ready(ports, procs)
        startup = time.monotonic() - t_start
        print(f"   ready in {startup:.0f}s; saturation run of {saturation:,} events", flush=True)

        t0 = time.time()
        _write(redis, stream, events[:saturation])
        t1 = _drained(redis, stream)
        throughput = saturation / (t1 - t0)
        print(f"   {throughput:,.0f} events/s; open loop at {rate:g}/s", flush=True)

        t2 = time.time()
        rest = events[saturation:]
        _write(redis, stream, rest, rate=rate)
        _drained(redis, stream)

        samples, per_worker = [], []
        for port in ports:
            m = httpx.get(f"http://127.0.0.1:{port}/metrics?raw=1", timeout=30).json()
            samples.extend(lat for at, lat in m.pop("samples") if at >= t2)
            per_worker.append(m)
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
    report = verify.verify(engine, settings)
    engine.dispose()
    result = {
        "workers": n,
        "startup_seconds": round(startup, 1),
        "saturation": {
            "events": saturation,
            "seconds": round(t1 - t0, 2),
            "events_per_second": round(throughput, 1),
        },
        "open_loop": {
            "events": len(rest),
            "rate_per_second": rate,
            "latency_ms": _latency(samples),
        },
        "per_worker": per_worker,
        "parity": {
            k: report.get(k)
            for k in ("ok", "compared", "tier_mismatches", "feature_rows_differing")
        },
    }
    print(json.dumps(result | {"per_worker": "..."}, indent=1), flush=True)
    return result


def table(results: list[dict]) -> str:
    base = results[0]["saturation"]["events_per_second"]
    lines = [
        "| Workers | Throughput (events/s) | Speed-up | p50 (ms) | p95 (ms) | p99 (ms) "
        "| Tier mismatches | Differing feature rows |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        lat, par = r["open_loop"]["latency_ms"], r["parity"]
        eps = r["saturation"]["events_per_second"]
        lines.append(
            f"| {r['workers']} | {eps:,.0f} | {eps / base:.2f}x | {lat.get('p50')} | "
            f"{lat.get('p95')} | {lat.get('p99')} | {par['tier_mismatches']} | "
            f"{par['feature_rows_differing']} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--saturation", type=int, default=12_000)
    parser.add_argument("--rate", type=float, default=150.0, help="open-loop events per second")
    parser.add_argument("--rate-events", type=int, default=6_000)
    parser.add_argument("--out", type=Path, default=BACKEND_DIR.parent / "docs" / "scaling")
    args = parser.parse_args()

    settings = Settings()
    migrate(settings.database_url)
    total = args.saturation + args.rate_events
    events = list(itertools.islice(test_events(settings.dataset_dir), total))
    results = [run(settings, n, events, args.saturation, args.rate) for n in args.workers]

    load_avg = os.getloadavg()
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "machine": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "load_average_at_end": [round(x, 1) for x in load_avg],
        },
        "dataset": settings.dataset,
        "events": total,
        "settings": vars(args) | {"out": str(args.out)},
        "results": results,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"loadtest-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(summary, indent=2))
    print(f"\nwritten {path}\n\n{table(results)}")


if __name__ == "__main__":
    main()
