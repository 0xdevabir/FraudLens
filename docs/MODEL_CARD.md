# Model card — FraudLens risk models, version v4

Every number below is read from `backend/artifacts/models/v4/report.json`, which
`make train` writes, except where §13 says otherwise. Nothing here is
hand-entered or rounded up. The data is synthetic
([DATA_ASSUMPTIONS.md](DATA_ASSUMPTIONS.md)); absolute values will not transfer
to real traffic.

Version numbers count registrations in one registry. This card was written on a
machine whose registry holds `v1` (trained on the first simulator, §11), `v2`
(the previous served model, 57 features), `v3` (its challenger), `v4`, the model
described here, and `v5` (a challenger retrained from v4 on analyst verdicts,
§13, not promoted). On a fresh checkout
(`make demo`) the same model, with the same numbers, is registered as `v1`.
Versions older than `v4` were trained on a shorter feature list and the service
refuses to load them with the current code (§11).

## 1. What the models are for

Scam-to-cash-out interception for a mobile wallet. In an authorised-push-payment
scam the victim sends the money themselves, so the sender's behaviour often looks
normal. FraudLens therefore scores the **recipient and its network** as well as
the sender, and gets a second chance when the money leaves the mule wallet.

The models **rank and explain**. They do not approve or deny anything: the
decision engine (policy file) maps a score to warn / step-up / hold, and a hold
or a wallet freeze is always reviewed by a person.

| Model | Type | Input | Output |
| --- | --- | --- | --- |
| Transaction risk | LightGBM, 667 trees | all 61 features | probability that a send-money or cash-out is part of a fraud chain |
| Mule wallet | LightGBM, 56 trees | 21 recipient features only | probability that the receiving wallet is a mule; usable without any sender |
| Behaviour anomaly | Isolation Forest | 15 sender-behaviour features | how unusual this is for the sender (unsupervised, needs no labels) |
| Fusion | logistic regression over the three scores | — | fitted and compared, **not served** (§4) |
| Calibration | Platt scaling | served score | probability; monotone, so ranking is unchanged |
| Agent risk | robust peer z-scores (median/MAD) | 8 agent metrics | review list of agents with plain-language reasons |
| Ring detection | connected components | wallets linked by shared handset or transfers | groups of suspicious wallets |

Only send-money and cash-out are scored. Every transaction type updates feature state.

## 2. Data and splits

Splits are by time. Nothing is shuffled across time.

| Fold | Days | Scored rows | Victim-loss transfers | Used for |
| --- | --- | --- | --- | --- |
| train | 0–74 | 387,068 | 847 | fitting the three models |
| val_a | 75–84 | 55,684 | 181 | early stopping |
| val_b | 85–94 | 54,406 | 153 | fusion weights, calibration, thresholds, model selection |
| test | 95–119 | 134,545 | 695 | evaluation only |

- Features for a transaction use only state from **before** it (tested by
  truncated replay). Confirmed-fraud flags become visible only from their timestamp.
- 6,901 ambiguous rows (bait transfers, agent commission farming) are excluded
  from training and from transaction metrics. Agent abuse is evaluated in §8.
- `investment_scam` exists only in the test period: 324 of the 695 test loss
  transfers and ৳1.54M of the ৳3.55M at risk. No model, threshold or rule saw it.

## 3. Headline result (test period)

| | PR-AUC | ROC-AUC | Base rate |
| --- | --- | --- | --- |
| Any fraud-chain transaction | 0.834 | 0.986 | 1.20% |
| Victim transfers only | 0.797 | 0.986 | 0.52% |

On val_b, where all typologies were seen in training, PR-AUC is 0.990. The drop
to 0.834 on test is almost entirely the unseen typology (§6).

## 4. Does each part earn its place? (ablation, top 1% of transactions alerted)

