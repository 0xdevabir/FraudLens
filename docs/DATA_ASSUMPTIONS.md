# Data assumptions

**All data in this project is synthetic.** No real customer, agent, merchant or
transaction data was used, and no real personal information exists anywhere in
the repository. Wallet, agent and device identifiers are generated sequences
(`W0000123`, `A00045`, `D0001234`); there are no names, phone numbers or NIDs.

The data comes from a discrete-event simulator (`backend/src/fraudlens/simulator/`)
that is fully reproducible: the same seed produces byte-identical output (tested).

```
cd backend && uv run python -m fraudlens.simulator.generate
```

This document lists every assumption built into that simulator, so a reader can
judge how far the results transfer to real traffic. Numbers we chose are marked
as **assumed**; none of them are measurements of upay's customers.

## 1. Scale and time

| Parameter | Value |
| --- | --- |
| Period | 120 days from 2026-01-01 |
| Customers / agents / merchants | 20,000 / 600 / 800 |
| Transactions produced | 1,450,261 (about 10–15k per day) |
| Per-transaction limit | ৳25,000 (assumed, in the range of MFS limits) |
| Seed | 7 |

Splits are strictly by time, never random:

| Split | Days | Transactions | Used for |
| --- | --- | --- | --- |
| train | 0–74 | 887,950 | fitting models |
| val | 75–94 | 257,577 | early stopping, calibration, fusion weights, thresholds |
| test | 95–119 | 304,734 | reported once, never used for any fitting or tuning |

## 2. The population

- **Districts.** 18 Bangladeshi districts with assumed population weights and a
  metro/non-metro flag. Each wallet has a home district and an urban/rural flag.
- **Segments** (assumed shares): salaried, garment worker, student, small trader,
  online seller, rural receiver, other. Each has its own daily rate for every
  transaction type, amount scale and opening balance.
- **Channel.** A wallet is either app (has a device ID) or USSD (no device ID).
- **Social graph.** Each customer has a contact list, mostly within their own
  district. 94% of transfers go to an existing contact, with a strong preference
  for the first few; 6% go to someone new, who then becomes a contact.
- **Fixed relationships.** Family member (remittance), guardian (student
  allowance), landlord (rent).
- **Agents and merchants.** At least three per district. Customers have a few
  preferred agents near home. 5% of agents are **transit hubs** (bus stands,
  markets) that serve travellers from everywhere, and 5% are **onboarding
  agents** that sign up new customers, so their clientele is mostly young wallets.
- **Shared handsets.** 6% of app users share a handset with one of their contacts
  (family phone), and about one shopkeeper per 300 customers runs a handset that
  3–8 local customers use for their wallets.

## 3. Normal behaviour

- Transaction types: send money, merchant payment, mobile recharge, bill pay,
  cash-out, cash-in, add-money from bank.
- Counts per customer per day are Poisson, scaled by a personal activity level.
- Time of day: each customer has a personal peak hour (σ ≈ 2.8h), mixed with a
  global daily curve.
- Amounts: log-normal by type and segment (send-money median ≈ ৳1,500), rounded
  to the round numbers people actually send.
- Balances are tracked. Nobody can overdraw (tested). When short, a customer tops
  up through cash-in or add-money and then retries, as people do.
- **Calendar effects.** Salary on a fixed day of month, followed by remittance to
  family and cash-out. Rent on days 3–8. Volume ramps up over the ten days before
  Eid (day 79) and drops during the holiday. Month-start and Friday effects.

## 4. Legitimate behaviour that looks like fraud

A model is only credible if the data contains honest customers who trip the same
signals. These are generated on purpose:

| Hard negative | Fraud signal it imitates |
| --- | --- |
| Online (F-commerce) sellers paid by strangers nationwide, cashing out most evenings | high fan-in, new senders, pass-through |
| 20% of sellers have **young** accounts | young wallet receiving from strangers |
| Rural receivers who cash out a remittance within minutes | short dwell time |
| Top-up then send immediately | funded just before sending |
| Garment workers cashing out 50–90% of salary | balance drain |
| Rent and other large transfers | unusually large amount |
| Travel (0.4% of customers per day, 1–5 days) | transaction away from home district |
| New phone (0.08% of app users per day) | new device |
| Family and shopkeeper handsets used by several honest wallets | several wallets on one device |
| Transit-hub agents: 60% of a traveller's cash-outs happen there | agent with many out-of-district customers |
| Onboarding agents: 70% of a new customer's cash-outs happen there | agent with many young wallets |
| First payment to a new contact | new recipient |

## 5. Fraud

Fraud is run by **cells**: a group with a pool of mule wallets, 2–4 shared
handsets and, for 65% of cells, one or two colluding agents. 16 cells with
staggered 25–45 day lifetimes, plus 3 held-out cells (§6).

**Mule wallets.** Each cell keeps three level-1 mules (receive from victims) and
one level-2 mule (receives forwarded money). Two kinds:
- *young*: opened for the purpose, given a small cash-in and a few recharges or
  payments, then used after 2–6 days. Each cell has its own level of carelessness:
  a young mule is opened on one of the cell's shared handsets with a per-cell
  probability of 30–80%, otherwise on a fresh handset used for nothing else;
- *recruited*: an existing customer at least 60 days old who rents out their wallet.

Mules are retired after 5–14 days of use and replaced ahead of time.

**Typologies.**

| Typology | Script | What the victim does |
| --- | --- | --- |
| impersonation | "Your account is at risk, move your money to this safe number" | 1–3 transfers of 50–97% of balance, minutes apart |
| wrong_send | "I sent you money by mistake, send it back" (15% of the time a small real bait transfer comes first) | one transfer of ৳1,000–15,000 |
| lottery_fee | "You won, pay the fee to release the prize" | 2–5 escalating payments over hours, topping up to pay |
| account_takeover | attacker has PIN and OTP and logs in on another handset (a cell handset half the time, a fresh one otherwise) | 1–4 transfers or direct cash-outs draining the wallet; half happen between midnight and 6am |
| investment_scam | "Invest for daily returns"; small payouts build trust | 3–8 growing deposits over days (held out, §6) |

**What happens to the money.** 90% of the time it leaves the mule within 1–25
minutes, otherwise 1–8 hours. It is forwarded to the level-2 mule (20–50% by
cell) or cashed out, 80% at the cell's colluding agent when it has one. Large
withdrawals are sometimes split in two.

**Reports and flags.** Half of victim transfers are reported (assumed). A report
is confirmed after a log-normal delay (median 1.5 days), which blocks the mule
and records a `wallet_flags` event; 30% of the time the linked level-2 mule is
flagged too. The feature layer sees a flag only from its timestamp onward, so
"linked to a known-bad wallet" is available exactly as late as it would be in
production.

**Agent abuse.** 1.5% of agents farm commission: about six cash-in/cash-out
round trips per day through friendly wallets. This is labelled as agent abuse,
**not** as transaction fraud, and is evaluated separately by the agent risk score.

**Volumes produced.** 1,056 cases; ৳9.48M victim loss (৳3.55M of it in the test
period); 406 mule wallets (287 young, 119 recruited), of which 354 are eventually
flagged; 13 colluding and 9 farming agents out of 600; 0.29% of transactions are
part of a fraud chain.

## 6. The held-out typology

The obvious objection to synthetic data is "you wrote the fraud, so of course the
model finds it". To answer it honestly, `investment_scam` **does not exist before
day 95**. No model, threshold or rule sees a single example. It is also built to
defeat the easy signals: all mules are aged recruited wallets (no young-account
signal) on their own handsets (no shared-device signal), the mule sends money *back* to the victim (looks reciprocal), money
dwells for 1–4 hours instead of minutes, there is no colluding agent, and reports
take a median of 5 days. Recall on this typology is reported separately in the
model card as the measure of generalisation.

