"""A four-provider market on top of the existing dataset, and the consortium experiment.

    uv run python -m fraudlens.consortium.simulate          # backend/data/full
    uv run python -m fraudlens.consortium.simulate --quick  # one seed, for a smoke run

Opt-in and read-only towards everything else: the dataset, the features and the
model versions are used as they are, and the output goes to
`artifacts/consortium/` only.

**The market.** Every wallet of the dataset is given to one of four simulated
providers, and a synthetic wallet number (MSISDN). A share of people hold wallets at
two providers on one SIM; this is applied to mule-cell wallets and to ordinary
customers at the *same* rate, so the experiment does not assume mules multi-home
more than anyone else. Handsets come from the dataset itself: a cell's handset used
by wallets that ended up at different providers is a real cross-provider link.

**The experiment.** Each provider scores receivers with the served mule model, as
today. Once a day it lists its confirmed mules (victim-reported) and its suspected
ones (mule-model alerts) in a signed bundle, through the real protocol in
`hub.py`. At every transfer the receiving provider looks the receiver up in its
partners' bundles. A receiver is alerted when the mule score clears its bar *or*
the consortium signal clears its own. Both bars are chosen on `val_b` (the mule bar
can only rise from the served threshold) so that no more non-mule wallets are
alerted there than with the mule model alone. Everything reported is measured on the
untouched test period.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import settings
from ..features import RECIPIENT_FEATURES, FeatureEngine
from ..models import registry
from ..models.data import load_frame
from ..models.rings import MAX_DEVICE_WALLETS
from . import crypto
from .hub import Hub, Member
from .protocol import SUSPECTED_TTL, iso, signal

PROVIDERS: tuple[tuple[str, str, float], ...] = (
    # Illustrative peer shares for the simulation — not market data.
    # Internal ids stay stable for saved demo artifacts; displays are upay-hackathon safe.
    ("upay", "upay (home)", 0.08),
    ("bkash", "RiverPay (peer, simulated)", 0.55),
    ("nagad", "CityCash (peer, simulated)", 0.25),
    ("rocket", "TapWallet (peer, simulated)", 0.12),
)
KEY_EPOCH = "2026Q1"
SEEDS = (11, 12, 13, 14, 15)
SIM_SHARES = (0.0, 0.25, 0.5)
HEADLINE_SHARE = 0.25
ARMS = {"mule_model_only": (), "device": ("device",), "msisdn": ("msisdn",),
        "msisdn_and_device": ("msisdn", "device")}  # fmt: skip
# A rule fixed in advance instead of tuned on val_b: alert on any receiver that a
# partner has listed as a *confirmed* mule (signal >= the confirmed confidence),
# on top of the served mule model at its served threshold.
RULE_ARMS = {"msisdn_confirmed_rule": ("msisdn",),
             "msisdn_and_device_confirmed_rule": ("msisdn", "device")}  # fmt: skip
ALL_ARMS = {**ARMS, **RULE_ARMS}
POISON_LISTINGS = 500


def out_dir() -> Path:
    return settings.artifacts_dir / "consortium"


def _epoch(series: pd.Series) -> np.ndarray:
    return series.astype("datetime64[us]").astype("int64").to_numpy() / 1e6


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


# ------------------------------------------------------------------ the market


@dataclass
class Market:
    provider: dict[str, str]  # wallet -> provider
    msisdn: dict[str, str]  # wallet -> synthetic wallet number
    shared_sim: dict[str, str]  # wallet -> the wallet it shares a SIM with

    def cell_spread(self, cells: pd.Series) -> dict:
        """How many providers each scam cell's wallets ended up spread over."""
        spread = cells.groupby(cells.values).apply(
            lambda ws: len({self.provider[w] for w in ws.index})
        )
        return {
            "cells": int(len(spread)),
            "on_one_provider": int((spread == 1).sum()),
            "on_two_or_more": int((spread >= 2).sum()),
            "on_all_four": int((spread == 4).sum()),
        }