| Score | PR-AUC | Precision | Loss transfers caught | Taka stopped | Taka incl. exit holds | Held-out typology caught |
| --- | --- | --- | --- | --- | --- | --- |
| Hand-written rules baseline | 0.0764 | 11.8% | 13.0% | 34.6% | 50.2% | 0.0% |
| Anomaly model only | 0.1748 | 27.4% | 19.9% | 33.1% | 58.3% | 0.9% |
| Mule model only | 0.3750 | 44.5% | 70.8% | 75.0% | 78.7% | **58.3%** |
| **Transaction model (served)** | 0.8343 | **84.8%** | **73.8%** | **78.1%** | **90.1%** | 45.7% |
| Fusion of the three | 0.8345 | 84.3% | 73.1% | 77.6% | 89.7% | 44.1% |

Findings, stated as they are:

- **Fusion did not win.** On val_b the cross-validated fusion scored 0.9891
  PR-AUC against 0.9896 for the transaction model alone. The rule set before
  training was "serve fusion only if it gains at least 0.002"; it did not, so the
  single model is served. On test the two are within noise of each other.
- **The mule model is what generalises.** Using recipient features alone, it
  catches 58.3% of the unseen typology at this budget, more than any other score,
  and comes close to the full model on loss transfers caught. It is kept as a
  separate output: it scores a wallet before any victim pays (§7), drives ring
  detection, and gives the decision engine an independent signal.
- The anomaly model is weak alone. It is kept as an explanation signal
  ("unusual for this customer") and as a label-free fallback, not for accuracy.
- The rules baseline counts five common hand-written rules (first payment to this
  recipient, recipient younger than 30 days, at least half the balance, new
  device, ৳5,000 or more). That is what a rules-only system achieves: 12% precision.

## 5. Operating points (test period, thresholds fixed beforehand on val_b)

Each tier's threshold is the lowest score on val_b that still meets a precision
target, capped by an alert budget. They were not adjusted on test.

| Tier | Target on val_b | Alerts/day | False alerts/day | Precision | Scams caught | Loss transfers caught | Taka stopped | Taka incl. exit holds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hold | ≥90% precise, ≤1.0% of traffic | 41.2 | 2.3 | 94.5% | 73.6% | 66.5% | 72.8% (৳2.59M) | 83.1% |
| step-up | ≥75%, ≤2.0% | 54.0 | 8.4 | 84.5% | 80.0% | 73.8% | 78.1% (৳2.77M) | 90.1% |
| warn | ≥50%, ≤4.0% | 80.2 | 27.8 | 65.3% | 86.9% | 81.4% | 85.1% (৳3.02M) | 96.6% |

Tiers are cumulative (warn includes step-up and hold). Traffic is about 5,400
scored transactions per day; ৳3,549,860 was at risk in the 25 test days.

- *Taka stopped*: value of victim transfers that were alerted.
- *Taka incl. exit holds*: adds money alerted later, when the mule forwarded it
  or cashed it out, counted once per scam and never above what was still
  unrecovered. This assumes the hold on the exit transaction succeeds; it is an
  upper bound on recovery, not a promise.

## 6. By typology (warn tier)

| Typology | In training? | Scams | Scams caught | Loss transfers caught | Taka stopped | Taka incl. exit holds |
| --- | --- | --- | --- | --- | --- | --- |
| account_takeover | yes | 62 | 100% | 100% | 100% | 100% |
| wrong_send | yes | 48 | 100% | 100% | 100% | 100% |
| lottery_fee | yes | 67 | 98.5% | 97.1% | 98.6% | 99.6% |
| impersonation | yes | 31 | 96.8% | 97.9% | 99.7% | 100.0% |
| **investment_scam** | **no** | 152 | **70.4%** | **61.7%** | **66.1%** | **92.3%** |

At the stricter tiers the held-out typology falls further: 54.6% of scams at
step-up and 39.5% at hold. This is the honest measure of how the system handles
a scam it was never shown: it catches most cases at the warning level, mainly
through recipient-side signals, and recovers most of the rest at cash-out, but it
is far from the near-perfect numbers on known typologies. Those near-perfect
numbers are themselves a property of scripted synthetic fraud and should not be
expected on real traffic.

## 7. Mule wallets: caught before the victim pays

Wallet-level evaluation in the test period (14,948 receiving wallets, 145 mules;
a wallet is alerted when its mule score reaches the threshold set on val_b).