## 7. Labels

| Level | Label |
| --- | --- |
| Transaction | `is_fraud`, `fraud_role` (`victim_transfer`, `ato_transfer`, `mule_forward`, `mule_cashout`, `bait`, `agent_abuse`), `typology`, `case_id` |
| Wallet | `is_mule` (received fraud money), kind, level, first/last fraud time, flag time |
| Agent | `is_risky`, `risk_type` (colluding / farming) |
| Case | victim, mule, typology, loss, number of transfers |

Money counted as **loss** (and as "taka saved" when stopped) is only what leaves
the victim: roles `victim_transfer` and `ato_transfer`. Mule forwarding and
cash-out are fraud-chain transactions but are not double-counted as loss. Bait
transfers are part of a case but are not fraud by themselves.

Labels never enter the feature engine: its input record has no label field (tested).

## 8. Assumptions in the derived signals

Four features are computed from the data with constants we chose. They are
**assumed**, like the simulator's rates.

- **Travel speed.** A transaction carries a district, not a position. The
  distance between two consecutive transactions of a wallet is the great-circle
  distance between the two district centres (coordinates in
  `features/engine.py`), less 30 km, so that someone who lives near a district
  border is not counted as having travelled. Speed is that distance over the
  time between the two transactions and is capped at 2,000 km/h. Real location
  data (cell tower, IP, GPS) would be finer and noisier than this.
- **Hops to a flagged wallet.** The shortest path through past transfers from a
  wallet to any wallet with a confirmed-fraud flag, counted up to 3 hops;
  further than that is "no link". A flag counts only from its own timestamp.
  Flags are never removed, so the share of wallets within 3 hops of one grows
  for as long as the system runs. This is the feature that drifts most between
  training and test (MODEL_CARD §13), and a deployment would need flags to
  expire.
- **Contacts in common.** The number of wallets that both the sender and the
  receiver have already transacted with, from the same transfer history.

## 9. Revision history

The simulator was revised **once**, after the first trained model (v1) was
inspected. In the first version only scammers ever shared a handset and only
colluding agents had unusual customers, so the model learned "shared device =
fraud": two device features carried 73% of its gain, it scored 0% on the held-out
typology, and agent ranking was trivially perfect. That was a flaw in the data,
not a result. The revision added what real traffic has: family and shopkeeper
handsets, transit-hub and onboarding agents, cells that do not always reuse
handsets, and takeovers from fresh handsets. Model v2 was the first trained on
the revised data. The data has not changed since: model v4, which the published
numbers come from, is trained on the same dataset with four more features (§8).

To be plain about what this means for the test set: the 0% held-out result was
seen on v1's test period, and it is one of the things that exposed the flaw. The
whole dataset, test period included, was then regenerated, and no simulator
parameter was tuned to move a test metric: each change adds a legitimate
behaviour that was missing. But the test period is no longer strictly
"looked at once", and the model card says so.

## 10. Known limitations

- Rates, shares and amounts are informed guesses, not calibrated to real traffic
  (the optional calibrated profile, §11, sets the few that have a public source).
  Absolute metrics will not transfer; the relative findings and the system design
  are what we claim.
- Fraud prevalence is likely higher than in real traffic, to give enough positives
  to evaluate each typology.
- Fraud scripts are stylised. Real scammers adapt to controls; the simulator's do
  not react to the model's decisions.
- No merchant fraud, no loan or credit fraud, no SIM-swap telemetry, no customer
  demographics beyond segment and district.
- Reports are the only source of confirmed labels and arrive for about half of
  cases. Training here uses ground-truth labels; with report-only labels a real
  deployment would learn from a biased subset.
