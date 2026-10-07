"""Adaptive adversaries: scam cells that change their scripts after FraudLens is deployed.

Opt-in only. The default dataset never touches this module: the experiment
(`fraudlens.models.adversary`) runs the default world unchanged, then hands its
fraud planner to an `AdaptivePlanner` on the deployment day. From then on new
cells run in rounds; every scam draws one evasion tactic from the cells' shared
mix, and after each round the `Adversary` moves the mix toward the tactics whose
scams got past the deployed model.

The tactics are the ones fraud teams report seeing once controls go live:

- none: the original script
- split_amounts: each victim transfer split into a few payments below a cap
- seasoned_mule: an old customer wallet with years of normal history, bought or
  rented, instead of a wallet opened for the purpose
- delayed_cashout: the mule waits hours to days, well past the 30-minute hold
  review window, before forwarding or cashing out
- fan_out: a larger pool of mules, each victim paying the least-used one, so no
  wallet collects many strangers' money
- mimic_hours: the scam call placed at the victim's own usual transacting hour
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .config import DAY
from .engine import CASH_OUT, SEND, VICTIM_ROLES, Intent
from .fraud import BASE_TYPOLOGIES, RECRUITABLE, WARMUP_DAYS, Case, Cell, FraudPlanner, Mule
from .world import DISTRICTS

TACTICS = ("none", "split_amounts", "seasoned_mule", "delayed_cashout", "fan_out", "mimic_hours")
SPLIT_CAPS = (1000.0, 1500.0, 2000.0, 2500.0)  # what a split payment is kept below
SEASONED_MIN_AGE_DAYS = 180
DELAY_HOURS = (6.0, 48.0)
FAN_OUT_POOL = 8  # mules kept ready for fan-out scams, on top of the cell's usual three
SEASONED_POOL = 2


@dataclass(frozen=True)
class AdversaryConfig:
    """How the adversary behaves. The numbers are assumptions, stated in the model card."""

    rounds: int = 6
    round_days: int = 7
    warmup_days: int = WARMUP_DAYS  # new cells open and warm up mules before round 0
    n_cells: int = 8
    initial_none: float = 0.5  # round 0: half the scams run the old script
    learning_rate: float = 3.0  # multiplicative-weights step on the success rate
    floor: float = 0.03  # every tactic keeps being tried a little
    min_cases: int = 5  # fewer scams than this with a tactic say nothing about it
    adapt: bool = True  # False keeps the round-0 mix: the non-adaptive control
    seed: int = 11


class Adversary:
    """The cells' shared tactic mix and how it learns from the deployed model."""

    def __init__(self, cfg: AdversaryConfig) -> None:
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        rest = (1.0 - cfg.initial_none) / (len(TACTICS) - 1)
        self.mix = {t: (cfg.initial_none if t == "none" else rest) for t in TACTICS}

    def draw(self) -> str:
        return self.rng.choices(TACTICS, weights=[self.mix[t] for t in TACTICS])[0]

    def update(self, outcomes: dict[str, tuple[int, int]]) -> dict[str, float]:
        """`outcomes` maps a tactic to (scams run, scams that got through). Returns the new mix.

        Multiplicative weights on the success rate: a tactic that got through more
        often gains share. A tactic tried fewer than `min_cases` times keeps its weight.
        """
        cfg = self.cfg
        if not cfg.adapt:
            return dict(self.mix)
        weights = {}
        for t in TACTICS:
            run, through = outcomes.get(t, (0, 0))
            gain = math.exp(cfg.learning_rate * through / run) if run >= cfg.min_cases else 1.0
            # Unproven tactics are scaled by the mean gain, so they neither win nor lose.
            weights[t] = (self.mix[t], gain if run >= cfg.min_cases else None)
        proven = [g for _, g in weights.values() if g is not None]
        mean_gain = sum(proven) / len(proven) if proven else 1.0
        raw = {t: share * (mean_gain if g is None else g) for t, (share, g) in weights.items()}
        total = sum(raw.values())
        free = 1.0 - cfg.floor * len(TACTICS)
        self.mix = {t: cfg.floor + free * raw[t] / total for t in TACTICS}
        return dict(self.mix)


