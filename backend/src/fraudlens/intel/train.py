"""Train the scam-message classifier and measure it.

    python -m fraudlens.intel.train

Writes the model and `report.json` under `artifacts/intel/`. Three test sets are
reported and they mean different things:

- `test`: scripts the model trained on, in wordings it never saw.
- `unseen`: whole scam scripts (and harmless topics) kept out of training.
- `external`: the test split of a public, human-labelled Bangla/Banglish/English
  smishing corpus (`external.py`), never trained on.

`test` and `unseen` are generated from templates written for this project.
`external` is the one set here that the author of the corpus did not write. The
public corpus's training split is trained on (for the scam-or-not output only)
when it can be fetched; offline, the model trains on the written corpus alone and
the report says so. None of the three is a measurement on messages real customers
of this service received.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score, f1_score, roc_auc_score

from ..config import Settings
from . import external
from .analyze import check_message
from .corpus import TYPOLOGIES, Sample, generate, load_families
from .taxonomy import load_taxonomy
from .text import SCAM, TextModel, build_pipeline, intel_dir, normalise

VERSION = "v2"
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
    "The public corpus (`external`) has its own label noise, and about 4% of its test "
    "texts repeat a training text once normalised; its numbers hold for messages like "
    "it, not for every scam a customer will receive.",
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


def _macro_f1(y: np.ndarray, flagged: np.ndarray) -> float | None:
    """Binary macro-F1 (the mean of the scam and the harmless F1), when both occur."""
    if len(set(y.tolist())) < 2:
        return None
    return round(float(f1_score(y, flagged.astype(int), average="macro")), 4)


def _caution_high(scores: np.ndarray, benign: np.ndarray, source: np.ndarray) -> dict:
    """Thresholds that flag at most CAUTION_FPR / HIGH_FPR of each source's harmless
    validation messages. The public harmless messages are mostly easy (promotions,
    chat); pooled with ours they would let the threshold drift onto our hard ones."""
    out = {}
    for name, fpr in (("caution", CAUTION_FPR), ("high", HIGH_FPR)):
        out[name] = max(
            _threshold(scores[benign & (source == src)], fpr)
            for src in sorted(set(source[benign].tolist()))
        )
    out["high"] = max(out["high"], out["caution"])
    return {k: round(v, 6) for k, v in out.items()}


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
            "macro_f1": _macro_f1(y[:, 0], flagged),
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
            "macro_f1": _macro_f1(y[langs == lang, 0], flagged[langs == lang]),
        }
        for lang in sorted(set(langs))
    }
    # Each typology's scams against every harmless message of the same split.
    typ = np.array([s.typology or "" for s in samples])
    out["typologies"] = {
        t: {
            "scam": int((typ == t).sum()),
            "recall": _rate(flagged[typ == t]),
            "macro_f1": _macro_f1(y[(typ == t) | harmless, 0], flagged[(typ == t) | harmless]),
        }
        for t in TYPOLOGIES
        if (typ == t).any()
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


def _external_report(model: TextModel, items: list[external.External], scores: np.ndarray) -> dict:
    y = np.array([e.scam for e in items], dtype=int)
    langs = np.array([e.lang for e in items])
    out: dict = {
        "messages": len(items),
        "scam": int(y.sum()),
        "harmless": int(len(y) - y.sum()),
        "auc": round(float(roc_auc_score(y, scores)), 4),
        "average_precision": round(float(average_precision_score(y, scores)), 4),
    }
    for name in ("caution", "high"):
        flagged = scores >= model.thresholds[name]
        out[name] = {
            "recall": _rate(flagged[y == 1]),
            "false_positive_rate": _rate(flagged[y == 0]),
            "macro_f1": _macro_f1(y, flagged),
        }
    flagged = scores >= model.thresholds["caution"]
    out["languages"] = {
        lang: {
            "messages": int((langs == lang).sum()),
            "recall": _rate(flagged[(y == 1) & (langs == lang)]),
            "false_positive_rate": _rate(flagged[(y == 0) & (langs == lang)]),
            "macro_f1": _macro_f1(y[langs == lang], flagged[langs == lang]),
        }
        for lang in sorted(set(langs.tolist()))
    }
    return out


def train(
    directory: Path, seed: int = 7, per_template: int = 30, settings: Settings | None = None
) -> dict:
    """`settings` lets it fetch the public corpus; without it, the written corpus only."""
    taxonomy = load_taxonomy()
    public = external.ensure(settings) if settings is not None else None
    samples = generate(seed=seed, per_template=per_template)
    by_split: dict[str, list[Sample]] = {}
    for s in samples:
        by_split.setdefault(s.split, []).append(s)

    outputs = [SCAM, *taxonomy.ids]
    fit = by_split["train"]
    texts = [s.text for s in fit]
    targets = _targets(fit, outputs)
    if public:
        # Public messages say scam or not, never which kind: -1 leaves them out of
        # the category outputs (`text.MultiHead`).
        texts += [e.text for e in public["train"]]
        extra = np.full((len(public["train"]), len(outputs)), -1, dtype=int)
        extra[:, 0] = [e.scam for e in public["train"]]
        targets = np.vstack([targets, extra])
    pipeline = build_pipeline(seed)
    pipeline.fit([normalise(t) for t in texts], targets)
    model = TextModel(VERSION, pipeline, outputs, {"category": CATEGORY_THRESHOLD})

    val = by_split["val"]
    val_texts = [s.text for s in val]
    benign = [not s.labels for s in val]
    source = ["corpus"] * len(val)
    if public:
        val_texts += [e.text for e in public["val"]]
        benign += [not e.scam for e in public["val"]]
        source += ["external"] * len(public["val"])
    val_scores = model.score(val_texts)[:, 0]
    model.thresholds.update(_caution_high(val_scores, np.array(benign), np.array(source)))

    families = load_families()
    report = {
        "version": VERSION,
        "taxonomy_version": taxonomy.version,
        "seed": seed,
        "model": "one logistic regression per output on character n-grams (2-5) and cues",
        "thresholds": model.thresholds,
        "threshold_targets": {
            "caution_fpr": CAUTION_FPR, "high_fpr": HIGH_FPR,
            "set_on": "val: each source's harmless messages, the stricter threshold",
        },
        "corpus": {
            "families": len(families),
            "scam_families": sum(bool(f.labels) for f in families),
            "held_out_families": sum(f.held_out for f in families),
            "templates": sum(len(f.templates) for f in families),
            "messages": dict(Counter(s.split for s in samples)),
            "languages": dict(Counter(s.lang for s in samples)),
        },
        "public_corpus": external.provenance() | {
            "messages": {split: len(items) for split, items in public.items()},
            "used_for": "train: scam output only; val: thresholds; test: reported only",
        } if public else None,
        "test": _split_report(model, by_split["test"], model.score(
            [s.text for s in by_split["test"]]), per_family=False),
        "unseen": _split_report(model, by_split["unseen"], model.score(
            [s.text for s in by_split["unseen"]]), per_family=True),
        "limits": LIMITS,
    }  # fmt: skip
    if public:
        test = public["test"]
        report["external"] = _external_report(
            model, test, model.score([e.text for e in test])[:, 0]
        )
    model.save(directory, taxonomy_version=taxonomy.version, seed=seed)
    (directory / REPORT_FILE).write_text(json.dumps(report, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the scam-message classifier")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    settings = Settings()
    report = train(intel_dir(settings), seed=args.seed, settings=settings)
    if report["public_corpus"] is None:
        print("public corpus not available: trained on the written corpus only")
    for split in ("test", "unseen", "external"):
        if split not in report:
            continue
        r = report[split]
        print(
            f"{split:8} auc {r['auc']:.3f}  "
            f"caution: recall {r['caution']['recall']:.3f} fpr "
            f"{r['caution']['false_positive_rate']:.3f} "
            f"macro-f1 {r['caution']['macro_f1']:.3f}  "
            f"high: recall {r['high']['recall']:.3f} fpr {r['high']['false_positive_rate']:.3f}"
        )
    print(f"written to {intel_dir(settings)}")


if __name__ == "__main__":
    main()
