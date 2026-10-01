"""Agent risk: how far an agent's cash-out pattern sits from its peers.

Unsupervised on purpose. There are too few confirmed bad agents to learn from, and
an agent review needs a reason a field officer can check ("three times the usual
share of cash-outs within 30 minutes of the money arriving"), not a black-box score.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..features import FeatureEngine

MIN_CASHOUTS = 30  # below this an agent has too little history to compare

# metric -> plain-language description used in agent review reasons
METRICS = {
    "young_share": "cash-outs by accounts under 30 days old",
    "out_district_share": "cash-outs by customers from another district",
    "fast_exit_share": "cash-outs within 30 minutes of the money arriving",
    "night_share": "cash-outs between midnight and 6am",
    "cycle_share": "cash-outs of money this same agent cashed in within the hour",
    "flagged_customer_rate": "customers later confirmed as fraud wallets",
    "avg_cashout": "average cash-out amount",
    "top_customer_share": "cash-outs concentrated in a single customer",
}


def agent_table(engine: FeatureEngine) -> pd.DataFrame:
    """Per-agent behaviour aggregates from the feature engine's state."""
    rows = []
    for agent_id, a in engine.agents.items():
        n = a.n_co

        def share(k: int, n: int = n) -> float:
            return k / n if n else np.nan

        # Largest number of cash-outs any one customer made at this agent.
        top = max((engine.wallets[w].agents_used.get(agent_id, 0) for w in a.customers), default=0)
        rows.append(
            {
                "agent_id": agent_id,
                "district": a.district,
                "n_cashouts": n,
                "n_cashins": a.n_ci,
                "n_customers": len(a.customers),
                "cashout_value": a.sum_co,
                "young_share": share(a.n_young),
                "out_district_share": share(a.n_out_district),
                "fast_exit_share": share(a.n_fast),
                "night_share": share(a.n_night),
                "cycle_share": share(a.n_cycle),
                "flagged_customer_rate": (
                    a.flagged_customers / len(a.customers) if a.customers else np.nan
                ),
                "avg_cashout": a.sum_co / n if n else np.nan,
                "top_customer_share": share(top),
            }
        )
    return pd.DataFrame(rows)


def score_agents(table: pd.DataFrame) -> pd.DataFrame:
    """Add robust peer z-scores, a combined `risk` and the reasons behind it.

    z = (value - peer median) / robust spread. `risk` is the mean of the three
    largest positive z-scores, so one odd metric does not condemn an agent but a
    consistent pattern does.
    """
    out = table.copy()
    eligible = out["n_cashouts"] >= MIN_CASHOUTS
    z = pd.DataFrame(0.0, index=out.index, columns=list(METRICS))
    for metric in METRICS:
        peers = out.loc[eligible, metric].dropna()
        if peers.empty:
            continue
        median = peers.median()
        mad = 1.4826 * (peers - median).abs().median()
        iqr = (peers.quantile(0.75) - peers.quantile(0.25)) / 1.349
        # Floors keep a metric that is zero for almost everyone from producing huge z-scores.
        floor = 0.02 if metric.endswith(("share", "rate")) else 0.05 * abs(median) + 1e-9
        z[metric] = ((out[metric] - median) / max(mad, iqr, floor)).fillna(0.0)
    z[~eligible] = 0.0
    positive = z.clip(lower=0.0, upper=20.0)
    out["risk"] = np.sort(positive.to_numpy(), axis=1)[:, -3:].mean(axis=1)
    out["eligible"] = eligible
    for metric in METRICS:
        out[f"z_{metric}"] = z[metric].round(2)
    out["reasons"] = [
        [m for m in positive.columns[np.argsort(-row)][:3] if row[positive.columns.get_loc(m)] >= 3]
        for row in positive.to_numpy()
    ]
    return out.sort_values("risk", ascending=False).reset_index(drop=True)
