"""Following a scam report: a reference, a one-time code, and a status that says no more
than the customer needs.

The status is worked out from the case the report belongs to, never stored, so it cannot
drift from what the investigation did. What leaves here is the status and fixed wording:
no wallet, no analyst note, no model output, and nothing about anyone else's case.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Case, CustomerReport

# No 0, O, 1, I or L: a reference is read aloud and typed from a phone.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
OTP_TTL_SECONDS = 600
OTP_TRIES = 5

STATUS_TEXT: dict[str, dict[str, dict[str, str]]] = {
    "received": {
        "title": {"en": "We have your report", "bn": "আমরা আপনার অভিযোগ পেয়েছি"},
        "detail": {
            "en": "Your report has reached upay's fraud team. A person will look at it.",
            "bn": "আপনার অভিযোগ উপায়ের প্রতারণা প্রতিরোধ দলের কাছে পৌঁছেছে। একজন কর্মকর্তা এটি দেখবেন।",
        },
    },
    "investigating": {
        "title": {"en": "We are looking into it", "bn": "আমরা বিষয়টি তদন্ত করছি"},
        "detail": {
            "en": "A fraud officer is investigating. We may contact you on your registered number. "
            "Never share your PIN or OTP with anyone.",
            "bn": "একজন কর্মকর্তা তদন্ত করছেন। আমরা আপনার নিবন্ধিত নম্বরে যোগাযোগ করতে পারি। "
            "পিন বা ওটিপি কাউকে দেবেন না।",
        },
    },
    "action_taken": {
        "title": {"en": "Action has been taken", "bn": "ব্যবস্থা নেওয়া হয়েছে"},
        "detail": {
            "en": "Our review found this was fraud and we have acted on the wallet involved. "
            "We cannot promise that money can be returned.",
            "bn": "আমাদের পর্যালোচনায় এটি প্রতারণা বলে প্রমাণিত হয়েছে এবং সংশ্লিষ্ট ওয়ালেটের বিরুদ্ধে "
            "ব্যবস্থা নেওয়া হয়েছে। টাকা ফেরত পাওয়ার নিশ্চয়তা আমরা দিতে পারি না।",
        },
    },
    "closed": {
        "title": {"en": "Review finished", "bn": "পর্যালোচনা শেষ হয়েছে"},
        "detail": {
            "en": "We reviewed your report and could not confirm fraud. "
            "If you have more details, you can report again.",
            "bn": "আমরা আপনার অভিযোগ পর্যালোচনা করেছি, কিন্তু প্রতারণা নিশ্চিত করতে পারিনি। "
            "আরও তথ্য থাকলে আবার অভিযোগ করতে পারেন।",
        },
    },
}


def new_reference(s: Session) -> str:
    """`FL-XXXX-XXXX`, not used by any other report."""
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(8))
        reference = f"FL-{code[:4]}-{code[4:]}"
        if s.scalar(select(CustomerReport.id).where(CustomerReport.reference == reference)) is None:
            return reference


def normalise(reference: str) -> str:
    """What a customer typed, as stored: upper case, with the dashes in the right places."""
    flat = "".join(ch for ch in reference.upper() if ch.isalnum())
    if flat.startswith("FL"):
        flat = flat[2:]
    return f"FL-{flat[:4]}-{flat[4:]}" if len(flat) == 8 else reference.upper()


def status_of(case: Case | None) -> str:
    if case is None or (case.status == "open" and case.assigned_to is None):
        return "received"  # nobody has picked it up yet
    if case.status != "closed":
        return "investigating"
    return "action_taken" if case.verdict == "confirmed_fraud" else "closed"


def summary(report: CustomerReport, case: Case | None) -> dict:
    status = status_of(case)
    return {
        "reference": report.reference,
        "status": status,
        "title": STATUS_TEXT[status]["title"],
        "detail": STATUS_TEXT[status]["detail"],
        "reported_at": report.reported_at,
        "updated_at": case.closed_at if case is not None and case.status == "closed" else None,
    }


def find(s: Session, reference: str) -> tuple[CustomerReport, Case | None] | None:
    report = s.scalar(
        select(CustomerReport).where(CustomerReport.reference == normalise(reference))
    )
    if report is None:
        return None
    return report, (s.get(Case, report.case_id) if report.case_id else None)


# ------------------------------------------------------------ one-time code


def _otp_key(reference: str) -> str:
    return f"fraudlens:otp:{reference}"


def _digest(reference: str, code: str) -> str:
    return hashlib.sha256(f"{reference}:{code}".encode()).hexdigest()


def issue_code(redis: Redis, reference: str) -> str:
    """A fresh six-digit code, valid for ten minutes. Only a hash of it is kept."""
    code = f"{secrets.randbelow(10**6):06d}"
    key = _otp_key(reference)
    pipe = redis.pipeline()
    pipe.delete(key)
    pipe.hset(key, mapping={"h": _digest(reference, code), "tries": 0})
    pipe.expire(key, OTP_TTL_SECONDS)
    pipe.execute()
    return code


def check_code(redis: Redis, reference: str, code: str) -> bool:
    """True if `code` is the live code for `reference`. Five wrong guesses burn the code."""
    key = _otp_key(reference)
    stored = redis.hgetall(key)
    if not stored:
        return False
    tries = redis.hincrby(key, "tries", 1)
    if tries > OTP_TRIES:
        redis.delete(key)
        return False
    return hmac.compare_digest(stored["h"], _digest(reference, code))