| | |
| --- | --- |
| PR-AUC / ROC-AUC | 0.722 / 0.937 |
| Wallets alerted | 134, of which 70.1% are mules |
| Mules detected | 94 of 145 (64.8%) |
| Detected **before any victim had paid them** | 70 |
| Median victim transfers before detection | 0 |
| Detected before the victim's report was confirmed | 82 of the 119 later reported |
| Median lead over the report | 40.2 hours |

## 8. Agents and rings

**Agents** (600 agents, 22 risky: 13 colluding with a cell, 9 farming commission).
Unsupervised: each agent is compared with its peers; no labels are used.

| | |
| --- | --- |
| PR-AUC / ROC-AUC | 0.990 / 1.000 |
| Precision in a review list of 22 | 90.9% |
| Found in that list | 100% of farming, 84.6% of colluding agents |

The data contains honest agents that look odd (transit hubs, onboarding agents),
but this is still an easy setting: 2 of 13 colluding agents are missed and real
agent abuse is subtler than the simulated kind.

**Rings.** 34 rings covering 331 wallets; 94.26% of ring members are true cell
wallets; 32 of 34 rings map to a single cell; 312 of all 507 cell wallets are
covered. Wallets pulled in only by a shared handset ("linked only") include mules
that had not been used yet. Wallets that had their own handset before appearing
on a ring's handset are listed as probable takeover victims, not members.

## 9. Calibration

| | |
| --- | --- |
| Brier score | 0.00568 |
| Mean predicted risk vs observed fraud rate | 0.62% vs 1.20% |
| Scores above 0.5 (809 rows) | predicted 96.2%, observed 99.0% |
| Top decile | predicted 6.2%, observed 11.7% |

The score **under-predicts in the test period** by about half. The cause is the
unseen typology raising the fraud rate after calibration was fitted. High scores
are reliable; mid-range scores are not well calibrated, and scores are bimodal
(most fraud is near 1, the hold threshold sits at 0.050). The risk score is
therefore used as a **ranking with tier thresholds**, and is not shown to
analysts or customers as a literal probability.

## 10. What drives the scores

Share of total gain:

- **Transaction model:** recipient's fast-exit share 24.0%, sender's wallet age
  23.6%, time since sender last received money 11.4%, recipient wallet age 9.4%,
  time since the sender's wallet was last funded 4.7%, away from home 3.6%,
  sender's device age 3.4%, agent's fast-exit share 3.3%.
- **Mule model:** fast-exit share 56.2%, wallet age 12.1%, incoming transfers per
  day 9.7%, cash-out share 3.7%, incoming value in 24h 3.3%.
- **The four features added in v4 are minor inputs.** Hops to a flagged wallet
  carries 0.7% of the mule model's gain; none of the four is among the
  transaction model's fifteen largest (each below 0.6%). See §11.

Per-alert reasons come from exact SHAP contributions of these models (tested to
sum to the model output), not from a separate explanation model.

## 11. What changed between versions, and what that means for the test set

v1 looked better than it was. Two device features carried 73% of its gain because
in the first simulator only scammers shared handsets; it caught 0% of the held-out
typology, saturated its probabilities at 1.0, and ranked agents perfectly because
only bad agents were unusual. We treated that as a data flaw, revised the
simulator once to add the legitimate behaviour that was missing (shared family
and shop handsets, hub and onboarding agents, cells that do not always reuse
handsets), regenerated everything and retrained. In v2 the device-sharing
features are below 1% of gain.

Three choices were made after seeing v1's test results, so the test period is
**not** strictly untouched in the sense of "looked at once":

1. the simulator revision above (the test period was regenerated with it);
2. thresholds moved from a fixed alert-rate quantile to precision targets capped
   by a budget, because the quantile capped recall when fraud was more common
   than the budget;
3. isotonic calibration was replaced by Platt scaling, which has no ties.

None of these was tuned to a test metric, v2's thresholds and model selection
were fixed on val_b before v2's test evaluation, and v2 was evaluated on test
once.