def build_market(wallets: pd.DataFrame, seed: int, sim_share: float) -> Market:
    rng = np.random.default_rng(seed)
    names = [p[0] for p in PROVIDERS]
    shares = np.array([p[2] for p in PROVIDERS])
    ids = wallets["wallet_id"].sort_values().tolist()
    provider = dict(zip(ids, rng.choice(names, size=len(ids), p=shares), strict=True))

    numbers = set()
    msisdn = {}
    for w in ids:
        while True:
            n = f"01{rng.integers(3, 10)}{rng.integers(0, 10**8):08d}"
            if n not in numbers:
                numbers.add(n)
                msisdn[w] = n
                break

    shared: dict[str, str] = {}
    cell = wallets.set_index("wallet_id")["cell_id"]
    groups = [g.index.tolist() for _, g in cell[cell >= 0].groupby(cell[cell >= 0])]
    groups.append(cell[cell < 0].index.tolist())  # ordinary customers, at the same rate
    for group in groups:
        order = rng.permutation(sorted(group)).tolist()
        for a, b in zip(order[0::2], order[1::2], strict=False):
            if rng.random() >= sim_share:
                continue
            # One SIM holds at most one wallet per provider.
            if provider[b] == provider[a]:
                others = [n for n in names if n != provider[a]]
                weights = np.array([s for n, s in zip(names, shares, strict=True)
                                    if n != provider[a]])  # fmt: skip
                provider[b] = str(rng.choice(others, p=weights / weights.sum()))
            msisdn[b] = msisdn[a]
            shared[a], shared[b] = b, a
    return Market(provider, msisdn, shared)


# ---------------------------------------------------------------- the inputs


@dataclass
class Inputs:
    send: pd.DataFrame  # val_a, val_b and test transfers with the mule score
    wallets: pd.DataFrame
    devices: dict[str, list[tuple[float, str]]]  # wallet -> [(first seen, device)]
    typology: dict[str, str]
    threshold: float
    model_version: str
    folds: dict[str, tuple[float, float]]  # fold -> [start, end) epoch seconds


def load_inputs(data_dir: Path) -> Inputs:
    frame = load_frame(data_dir)
    frame = frame[~frame["ambiguous"] & (frame["type"] == "SEND_MONEY")]
    frame = frame[frame["fold"].isin(("val_a", "val_b", "test"))].copy()
    version = registry.current_version()
    bundle = registry.load(version)
    frame["mule"] = bundle.recipient_risk(frame[list(RECIPIENT_FEATURES)].to_numpy())
    frame["t"] = _epoch(frame["ts"])
    send = frame[["txn_id", "t", "day", "fold", "receiver_id", "amount", "mule", "y_mule",
                  "y_loss"]].sort_values(["t", "txn_id"]).reset_index(drop=True)  # fmt: skip

    wallets = pd.read_parquet(data_dir / "wallets.parquet")
    engine = FeatureEngine.load(data_dir / "engine_after_test.pkl")
    devices: dict[str, list[tuple[float, str]]] = {}
    for wid, state in engine.wallets.items():
        handsets = [
            (ts, d)
            for d, ts in state.devices.items()
            if len(engine.device_wallets.get(d, ())) <= MAX_DEVICE_WALLETS
        ]
        if handsets:
            devices[wid] = sorted(handsets)
    cases = pd.read_parquet(data_dir / "cases.parquet", columns=["mule_id", "typology"])
    typology = cases.groupby("mule_id")["typology"].agg(lambda s: s.mode().iloc[0]).to_dict()
    cfg = json.loads((data_dir / "meta.json").read_text())["config"]
    start = datetime.fromisoformat(cfg["start"]).replace(tzinfo=UTC).timestamp()
    val_mid = (cfg["train_end_day"] + cfg["val_end_day"]) // 2
    days = {"val_a": (cfg["train_end_day"], val_mid), "val_b": (val_mid, cfg["val_end_day"]),
            "test": (cfg["val_end_day"], cfg["days"])}  # fmt: skip
    folds = {k: (start + a * 86_400.0, start + b * 86_400.0) for k, (a, b) in days.items()}
    return Inputs(send, wallets, devices, typology,
                  float(bundle.manifest["mule_wallet_threshold"]), version, folds)  # fmt: skip


