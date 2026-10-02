"""Train the scam-message classifier and measure it.

    python -m fraudlens.intel.train

Writes the model and `report.json` under `artifacts/intel/`. Two test sets are
reported and they mean different things:

- `test`: scripts the model trained on, in wordings it never saw.
- `unseen`: whole scam scripts (and harmless topics) kept out of training.

Both are generated from templates written for this project. Neither is a
measurement on messages real customers received.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from ..config import Settings
from .analyze import check_message
from .corpus import Sample, generate, load_families
from .taxonomy import load_taxonomy
from .text import SCAM, TextModel, build_pipeline, intel_dir, normalise

VERSION = "v1"
REPORT_FILE = "report.json"
# The share of harmless messages each level may flag, set on the validation split.
CAUTION_FPR = 0.03
HIGH_FPR = 0.005
CATEGORY_THRESHOLD = 0.5

LIMITS = [
    "The corpus is synthetic: templates written for this project and filled with "
    "generated names, amounts and numbers. No real customer message was used, so "
    "these numbers say how the model does on that corpus, not in the field.",
    "A forged cash-in SMS reads the same as a real one. The model flags the request "
    "that follows it (send the money back); whether money arrived is the ledger check.",
    "The cue patterns were written by hand, by the author of the corpus, with the "
    "trained scripts in view. They were not tuned against the held-out scripts, but "
    "the same person wrote both, so real messages will match them less often.",
    "It reads text only: no screenshots, no voice calls, no images of QR codes.",
    "Scripts that share no wording with the trained ones are caught less often; "
    "the `unseen` numbers are the estimate of that.",
]


def _targets(samples: list[Sample], outputs: list[str]) -> np.ndarray:
    rows = [[bool(s.labels), *(c in s.labels for c in outputs[1:])] for s in samples]
    return np.array(rows, dtype=int)


def _threshold(benign_scores: np.ndarray, fpr: float) -> float:
    """The lowest score that flags at most `fpr` of the harmless messages."""
    return float(np.quantile(benign_scores, 1 - fpr, method="higher")) + 1e-6


def _rate(flags: np.ndarray) -> float | None:
    return round(float(flags.mean()), 4) if len(flags) else None


def _split_report(
    model: TextModel, samples: list[Sample], scores: np.ndarray, per_family: bool
) -> dict:
    y = _targets(samples, model.outputs)
    scam, harmless = y[:, 0] == 1, y[:, 0] == 0
    hard = np.array([s.hard for s in samples]) & harmless
    out: dict = {
        "messages": len(samples),
        "scam": int(scam.sum()),
        "harmless": int(harmless.sum()),
        "auc": round(float(roc_auc_score(y[:, 0], scores[:, 0])), 4),
        "average_precision": round(float(average_precision_score(y[:, 0], scores[:, 0])), 4),
    }
    for name in ("caution", "high"):
        flagged = scores[:, 0] >= model.thresholds[name]
        out[name] = {
            "recall": _rate(flagged[scam]),
            "false_positive_rate": _rate(flagged[harmless]),
            "false_positive_rate_hard": _rate(flagged[hard]),
        }

    # Categories are named only for a message already flagged, so they are measured that way.
    flagged = scores[:, 0] >= model.thresholds["caution"]
    categories = {}
    for j, cid in enumerate(model.outputs[1:], start=1):
        said = flagged & (scores[:, j] >= model.thresholds["category"])
        true = y[:, j] == 1
        hit = int((said & true).sum())
        categories[cid] = {
            "support": int(true.sum()),
            "precision": round(hit / said.sum(), 4) if said.sum() else None,
            "recall": round(hit / true.sum(), 4) if true.sum() else None,
        }
    out["categories"] = categories

    # What a customer is actually told: the classifier and the link check together.
    taxonomy = load_taxonomy()
    told = np.array([check_message(s.text, model, taxonomy)["level"] != "none" for s in samples])
    out["with_link_check"] = {
        "recall": _rate(told[scam]),
        "false_positive_rate": _rate(told[harmless]),
    }

    langs = np.array([s.lang for s in samples])
    out["languages"] = {
        lang: {
            "messages": int((langs == lang).sum()),
            "recall": _rate(flagged[scam & (langs == lang)]),
            "false_positive_rate": _rate(flagged[harmless & (langs == lang)]),
        }
        for lang in sorted(set(langs))
    }
    if per_family:
        families = np.array([s.family for s in samples])
        out["families"] = {
            fam: {
                "scam": bool(y[families == fam][0, 0]),
                "messages": int((families == fam).sum()),
                "flagged": _rate(flagged[families == fam]),
            }
            for fam in sorted(set(families))
        }
    return out


def train(directory: Path, seed: int = 7, per_template: int = 30) -> dict:
    taxonomy = load_taxonomy()
    samples = generate(seed=seed, per_template=per_template)
    by_split: dict[str, list[Sample]] = {}
    for s in samples:
        by_split.setdefault(s.split, []).append(s)

    outputs = [SCAM, *taxonomy.ids]
    fit = by_split["train"]
    pipeline = build_pipeline(seed)
    pipeline.fit([normalise(s.text) for s in fit], _targets(fit, outputs))
    model = TextModel(VERSION, pipeline, outputs, {"category": CATEGORY_THRESHOLD})

    val = by_split["val"]
    val_scores = model.score([s.text for s in val])
    benign = val_scores[[not s.labels for s in val], 0]
    model.thresholds["caution"] = round(_threshold(benign, CAUTION_FPR), 6)
    model.thresholds["high"] = round(
        max(_threshold(benign, HIGH_FPR), model.thresholds["caution"]), 6
    )

    families = load_families()
    report = {
        "version": VERSION,
        "taxonomy_version": taxonomy.version,
        "seed": seed,
        "model": "one-vs-rest logistic regression on character n-grams (2-5) and cues",
        "thresholds": model.thresholds,
        "threshold_targets": {"caution_fpr": CAUTION_FPR, "high_fpr": HIGH_FPR, "set_on": "val"},
        "corpus": {
            "families": len(families),
            "scam_families": sum(bool(f.labels) for f in families),
            "held_out_families": sum(f.held_out for f in families),
            "templates": sum(len(f.templates) for f in families),
            "messages": dict(Counter(s.split for s in samples)),
            "languages": dict(Counter(s.lang for s in samples)),
        },
        "test": _split_report(model, by_split["test"], model.score(
            [s.text for s in by_split["test"]]), per_family=False),
        "unseen": _split_report(model, by_split["unseen"], model.score(
            [s.text for s in by_split["unseen"]]), per_family=True),
        "limits": LIMITS,
    }  # fmt: skip
    model.save(directory, taxonomy_version=taxonomy.version, seed=seed)
    (directory / REPORT_FILE).write_text(json.dumps(report, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the scam-message classifier")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    report = train(intel_dir(Settings()), seed=args.seed)
    for split in ("test", "unseen"):
        r = report[split]
        print(
            f"{split:7} auc {r['auc']:.3f}  "
            f"caution: recall {r['caution']['recall']:.3f} fpr "
            f"{r['caution']['false_positive_rate']:.3f} "
            f"(hard {r['caution']['false_positive_rate_hard']})  "
            f"high: recall {r['high']['recall']:.3f} fpr {r['high']['false_positive_rate']:.3f}"
        )
    print(f"written to {intel_dir(Settings())}")


if __name__ == "__main__":
    main()
