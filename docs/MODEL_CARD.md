# Model card — FraudLens risk models, version v4

Every number below is read from `backend/artifacts/models/v4/report.json`, which
`make train` writes, except where §13 and §15 say otherwise. Nothing here is
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
  cases are labelled (about half), which biases what the model learns.
- **No adaptation.** Simulated scammers do not react to the controls.
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

## 14. Reproduce

```
make data features train     # about one minute; writes backend/artifacts/models/<version>/
make policy insights         # policy_report.json and insights.json (fairness, drift, threshold sweep)
make test                    # 203 tests, including leakage and round-trip checks
```

`report.json` holds every number above; `manifest.json` holds thresholds,
calibration, feature list and data seed; `test_scores.parquet` holds the score of
every test transaction.

## 15. External validation

Everything above comes from our own simulator. This section asks two questions:
how precise are the headline numbers, and does the modelling approach hold up on
fraud data we did not generate? `make external` reproduces all of it (it downloads
about 560 MB once and verifies SHA-256 checksums; code in
`backend/src/fraudlens/external/` and `backend/src/fraudlens/models/bootstrap.py`).
Numbers are read from `backend/artifacts/models/v4/bootstrap_ci.json`,
`backend/artifacts/external/paysim.json` and `backend/artifacts/external/baf.json`.

No real MFS transaction data was available to us. Both external datasets are
themselves synthetic, but generated by other teams from real data: PaySim from a
month of logs of a real African mobile-money service, BAF from a real bank's
account-opening data. Neither contains authorised-push-payment scams, so this is
a test of the method, not of the scam-detection claim itself.

### Confidence intervals on the headline (synthetic test period)

1,000 bootstrap replicates, percentile 95% intervals. The model, calibration and
thresholds are fixed; only the test sample is resampled, two ways: whole days (25
of them, which keeps a day's and a scam's transactions together) and scams (each
of the 360 scams as one unit, each legitimate payment as one unit).