# ------------------------------------------------------------- measured priors


def measured_confidences(inp: Inputs) -> dict:
    """What a listing is worth, measured before the test period starts.

    Confirmed: the share of wallets reported by victims before the test period
    that really received fraud money. Suspected: the wallet-level precision of the
    mule model's alerts on val_b.
    """
    test_start = inp.folds["test"][0]
    w = inp.wallets
    flagged = w[w["flagged_at"].notna() & (_epoch(w["flagged_at"].fillna(pd.Timestamp(0)))
                                           < test_start)]  # fmt: skip
    confirmed = float(flagged["first_fraud_at"].notna().mean())
    vb = inp.send[inp.send["fold"] == "val_b"]
    by = vb.groupby("receiver_id").agg(score=("mule", "max"), y=("y_mule", "max"))
    alerted = by[by["score"] >= inp.threshold]
    suspected = float(alerted["y"].mean())
    return {
        "confirmed": round(confirmed, 3),
        "confirmed_basis": f"{len(flagged)} wallets reported before the test period",
        "suspected": round(suspected, 3),
        "suspected_basis": f"{len(alerted)} wallets alerted by the mule model on val_b",
    }


# -------------------------------------------------------------------- the run


def _identifiers(inp: Inputs, market: Market, wallet: str, at: float) -> list[tuple[str, str]]:
    ids = [("msisdn", market.msisdn[wallet])]
    ids += [("device", d) for ts, d in inp.devices.get(wallet, ()) if ts <= at]
    return ids


def run_consortium(
    inp: Inputs,
    market: Market,
    conf: dict,
    hub_key: crypto.OprfKey,
    token_cache: dict,
    mapper=map,
    poison: list[str] | None = None,
    keep: bool = False,
) -> dict:
    """Day by day: list, publish, import, then look up every transfer of that day.

    Returns the consortium matches for every val_b and test transfer, and (with
    `keep`) the hub and members for the demo state.
    """
    t_begin, t_end = inp.folds["val_b"][0], inp.folds["test"][1]
    hub = Hub(KEY_EPOCH, _dt(inp.folds["val_a"][0]), key=hub_key)
    members: dict[str, Member] = {}
    for name, display, _ in PROVIDERS:
        m = Member(name, hub)
        m._tokens = token_cache  # see main(): every member's own tokens, made once
        hub.register(name, display, m.key.public, _dt(inp.folds["val_a"][0]))
        members[name] = m

    w = inp.wallets.set_index("wallet_id")
    flagged_at = _epoch(w["flagged_at"].fillna(pd.Timestamp(0)))
    flagged = {wid: ts for wid, ts, ok in zip(w.index, flagged_at, w["flagged_at"].notna(),
                                              strict=True) if ok}  # fmt: skip
    crossings = inp.send[inp.send["mule"] >= inp.threshold]
    crossed = list(zip(crossings["t"], crossings["receiver_id"], crossings["mule"], strict=True))

    send = inp.send[(inp.send["t"] >= t_begin) & (inp.send["t"] < t_end)]
    matches: list[list] = [[] for _ in range(len(send))]
    i_cross, prev = 0, t_begin - SUSPECTED_TTL.total_seconds()
    rows = list(zip(send["t"], send["receiver_id"], strict=True))
    i_row = 0
    day = t_begin
    while day < t_end:
        now = _dt(day)
        # Confirmed: reported by victims, listed with every handset seen so far.
        for wid, ts in flagged.items():
            if ts <= day:
                p = market.provider[wid]
                members[p].list_wallet(
                    _identifiers(inp, market, wid, day),
                    "confirmed",
                    conf["confirmed"],
                    now,
                    typology=inp.typology.get(wid, "unknown"),
                    first_seen=_dt(ts),
                )
        # Suspected: the provider's own mule model alerted since the last bundle.
        while i_cross < len(crossed) and crossed[i_cross][0] <= day:
            ts, wid, _score = crossed[i_cross]
            i_cross += 1
            if ts > prev:
                p = market.provider[wid]
                members[p].list_wallet(_identifiers(inp, market, wid, ts), "suspected",
                                       conf["suspected"], _dt(ts))  # fmt: skip
        if poison and day == inp.folds["test"][0]:
            # A dishonest member lists innocent customers' numbers as confirmed mules.
            members["upay"].list_wallet([("msisdn", market.msisdn[v]) for v in poison],
                                        "confirmed", conf["confirmed"], now)  # fmt: skip
        prev = day
        for m in members.values():
            m.publish(now, conf["suspected"], mapper)
        for m in members.values():
            m.sync(now)
        nxt = day + 86_400.0
        while i_row < len(rows) and rows[i_row][0] < nxt:
            t, wid = rows[i_row]
            home = members[market.provider[wid]]
            ids = _identifiers(inp, market, wid, t)
            tokens = home.tokenize(ids, _dt(t))
            found = home.ledger.lookup(
                [(k, tok) for (k, _), tok in zip(ids, tokens, strict=True)], _dt(t)
            )
            matches[i_row] = found
            i_row += 1
        day = nxt
    out = {"send": send.reset_index(drop=True), "matches": matches}
    if keep:
        out.update(hub=hub, members=members)
    return out


