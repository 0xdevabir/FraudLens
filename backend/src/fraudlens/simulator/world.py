"""Static world: customers, agents, merchants and the relationships between them."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import numpy as np

from .config import DAY, SimConfig

# (name, is_metro, population weight)
DISTRICTS: tuple[tuple[str, int, int], ...] = (
    ("Dhaka", 1, 22),
    ("Gazipur", 1, 6),
    ("Narayanganj", 1, 5),
    ("Chattogram", 1, 10),
    ("Cumilla", 0, 6),
    ("Sylhet", 0, 5),
    ("Rajshahi", 0, 5),
    ("Khulna", 0, 5),
    ("Barishal", 0, 4),
    ("Rangpur", 0, 5),
    ("Mymensingh", 0, 6),
    ("Bogura", 0, 4),
    ("Cox's Bazar", 0, 3),
    ("Jashore", 0, 4),
    ("Dinajpur", 0, 3),
    ("Noakhali", 0, 4),
    ("Faridpur", 0, 3),
    ("Kushtia", 0, 3),
)

# The administrative division each district belongs to: the regions reports are cut by.
DIVISIONS: dict[str, str] = {
    "Dhaka": "Dhaka", "Gazipur": "Dhaka", "Narayanganj": "Dhaka", "Faridpur": "Dhaka",
    "Chattogram": "Chattogram", "Cumilla": "Chattogram", "Cox's Bazar": "Chattogram",
    "Noakhali": "Chattogram", "Sylhet": "Sylhet", "Rajshahi": "Rajshahi",
    "Bogura": "Rajshahi", "Khulna": "Khulna", "Jashore": "Khulna", "Kushtia": "Khulna",
    "Barishal": "Barishal", "Rangpur": "Rangpur", "Dinajpur": "Rangpur",
    "Mymensingh": "Mymensingh",
}  # fmt: skip

SEGMENTS = (
    "salaried",
    "garment_worker",
    "student",
    "small_trader",
    "seller",
    "rural_receiver",
    "other",
)
SALARIED, GARMENT, STUDENT, TRADER, SELLER, RURAL, OTHER = range(7)
SEGMENT_SHARE = (0.18, 0.15, 0.17, 0.12, 0.03, 0.20, 0.15)

# Mean spontaneous transactions per day, by segment.
# Columns: SEND, PAYMENT, RECHARGE, BILL_PAY, CASH_OUT, CASH_IN
BASE_RATES = np.array(
    [
        (0.10, 0.25, 0.08, 0.04, 0.04, 0.02),  # salaried
        (0.05, 0.05, 0.07, 0.01, 0.08, 0.02),  # garment_worker
        (0.05, 0.15, 0.10, 0.01, 0.04, 0.03),  # student
        (0.20, 0.10, 0.06, 0.05, 0.10, 0.10),  # small_trader
        (0.40, 0.10, 0.06, 0.04, 0.00, 0.02),  # seller (cash-out is scheduled separately)
        (0.02, 0.03, 0.05, 0.01, 0.03, 0.02),  # rural_receiver
        (0.05, 0.05, 0.06, 0.01, 0.05, 0.04),  # other
    ]
)

# Multiplier on typical transaction size, by segment.
AMOUNT_SCALE = np.array([1.4, 0.8, 0.6, 1.6, 1.5, 0.7, 0.9])
# Median opening balance (BDT), by segment.
OPENING_BALANCE = np.array([6000, 1200, 800, 5000, 8000, 900, 1200])

MERCHANT_CATEGORIES = ("grocery", "pharmacy", "restaurant", "fashion", "electronics", "transport")
N_TELCOS = 4
N_BILLERS = 12


@dataclass
class World:
    cfg: SimConfig
    n_customers: int
    capacity: int
    n_wallets: int

    # Static per-customer arrays used for vectorised daily scheduling.
    segment: np.ndarray
    activity: np.ndarray
    peak_hour: np.ndarray
    created_np: np.ndarray
    salary_dom: np.ndarray
    salary_amount: np.ndarray
    allowance_dom: np.ndarray
    family: np.ndarray
    guardian: np.ndarray
    landlord: np.ndarray
    seller_rate: np.ndarray

    # Mutable per-wallet state (customers first, mule wallets appended later).
    created_ts: list[float]
    district: list[int]
    area_urban: list[int]
    channel_app: list[int]
    device: list[int]
    balance: list[float]
    blocked_ts: list[float]
    away_until: list[int]
    away_district: list[int]
    contacts: list[list[int]]
    pref_agents: list[list[int]]
    fav_merchants: list[list[int]]

    agent_district: list[int]
    agents_by_district: list[list[int]]
    agent_colluding: list[bool]
    agent_farming: list[bool]
    farming_friends: dict[int, list[int]]
    hub_agents_by_district: list[list[int]]

    merchant_district: list[int]
    merchant_category: list[int]
    merchants_by_district: list[list[int]]

    customers_by_district: list[list[int]]
    next_device: int = 0
    sellers: list[int] = field(default_factory=list)

    def new_device(self) -> int:
        self.next_device += 1
        return self.next_device

    def new_wallet(self, created_ts: float, district: int, device: int, rng: random.Random) -> int:
        """Register a wallet created during the simulation (used for mule accounts)."""
        w = self.n_wallets
        if w >= self.capacity:
            raise RuntimeError("mule wallet capacity exhausted; raise SimConfig.max_mule_wallets")
        self.n_wallets += 1
        self.segment[w] = OTHER
        self.created_ts[w] = created_ts
        self.district[w] = district
        self.area_urban[w] = 1
        self.channel_app[w] = 1
        self.device[w] = device
        self.balance[w] = 0.0
        agents = self.agents_by_district[district]
        self.pref_agents[w] = rng.sample(agents, min(len(agents), 2))
        merchants = self.merchants_by_district[district]
        self.fav_merchants[w] = rng.sample(merchants, min(len(merchants), 2))
        return w


def build_world(cfg: SimConfig, np_rng: np.random.Generator, rng: random.Random) -> World:
    n = cfg.n_customers
    cap = n + cfg.max_mule_wallets
    n_dist = len(DISTRICTS)
    metro = np.array([d[1] for d in DISTRICTS])
    weight = np.array([d[2] for d in DISTRICTS], dtype=float)

    segment = np.full(cap, OTHER, dtype=np.int8)
    segment[:n] = np_rng.choice(len(SEGMENTS), size=n, p=SEGMENT_SHARE)

    # Garment workers live in the metro belt, remittance receivers outside it.
    district = np.empty(n, dtype=np.int64)
    for seg in range(len(SEGMENTS)):
        mask = segment[:n] == seg
        w = weight.copy()
        if seg == GARMENT:
            w = w * metro
        elif seg == RURAL:
            w = w * (1 - metro)
        district[mask] = np_rng.choice(n_dist, size=int(mask.sum()), p=w / w.sum())

    p_urban = np.where(metro[district] == 1, 0.9, 0.35)
    p_urban = np.where(segment[:n] == RURAL, 0.1, p_urban)
    area_urban = (np_rng.random(n) < p_urban).astype(int)
    p_app = np.where(area_urban == 1, 0.8, 0.5)
    p_app = np.where(segment[:n] == SELLER, 1.0, p_app)
    channel_app = (np_rng.random(n) < p_app).astype(int)

    # 90% of wallets pre-date the simulation; the rest sign up during it.
    age_days = np.minimum(np_rng.exponential(500, size=n) + 5, 3 * 365)
    created = -age_days * DAY
    signup = np_rng.random(n) < 0.10
    created[signup] = np_rng.uniform(0, cfg.days * DAY, size=int(signup.sum()))
    # A fifth of sellers are young accounts: a legitimate lookalike for mule wallets.
    sellers_mask = segment[:n] == SELLER
    young_seller = sellers_mask & (np_rng.random(n) < 0.2)
    created[young_seller] = np_rng.uniform(-20 * DAY, cfg.days * DAY * 0.6, int(young_seller.sum()))

    activity = np_rng.lognormal(mean=-0.125, sigma=0.5, size=n)
    peak_hour = np.clip(np_rng.normal(15.5, 3.0, size=n), 8, 22)
    balance = OPENING_BALANCE[segment[:n]] * np_rng.lognormal(0, 0.8, size=n)
    balance = np.round(balance, -1)

    salary_dom = np.zeros(n, dtype=int)
    salary_amount = np.zeros(n)
    sal = (segment[:n] == SALARIED) & (np_rng.random(n) < 0.55)
    salary_dom[sal] = np_rng.integers(1, 6, size=int(sal.sum()))
    salary_amount[sal] = np.round(32_000 * np_rng.lognormal(0, 0.35, size=int(sal.sum())), -2)
    gar = (segment[:n] == GARMENT) & (np_rng.random(n) < 0.85)
    salary_dom[gar] = np_rng.integers(5, 11, size=int(gar.sum()))
    salary_amount[gar] = np.round(12_500 * np_rng.lognormal(0, 0.15, size=int(gar.sum())), -2)

    by_district: list[list[int]] = [[] for _ in range(n_dist)]
    for i, d in enumerate(district.tolist()):
        by_district[d].append(i)

    seg_list = segment[:n].tolist()
    dist_list = district.tolist()
    created_list = created.tolist()

    # Social graph: mostly same-district contacts, a few elsewhere.
    contacts: list[list[int]] = [[] for _ in range(cap)]
    k_contacts = (3 + np_rng.poisson(3, size=n)).tolist()
    for i in range(n):
        local = by_district[dist_list[i]]
        picks: set[int] = set()
        for _ in range(k_contacts[i]):
            c = local[rng.randrange(len(local))] if rng.random() < 0.8 else rng.randrange(n)
            if c != i:
                picks.add(c)
        contacts[i] = list(picks)

    rural_ids = [i for i in range(n) if seg_list[i] == RURAL]
    earner_ids = [i for i in range(n) if seg_list[i] in (SALARIED, TRADER, OTHER)]
    family = np.full(n, -1, dtype=int)
    guardian = np.full(n, -1, dtype=int)
    landlord = np.full(n, -1, dtype=int)
    allowance_dom = np.zeros(n, dtype=int)
    for i in range(n):
        seg = seg_list[i]
        if seg == GARMENT or (seg == SALARIED and rng.random() < 0.4):
            f = rural_ids[rng.randrange(len(rural_ids))]
            family[i] = f
            contacts[i].append(f)
            contacts[f].append(i)
        if seg == STUDENT:
            g = earner_ids[rng.randrange(len(earner_ids))]
            guardian[i] = g
            allowance_dom[i] = rng.randint(1, 7)
            contacts[g].append(i)
            contacts[i].append(g)
        if seg == SALARIED and rng.random() < 0.6:
            local = by_district[dist_list[i]]
            for _ in range(5):
                c = local[rng.randrange(len(local))]
                if c != i and created_list[c] < 0:
                    landlord[i] = c
                    contacts[i].append(c)
                    break

    seller_rate = np.zeros(n)
    seller_ids = [i for i in range(n) if seg_list[i] == SELLER]
    seller_rate[seller_ids] = np_rng.lognormal(np.log(2.5), 0.6, size=len(seller_ids))

    # Agents and merchants: at least three of each per district.
    agent_district = _spread(cfg.n_agents, weight, np_rng)
    agents_by_district: list[list[int]] = [[] for _ in range(n_dist)]
    for a, d in enumerate(agent_district):
        agents_by_district[d].append(a)
    # Honest agents that look unusual next to their peers: transit hubs serve travellers
    # from everywhere, onboarding points (campuses, factory gates) serve mostly new accounts.
    hub_agents: list[list[int]] = [[] for _ in range(n_dist)]
    onboarding_agents: list[list[int]] = [[] for _ in range(n_dist)]
    for a, d in enumerate(agent_district):
        u = rng.random()
        if u < 0.05:
            hub_agents[d].append(a)
        elif u < 0.10:
            onboarding_agents[d].append(a)
    merchant_district = _spread(cfg.n_merchants, weight, np_rng)
    merchants_by_district: list[list[int]] = [[] for _ in range(n_dist)]
    for m, d in enumerate(merchant_district):
        merchants_by_district[d].append(m)
    merchant_category = np_rng.integers(0, len(MERCHANT_CATEGORIES), cfg.n_merchants).tolist()

    pref_agents: list[list[int]] = [[] for _ in range(cap)]
    fav_merchants: list[list[int]] = [[] for _ in range(cap)]
    for i in range(n):
        agents = agents_by_district[dist_list[i]]
        pref_agents[i] = rng.sample(agents, min(len(agents), rng.randint(2, 4)))
        onboarding = onboarding_agents[dist_list[i]]
        if created_list[i] >= 0 and onboarding and rng.random() < 0.7:
            # New customers keep going back to the agent who signed them up.
            pref_agents[i] = [rng.choice(onboarding)] * 2 + pref_agents[i][:1]
        merchants = merchants_by_district[dist_list[i]]
        fav_merchants[i] = rng.sample(merchants, min(len(merchants), rng.randint(3, 6)))

    # Commission-farming agents cycle cash through a few friendly wallets.
    agent_farming = [False] * cfg.n_agents
    farming_friends: dict[int, list[int]] = {}
    n_farming = max(1, round(cfg.n_agents * cfg.farming_agent_share))
    for a in rng.sample(range(cfg.n_agents), n_farming):
        local = [c for c in by_district[agent_district[a]] if created_list[c] < 0]
        if len(local) < 8:
            continue
        agent_farming[a] = True
        farming_friends[a] = rng.sample(local, rng.randint(4, 8))

    pad = cap - n
    # Shared handsets, the honest kind: a family phone, or a shopkeeper who transacts
    # for neighbours. Without these, "several wallets on one device" would mean fraud.
    device = list(range(1, n + 1))
    uses_app = channel_app.tolist()
    for i in range(n):
        if uses_app[i] and contacts[i] and rng.random() < 0.06:
            relative = rng.choice(contacts[i])
            if uses_app[relative]:
                device[i] = device[relative]
    for helper in rng.sample(range(n), max(1, n // 300)):
        if not uses_app[helper]:
            continue
        local = by_district[dist_list[helper]]
        for c in rng.sample(local, min(len(local), rng.randint(3, 8))):
            if uses_app[c]:
                device[c] = device[helper]
    device += [0] * pad
    return World(
        cfg=cfg,
        n_customers=n,
        capacity=cap,
        n_wallets=n,
        segment=segment,
        activity=activity,
        peak_hour=peak_hour,
        created_np=created,
        salary_dom=salary_dom,
        salary_amount=salary_amount,
        allowance_dom=allowance_dom,
        family=family,
        guardian=guardian,
        landlord=landlord,
        seller_rate=seller_rate,
        created_ts=created_list + [float("inf")] * pad,
        district=dist_list + [0] * pad,
        area_urban=area_urban.tolist() + [1] * pad,
        channel_app=channel_app.tolist() + [1] * pad,
        device=device,
        balance=balance.tolist() + [0.0] * pad,
        blocked_ts=[float("inf")] * cap,
        away_until=[0] * cap,
        away_district=[0] * cap,
        contacts=contacts,
        pref_agents=pref_agents,
        fav_merchants=fav_merchants,
        agent_district=agent_district,
        agents_by_district=agents_by_district,
        agent_colluding=[False] * cfg.n_agents,
        agent_farming=agent_farming,
        farming_friends=farming_friends,
        hub_agents_by_district=hub_agents,
        merchant_district=merchant_district,
        merchant_category=merchant_category,
        merchants_by_district=merchants_by_district,
        customers_by_district=by_district,
        next_device=n,
        sellers=seller_ids,
    )


def _spread(count: int, weight: np.ndarray, np_rng: np.random.Generator) -> list[int]:
    """Assign `count` entities to districts by weight, with at least three in each."""
    n_dist = len(weight)
    base = np.repeat(np.arange(n_dist), 3)
    rest = np_rng.choice(n_dist, size=max(0, count - len(base)), p=weight / weight.sum())
    out = np.concatenate([base, rest])[:count]
    np_rng.shuffle(out)
    return out.tolist()
