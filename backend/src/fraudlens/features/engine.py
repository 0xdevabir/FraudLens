"""Stateful feature engine.

One class computes features for both training and live scoring. Training replays
history in time order; serving feeds it live events. Because both go through
`features()` then `update()`, a feature can only ever see the past, and the
values used in training are exactly the values produced in production.

Only money leaving a wallet is scored (SEND_MONEY, CASH_OUT). Every transaction
type updates state.
"""

from __future__ import annotations

import math
import pickle
from collections import deque
from pathlib import Path
from typing import NamedTuple

NAN = math.nan
HOUR, DAY = 3600.0, 86_400.0
SCORED_TYPES = ("SEND_MONEY", "CASH_OUT")
YOUNG_ACCOUNT_DAYS = 30.0
FAST_EXIT_SECONDS = 1800.0
MIN_AGENT_HISTORY = 20


class Txn(NamedTuple):
    txn_id: int
    ts: float  # epoch seconds
    type: str
    sender_id: str
    sender_type: str
    receiver_id: str
    receiver_type: str
    amount: float
    balance_before: float
    device_id: str
    channel: str
    district: str


FEATURES: tuple[str, ...] = (
    # --- the transaction itself
    "amount",
    "is_cash_out",
    "hour",
    "amount_to_balance",
    "balance_after",
    # --- sender: deviation from own behaviour
    "s_age_days",
    "s_n_money_out",
    "s_amount_z",
    "s_amount_vs_max",
    "s_cnt_1h",
    "s_cnt_24h",
    "s_sum_24h",
    "s_new_recipients_24h",
    "s_secs_since_last_txn",
    "s_secs_since_funding",
    "s_funded_share_1h",
    "s_hour_share",
    "s_new_device",
    "s_device_age_secs",
    "s_device_other_wallets",
    "s_device_flagged",
    "s_away_from_home",
    "s_district_changed",
    # --- sender seen as a receiver: is the sender itself passing money through?
    "s_fan_in_7d",
    "s_in_sum_24h",
    "s_secs_since_last_in",
    "s_out_in_ratio_24h",
    "s_flagged_neighbors",
    # --- the relationship
    "pair_prior_count",
    "pair_reverse_count",
    "pair_same_district",
    # --- recipient wallet (SEND_MONEY only)
    "r_age_days",
    "r_fan_in_24h",
    "r_fan_in_7d",
    "r_in_cnt_24h",
    "r_in_sum_24h",
    "r_in_cnt_7d",
    "r_new_sender_share_7d",
    "r_cross_district_share_7d",
    "r_sender_districts_7d",
    "r_reciprocity",
    "r_dwell_secs",
    "r_fast_exit_share",
    "r_out_in_ratio_24h",
    "r_cashout_share",
    "r_n_initiated",
    "r_n_recipients",
    "r_in_per_day",
    "r_device_other_wallets",
    "r_device_flagged",
    "r_flagged_neighbors",
    # --- agent (CASH_OUT only)
    "a_n_cashouts",
    "a_young_share",
    "a_out_district_share",
    "a_fast_exit_share",
    "a_night_share",
    "a_flagged_customers",
)

# Features describing only the receiving wallet. The mule model uses just these,
# so it can score a wallet without reference to any one sender.
RECIPIENT_FEATURES: tuple[str, ...] = tuple(f for f in FEATURES if f.startswith("r_"))


class WalletState:
    __slots__ = (
        "created_ts", "home", "n_init", "hour_hist", "last_ts", "last_district",
        "devices", "last_device", "n_mo", "sum_mo", "sumsq_mo", "max_mo", "out_events",
        "recipients", "new_recipient_events", "senders", "n_reciprocated", "in_events",
        "n_in", "sum_in", "last_in_ts", "fund_ts", "fund_amount", "fund_agent",
        "dwell", "n_fast_exit", "sum_cashout", "agents_used", "flagged_neighbors",
    )  # fmt: skip

    def __init__(self, created_ts: float, home: str) -> None:
        self.created_ts = created_ts
        self.home = home
        self.n_init = 0
        self.hour_hist = [0] * 24
        self.last_ts = NAN
        self.last_district = ""
        self.devices: dict[str, float] = {}
        self.last_device = ""
        self.n_mo = 0
        self.sum_mo = 0.0
        self.sumsq_mo = 0.0
        self.max_mo = 0.0
        self.out_events: deque[tuple[float, float]] = deque()
        self.recipients: dict[str, int] = {}
        self.new_recipient_events: deque[float] = deque()
        self.senders: dict[str, int] = {}
        self.n_reciprocated = 0
        self.in_events: deque[tuple[float, float, str, bool, str]] = deque()
        self.n_in = 0
        self.sum_in = 0.0
        self.last_in_ts = NAN
        self.fund_ts = NAN
        self.fund_amount = 0.0
        self.fund_agent = ""
        self.dwell = NAN
        self.n_fast_exit = 0
        self.sum_cashout = 0.0
        self.agents_used: dict[str, int] = {}
        self.flagged_neighbors = 0


