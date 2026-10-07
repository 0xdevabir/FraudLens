"""Adaptive-adversary experiment: does detection survive scammers who learn?

    uv run python -m fraudlens.models.adversary      # backend/data/full and the served model

The default world runs unchanged to its last day (checked against the stored
dataset, so the stored feature-engine snapshot can carry on from it). Then the
served model is "deployed", new scam cells set up for a week, and the adversary
runs in rounds (`simulator.adversary`): after each round it moves its tactic mix
toward whatever got past the model deployed in that arm. Four arms, each its own
simulation because the adversary reacts to its own arm's model:

- control: frozen model, adversary does not adapt (the round-0 mix throughout)
- frozen: the served model, never changed
- retrain: retrained after every round, by the `models.train` recipe with the
  labels `mlops.retrain` would have (see `labels`)
- drift_gated: retrained only after a round in which `mlops.drift.measure` raises
  an alert on the served score

Writes `artifacts/reports/adversary.json`. Nothing in the model registry or the
dataset is written.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict

from ..config import settings
from ..decision.insights import drift_reference, reference_bins
from ..features import FEATURES, RECIPIENT_FEATURES, FeatureEngine
from ..features.build import LABEL_COLUMNS, Replayer, epoch_seconds
from ..mlops import drift
from ..simulator.adversary import TACTICS, Adversary, AdversaryConfig, deploy
from ..simulator.config import DAY, SimConfig
from ..simulator.engine import K_WALLET, Simulation
from ..simulator.generate import DISTRICT_NAMES, _when, party_id, transaction_table
from . import metrics, registry
from .bundle import ANOMALY_FEATURES, ModelBundle, logit
from .data import AMBIGUOUS_ROLES, LOSS_ROLES, load_frame
from .train import FEEDBACK_COLUMNS, FUSION_MIN_GAIN, SEED, choose_thresholds, fit_booster

ARMS = ("control", "frozen", "retrain", "drift_gated")
REPORT_FILE = "adversary.json"
TIER = "warn"  # the operating point the card's headline case recall is quoted at
SHAP_TOP = 8


# ----------------------------------------------------------------- the world


def sim_config(data_dir: Path) -> SimConfig:
    """The configuration the stored dataset was generated with."""
    cfg = json.loads((data_dir / "meta.json").read_text())["config"]
    return SimConfig(**{**cfg, "start": datetime.fromisoformat(cfg["start"])})


class World:
    """One arm's running simulation and the feature engine that follows it."""

    def __init__(self, data_dir: Path, adversary: Adversary) -> None:
        self.cfg = cfg = sim_config(data_dir)
        self.sim = sim = Simulation(cfg)
        sim.run()
        stored = pd.read_parquet(data_dir / "transactions.parquet", columns=["amount"])["amount"]
        if len(stored) != len(sim.cols["amount"]) or not np.isclose(
            stored.sum(), float(np.sum(sim.cols["amount"]))
        ):
            raise RuntimeError(
                f"the simulator no longer reproduces {data_dir}: regenerate the dataset "
                "(make data features) before running the adversary experiment"
            )
        self.engine = FeatureEngine.load(data_dir / "engine_after_test.pkl")
        self.known_wallets = sim.w.n_wallets
        self.done = len(sim.cols["ts"])
        self.day = cfg.days
        self.planner = deploy(sim, adversary, cfg.days)

    def step(self, days: int) -> pd.DataFrame:
        """Simulate `days` more days and replay them: one row per scored transaction."""
        sim, cfg, engine = self.sim, self.cfg, self.engine
        sim.run_days(self.day, self.day + days)
        self.day += days
        end_ts = self.day * DAY
        txns = transaction_table(sim, self.done)
        self.done += len(txns)

        w = sim.w
        new = range(self.known_wallets, w.n_wallets)
        created = epoch_seconds(_when(cfg, [w.created_ts[i] for i in new])).tolist()
        for i, ts in zip(new, created, strict=True):
            engine.register_wallet(party_id(K_WALLET, i), ts, DISTRICT_NAMES[w.district[i]])
        self.known_wallets = w.n_wallets

        flagged = sorted(
            (m.flagged_ts, party_id(K_WALLET, m.wallet))
            for m in self.planner.mules.values()
            if m.flagged_ts < end_ts and party_id(K_WALLET, m.wallet) not in engine.flagged
        )
        flags = pd.DataFrame(
            {
                "wallet_id": [f[1] for f in flagged],
                "flagged_at": _when(cfg, [f[0] for f in flagged]),
            }
        )
        pos, x = Replayer(engine, flags).run(txns)
        frame = txns.iloc[pos][list(LABEL_COLUMNS)].reset_index(drop=True)
        frame = pd.concat([frame, pd.DataFrame(x, columns=list(FEATURES))], axis=1)
        frame["y"] = frame["is_fraud"].astype("int8")
        frame["y_loss"] = frame["fraud_role"].isin(LOSS_ROLES).astype("int8")
        frame = frame[~frame["fraud_role"].isin(AMBIGUOUS_ROLES)].reset_index(drop=True)
        mules = self.planner.mules
        first = {party_id(K_WALLET, k): m.first_fraud_ts for k, m in mules.items()}
        first_ts = _when(cfg, frame["receiver_id"].map(first).fillna(np.inf).to_numpy())
        frame["y_mule"] = ((frame["type"] == "SEND_MONEY") & (frame["ts"] >= first_ts)).astype(
            "int8"
        )
        frame["tactic"] = frame["case_id"].map(self.planner.tactic).fillna("")
        return frame

    def reported_cases(self) -> set[int]:
        """Scams a victim has reported by now: what a complaint desk would know."""
        now = self.day * DAY
        return {case for case, ts in self.planner.reported.items() if ts < now}


