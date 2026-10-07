"""Which model reads scam messages best: the evidence behind the production choice.

    python -m fraudlens.intel.compare            # or: make intel-compare

Trains each candidate for the scam-or-not output on the same rows, sets each one's
caution threshold the same way (at most 3% of each source's harmless validation
messages flagged),
and measures it on three sets it never trained on:

- `test`: corpus scripts it trained on, in wordings it never saw.
- `unseen`: corpus scripts (one per typology, and harmless topics) kept out of training.
- `external`: the public corpus's own test split (`external.py`), written by people
  who are not the author of this one.

Candidates are the cues alone, word TF-IDF, character n-gram TF-IDF, character
n-grams with cues (the v1 recipe), and a small multilingual sentence encoder
(paraphrase-multilingual-MiniLM-L12-v2) with logistic regression on top, each
trained on the corpus alone and on the corpus plus the public training split. The
encoder runs only when the optional `transformer` extra is installed
(`uv sync --extra transformer`); without it, it is reported as skipped.

Writes `compare.json` next to the model. It does not change the served model.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, roc_auc_score
from sklearn.pipeline import FeatureUnion, Pipeline

from ..config import Settings
from . import external
from .corpus import TYPOLOGIES, generate
from .cues import CueFeatures
from .text import intel_dir, normalise

COMPARE_FILE = "compare.json"
CAUTION_FPR = 0.03
ENCODER = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
LATENCY_MESSAGES = 200


@dataclass(frozen=True)
class Row:
    text: str
    scam: bool
    lang: str
    typology: str | None  # corpus scams only
    source: str  # corpus | external


def rows(seed: int, per_template: int, settings: Settings) -> dict[str, list[Row]]:
    """Every split, corpus and public. Public rows are absent when they cannot be fetched."""
    out: dict[str, list[Row]] = {}
    for s in generate(seed=seed, per_template=per_template):
        row = Row(s.text, bool(s.labels), s.lang, s.typology, "corpus")
        out.setdefault(s.split, []).append(row)
    public = external.ensure(settings)
    for split, items in (public or {}).items():
        out[f"external_{split}"] = [Row(e.text, e.scam, e.lang, None, "external") for e in items]
    return out


# ------------------------------------------------------------------ candidates


def _lr(seed: int) -> LogisticRegression:
    return LogisticRegression(C=2.0, max_iter=3000, class_weight="balanced", random_state=seed)


def _chars() -> TfidfVectorizer:
    return TfidfVectorizer(
        analyzer="char_wb", ngram_range=(2, 5), min_df=2, sublinear_tf=True,
        max_features=60_000, lowercase=False,
    )  # fmt: skip


def _sparse(features, seed: int) -> Callable:
    def fit(texts: list[str], y: np.ndarray) -> Callable[[list[str]], np.ndarray]:
        pipe = Pipeline([("features", features()), ("model", _lr(seed))])
        pipe.fit([normalise(t) for t in texts], y)
        return lambda batch: pipe.predict_proba([normalise(t) for t in batch])[:, 1]

    return fit


def _encoder_fit(seed: int):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None
    encoder = SentenceTransformer(ENCODER, device="cpu")

    def encode(batch: list[str]) -> np.ndarray:
        return encoder.encode(
            [t[:2000] for t in batch], batch_size=64, normalize_embeddings=True,
            show_progress_bar=False,
        )  # fmt: skip

    cache: dict[str, np.ndarray] = {}

    def embed(batch: list[str]) -> np.ndarray:
        todo = [t for t in dict.fromkeys(batch) if t not in cache]
        if todo:
            cache.update(zip(todo, encode(todo), strict=True))
        return np.stack([cache[t] for t in batch])

    def fit(texts: list[str], y: np.ndarray):
        model = _lr(seed).fit(embed(texts), y)
        scorer = lambda batch: model.predict_proba(embed(batch))[:, 1]  # noqa: E731
        # Latency is measured without the cache: a new message is encoded from scratch.
        scorer.uncached = lambda batch: model.predict_proba(encode(batch))[:, 1]
        return scorer

    return fit


def candidates(seed: int) -> dict[str, Callable | None]:
    def cues():
        return CueFeatures()

    def words():
        return TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, lowercase=False)

    def chars_cues():
        return FeatureUnion(
            [("chars", _chars()), ("cues", CueFeatures())],
            transformer_weights={"chars": 1.0, "cues": 0.5},
        )

    return {
        "cues_only": _sparse(cues, seed),
        "word_tfidf": _sparse(words, seed),
        "char_tfidf": _sparse(_chars, seed),
        "char_cues": _sparse(chars_cues, seed),
        "minilm_lr": _encoder_fit(seed),
    }


# --------------------------------------------------------------------- metrics


def _macro_f1(y: np.ndarray, flagged: np.ndarray) -> float | None:
    if len(y) == 0 or len(set(y)) < 2:
        return None
    return round(float(f1_score(y, flagged, average="macro")), 4)


def measure(items: list[Row], scores: np.ndarray, threshold: float) -> dict:
    y = np.array([r.scam for r in items], dtype=int)
    flagged = (scores >= threshold).astype(int)
    langs = np.array([r.lang for r in items])
    out = {
        "messages": len(items),
        "scam": int(y.sum()),
        "auc": round(float(roc_auc_score(y, scores)), 4),
        "macro_f1": _macro_f1(y, flagged),
        "recall": round(float(flagged[y == 1].mean()), 4),
        "false_positive_rate": round(float(flagged[y == 0].mean()), 4),
        "languages": {
            lang: _macro_f1(y[langs == lang], flagged[langs == lang]) for lang in sorted(set(langs))
        },
    }
    typ = np.array([r.typology or "" for r in items])
    if typ.any():
        # Each typology's scams against every harmless message of the same split.
        out["typologies"] = {}
        for t in TYPOLOGIES:
            keep = (typ == t) | (y == 0)
            if (typ == t).any():
                out["typologies"][t] = {
                    "scam": int((typ == t).sum()),
                    "macro_f1": _macro_f1(y[keep], flagged[keep]),
                    "recall": round(float(flagged[typ == t].mean()), 4),
                }
    return out


def caution_threshold(val: list[Row], scores: np.ndarray, fpr: float = CAUTION_FPR) -> float:
    """The lowest score that flags at most `fpr` of the harmless validation messages
    of each source. The public harmless messages are mostly easy (promotions, chat);
    pooled with ours they would let the threshold drift onto our hard ones."""
    out = 0.0
    for source in {r.source for r in val}:
        benign = scores[[r.source == source and not r.scam for r in val]]
        if len(benign):
            out = max(out, float(np.quantile(benign, 1 - fpr, method="higher")) + 1e-6)
    return out


def latency(score: Callable, texts: list[str]) -> dict:
    """Milliseconds to score one message at a time on this machine's CPU."""
    score(texts[:5])  # warm up
    times = []
    for t in texts:
        start = time.perf_counter()
        score([t])
        times.append((time.perf_counter() - start) * 1000)
    return {
        "p50_ms": round(float(np.percentile(times, 50)), 2),
        "p95_ms": round(float(np.percentile(times, 95)), 2),
    }