**v4 adds four features and is a fourth look at the same test period.** The
feature list grew from 57 to 61: speed implied by the sender's last two
locations (impossible travel), hops from the sender and from the recipient to
the nearest confirmed-fraud wallet (up to three), and the number of contacts
the two wallets share. They were added to cover the feature specification, not
picked by a test metric; training, thresholds and model selection used the same
procedure and v4 was evaluated on test once. Against v2:

| Test period | v2 | v4 |
| --- | --- | --- |
| PR-AUC, any fraud-chain transaction | 0.823 | 0.834 |
| Precision at the 1% budget | 82.0% | 84.8% |
| Warn tier: alerts/day | 82.8 | 80.2 |
| Warn tier: precision | 62.5% | 65.3% |
| Warn tier: scams caught | 87.8% | 86.9% |
| Warn tier: taka incl. exit holds | 96.7% | 96.6% |
| Held-out typology, scams caught at warn | 72.4% | 70.4% |
| Legitimate payments interrupted (§13) | 0.59% | 0.53% |

So v4 is **a little more precise and a little less sensitive**: fewer false
alerts for about one percentage point of scams caught, and it is slightly
worse, not better, on the scam type it never saw. The new features account for
little of this (§10); the transaction model also stopped later (667 trees
against 304), and differences of this size are within what a different seed
would produce. Nothing here supports a claim that v4 is clearly the better
model, only that it is not worse and uses the full feature specification.

Older versions cannot be served by the current code: a bundle whose feature
list differs from the engine's is rejected at load, so rolling back to v2 means
checking out the code that built it.

A stricter protocol would evaluate on a world generated with a seed never used
during development (`python -m fraudlens.simulator.generate --seed N --out
DIR`, then features and `train --data DIR --no-promote`); that confirmation run
has not been done yet.

## 12. Limitations and responsible use

- **Synthetic data.** Fraud prevalence (0.29% of transactions, 1.2% of scored
  rows in test) is higher than real traffic, and scripted fraud is easier than
  real fraud. Precision in production would be lower at the same thresholds;
  thresholds must be re-fitted on real data.
- **Labels.** Training uses ground-truth labels. In production only reported
  cases are labelled (about half), which biases what the model learns. §15
  measures what that costs and what wins it back.
- **No adaptation.** The scammers behind every number above do not react to the
  controls. §14 adds scammers that do, and measures the decay.
- **Young wallets and fast cash-out are risk signals.** New customers and
  remittance receivers legitimately show both. The data contains such customers
  as hard negatives. This is the main fairness risk, and the fairness report
  (§13) measures it: wallets under 30 days old are alerted on legitimate
  payments several times more often than average.
- **No protected attributes** are used as features: no name, gender, age, NID or
  religion exists in the data. District and urban/rural are used only relatively
  (away from home, same district as recipient), never as a raw location feature.
- **Human oversight.** The models only produce scores. The decision engine
  ([DECISION_POLICY.md](DECISION_POLICY.md)) pauses a held transaction for
  analyst review and refuses to load a policy that removes that review. Freezing
  a wallet is only ever a request that a second person must approve (PLATFORM
  §5). No model output blocks money by itself.
- **Artifacts** are pickled/joblib files produced by this pipeline and must never
  be loaded from an untrusted source.

## 13. Fairness, feedback, shadow mode and drift

These numbers are read from `insights.json` (`make insights`), from the
retrained version's `manifest.json` (`make retrain`) and from the running service
after the test period was replayed through it. The console shows all of them.

### Who pays for false alarms

False-alert rate: the share of a group's **legitimate** scored payments that were
interrupted (warn or above). Overall it is 0.53%, and 0.04% are held. Groups under
200 rows are not reported.

| By the customer sending | False-alert rate | Times the overall rate |
| --- | --- | --- |
| Account under 30 days old | 3.17% | 6.0 |
| Account 30–179 days | 0.42% | 0.8 |
| Account 1 year or more | 0.45% | 0.9 |
| Rural / urban | 0.52% / 0.54% | 1.0 / 1.0 |
| App / USSD | 0.58% / 0.41% | 1.1 / 0.8 |
| Balance under ৳1,000 / ৳10,000 or more | 0.27% / 0.70% | 0.5 / 1.3 |
| Region, highest (Sylhet) and lowest (Barishal) | 0.75% / 0.22% | 1.4 / 0.4 |

