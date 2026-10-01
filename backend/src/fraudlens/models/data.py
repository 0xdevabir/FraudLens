"""Training frame: features joined with targets and time-based folds."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

LOSS_ROLES = ("victim_transfer", "ato_transfer")
# Part of a scam or of agent abuse, but neither clean nor transaction fraud.
# Excluded from training and from transaction-level metrics.
AMBIGUOUS_ROLES = ("bait", "agent_abuse")
FOLDS = ("train", "val_a", "val_b", "test")


def load_frame(data_dir: Path) -> pd.DataFrame:
    """Scored transactions with targets and folds.

    Folds are by time. Validation is cut in two so that nothing is tuned twice on
    the same days: `val_a` stops boosting, `val_b` fits fusion weights, calibration
    and alert thresholds. `test` is only ever evaluated.
    """
    df = pd.read_parquet(data_dir / "features.parquet")
    cfg = json.loads((data_dir / "meta.json").read_text())["config"]
    train_end, val_end = cfg["train_end_day"], cfg["val_end_day"]
    val_mid = (train_end + val_end) // 2

    df["fold"] = "test"
    df.loc[df["day"] < val_end, "fold"] = "val_b"
    df.loc[df["day"] < val_mid, "fold"] = "val_a"
    df.loc[df["day"] < train_end, "fold"] = "train"

    df["y"] = df["is_fraud"].astype("int8")
    df["y_loss"] = df["fraud_role"].isin(LOSS_ROLES).astype("int8")
    df["ambiguous"] = df["fraud_role"].isin(AMBIGUOUS_ROLES)

    # A wallet counts as a mule from the first fraud money it receives.
    wallets = pd.read_parquet(data_dir / "wallets.parquet", columns=["wallet_id", "first_fraud_at"])
    first = df["receiver_id"].map(wallets.set_index("wallet_id")["first_fraud_at"])
    df["y_mule"] = ((df["type"] == "SEND_MONEY") & (df["ts"] >= first)).astype("int8")
    return df