class AgentState:
    __slots__ = (
        "district", "n_co", "sum_co", "n_ci", "sum_ci", "n_young", "n_out_district",
        "n_fast", "n_night", "n_cycle", "customers", "flagged_customers",
    )  # fmt: skip

    def __init__(self, district: str) -> None:
        self.district = district
        self.n_co = 0
        self.sum_co = 0.0
        self.n_ci = 0
        self.sum_ci = 0.0
        self.n_young = 0
        self.n_out_district = 0
        self.n_fast = 0
        self.n_night = 0
        self.n_cycle = 0
        self.customers: dict[str, int] = {}
        self.flagged_customers = 0


class FeatureEngine:
    def __init__(self) -> None:
        self.wallets: dict[str, WalletState] = {}
        self.agents: dict[str, AgentState] = {}
        self.device_wallets: dict[str, set[str]] = {}
        self.flagged: dict[str, float] = {}
        self.flagged_devices: set[str] = set()
        self.last_ts = 0.0

    # ------------------------------------------------------------ master data

    def register_wallet(self, wallet_id: str, created_ts: float, district: str) -> None:
        w = self.wallets.get(wallet_id)
        if w is None:
            self.wallets[wallet_id] = WalletState(created_ts, district)
        else:
            w.created_ts, w.home = created_ts, district

    def register_agent(self, agent_id: str, district: str) -> None:
        if agent_id not in self.agents:
            self.agents[agent_id] = AgentState(district)

    def _wallet(self, wallet_id: str, ts: float, district: str) -> WalletState:
        w = self.wallets.get(wallet_id)
        if w is None:
            # First sighting of a wallet with no master record: treat it as opened now.
            w = self.wallets[wallet_id] = WalletState(ts, district)
        return w

    def _agent(self, agent_id: str, district: str) -> AgentState:
        a = self.agents.get(agent_id)
        if a is None:
            a = self.agents[agent_id] = AgentState(district)
        return a

    def flag_wallet(self, wallet_id: str, ts: float) -> None:
        """A wallet is confirmed bad. Everything it touched inherits a link to it."""
        if wallet_id in self.flagged:
            return
        self.flagged[wallet_id] = ts
        w = self.wallets.get(wallet_id)
        if w is None:
            return
        for other in w.recipients.keys() | w.senders.keys():
            self.wallets[other].flagged_neighbors += 1
        self.flagged_devices.update(w.devices)
        for agent_id in w.agents_used:
            self.agents[agent_id].flagged_customers += 1

    # --------------------------------------------------------------- features

    @staticmethod
    def _inbound(w: WalletState, ts: float) -> tuple[int, int, int, float, int, float, float, int]:
        """Profile of wallet-to-wallet money received in the last 24 hours and 7 days."""
        events = w.in_events
        while events and events[0][0] <= ts - 7 * DAY:
            events.popleft()
        day_ago = ts - DAY
        senders7: set[str] = set()
        senders24: set[str] = set()
        districts: set[str] = set()
        cnt24, sum24, new7, cross7 = 0, 0.0, 0, 0
        for ets, amount, sender, is_new, district in events:
            senders7.add(sender)
            districts.add(district)
            new7 += is_new
            cross7 += district != w.home
            if ets > day_ago:
                senders24.add(sender)
                cnt24 += 1
                sum24 += amount
        n7 = len(events)
        return (
            len(senders24), len(senders7), cnt24, sum24, n7,
            new7 / n7 if n7 else NAN, cross7 / n7 if n7 else NAN, len(districts),
        )  # fmt: skip

    @staticmethod
    def _out_24h(w: WalletState, ts: float) -> tuple[int, int, float]:
        events = w.out_events
        while events and events[0][0] <= ts - DAY:
            events.popleft()
        hour_ago = ts - HOUR
        cnt1 = sum(1 for ets, _ in events if ets > hour_ago)
        return cnt1, len(events), sum(a for _, a in events)

    def _device_others(self, device: str, wallet_id: str) -> int:
        if not device:
            return 0
        users = self.device_wallets.get(device)
        if not users:
            return 0
        return len(users) - (wallet_id in users)

    def features(self, t: Txn) -> list[float]:
        """Feature vector for a transaction, using only state from before it."""
        if t.type not in SCORED_TYPES:
            raise ValueError(f"{t.type} is not a scored transaction type")
        ts, amount = t.ts, t.amount
        s = self._wallet(t.sender_id, ts, t.district)
        is_cash_out = t.type == "CASH_OUT"
        hour = int(ts % DAY // HOUR)
        bal = t.balance_before

        n = s.n_mo
        if n >= 3:
            mean = s.sum_mo / n
            std = math.sqrt(max(s.sumsq_mo / n - mean * mean, 0.0))
            # Regularised so a customer with near-constant amounts does not explode the score.
            amount_z = (amount - mean) / (std + 0.1 * mean + 1.0)
        else:
            amount_z = NAN
        cnt_1h, cnt_24h, sum_24h = self._out_24h(s, ts)
        recent_new = s.new_recipient_events
        while recent_new and recent_new[0] <= ts - DAY:
            recent_new.popleft()
        since_fund = ts - s.fund_ts
        funded_share = min(s.fund_amount / amount, 5.0) if since_fund <= HOUR else 0.0
        hist = s.hour_hist
        hour_share = (hist[hour - 1] + hist[hour] + hist[(hour + 1) % 24] + 1) / (s.n_init + 8)

        device = t.device_id if t.channel == "app" else ""
        if device:
            first_seen = s.devices.get(device)
            new_device = 1.0 if first_seen is None and s.devices else 0.0
            device_age = ts - first_seen if first_seen is not None else (0.0 if s.devices else NAN)
        else:
            new_device, device_age = 0.0, NAN
        s_in = self._inbound(s, ts)

        row = [
            amount,
            1.0 if is_cash_out else 0.0,
            float(hour),
            amount / bal if bal > 0 else 1.0,
            bal - amount,
            (ts - s.created_ts) / DAY,
            float(n),
            amount_z,
            amount / s.max_mo if s.max_mo > 0 else NAN,
            float(cnt_1h),
            float(cnt_24h),
            sum_24h,
            float(len(recent_new)),
            ts - s.last_ts,
            since_fund,
            funded_share,
            hour_share,
            new_device,
            device_age,
            float(self._device_others(device, t.sender_id)),
            1.0 if device and device in self.flagged_devices else 0.0,
            1.0 if t.district != s.home else 0.0,
            1.0 if s.last_district and t.district != s.last_district else 0.0,
            float(s_in[1]),
            s_in[3],
            ts - s.last_in_ts,
            min((sum_24h + amount) / (s_in[3] + 1.0), 50.0),
            float(s.flagged_neighbors),
        ]

        if is_cash_out:
            a = self._agent(t.receiver_id, t.district)
            row += [
                float(s.agents_used.get(t.receiver_id, 0)),
                NAN,
                1.0 if s.home == a.district else 0.0,
            ]
            row += [NAN] * len(RECIPIENT_FEATURES)
            enough = a.n_co >= MIN_AGENT_HISTORY
            row += [
                float(a.n_co),
                a.n_young / a.n_co if enough else NAN,
                a.n_out_district / a.n_co if enough else NAN,
                a.n_fast / a.n_co if enough else NAN,
                a.n_night / a.n_co if enough else NAN,
                float(a.flagged_customers),
            ]
            return row

        r = self._wallet(t.receiver_id, ts, t.district)
        row += [
            float(s.recipients.get(t.receiver_id, 0)),
            float(r.recipients.get(t.sender_id, 0)),
            1.0 if s.home == r.home else 0.0,
        ]
        row += self.recipient_features(t.receiver_id, ts)
        row += [NAN] * 6
        return row

    def recipient_features(self, wallet_id: str, ts: float) -> list[float]:
        """The receiving wallet's profile at time `ts` (RECIPIENT_FEATURES order)."""
        r = self.wallets[wallet_id]
        fan24, fan7, cnt24, sum24, cnt7, new_share, cross_share, n_districts = self._inbound(r, ts)
        _, _, out_24h = self._out_24h(r, ts)
        age_days = (ts - r.created_ts) / DAY
        n_senders = len(r.senders)
        return [
            age_days,
            float(fan24),
            float(fan7),
            float(cnt24),
            sum24,
            float(cnt7),
            new_share,
            cross_share,
            float(n_districts),
            r.n_reciprocated / n_senders if n_senders else NAN,
            r.dwell,
            r.n_fast_exit / r.n_in if r.n_in else NAN,
            min(out_24h / (sum24 + 1.0), 50.0),
            min(r.sum_cashout / (r.sum_in + 1.0), 5.0),
            float(r.n_init),
            float(len(r.recipients)),
            r.n_in / max(age_days, 1.0),
            float(self._device_others(r.last_device, wallet_id)),
            1.0 if r.last_device and r.last_device in self.flagged_devices else 0.0,
            float(r.flagged_neighbors),
        ]

    # ----------------------------------------------------------------- update

    def update(self, t: Txn) -> None:
        """Apply a completed transaction to state."""
        ts, amount = t.ts, t.amount
        self.last_ts = ts
        if t.sender_type != "wallet":
            # Cash-in from an agent or add-money from a bank: the receiver is funded.
            r = self._wallet(t.receiver_id, ts, t.district)
            r.fund_ts, r.fund_amount = ts, amount
            if t.sender_type == "agent":
                a = self._agent(t.sender_id, t.district)
                a.n_ci += 1
                a.sum_ci += amount
                a.customers[t.receiver_id] = a.customers.get(t.receiver_id, 0) + 1
                r.fund_agent = t.sender_id
            else:
                r.fund_agent = ""
            return

        sid = t.sender_id
        s = self._wallet(sid, ts, t.district)
        hour = int(ts % DAY // HOUR)
        s.n_init += 1
        s.hour_hist[hour] += 1
        s.last_ts = ts
        s.last_district = t.district
        if t.channel == "app" and t.device_id:
            device = t.device_id
            if device not in s.devices:
                s.devices[device] = ts
                self.device_wallets.setdefault(device, set()).add(sid)
                if sid in self.flagged:
                    self.flagged_devices.add(device)
            s.last_device = device

        if t.type not in SCORED_TYPES:
            return
        s.n_mo += 1
        s.sum_mo += amount
        s.sumsq_mo += amount * amount
        if amount > s.max_mo:
            s.max_mo = amount
        s.out_events.append((ts, amount))
        gap = ts - s.last_in_ts  # NaN when nothing was ever received; comparisons are then False
        if gap <= DAY:
            s.dwell = gap if s.dwell != s.dwell else 0.7 * s.dwell + 0.3 * gap
        fast = gap <= FAST_EXIT_SECONDS
        if fast:
            s.n_fast_exit += 1

        if t.type == "SEND_MONEY":
            rid = t.receiver_id
            r = self._wallet(rid, ts, t.district)
            known = rid in s.recipients or rid in s.senders
            if rid not in s.recipients:
                s.new_recipient_events.append(ts)
                if rid in s.senders:
                    s.n_reciprocated += 1
            s.recipients[rid] = s.recipients.get(rid, 0) + 1
            new_sender = sid not in r.senders
            if new_sender and sid in r.recipients:
                r.n_reciprocated += 1
            r.senders[sid] = r.senders.get(sid, 0) + 1
            r.n_in += 1
            r.sum_in += amount
            r.last_in_ts = ts
            r.in_events.append((ts, amount, sid, new_sender, s.home))
            if not known:
                # A new edge in the graph: inherit links to already-flagged wallets.
                if rid in self.flagged:
                    s.flagged_neighbors += 1
                if sid in self.flagged:
                    r.flagged_neighbors += 1
        else:
            aid = t.receiver_id
            a = self._agent(aid, t.district)
            if aid not in s.agents_used and sid in self.flagged:
                a.flagged_customers += 1
            s.agents_used[aid] = s.agents_used.get(aid, 0) + 1
            s.sum_cashout += amount
            a.n_co += 1
            a.sum_co += amount
            a.customers[sid] = a.customers.get(sid, 0) + 1
            a.n_young += (ts - s.created_ts) / DAY < YOUNG_ACCOUNT_DAYS
            a.n_out_district += s.home != a.district
            a.n_fast += fast
            a.n_night += hour < 6
            a.n_cycle += s.fund_agent == aid and ts - s.fund_ts <= HOUR

    # --------------------------------------------------------------- snapshot

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            pickle.dump(self, fh, protocol=pickle.HIGHEST_PROTOCOL)

    @staticmethod
    def load(path: Path) -> FeatureEngine:
        # Snapshots are our own build artifacts; never load one from an untrusted source.
        with path.open("rb") as fh:
            engine = pickle.load(fh)  # noqa: S301
        if not isinstance(engine, FeatureEngine):
            raise TypeError(f"{path} is not a FeatureEngine snapshot")
        return engine
