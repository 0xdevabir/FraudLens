"""Send one signed transaction to FraudLens and print the decision; optionally receive callbacks.

    export FRAUDLENS_PARTNER_KEY_ID=upay-20261007-77d1 FRAUDLENS_PARTNER_SECRET=...
    python sdk/python/example.py --api http://127.0.0.1:8010 \\
        --sender W000123 --receiver W000456 --amount 900 --balance 20000 --district Dhaka

    # queued instead of decided, with a receiver for the signed decision callback
    # (the partner's callback_url in the keyring: http://127.0.0.1:9099/fraudlens)
    python sdk/python/example.py ... --queue --listen 9099
"""

from __future__ import annotations

import argparse
import json
import os
import random
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from fraudlens_partner import Client, credit_transfer, pacs008, verify


def receiver(port: int, key_id: str, secret: str) -> HTTPServer:
    seen: set[str] = set()

    def seen_nonce(nonce: str) -> bool:
        if nonce in seen:
            return True
        seen.add(nonce)
        return False

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            ok = verify({key_id: secret}, dict(self.headers), "POST", self.path, body, seen_nonce)
            self.send_response(204 if ok else 401)
            self.end_headers()
            print("callback", "verified" if ok else "REJECTED", json.loads(body) if ok else "")

        def log_message(self, *args) -> None:
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api", default="http://127.0.0.1:8010")
    parser.add_argument("--sender", required=True)
    parser.add_argument("--receiver", required=True)
    parser.add_argument("--amount", type=float, default=900.0)
    parser.add_argument("--balance", type=float, default=20_000.0)
    parser.add_argument("--district", default="Dhaka")
    parser.add_argument("--queue", action="store_true", help="queue it; do not wait")
    # The demo world runs on recorded time: pass its clock (`now` in GET /v1/demo/scenarios).
    parser.add_argument("--at", type=datetime.fromisoformat, help="default: the wall clock")
    parser.add_argument("--listen", type=int, help="receive callbacks on this port")
    args = parser.parse_args()
    key_id, secret = os.environ["FRAUDLENS_PARTNER_KEY_ID"], os.environ["FRAUDLENS_PARTNER_SECRET"]

    server = receiver(args.listen, key_id, secret) if args.listen else None
    txn_id = random.randrange(8_000_000_000, 9_000_000_000)
    transfer = credit_transfer(
        txn_id=txn_id,
        end_to_end_id=f"E2E-{txn_id}",
        accepted_at=args.at or datetime.now(UTC),
        purpose="SEND_MONEY",
        amount=args.amount,
        debtor=(args.sender, "wallet"),
        creditor=(args.receiver, "wallet"),
        channel="app",
        district=args.district,
        debtor_balance_before=args.balance,
    )
    client = Client(args.api, key_id, secret)
    status, answer = client.send(pacs008(f"MSG-{txn_id}", [transfer]), not args.queue)
    print(status, json.dumps(answer, indent=2))
    if server:
        input("waiting for the callback; press Enter to stop\n")
        server.shutdown()


if __name__ == "__main__":
    main()
