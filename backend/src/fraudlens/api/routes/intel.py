"""The fraud categories the platform names, and how well the message check does on each."""

from __future__ import annotations

from fastapi import APIRouter

from ...intel.taxonomy import load_taxonomy
from ...intel.text import intel_dir
from ...intel.train import REPORT_FILE
from ..deps import Plat, Staff
from .ops import _report

router = APIRouter(prefix="/intel", tags=["intel"])


@router.get("/taxonomy")
def taxonomy(p: Plat, ctx: Staff) -> dict:
    """Every category: what it is here, what detects it, what the customer is told.

    `report` is the message classifier's evaluation as written by the training
    run, or None if it has not been run. `model` says whether one is being served.
    """
    tax = load_taxonomy()
    return {
        "version": tax.version,
        "categories": [c.model_dump() for c in tax.categories],
        "general_advice": tax.general_advice.model_dump(),
        "model": {
            "serving": p.intel is not None,
            "version": p.intel.version if p.intel is not None else None,
            "thresholds": p.intel.thresholds if p.intel is not None else None,
        },
        "report": _report(intel_dir(p.settings), REPORT_FILE),
    }