# ---------------------------------------------------------------- the models


class Deployed:
    """A model as served: bundle, tier thresholds and its drift reference."""

    def __init__(self, bundle: ModelBundle, thresholds: dict[str, float], reference: dict):
        self.bundle = bundle
        self.thresholds = thresholds
        self.reference = reference

    def risk(self, frame: pd.DataFrame) -> np.ndarray:
        return self.bundle.score(frame[list(FEATURES)].to_numpy(dtype=np.float64))["risk"]


def fit_bundle(fold: dict[str, pd.DataFrame], version: str) -> tuple[ModelBundle, np.ndarray]:
    """The `models.train.train` recipe without its reports: boosted transaction and
    mule models, isolation forest, fusion and calibration on val_b, thresholds on val_b.
    Returns the bundle (thresholds in its manifest) and its calibrated val_b risk."""
    cols, recipient = list(FEATURES), list(RECIPIENT_FEATURES)
    send = {name: part[part["type"] == "SEND_MONEY"] for name, part in fold.items()}
    txn = fit_booster(fold["train"], fold["val_a"], cols, "y")
    mule = fit_booster(send["train"], send["val_a"], recipient, "y_mule")
    behaviour = fold["train"][list(ANOMALY_FEATURES)]
    sample = behaviour.fillna(behaviour.median()).sample(
        min(len(behaviour), 200_000), random_state=SEED
    )
    anomaly = IsolationForest(n_estimators=100, random_state=SEED, n_jobs=-1).fit(sample.to_numpy())
    bundle = ModelBundle(
        version=version, txn=txn, mule=mule, anomaly=anomaly,
        anomaly_fill=behaviour.median().to_numpy(), fusion_coef=np.zeros(4),
        fusion_intercept=0.0, calibration=(1.0, 0.0), risk_source="txn", manifest={},
    )  # fmt: skip
    vb = fold["val_b"]
    parts = bundle.components(vb[cols].to_numpy())
    z, y = bundle.fusion_inputs(parts), vb["y"].to_numpy()
    fusion = LogisticRegression(C=1.0, max_iter=2000)
    cv = StratifiedKFold(5, shuffle=True, random_state=SEED)
    fused_cv = cross_val_predict(fusion, z, y, cv=cv, method="predict_proba")[:, 1]
    fusion.fit(z, y)
    bundle.fusion_coef, bundle.fusion_intercept = fusion.coef_[0], float(fusion.intercept_[0])
    val_txn = metrics.ranking(y, parts["txn"])["pr_auc"]
    val_fused = metrics.ranking(y, fused_cv)["pr_auc"]
    bundle.risk_source = "fused" if val_fused >= val_txn + FUSION_MIN_GAIN else "txn"
    raw = bundle.raw_risk(parts)
    platt = LogisticRegression(C=1e6, max_iter=2000).fit(logit(raw).reshape(-1, 1), y)
    bundle.calibration = (float(platt.coef_[0][0]), float(platt.intercept_[0]))
    risk = bundle.calibrate(raw)
    bundle.manifest = {"version": version, "thresholds": choose_thresholds(y, risk)}
    return bundle, risk