# ----------------------------------------------------------------- evaluation


def _wallet_scores(send: pd.DataFrame, score: np.ndarray) -> pd.DataFrame:
    return (
        send.assign(score=score)
        .groupby("receiver_id")
        .agg(score=("score", "max"), y=("y_mule", "max"))
    )


def _fused(base: np.ndarray, c: np.ndarray, t_mule: float, t_cons: float) -> np.ndarray:
    """Alert when the mule model or the consortium signal clears its own bar (>= 1)."""
    return np.maximum(base / t_mule, c / t_cons)


def _choose_thresholds(
    send_b: pd.DataFrame, base: np.ndarray, c: np.ndarray, t0: float, allowed_fp: int
) -> tuple[float, float]:
    """On val_b only: the (mule, consortium) bars that catch the most mule wallets
    while alerting no more non-mule wallets than the mule model alone does.

    The mule bar can only go up from the served threshold; the consortium bar is
    one of the signal levels actually seen (or off). Ties keep the served bar.
    """
    by_base = _wallet_scores(send_b, base)
    above = by_base.loc[by_base["score"] >= t0, "score"].to_numpy()
    t_mules = sorted({t0, *np.quantile(above, np.linspace(0, 1, 21)).tolist()} if len(above)
                     else {t0})  # fmt: skip
    t_conss = sorted({float(v) for v in np.unique(np.round(c[c > 0], 6))}) + [np.inf]
    best = (-1, 0, np.inf, t0, np.inf)
    for tm in t_mules:
        for tc in t_conss:
            by = _wallet_scores(send_b, _fused(base, c, tm, tc))
            alerted = by["score"] >= 1.0
            fp = int((alerted & (by["y"] == 0)).sum())
            tp = int((alerted & (by["y"] == 1)).sum())
            key = (tp, -(tm - t0), -fp)
            if fp <= allowed_fp and key > best[:3]:
                best = (*key, tm, tc)
    return best[3], best[4]


