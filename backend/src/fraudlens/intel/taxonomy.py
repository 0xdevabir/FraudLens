"""The fraud categories FraudLens names, loaded from `taxonomy.yaml` and validated.

The file is the single place that says what a category is, what a customer is
told about it and how the platform's older names (simulator typologies, decision
scenarios, report categories) map onto it.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

TAXONOMY_PATH = Path(__file__).parent / "taxonomy.yaml"

Detector = Literal["transaction", "text", "links", "ledger"]
ProofStatus = Literal["verified", "mismatch", "not_found"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Text2(_Strict):
    en: str = Field(min_length=1)
    bn: str = Field(min_length=1)


class Category(_Strict):
    id: str = Field(pattern=r"^[a-z_]{3,24}$")
    number: int = Field(ge=1)
    name: Text2
    summary: str
    bangladesh: str
    examples: list[str] = Field(min_length=1)
    detectors: list[Detector] = Field(min_length=1)
    signals: list[str] = Field(min_length=1)
    advice: Text2


class Taxonomy(_Strict):
    version: str
    categories: list[Category] = Field(min_length=1)
    general_advice: Text2
    typologies: dict[str, list[str]]
    scenarios: dict[str, list[str]]
    report_categories: dict[str, list[str]]
    brands: dict[str, list[str]]
    proof_messages: dict[ProofStatus, Text2]

    @model_validator(mode="after")
    def _consistent(self) -> Taxonomy:
        ids = [c.id for c in self.categories]
        if len(set(ids)) != len(ids):
            raise ValueError("category ids must be unique")
        if [c.number for c in self.categories] != list(range(1, len(ids) + 1)):
            raise ValueError("categories must be numbered 1..n in order")
        for name in ("typologies", "scenarios", "report_categories"):
            for key, mapped in getattr(self, name).items():
                unknown = set(mapped) - set(ids)
                if unknown:
                    raise ValueError(f"{name}.{key} names unknown categories {sorted(unknown)}")
        if set(self.proof_messages) != {"verified", "mismatch", "not_found"}:
            raise ValueError("proof_messages needs verified, mismatch and not_found")
        return self

    @property
    def ids(self) -> list[str]:
        return [c.id for c in self.categories]

    def category(self, category_id: str) -> Category:
        return next(c for c in self.categories if c.id == category_id)


@cache
def load_taxonomy(path: Path = TAXONOMY_PATH) -> Taxonomy:
    return Taxonomy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