| By the wallet receiving | False-alert rate | Times the overall rate |
| --- | --- | --- |
| Account under 30 days old | 8.88% (1.50% held) | 16.4 |
| Account 30–179 days | 0.16% | 0.3 |
| USSD | 1.05% | 1.9 |
| Seller | 0.09% | 0.2 |

- **Account age is the gap that matters.** One legitimate payment in eleven to a
  wallet under a month old is interrupted, and one in sixty-seven is held. A young
  receiving wallet is also the strongest honest signal of a mule, so the gap
  cannot be removed by dropping the feature without losing most of the recall.
  What limits the harm is the form of the interruption: a warning the customer
  can dismiss, and a hold with a 30-minute review deadline. A deployment should
  watch this rate and consider a separate, higher threshold for new wallets.
- Location, channel and balance gaps are within about a factor of two. No
  protected attribute exists in the data, so nothing is known about gender, age
  or religion, and nothing can be claimed.
- These are rates on synthetic customers. The report is a method and a place on
  the dashboard, not evidence about real people.

### Verdicts as labels

This subsection and the next (shadow mode) were measured with v4 as the served
model and `v5`, the challenger retrained from it.

After the replay, 176 older cases were closed: 105 confirmed fraud, 62
legitimate, 9 inconclusive (which label nothing). The retraining job added 970
labelled alerts (873 fraud, 97 legitimate), with the features that were served
for them, to the original training data and registered a challenger. It promotes
nothing.

Compared on the 34,349 test rows **after the last label** (the only rows neither
model could have learnt from through feedback):

| | Served model | Retrained on verdicts |
| --- | --- | --- |
| PR-AUC | 0.774 | 0.912 |
| Precision at the 1% alert budget | 75.0% | 95.1% |
| Victim transfers caught | 65.5% | 76.6% |
| Money caught | 69.8% | 86.8% |

Read this with three cautions:

1. **The reviewers are simulated and always right.** `make review` closes cases
   with the simulator's ground truth, which no analyst has. Real verdicts are
   slower, sometimes wrong and sometimes missing. This is the upper bound of what
   feedback can give, and it is demo scaffolding.
2. **The gain is probably the unseen scam type.** The verdicts are the first
   labelled examples of it any model has seen, and it is where the served model
   is weakest (§6). The comparison has not been broken down by typology, so this
   is the likely explanation, not a measured one. Without a new typology to
   learn, expect a much smaller gain.
3. **Feedback labels are a biased sample.** Only alerted payments are reviewed, so
   the labels say where the model was right or wrong among its alerts and nothing
   about the fraud it let through. They are added to the training data and never
   replace it.

### Shadow mode

The challenger scored all 136,180 served decisions next to the served model. The
two agree on the tier for 98.5% of them. The challenger would alert on 1.78% of
traffic against 1.61%, and hold 1.06% against 0.78%: it is more willing to hold,
which means more review work, and that is a decision for a person to weigh before
promotion. Among alerts an analyst has closed, it would hold all 923 confirmed
frauds and allow 89 of the 100 false positives, which is expected and proves
little: those are the labels it was trained on. Outcomes are known only for
closed alerts, so the 910 payments the challenger alone would alert on are a
count without an outcome.

### Drift