def _timing(send: pd.DataFrame, score: np.ndarray, thr: float, flagged: dict) -> dict:
    """For each mule wallet in the period: when was it first alerted, and at what cost."""
    df = send.assign(score=score)
    mules = df[df["receiver_id"].isin(df.loc[df["y_mule"] == 1, "receiver_id"].unique())]
    detected, before_any, victims_before, taka_before, ahead = 0, 0, [], 0.0, []
    missed_taka = 0.0
    for wid, rows in mules.groupby("receiver_id", sort=False):
        hit = rows["score"].to_numpy() >= thr
        loss = rows["y_loss"].to_numpy()
        amount = rows["amount"].to_numpy()
        if not hit.any():
            missed_taka += float(amount[loss == 1].sum())
            continue
        first = int(np.argmax(hit))
        detected += 1
        n_before = int(loss[:first].sum())
        victims_before.append(n_before)
        before_any += n_before == 0
        taka_before += float(amount[:first][loss[:first] == 1].sum())
        if wid in flagged:
            ahead.append((flagged[wid] - rows["t"].iloc[first]) / 3600)
    return {
        "mule_wallets": int(mules["receiver_id"].nunique()),
        "detected": detected,
        "detected_before_any_victim_paid": before_any,
        "victim_transfers_before_detection": int(sum(victims_before)),
        "taka_paid_to_detected_mules_before_detection": round(taka_before),
        "taka_paid_to_undetected_mules": round(missed_taka),
        "median_hours_ahead_of_victim_report": round(float(np.median(ahead)), 1) if ahead else None,
    }


def evaluate(inp: Inputs, run: dict, flagged: dict, cells: set[str], confirmed: float) -> dict:
    send, matches = run["send"], run["matches"]
    is_b = (send["fold"] == "val_b").to_numpy()
    is_t = (send["fold"] == "test").to_numpy()
    base = send["mule"].to_numpy()
    out = {}
    base_b = _wallet_scores(send[is_b], base[is_b])
    allowed = int(((base_b["score"] >= inp.threshold) & (base_b["y"] == 0)).sum())
    neg_b = int((base_b["y"] == 0).sum())
    detected_by_base: set[str] = set()
    for arm, kinds in ALL_ARMS.items():
        c = np.array([signal([m for m in ms if m.kind in kinds]) for ms in matches])
        if not kinds:
            t_mule, t_cons = inp.threshold, np.inf
        elif arm in RULE_ARMS:
            t_mule, t_cons = inp.threshold, confirmed
        else:
            t_mule, t_cons = _choose_thresholds(
                send[is_b], base[is_b], c[is_b], inp.threshold, allowed
            )
        score, thr = _fused(base, c, t_mule, t_cons), 1.0
        by = _wallet_scores(send[is_t], score[is_t])
        alerted = by["score"] >= thr
        tp = alerted & (by["y"] == 1)
        fp = alerted & (by["y"] == 0)
        caught = set(by.index[tp])
        if not kinds:
            detected_by_base = caught
        matched = c[is_t] > 0
        out[arm] = {
            "mule_threshold": round(t_mule, 4),
            "consortium_threshold": None if np.isinf(t_cons) else round(t_cons, 4),
            "val_b_fpr": round(
                float(
                    (
                        (_wallet_scores(send[is_b], score[is_b])["score"] >= thr)
                        & (base_b["y"] == 0)
                    ).sum()
                )
                / neg_b,
                5,
            ),  # fmt: skip
            "wallets_alerted": int(alerted.sum()),
            "mule_wallets": int(by["y"].sum()),
            "recall": round(float(tp.sum() / by["y"].sum()), 4),
            "precision": round(float(tp.sum() / max(alerted.sum(), 1)), 4),
            "false_alerts": int(fp.sum()),
            "fpr": round(float(fp.sum() / (by["y"] == 0).sum()), 5),
            "false_alerts_that_are_ring_wallets": int(len(set(by.index[fp]) & cells)),
            "mules_found_only_with_consortium": len(caught - detected_by_base),
            "transfers_with_a_partner_match": int(matched.sum()),
            "timing": _timing(send[is_t], score[is_t], thr, flagged),
        }
    return out


