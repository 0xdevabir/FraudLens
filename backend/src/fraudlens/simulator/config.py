"""Simulation parameters. Every number here is a documented synthetic assumption
(see docs/DATA_ASSUMPTIONS.md), not a measurement of any real MFS."""

from dataclasses import dataclass
from datetime import datetime

DAY = 86_400


@dataclass(frozen=True)
class SimConfig:
    seed: int = 7
    start: datetime = datetime(2026, 1, 1)
    days: int = 120
    n_customers: int = 20_000
    n_agents: int = 600
    n_merchants: int = 800

    # Time-based split on day index: train [0, train_end), val [train_end, val_end),
    # test [val_end, days).
    train_end_day: int = 75
    val_end_day: int = 95
    # Eid al-Fitr 2026 falls around 21 March, which is day 79 from 1 January.
    eid_day: int = 79

    n_cells: int = 16
    # Cells running the held-out typology. They start on val_end_day, so the
    # typology never appears in train or validation data.
    n_heldout_cells: int = 3
    cases_per_cell_day: float = 2.2
    # Share of scam victims who report, which is what eventually flags a mule.
    report_rate: float = 0.5

    txn_cap: float = 25_000.0
    max_mule_wallets: int = 3_000
    farming_agent_share: float = 0.015

    def split_of(self, day: int) -> str:
        if day < self.train_end_day:
            return "train"
        if day < self.val_end_day:
            return "val"
        return "test"

    @classmethod
    def small(cls, seed: int = 7) -> "SimConfig":
        """A small world for unit tests: same mechanics, runs in a few seconds."""
        return cls(
            seed=seed,
            days=40,
            n_customers=1_500,
            n_agents=60,
            n_merchants=80,
            train_end_day=24,
            val_end_day=31,
            eid_day=20,
            n_cells=5,
            n_heldout_cells=1,
            max_mule_wallets=600,
        )
