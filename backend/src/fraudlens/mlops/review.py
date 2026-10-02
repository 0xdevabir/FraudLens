"""Stand in for the analysts: close the older open cases with the verdict a careful
reviewer would reach, so the feedback loop has labels to learn from.

    uv run python -m fraudlens.mlops.review                 # leave the last 7 days open
    uv run python -m fraudlens.mlops.review --leave-days 3 --inconclusive 0.1

This is demo scaffolding and says so in every note it writes. The verdict comes
from the simulator's ground truth, which no real analyst has: a case is fraud if
its subject is a mule or any of its alerted transactions was fraud. A share of
cases is closed as inconclusive, because some reviews end that way and those must
not become labels.

Verdicts move money and flag wallets, so they go through the running API like
any other reviewer's, signed in as the `review-sim` account and audited.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

from ..config import Settings
from ..platform.replay import login

REVIEW_USER = "review-sim"
LEAVE_DAYS = 7
PAGE = 200
OPEN = ("open", "in_review", "escalated")
NOTE = "Review simulator (demo): verdict taken from the simulation's ground truth."


def ground_truth(data_dir: Path) -> tuple[set[str], set[int]]:
    """The mule wallets and the fraudulent transactions, as the simulator made them."""
    wallets = pd.read_parquet(data_dir / "wallets.parquet", columns=["wallet_id", "is_mule"])
    txns = pd.read_parquet(data_dir / "transactions.parquet", columns=["txn_id", "is_fraud"])
    return (
        set(wallets.loc[wallets["is_mule"], "wallet_id"]),
        set(txns.loc[txns["is_fraud"], "txn_id"].tolist()),
    )


def open_cases(client: httpx.Client) -> list[dict]:
    found, offset = [], 0
    while True:
        response = client.get(
            "/v1/cases", params={"status": list(OPEN), "limit": PAGE, "offset": offset}
        )
        response.raise_for_status()
        page = response.json()["cases"]
        found.extend(page)
        if len(page) < PAGE:
            return found
        offset += PAGE


def decide(case: dict, alerts: list[dict], mules: set[str], fraud: set[int]) -> str:
    if case["subject_id"] in mules or any(a["txn_id"] in fraud for a in alerts):
        return "confirmed_fraud"
    return "legitimate"


def run(
    client: httpx.Client,
    data_dir: Path,
    leave_days: float = LEAVE_DAYS,
    inconclusive: float = 0.05,
    seed: int = 7,
    limit: int | None = None,
) -> dict:
    """Close every open case opened more than `leave_days` before the newest one."""
    mules, fraud = ground_truth(data_dir)
    cases = open_cases(client)
    if not cases:
        return {"open_cases": 0, "closed": 0, "verdicts": {}}
    opened = {c["id"]: datetime.fromisoformat(c["opened_at"]) for c in cases}
    cutoff = max(opened.values()) - timedelta(days=leave_days)
    due = sorted((c for c in cases if opened[c["id"]] <= cutoff), key=lambda c: opened[c["id"]])
    rng = random.Random(seed)
    verdicts, released, blocked, skipped = Counter(), 0, 0, 0
    for case in due[:limit]:
        detail = client.get(f"/v1/cases/{case['id']}")
        detail.raise_for_status()
        verdict = decide(case, detail.json()["alerts"], mules, fraud)
        if rng.random() < inconclusive:
            verdict = "inconclusive"
        response = client.post(
            f"/v1/cases/{case['id']}/verdict",
            json={"verdict": verdict, "note": NOTE},
        )
        if response.status_code == 409:  # someone closed it in the meantime
            skipped += 1
            continue
        response.raise_for_status()
        outcome = response.json()
        verdicts[verdict] += 1
        released += len(outcome["released"])
        blocked += len(outcome["blocked"])
    return {
        "open_cases": len(cases),
        "left_open": len(cases) - sum(verdicts.values()),
        "opened_up_to": cutoff.isoformat(),
        "closed": sum(verdicts.values()),
        "skipped": skipped,
        "verdicts": dict(verdicts),
        "payments_released": released,
        "payments_blocked": blocked,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Close older cases with ground-truth verdicts")
    parser.add_argument("--api", default="http://127.0.0.1:8010")
    parser.add_argument("--leave-days", type=float, default=LEAVE_DAYS)
    parser.add_argument("--inconclusive", type=float, default=0.05, help="share closed that way")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--limit", type=int, default=None, help="close at most this many")
    args = parser.parse_args()
    if not 0 <= args.inconclusive <= 1:
        parser.error("--inconclusive is a share between 0 and 1")
    settings = Settings()
    if settings.seed_password is None:
        raise SystemExit("set FRAUDLENS_SEED_PASSWORD to the review-sim account's password")
    with httpx.Client(base_url=args.api, timeout=60.0) as client:
        login(client, REVIEW_USER, settings.seed_password.get_secret_value())
        report = run(
            client,
            settings.dataset_dir,
            args.leave_days,
            args.inconclusive,
            args.seed,
            args.limit,
        )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