Population stability index of each feature and of the score, test period against
training period: 34 features stable (below 0.1), 10 to watch, 17 shifted (above
0.25). The score itself stays stable (PSI 0.0004 to 0.0007 by week) while the
weekly fraud rate moves between 0.97% and 1.46%. The most shifted inputs are
lifetime counts and ages (handset age, payments sent, an agent's cash-outs),
which grow with time by construction, so a shifted input is a prompt to look,
not an alarm; the alert rate (1.49% on test against 1.24% on validation) is the
signal to act on. The sender's hops to a flagged wallet is the second most
shifted input (PSI 3.1) for the same reason: confirmed flags only accumulate, so
more wallets sit within three hops of one as time passes. A deployment that
keeps this feature should expire or age old flags. The live endpoint measures
the same on the latest decisions actually served.

## 14. Adaptive adversaries

The scammers in §2 to §13 follow fixed scripts. Real scammers learn which
scripts get through. This section deploys scammers that do, against the served
model (v4), and asks three questions: how fast recall decays, whether weekly
retraining keeps up, and whether the drift monitor notices.

**Set-up** (`make adversary`, about five minutes; writes
`artifacts/reports/adversary.json`). The default world is re-simulated to day 120
(it reproduces the stored dataset exactly, which the script checks). Then 8 new
fraud cells open and run 6 one-week rounds after an 8-day warm-up. Every scam
draws one of six tactics:

| Tactic | What changes |
| --- | --- |
| none | the usual script for the typology |
| split_amounts | each payment split into 2 to 6 parts below ৳1,000 to ৳2,500, 5 to 30 minutes apart |
| seasoned_mule | the money goes to an existing customer's wallet over 180 days old, not a young one |
| delayed_cashout | the mule waits 6 to 48 hours before forwarding or cashing out (past a 30-minute hold) |
| fan_out | each payment goes to the least-used of up to 11 mules |
| mimic_hours | the scam runs at the victim's own usual hour |

Round 0 runs half the scams on the usual script and spreads the rest evenly.
After each round the cells see which scams had no victim transfer alerted at the
warn tier. They then shift their mix by multiplicative weights on that success
rate (step 3.0, floor 3% per tactic, tactics run fewer than 5 times keep their
weight). Each round is scored in full with no subsampling: 34,000 to 46,000
transactions and 84 to 117 scams. There are four arms, all on the same world and
seed:

- **control**: the mix never changes. This is natural decay.
- **frozen**: the scammers adapt and v4 stays deployed.
- **retrain**: after every round the model is refitted with the `make train`
  recipe. It uses the training fold plus what an operator would know by then:
  the verdict on every alerted transaction, and the complaints of reported scams.
  Thresholds are re-chosen on val_b.
- **drift_gated**: retrain only when the live drift monitor
  (`mlops/drift.py`: score PSI against training, or the alert rate) leaves "stable".

**Scams caught (case recall) at the warn tier, by round:**

| Arm | 0 | 1 | 2 | 3 | 4 | 5 | Pooled PR-AUC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| control | 96.7% | 94.6% | 90.9% | 93.1% | 89.5% | 94.8% | 0.977 |
| frozen | 96.7% | 94.0% | 90.0% | 87.2% | 87.0% | **79.6%** | 0.961 |
| retrain | 96.7% | 97.0% | 90.5% | 92.6% | 97.8% | 98.0% | 0.960 |
| drift_gated | identical to frozen: the monitor never fired | | | | | | 0.961 |

What the numbers say:

- **The scammers found the weak spot, and it is seasoned mules.** Against the
  frozen model, seasoned_mule's share grew from 10% to 48% of scams. Pooled over
  the six rounds, the model caught 74.4% of seasoned-mule scams (191 scams),
  against 92% to 100% for every other tactic. The other tactics barely hurt:
  splitting, delaying, fan-out and hour-mimicking were each caught at 92% or
  more. The model's two largest signals are the receiver's fast-exit share and
  its age. Delaying cash-out does not hide the first signal on a mule that has
  other victims, and only an old wallet hides the second.
- **Frozen decays, control does not.** By round 5 the frozen model caught 79.6%
  of scams against 94.8% for the same model facing non-adaptive scammers. That is
  15 points lost to adaptation alone. Money is more robust: with holds on the
  mule's exit transactions, 98.3% of the taka is still covered in round 5. The
  scams that get through are caught at cash-out.
- **Weekly retraining keeps recall, at a price in alerts.** The retrained arm
  catches 98.0% of scams in round 5 and 90.1% of seasoned-mule scams pooled. But
  the first retrain raised alerts from 76 to 234 a day and cut precision from
  72% to 22%. By round 5 it was still at 118 alerts a day and 46% precision,
  against 66 and 79% for the frozen model. The feedback rows are mostly alerted
  rows and complaints, so they are far richer in fraud than real traffic, and
  thresholds re-chosen on val_b do not correct for it. Retraining on verdicts
  needs its alert budget re-checked before promotion. That is exactly what
  shadow mode (§13) is for.
- **The drift monitor did not notice.** In the frozen arm the population score
  PSI stayed between 0.0007 and 0.0044 and the alert rate between 1.29% and
  1.39% (1.24% on validation). So `drift_gated` never retrained and matched the
  frozen arm round for round. Fraud is about 1% of traffic, and a tactic shift inside that 1%
  barely moves the whole score distribution. Pointed at confirmed fraud only
  (val_b fraud as the score reference), the same measure rose from 0.40 in round
  0 to 0.85 in round 5. But the control arm already reads 0.24 to 0.44, so the
  standard 0.25 threshold does not separate the two. A fraud-segment monitor
  needs its own baseline. The signals that did move are per-tactic catch rates
  on confirmed cases, which only labels give. Drift on unlabelled traffic is no
  substitute for them.
- **What carries detection after adaptation** (mean |SHAP| share on victim
  transfers, round 0, then round 5). The frozen model keeps leaning on the same
  features: receiver fast-exit share 14.8% then 12.9%, receiver age 10.8% then
  8.5%. The retrained model moves away from receiver age (to 7.8%) and fast exit
  (to 9.5%). It moves toward the sender's handset age (7.7% to 13.2%, now the
  largest), the receiver's 7-day fan-in (3.3% to 6.6%), its number of recipients
  (3.4% to 6.4%) and prior payments between the pair (3.8%, new in the top 8).
  These are the signals an old wallet does not fake: a victim on a new handset,
  and many unrelated senders converging on one receiver.

