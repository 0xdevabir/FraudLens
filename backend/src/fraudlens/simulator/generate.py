"""Run the simulation and write the dataset.

uv run python -m fraudlens.simulator.generate            # full world
uv run python -m fraudlens.simulator.generate --small    # test-sized world
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import settings
from .config import DAY, PROFILES, SimConfig
from .engine import ADD_MONEY, CASH_IN, K_WALLET, KIND_NAMES, TYPE_NAMES, Simulation
from .world import DISTRICTS, MERCHANT_CATEGORIES, SEGMENTS

_PREFIX = ("W{:07d}", "A{:05d}", "M{:05d}", "T{:02d}", "B{:03d}", "BANK")
DISTRICT_NAMES = np.array([d[0] for d in DISTRICTS])


def party_id(kind: int, idx: int) -> str:
    return _PREFIX[kind].format(idx)


def _ids(kinds: list[int], idxs: list[int]) -> list[str]:
    return [_PREFIX[k].format(i) for k, i in zip(kinds, idxs, strict=True)]


def _when(cfg: SimConfig, seconds) -> pd.Series:
    """Simulation seconds to timestamps; infinity (never happened) becomes NaT."""
    s = pd.Series(seconds, dtype="float64").replace([math.inf, -math.inf], np.nan)
    return pd.Timestamp(cfg.start) + pd.to_timedelta(s.round(), unit="s")


def build_tables(sim: Simulation) -> dict[str, pd.DataFrame]:
    cfg, w, c = sim.cfg, sim.w, sim.cols
    ts = np.asarray(c["ts"], dtype=np.int64)
    day = ts // DAY
    typ = np.asarray(c["type"])
    app = np.asarray(c["app"])
    device = np.asarray(c["device"])

    channel = np.where(app == 1, "app", "ussd").astype(object)
    channel[typ == CASH_IN] = "agent"
    channel[typ == ADD_MONEY] = "bank"

    txns = pd.DataFrame(
        {
            "txn_id": np.arange(len(ts), dtype=np.int64),
            "ts": _when(cfg, ts),
            "day": day,
            "split": np.select(
                [day < cfg.train_end_day, day < cfg.val_end_day], ["train", "val"], "test"
            ),
            "type": np.asarray(TYPE_NAMES)[typ],
            "sender_id": _ids(c["s_kind"], c["s"]),
            "sender_type": np.asarray(KIND_NAMES)[np.asarray(c["s_kind"])],
            "receiver_id": _ids(c["r_kind"], c["r"]),
            "receiver_type": np.asarray(KIND_NAMES)[np.asarray(c["r_kind"])],
            "amount": c["amount"],
            "sender_balance_before": c["bal_before"],
            "device_id": [f"D{d:07d}" if d else "" for d in device.tolist()],
            "channel": channel,
            "district": DISTRICT_NAMES[np.asarray(c["district"])],
            "is_fraud": c["is_fraud"],
            "fraud_role": c["role"],
            "typology": c["typology"],
            "case_id": c["case"],
            "cell_id": c["cell"],
        }
    )

    nw = w.n_wallets
    mules = sim.fraud.mules
    used = {wid: m for wid, m in mules.items() if m.n_victims > 0 or m.first_fraud_ts < math.inf}

    def mule_col(attr: str, default):
        return [getattr(mules[i], attr) if i in mules else default for i in range(nw)]

    wallets = pd.DataFrame(
        {
            "wallet_id": [party_id(K_WALLET, i) for i in range(nw)],
            "created_at": _when(cfg, w.created_ts[:nw]),
            "district": DISTRICT_NAMES[np.asarray(w.district[:nw])],
            "area_type": np.where(np.asarray(w.area_urban[:nw]) == 1, "urban", "rural"),
            "channel": np.where(np.asarray(w.channel_app[:nw]) == 1, "app", "ussd"),
            "segment": np.asarray(SEGMENTS)[w.segment[:nw]],
            "is_mule": [i in used for i in range(nw)],
            "mule_kind": mule_col("kind", ""),
            "mule_level": mule_col("level", 0),
            "cell_id": mule_col("cell", -1),
            "mule_usable_from": _when(cfg, mule_col("usable_ts", math.inf)),
            "first_fraud_at": _when(cfg, mule_col("first_fraud_ts", math.inf)),
            "last_fraud_at": _when(cfg, mule_col("last_fraud_ts", math.inf)),
            "n_victims": mule_col("n_victims", 0),
            "flagged_at": _when(cfg, mule_col("flagged_ts", math.inf)),
            "flag_reason": mule_col("flag_reason", ""),
        }
    )

    flags = wallets.loc[wallets["flagged_at"].notna(), ["wallet_id", "flagged_at", "flag_reason"]]
    flags = flags.sort_values("flagged_at").reset_index(drop=True)

    n_agents = len(w.agent_district)
    risk_type = np.where(w.agent_colluding, "colluding", np.where(w.agent_farming, "farming", ""))
    agents = pd.DataFrame(
        {
            "agent_id": [party_id(1, a) for a in range(n_agents)],
            "district": DISTRICT_NAMES[np.asarray(w.agent_district)],
            "is_risky": np.asarray(w.agent_colluding) | np.asarray(w.agent_farming),
            "risk_type": risk_type,
        }
    )
    merchants = pd.DataFrame(
        {
            "merchant_id": [party_id(2, m) for m in range(len(w.merchant_district))],
            "district": DISTRICT_NAMES[np.asarray(w.merchant_district)],
            "category": np.asarray(MERCHANT_CATEGORIES)[np.asarray(w.merchant_category)],
        }
    )

    done = [k for k in sim.fraud.cases if k.n_txn > 0]
    cases = pd.DataFrame(
        {
            "case_id": [k.id for k in done],
            "typology": [k.typology for k in done],
            "cell_id": [k.cell for k in done],
            "victim_id": [party_id(K_WALLET, k.victim) for k in done],
            "mule_id": [party_id(K_WALLET, k.mule) for k in done],
            "started_at": _when(cfg, [k.start_ts for k in done]),
            "day": [k.start_ts // DAY for k in done],
            "loss": [k.loss for k in done],
            "n_txn": [k.n_txn for k in done],
        }
    )
    cells = pd.DataFrame(
        {
            "cell_id": [k.id for k in sim.fraud.cells],
            "district": [DISTRICTS[k.district][0] for k in sim.fraud.cells],
            "start_day": [k.start_day for k in sim.fraud.cells],
            "end_day": [k.end_day for k in sim.fraud.cells],
            "typologies": [",".join(k.typologies) for k in sim.fraud.cells],
            "heldout": [k.heldout for k in sim.fraud.cells],
            "agents": [",".join(party_id(1, a) for a in k.agents) for k in sim.fraud.cells],
        }
    )
    return {
        "transactions": txns,
        "wallets": wallets,
        "wallet_flags": flags,
        "agents": agents,
        "merchants": merchants,
        "cases": cases,
        "cells": cells,
    }


def profile(tables: dict[str, pd.DataFrame]) -> dict:
    """Summary statistics, printed after generation and stored next to the data."""
    t, wl, cs = tables["transactions"], tables["wallets"], tables["cases"]
    loss = t[t["fraud_role"].isin(["victim_transfer", "ato_transfer"])]
    by_typ = (
        loss.groupby(["typology", "split"]).agg(txns=("amount", "size"), loss=("amount", "sum"))
    ).reset_index()
    return {
        "transactions": len(t),
        "by_type": t["type"].value_counts().to_dict(),
        "by_split": t["split"].value_counts().to_dict(),
        "fraud_txn_share": round(float(t["is_fraud"].mean()), 5),
        "fraud_txns_by_role": t.loc[t["is_fraud"], "fraud_role"].value_counts().to_dict(),
        "victim_loss_total": float(loss["amount"].sum()),
        "victim_loss_by_typology_split": by_typ.to_dict("records"),
        "cases": len(cs),
        "wallets": len(wl),
        "mule_wallets": int(wl["is_mule"].sum()),
        "mules_by_kind": wl.loc[wl["is_mule"], "mule_kind"].value_counts().to_dict(),
        "mules_flagged": int(wl.loc[wl["is_mule"], "flagged_at"].notna().sum()),
        "risky_agents": tables["agents"]["risk_type"].value_counts().to_dict(),
    }


def generate(cfg: SimConfig, out_dir: Path) -> dict:
    t0 = time.perf_counter()
    sim = Simulation(cfg)
    sim.run()
    tables = build_tables(sim)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(out_dir / f"{name}.parquet", index=False)
    summary = profile(tables)
    meta = {
        "config": {**asdict(cfg), "start": cfg.start.isoformat()},
        "profile": summary,
        "seconds": round(time.perf_counter() - t0, 1),
        "synthetic": True,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the FraudLens synthetic dataset")
    parser.add_argument("--small", action="store_true", help="test-sized world")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--profile",
        choices=PROFILES,
        default=settings.sim_profile,
        help="calibrated: Bangladesh-sourced parameters (env FRAUDLENS_SIM_PROFILE)",
    )
    args = parser.parse_args()
    cfg = SimConfig.small(args.seed) if args.small else SimConfig(seed=args.seed)
    cfg = cfg.with_profile(args.profile)
    name = "small" if args.small else "full"
    if cfg.profile != "default":
        name = f"{name}_{cfg.profile}"  # never overwrite the published dataset
    out = args.out or settings.data_dir / name
    meta = generate(cfg, out)
    print(json.dumps(meta["profile"], indent=2, default=str))
    print(f"wrote {out} in {meta['seconds']}s")


if __name__ == "__main__":
    main()
