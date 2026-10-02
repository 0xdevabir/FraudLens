"""Which fraud categories an alert belongs to, read from the evidence already on it.

Nothing new is scored here. A decision already carries its scenario, its model
scores and the past cases it most resembles; this names the categories those
point to, and says which piece of evidence each name rests on.
"""

from __future__ import annotations

from collections import defaultdict

from .taxonomy import Taxonomy

MULE_SCORE = 0.5  # the receiver model's score from which the receiver is called a likely mule
MIN_SIMILARITY = 0.5  # weaker resemblances are not used to name a category


def categorise(
    scenario: str | None,
    scores: dict | None,
    similar_cases: list[dict] | None,
    taxonomy: Taxonomy,
) -> list[dict]:
    basis: dict[str, list[str]] = defaultdict(list)

    for cid in taxonomy.scenarios.get(scenario or "", []):
        basis[cid].append("scenario")

    # The typology of the past cases this one resembles, weighted by how closely.
    weight: dict[str, float] = defaultdict(float)
    for case in similar_cases or []:
        similarity = case.get("similarity") or 0.0
        if similarity >= MIN_SIMILARITY and case.get("typology"):
            weight[case["typology"]] += similarity
    if weight:
        typology = max(weight, key=weight.__getitem__)
        for cid in taxonomy.typologies.get(typology, []):
            basis[cid].append("similar_cases")

    if ((scores or {}).get("mule") or 0.0) >= MULE_SCORE:
        basis["financial_network"].append("mule_score")

    return [
        {"id": c.id, "number": c.number, "name": c.name.model_dump(), "basis": basis[c.id]}
        for c in taxonomy.categories
        if c.id in basis
    ]