class Lab:
    """What every arm shares: the training folds, drift references, the served model."""

    def __init__(self, data_dir: Path, models_root: Path | None, version: str | None) -> None:
        self.data_dir = data_dir
        frame = load_frame(data_dir)
        clean = frame[~frame["ambiguous"]]
        self.fold = {n: clean[clean["fold"] == n] for n in ("train", "val_a", "val_b", "test")}
        served = registry.load(version, models_root)
        self.version = served.version
        thresholds = dict(served.manifest["thresholds"])
        ref_rows = pd.concat([self.fold["train"], self.fold["val_b"]], ignore_index=True)
        risk = served.score(ref_rows[list(FEATURES)].to_numpy(dtype=np.float64))["risk"]
        self.population_reference = drift_reference(ref_rows, risk, thresholds)
        self.served = Deployed(served, thresholds, self.population_reference)

        test = self.fold["test"]
        test_risk = self.served.risk(test)
        self.served_check = {
            "test_pr_auc": metrics.ranking(test["y"].to_numpy(), test_risk)["pr_auc"],
            "manifest_pr_auc": served.manifest.get("headline", {}).get("pr_auc"),
        }

        # The same monitor pointed at confirmed fraud only: training-period fraud as
        # the feature reference, the model's scores on val_b fraud as the score reference.
        fraud_train = self.fold["train"][self.fold["train"]["y"] == 1]
        self.fraud_features = {
            name: reference_bins(fraud_train[name].to_numpy(dtype=np.float64)) for name in FEATURES
        }
        self.val_b_fraud = self.fold["val_b"][self.fold["val_b"]["y"] == 1]

    def fraud_reference(self, model: Deployed) -> dict:
        val_b_fraud_risk = model.risk(self.val_b_fraud)
        thresholds = model.thresholds
        return {
            "features": self.fraud_features,
            "score": reference_bins(val_b_fraud_risk),
            "validation_alert_rate": round(float((val_b_fraud_risk >= thresholds[TIER]).mean()), 5),
            "validation_hold_rate": round(
                float((val_b_fraud_risk >= thresholds["hold"]).mean()), 5
            ),
        }

    def retrain(self, feedback: pd.DataFrame, version: str) -> Deployed:
        """Verdicts and complaints join the training fold, as `models.train.add_feedback` does."""
        fold = dict(self.fold)
        rows = feedback[list(FEEDBACK_COLUMNS)].astype({"y": "int8", "y_mule": "int8"})
        fold["train"] = pd.concat([fold["train"], rows], ignore_index=True)
        bundle, val_b_risk = fit_bundle(fold, version)
        thresholds = bundle.manifest["thresholds"]
        reference = {
            **self.population_reference,
            "score": reference_bins(val_b_risk),
            "validation_alert_rate": round(float((val_b_risk >= thresholds[TIER]).mean()), 5),
            "validation_hold_rate": round(float((val_b_risk >= thresholds["hold"]).mean()), 5),
        }
        return Deployed(bundle, thresholds, reference)


# -------------------------------------------------------------- measurement


def labels(frames: list[pd.DataFrame], alerted: list[np.ndarray], reported: set[int]):
    """What a retrain could learn from by now, in `FEEDBACK_COLUMNS`.

    Verdicts: every alerted transaction, closed with its true label (as `make review`
    closes cases with the simulation's ground truth). Complaints: every fraud-chain
    transaction of a scam whose victim has reported it. Fraud nobody alerted and
    nobody reported stays unlabelled, as it would in production.
    """
    parts = []
    for frame, hit in zip(frames, alerted, strict=True):
        complaint = frame["case_id"].isin(reported).to_numpy() & (frame["y"] == 1).to_numpy()
        parts.append(frame[hit | complaint])
    rows = pd.concat(parts, ignore_index=True).drop_duplicates("txn_id")
    rows = rows.assign(y_mule=(rows["y"] * (rows["type"] == "SEND_MONEY")).astype("int8"))
    return rows


