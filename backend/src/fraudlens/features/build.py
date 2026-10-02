"""Replay history through the feature engine to build the training dataset.

uv run python -m fraudlens.features.build            # backend/data/full
uv run python -m fraudlens.features.build --small    # backend/data/small

Writes `features.parquet` (one row per scored transaction: keys, labels, features,
and the behaviour facts the decision policy reads)
and an engine snapshot at the end of each split, so the API can start warm from
the state as of the end of validation and score the test period live.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import settings
from .engine import BEHAVIOUR_FIELDS, FEATURES, SCORED_TYPES, FeatureEngine, Txn

# Carried next to the features for training and evaluation. Never model inputs.
LABEL_COLUMNS = (
    "txn_id", "ts", "day", "split", "type", "sender_id", "receiver_id",
    "is_fraud", "fraud_role", "typology", "case_id",
)  # fmt: skip
SPLITS = ("train", "val", "test")


def epoch_seconds(ts: pd.Series) -> np.ndarray:
    return ts.to_numpy("datetime64[s]").astype("int64").astype("float64")


def iter_txns(df: pd.DataFrame):
    """Transaction rows as engine inputs. Label columns are deliberately not passed."""
    # The synthetic history carries no network; a dataset that does has a `network` column.
    network = df["network"].tolist() if "network" in df.columns else [""] * len(df)
    return map(
        Txn._make,
        zip(
            df["txn_id"].tolist(),
            epoch_seconds(df["ts"]).tolist(),
            df["type"].tolist(),
            df["sender_id"].tolist(),
            df["sender_type"].tolist(),
            df["receiver_id"].tolist(),
            df["receiver_type"].tolist(),
            df["amount"].tolist(),
            df["sender_balance_before"].tolist(),
            df["device_id"].tolist(),
            df["channel"].tolist(),
            df["district"].tolist(),
            network,
            strict=True,
        ),
    )


def new_engine(wallets: pd.DataFrame, agents: pd.DataFrame) -> FeatureEngine:
    """An engine that knows the wallet and agent master data and nothing else."""
    engine = FeatureEngine()
    created = epoch_seconds(wallets["created_at"]).tolist()
    for wid, ts, district in zip(wallets["wallet_id"], created, wallets["district"], strict=True):
        engine.register_wallet(wid, ts, district)
    for aid, district in zip(agents["agent_id"], agents["district"], strict=True):
        engine.register_agent(aid, district)
    return engine


class Replayer:
    """Feeds transactions and wallet-flag events to an engine in time order."""

    def __init__(self, engine: FeatureEngine, flags: pd.DataFrame) -> None:
        self.engine = engine
        self._flags = list(
            zip(epoch_seconds(flags["flagged_at"]).tolist(), flags["wallet_id"], strict=True)
        )
        self._next_flag = 0
        # Behaviour facts (BEHAVIOUR_FIELDS) of the rows scored by the last `run`.
        self.behaviour = np.empty((0, len(BEHAVIOUR_FIELDS)))

    def run(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Replay `df`. Returns (positions of scored rows in df, their feature matrix)."""
        engine, flags = self.engine, self._flags
        scored = df["type"].isin(SCORED_TYPES).to_numpy()
        out = np.empty((int(scored.sum()), len(FEATURES)))
        behaviour = np.empty((len(out), len(BEHAVIOUR_FIELDS)))
        i, j, n_flags = 0, self._next_flag, len(flags)
        for t, is_scored in zip(iter_txns(df), scored.tolist(), strict=True):
            # A flag becomes visible only to transactions at or after the time it was raised.
            while j < n_flags and flags[j][0] <= t.ts:
                engine.flag_wallet(flags[j][1], flags[j][0])
                j += 1
            if is_scored:
                out[i] = engine.features(t)
                behaviour[i] = engine.behaviour(t)
                i += 1
            engine.update(t)
        self._next_flag = j
        self.behaviour = behaviour
        return np.flatnonzero(scored), out


def build_features(
    tables: dict[str, pd.DataFrame], snapshot_dir: Path | None = None
) -> pd.DataFrame:
    txns = tables["transactions"]
    replayer = Replayer(new_engine(tables["wallets"], tables["agents"]), tables["wallet_flags"])
    parts = []
    for split in SPLITS:
        part = txns[txns["split"] == split]
        pos, x = replayer.run(part)
        frame = part.iloc[pos][list(LABEL_COLUMNS)].reset_index(drop=True)
        columns = [*FEATURES, *BEHAVIOUR_FIELDS]
        values = np.hstack([x, replayer.behaviour])
        parts.append(pd.concat([frame, pd.DataFrame(values, columns=columns)], axis=1))
        if snapshot_dir is not None:
            replayer.engine.save(snapshot_dir / f"engine_after_{split}.pkl")
    return pd.concat(parts, ignore_index=True)


def load_tables(data_dir: Path) -> dict[str, pd.DataFrame]:
    names = ("transactions", "wallets", "wallet_flags", "agents")
    return {n: pd.read_parquet(data_dir / f"{n}.parquet") for n in names}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the FraudLens feature dataset")
    parser.add_argument("--small", action="store_true")
    parser.add_argument("--data", type=Path, default=None)
    args = parser.parse_args()
    data_dir = args.data or settings.data_dir / ("small" if args.small else "full")
    t0 = time.perf_counter()
    tables = load_tables(data_dir)
    features = build_features(tables, snapshot_dir=data_dir)
    features.to_parquet(data_dir / "features.parquet", index=False)
    secs = time.perf_counter() - t0
    n = len(tables["transactions"])
    print(f"{len(features):,} scored rows x {len(FEATURES)} features from {n:,} transactions")
    print(f"{secs:.1f}s ({n / secs:,.0f} events/s) -> {data_dir / 'features.parquet'}")


if __name__ == "__main__":
    main()
