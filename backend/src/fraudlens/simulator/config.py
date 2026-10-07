"""Simulation parameters. Every number here is a documented synthetic assumption
(see docs/DATA_ASSUMPTIONS.md), not a measurement of any real MFS.

Two profiles exist. "default" is the world every published number comes from.
"calibrated" replaces the parameters that a public Bangladesh source pins down
(DATA_ASSUMPTIONS.md §11, sources in docs/BANGLADESH_CONTEXT.md) and leaves
everything else as it is.
"""

from dataclasses import dataclass, replace
from datetime import datetime

DAY = 86_400
PROFILES = ("default", "calibrated")


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

    profile: str = "default"
    # Median (BDT, before the segment multiplier) of spontaneous send-money and
    # cash-out amounts. The calibrated profile moves them toward Bangladesh Bank's
    # published average ticket sizes.
    send_median: float = 1_500.0
    cash_out_median: float = 3_000.0
    # Share of account-takeover cases kept; the rest are run as impersonation
    # instead. 1.0 leaves the typology mix (and the random stream) untouched.
    ato_keep_share: float = 1.0

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

    def calibrated(self) -> "SimConfig":
        """The same world with every parameter that has a public Bangladesh source
        set from it. Values and sources: DATA_ASSUMPTIONS.md §11."""
        return replace(
            self,
            profile="calibrated",
            # Smallest Bangladesh Bank daily limit from 27 March 2025 (cash-out, Tk 30,000).
            txn_cap=30_000.0,
            # Average P2P ticket Tk 3,552 and cash-out Tk 2,132 (Bangladesh Bank, Oct 2025).
            # The send median puts the simulated send mean at about Tk 3,520. Most cash-outs
            # are balance-driven withdrawals, so the cash-out mean barely moves (§11).
            send_median=2_850.0,
            cash_out_median=1_400.0,
            # 41.2% of personal account holders filed a complaint (TIB 2025).
            report_rate=0.412,
            # Hacking is 12.3% of fraud reports (TIB 2025), half the default ATO share.
            ato_keep_share=0.5,
        )

    def with_profile(self, profile: str) -> "SimConfig":
        if profile not in PROFILES:
            raise ValueError(f"unknown simulator profile {profile!r}; choose from {PROFILES}")
        return self.calibrated() if profile == "calibrated" else self
