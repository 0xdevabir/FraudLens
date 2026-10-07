"""Known-bad wallets, phone numbers and web domains.

A listed wallet raises the tier of a payment to it (policy rule R08) so the
customer is asked to verify and wait; it never blocks money, and a person
decides what happens to anything that is held. A listed phone number or domain
makes the message check call a message high risk. Entries come from supervisors,
from confirmed-fraud verdicts, or from an import, and are removed rather than
deleted so the history stays readable.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..intel.links import URL
from .audit import Ctx, WorkflowError, audit
from .models import BlocklistEntry

_WALLET = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_PHONE_DIGITS = re.compile(r"(?<!\d)(?:\+?88)?0?1[3-9](?:[ -]?\d){8}(?!\d)")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")


def normalise_phone(raw: str) -> str | None:
    """A Bangladesh mobile number as `01XXXXXXXXX`, or None if it is not one."""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("880"):
        digits = digits[3:]
    if len(digits) == 10 and digits.startswith("1"):
        digits = "0" + digits
    return digits if re.fullmatch(r"01[3-9]\d{8}", digits) else None


def host_of(raw: str) -> str | None:
    """The host of a link or bare domain, lower-case, without `www.`; None if there is none."""
    text = raw.strip().lower()
    if "://" not in text:
        text = "http://" + text
    try:
        host = urlsplit(text).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.removeprefix("www.").rstrip(".")
    return host if _HOST.match(host) and "." in host else None


def normalise(kind: str, value: str) -> str:
    value = value.strip()
    out = None
    if kind == "wallet":
        out = value if _WALLET.match(value) else None
    elif kind == "phone":
        out = normalise_phone(value)
    elif kind == "url":
        out = host_of(value)
    if out is None:
        raise WorkflowError(422, "invalid_value", f"that is not a valid {kind}")
    return out


def _live(now: datetime):
    return (
        BlocklistEntry.removed_at.is_(None),
        or_(BlocklistEntry.expires_at.is_(None), BlocklistEntry.expires_at > now),
    )


def add(
    s: Session,
    ctx: Ctx,
    kind: str,
    value: str,
    reason: str,
    source: str = "manual",
    expires_at: datetime | None = None,
    case_id: int | None = None,
) -> BlocklistEntry:
    """List a value. Listing one that is already live is a conflict, except from the
    system (a verdict), where it is simply already done."""
    value = normalise(kind, value)
    entry = BlocklistEntry(
        kind=kind, value=value, reason=reason, source=source, expires_at=expires_at,
        case_id=case_id, created_by=ctx.user_id,
    )  # fmt: skip
    try:
        with s.begin_nested():
            s.add(entry)
            s.flush()
    except IntegrityError:
        existing = s.scalar(
            select(BlocklistEntry).where(
                BlocklistEntry.kind == kind,
                BlocklistEntry.value == value,
                BlocklistEntry.removed_at.is_(None),
            )
        )
        if source == "verdict" and existing is not None:
            return existing
        raise WorkflowError(409, "already_listed", f"that {kind} is already on the list") from None
    audit(s, ctx, "blocklist.add", "blocklist", entry.id, kind=kind, source=source, case_id=case_id)
    return entry


def remove(s: Session, ctx: Ctx, entry_id: int, reason: str, now: datetime) -> BlocklistEntry:
    entry = s.scalar(select(BlocklistEntry).where(BlocklistEntry.id == entry_id).with_for_update())
    if entry is None:
        raise WorkflowError(404, "entry_not_found", f"no blocklist entry {entry_id}")
    if entry.removed_at is not None:
        raise WorkflowError(409, "already_removed", "the entry was already removed")
    entry.removed_at, entry.removed_by, entry.remove_reason = now, ctx.user_id, reason
    audit(s, ctx, "blocklist.remove", "blocklist", entry.id, kind=entry.kind)
    return entry


def wallets(s: Session) -> dict[str, float | None]:
    """Live wallet entries as {wallet: expiry as epoch seconds, or None}: what the scorer holds."""
    rows = s.execute(
        select(BlocklistEntry.value, BlocklistEntry.expires_at).where(
            BlocklistEntry.kind == "wallet", BlocklistEntry.removed_at.is_(None)
        )
    ).all()
    return {value: (at.timestamp() if at else None) for value, at in rows}


def listed(entries: dict[str, float | None] | None, wallet: str, ts: float) -> bool:
    """Is `wallet` on the list at transaction time `ts`?"""
    if not entries or wallet not in entries:
        return False
    expires = entries[wallet]
    return expires is None or expires > ts


def in_text(s: Session, text: str, now: datetime) -> list[str]:
    """The kinds ('phone', 'url') of live entries a message mentions. Values are not returned."""
    phones = {n for m in _PHONE_DIGITS.finditer(text) if (n := normalise_phone(m.group()))}
    hosts: set[str] = set()
    for match in URL.finditer(text):
        host = host_of(match.group())
        if host:
            labels = host.split(".")
            # The host and every parent domain: listing `evil.com` covers `login.evil.com`.
            hosts.update(".".join(labels[i:]) for i in range(len(labels) - 1))
    found: set[str] = set()
    if phones:
        hit = s.scalar(
            select(BlocklistEntry.id)
            .where(BlocklistEntry.kind == "phone", BlocklistEntry.value.in_(phones), *_live(now))
            .limit(1)
        )
        if hit is not None:
            found.add("phone")
    if hosts:
        hit = s.scalar(
            select(BlocklistEntry.id)
            .where(BlocklistEntry.kind == "url", BlocklistEntry.value.in_(hosts), *_live(now))
            .limit(1)
        )
        if hit is not None:
            found.add("url")
    return sorted(found)
