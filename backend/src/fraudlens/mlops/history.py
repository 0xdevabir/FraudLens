"""Fill the operational queues so the console looks like work has already been done.

    uv run python -m fraudlens.mlops.history                 # claims then resolutions
    uv run python -m fraudlens.mlops.history claims          # before closing cases
    uv run python -m fraudlens.mlops.history resolve         # after the review step

Claims: customer scam reports (refunds) and appeals on warned/held payments.
`all` also closes older open cases (same as the review step) so freezes open.
Resolve: freeze approvals, appeal decisions, phone/url blocklist entries.

Everything goes through the running API as the usual accounts, so audit and
two-person rules apply. Notes read like ordinary case work. Safe to re-run:
existing rows are skipped when targets are already met.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

import httpx
import pandas as pd

from ..config import Settings
from ..platform.replay import login

SERVICE = "upay-core"
ANALYST = "analyst1"
ANALYST_B = "analyst2"
SUPERVISOR = "supervisor1"
SUPERVISOR_B = "supervisor2"

REPORT_TARGET = 32
APPEAL_TARGET = 22
DECLINE_REFUNDS = 3
APPROVE_FREEZES = 18
REJECT_FREEZES = 2
APPROVE_APPEALS = 10
REJECT_APPEALS = 6
EXTRA_FREEZE_REQUESTS = 4
PHONE_TARGET = 8
URL_TARGET = 6
REMOVE_BLOCKLIST = 2
PAGE = 200

CATEGORIES = (
    "impersonation",
    "prize_or_lottery",
    "investment",
    "phishing_link_or_app",
    "job_or_loan",
    "merchant_or_marketplace",
    "fake_payment",
    "account_takeover",
)

REPORT_NOTES = (
    "Someone called saying they were from upay support and asked me to send this.",
    "They said I won a prize and needed to pay a fee before they release it.",
    "An agent on Facebook promised high returns if I transferred today.",
    "I clicked a link in SMS and then sent money to this number by mistake.",
    "Job offer asked for a registration payment to this wallet.",
    "Seller on a marketplace took payment and never delivered the goods.",
    "Message looked like a payment request from a shop I use.",
    "I think my account was used; I did not authorise this transfer.",
)

APPEAL_REASONS = (
    ("family", "Paying my sister for household expenses this month."),
    ("friend", "Settling money I borrowed from a friend last week."),
    ("business", "Supplier payment for goods delivered yesterday."),
    ("seller", "Paying the shop for an order I already collected."),
    ("landlord", "Monthly rent for my flat, same wallet as always."),
    ("employer", "Reimbursing office expenses my manager asked for."),
    ("other", "Known contact; we transfer this way regularly."),
    ("none", "I verified the number myself before sending."),
)

FREEZE_NOTES = (
    "Wallet linked to multiple victim reports; freeze to stop further outflow.",
    "Confirmed mule pattern on the case alerts; approve freeze for recovery.",
    "Balance still holds victim funds; freeze before the next cash-out.",
    "Subject matches prior confirmed typology; two-person freeze approved.",
)

REJECT_FREEZE_NOTES = (
    "Insufficient evidence on the alerts alone; keep monitoring the case.",
    "Subject has long clean history with these counterparties; reject for now.",
)

APPROVE_APPEAL_NOTES = (
    "Sender confirmed the payee by callback; prior transfers match the story.",
    "Relation and amount consistent with the customer's usual pattern.",
    "Held payment released after verifying the recipient with the customer.",
)

REJECT_APPEAL_NOTES = (
    "Recipient pattern still matches confirmed mule behaviour; keep the hold.",
    "Customer's explanation does not match the device and timing signals.",
    "Payee appears on related confirmed cases; appeal rejected.",
)

DECLINE_NOTES = (
    "Claimant looks like a pass-through, not a victim of this wallet.",
    "Payment does not match the reported scam story; declining the claim.",
)

PHONES = (
    "01712345678",
    "01898765432",
    "01911223344",
    "01655443322",
    "01566778899",
    "01344556677",
    "01499887766",
    "01770001122",
)

URLS = (
    "upay-secure-login.tk",
    "bkash-bonus-claim.com",
    "nagad-prize24.net",
    "pay-verify-bd.info",
    "wallet-refund.support",
    "agent-cashout.xyz",
)

BLOCK_REASONS = (
    "Number used in SMS lures directing victims to send money.",
    "Domain hosting a fake upay login page reported by customers.",
    "Appeared in multiple phishing messages tied to mule wallets.",
    "Imported from partner intel after confirmed social-engineering cases.",
)


def _as(client: httpx.Client, username: str, password: str) -> None:
    login(client, username, password)


def _counts(client: httpx.Client, path: str) -> dict[str, int]:
    response = client.get(path, params={"limit": 1})
    response.raise_for_status()
    return response.json().get("counts", {})


def refund_candidates(data_dir: Path, limit: int, seed: int) -> list[dict]:
    """Fraudulent SEND_MONEY rows, one victim payment per mule where possible."""
    frame = pd.read_parquet(
        data_dir / "transactions.parquet",
        columns=["txn_id", "sender_id", "receiver_id", "type", "is_fraud", "amount", "ts"],
    )
    fraud = frame[(frame["is_fraud"] == 1) & (frame["type"] == "SEND_MONEY")].sort_values(
        "ts", ascending=False
    )
    rng = random.Random(seed)
    picked: list[dict] = []
    seen_receiver: set[str] = set()
    seen_sender: set[str] = set()
    rows = fraud.to_dict("records")
    rng.shuffle(rows)
    for row in rows:
        receiver, sender = row["receiver_id"], row["sender_id"]
        if receiver in seen_receiver or sender in seen_sender:
            continue
        seen_receiver.add(receiver)
        seen_sender.add(sender)
        picked.append(row)
        if len(picked) >= limit:
            return picked
    for row in rows:
        if row["txn_id"] in {p["txn_id"] for p in picked}:
            continue
        picked.append(row)
        if len(picked) >= limit:
            break
    return picked


def open_cases(client: httpx.Client) -> list[dict]:
    found, offset = [], 0
    while True:
        response = client.get(
            "/v1/cases",
            params={"status": ["open", "in_review", "escalated"], "limit": PAGE, "offset": offset},
        )
        response.raise_for_status()
        page = response.json()["cases"]
        found.extend(page)
        if len(page) < PAGE:
            return found
        offset += PAGE


def appeal_candidates(client: httpx.Client, limit: int) -> list[dict]:
    """Warned or still-held payments that a customer can appeal."""
    found: list[dict] = []
    for case in open_cases(client):
        detail = client.get(f"/v1/cases/{case['id']}")
        if detail.status_code != 200:
            continue
        for alert in detail.json().get("alerts", []):
            tier = alert.get("tier")
            status = alert.get("status")
            if tier not in ("warn", "step_up", "hold"):
                continue
            if tier == "hold" and status != "held":
                continue
            found.append(
                {
                    "txn_id": alert["txn_id"],
                    "wallet_id": alert["sender_id"],
                    "tier": tier,
                    "case_id": case["id"],
                }
            )
            if len(found) >= limit * 3:
                return found
    return found


def file_reports(client: httpx.Client, data_dir: Path, password: str, seed: int) -> dict:
    _as(client, ANALYST, password)
    existing = sum(_counts(client, "/v1/refunds").values())
    need = max(0, REPORT_TARGET - existing)
    if need == 0:
        return {"reports": 0, "refunds": 0, "skipped": "already_enough"}

    _as(client, SERVICE, password)
    rng = random.Random(seed)
    made, refunds, skipped = 0, 0, Counter()
    for row in refund_candidates(data_dir, need * 2, seed):
        if made >= need:
            break
        body = {
            "reporter_id": row["sender_id"],
            "reported_wallet_id": row["receiver_id"],
            "txn_id": int(row["txn_id"]),
            "category": rng.choice(CATEGORIES),
            "description": rng.choice(REPORT_NOTES),
        }
        response = client.post("/v1/customer/reports", json=body)
        if response.status_code == 201:
            made += 1
            if response.json().get("refund") is not None:
                refunds += 1
            continue
        skipped[str(response.status_code)] += 1
    return {"reports": made, "refunds": refunds, "skipped": dict(skipped)}


def file_appeals(client: httpx.Client, password: str, seed: int) -> dict:
    _as(client, ANALYST, password)
    existing = sum(_counts(client, "/v1/appeals").values())
    need = max(0, APPEAL_TARGET - existing)
    if need == 0:
        return {"appeals": 0, "skipped": "already_enough"}

    candidates = appeal_candidates(client, need)
    rng = random.Random(seed + 1)
    rng.shuffle(candidates)
    _as(client, SERVICE, password)
    made, skipped = 0, Counter()
    for item in candidates:
        if made >= need:
            break
        relation, reason = rng.choice(APPEAL_REASONS)
        response = client.post(
            f"/v1/customer/transactions/{item['txn_id']}/appeal",
            json={"wallet_id": item["wallet_id"], "relation": relation, "reason": reason},
        )
        if response.status_code == 201:
            made += 1
        else:
            skipped[str(response.status_code)] += 1
    return {"appeals": made, "skipped": dict(skipped)}


def decline_some_refunds(client: httpx.Client, password: str, seed: int) -> dict:
    _as(client, ANALYST, password)
    open_claims = client.get("/v1/refunds", params={"status": "open", "limit": 50})
    open_claims.raise_for_status()
    rows = open_claims.json()["refunds"]
    rng = random.Random(seed + 2)
    rng.shuffle(rows)
    declined = 0
    for row in rows[:DECLINE_REFUNDS]:
        response = client.post(
            f"/v1/refunds/{row['id']}/decline",
            json={"note": rng.choice(DECLINE_NOTES)},
        )
        if response.status_code == 200:
            declined += 1
    return {"declined": declined}


def request_extra_freezes(client: httpx.Client, password: str, seed: int) -> dict:
    """A few freeze requests on open cases that the refund path did not create."""
    _as(client, ANALYST, password)
    pending = {
        r["wallet_id"]
        for r in client.get("/v1/freeze-requests", params={"status": "pending", "limit": 200}).json()
    }
    cases = [c for c in open_cases(client) if c["subject_id"] not in pending]
    rng = random.Random(seed + 3)
    rng.shuffle(cases)
    made = 0
    for case in cases:
        if made >= EXTRA_FREEZE_REQUESTS:
            break
        response = client.post(
            f"/v1/wallets/{case['subject_id']}/freeze-requests",
            json={
                "reason": (
                    f"Case #{case['id']} shows coordinated outflow risk; "
                    "requesting freeze pending verdict."
                ),
                "case_id": case["id"],
            },
        )
        if response.status_code == 201:
            made += 1
    return {"requested": made}


def _decide_freeze(
    client: httpx.Client, password: str, request_id: int, approve: bool, note: str
) -> bool:
    """Approve or reject with a supervisor who is not the requester."""
    path = "approve" if approve else "reject"
    for user in (SUPERVISOR, SUPERVISOR_B):
        _as(client, user, password)
        response = client.post(f"/v1/freeze-requests/{request_id}/{path}", json={"note": note})
        if response.status_code == 200:
            return True
        if response.status_code != 403:
            return False
    return False


def resolve_freezes(client: httpx.Client, password: str, seed: int) -> dict:
    _as(client, SUPERVISOR, password)
    pending = client.get("/v1/freeze-requests", params={"status": "pending", "limit": 200}).json()
    rng = random.Random(seed + 4)
    rng.shuffle(pending)
    approved = rejected = left = 0
    for i, row in enumerate(pending):
        if i >= APPROVE_FREEZES + REJECT_FREEZES:
            left += 1
            continue
        if i < APPROVE_FREEZES:
            if _decide_freeze(client, password, row["id"], True, rng.choice(FREEZE_NOTES)):
                approved += 1
        elif _decide_freeze(client, password, row["id"], False, rng.choice(REJECT_FREEZE_NOTES)):
            rejected += 1
    return {"approved": approved, "rejected": rejected, "left_pending": left}


def resolve_appeals(client: httpx.Client, password: str, seed: int) -> dict:
    _as(client, ANALYST, password)
    pending = client.get("/v1/appeals", params={"status": "pending", "limit": 200}).json()["appeals"]
    rng = random.Random(seed + 5)
    rng.shuffle(pending)
    approved = rejected = left = 0
    for i, row in enumerate(pending):
        if i >= APPROVE_APPEALS + REJECT_APPEALS:
            left += 1
            continue
        _as(client, ANALYST if i % 2 == 0 else ANALYST_B, password)
        if i < APPROVE_APPEALS:
            response = client.post(
                f"/v1/appeals/{row['id']}/approve",
                json={"note": rng.choice(APPROVE_APPEAL_NOTES)},
            )
            if response.status_code == 200:
                approved += 1
        else:
            response = client.post(
                f"/v1/appeals/{row['id']}/reject",
                json={"note": rng.choice(REJECT_APPEAL_NOTES)},
            )
            if response.status_code == 200:
                rejected += 1
    return {"approved": approved, "rejected": rejected, "left_pending": left}


def seed_blocklist(client: httpx.Client, password: str, seed: int) -> dict:
    _as(client, SUPERVISOR, password)
    phones_have = client.get("/v1/blocklist", params={"kind": "phone", "limit": 1}).json().get(
        "total", 0
    )
    urls_have = client.get("/v1/blocklist", params={"kind": "url", "limit": 1}).json().get("total", 0)
    rng = random.Random(seed + 6)
    added_phones = added_urls = 0
    created_ids: list[int] = []

    for phone in PHONES:
        if added_phones >= max(0, PHONE_TARGET - phones_have):
            break
        response = client.post(
            "/v1/blocklist",
            json={"kind": "phone", "value": phone, "reason": rng.choice(BLOCK_REASONS)},
        )
        if response.status_code == 201:
            added_phones += 1
            created_ids.append(response.json()["id"])

    for host in URLS:
        if added_urls >= max(0, URL_TARGET - urls_have):
            break
        body: dict = {"kind": "url", "value": host, "reason": rng.choice(BLOCK_REASONS)}
        days = rng.choice([90, 180, 365, None])
        if days is not None:
            body["expires_in_days"] = days
        response = client.post("/v1/blocklist", json=body)
        if response.status_code == 201:
            added_urls += 1
            created_ids.append(response.json()["id"])

    removed = 0
    for entry_id in created_ids[:REMOVE_BLOCKLIST]:
        response = client.post(
            f"/v1/blocklist/{entry_id}/remove",
            json={"reason": "Number/domain cleaned after partner confirmation; kept for history."},
        )
        if response.status_code == 200:
            removed += 1
    return {"phones": added_phones, "urls": added_urls, "removed": removed}


def claims(client: httpx.Client, data_dir: Path, password: str, seed: int) -> dict:
    """Customer-side activity that should exist before cases are closed."""
    return {
        "reports": file_reports(client, data_dir, password, seed),
        "appeals": file_appeals(client, password, seed),
        "declined_refunds": decline_some_refunds(client, password, seed),
    }


def resolve(client: httpx.Client, password: str, seed: int) -> dict:
    """Staff decisions that fill freeze, appeal, refund and blocklist history."""
    return {
        "extra_freezes": request_extra_freezes(client, password, seed),
        "freezes": resolve_freezes(client, password, seed),
        "appeals": resolve_appeals(client, password, seed),
        "blocklist": seed_blocklist(client, password, seed),
    }


def run(client: httpx.Client, data_dir: Path, password: str, phase: str, seed: int) -> dict:
    out: dict = {"phase": phase}
    if phase in ("claims", "all"):
        out["claims"] = claims(client, data_dir, password, seed)
    if phase == "all":
        # Close older cases so refunds can open freeze requests before we settle them.
        from . import review

        _as(client, review.REVIEW_USER, password)
        out["review"] = review.run(client, data_dir, seed=seed)
    if phase in ("resolve", "all"):
        out["resolve"] = resolve(client, password, seed)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed prior freeze, appeal, refund and blocklist activity"
    )
    parser.add_argument(
        "phase",
        nargs="?",
        choices=("claims", "resolve", "all"),
        default="all",
        help="claims before review; resolve after; all runs both",
    )
    parser.add_argument("--api", default="http://127.0.0.1:8010")
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()
    settings = Settings()
    if settings.seed_password is None:
        raise SystemExit("set FRAUDLENS_SEED_PASSWORD to the shared account password")
    password = settings.seed_password.get_secret_value()
    with httpx.Client(base_url=args.api, timeout=60.0) as client:
        report = run(client, settings.dataset_dir, password, args.phase, args.seed)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
