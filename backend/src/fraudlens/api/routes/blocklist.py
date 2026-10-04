"""Known-bad wallets, phone numbers and domains, and the sign-off on Bangla texts."""

from __future__ import annotations

import csv
import io
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import func, select

from ...platform import blocklist as lists
from ...platform import translations
from ...platform.audit import WorkflowError, audit
from ...platform.models import BlocklistEntry, User
from ..deps import Db, Oversight, Plat, Reviewer, Staff, Supervisor
from ..schemas import BlocklistAdd, BlocklistImport, BlocklistRemove, TranslationSignoff

router = APIRouter(tags=["trust"])

Kind = Literal["wallet", "phone", "url"]


def _view(e: BlocklistEntry, names: dict[int, str], now) -> dict:
    live = e.removed_at is None and (e.expires_at is None or e.expires_at > now)
    return {
        "id": e.id,
        "kind": e.kind,
        "value": e.value,
        "reason": e.reason,
        "source": e.source,
        "case_id": e.case_id,
        "created_by": names.get(e.created_by, "system") if e.created_by else "system",
        "created_at": e.created_at,
        "expires_at": e.expires_at,
        "state": "removed" if e.removed_at else ("active" if live else "expired"),
        "removed_at": e.removed_at,
        "removed_by": names.get(e.removed_by) if e.removed_by else None,
        "remove_reason": e.remove_reason,
    }


@router.get("/blocklist")
def list_entries(
    p: Plat,
    s: Db,
    ctx: Reviewer,
    kind: Kind | None = None,
    state: Literal["active", "removed", "all"] = "active",
    q: Annotated[str | None, Query(max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> dict:
    now = p.scorer.now()
    where = []
    if kind:
        where.append(BlocklistEntry.kind == kind)
    if state == "active":
        where += [
            BlocklistEntry.removed_at.is_(None),
            (BlocklistEntry.expires_at.is_(None)) | (BlocklistEntry.expires_at > now),
        ]
    elif state == "removed":
        where.append(BlocklistEntry.removed_at.is_not(None))
    if q:
        where.append(func.lower(BlocklistEntry.value).startswith(q.lower(), autoescape=True))
    rows = s.scalars(
        select(BlocklistEntry)
        .where(*where)
        .order_by(BlocklistEntry.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    total = s.scalar(select(func.count()).select_from(BlocklistEntry).where(*where))
    ids = {i for e in rows for i in (e.created_by, e.removed_by) if i}
    names = (
        dict(s.execute(select(User.id, User.username).where(User.id.in_(ids))).all()) if ids else {}
    )
    return {"total": total, "entries": [_view(e, names, now) for e in rows]}


@router.post("/blocklist", status_code=201)
def add_entry(body: BlocklistAdd, p: Plat, ctx: Supervisor) -> dict:
    expires = (
        p.scorer.now() + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    )
    with p.scorer.transaction() as s:
        entry = lists.add(s, ctx, body.kind, body.value, body.reason, "manual", expires)
        p.scorer.load_blocklist(s)
        return _view(entry, {ctx.user_id: ctx.username}, p.scorer.now())


@router.post("/blocklist/import")
def import_entries(body: BlocklistImport, p: Plat, ctx: Supervisor) -> dict:
    """Add many at once. Values that are invalid or already listed are skipped and counted."""
    added = skipped = 0
    with p.scorer.transaction() as s:
        for row in body.entries:
            try:
                lists.add(s, ctx, row.kind, row.value, body.reason, "import")
                added += 1
            except WorkflowError:
                skipped += 1
        audit(s, ctx, "blocklist.import", "blocklist", None, added=added, skipped=skipped)
        p.scorer.load_blocklist(s)
    return {"added": added, "skipped": skipped}


@router.post("/blocklist/{entry_id}/remove")
def remove_entry(entry_id: int, body: BlocklistRemove, p: Plat, ctx: Supervisor) -> dict:
    with p.scorer.transaction() as s:
        entry = lists.remove(s, ctx, entry_id, body.reason, p.scorer.now())
        p.scorer.load_blocklist(s)
        return _view(entry, {ctx.user_id: ctx.username}, p.scorer.now())


# -------------------------------------------------- Bangla text sign-off


@router.get("/policy/translations")
def translation_status(p: Plat, s: Db, ctx: Staff) -> dict:
    """Every Bangla text in the policy in force, and whether a translator has signed it off."""
    items = translations.status(s, p.scorer.policy)
    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"policy_version": p.scorer.policy.version, "counts": counts, "texts": items}


@router.get("/policy/translations.csv")
def translation_sheet(p: Plat, s: Db, ctx: Staff) -> Response:
    """The texts as a sheet to hand to a translator."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["key", "kind", "english", "bangla", "status", "reviewed_by"])
    for t in translations.status(s, p.scorer.policy):
        writer.writerow(
            [t["key"], t["kind"], t["en"], t["bn"], t["status"], t["reviewed_by"] or ""]
        )
    return Response(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="fraudlens-bangla-texts.csv"'},
    )


@router.post("/policy/translations/{key:path}/review", status_code=201)
def sign_off(key: str, body: TranslationSignoff, p: Plat, s: Db, ctx: Oversight) -> dict:
    row = translations.review(
        s, ctx, p.scorer.policy, key, body.status, body.reviewer_name, body.note
    )
    s.commit()
    return {"key": key, "status": row.status, "reviewed_by": row.reviewer_name}
