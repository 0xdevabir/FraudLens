"""Discrete-event simulation of wallet activity.

Legitimate behaviour is scheduled a day at a time; reactions (top-ups, cash-outs
after a remittance, mule exits) are pushed onto the same event queue, so the
output is strictly chronological and balances never go negative.
"""

from __future__ import annotations

import heapq
import math
import random
from datetime import timedelta

import numpy as np

from .config import DAY, SimConfig
from .world import (
    AMOUNT_SCALE,
    BASE_RATES,
    DISTRICTS,
    GARMENT,
    N_BILLERS,
    N_TELCOS,
    RURAL,
    SELLER,
    World,
    build_world,
)

SEND, PAYMENT, RECHARGE, BILL_PAY, CASH_OUT, CASH_IN, ADD_MONEY = range(7)
TYPE_NAMES = (
    "SEND_MONEY",
    "PAYMENT",
    "RECHARGE",
    "BILL_PAY",
    "CASH_OUT",
    "CASH_IN",
    "ADD_MONEY",
)
K_WALLET, K_AGENT, K_MERCHANT, K_TELCO, K_BILLER, K_BANK = range(6)
KIND_NAMES = ("wallet", "agent", "merchant", "telco", "biller", "bank")

SPONTANEOUS = (SEND, PAYMENT, RECHARGE, BILL_PAY, CASH_OUT, CASH_IN)
MIN_AMOUNT = {SEND: 50, PAYMENT: 20, RECHARGE: 10, BILL_PAY: 50, CASH_OUT: 50}
RECHARGE_AMOUNTS = np.array([20, 30, 50, 100, 200, 500])
RECHARGE_P = np.array([0.20, 0.15, 0.30, 0.22, 0.10, 0.03])

# Share of activity by hour for the 20% of transactions not tied to a customer's own rhythm.
_HOUR_W = np.array(
    [1, 0.5, 0.3, 0.3, 0.4, 0.8, 2, 4, 6, 8, 9, 9, 9, 8, 8, 8, 9, 10, 11, 11, 9, 7, 4, 2]
)
GLOBAL_HOUR_P = _HOUR_W / _HOUR_W.sum()

VICTIM_ROLES = ("victim_transfer", "ato_transfer")
INF = float("inf")


class Intent:
    """A transaction someone is about to attempt. Amount is fixed, or a fraction
    of the sender's balance at the moment it executes."""

    __slots__ = (
        "typ", "wallet", "other", "amount", "frac", "topup", "retried",
        "device", "district", "case", "role", "typology", "cell", "fraud",
    )  # fmt: skip

    def __init__(
        self,
        typ: int,
        wallet: int,
        other: int | None = None,
        amount: float = 0.0,
        *,
        frac: float | None = None,
        topup: bool = False,
        device: int | None = None,
        district: int | None = None,
        case: int = -1,
        role: str = "",
        typology: str = "",
        cell: int = -1,
        fraud: bool = False,
    ) -> None:
        self.typ = typ
        self.wallet = wallet  # the customer wallet: sender, or receiver for cash-in/add-money
        self.other = other  # counterparty id; None means "pick at execution time"
        self.amount = amount
        self.frac = frac
        self.topup = topup
        self.retried = False
        self.device = device
        self.district = district
        self.case = case
        self.role = role
        self.typology = typology
        self.cell = cell
        self.fraud = fraud


