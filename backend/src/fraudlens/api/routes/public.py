"""What a customer can do without signing in: follow a scam report.

There is no account here, so every answer is built not to help a guesser: the same
reply whether or not a reference exists, a code that expires and burns after five
wrong tries, strict limits per address and per reference, and a status that carries
no wallet, note or model output.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ...platform import tracking
from ...platform.audit import WorkflowError
from ..deps import Db, Plat, client_ip
from ..schemas import ReportOtpRequest, ReportStatusRequest

log = logging.getLogger(__name__)
router = APIRouter(prefix="/public", tags=["public"])

_GENERIC = WorkflowError(
    400, "invalid_code", "the reference or the code is not right, or the code has expired"
)


def _limit(limiter, key: str) -> None:
    if not limiter.hit(key):
        raise WorkflowError(
            429, "rate_limited", "too many tries; please wait and try again",
            retry_after_seconds=limiter.window,
        )  # fmt: skip


@router.post("/reports/code", status_code=202)
def send_code(body: ReportOtpRequest, request: Request, p: Plat, s: Db) -> dict:
    """Send a one-time code to the phone that made the report. Always answers the same."""
    reference = tracking.normalise(body.reference)
    _limit(p.public_ip_limit, client_ip(request) or "unknown")
    _limit(p.public_ref_limit, reference)
    found = tracking.find(s, reference)
    if found is not None and p.dispatcher is not None and p.dispatcher.notifier is not None:
        report, _ = found
        code = tracking.issue_code(p.redis, reference)
        try:
            p.dispatcher.notifier.send(
                report.reporter_id,
                f"আপনার অভিযোগ ({reference}) দেখার কোড: {code}। এটি কাউকে বলবেন না।",
                f"Your code to view report {reference}: {code}. Do not share it.",
                "report.code",
            )
        except Exception:
            log.exception("could not send a report code")
    return {"sent": True}


@router.post("/reports/status")
def report_status(body: ReportStatusRequest, request: Request, p: Plat, s: Db) -> dict:
    """The safe status of a report, for the right reference and the code sent for it."""
    reference = tracking.normalise(body.reference)
    _limit(p.public_ip_limit, client_ip(request) or "unknown")
    found = tracking.find(s, reference)
    if found is None or not tracking.check_code(p.redis, reference, body.code):
        raise _GENERIC
    return tracking.summary(*found)