# ------------------------------------------------------------------------ main


def compare(directory: Path, settings: Settings, seed: int = 7, per_template: int = 30) -> dict:
    data = rows(seed, per_template, settings)
    has_public = "external_train" in data
    val = data["val"] + data.get("external_val", [])
    evals = {"test": data["test"], "unseen": data["unseen"]}
    if has_public:
        evals["external"] = data["external_test"]
    train_sets = {"corpus": data["train"]}
    if has_public:
        train_sets["corpus+external"] = data["train"] + data["external_train"]

    rng = np.random.default_rng(seed)
    probe = [r.text for r in rng.permutation(np.array(val, dtype=object))[:LATENCY_MESSAGES]]

    results: dict = {}
    for name, fit in candidates(seed).items():
        if fit is None:
            results[name] = {"skipped": "install the optional extra: uv sync --extra transformer"}
            continue
        for train_name, train_rows in train_sets.items():
            key = f"{name}+ext" if train_name != "corpus" else name
            start = time.perf_counter()
            score = fit([r.text for r in train_rows], np.array([r.scam for r in train_rows]))
            fit_s = time.perf_counter() - start
            val_scores = score([r.text for r in val])
            threshold = caution_threshold(val, val_scores)
            entry = {
                "trained_on": train_name,
                "train_messages": len(train_rows),
                "fit_seconds": round(fit_s, 1),
                "caution_threshold": round(threshold, 6),
                "latency": latency(getattr(score, "uncached", score), probe),
            }
            for split, items in evals.items():
                entry[split] = measure(items, score([r.text for r in items]), threshold)
            results[key] = entry
            print(_line(key, entry), flush=True)

    out = {
        "seed": seed,
        "threshold": {
            "caution_fpr": CAUTION_FPR,
            "set_on": "harmless val messages of each source; the stricter of the two",
        },
        "encoder": ENCODER,
        "public_corpus": external.provenance() if has_public else None,
        "sets": {k: len(v) for k, v in data.items()},
        "candidates": results,
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / COMPARE_FILE).write_text(json.dumps(out, indent=2))
    return out


def _line(key: str, e: dict) -> str:
    parts = [f"{key:16}"]
    for split in ("test", "unseen", "external"):
        if split in e:
            parts.append(f"{split} auc {e[split]['auc']:.3f} f1 {e[split]['macro_f1']:.3f}")
    parts.append(f"p50 {e['latency']['p50_ms']}ms")
    return "  ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare scam-message models")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    settings = Settings()
    compare(intel_dir(settings), settings, seed=args.seed)
    print(f"written to {intel_dir(settings) / COMPARE_FILE}")


if __name__ == "__main__":
    main()
