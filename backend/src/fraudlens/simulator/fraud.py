"""Scam cells, mule wallets and the fraud typologies they run.

A cell is a group of scammers with a pool of mule wallets, a few shared devices
and sometimes a colluding agent. Victim money lands on a level-1 mule, is
optionally forwarded to a level-2 mule, and leaves the system as a cash-out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .config import DAY
from .engine import CASH_IN, CASH_OUT, PAYMENT, RECHARGE, SEND, VICTIM_ROLES, Intent, _poisson
from .world import DISTRICTS, GARMENT, OTHER, STUDENT, TRADER

if TYPE_CHECKING:
    from .engine import Simulation

BASE_TYPOLOGIES = ("impersonation", "wrong_send", "lottery_fee", "account_takeover")
HELDOUT_TYPOLOGY = "investment_scam"
WARMUP_DAYS = 8
RECRUITABLE = (GARMENT, STUDENT, TRADER, OTHER)


@dataclass
class Mule:
    wallet: int
    cell: int
    level: int
    kind: str  # "young" (opened for the purpose) or "recruited" (an existing customer)
    usable_ts: float
    retire_ts: float
    first_fraud_ts: float = math.inf
    last_fraud_ts: float = -math.inf
    n_victims: int = 0
    flagged_ts: float = math.inf
    flag_reason: str = ""


@dataclass
class Cell:
    id: int
    district: int
    start_day: int
    end_day: int
    typologies: tuple[str, ...]
    agents: list[int]
    devices: list[int]
    recruited_share: float
    p_forward: float
    device_reuse: float
    heldout: bool
    intensity: float
    l1: list[Mule] = field(default_factory=list)
    l2: list[Mule] = field(default_factory=list)


@dataclass
class Case:
    id: int
    typology: str
    cell: int
    victim: int
    mule: int
    start_ts: int
    loss: float = 0.0
    n_txn: int = 0


class FraudPlanner:
    def __init__(self, sim: Simulation) -> None:
        self.sim = sim
        self.mules: dict[int, Mule] = {}
        self.victims: set[int] = set()
        self.cases: list[Case] = []
        self.cells = self._build_cells()

    # ------------------------------------------------------------------ cells

    def _build_cells(self) -> list[Cell]:
        sim = self.sim
        cfg, w, rng = sim.cfg, sim.w, sim.rng
        cells: list[Cell] = []
        span = cfg.days - 20 - WARMUP_DAYS
        for i in range(cfg.n_cells):
            start = WARMUP_DAYS + round(span * i / max(1, cfg.n_cells - 1)) + rng.randint(-2, 2)
            start = max(WARMUP_DAYS, start)
            # Rotate so every base typology is run by several cells across the timeline.
            a = BASE_TYPOLOGIES[i % 4]
            b = BASE_TYPOLOGIES[(i // 4 + i + 1) % 4]
            cells.append(
                Cell(
                    id=i,
                    district=rng.randrange(len(DISTRICTS)),
                    start_day=start,
                    end_day=min(cfg.days - 1, start + rng.randint(25, 45)),
                    typologies=(a, b) if a != b else (a,),
                    agents=[],
                    devices=[w.new_device() for _ in range(rng.randint(2, 4))],
                    recruited_share=rng.uniform(0.0, 0.35),
                    p_forward=rng.uniform(0.2, 0.5),
                    device_reuse=rng.uniform(0.3, 0.8),
                    heldout=False,
                    intensity=rng.uniform(0.7, 1.3),
                )
            )
        for j in range(cfg.n_heldout_cells):
            cells.append(
                Cell(
                    id=cfg.n_cells + j,
                    district=rng.randrange(len(DISTRICTS)),
                    start_day=cfg.val_end_day + min(j * 2, cfg.days - cfg.val_end_day - 6),
                    end_day=cfg.days - 1,
                    typologies=(HELDOUT_TYPOLOGY,),
                    agents=[],
                    devices=[w.new_device() for _ in range(2)],
                    recruited_share=1.0,
                    p_forward=0.7,
                    device_reuse=0.0,
                    heldout=True,
                    intensity=rng.uniform(0.9, 1.3),
                )
            )
        for cell in cells:
            n_agents = rng.choices((0, 1, 2), weights=(0.35, 0.45, 0.20))[0]
            if cell.heldout:
                n_agents = 0
            free = [
                a
                for a in w.agents_by_district[cell.district]
                if not w.agent_colluding[a] and not w.agent_farming[a]
            ]
            for a in rng.sample(free, min(n_agents, len(free))):
                w.agent_colluding[a] = True
                cell.agents.append(a)
        return cells

    # ------------------------------------------------------------- daily plan

    def schedule_day(self, day: int) -> None:
        sim = self.sim
        cfg, rng = sim.cfg, sim.rng
        t0 = day * DAY
        d = day - cfg.eid_day
        eid = 1.5 if -10 <= d <= -1 else 1.0  # scams peak with pre-Eid spending
        for cell in self.cells:
            warmup = 0 if cell.heldout else WARMUP_DAYS
            if day < cell.start_day - warmup or day > cell.end_day:
                continue
            self._maintain_mules(cell, t0)
            if day < cell.start_day:
                continue
            for _ in range(_poisson(rng, cfg.cases_per_cell_day * eid * cell.intensity)):
                self._new_case(cell, t0)

    def _maintain_mules(self, cell: Cell, t0: int) -> None:
        for level, pool, target in ((1, cell.l1, 3), (2, cell.l2, 1)):
            pool[:] = [m for m in pool if m.retire_ts > t0 and m.flagged_ts > t0]
            # Replace mules ahead of their retirement so a fresh one is warmed up in time.
            ahead = sum(1 for m in pool if m.retire_ts > t0 + 6 * DAY)
            for _ in range(target - ahead):
                mule = self._create_mule(cell, level, t0)
                if mule is not None:
                    pool.append(mule)

    def _create_mule(self, cell: Cell, level: int, t0: int) -> Mule | None:
        sim = self.sim
        w, rng = sim.w, sim.rng
        if rng.random() < cell.recruited_share:
            for _ in range(20):
                c = rng.randrange(w.n_customers)
                if (
                    w.created_ts[c] < t0 - 60 * DAY
                    and w.segment[c] in RECRUITABLE
                    and c not in self.mules
                    and c not in self.victims
                    and w.blocked_ts[c] == math.inf
                ):
                    usable = t0 + rng.uniform(0, DAY)
                    mule = Mule(
                        c, cell.id, level, "recruited", usable, usable + rng.uniform(6, 14) * DAY
                    )
                    self.mules[c] = mule
                    return mule
            return None
        created = t0 + rng.uniform(0, DAY)
        district = cell.district if rng.random() < 0.7 else rng.randrange(len(DISTRICTS))
        # Careless cells open many wallets on one handset; careful ones use a fresh SIM and phone.
        reuse = rng.random() < cell.device_reuse
        device = rng.choice(cell.devices) if reuse else w.new_device()
        wallet = w.new_wallet(created, district, device, rng)
        usable = created + rng.uniform(2, 6) * DAY
        mule = Mule(wallet, cell.id, level, "young", usable, usable + rng.uniform(5, 12) * DAY)
        self.mules[wallet] = mule
        # A little activity so the wallet is not completely blank when first used.
        t = created + rng.uniform(3600, DAY)
        sim.push(t, Intent(CASH_IN, wallet, None, float(rng.randrange(200, 1100, 100))))
        for _ in range(rng.randint(0, 3)):
            t += rng.uniform(1800, DAY)
            if t < usable:
                typ = RECHARGE if rng.random() < 0.6 else PAYMENT
                sim.push(t, Intent(typ, wallet, None, float(rng.choice((20, 50, 100, 150)))))
        return mule

    def _ready(self, pool: list[Mule], ts: float) -> list[Mule]:
        return [m for m in pool if m.usable_ts <= ts < m.retire_ts and m.flagged_ts > ts]

    def _pick_victim(self, ts: int, min_balance: float, need_app: bool = False) -> int | None:
        w, rng = self.sim.w, self.sim.rng
        for _ in range(12):
            c = rng.randrange(w.n_customers)
            if (
                w.created_ts[c] < ts - 3 * DAY
                and c not in self.victims
                and c not in self.mules
                and w.balance[c] >= min_balance
                and (w.channel_app[c] or not need_app)
            ):
                self.victims.add(c)
                return c
        return None

    # ------------------------------------------------------------- typologies

    def _new_case(self, cell: Cell, t0: int) -> None:
        sim = self.sim
        rng = sim.rng
        typology = rng.choice(cell.typologies)
        if typology == "account_takeover":
            hour = rng.uniform(0, 6) if rng.random() < 0.5 else rng.uniform(0, 24)
        else:
            # Scam calls happen when people answer the phone.
            hour = min(22.0, max(9.0, rng.gauss(15, 3.5)))
        ts0 = t0 + int(hour * 3600)
        ready = self._ready(cell.l1, ts0)
        if not ready:
            return
        mule = rng.choice(ready)
        min_balance = {"impersonation": 1500, "wrong_send": 800, "account_takeover": 2000}.get(
            typology, 0
        )
        victim = self._pick_victim(ts0, min_balance, need_app=typology == "account_takeover")
        if victim is None:
            return
        case = Case(len(self.cases), typology, cell.id, victim, mule.wallet, ts0)
        self.cases.append(case)
        tag = {"case": case.id, "typology": typology, "cell": cell.id}
        m = mule.wallet
        cap = sim.cfg.txn_cap

        def victim_send(t: float, **kw) -> None:
            sim.push(t, Intent(SEND, victim, m, role="victim_transfer", fraud=True, **tag, **kw))

        def bait(t: float, amount: float) -> None:
            sim.push(t, Intent(SEND, m, victim, amount, role="bait", **tag))

        if typology == "impersonation":
            # "Your account is at risk, move your money to this safe number."
            t = ts0
            for _ in range(rng.choices((1, 2, 3), weights=(0.55, 0.30, 0.15))[0]):
                victim_send(t, frac=rng.uniform(0.5, 0.97))
                t += rng.uniform(60, 480)

        elif typology == "wrong_send":
            # "I sent you money by mistake, please send it back."
            if rng.random() < 0.15:
                bait(ts0, float(rng.choice((20, 50, 100))))
            amount = rng.choices(
                (1000, 1500, 2000, 3000, 5000, 8000, 10000, 15000),
                weights=(18, 12, 20, 16, 16, 8, 7, 3),
            )[0]
            victim_send(ts0 + rng.uniform(300, 3600), amount=float(amount))

        elif typology == "lottery_fee":
            # "You won a prize, pay the processing fee to release it."
            t, amount = ts0, float(rng.choice((500, 1000, 1500, 2000)))
            for _ in range(rng.randint(2, 5)):
                victim_send(t, amount=min(amount, cap), topup=True)
                if rng.random() < 0.25:
                    break  # the victim catches on
                amount = round(amount * rng.uniform(1.4, 2.4), -2)
                t += rng.uniform(1200, 20 * 3600)

        elif typology == "account_takeover":
            # Attacker has the PIN and OTP and logs in from their own phone.
            device = rng.choice(cell.devices) if rng.random() < 0.5 else sim.w.new_device()
            direct = bool(cell.agents) and rng.random() < 0.3
            t = ts0
            for _ in range(rng.randint(1, 4)):
                kw = dict(frac=rng.uniform(0.5, 1.0), device=device, district=cell.district,
                          role="ato_transfer", fraud=True, **tag)  # fmt: skip
                if direct:
                    sim.push(t, Intent(CASH_OUT, victim, rng.choice(cell.agents), **kw))
                else:
                    sim.push(t, Intent(SEND, victim, m, **kw))
                t += rng.uniform(40, 300)

        else:  # investment_scam, the held-out typology
            # "Invest with us for daily returns." Small payouts build trust, deposits grow.
            t, amount = ts0, float(rng.choice((1000, 2000, 3000)))
            for step in range(rng.randint(3, 8)):
                victim_send(t, amount=min(amount, cap), topup=True)
                if step < 2:
                    bait(t + rng.uniform(600, 3600), round(amount * rng.uniform(0.1, 0.3), -1))
                if rng.random() < 0.12:
                    break
                amount = round(amount * rng.uniform(1.5, 2.5), -2)
                t += rng.uniform(8 * 3600, 60 * 3600)
                if t >= sim.cfg.days * DAY:
                    break

    # ---------------------------------------------------------------- reaction

    def on_case_transfer(self, ts: int, it: Intent, receiver: int | None, amount: float) -> None:
        """Called after a case-tagged transfer executes: book the loss, move the money on."""
        sim = self.sim
        rng = sim.rng
        cell = self.cells[it.cell]
        if it.role in VICTIM_ROLES:
            case = self.cases[it.case]
            case.loss += amount
            case.n_txn += 1
        if receiver is None or it.role == "bait":
            return
        mule = self.mules.get(receiver)
        if mule is None:
            return
        mule.first_fraud_ts = min(mule.first_fraud_ts, ts)
        mule.last_fraud_ts = max(mule.last_fraud_ts, ts)
        tag = {"case": it.case, "typology": it.typology, "cell": it.cell, "fraud": True}

        if it.role in VICTIM_ROLES:
            mule.n_victims += 1
            if rng.random() < sim.cfg.report_rate:
                median = 5 * DAY if cell.heldout else 1.5 * DAY
                delay = rng.lognormvariate(math.log(median), 0.8) + rng.uniform(3600, 12 * 3600)
                sim.push(ts + delay, lambda t, mu=mule: self._flag(mu, t, "victim_report"))

        out = math.floor(amount * rng.uniform(0.92, 1.0) / 10) * 10
        if out < 50:
            return
        if cell.heldout:
            dwell = rng.uniform(3600, 4 * 3600)
        elif rng.random() < 0.1:
            dwell = rng.uniform(3600, 8 * 3600)
        else:
            dwell = rng.uniform(60, 1500)

        l2 = self._ready(cell.l2, ts) if mule.level == 1 else []
        if l2 and rng.random() < cell.p_forward:
            nxt = rng.choice(l2).wallet
            sim.push(
                ts + dwell, Intent(SEND, receiver, nxt, float(out), role="mule_forward", **tag)
            )
            return

        w = sim.w
        if cell.agents and rng.random() < 0.8:
            agent = rng.choice(cell.agents)
        else:
            agent = rng.choice(w.agents_by_district[w.district[receiver]])
        chunks = [out]
        if out > 12_000 and rng.random() < 0.5:
            # Split a large withdrawal into a few smaller ones.
            first = math.floor(out * rng.uniform(0.35, 0.65) / 100) * 100
            chunks = [first, out - first]
        t = ts + dwell
        for chunk in chunks:
            sim.push(t, Intent(CASH_OUT, receiver, agent, float(chunk), role="mule_cashout", **tag))
            t += rng.uniform(60, 400)

    def _flag(self, mule: Mule, ts: int, reason: str) -> None:
        """A report is confirmed: the wallet is blocked and becomes a known-bad node."""
        if mule.flagged_ts <= ts:
            return
        sim = self.sim
        mule.flagged_ts = ts
        mule.flag_reason = reason
        sim.w.blocked_ts[mule.wallet] = ts
        if mule.level == 1 and sim.rng.random() < 0.3:
            # Investigators sometimes follow the money one hop further.
            for nxt in self.cells[mule.cell].l2:
                delay = sim.rng.uniform(1, 3) * DAY
                sim.push(ts + delay, lambda t, mu=nxt: self._flag(mu, t, "linked_investigation"))
