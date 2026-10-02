"""Cues: what a message does, recognised by pattern (`cues.yaml`).

A cue is a feature, not a verdict. The classifier is given which cues a message
shows, and each pair of them, and learns from the corpus what they are worth:
a phone number alone is nothing, a phone number with a fee to pay first is not.
The cues that matched are also what a customer is shown as the reason.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from itertools import combinations
from pathlib import Path

import numpy as np
import yaml
from sklearn.base import BaseEstimator, TransformerMixin

CUES_PATH = Path(__file__).parent / "cues.yaml"


@dataclass(frozen=True)
class Cue:
    id: str
    label: dict[str, str]
    patterns: tuple[re.Pattern[str], ...]
    benign: bool = False
    unless: str | None = None


@cache
def load_cues(path: Path = CUES_PATH) -> tuple[Cue, ...]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))["cues"]
    cues = tuple(
        Cue(
            id=c["id"],
            label={"en": c["label"]["en"], "bn": c["label"]["bn"]},
            # The text is NFKC-normalised before matching, so the patterns must be too.
            patterns=tuple(re.compile(unicodedata.normalize("NFKC", p)) for p in c["patterns"]),
            benign=bool(c.get("benign", False)),
            unless=c.get("unless"),
        )
        for c in raw
    )
    ids = [c.id for c in cues]
    if len(set(ids)) != len(ids):
        raise ValueError("cue ids must be unique")
    for c in cues:
        if c.unless is not None and c.unless not in ids:
            raise ValueError(f"cue {c.id}: unless names unknown cue {c.unless}")
    return cues


def matched(normalised: str) -> list[Cue]:
    """The cues a normalised text shows, in file order."""
    cues = load_cues()
    hit = {c.id for c in cues if any(p.search(normalised) for p in c.patterns)}
    return [c for c in cues if c.id in hit and c.unless not in hit]


class CueFeatures(BaseEstimator, TransformerMixin):
    """One column per cue and one per pair of cues, each 0 or 1."""

    def fit(self, X, y=None):
        return self

    def transform(self, X) -> np.ndarray:
        ids = [c.id for c in load_cues()]
        index = {cid: i for i, cid in enumerate(ids)}
        pairs = list(combinations(range(len(ids)), 2))
        out = np.zeros((len(X), len(ids) + len(pairs)), dtype=np.float32)
        for row, text in enumerate(X):
            on = [index[c.id] for c in matched(text)]
            out[row, on] = 1.0
        single = out[:, : len(ids)]
        for col, (a, b) in enumerate(pairs, start=len(ids)):
            out[:, col] = single[:, a] * single[:, b]
        return out

    def get_feature_names_out(self, input_features=None) -> np.ndarray:
        ids = [c.id for c in load_cues()]
        return np.array(ids + [f"{ids[a]}+{ids[b]}" for a, b in combinations(range(len(ids)), 2)])