**Caveats.** One seed and one world. Each round has 84 to 117 scams and each
tactic 4 to 53, so a single round's number moves by a few points on a few scams.
Read the trend, not a single cell. The adversary sees ground truth on what was
alerted, which is more feedback than real scammers get. The tactic set and its
parameters are assumptions (DATA_ASSUMPTIONS.md). The retrained models are
experiment-only and are not registered.

## 15. Under-reporting: training on reported scams only

Every number here is read from `backend/artifacts/reports/label_realism.json`,
which `make label-realism` writes (about 5 to 15 minutes). It does not change
the served model or any number above.

The served model learnt from every scam the simulator ran. A wallet provider
only learns about the ones victims report. To measure what that costs, the
transaction model is retrained, with the same LightGBM settings, training fold
and early stopping as `make train`, on the labels a provider would have had,
and every version is scored on **every** scam in the test period. Each is
compared at the false-positive rate the served model (v4, warn tier) runs at
on test, 0.523% of honest payments, so the rows differ only in how they rank.
Five seeds (7, 11, 13, 17, 19) vary both the reporting draw and the model;
values are mean ± sd.

**How reporting is simulated.** A case is reported with a probability that
averages the simulator's `report_rate` of 50% but is higher for larger losses,
urban victims, accounts held for a year or more (the data has no person age)
and account takeovers, and lower for lottery-fee and investment scams. Reports
arrive after the simulator's delay (median 1.5 days, 5 for investment scams);
only those in by the start of the test period count. A reported case labels all
its transactions, including the mule's forwards and cash-outs. Unreported fraud
is labelled clean. These weights are assumptions, listed in the JSON, not
measurements. In the training data this labels 82% of takeovers, 56% of
impersonations, 40% of wrong-send and 35% of lottery-fee cases.

