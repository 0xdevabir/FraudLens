"""Decision layer: policy, tiers, reasons and case notes on top of the model scores."""

from .engine import Decision, DecisionEngine, context_from_engine, display_score
from .narrative import LLMNarrator, check_grounding, mask_id, narrate, template
from .policy import TIERS, Policy, PolicyError, apply_policy, load_policy
from .similar import SimilarCases

__all__ = [
    "TIERS",
    "Decision",
    "DecisionEngine",
    "LLMNarrator",
    "Policy",
    "PolicyError",
    "SimilarCases",
    "apply_policy",
    "check_grounding",
    "context_from_engine",
    "display_score",
    "load_policy",
    "mask_id",
    "narrate",
    "template",
]
