"""The scam-message classifier: is this text a scam, and of which kinds.

One logistic regression per output (scam or not, then one per category) over two
kinds of feature: character n-grams, and cues (`cues.py`: what the message does,
such as asking for a code or wanting a fee first). Character n-grams are what
make it work across Bangla script, Bangla written in Latin letters with no fixed
spelling, and English, without a tokenizer or a language detector for any of
them. The cues are what carry it to a scam script it was never trained on.

Numbers and links are reduced to their shape before the model sees them: it
learns that a message carries an eleven-digit number or a link, never which one.
What a link points to is the link check's job (`links.py`).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline

from ..config import Settings
from .cues import Cue, CueFeatures, load_cues, matched
from .links import URL

log = logging.getLogger(__name__)

MODEL_FILE = "text_model.joblib"
MANIFEST_FILE = "manifest.json"
SCAM = "scam"
MAX_CHARS = 2000  # the longest text the API accepts

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_DIGIT = re.compile(r"\d")
_SPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Lower-case, one form per character, digits as `0`, links as one token."""
    text = unicodedata.normalize("NFKC", text[:MAX_CHARS]).translate(_BN_DIGITS).lower()
    text = URL.sub(" urltoken ", text)
    return _SPACE.sub(" ", _DIGIT.sub("0", text)).strip()


def build_pipeline(seed: int = 7) -> Pipeline:
    features = FeatureUnion(
        [
            (
                "chars",
                TfidfVectorizer(
                    analyzer="char_wb", ngram_range=(2, 5), min_df=2, sublinear_tf=True,
                    max_features=60_000, lowercase=False,
                ),
            ),
            ("cues", CueFeatures()),
        ],
        # A message's n-grams have length 1 together; without this a handful of
        # cues would outweigh them and the wording would stop counting.
        transformer_weights={"chars": 1.0, "cues": 0.5},
    )  # fmt: skip
    return Pipeline([("features", features), ("model", MultiHead(C=2.0, seed=seed))])


class MultiHead(BaseEstimator, ClassifierMixin):
    """One logistic regression per output, like one-vs-rest, except that a label of
    -1 means "not known" and leaves that row out of that output. A message from the
    public corpus (`external.py`) is known to be a scam or not, but not of which
    category, so it trains the scam output only."""

    def __init__(self, C: float = 2.0, seed: int = 7) -> None:
        self.C = C
        self.seed = seed

    def fit(self, X, Y) -> MultiHead:
        Y = np.asarray(Y)
        self.estimators_ = []
        for j in range(Y.shape[1]):
            known = Y[:, j] >= 0
            est = LogisticRegression(
                C=self.C, max_iter=3000, class_weight="balanced", random_state=self.seed
            )
            self.estimators_.append(est.fit(X[known], Y[known, j]))
        return self

    def predict_proba(self, X) -> np.ndarray:
        return np.column_stack([est.predict_proba(X)[:, 1] for est in self.estimators_])


@dataclass
class TextModel:
    version: str
    pipeline: Pipeline
    outputs: list[str]  # "scam", then the category ids
    thresholds: dict[str, float]  # caution, high (on the scam score), category

    @property
    def categories(self) -> list[str]:
        return self.outputs[1:]

    def score(self, texts: list[str]) -> np.ndarray:
        """One row per text, one column per output, each a probability."""
        return self.pipeline.predict_proba([normalise(t) for t in texts])

    def level(self, scam: float) -> str:
        if scam >= self.thresholds["high"]:
            return "high"
        return "caution" if scam >= self.thresholds["caution"] else "none"

    @staticmethod
    def signals(text: str) -> list[Cue]:
        """What the message does that a scam does: the cues that matched, minus
        the ones that point the other way. Shown as the reason; nothing is generated."""
        return [cue for cue in matched(normalise(text)) if not cue.benign]

    # -------------------------------------------------------------- storage

    def save(self, directory: Path, **manifest) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.pipeline, directory / MODEL_FILE, compress=3)
        body = {
            "version": self.version,
            "outputs": self.outputs,
            "thresholds": self.thresholds,
            "cues": [cue.id for cue in load_cues()],
        }
        (directory / MANIFEST_FILE).write_text(json.dumps(body | manifest, indent=2))

    @classmethod
    def load(cls, directory: Path) -> TextModel:
        manifest = json.loads((directory / MANIFEST_FILE).read_text())
        # Our own build artifact; never load a model file from an untrusted source.
        pipeline = joblib.load(directory / MODEL_FILE)
        return cls(manifest["version"], pipeline, manifest["outputs"], manifest["thresholds"])


def intel_dir(settings: Settings) -> Path:
    return settings.artifacts_dir / "intel"


def load_text_model(settings: Settings) -> TextModel | None:
    """The trained classifier, or None when there is none that fits this code.

    The model's cue columns are those of the cue file it was trained with. If the
    file has changed since, the model is not used until it is trained again.
    """
    directory = intel_dir(settings)
    try:
        manifest = json.loads((directory / MANIFEST_FILE).read_text())
    except (OSError, ValueError):
        log.warning("no scam-message model in %s: run `python -m fraudlens.intel.train`", directory)
        return None
    if manifest.get("cues") != [cue.id for cue in load_cues()]:
        log.warning("the scam-message model predates the cue file: train it again")
        return None
    return TextModel.load(directory)