| Training labels | Scams caught | Loss transfers caught | Taka stopped | PR-AUC |
| --- | --- | --- | --- | --- |
| Every scam (as served) | 87.3% ± 0.5 | 81.7% ± 0.7 | 85.0% ± 0.6 | 0.831 ± 0.003 |
| Reported only, half at random | 72.9% ± 5.3 | 66.4% ± 6.0 | 72.3% ± 7.4 | 0.595 ± 0.038 |
| **Reported only, biased** | **78.8% ± 2.7** | 71.4% ± 2.2 | 77.9% ± 1.9 | 0.724 ± 0.019 |
| + positive-unlabelled learning | 79.4% ± 1.5 | 72.1% ± 1.9 | 79.5% ± 1.9 | 0.762 ± 0.020 |
| + same, report rate given as 50% | 79.0% ± 1.8 | 71.9% ± 1.8 | 78.8% ± 2.0 | 0.754 ± 0.020 |
| + labels through mule wallets | 82.8% ± 2.2 | 75.6% ± 1.7 | 80.9% ± 1.5 | 0.770 ± 0.043 |
| + analyst verdicts on alerts | 83.0% ± 2.0 | 76.1% ± 2.1 | 81.9% ± 1.6 | 0.769 ± 0.033 |
| + mule-wallet labels and verdicts | 82.4% ± 1.4 | 75.0% ± 1.4 | 79.7% ± 2.1 | 0.781 ± 0.023 |

Scams caught, by typology (mean over seeds):

| Training labels | account_takeover | impersonation | lottery_fee | wrong_send | **investment_scam** (unseen) |
| --- | --- | --- | --- | --- | --- |
| Every scam | 100% | 96.8% | 98.5% | 100% | 71.2% ± 1.3 |
| Reported only, biased | 100% | 96.8% | 95.8% | 100% | **52.4% ± 5.8** |
| + positive-unlabelled | 100% | 96.8% | 95.2% | 100% | 54.1% ± 3.5 |
| + labels through mule wallets | 100% | 96.8% | 96.7% | 100% | 61.5% ± 4.9 |
| + analyst verdicts | 100% | 96.8% | 96.7% | 100% | 61.8% ± 4.5 |

What this shows:

- **Under-reporting costs about 8.5 points of scams caught** (87.3% to 78.8%)
  at the same false-alert rate, and PR-AUC falls from 0.831 to 0.724.
- **It hides the scam the model has not seen, not the ones it has.** Known
  typologies stay at 96 to 100% caught: scripted fraud is easy enough that half
  the examples suffice. The held-out investment scam falls from 71.2% to 52.4%.
  With fewer examples the model learns the narrow signature of each reported
  pattern rather than the general shape of a mule chain, which is what caught
  the new scam.
- **Who reports matters less than how many.** Biased reporting did better than
  reporting at random (78.8% against 72.9%), with far less spread between
  seeds, because it keeps the large, cleanly labelled takeover and
  impersonation cases; it also labels more fraud rows (1,293 against 1,066 on
  average). The random-reporting result swings by ±5 points with the draw.
- **Positive-unlabelled learning does little.** Elkan–Noto weighting lifts
  PR-AUC to 0.762 but scams caught by under one point, within noise, and
  telling it the true report rate does not help. Its assumption, that labels go
  missing at random among frauds, is what biased reporting breaks.
- **What helps is the network and the analysts.** Spreading soft labels (weight
  0.7) from reported mule wallets to their other incoming and outgoing money
  within three days labels about 755 extra rows, 91% of them really fraud, and
  wins back 4 points of scams caught and 9 of the unseen typology. Analyst
  verdicts on the top 1% of a model's alerts over the last 15 training days
  (about 978 reviews, 230 of them fraud no victim had reported) win back the
  same. Together they are no better than either alone: they recover largely the
  same rows. Roughly half the cost of under-reporting is recovered; the unseen
  typology stays about 10 points below the ground-truth model.

Cautions: the reviewers are simulated and always right (as in §13); the
operating threshold is set on test negatives so every row has the same false-
alert rate, which isolates ranking but is not how a threshold would be chosen in
production; the report propensities are assumptions; and five seeds give
standard deviations of 1.5 to 5 points, so differences under about 3 points
between recovery methods are not meaningful.

## 16. Reproduce

```
make data features train     # about one minute; writes backend/artifacts/models/<version>/
make policy insights         # policy_report.json and insights.json (fairness, drift, threshold sweep)
make adversary               # §14: adaptive scammers vs frozen, retrained and drift-gated models
make label-realism          # §15: reported-only labels and recovery methods
make test                    # 203 tests, including leakage and round-trip checks
```

`report.json` holds every number above; `manifest.json` holds thresholds,
calibration, feature list and data seed; `test_scores.parquet` holds the score of
every test transaction.
