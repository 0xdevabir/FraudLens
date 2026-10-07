"""One stream worker as its own process: run as many as the load needs.

    uv run python -m fraudlens.platform.worker [--port 8081] [--name W]

It loads the model and policy once, rebuilds the feature state from the database
like the API does, and consumes the event stream in the `scorer` group next to
the other workers (docs/SCALING.md). A small HTTP port serves what an
orchestrator needs:

    GET /health   the process is up (liveness)
    GET /ready    the state is rebuilt and the consumer is running (readiness)
    GET /metrics  counters and end-to-end latency, JSON; ?since=<epoch s>, ?raw=1

SIGTERM stops it after the batch in hand; what it has not acknowledged is taken
over by another worker after `FRAUDLENS_CLAIM_IDLE_MS`.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from redis import Redis

from ..config import Settings
from .db import make_engine, make_sessions
from .scoring import Scorer
from .stream import Worker

log = logging.getLogger("fraudlens.worker")


def serve_health(worker: Worker, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (the stdlib's name)
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/health":
                code, body = 200, {"status": "ok"}
            elif url.path == "/ready":
                ready = worker.scorer.ready and worker.is_alive()
                code, body = (200 if ready else 503), {"ready": ready}
            elif url.path == "/metrics":
                since = float(query["since"][0]) if "since" in query else None
                code = 200
                body = worker.metrics(since=since, raw=query.get("raw") == ["1"])
            else:
                code, body = 404, {"error": "not_found"}
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args) -> None:  # probes every few seconds: not worth a line
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, name="fraudlens-health", daemon=True).start()
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, help="health and metrics port")
    parser.add_argument("--name", help="consumer name in the group (default: host-pid)")
    parser.add_argument("--batch", type=int, default=200, help="entries read at a time")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()

    db = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    scorer = Scorer(settings, make_sessions(db), redis)
    worker = Worker(scorer, redis, settings, batch=args.batch, consumer=args.name)
    server = serve_health(worker, args.port or settings.worker_port)

    stopped = threading.Event()

    def stop(signum, _frame) -> None:
        log.info("signal %d: stopping after the batch in hand", signum)
        worker.stop()
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker.start()
    log.info("worker %s consuming %s in %s mode", worker.consumer, worker.stream, scorer.mode)
    stopped.wait()
    worker.join(timeout=30.0)
    server.shutdown()
    redis.close()
    db.dispose()


if __name__ == "__main__":
    main()