def outcomes(frame: pd.DataFrame, risk: np.ndarray, threshold: float) -> dict:
    """Recall and precision at the tier threshold, overall and by tactic."""
    hit = risk >= threshold
    overall = metrics.alert_outcomes(frame, hit)
    by_tactic = metrics.alert_outcomes(frame.assign(typology=frame["tactic"]), hit)["by_typology"]
    keep = ("loss_txns", "cases", "loss_txn_recall", "case_recall", "taka_recall",
            "taka_recall_with_exit_holds")  # fmt: skip
    return {
        "pr_auc": metrics.ranking(frame["y"].to_numpy(), risk)["pr_auc"],
        **{k: overall[k] for k in ("alerts_per_day", "alert_rate", "precision", *keep)},
        "by_tactic": {t: {k: v[k] for k in keep} for t, v in by_tactic.items() if t},
    }


def tactic_results(frame: pd.DataFrame, risk: np.ndarray, threshold: float):
    """Per tactic: (scams run, scams with no victim transfer alerted) — the adversary's view."""
    victim = frame[frame["y_loss"] == 1].assign(
        hit=risk[(frame["y_loss"] == 1).to_numpy()] >= threshold
    )
    caught = victim.groupby("case_id").agg(tactic=("tactic", "first"), hit=("hit", "any"))
    out = {}
    for tactic, rows in caught.groupby("tactic"):
        out[str(tactic)] = (int(len(rows)), int((~rows["hit"]).sum()))
    return out


def monitor(reference: dict, frame: pd.DataFrame, risk: np.ndarray, thresholds: dict) -> dict:
    """`mlops.drift.measure`, summarised: does it raise an alert?"""
    if len(frame) < drift.MIN_ROWS:
        return {"status": "not_enough_data", "rows": int(len(frame)), "needed": drift.MIN_ROWS}
    m = drift.measure(reference, frame[list(FEATURES)].to_numpy(dtype=np.float64), risk, thresholds)
    return {
        "rows": m["rows"],
        "score_psi": m["score"]["psi"],
        "score_status": m["score"]["status"],
        "alert": m["score"]["status"] != "stable",
        "alert_rate": m["score"]["alert_rate"],
        "validation_alert_rate": m["score"]["validation_alert_rate"],
        "feature_status": m["feature_status"],
        "most_shifted": [{"feature": f["feature"], "psi": f["psi"]} for f in m["features"][:5]],
    }


def shap_shares(model: Deployed, frame: pd.DataFrame) -> list[dict]:
    """Mean |SHAP| of the transaction model on victim transfers, as shares of the total."""
    victims = frame[frame["y_loss"] == 1]
    if victims.empty:
        return []
    txn, _ = model.bundle.contributions(victims[list(FEATURES)].to_numpy(dtype=np.float64))
    mean = np.abs(txn).mean(axis=0)
    order = np.argsort(-mean)[:SHAP_TOP]
    total = mean.sum() or 1.0
    return [{"feature": FEATURES[i], "share": round(float(mean[i] / total), 4)} for i in order]


# ------------------------------------------------------------------- arms


