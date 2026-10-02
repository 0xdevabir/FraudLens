"""After deployment: the model registry, the challenger, live drift, the labels coming back."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from ...decision.insights import DRIFT_REFERENCE_FILE
from ...features import FEATURES
from ...mlops import drift, feedback
from ...mlops import shadow as shadow_mode
from ...models import registry
from ...platform.audit import WorkflowError, audit
from ...platform.models import Case
from ..deps import Db, Plat, Reviewer, Staff
from ..schemas import Reveal
from .ops import _report

router = APIRouter(tags=["mlops"])

Version = Annotated[str, Query(pattern=r"^v\d{1,6}$")]


@router.get("/models")
def models(p: Plat, ctx: Staff) -> dict:
    """Every registered model version, and which one is serving, promoted or in shadow."""
    root = p.settings.models_dir
    serving = p.scorer.bundle.version if p.scorer.bundle is not None else None
    in_shadow = p.scorer.shadow.version if p.scorer.shadow is not None else None
    promoted = registry.current_version(root)
    found = []
    for version in reversed(registry.versions(root)):
        manifest = _report(root / version, "manifest.json")
        if manifest is None:
            continue
        used = manifest.get("feedback")
        found.append(
            {
                "version": version,
                "created_at": manifest.get("created_at"),
                "parent": manifest.get("parent"),
                "risk_source": manifest.get("risk_source"),
                "trees": manifest.get("trees"),
                "thresholds": manifest.get("thresholds"),
                "headline": manifest.get("headline"),
                "feedback": used and {
                    key: used.get(key)
                    for key in ("rows", "fraud", "legitimate", "last_label_at",
                                "test_rows_after_last_label", "comparison_after_last_label")
                },
                "serving": version == serving,
                "promoted": version == promoted,
                "shadow": version == in_shadow,
            }
        )  # fmt: skip
    return {
        "serving": serving,
        "promoted": promoted,
        "shadow": in_shadow,
        # Promotion is a deliberate step outside the console; the API picks it up on restart.
        "restart_needed": promoted != serving and p.settings.model_version is None,
        "versions": found,
    }


@router.get("/model/shadow")
def shadow(p: Plat, s: Db, ctx: Staff, version: Version | None = None) -> dict:
    """The challenger against the served model on the decisions both have scored."""
    available = shadow_mode.versions(s)
    live = p.scorer.shadow.version if p.scorer.shadow is not None else None
    version = version or live or (available[-1] if available else None)
    if version is None:
        return {"status": "no_challenger", "available": [], "live": None}
    if version not in available:
        return {"status": "no_scores", "model_version": version, "available": available}
    return {
        "status": "ok",
        "available": available,
        "live": live,
        **shadow_mode.compare(s, version),
    }


@router.get("/metrics/drift")
def live_drift(
    p: Plat,
    s: Db,
    ctx: Staff,
    rows: Annotated[int, Query(ge=drift.MIN_ROWS, le=100_000)] = drift.DEFAULT_ROWS,
) -> dict:
    """Have the inputs or the score moved away from what the served model was trained on?

    Measured on the latest decisions actually served, against the training period.
    """
    scorer = p.scorer
    if scorer.bundle is None:
        return {"status": "rules_only"}
    version = scorer.bundle.version
    reference = _report(p.settings.models_dir / version, DRIFT_REFERENCE_FILE)
    if reference is None or set(reference.get("features", ())) != set(FEATURES):
        return {"status": "no_reference", "model_version": version}
    return drift.live(s, reference, scorer.decision.thresholds, version, rows)


@router.get("/feedback")
def feedback_summary(p: Plat, s: Db, ctx: Staff) -> dict:
    """What the review work has given back as labels, and which versions learnt from it."""
    root = p.settings.models_dir
    trained = []
    for version in registry.versions(root):
        manifest = _report(root / version, "manifest.json") or {}
        used = manifest.get("feedback")
        if used:
            trained.append(
                {
                    "version": version,
                    "created_at": manifest.get("created_at"),
                    "rows": used.get("rows"),
                    "last_label_at": used.get("last_label_at"),
                }
            )
    return {**feedback.summary(s), "versions_trained_on_feedback": trained}


@router.post("/audit/reveals", status_code=201)
def reveal(body: Reveal, s: Db, ctx: Reviewer) -> dict:
    """Record that someone unmasked an identifier. The console shows them masked
    until this call succeeds, so every look at a full identifier has a name on it."""
    if (
        body.case_id is not None
        and s.scalar(select(Case.id).where(Case.id == body.case_id)) is None
    ):
        raise WorkflowError(404, "case_not_found", f"no case {body.case_id}")
    audit(s, ctx, "pii.reveal", body.object_type, body.object_id, case_id=body.case_id)
    s.commit()
    return {"object_type": body.object_type, "object_id": body.object_id, "revealed": True}
