"""The set of models that scores a transaction, saved and loaded as one version."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np

from ..features import FEATURES, RECIPIENT_FEATURES

ANOMALY_FEATURES = (
    "amount", "hour", "amount_to_balance", "s_amount_z", "s_amount_vs_max", "s_cnt_1h",
    "s_cnt_24h", "s_sum_24h", "s_new_recipients_24h", "s_secs_since_last_txn", "s_hour_share",
    "s_new_device", "s_away_from_home", "s_district_changed", "pair_prior_count",
)  # fmt: skip

_IDX = {name: i for i, name in enumerate(FEATURES)}
RECIPIENT_IDX = [_IDX[n] for n in RECIPIENT_FEATURES]
ANOMALY_IDX = [_IDX[n] for n in ANOMALY_FEATURES]
IS_CASH_OUT = _IDX["is_cash_out"]
EPS = 1e-6


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


@dataclass
class ModelBundle:
    version: str
    txn: lgb.Booster
    mule: lgb.Booster
    anomaly: object  # sklearn IsolationForest
    anomaly_fill: np.ndarray  # training medians, used where a behaviour feature is missing
    fusion_coef: np.ndarray  # weights for fusion_inputs() columns
    fusion_intercept: float
    calibration: tuple[float, float]  # Platt scaling (slope, intercept) on the risk logit
    risk_source: str  # "fused" or "txn": which score is served as the risk score
    manifest: dict

    # ---------------------------------------------------------------- scoring

    def components(self, x: np.ndarray) -> dict[str, np.ndarray]:
        """Scores from each model for a feature matrix in FEATURES order."""
        x = np.atleast_2d(np.asarray(x, dtype=np.float64))
        is_send = x[:, IS_CASH_OUT] == 0
        mule = np.full(len(x), np.nan)
        if is_send.any():
            mule[is_send] = self.mule.predict(x[is_send][:, RECIPIENT_IDX])
        behaviour = x[:, ANOMALY_IDX]
        behaviour = np.where(np.isnan(behaviour), self.anomaly_fill, behaviour)
        return {
            "txn": self.txn.predict(x),
            "mule": mule,
            "anomaly": -self.anomaly.score_samples(behaviour),
        }

    @staticmethod
    def fusion_inputs(parts: dict[str, np.ndarray]) -> np.ndarray:
        has_mule = ~np.isnan(parts["mule"])
        mule_logit = np.where(has_mule, logit(np.nan_to_num(parts["mule"], nan=0.5)), 0.0)
        return np.column_stack([logit(parts["txn"]), mule_logit, has_mule, parts["anomaly"]])

    def raw_risk(self, parts: dict[str, np.ndarray]) -> np.ndarray:
        if self.risk_source == "txn":
            return parts["txn"]
        z = self.fusion_inputs(parts) @ self.fusion_coef + self.fusion_intercept
        return 1.0 / (1.0 + np.exp(-z))

    def score(self, x: np.ndarray) -> dict[str, np.ndarray]:
        """Component scores plus `risk`: the calibrated probability of fraud."""
        parts = self.components(x)
        parts["risk"] = self.calibrate(self.raw_risk(parts))
        return parts

    def calibrate(self, raw: np.ndarray) -> np.ndarray:
        # Platt scaling is strictly increasing, so calibration never changes the ranking.
        slope, intercept = self.calibration
        return 1.0 / (1.0 + np.exp(-(slope * logit(raw) + intercept)))

    def recipient_risk(self, recipient_features: np.ndarray) -> np.ndarray:
        """Mule probability for a wallet from RECIPIENT_FEATURES alone (no sender needed)."""
        return self.mule.predict(np.atleast_2d(np.asarray(recipient_features, dtype=np.float64)))

    def contributions(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Per-feature SHAP contributions, in log-odds, for the transaction and mule models.

        Returns (txn_contrib[n, len(FEATURES)], mule_contrib[n, len(RECIPIENT_FEATURES)]).
        Mule contributions are zero for cash-outs, which have no recipient wallet.
        """
        x = np.atleast_2d(np.asarray(x, dtype=np.float64))
        txn = self.txn.predict(x, pred_contrib=True)[:, :-1]
        mule = np.zeros((len(x), len(RECIPIENT_IDX)))
        is_send = x[:, IS_CASH_OUT] == 0
        if is_send.any():
            mule[is_send] = self.mule.predict(x[is_send][:, RECIPIENT_IDX], pred_contrib=True)[
                :, :-1
            ]
        return txn, mule

    # ------------------------------------------------------------ persistence

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.txn.save_model(str(directory / "txn.lgb"))
        self.mule.save_model(str(directory / "mule.lgb"))
        joblib.dump(self.anomaly, directory / "anomaly.joblib")
        manifest = {
            **self.manifest,
            "version": self.version,
            "features": list(FEATURES),
            "recipient_features": list(RECIPIENT_FEATURES),
            "anomaly_features": list(ANOMALY_FEATURES),
            "anomaly_fill": self.anomaly_fill.tolist(),
            "fusion": {
                "inputs": ["txn_logit", "mule_logit", "has_mule", "anomaly"],
                "coef": self.fusion_coef.tolist(),
                "intercept": self.fusion_intercept,
            },
            "calibration": {"slope": self.calibration[0], "intercept": self.calibration[1]},
            "risk_source": self.risk_source,
        }
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2))

    @classmethod
    def load(cls, directory: Path) -> ModelBundle:
        manifest = json.loads((directory / "manifest.json").read_text())
        if manifest["features"] != list(FEATURES):
            raise ValueError(
                f"model {manifest['version']} was trained on a different feature list "
                "than this code computes; retrain or deploy the matching code"
            )
        return cls(
            version=manifest["version"],
            txn=lgb.Booster(model_file=str(directory / "txn.lgb")),
            mule=lgb.Booster(model_file=str(directory / "mule.lgb")),
            # Our own build artifact; never load a model file from an untrusted source.
            anomaly=joblib.load(directory / "anomaly.joblib"),
            anomaly_fill=np.asarray(manifest["anomaly_fill"]),
            fusion_coef=np.asarray(manifest["fusion"]["coef"]),
            fusion_intercept=float(manifest["fusion"]["intercept"]),
            calibration=(
                float(manifest["calibration"]["slope"]),
                float(manifest["calibration"]["intercept"]),
            ),
            risk_source=manifest["risk_source"],
            manifest=manifest,
        )
