"""Segment thresholds for young wallets: fitted with a stated objective, measured on test.

The fairness report (insights.py) shows that honest payments involving a wallet under
30 days old are interrupted far more often than others. A policy can give such
payments their own cut-offs (`segments:` in the policy file). This module chooses
those cut-offs and measures what they cost.

- Fitted on the validation folds (val_a + val_b), never on test.
- Objective: make the larger of the two young-wallet false-alert ratios (sender under
  30 days, receiving wallet under 30 days, each against honest payments that involve
  no young wallet, a rate the segments cannot move) as small as possible, subject to
  losing at most MAX_RECALL_LOSS of victim transfers alerted, the same of victim
  money alerted, and MAX_HOLD_RECALL_LOSS of victim transfers held. Ratios within
  GAP_TOLERANCE count as equal; ties go to the smaller sum of the two ratios, then
  the smaller false-hold ratio, then the smaller change. The report also gives the
  ratios against the overall rate, as the fairness report does.
- Only candidates the policy guard admits can be chosen: a segment's warn cut-off may
  not exceed the model's hold cut-off. Inadmissible candidates are still measured, so
  the frontier shows what closing the gap further would cost.
- Evaluated on the test period by running the base policy and the target policy
  through the real decision code, with 95% intervals from a bootstrap that resamples
  sending customers (all of a customer's payments together).

    uv run python -m fraudlens.decision.mitigation            # fit, then evaluate policy v3
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import settings
from ..models import registry
from ..models.data import load_frame
from ..models.metrics import alert_outcomes
from .evaluate import add_context, decide_batch
from .insights import _group_table
from .policy import _OPS, POLICY_DIR, RANK, Condition, Policy, Segment, load_policy

MITIGATION_FILE = "fairness_mitigation.json"
TARGET_POLICY = "v3"
FIT_FOLDS = ("val_a", "val_b")
YOUNG_DAYS = 30.0
# Warn cut-off as a multiple of the model's. The largest admissible one is the model's
# hold / warn ratio (25.1 for model v4); the larger ones only draw the frontier.
WARN_SCALES = (1, 1.5, 2, 3, 4, 6, 8, 12, 16, 20, 25, 40, 60, 100, 150, 250)
HOLD_SCALES = (1, 1.5, 2, 3, 4)
MAX_RECALL_LOSS = 0.005  # 0.5 percentage points
MAX_HOLD_RECALL_LOSS = 0.01  # 1 percentage point
GAP_TOLERANCE = 0.1
BOOTSTRAP = 1000
SEED = 7

# --------------------------------------------------------------------- measures


def _columns(rows: pd.DataFrame, rank: np.ndarray) -> dict[str, np.ndarray]:
    """Per-row indicators whose sums give every rate reported here."""
    legit = rows["y"].to_numpy() == 0
    victim = rows["y_loss"].to_numpy() == 1
    send = (rows["type"] == "SEND_MONEY").to_numpy()
    young_sender = rows["s_age_days"].to_numpy(dtype=np.float64) < YOUNG_DAYS
    young_receiver = send & (rows["r_age_days"].to_numpy(dtype=np.float64) < YOUNG_DAYS)
    taka = np.where(victim, rows["amount"].to_numpy(dtype=np.float64), 0.0)
    alert, hold = rank > 0, rank == RANK["hold"]
    groups = {
        "": legit,
        "_send": legit & send,
        "_ys": legit & young_sender,
        "_yr": legit & young_receiver,
        "_ref": legit & ~young_sender & ~young_receiver,  # no young wallet: no segment applies
    }
    cols = {"rows": np.ones(len(rows)), "alert": alert, "hold": hold}
    for suffix, mask in groups.items():
        cols["legit" + suffix] = mask
        cols["fa" + suffix] = mask & alert
        cols["fh" + suffix] = mask & hold
    cols |= {"victim": victim, "va": victim & alert, "vh": victim & hold}
    cols |= {"taka": taka, "ta": taka * alert, "th": taka * hold}
    return {name: np.asarray(values, dtype=np.float64) for name, values in cols.items()}


def _metrics(s: dict) -> dict:
    """Rates from summed indicators. Works on numbers or on arrays of them."""

    def div(a, b):
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.divide(a, b)

    far, far_send = div(s["fa"], s["legit"]), div(s["fa_send"], s["legit_send"])
    fhr, fhr_send = div(s["fh"], s["legit"]), div(s["fh_send"], s["legit_send"])
    out = {
        "false_alert_rate": far,
        "false_hold_rate": fhr,
        "sender_young_false_alert_rate": div(s["fa_ys"], s["legit_ys"]),
        "sender_young_false_hold_rate": div(s["fh_ys"], s["legit_ys"]),
        "receiver_young_false_alert_rate": div(s["fa_yr"], s["legit_yr"]),
        "receiver_young_false_hold_rate": div(s["fh_yr"], s["legit_yr"]),
        "victim_recall": div(s["va"], s["victim"]),
        "victim_recall_hold": div(s["vh"], s["victim"]),
        "taka_recall": div(s["ta"], s["taka"]),
        "taka_recall_hold": div(s["th"], s["taka"]),
        "alert_rate": div(s["alert"], s["rows"]),
        "hold_rate": div(s["hold"], s["rows"]),
    }
    # Same definitions as the fairness report: the sender against everyone, the
    # receiving wallet against all transfers.
    out["sender_young_ratio"] = div(out["sender_young_false_alert_rate"], far)
    out["receiver_young_ratio"] = div(out["receiver_young_false_alert_rate"], far_send)
    out["largest_young_ratio"] = np.maximum(out["sender_young_ratio"], out["receiver_young_ratio"])
    # The fit's yardstick: against payments with no young wallet, whose rate the
    # segments cannot move. (Against the overall rate, interrupting young wallets less
    # lowers the overall rate too, so a fix for one group raises the other's ratio.)
    ref = div(s["fa_ref"], s["legit_ref"])
    out["reference_false_alert_rate"] = ref
    out["sender_young_ratio_ref"] = div(out["sender_young_false_alert_rate"], ref)
    out["receiver_young_ratio_ref"] = div(out["receiver_young_false_alert_rate"], ref)
    out["largest_young_ratio_ref"] = np.maximum(
        out["sender_young_ratio_ref"], out["receiver_young_ratio_ref"]
    )
    out["summed_young_ratio_ref"] = out["sender_young_ratio_ref"] + out["receiver_young_ratio_ref"]
    out["sender_young_hold_ratio"] = div(out["sender_young_false_hold_rate"], fhr)
    out["receiver_young_hold_ratio"] = div(out["receiver_young_false_hold_rate"], fhr_send)
    out["largest_young_hold_ratio"] = np.maximum(
        out["sender_young_hold_ratio"], out["receiver_young_hold_ratio"]
    )
    return out


def _sums(cols: dict[str, np.ndarray], mask: np.ndarray | None = None) -> dict[str, float]:
    return {k: float(v.sum() if mask is None else v[mask].sum()) for k, v in cols.items()}


# ------------------------------------------------------------------- the grid


def _mask(conditions: tuple[Condition, ...], rows: pd.DataFrame) -> np.ndarray:
    """Vectorised `evaluate(...) == 'fired'`: a missing value never matches."""
    hit = np.ones(len(rows), dtype=bool)
    for c in conditions:
        values = rows[c.field].to_numpy(dtype=np.float64)
        hit &= ~np.isnan(values) & _OPS[c.op](values, c.value)
    return hit


def segment_masks(segments: tuple[Segment, ...], rows: pd.DataFrame) -> list[np.ndarray]:
    """Which rows each segment decides; the first segment that matches wins."""
    taken = np.zeros(len(rows), dtype=bool)
    masks = []
    for segment in segments:
        mask = rows["type"].isin(segment.applies_to).to_numpy() & _mask(segment.when, rows) & ~taken
        masks.append(mask)
        taken |= mask
    return masks


def scales(base: dict[str, float], warn: float, hold: float) -> dict[str, float]:
    """Full scale for a segment: step-up sits between the new warn and hold cut-offs."""
    step_up = min(max(base["step_up"], base["warn"] * warn), base["hold"] * hold)
    # Four decimals, so the policy file can carry the exact number; rounded towards
    # the middle so the cut-offs stay in order.
    exact = step_up / base["step_up"]
    rounded = math.ceil(exact * 1e4) / 1e4
    if base["step_up"] * rounded > base["hold"] * hold:
        rounded = math.floor(exact * 1e4) / 1e4
    return {"warn": float(warn), "step_up": rounded, "hold": float(hold)}


def admissible(base: dict[str, float], scale: dict[str, float]) -> bool:
    """The policy guard (Segment.thresholds), without raising."""
    t = {tier: base[tier] * scale[tier] for tier in scale}
    return 0 < t["warn"] <= t["step_up"] <= t["hold"] < 1 and t["warn"] <= base["hold"]


def segment_thresholds(base: dict[str, float], scale: dict[str, float]) -> dict[str, float]:
    """The cut-offs the engine would use; beyond the guard, the plain scaled ones, to
    show what removing the guard would do."""
    if admissible(base, scale):
        return Segment.model_construct(id="FIT", scale=scale).thresholds(base)
    return {tier: base[tier] * value for tier, value in scale.items()}


def model_rank(risk: np.ndarray, thresholds: dict[str, float]) -> np.ndarray:
    return np.select(
        [risk >= thresholds["hold"], risk >= thresholds["step_up"], risk >= thresholds["warn"]],
        [RANK["hold"], RANK["step_up"], RANK["warn"]],
        RANK["allow"],
    )


def rule_floor(policy: Policy, outcomes: list) -> np.ndarray:
    """The tier the rules lift each row to; with no cap rule, final = max(model, floor)."""
    if any(rule.effect == "cap_at" for rule in policy.rules):
        raise NotImplementedError("fitting assumes the policy has no cap rule")
    return np.array(
        [
            max((RANK[r.tier] for r in o.fired if r.effect == "raise_to"), default=0)
            for o in outcomes
        ]
    )


def grid(
    rows: pd.DataFrame,
    risk: np.ndarray,
    floor: np.ndarray,
    base: dict[str, float],
    masks: list[np.ndarray],
) -> tuple[list[tuple[float, float]], dict[str, np.ndarray]]:
    """Every combination of (warn, hold) scale per segment, summed indicators per combination.

    Segments are disjoint, so a combination's sums are those of the rows no segment
    takes plus each segment's sums under its own scale.
    """
    options = list(itertools.product(WARN_SCALES, HOLD_SCALES))
    rest = ~np.any(masks, axis=0)
    total = _sums(_columns(rows[rest], np.maximum(model_rank(risk[rest], base), floor[rest])))
    per_segment = []
    for mask in masks:
        seg_rows, seg_risk, seg_floor = rows[mask], risk[mask], floor[mask]
        sums = []
        for warn, hold in options:
            t = segment_thresholds(base, scales(base, warn, hold))
            rank = np.maximum(model_rank(seg_risk, t), seg_floor)
            sums.append(_sums(_columns(seg_rows, rank)))
        per_segment.append(sums)
    # One axis per segment: sums[name] has shape (len(options),) * n_segments.
    combined = {}
    for name in total:
        acc = np.asarray(total[name])
        for i, sums in enumerate(per_segment):
            shape = [1] * len(masks)
            shape[i] = len(options)
            acc = acc + np.array([s[name] for s in sums]).reshape(shape)
        combined[name] = acc
    return options, combined


def _pareto(gap: np.ndarray, recall: np.ndarray) -> np.ndarray:
    """Points no other point beats on both: smaller gap and larger recall."""
    order = np.lexsort((-recall, gap))
    keep, best = np.zeros(len(gap), dtype=bool), -np.inf
    for i in order:
        if recall[i] > best + 1e-12:
            keep[i], best = True, recall[i]
    return keep


def fit(
    fit_metrics: dict,
    test_metrics: dict,
    options: list[tuple[float, float]],
    base: dict[str, float],
    segment_ids: list[str],
) -> dict:
    """Choose one (warn, hold) scale per segment; also the frontiers it was chosen from."""
    shape = fit_metrics["largest_young_ratio"].shape
    index = list(np.ndindex(*shape))
    ok = np.array([all(admissible(base, scales(base, *options[i])) for i in ix) for ix in index])
    m = {k: np.asarray(v).reshape(-1) for k, v in fit_metrics.items()}
    t = {k: np.asarray(v).reshape(-1) for k, v in test_metrics.items()}
    # The base policy is the combination with every scale at 1.
    origin = index.index(tuple(options.index((1, 1)) for _ in segment_ids))

    def kept(key: str, loss: float) -> np.ndarray:
        # A rate with nothing to measure (no victims in the rows) cannot be lost.
        with np.errstate(invalid="ignore"):
            return ~(m[key] < m[key][origin] - loss - 1e-12)

    meets = (
        ok
        & kept("victim_recall", MAX_RECALL_LOSS)
        & kept("taka_recall", MAX_RECALL_LOSS)
        & kept("victim_recall_hold", MAX_HOLD_RECALL_LOSS)
    )
    change = np.array([sum(options[i][0] + options[i][1] for i in ix) for ix in index])
    candidates = np.flatnonzero(meets)  # never empty: the base policy meets every constraint
    gap = np.nan_to_num(m["largest_young_ratio_ref"], nan=np.inf)
    total = np.nan_to_num(m["summed_young_ratio_ref"], nan=np.inf)
    near = candidates[gap[candidates] <= gap[candidates].min() + GAP_TOLERANCE]
    near = near[total[near] <= total[near].min() + GAP_TOLERANCE]
    chosen = min(near, key=lambda i: (round(float(m["largest_young_hold_ratio"][i]), 2), change[i]))

    def point(i: int) -> dict:
        return {
            "scales": {
                sid: dict(zip(("warn", "hold"), options[ix], strict=True))
                for sid, ix in zip(segment_ids, index[i], strict=True)
            },
            "admissible": bool(ok[i]),
            "meets_constraints": bool(meets[i]),
            "chosen": bool(i == chosen),
            "fit": {k: _r(m[k][i]) for k in _FRONTIER_KEYS},
            "test": {k: _r(t[k][i]) for k in _FRONTIER_KEYS},
        }

    # Alert frontier: warn scales vary, hold scales as chosen. Hold frontier: the reverse.
    def path(vary: int) -> list[dict]:
        """Every segment given the same warn (vary=0) or hold (vary=1) scale, in turn, with
        the other scale as chosen: one line from the base policy to beyond the guard."""
        fixed = index[chosen]
        picked = []
        for value in sorted({option[vary] for option in options}):
            want = tuple(
                options.index((value, options[f][1]) if vary == 0 else (options[f][0], value))
                for f in fixed
            )
            picked.append(index.index(want))
        return [point(i) for i in picked]

    return {
        "candidates": len(index),
        "admissible": int(ok.sum()),
        "meeting_constraints": int(meets.sum()),
        "base": point(origin),
        "chosen": point(chosen),
        # Pareto-best points on validation, by gap and victim recall; then two paths.
        "frontier": [
            point(i)
            for i in sorted(
                np.flatnonzero(_pareto(m["largest_young_ratio_ref"], m["victim_recall"])),
                key=lambda i: m["largest_young_ratio_ref"][i],
            )
        ],
        "path_warn": path(0),
        "path_hold": path(1),
    }


_FRONTIER_KEYS = (
    "largest_young_ratio_ref",
    "sender_young_ratio_ref",
    "receiver_young_ratio_ref",
    "largest_young_ratio",
    "sender_young_ratio",
    "receiver_young_ratio",
    "largest_young_hold_ratio",
    "receiver_young_false_hold_rate",
    "false_alert_rate",
    "victim_recall",
    "victim_recall_hold",
    "taka_recall",
    "taka_recall_hold",
)


def _r(value, digits: int = 5):
    value = float(value)
    return None if np.isnan(value) else round(value, digits)


# ------------------------------------------------------------------ evaluation


_REPORTED = (
    ("sender_young_false_alert_rate", "Honest payments from senders under 30 days, interrupted"),
    ("sender_young_ratio", "… times the overall rate"),
    ("receiver_young_false_alert_rate", "Honest payments to wallets under 30 days, interrupted"),
    ("receiver_young_ratio", "… times the overall rate"),
    ("sender_young_ratio_ref", "Young senders: times the rate with no young wallet"),
    ("receiver_young_ratio_ref", "Young receivers: times the rate with no young wallet"),
    ("receiver_young_false_hold_rate", "Honest payments to wallets under 30 days, held"),
    ("sender_young_false_hold_rate", "Honest payments from senders under 30 days, held"),
    ("false_alert_rate", "All honest payments, interrupted"),
    ("false_hold_rate", "All honest payments, held"),
    ("victim_recall", "Victim transfers alerted (warn or above)"),
    ("victim_recall_hold", "Victim transfers held"),
    ("taka_recall", "Victim money alerted"),
    ("taka_recall_hold", "Victim money held"),
    ("alert_rate", "All payments alerted"),
    ("hold_rate", "All payments held"),
)


def bootstrap(
    rows: pd.DataFrame, before: np.ndarray, after: np.ndarray, reps: int = BOOTSTRAP
) -> dict:
    """Point values and 95% intervals, before, after and the difference.

    Resamples sending customers with Poisson(1) weights, so that a customer's
    payments stay together; resampling single payments would understate the spread.
    """
    a, b = _columns(rows, before), _columns(rows, after)
    names = list(a)
    x = np.column_stack([a[n] for n in names] + [b[n] for n in names])
    codes, uniques = pd.factorize(rows["sender_id"])
    rng = np.random.default_rng(SEED)
    draws = []
    for start in range(0, reps, 100):
        n = min(100, reps - start)
        w = rng.poisson(1.0, size=(n, len(uniques))).astype(np.float64)[:, codes]
        draws.append(w @ x)
    sums = np.vstack(draws)
    k = len(names)
    mb = _metrics({n: sums[:, i] for i, n in enumerate(names)})
    ma = _metrics({n: sums[:, k + i] for i, n in enumerate(names)})
    pb, pa = _metrics(_sums(a)), _metrics(_sums(b))

    def ci(values):
        if np.isnan(values).all():  # nothing to measure in these rows
            return [None, None]
        lo, hi = np.nanpercentile(values, [2.5, 97.5])
        return [_r(lo), _r(hi)]

    with np.errstate(invalid="ignore"):
        return _summary(reps, pb, pa, mb, ma, ci)


def _summary(reps, pb, pa, mb, ma, ci) -> dict:
    return {
        "reps": reps,
        "resampled": "sending customers",
        "metrics": [
            {
                "key": key,
                "label": label,
                "before": _r(pb[key]),
                "after": _r(pa[key]),
                "difference": _r(pa[key] - pb[key]),
                "before_ci": ci(mb[key]),
                "after_ci": ci(ma[key]),
                "difference_ci": ci(ma[key] - mb[key]),
            }
            for key, label in _REPORTED
        ],
    }


def intersectional(
    rows: pd.DataFrame, before: np.ndarray, after: np.ndarray, wallets: pd.DataFrame
) -> dict:
    """Account age x channel x area, for the sender and the receiving wallet, both policies.

    Same table as the fairness report (rates against the overall rate of the same
    rows; groups under MIN_GROUP_ROWS honest payments marked too small).
    """
    attributes = wallets.set_index("wallet_id")[["channel", "area_type"]]
    sends = (rows["type"] == "SEND_MONEY").to_numpy()
    out = {}
    for side, part, party, age in (
        ("sender", np.ones(len(rows), dtype=bool), "sender_id", "s_age_days"),
        ("receiver", sends, "receiver_id", "r_age_days"),
    ):
        sub = rows[part].reset_index(drop=True)
        young = np.where(
            sub[age].to_numpy(dtype=np.float64) < YOUNG_DAYS, "under 30 days", "30 days+"
        )
        labels = (
            pd.Series(young)
            + " · "
            + sub[party].map(attributes["channel"]).fillna("unknown").str.upper().to_numpy()
            + " · "
            + sub[party].map(attributes["area_type"]).fillna("unknown").to_numpy()
        ).to_numpy(str)
        b = _group_table(labels, sub, before[part])
        a = {row["group"]: row for row in _group_table(labels, sub, after[part])}
        out[side] = [
            {
                **row,
                "false_alert_rate_after": a[row["group"]]["false_alert_rate"],
                "false_hold_rate_after": a[row["group"]]["false_hold_rate"],
                "ratio_to_overall_after": a[row["group"]]["ratio_to_overall"],
                "victim_transfers_alerted_after": a[row["group"]]["victim_transfers_alerted"],
            }
            for row in b
        ]
    return out


def _outcomes(rows: pd.DataFrame, rank: np.ndarray) -> dict:
    keys = (
        "alerts_per_day",
        "precision",
        "case_recall",
        "loss_txn_recall",
        "taka_recall",
        "taka_stopped",
    )
    return {
        tier: {k: v for k, v in alert_outcomes(rows, rank >= RANK[tier]).items() if k in keys}
        for tier in ("warn", "hold")
    }


def run(
    data_dir: Path,
    policy_version: str = TARGET_POLICY,
    model: str | None = None,
    models_root: Path | None = None,
    reps: int = BOOTSTRAP,
) -> dict:
    target = load_policy(policy_version)
    if not target.segments:
        raise ValueError(f"policy {policy_version} has no segments to fit or evaluate")
    base_policy = target.model_copy(update={"segments": ()})
    bundle = registry.load(model, models_root)
    model_dir = (models_root or registry.models_dir()) / bundle.version
    base = base_policy.resolve_thresholds(bundle.manifest)

    frame = load_frame(data_dir)
    frame = frame[~frame["ambiguous"]].reset_index(drop=True)
    frame = add_context(frame, pd.read_parquet(data_dir / "wallet_flags.parquet"))
    ids = [s.id for s in target.segments]

    measured = {}
    for name, folds in (("fit", FIT_FOLDS), ("test", ("test",))):
        rows = frame[frame["fold"].isin(folds)].reset_index(drop=True)
        outcomes, risk = decide_batch(base_policy, rows, bundle)
        floor = rule_floor(base_policy, outcomes)
        masks = segment_masks(target.segments, rows)
        options, sums = grid(rows, risk, floor, base, masks)
        measured[name] = (rows, outcomes, risk, floor, masks, _metrics(sums))
    choice = fit(measured["fit"][5], measured["test"][5], options, base, ids)

    # Before and after, through the real decision code.
    test, before_outcomes, risk, floor, masks, _ = measured["test"]
    after_outcomes, _ = decide_batch(target, test, bundle)
    before = np.array([RANK[o.tier] for o in before_outcomes])
    after = np.array([RANK[o.tier] for o in after_outcomes])
    # The grid's shortcut must agree with the engine for the scales in the file.
    replayed = np.maximum(model_rank(risk, base), floor)
    for segment, mask in zip(target.segments, masks, strict=True):
        replayed[mask] = np.maximum(model_rank(risk[mask], segment.thresholds(base)), floor[mask])
    file_scales = {
        s.id: {"warn": s.scale["warn"], "hold": s.scale["hold"]} for s in target.segments
    }

    tiers = list(RANK)
    moved = Counter(
        (tiers[b], tiers[a], "fraud" if y else "legitimate")
        for b, a, y in zip(before, after, test["y"].to_numpy(), strict=True)
        if a != b
    )
    report = {
        "model_version": bundle.version,
        "base_policy": same_as(base_policy),
        "policy_version": target.version,
        "fitted_on": list(FIT_FOLDS),
        "evaluated_on": "test",
        "rows": {"fit": len(measured["fit"][0]), "test": len(test)},
        "days": int(test["day"].nunique()),
        "objective": {
            "minimise": (
                "the larger of the young-sender and young-receiver false-alert ratios, "
                "each against honest payments with no young wallet"
            ),
            "subject_to": {
                "victim_recall_loss_at_most": MAX_RECALL_LOSS,
                "taka_recall_loss_at_most": MAX_RECALL_LOSS,
                "victim_recall_hold_loss_at_most": MAX_HOLD_RECALL_LOSS,
                "guard": "segment warn cut-off at most the model's hold cut-off; hold below 1",
            },
            "ties": (
                f"ratios within {GAP_TOLERANCE} count as equal; then the smaller sum of the two "
                "ratios, the smaller false-hold ratio, the smaller change"
            ),
            "warn_scales": list(WARN_SCALES),
            "hold_scales": list(HOLD_SCALES),
        },
        "thresholds": base,
        "segments": [
            {
                "id": s.id,
                "description": s.description,
                "when": [c.model_dump() for c in s.when],
                "applies_to": list(s.applies_to),
                "scale": s.scale,
                "thresholds": s.thresholds(base),
                "rows_test": int(mask.sum()),
            }
            for s, mask in zip(target.segments, masks, strict=True)
        ],
        "fit": choice,
        "policy_matches_fit": all(
            abs(file_scales[sid][k] - choice["chosen"]["scales"][sid][k]) < 1e-9
            for sid in ids
            for k in ("warn", "hold")
        ),
        "engine_matches_grid": bool(np.array_equal(replayed, after)),
        "tier_counts": {
            "before": {t: int((before == RANK[t]).sum()) for t in tiers},
            "after": {t: int((after == RANK[t]).sum()) for t in tiers},
        },
        "tier_changes": [
            {"from": f, "to": t, "label": y, "count": n} for (f, t, y), n in sorted(moved.items())
        ],
        "outcomes": {"before": _outcomes(test, before), "after": _outcomes(test, after)},
        "before_after": bootstrap(test, before, after, reps),
        "intersectional": intersectional(
            test, before, after, pd.read_parquet(data_dir / "wallets.parquet")
        ),
    }
    (model_dir / MITIGATION_FILE).write_text(json.dumps(report, indent=2))
    return report


def same_as(policy: Policy) -> str:
    """The published policy that decides exactly as this one does, e.g. v2 for v3 without
    its segments; otherwise a description."""
    ignore = {"version", "description", "segments"}
    for path in sorted(POLICY_DIR.glob("v*.yaml")):
        other = load_policy(path.stem)
        if not other.segments and other.model_dump(exclude=ignore) == policy.model_dump(
            exclude=ignore
        ):
            return other.version
    return f"{policy.version} without segments"


def print_summary(report: dict) -> None:
    print(f"policy {report['policy_version']} on model {report['model_version']}")
    chosen = report["fit"]["chosen"]
    print("chosen scales (fitted on validation):", json.dumps(chosen["scales"]))
    print("policy file matches the fit:", report["policy_matches_fit"])
    print("engine matches the grid:", report["engine_matches_grid"])
    print(f"{'test period':<58}{'before':>10}{'after':>10}{'diff 95% CI':>24}")
    for m in report["before_after"]["metrics"]:
        lo, hi = m["difference_ci"]
        print(f"{m['label']:<58}{m['before']:>10.5f}{m['after']:>10.5f}   [{lo:+.5f}, {hi:+.5f}]")
    print("tier counts:", json.dumps(report["tier_counts"]))
    print("outcomes:", json.dumps(report["outcomes"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Fit and evaluate young-wallet segment thresholds")
    parser.add_argument("--small", action="store_true")
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--policy", default=TARGET_POLICY)
    parser.add_argument("--model", default=None, help="model version (default: current)")
    parser.add_argument("--reps", type=int, default=BOOTSTRAP)
    args = parser.parse_args()
    data_dir = args.data or settings.data_dir / ("small" if args.small else "full")
    print_summary(run(data_dir, args.policy, args.model, reps=args.reps))


if __name__ == "__main__":
    main()