def _summary(per_seed: list[dict]) -> dict:
    """Mean, min and max over seeds of every number in the arm results."""
    out: dict = {}
    for arm in per_seed[0]:
        out[arm] = {}
        flat = [_flatten(r[arm]) for r in per_seed]
        for key in flat[0]:
            vals = [f[key] for f in flat if f[key] is not None]
            if not vals:
                out[arm][key] = None
                continue
            out[arm][key] = {"mean": round(float(np.mean(vals)), 4), "min": min(vals),
                             "max": max(vals)}  # fmt: skip
    return out


def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flatten(v, f"{prefix}{k}."))
        else:
            out[prefix + k] = v
    return out


# ---------------------------------------------------------------------- main


def _oprf_timing(key: crypto.OprfKey, n: int = 200) -> dict:
    """Measured cost of one blinded token on this machine, client and hub sides."""
    from .protocol import group_element

    elements = [group_element("msisdn", f"0171{i:07d}") for i in range(n)]
    t0 = time.perf_counter()
    blinded = [crypto.blind(e) for e in elements]
    t1 = time.perf_counter()
    evaluated = [key.evaluate(b) for b, _ in blinded]
    t2 = time.perf_counter()
    out = [crypto.unblind(e, r, key.public) for e, (_, r) in zip(evaluated, blinded, strict=True)]
    t3 = time.perf_counter()
    assert all(o == pow(e, key.secret, crypto.P) for o, e in zip(out, elements, strict=True))
    return {
        "samples": n,
        "client_ms_per_token": round((t1 - t0 + t3 - t2) / n * 1000, 2),
        "hub_ms_per_token": round((t2 - t1) / n * 1000, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate the cross-provider consortium")
    parser.add_argument("--data", type=Path, default=settings.data_dir / "full")
    parser.add_argument("--quick", action="store_true", help="one seed, headline share only")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    t0 = time.perf_counter()
    inp = load_inputs(args.data)
    conf = measured_confidences(inp)
    print("model", inp.model_version, "| mule threshold", round(inp.threshold, 4), "|", conf)
    w = inp.wallets.set_index("wallet_id")
    flagged = {wid: ts for wid, ts, ok in zip(w.index, _epoch(w["flagged_at"].fillna(
        pd.Timestamp(0))), w["flagged_at"].notna(), strict=True) if ok}  # fmt: skip
    cell = w["cell_id"]
    cells = set(cell.index[cell >= 0])

    hub_key = crypto.OprfKey.generate(KEY_EPOCH)
    seeds = SEEDS[:1] if args.quick else SEEDS
    shares = (HEADLINE_SHARE,) if args.quick else SIM_SHARES
    token_cache: dict = {}
    results: dict = {}
    timing = _oprf_timing(hub_key)
    t_tok = 0.0
    demo = None
    with ProcessPoolExecutor(args.workers) as pool:

        def mapper(fn, items):
            items = list(items)
            return pool.map(fn, items, chunksize=max(1, len(items) // (args.workers * 4)))

        for share in shares:
            per_seed = []
            for seed in seeds:
                market = build_market(inp.wallets, seed, share)
                # Each provider tokenises its own customers' identifiers through the
                # blinded OPRF once; the cache is shared because tokens do not depend on
                # who asked, and the experiment re-runs the market many times.
                tt = time.perf_counter()
                _bootstrap_tokens(inp, market, hub_key, token_cache, mapper)
                t_tok += time.perf_counter() - tt
                keep = share == HEADLINE_SHARE and seed == seeds[0]
                run = run_consortium(inp, market, conf, hub_key, token_cache, mapper, keep=keep)
                res = evaluate(inp, run, flagged, cells, conf["confirmed"])
                res["cells_spread"] = market.cell_spread(cell[cell >= 0])
                per_seed.append(res)
                if keep:
                    demo = (market, run)
                print(f"share {share} seed {seed}:",
                      {a: (r["recall"], r["fpr"], r["timing"]["detected_before_any_victim_paid"])
                       for a, r in res.items() if a in ALL_ARMS})  # fmt: skip
            spread = [r.pop("cells_spread") for r in per_seed]
            results[f"{share:.2f}"] = {
                "summary": _summary(per_seed),
                "per_seed": per_seed,
                "cells_spread": spread,
            }

        # Poisoning: a dishonest member lists 500 innocent customers as confirmed mules.
        market = build_market(inp.wallets, seeds[0], HEADLINE_SHARE)
        rng = np.random.default_rng(seeds[0])
        innocent = sorted(set(w.index[(w["cell_id"] < 0) & w["first_fraud_at"].isna()]))
        poison = rng.choice(innocent, size=POISON_LISTINGS, replace=False).tolist()
        _bootstrap_tokens(inp, market, hub_key, token_cache, mapper, extra=poison)
        attacked = evaluate(
            inp,
            run_consortium(inp, market, conf, hub_key, token_cache, mapper, poison=poison),
            flagged,
            cells,
            conf["confirmed"],
        )

    clean = results[f"{HEADLINE_SHARE:.2f}"]["per_seed"][0]
    report = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model_version": inp.model_version,
        "mule_wallet_threshold": inp.threshold,
        "providers": [{"name": n, "display": d, "share": s} for n, d, s in PROVIDERS],
        "seeds": list(seeds),
        "sim_shares": list(shares),
        "headline_share": HEADLINE_SHARE,
        "listing_confidence": conf,
        "periods": {k: [iso(_dt(a)), iso(_dt(b))] for k, (a, b) in inp.folds.items()},
        "results_by_shared_sim_rate": results,
        "poisoning": {
            "listings": POISON_LISTINGS,
            "by": "upay",
            "seed": seeds[0],
            "clean": {a: _pick(clean[a]) for a in ALL_ARMS},
            "attacked": {a: _pick(attacked[a]) for a in ALL_ARMS},
        },
        "cost": {
            "oprf": timing,
            "tokens_made": len(token_cache),
            "seconds_making_tokens": round(t_tok, 1),
        },
        "seconds": round(time.perf_counter() - t0, 1),
    }
    out = out_dir()
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2))
    if demo is not None:
        from .demo import save_demo

        save_demo(out, inp, demo[0], demo[1], hub_key, report)
    print(json.dumps({k: report[k] for k in ("listing_confidence", "cost", "seconds")}, indent=1))
    print(json.dumps(results[f"{HEADLINE_SHARE:.2f}"]["summary"]["msisdn_and_device"], indent=1))


def _pick(r: dict) -> dict:
    keys = ("recall", "false_alerts", "fpr", "mule_threshold", "consortium_threshold",
            "wallets_alerted")  # fmt: skip
    return {k: r[k] for k in keys}


def _bootstrap_tokens(
    inp: Inputs, market: Market, key: crypto.OprfKey, cache: dict, mapper, extra=()
) -> None:
    """Each provider tokenises the identifiers of its own customers (blinded)."""
    hub = Hub(KEY_EPOCH, _dt(inp.folds["val_a"][0]), key=key)
    wanted: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for wid, p in market.provider.items():
        wanted[p].append(("msisdn", market.msisdn[wid]))
        wanted[p] += [("device", d) for _, d in inp.devices.get(wid, ())]
    for wid in extra:
        wanted["upay"].append(("msisdn", market.msisdn[wid]))
    for p, pairs in wanted.items():
        m = Member(p, hub)
        m._tokens = cache
        hub.register(p, p, m.key.public, _dt(inp.folds["val_a"][0]), quota_per_day=10**6)
        m.tokenize(pairs, _dt(inp.folds["val_a"][0]), mapper)


if __name__ == "__main__":
    main()
