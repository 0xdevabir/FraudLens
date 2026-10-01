"""Find past confirmed scams that look like the transaction in front of the analyst.

"Looks like" is measured in explanation space: two transactions are similar when
the model found them risky for the same reasons (their SHAP contribution vectors
point the same way), not merely when their raw numbers are close. The index holds
confirmed victim transfers from the training and validation periods only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..features import FEATURES
from ..models.bundle import ModelBundle

INDEX_FILE = "similar_cases.npz"
INDEX_FOLDS = ("train", "val_a", "val_b")  # never the test period


def _unit(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1.0, norms)


@dataclass
class SimilarCases:
    vectors: np.ndarray  # [n, len(FEATURES)], unit length
    case_id: np.ndarray
    typology: np.ndarray
    day: np.ndarray
    amount: np.ndarray  # this transfer
    loss: np.ndarray  # the whole case
    n_txn: np.ndarray

    def __len__(self) -> int:
        return len(self.case_id)

    @classmethod
    def build(cls, bundle: ModelBundle, frame: pd.DataFrame, cases: pd.DataFrame) -> SimilarCases:
        """Index the victim transfers in `frame` (output of models.data.load_frame)."""
        rows = frame[(frame["y_loss"] == 1) & frame["fold"].isin(INDEX_FOLDS)]
        if rows.empty:
            raise ValueError("no confirmed victim transfers to index")
        contrib, _ = bundle.contributions(rows[list(FEATURES)].to_numpy())
        meta = cases.set_index("case_id").loc[rows["case_id"]]
        return cls(
            vectors=_unit(contrib).astype(np.float32),
            case_id=rows["case_id"].to_numpy(np.int64),
            typology=rows["typology"].to_numpy(str),
            day=rows["day"].to_numpy(np.int64),
            amount=rows["amount"].to_numpy(np.float64),
            loss=meta["loss"].to_numpy(np.float64),
            n_txn=meta["n_txn"].to_numpy(np.int64),
        )

    def save(self, directory: Path) -> Path:
        path = directory / INDEX_FILE
        np.savez_compressed(path, **self.__dict__)
        return path

    @classmethod
    def load(cls, directory: Path) -> SimilarCases:
        # Plain arrays only: allow_pickle stays off so loading can never run code.
        with np.load(directory / INDEX_FILE, allow_pickle=False) as data:
            index = cls(**{name: data[name] for name in data.files})
        if index.vectors.shape[1] != len(FEATURES):
            raise ValueError("similar-case index was built for a different feature list")
        return index

    def query(
        self, contribution: np.ndarray, k: int = 3, min_similarity: float = 0.5
    ) -> list[dict]:
        """The `k` most similar past cases, at most one entry per case."""
        vector = _unit(np.asarray(contribution, dtype=np.float32).reshape(1, -1))[0]
        similarity = self.vectors @ vector
        found: dict[int, dict] = {}
        for i in np.argsort(-similarity):
            if similarity[i] < min_similarity or len(found) == k:
                break
            case = int(self.case_id[i])
            if case not in found:
                found[case] = {
                    "case_id": case,
                    "typology": str(self.typology[i]),
                    "similarity": round(float(similarity[i]), 3),
                    "amount": float(self.amount[i]),
                    "loss": float(self.loss[i]),
                    "n_txn": int(self.n_txn[i]),
                    "day": int(self.day[i]),
                }
        return list(found.values())

    def top_typology(self, contributions: np.ndarray) -> np.ndarray:
        """Typology of the single nearest case for each row (used to evaluate the index)."""
        best = np.argmax(
            _unit(np.asarray(contributions, dtype=np.float32)) @ self.vectors.T, axis=1
        )
        return self.typology[best]