class AdaptivePlanner(FraudPlanner):
    """The default planner's state plus adaptive cells that start on `deploy_day`.

    Built from the running planner without drawing any random numbers, so the
    world up to the deployment day is exactly the default one.
    """

    def __init__(self, base: FraudPlanner, adversary: Adversary, deploy_day: int) -> None:
        self.sim = base.sim
        self.mules = base.mules
        self.victims = base.victims
        self.cases = base.cases
        self.cells = base.cells  # shared: callbacks queued by `base` still find every cell
        self.adversary = adversary
        self.deploy_day = deploy_day
        self.tactic: dict[int, str] = {}  # case id -> tactic
        self.reported: dict[int, float] = {}  # case id -> when the victim first reported
        self.first_adaptive = len(self.cells)
        self.seasoned: dict[int, list[Mule]] = {}
        self.spare: dict[int, list[Mule]] = {}
        self._add_cells()

    @property
    def round_start_day(self) -> int:
        return self.deploy_day + self.adversary.cfg.warmup_days

    def is_adaptive(self, cell: Cell) -> bool:
        return cell.id >= self.first_adaptive

    def _add_cells(self) -> None:
        sim = self.sim
        w, rng, acfg = sim.w, sim.rng, self.adversary.cfg
        start = self.round_start_day
        end = start + acfg.rounds * acfg.round_days - 1
        for i in range(acfg.n_cells):
            a = BASE_TYPOLOGIES[i % 4]
            b = BASE_TYPOLOGIES[(i // 4 + i + 1) % 4]
            cell = Cell(
                id=len(self.cells),
                district=rng.randrange(len(DISTRICTS)),
                start_day=start,
                end_day=end,
                typologies=(a, b) if a != b else (a,),
                agents=[],
                devices=[w.new_device() for _ in range(rng.randint(2, 4))],
                recruited_share=rng.uniform(0.0, 0.35),
                p_forward=rng.uniform(0.2, 0.5),
                device_reuse=rng.uniform(0.3, 0.8),
                heldout=False,
                intensity=rng.uniform(0.7, 1.3),
            )
            n_agents = rng.choices((0, 1, 2), weights=(0.35, 0.45, 0.20))[0]
            free = [
                a
                for a in w.agents_by_district[cell.district]
                if not w.agent_colluding[a] and not w.agent_farming[a]
            ]
            for a in rng.sample(free, min(n_agents, len(free))):
                w.agent_colluding[a] = True
                cell.agents.append(a)
            self.cells.append(cell)
            self.seasoned[cell.id] = []
            self.spare[cell.id] = []

    # ----------------------------------------------------------------- mules

    def _maintain_mules(self, cell: Cell, t0: int) -> None:
        super()._maintain_mules(cell, t0)
        if not self.is_adaptive(cell):
            return
        for pool, target, make in (
            (self.seasoned[cell.id], SEASONED_POOL, self._seasoned_mule),
            (self.spare[cell.id], FAN_OUT_POOL, lambda c, t: self._create_mule(c, 1, t)),
        ):
            pool[:] = [m for m in pool if m.retire_ts > t0 and m.flagged_ts > t0]
            ahead = sum(1 for m in pool if m.retire_ts > t0 + 6 * DAY)
            for _ in range(target - ahead):
                mule = make(cell, t0)
                if mule is not None:
                    pool.append(mule)

    def _seasoned_mule(self, cell: Cell, t0: int) -> Mule | None:
        """An old customer wallet with a normal history, taken over for the cell."""
        w, rng = self.sim.w, self.sim.rng
        for _ in range(30):
            c = rng.randrange(w.n_customers)
            if (
                w.created_ts[c] < t0 - SEASONED_MIN_AGE_DAYS * DAY
                and w.segment[c] in RECRUITABLE
                and c not in self.mules
                and c not in self.victims
                and w.blocked_ts[c] == math.inf
            ):
                usable = t0 + rng.uniform(0, DAY)
                mule = Mule(c, cell.id, 1, "seasoned", usable, usable + rng.uniform(10, 20) * DAY)
                self.mules[c] = mule
                return mule
        return None

    # ----------------------------------------------------------------- scams

    def _new_case(self, cell: Cell, t0: int) -> None:
        if not self.is_adaptive(cell):
            super()._new_case(cell, t0)
            return
        sim = self.sim
        rng, w, cap = sim.rng, sim.w, sim.cfg.txn_cap
        tactic = self.adversary.draw()
        typology = rng.choice(cell.typologies)
        min_balance = {"impersonation": 1500, "wrong_send": 800, "account_takeover": 2000}.get(
            typology, 0
        )
        need_app = typology == "account_takeover"
        if tactic == "mimic_hours":
            # Choose the victim first, then call at the hour they usually transact.
            victim = self._pick_victim(t0, min_balance, need_app)
            if victim is None:
                return
            hour = min(23.9, max(0.0, rng.gauss(float(w.peak_hour[victim]), 1.0)))
            ts0 = t0 + int(hour * 3600)
        else:
            if typology == "account_takeover":
                hour = rng.uniform(0, 6) if rng.random() < 0.5 else rng.uniform(0, 24)
            else:
                hour = min(22.0, max(9.0, rng.gauss(15, 3.5)))
            ts0 = t0 + int(hour * 3600)
            victim = None

        ready = self._ready(cell.l1, ts0)
        if tactic == "seasoned_mule":
            ready = self._ready(self.seasoned[cell.id], ts0) or ready
        elif tactic == "fan_out":
            ready = ready + self._ready(self.spare[cell.id], ts0)
        if not ready:
            if victim is not None:
                self.victims.discard(victim)
            return
        if victim is None:
            victim = self._pick_victim(ts0, min_balance, need_app)
            if victim is None:
                return
        mule = rng.choice(ready)

        case = Case(len(self.cases), typology, cell.id, victim, mule.wallet, ts0)
        self.cases.append(case)
        self.tactic[case.id] = tactic
        tag = {"case": case.id, "typology": typology, "cell": cell.id}

        sent_to: dict[int, int] = {}

        def receiver() -> int:
            if tactic != "fan_out":
                return mule.wallet
            # The least-used ready mule takes the next payment.
            least = min(ready, key=lambda m: (m.n_victims + sent_to.get(m.wallet, 0), rng.random()))
            sent_to[least.wallet] = sent_to.get(least.wallet, 0) + 1
            return least.wallet

        def amounts(
            amount: float | None, frac: float | None
        ) -> list[tuple[float | None, float | None]]:
            """One transfer, or with split_amounts several below a cap."""
            if tactic != "split_amounts":
                return [(amount, frac)]
            limit = rng.choice(SPLIT_CAPS)
            total = amount if amount is not None else w.balance[victim] * (frac or 0.0)
            k = max(2, min(6, math.ceil(total / limit)))
            part = max(math.floor(total / k / 10) * 10, 50.0)
            return [(float(part), None)] * k

        def victim_send(t: float, amount: float | None = None, frac: float | None = None,
                        **kw) -> float:  # fmt: skip
            for a, f in amounts(amount, frac):
                sim.push(
                    t,
                    Intent(SEND, victim, receiver(), a or 0.0, frac=f, role="victim_transfer",
                           fraud=True, **tag, **kw),
                )  # fmt: skip
                t += rng.uniform(300, 1800)
            return t

        def bait(t: float, amount: float) -> None:
            sim.push(t, Intent(SEND, mule.wallet, victim, amount, role="bait", **tag))

        if typology == "impersonation":
            t = ts0
            for _ in range(rng.choices((1, 2, 3), weights=(0.55, 0.30, 0.15))[0]):
                t = victim_send(t, frac=rng.uniform(0.5, 0.97))
                t += rng.uniform(60, 480)

        elif typology == "wrong_send":
            if rng.random() < 0.15:
                bait(ts0, float(rng.choice((20, 50, 100))))
            amount = rng.choices(
                (1000, 1500, 2000, 3000, 5000, 8000, 10000, 15000),
                weights=(18, 12, 20, 16, 16, 8, 7, 3),
            )[0]
            victim_send(ts0 + rng.uniform(300, 3600), amount=float(amount))

        elif typology == "lottery_fee":
            t, amount = ts0, float(rng.choice((500, 1000, 1500, 2000)))
            for _ in range(rng.randint(2, 5)):
                t = victim_send(t, amount=min(amount, cap), topup=True)
                if rng.random() < 0.25:
                    break
                amount = round(amount * rng.uniform(1.4, 2.4), -2)
                t += rng.uniform(1200, 20 * 3600)

        else:  # account_takeover
            device = rng.choice(cell.devices) if rng.random() < 0.5 else w.new_device()
            direct = bool(cell.agents) and rng.random() < 0.3
            t = ts0
            for _ in range(rng.randint(1, 4)):
                for a, f in amounts(None, rng.uniform(0.5, 1.0)):
                    kw = dict(frac=f, device=device, district=cell.district,
                              role="ato_transfer", fraud=True, **tag)  # fmt: skip
                    if direct:
                        sim.push(
                            t, Intent(CASH_OUT, victim, rng.choice(cell.agents), a or 0.0, **kw)
                        )
                    else:
                        sim.push(t, Intent(SEND, victim, receiver(), a or 0.0, **kw))
                    t += (
                        rng.uniform(40, 300)
                        if tactic != "split_amounts"
                        else rng.uniform(300, 1800)
                    )

    # -------------------------------------------------------------- reaction

    def on_case_transfer(self, ts: int, it: Intent, receiver: int | None, amount: float) -> None:
        cell = self.cells[it.cell]
        if not self.is_adaptive(cell):
            super().on_case_transfer(ts, it, receiver, amount)
            return
        sim = self.sim
        rng = sim.rng
        tactic = self.tactic.get(it.case, "none")
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
                delay = rng.lognormvariate(math.log(1.5 * DAY), 0.8) + rng.uniform(3600, 12 * 3600)
                first = self.reported.get(it.case, math.inf)
                self.reported[it.case] = min(first, ts + delay)
                sim.push(ts + delay, lambda t, mu=mule: self._flag(mu, t, "victim_report"))

        out = math.floor(amount * rng.uniform(0.92, 1.0) / 10) * 10
        if out < 50:
            return
        if tactic == "delayed_cashout":
            dwell = rng.uniform(*DELAY_HOURS) * 3600
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
            first_part = math.floor(out * rng.uniform(0.35, 0.65) / 100) * 100
            chunks = [first_part, out - first_part]
        t = ts + dwell
        for chunk in chunks:
            sim.push(t, Intent(CASH_OUT, receiver, agent, float(chunk), role="mule_cashout", **tag))
            t += rng.uniform(60, 400)


def deploy(sim, adversary: Adversary, deploy_day: int) -> AdaptivePlanner:
    """Swap the running simulation's fraud planner for an adaptive one."""
    planner = AdaptivePlanner(sim.fraud, adversary, deploy_day)
    sim.fraud = planner
    return planner
