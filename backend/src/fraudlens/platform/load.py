"""Load the historical period into the platform database.

    uv run python -m fraudlens.platform.load [--reset]

Loads what upay would already hold on the day the platform goes live: wallet and
agent master data, every transaction up to the end of the validation period,
the wallets confirmed as fraud by then, and the fraud cases closed by then.
The held-out test period is not loaded: it arrives through the API and the
event stream (`fraudlens.platform.replay`).

Ground-truth columns of the synthetic data (who is a mule, which transaction is
fraud) are never loaded. The platform only knows what a real one would know.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd
from redis import Redis
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from ..config import Settings
from .audit import Ctx, audit
from .db import make_engine, migrate
from .models import Transaction
from .stream import DEAD_SUFFIX

SYSTEM = Ctx(None, "system", None)
# Everything the platform writes at run time. Never users, never the audit log.
RUNTIME_TABLES = (
    "shadow_scores", "decisions", "case_events", "freeze_requests", "customer_reports",
    "wallet_flags", "blocklist", "cases", "transactions", "past_cases", "wallets", "agents",
)  # fmt: skip
TXN_COLUMNS = (
    "txn_id", "ts", "type", "sender_id", "sender_type", "receiver_id", "receiver_type",
    "amount", "sender_balance_before", "device_id", "channel", "district",
)  # fmt: skip


def _utc(series: pd.Series) -> pd.Series:
    """Dataset times carry no zone and are UTC."""
    return series.dt.strftime("%Y-%m-%d %H:%M:%S+00")


def _copy(cursor, table: str, frame: pd.DataFrame) -> int:
    columns = ", ".join(frame.columns)
    with cursor.copy(f"COPY {table} ({columns}) FROM STDIN") as copy:
        for row in frame.astype(object).where(frame.notna(), None).itertuples(index=False):
            copy.write_row(row)
    return len(frame)


def history_cutoff(txns: pd.DataFrame) -> pd.Timestamp:
    """Time of the last transaction before the test period."""
    return txns.loc[txns["split"] != "test", "ts"].max()


def load(engine: Engine, redis: Redis | None, settings: Settings, reset: bool = False) -> dict:
    data_dir: Path = settings.dataset_dir
    with Session(engine) as s:
        existing = s.scalar(select(func.count()).select_from(Transaction))
    if existing and not reset:
        raise RuntimeError(
            f"the database already holds {existing:,} transactions; pass --reset to replace them"
        )

    val_end_day = json.loads((data_dir / "meta.json").read_text())["config"]["val_end_day"]
    txns = pd.read_parquet(data_dir / "transactions.parquet", columns=[*TXN_COLUMNS, "split"])
    cutoff = history_cutoff(txns)
    history = txns.loc[txns["split"] != "test", list(TXN_COLUMNS)].copy()
    history["applied_at"] = history["ts"] = _utc(history["ts"])
    history["status"], history["source"] = "completed", "history"

    wallets = pd.read_parquet(
        data_dir / "wallets.parquet",
        columns=["wallet_id", "created_at", "district", "area_type", "channel", "segment"],
    )
    wallets["created_at"] = _utc(wallets["created_at"])
    agents = pd.read_parquet(data_dir / "agents.parquet", columns=["agent_id", "district"])

    flags = pd.read_parquet(data_dir / "wallet_flags.parquet")
    flags = flags.loc[flags["flagged_at"] <= cutoff, ["wallet_id", "flagged_at", "flag_reason"]]
    flags = flags.rename(columns={"flag_reason": "reason"})
    flags["flagged_at"], flags["source"] = _utc(flags["flagged_at"]), "history"

    cases = pd.read_parquet(data_dir / "cases.parquet")
    cases = cases.loc[
        cases["day"] < val_end_day,
        ["case_id", "typology", "victim_id", "mule_id", "started_at", "loss", "n_txn"],
    ].copy()
    cases["started_at"] = _utc(cases["started_at"])

    t0 = time.perf_counter()
    counts = {}
    connection = engine.raw_connection()
    try:
        with connection.cursor() as cursor:
            if reset:
                cursor.execute(f"TRUNCATE {', '.join(RUNTIME_TABLES)} RESTART IDENTITY")
            counts["wallets"] = _copy(cursor, "wallets", wallets)
            counts["agents"] = _copy(cursor, "agents", agents)
            counts["transactions"] = _copy(cursor, "transactions", history)
            counts["wallet_flags"] = _copy(cursor, "wallet_flags", flags)
            counts["past_cases"] = _copy(cursor, "past_cases", cases)
        connection.commit()
    finally:
        connection.close()

    with Session(engine) as s:
        audit(s, SYSTEM, "platform.load", "dataset", settings.dataset, reset=reset, **counts)
        s.commit()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text("ANALYZE"))
    if redis is not None and reset:
        # Events queued for the previous contents make no sense against the new ones.
        redis.delete(settings.events_stream, settings.events_stream + DEAD_SUFFIX)
    return {**counts, "history_ends": str(cutoff), "seconds": round(time.perf_counter() - t0, 1)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Load the historical period into the database")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="replace the current contents (users and the audit log are kept)",
    )
    args = parser.parse_args()
    settings = Settings()
    migrate(settings.database_url)
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    report = load(engine, redis, settings, reset=args.reset)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