class Simulation:
    def __init__(self, cfg: SimConfig) -> None:
        from .fraud import FraudPlanner  # local import: fraud.py imports this module

        self.cfg = cfg
        self.np_rng = np.random.default_rng(cfg.seed)
        self.rng = random.Random(cfg.seed)
        self.w: World = build_world(cfg, self.np_rng, self.rng)
        self._heap: list[tuple[int, int, object]] = []
        self._seq = 0
        self.cols: dict[str, list] = {
            k: []
            for k in (
                "ts", "type", "s_kind", "s", "r_kind", "r", "amount", "bal_before",
                "device", "app", "district", "is_fraud", "role", "typology", "case", "cell",
            )
        }  # fmt: skip
        self._pending_cashout: set[int] = set()
        self.fraud = FraudPlanner(self)

    # ------------------------------------------------------------------ queue

    def push(self, ts: float, item: object) -> None:
        self._seq += 1
        heapq.heappush(self._heap, (int(ts), self._seq, item))

    def run(self) -> None:
        self.run_days(0, self.cfg.days)

    def run_days(self, start: int, stop: int) -> None:
        """Simulate days [start, stop). Called in order, it continues where it stopped,
        which is how the adversary experiment steps the world a round at a time."""
        for day in range(start, stop):
            self._schedule_day(day)
            self.fraud.schedule_day(day)
            end = (day + 1) * DAY
            heap = self._heap
            while heap and heap[0][0] < end:
                ts, _, item = heapq.heappop(heap)
                if isinstance(item, Intent):
                    self._execute(ts, item)
                else:
                    item(ts)  # a scheduled callback, e.g. a mule being flagged

    # ------------------------------------------------------------- scheduling

    def _multipliers(self, day: int) -> np.ndarray:
        """Per-type volume multiplier for the day (Eid, Friday, month start)."""
        m = np.ones(7)
        d = day - self.cfg.eid_day
        if -10 <= d <= -1:
            m[[SEND, PAYMENT, CASH_OUT, CASH_IN]] *= 1.2 + 0.08 * (d + 10)
        elif 0 <= d <= 2:
            m[[SEND, PAYMENT, CASH_OUT, CASH_IN]] *= 0.6
        date = self.cfg.start + timedelta(days=day)
        if date.weekday() == 4:
            m[[PAYMENT, BILL_PAY]] *= 0.85
        if date.day <= 7:
            m[SEND] *= 1.25
            m[BILL_PAY] *= 1.5
        return m

    def _times(self, idx: np.ndarray, t0: int) -> np.ndarray:
        n = len(idx)
        hours = self.np_rng.normal(self.w.peak_hour[idx], 2.8)
        g = self.np_rng.random(n) < 0.2
        k = int(g.sum())
        hours[g] = self.np_rng.choice(24, size=k, p=GLOBAL_HOUR_P) + self.np_rng.random(k)
        return t0 + (np.mod(hours, 24.0) * 3600).astype(np.int64)

    def _amounts(self, idx: np.ndarray, median: float, sigma: float, lo: float) -> np.ndarray:
        scale = AMOUNT_SCALE[self.w.segment[idx]]
        a = median * scale * np.exp(sigma * self.np_rng.standard_normal(len(idx)))
        a = np.clip(a, lo, self.cfg.txn_cap)
        # People mostly send round numbers.
        step = np.where(a >= 3000, 500, np.where(a >= 300, 100, 10))
        step = np.where(self.np_rng.random(len(idx)) < 0.7, step, 10)
        return np.maximum(np.round(a / step) * step, lo)

    def _schedule_day(self, day: int) -> None:
        w, cfg, rng = self.w, self.cfg, self.rng
        n = w.n_customers
        t0 = day * DAY
        active = w.created_np < t0
        mult = self._multipliers(day)
        seg = w.segment[:n]

        for k, typ in enumerate(SPONTANEOUS):
            lam = BASE_RATES[seg, k] * w.activity * mult[typ] * active
            idx = np.repeat(np.arange(n), self.np_rng.poisson(lam))
            if len(idx) == 0:
                continue
            times = self._times(idx, t0).tolist()
            if typ == SEND:
                amounts = self._amounts(idx, 1500, 0.9, 50)
            elif typ == PAYMENT:
                amounts = self._amounts(idx, 450, 0.8, 20)
            elif typ == RECHARGE:
                amounts = self.np_rng.choice(RECHARGE_AMOUNTS, size=len(idx), p=RECHARGE_P)
            elif typ == BILL_PAY:
                amounts = self._amounts(idx, 900, 0.6, 100)
            elif typ == CASH_OUT:
                amounts = self._amounts(idx, 3000, 0.7, 500)
            else:
                amounts = self._amounts(idx, 2500, 0.7, 500)
            topup = typ in (SEND, PAYMENT, RECHARGE, BILL_PAY)
            for i, t, a in zip(idx.tolist(), times, amounts.tolist(), strict=True):
                other = self._pick_contact(i, t) if typ == SEND else None
                self.push(t, Intent(typ, i, other, a, topup=topup))

        date = cfg.start + timedelta(days=day)
        dom = date.day

        # Salary and disbursement, then what people do with it.
        for i in np.flatnonzero((w.salary_dom == dom) & active).tolist():
            t = t0 + rng.randint(9 * 3600, 18 * 3600)
            salary = float(w.salary_amount[i])
            self.push(t, Intent(ADD_MONEY, i, None, salary))
            fam = int(w.family[i])
            if fam >= 0:
                share = round(salary * rng.uniform(0.3, 0.6) / 500) * 500
                self.push(
                    t + rng.randint(1800, 2 * DAY), Intent(SEND, i, fam, min(share, cfg.txn_cap))
                )
            if seg[i] == GARMENT:
                self.push(
                    t + rng.randint(2 * 3600, 3 * DAY),
                    Intent(CASH_OUT, i, frac=rng.uniform(0.5, 0.9)),
                )
            else:
                self.push(
                    t + rng.randint(DAY, 12 * DAY), Intent(CASH_OUT, i, frac=rng.uniform(0.2, 0.4))
                )
        if 3 <= dom <= 8:
            rent_day = np.flatnonzero((w.landlord >= 0) & active & ((np.arange(n) % 6) + 3 == dom))
            for i in rent_day.tolist():
                rent = float(rng.choice((8000, 10000, 12000, 15000, 18000, 20000)))
                t = t0 + rng.randint(9 * 3600, 21 * 3600)
                self.push(t, Intent(SEND, i, int(w.landlord[i]), rent, topup=True))
        for i in np.flatnonzero((w.allowance_dom == dom) & active).tolist():
            g = int(w.guardian[i])
            if w.created_ts[g] < t0:
                amt = float(rng.randrange(3000, 8500, 500))
                self.push(
                    t0 + rng.randint(9 * 3600, 21 * 3600), Intent(SEND, g, i, amt, topup=True)
                )

        # F-commerce sellers: paid by strangers nationwide, cash out once a day or so.
        seller_ids = np.array(w.sellers)
        if len(seller_ids):
            live = seller_ids[w.created_np[seller_ids] < t0]
            orders = self.np_rng.poisson(w.seller_rate[live] * mult[PAYMENT])
            idx = np.repeat(live, orders)
            if len(idx):
                times = self._times(idx, t0).tolist()
                amounts = self._amounts(idx, 600, 0.7, 100).tolist()
                for s, t, a in zip(idx.tolist(), times, amounts, strict=True):
                    buyer = self._pick_buyer(s, t)
                    if buyer is not None:
                        self.push(t, Intent(SEND, buyer, s, a, topup=True))
            for s in live.tolist():
                if rng.random() < 0.5:
                    t = t0 + rng.randint(18 * 3600, 23 * 3600)
                    self.push(t, Intent(CASH_OUT, s, frac=rng.uniform(0.6, 0.95)))

        # Commission farming: cash in and straight back out through friendly wallets.
        for a, friends in w.farming_friends.items():
            for _ in range(_poisson(rng, 6.0)):
                f = rng.choice(friends)
                amt = float(rng.randrange(5000, 25500, 500))
                t = t0 + rng.randint(9 * 3600, 21 * 3600)
                tag = {"role": "agent_abuse", "typology": "commission_farming"}
                self.push(t, Intent(CASH_IN, f, a, amt, **tag))
                self.push(t + rng.randint(120, 1800), Intent(CASH_OUT, f, a, amt, **tag))

        # Travel and new phones: the legitimate versions of account-takeover signals.
        for i in np.flatnonzero((self.np_rng.random(n) < 0.004) & active).tolist():
            w.away_until[i] = t0 + rng.randint(1, 5) * DAY
            w.away_district[i] = rng.randrange(len(DISTRICTS))
        for i in np.flatnonzero((self.np_rng.random(n) < 0.0008) & active).tolist():
            if w.channel_app[i]:
                w.device[i] = w.new_device()

    def _pick_contact(self, i: int, ts: int) -> int:
        w, rng = self.w, self.rng
        cs = w.contacts[i]
        if cs and rng.random() >= 0.06:
            # Geometric preference: most transfers go to the first few contacts.
            return cs[min(int(rng.expovariate(0.7)), len(cs) - 1)]
        pool = w.customers_by_district[w.district[i]] if rng.random() < 0.7 else None
        for _ in range(6):
            c = pool[rng.randrange(len(pool))] if pool else rng.randrange(w.n_customers)
            if c != i and w.created_ts[c] < ts:
                cs.append(c)
                return c
        return cs[0] if cs else (i + 1) % w.n_customers

    def _pick_buyer(self, seller: int, ts: int) -> int | None:
        w, rng = self.w, self.rng
        cs = w.contacts[seller]
        if cs and rng.random() < 0.15:
            return rng.choice(cs)  # repeat customer
        for _ in range(6):
            c = rng.randrange(w.n_customers)
            if c != seller and w.created_ts[c] < ts and w.segment[c] != SELLER:
                return c
        return None

    def pick_agent(self, wallet: int, ts: int) -> int:
        w, rng = self.w, self.rng
        if w.away_until[wallet] > ts:
            district = w.away_district[wallet]
            hubs = w.hub_agents_by_district[district]
            if hubs and rng.random() < 0.6:
                return rng.choice(hubs)  # travellers use the agent at the terminal
            return rng.choice(w.agents_by_district[district])
        return rng.choice(w.pref_agents[wallet])

    # -------------------------------------------------------------- execution

    def _execute(self, ts: int, it: Intent) -> None:
        w, rng = self.w, self.rng
        typ, wallet = it.typ, it.wallet
        if w.created_ts[wallet] > ts or w.blocked_ts[wallet] <= ts:
            return

        if typ in (CASH_IN, ADD_MONEY):
            if typ == CASH_IN:
                agent = it.other if it.other is not None else self.pick_agent(wallet, ts)
                self._emit(ts, it, K_AGENT, agent, K_WALLET, wallet, it.amount, math.nan,
                           0, 0, w.agent_district[agent])  # fmt: skip
            else:
                self._emit(ts, it, K_BANK, 0, K_WALLET, wallet, it.amount, math.nan,
                           0, 0, self._district_of(wallet, ts))  # fmt: skip
            w.balance[wallet] += it.amount
            return

        bal = w.balance[wallet]
        if typ == CASH_OUT:
            self._pending_cashout.discard(wallet)
        if typ == SEND:
            r = it.other
            if r == wallet or w.created_ts[r] > ts or w.blocked_ts[r] <= ts:
                return
        amount = it.amount if it.frac is None else math.floor(bal * it.frac / 10) * 10
        amount = min(amount, self.cfg.txn_cap)
        if amount > bal:
            if it.topup and not it.retried:
                # Fund the wallet first, then come back to this transaction.
                need = math.ceil((amount - bal) / 500) * 500 + rng.choice((0, 0, 500, 1000))
                if w.channel_app[wallet] and rng.random() < 0.3:
                    self._execute(ts, Intent(ADD_MONEY, wallet, None, float(need)))
                else:
                    self._execute(ts, Intent(CASH_IN, wallet, None, float(need)))
                it.retried = True
                self.push(ts + rng.randint(60, 900), it)
                return
            amount = math.floor(bal / 50) * 50
        if amount < MIN_AMOUNT[typ]:
            return

        device = it.device if it.device is not None else w.device[wallet]
        app = 1 if it.device is not None else w.channel_app[wallet]
        if typ == SEND:
            r_kind, r = K_WALLET, it.other
        elif typ == CASH_OUT:
            r_kind = K_AGENT
            r = it.other if it.other is not None else self.pick_agent(wallet, ts)
        elif typ == PAYMENT:
            r_kind, r = K_MERCHANT, rng.choice(w.fav_merchants[wallet])
        elif typ == RECHARGE:
            r_kind, r = K_TELCO, wallet % N_TELCOS
        else:
            r_kind, r = K_BILLER, rng.randrange(N_BILLERS)
        district = it.district if it.district is not None else self._district_of(wallet, ts)
        if typ == CASH_OUT and it.district is None:
            district = w.agent_district[r]  # cash-out happens at the agent's counter

        self._emit(
            ts, it, K_WALLET, wallet, r_kind, r, amount, bal, device if app else 0, app, district
        )
        w.balance[wallet] = bal - amount

        if typ == SEND:
            w.balance[r] += amount
            if it.case >= 0:
                self.fraud.on_case_transfer(ts, it, r, amount)
            elif w.segment[r] == RURAL and amount >= 500 and r not in self._pending_cashout:
                # Remittance receivers usually withdraw soon after the money lands.
                self._pending_cashout.add(r)
                delay = min(rng.lognormvariate(math.log(7200), 1.0), 3 * DAY)
                self.push(ts + 600 + delay, Intent(CASH_OUT, r, frac=rng.uniform(0.7, 1.0)))
        elif it.case >= 0 and it.role in VICTIM_ROLES:
            self.fraud.on_case_transfer(ts, it, None, amount)

    def _district_of(self, wallet: int, ts: int) -> int:
        w = self.w
        return w.away_district[wallet] if w.away_until[wallet] > ts else w.district[wallet]

    def _emit(
        self, ts, it: Intent, s_kind, s, r_kind, r, amount, bal, device, app, district
    ) -> None:
        c = self.cols
        c["ts"].append(ts)
        c["type"].append(it.typ)
        c["s_kind"].append(s_kind)
        c["s"].append(s)
        c["r_kind"].append(r_kind)
        c["r"].append(r)
        c["amount"].append(float(amount))
        c["bal_before"].append(bal)
        c["device"].append(device)
        c["app"].append(app)
        c["district"].append(district)
        c["is_fraud"].append(it.fraud)
        c["role"].append(it.role)
        c["typology"].append(it.typology)
        c["case"].append(it.case)
        c["cell"].append(it.cell)


def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth's method; fine for the small rates used here."""
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1
