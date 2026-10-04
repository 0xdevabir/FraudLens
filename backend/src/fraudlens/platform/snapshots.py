"""Periodic snapshots of the scorer's feature state, so a restart does not replay everything.

The state a scorer holds in memory is the baseline (the dataset's end-of-history snapshot)
plus every transaction and flag applied since, in `applied_seq` order. Rebuilding replays all
of them, so recovery time grows with the number of transactions. A live snapshot is that same
state saved at a known `seq`; recovery then replays only what came after.

A snapshot is only trusted when it still matches the database it was taken from: the same
baseline file, and the same number of applied transactions and flags up to its `seq`. After a
database reset, a restore, or a rebuilt baseline the counts or the baseline stamp differ and the
snapshot is ignored (and deleted), so the worst case is the slow, correct path.

Snapshots are our own artifacts, written next to the dataset; never load one from elsewhere.
"""

from __future__ import annotations

import logging
import os
import pickle
import time
from dataclasses import dataclass
from pathlib import Path

from ..features import FeatureEngine

log = logging.getLogger(__name__)

LIVE_SNAPSHOT = "engine_live.pkl"
FORMAT = 1


@dataclass(frozen=True)
class Meta:
    seq: int  # the scorer's log position: everything up to here is in the state
    applied: int  # transactions with applied_seq <= seq
    flags: int  # wallet flags with applied_seq <= seq
    baseline: tuple[int, int]  # size and mtime of the baseline snapshot it grew from
    taken_at: float


def stamp(baseline: Path) -> tuple[int, int]:
    info = baseline.stat()
    return info.st_size, info.st_mtime_ns


def write(path: Path, engine: FeatureEngine, meta: Meta) -> float:
    """Save atomically (a crash mid-write leaves the previous snapshot). Returns seconds taken."""
    started = time.perf_counter()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as fh:
        pickle.dump({"format": FORMAT, "meta": meta, "engine": engine}, fh, protocol=5)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return time.perf_counter() - started


def read(path: Path) -> tuple[FeatureEngine, Meta] | None:
    """The snapshot at `path`, or None if there is none or it cannot be used."""
    if not path.exists():
        return None
    try:
        with path.open("rb") as fh:
            blob = pickle.load(fh)  # noqa: S301  (our own file)
        engine, meta = blob["engine"], blob["meta"]
        if blob.get("format") != FORMAT or not isinstance(engine, FeatureEngine):
            raise TypeError("not a current live snapshot")
        if not isinstance(meta, Meta):
            raise TypeError("no metadata")
    except Exception:
        log.warning("live snapshot %s is unreadable; ignoring it", path, exc_info=True)
        return None
    return engine, meta


def discard(path: Path) -> None:
    path.unlink(missing_ok=True)
    path.with_suffix(".tmp").unlink(missing_ok=True)
