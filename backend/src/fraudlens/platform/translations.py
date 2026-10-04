"""Sign-off on the Bangla texts customers read.

The warning texts and rule descriptions were written by the developers. This
records when a translator has read each one and agreed, or asked for changes. A
sign-off is tied to the exact wording (a hash), so editing a text, which means
adding a new policy version, makes it unreviewed again.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..decision.policy import Policy
from .audit import Ctx, WorkflowError, audit
from .models import TranslationReview
from .tracking import STATUS_TEXT


def digest(bn: str) -> str:
    return hashlib.sha256(bn.encode()).hexdigest()


def texts(policy: Policy) -> list[dict]:
    """Every Bangla text in the policy, with a stable key."""
    out = []
    for rule in policy.rules:
        out.append(
            {"key": f"rule:{rule.id}", "kind": "rule", "label": rule.id,
             "en": rule.description, "bn": rule.description_bn}
        )  # fmt: skip
    for scenario, by_tier in policy.messages.items():
        for tier, text in by_tier.items():
            out.append(
                {"key": f"message:{scenario}.{tier}", "kind": "customer_message",
                 "label": f"{scenario} / {tier}", "en": text.en, "bn": text.bn}
            )  # fmt: skip
    for rec in policy.recommendations:
        out.append(
            {"key": f"recommendation:{rec.id}", "kind": "recommendation", "label": rec.id,
             "en": rec.en, "bn": rec.bn}
        )  # fmt: skip
    for status, parts in STATUS_TEXT.items():
        for part, text in parts.items():
            out.append(
                {"key": f"tracking:{status}.{part}", "kind": "report_status",
                 "label": f"report status / {status} / {part}", "en": text["en"], "bn": text["bn"]}
            )  # fmt: skip
    return out


def _latest(s: Session) -> dict[str, TranslationReview]:
    latest: dict[str, TranslationReview] = {}
    for row in s.scalars(select(TranslationReview).order_by(TranslationReview.id)):
        latest[row.key] = row
    return latest


def status(s: Session, policy: Policy) -> list[dict]:
    """Each text with its review state: `approved`, `changes_requested`, `unreviewed`,
    or `outdated` when it was reviewed in an earlier wording."""
    latest = _latest(s)
    out = []
    for item in texts(policy):
        row = latest.get(item["key"])
        state = "unreviewed"
        if row is not None:
            state = row.status if row.text_hash == digest(item["bn"]) else "outdated"
        out.append(
            item
            | {
                "status": state,
                "reviewed_by": row.reviewer_name if row else None,
                "reviewed_at": row.reviewed_at if row else None,
                "note": row.note if row else None,
            }
        )
    return out


def review(
    s: Session, ctx: Ctx, policy: Policy, key: str, verdict: str, reviewer_name: str,
    note: str | None,
) -> TranslationReview:  # fmt: skip
    item = next((t for t in texts(policy) if t["key"] == key), None)
    if item is None:
        raise WorkflowError(404, "text_not_found", f"no text {key!r} in policy {policy.version}")
    row = TranslationReview(
        key=key, text_hash=digest(item["bn"]), status=verdict, reviewer_id=ctx.user_id,
        reviewer_name=reviewer_name, note=note,
    )  # fmt: skip
    s.add(row)
    s.flush()
    audit(s, ctx, "translation.review", "translation", key, status=verdict, reviewer=reviewer_name)
    return row