def run_arm(arm: str, lab: Lab, acfg: AdversaryConfig, log=print) -> dict:
    adversary = Adversary(replace(acfg, adapt=arm != "control"))
    world = World(lab.data_dir, adversary)
    world.step(acfg.warmup_days)  # the new cells open and warm up their mules
    model, model_name = lab.served, lab.version
    frames, alerted, rounds = [], [], []
    for r in range(acfg.rounds):
        mix = {t: round(v, 4) for t, v in adversary.mix.items()}
        frame = world.step(acfg.round_days)
        risk = model.risk(frame)
        threshold = model.thresholds[TIER]
        frames.append(frame.assign(risk=risk))
        alerted.append(risk >= threshold)

        result = outcomes(frame, risk, threshold)
        population = monitor(model.reference, frame, risk, model.thresholds)
        known = labels(frames, alerted, world.reported_cases())
        known_now = known[known["txn_id"].isin(frame["txn_id"]) & (known["y"] == 1)]
        fraud_seg = monitor(
            lab.fraud_reference(model), known_now,
            model.risk(known_now) if len(known_now) else np.array([]), model.thresholds,
        )  # fmt: skip
        tactics = tactic_results(frame, risk, threshold)
        new_mix = adversary.update(tactics)

        retrain = arm == "retrain" or (arm == "drift_gated" and population.get("alert", False))
        rounds.append(
            {
                "round": r,
                "days": [world.day - acfg.round_days, world.day],
                "model": model_name,
                "mix": mix,
                "scams_by_tactic": {
                    t: {"run": n, "got_through": g} for t, (n, g) in tactics.items()
                },
                **result,
                "drift_population": population,
                "drift_confirmed_fraud": fraud_seg,
                "labels_known": {"rows": int(len(known)), "fraud": int(known["y"].sum())},
                "retrain_after": bool(retrain and r < acfg.rounds - 1),
            }
        )
        log(
            f"  {arm} round {r}: case recall {result['case_recall']}, victim-txn recall "
            f"{result['loss_txn_recall']}, score PSI {population.get('score_psi')}, "
            f"model {model_name}"
        )
        if retrain and r < acfg.rounds - 1:
            model_name = f"{arm}-r{r + 1}"
            model = lab.retrain(known, model_name)
        rounds[-1]["mix_after"] = {t: round(v, 4) for t, v in new_mix.items()}

    pooled = pd.concat(frames, ignore_index=True)
    risk_all = pooled["risk"].to_numpy()
    by_tactic = metrics.alert_outcomes(
        pooled.assign(typology=pooled["tactic"]), np.concatenate(alerted)
    )["by_typology"]
    return {
        "rounds": rounds,
        "pooled_pr_auc": metrics.ranking(pooled["y"].to_numpy(), risk_all)["pr_auc"],
        "pooled_by_tactic": {
            t: {
                k: v[k]
                for k in (
                    "loss_txns",
                    "cases",
                    "loss_txn_recall",
                    "case_recall",
                    "taka_recall_with_exit_holds",
                )
            }  # fmt: skip
            for t, v in by_tactic.items()
            if t
        },
        "shap_first_round": shap_shares(lab.served, frames[0]),
        "shap_last_round": shap_shares(model, frames[-1]),
        "last_model": model_name,
    }


def run(
    data_dir: Path,
    models_root: Path | None = None,
    version: str | None = None,
    acfg: AdversaryConfig | None = None,
    arms: tuple[str, ...] = ARMS,
    log=print,
) -> dict:
    t0 = time.perf_counter()
    acfg = acfg or AdversaryConfig()
    lab = Lab(data_dir, models_root, version)
    log(f"served model {lab.version}: test PR-AUC {lab.served_check['test_pr_auc']}")
    results = {}
    for arm in arms:
        t = time.perf_counter()
        results[arm] = run_arm(arm, lab, acfg, log)
        log(f"{arm} done in {time.perf_counter() - t:.0f}s")
    summary = {
        arm: {
            "case_recall": [r["case_recall"] for r in res["rounds"]],
            "loss_txn_recall": [r["loss_txn_recall"] for r in res["rounds"]],
            "drift_alert": [r["drift_population"].get("alert") for r in res["rounds"]],
        }
        for arm, res in results.items()
    }
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "synthetic": True,
        "data_dir": str(data_dir),
        "served_model": lab.version,
        "served_model_check": lab.served_check,
        "tier": TIER,
        "tactics": list(TACTICS),
        "adversary": asdict(acfg),
        "sampling": "none: every transaction of every round is replayed and scored",
        "arms": results,
        "summary": summary,
        "seconds": round(time.perf_counter() - t0, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Adaptive-adversary experiment")
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--version", default=None, help="model to deploy (default: the served one)")
    parser.add_argument("--rounds", type=int, default=AdversaryConfig.rounds)
    parser.add_argument("--round-days", type=int, default=AdversaryConfig.round_days)
    parser.add_argument("--cells", type=int, default=AdversaryConfig.n_cells)
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    arms = tuple(a for a in args.arms.split(",") if a)
    if unknown := set(arms) - set(ARMS):
        parser.error(f"unknown arms: {sorted(unknown)}")
    acfg = AdversaryConfig(rounds=args.rounds, round_days=args.round_days, n_cells=args.cells)
    report = run(args.data or settings.dataset_dir, settings.models_dir, args.version, acfg, arms)
    out = args.out or settings.artifacts_dir / "reports" / REPORT_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report["summary"], indent=2))
    print(f"wrote {out} in {report['seconds']}s")


if __name__ == "__main__":
    main()