| Warn tier on v4 | Point | 95% CI, by day | 95% CI, by scam |
|---|---|---|---|
| Scams caught (model warn threshold) | 86.9% | 82.7–91.0% | 83.3–90.6% |
| False-alert rate on legitimate payments (model warn threshold) | 0.52% | 0.47–0.58% | 0.49–0.57% |
| Scams caught (policy v1, warn or above) | 87.5% | 83.6–91.3% | 83.9–90.8% |
| False-alert rate (policy v1, warn or above; §13's 0.53%) | 0.53% | 0.47–0.59% | 0.49–0.57% |
| PR-AUC, any fraud-chain transaction | 0.834 | 0.806–0.858 | 0.807–0.858 |

The headline "86.9% of scams at 0.53% false alerts" mixes two operating points:
86.9% is the model's warn threshold (§5), 0.53% is the served policy's (§13). At
the policy's own point the figures are 87.5% and 0.53%. Either way, with 360 scams
the recall is known to about ±4 points.

### PaySim (mobile money)

6,362,620 transactions over 743 hours. Fraud is account takeover: the balance is
TRANSFERred out and cashed out. Only TRANSFER and CASH_OUT rows are scored (the
only types that are ever fraud). Split by hour: train 1–300 (1,780,544 rows,
3,401 frauds), validation 301–400 (748,992; 1,076), test 401–743 (240,873;
3,736). The test hours have far less legitimate traffic, so the base rate there is
1.55% against 0.19% in training.

Features mapped from FraudLens, each computed from earlier rows only:
receiver-centric (earlier incoming payments, in the last 24 hours and in value;
hours since the receiver last received and since it was first seen; earlier
incoming TRANSFERs; amount against the receiver's usual incoming amount), velocity
(the 24-hour counts; the sender's earlier payments), and cash-out timing (hours
since the sender last received money, amount against what it received). The last
one cannot see PaySim's fraud chain: the account that receives a fraudulent
TRANSFER never appears as the one that cashes out (0 of 4,097). Devices, agents,
districts, wallet age and confirmed-fraud flags have no equivalent.

Thresholds are fixed on the validation hours at each target false-positive rate
and applied unchanged to test, as in §5. Because the test hours look different,
the rate reached on test drifts from the target; both are shown. The last column
sets the threshold on the test set itself, which no deployment can do but which
makes scores comparable at exactly 0.5%.

| Score | PR-AUC | ROC-AUC | Recall at 0.5% target (FPR reached on test) | Recall at 1% (FPR reached) | Recall at exactly 0.5% FPR on test |
|---|---|---|---|---|---|
| PaySim's own rule (TRANSFER over 200,000) | 0.019 | 0.502 | 0.3% (0.00%) | 0.3% (0.00%) | 0.3% |
| Amount only | 0.145 | 0.704 | 0.0% (0.01%) | 8.8% (0.13%) | 13.3% |
| Isolation Forest (no labels) | 0.022 | 0.530 | 4.1% (0.90%) | 6.6% (2.02%) | 3.1% |
| Logistic regression, same features | 0.323 | 0.790 | 25.1% (0.26%) | 32.4% (0.54%) | 31.5% |
| **LightGBM, same recipe as v4** | **0.468** | **0.940** | 57.3% (1.25%) | 68.0% (2.03%) | 32.1% |
| LightGBM without hour of day | 0.469 | 0.903 | 32.9% (0.32%) | 46.8% (0.91%) | 36.7% |
| LightGBM plus pre-transaction balances | 0.991 | 1.000 | 99.97% (0.54%) | 99.97% (1.15%) | 99.95% |

Bootstrap by test hour (343 hours, 500 replicates): LightGBM PR-AUC 0.394–0.555,
recall at the 0.5% validation threshold 53.4–60.7%; without hour of day, PR-AUC
0.420–0.519 and recall 30.9–35.1%.

What this shows:

- **The approach beats every simple baseline on external MFS data**: PR-AUC 0.47
  against 0.32 for logistic regression on the same features and 0.15 for amount
  alone. The receiver-centric features carry the model: without hour of day the
  top features are amount (37% of gain), amount against the receiver's usual
  incoming amount (20%), hours since the receiver last received (15%) and the
  receiver's earlier incoming payments (12%).
- **At a 0.5% false-positive rate it catches about a third of PaySim fraud**
  (32–37%), not 87%. PaySim's fraud is single-step account takeover with no mule
  chain, no shared devices and no history to learn a victim's pattern from, which
  is where most of the FraudLens signal lives (§10). The number to compare with
  our 86.9% is not this one: the tasks differ.
- **Hour of day helps ranking but hurts stability.** PaySim spreads fraud evenly
  over the day while legitimate traffic follows a daily cycle, so the model leans
  on hour (32% of gain); in the quieter test hours its validation threshold lets
  through 1.25% false positives instead of 0.5%. Without it the threshold holds
  (0.32%). The same lesson as §11: a signal that is an artefact of the generator
  looks strong and does not travel.
- **PaySim's balance fields are a known artefact.** 97.8% of frauds move exactly
  the sender's whole balance and no legitimate TRANSFER or CASH_OUT does, so adding
  the pre-transaction balances gives near-perfect scores. We report it to show
  why published PaySim results near 0.99 say little, and we do not count it.
- Isolation Forest alone is near chance here (ROC-AUC 0.53), consistent with §4,
  where the anomaly model is a weak detector on its own.

### Bank Account Fraud (BAF, NeurIPS 2022), Base variant

1,000,000 account applications over 8 months, 1.1% fraudulent, from OpenML
(dataset 46793). This is account opening, not payments; the nearest FraudLens task
is the mule-wallet model (wallets opened to receive scam money). Mapped ideas:
application velocity (6 h, 24 h, 4 weeks), postcode and branch velocity, shared
devices and identities (distinct e-mails per device and per date of birth).
Split by month as in the benchmark: train 0–4 (675,666; 6,740 frauds), validation
5 (119,323; 1,411), test 6–7 (205,011; 2,878). All columns are used as given.

| Score | PR-AUC | ROC-AUC | Recall at 5% target (FPR reached) | Recall at exactly 5% FPR on test | Recall at exactly 1% |
|---|---|---|---|---|---|
| Velocity counts only | 0.014 | 0.486 | 2.8% (3.04%) | 4.9% | 0.9% |
| Credit-risk score only | 0.037 | 0.672 | 19.8% (4.93%) | 19.8% | 6.6% |
| Isolation Forest (no labels) | 0.018 | 0.576 | 8.1% (5.49%) | 7.3% | 1.5% |
| Logistic regression | 0.143 | 0.863 | 46.9% (5.22%) | 45.9% | 20.3% |
| **LightGBM, same recipe as v4** | **0.193** | **0.893** | 58.8% (6.03%) | 54.8% | 25.3% |

Bootstrap by application (500 replicates): LightGBM PR-AUC 0.179–0.207, recall at
the 5% validation threshold 57.2–60.7%.

The same recipe, unchanged, beats logistic regression and every single-signal
baseline on a public benchmark built from real bank data. BAF's velocity columns
count applications across the whole population, not per customer, and carry no
signal on their own (ROC-AUC 0.49); the model's top features are housing status,
device OS, name–e-mail similarity and address history, not the FraudLens ideas.
So BAF supports the modelling recipe, not our particular features.

### What this does and does not establish

- It establishes that the modelling recipe (gradient-boosted trees on
  receiver-centric and velocity features, thresholds fixed in advance on later
  validation data) is not an artefact of our simulator: on two independent public
  datasets it has the highest PR-AUC of the scores tried (leaving aside PaySim's
  balance artefact). At a fixed low false-positive rate its lead over logistic
  regression is small on PaySim (32–37% against 31.5% at exactly 0.5%) and clear
  on BAF (54.8% against 45.9% at exactly 5%). Intervals were computed for the
  LightGBM scores only.
- It does not validate the 86.9% figure. No public dataset contains MFS
  authorised-push-payment scams with mule chains, so the scam-specific numbers in
  §3–§7 still rest on synthetic data. The test that would settle it is a
  back-test on a provider's own labelled transactions.
- Both external datasets are synthetic derivatives of real data, with artefacts of
  their own (shown above for PaySim).