- Analysts, verdicts and customer responses in the demo are also simulated. The
  review simulator (`make review`) closes cases with the simulator's ground
  truth, so the labels the feedback loop learns from are more accurate and more
  complete than real verdicts would be (MODEL_CARD §13).

## 11. Calibration against Bangladesh sources

Most of the parameters above have no public source. A few do, and the
**calibrated** profile (`SimConfig.calibrated()`) sets those from public
Bangladesh figures and leaves everything else alone. The **default** profile is
unchanged: every published model, policy and metric still comes from it, and a
test checks that `with_profile("default")` returns the same config.

```
cd backend
uv run python -m fraudlens.simulator.generate --profile calibrated   # writes data/full_calibrated
FRAUDLENS_SIM_PROFILE=calibrated uv run python -m fraudlens.simulator.generate
```

Sources are listed in full, with access dates, in BANGLADESH_CONTEXT.md.

| Parameter | Default | Calibrated | Sourced value | Source |
| --- | --- | --- | --- | --- |
| `txn_cap` (largest single wallet transaction) | ৳25,000 | ৳30,000 | Bangladesh Bank publishes daily limits, not a per-transaction one: cash-out ৳30,000/day, send money ৳50,000/day (from 27 Mar 2025). We use the smallest. | [S10] |
| `send_median` (send-money amount) | ৳1,500 → mean ৳2,425 | ৳2,850 → mean ৳3,520 | Average P2P transfer ৳3,552 (Oct 2025, derived from 134.26M txns worth ৳476.93bn) | [S2] |
| `cash_out_median` (spontaneous cash-out) | ৳3,000 → mean ৳4,336 | ৳1,400 → mean ৳4,337 | Average cash-out ৳2,132 (Oct 2025, derived). **Not matched**: most simulated cash-outs are balance-driven withdrawals after salary and remittance, which this parameter does not set. | [S2] |
| `report_rate` (share of victim transfers reported) | 0.50 (assumed) | 0.412 | 41.2% of personal holders with a problem complained (TIB). The denominator is all problems, not only fraud, so this is an approximation. | [S4] |
| Account-takeover share of base-typology cases (`ato_keep_share`) | 24.8% (keep 1.0) | 12.5% (keep 0.5; the rest become impersonation) | Hacking 12.3% of fraud reports; deceptive information 52.6% | [S4] |
| Cash-in amount | mean ৳2,978 | unchanged | Average cash-in ৳2,898 (Oct 2025, derived) — already close | [S2] |
| Overall mean transaction | ৳2,456 | ৳2,957 | ৳2,454 (Feb 2025, derived from ৳164,726 crore / 671.3M). The default matched without being tuned to it; the calibrated profile moves away because of the higher send amounts. | [S1] |
| Mean loss per case | ৳8,982 | ৳11,157 | "Over ৳9,000" average loss (2021 survey); TIB range ৳300–83,000 | [S5], [S4] |

**Effect on the data (full world, seed 7).** 1,207 cases instead of 1,056; the
typology mix moves from 21% to 10.5% account takeover and from 19% to 31%
impersonation. The random stream of the default profile is untouched (the
extra draw for takeovers only happens when `ato_keep_share < 1`).

**Not calibrated, and why.**

- *Transaction mix by type.* Bangladesh Bank's Oct 2025 counts give P2P 19.8%,
  cash-in 21.0%, cash-out 25.7%; the simulator has 29%, 20% and 15%. The mix
  comes out of many behaviours (salary, remittance, agent farming) rather than
  one parameter, and re-weighting it is a larger change than this profile makes.
- *Fraud prevalence.* No public source gives a per-transaction scam rate; the
  simulator's rate stays deliberately high (§10).
- *No public source found* for district weights, segment shares, mule dwell
  times and lifetimes, report delay, colluding-agent share or cell structure.
  The validation kit (docs/validation/) is designed to collect several of these
  from customers and analysts.

The calibrated profile has not been used to retrain or re-evaluate the model;
doing so, and reporting the change, is the next step.
