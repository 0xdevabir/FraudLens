# Decision policy — FraudLens policy v2 on model v4

The models rank transactions by risk ([MODEL_CARD.md](MODEL_CARD.md)). This
document covers the layer that turns a score into an action: what happens at each
level of risk, which business rules apply whatever the score is, what the system
does when the model is unavailable, and how each alert explains itself.

Every number below is read from `backend/artifacts/models/v4/policy_report.json`,
which `make policy` writes. The rule-selection table in §3 is the exception: it
records the validation measurements made when the rules were chosen, on the
previous model, and was not repeated for v4. The data is synthetic
([DATA_ASSUMPTIONS.md](DATA_ASSUMPTIONS.md)).

Code: `backend/src/fraudlens/decision/`. Policy file:
`backend/src/fraudlens/decision/policies/v2.yaml`, which is v1 plus the three
behaviour rules of §3 (R05–R07). `v1.yaml` is kept unchanged, so decisions recorded
under it can still be reproduced.

## 1. What a decision answers

For each send-money or cash-out, the engine returns one `Decision` that answers
the three questions an analyst asks:

| Question | Where it is in the decision |
| --- | --- |
| What happened? | `evidence.transaction` (wallet numbers masked), first paragraph of the case note |
| Why is it risky? | `risk_score` and `risk_band`, `reasons` (English and Bangla), `rule_trace`, `similar_cases` |
| What should upay do next? | `tier`, `action`, `requires_review`, `customer_message`, `recommended_actions` |

It also records `model_version`, `policy_version`, `mode` and `decided_by`, so any
decision can be traced to the exact model and policy that produced it.

## 2. Tiers

| Tier | Action | Who decides | Customer sees |
| --- | --- | --- | --- |
| allow | proceed | — | nothing |
| warn | show a warning, customer may continue | customer | a scam warning in their language |
| step-up | extra authentication and a 30-minute cooling-off | customer, after re-authenticating | why the payment is paused and how to continue |
| hold | hold for review, 30-minute review target | **a human analyst** | that the money is still in their wallet and upay will check |

The engine never refuses a payment and never freezes a wallet. The strongest
thing it does is pause one transaction and put it in front of a person. This is
enforced in code, not left to configuration: a policy file whose `hold` tier does
not set `human_review: true` fails validation and will not load. Freezing the
receiving wallet is only ever a *recommended action*, marked
`needs_second_approver`.

Score thresholds for the tiers come from the model version's manifest (fitted on
val_b to precision targets under an alert budget, see MODEL_CARD §5). The policy
can override them; overrides must keep `0 < warn ≤ step_up ≤ hold < 1`.

The score shown to people is a 0–100 number anchored to the tiers (warn starts at
40, step-up at 60, hold at 80). It is a ranking aid, not a probability, because
the calibrated probability under-predicts in the test period (MODEL_CARD §9).

## 3. Rules are data, kept apart from the model

A rule is `field op number` conditions plus an effect (`raise_to` a tier, or
`cap_at` a tier). Conditions are checked by a fixed comparison table; the policy
file is parsed with `yaml.safe_load` and validated with unknown keys forbidden, so
changing the policy never executes anything. A rule whose input is missing is
recorded as `not_evaluated` and does not fire.

Order of application: model tier → `raise_to` rules (highest wins) → `cap_at`
rules, unless a `hard` rule fired. Rules can therefore never silently lower a
decision the model made, except through an explicit cap, and nothing lowers a
hard rule.

| Rule | Applies to | Condition | Effect |
| --- | --- | --- | --- |
| R01_RECIPIENT_CONFIRMED_FRAUD | send-money | receiving wallet is a confirmed-fraud wallet | hold (hard) |
| R02_SENDER_CONFIRMED_FRAUD | both | sending wallet is a confirmed-fraud wallet | hold (hard) |
| R03_FRAUD_HANDSET_NEW_ON_WALLET | both | first use of this handset on the wallet, and the handset is linked to confirmed fraud | step-up |
| R04_RECIPIENT_LOOKS_LIKE_MULE | send-money | mule model scores the receiving wallet above its wallet threshold | warn |
| R05_LARGE_PAYMENT_FROM_UNUSUAL_PLACE | both | at most 2% of the wallet's payments were made from this place, and the amount is 3 or more deviations above its usual | warn |
| R06_LARGE_PAYMENT_FROM_UNFAMILIAR_NETWORK | both | the wallet has never paid over this network, and the same amount test | warn |
| R07_LARGE_PAYMENT_FROM_UNUSUAL_PLACE_AND_NETWORK | both | R05 and R06 together | step-up |

### The customer's own habits (v2)

R05–R07 compare a payment with the history of the wallet that makes it, not with
other customers. The feature engine keeps two counts per wallet, next to the state
the model reads:

- **Places.** How many of the wallet's own transactions were made from each
  district. `s_place_share` is the share made from the current district or one
  bordering it, so home, office and campus are all usual, and so is the next
  district over. It is missing until the wallet has made 10 transactions.
- **Networks.** The same count per network the request came over.
  `s_network_share` is missing when the channel sends no address, and until 3 of
  the wallet's payments have carried one. At most 32 networks are remembered per
  wallet; the least used is dropped first.

A missing value means the rule is recorded as `not_evaluated`: a new customer, or
a channel that sends no address, is never penalised for having no history.

Being somewhere new is not suspicious by itself, so every rule also needs an
amount far above what the wallet normally sends. These two values are inputs to
rules only. They are not model features: the model was not retrained and its 61
features are unchanged. A rule that fires gives the customer the `unusual_access`
message ("from a place or a network your wallet is not normally used from") and
gives the analyst `CONFIRM_PLACE` / `CONFIRM_NETWORK` as next steps.

### How the rules were chosen

Candidates were measured on the validation folds (val_a and val_b), never on test.

| Candidate | Result on validation | Decision |
| --- | --- | --- |
| Raise when the sender's handset is linked to fraud | 88 extra alerts, all false | rejected |
| Raise when the receiver's handset is linked to fraud | 24 extra alerts, all false | rejected |
| Raise on a new handset draining the balance | 9 extra alerts, all false | rejected |
| Raise when the agent has confirmed-fraud customers | 368 extra alerts, all false | rejected |
| Cap small amounts (under ৳200) at allow | would have suppressed 21 true frauds among 36 warn alerts | rejected: no cap rule in v1 |
| Fraud-linked handset that is also new on the wallet | 19 hits, all fraud, all already held by the model | kept as R03: a guarantee, not a gain |
| Mule alert on the receiver | 9 extra alerts, all false | kept as R04 at the lowest tier only: it is the signal that generalises to an unseen scam (MODEL_CARD §4) |
| Confirmed-fraud sender or receiver | never fires: in the simulator a flagged wallet stops transacting | kept as R01 and R02: policy guarantees that must hold even if the model fails |
| Unusual place alone (`s_place_share` ≤ 0.02) | 673 hits, 8.6% fraud | rejected: mostly customers who are travelling |
| Unusual place and an amount at or above the wallet's largest ever | 61 hits, 45.9% fraud | rejected for the next row |
| Unusual place and an amount 3 deviations above usual (model v4) | 41 hits in 20 days, 65.9% fraud; all 27 frauds are account takeovers the model already holds; 14 honest payments raised from allow | kept as R05 at the lowest tier only |
| Unfamiliar network | cannot be measured: the synthetic history carries no address | kept as R06 at the lowest tier; step-up only together with R05 (R07), whose cost R05 bounds |

Conclusion, stated as it is: **on this data the model already contains everything
the hand-written rules know.** The rules are in the policy as guarantees and as
defence in depth, not because they improve the numbers.

## 4. Results on the test period (25 days, 134,545 scored transactions)

Tier counts:

| | allow | warn | step-up | hold |
| --- | --- | --- | --- | --- |
| Model only | 132,541 | 653 | 322 | 1,029 |
| Model + policy v1 | 132,533 | 661 | 322 | 1,029 |
| Model + policy v2 | 132,520 | 674 | 322 | 1,029 |
| Rules-only fallback (v2) | 134,217 | 261 | 67 | 0 |

What each rule did:

| Rule | Fired | Precision when fired | Changed the tier | Of those, fraud |
| --- | --- | --- | --- | --- |
| R01 | 0 | — | 0 | — |
| R02 | 0 | — | 0 | — |
| R03 | 21 | 100% | 0 | — |
| R04 | 505 | 90.7% | 8 | 2 (two extra victim transfers warned) |
| R05 | 34 | 58.8% | 13 | 0 (thirteen honest payments warned) |
| R06 | 0 | — | 0 | — |
| R07 | 0 | — | 0 | — |

Outcomes, everything at or above each tier:

| | Alerts/day | Precision | Scams caught | Taka stopped | Taka incl. exit holds |
| --- | --- | --- | --- | --- | --- |
| Policy v2, warn | 81.0 | 64.7% | 87.5% | 85.2% | 96.6% |
| Policy v1, warn | 80.5 | 65.2% | 87.5% | 85.2% | 96.6% |
| Policy, step-up | 54.0 | 84.5% | 80.0% | 78.1% | 90.1% |
| Policy, hold | 41.2 | 94.5% | 73.6% | 72.8% | 83.1% |
| Model only, warn | 80.2 | 65.3% | 86.9% | 85.1% | 96.6% |

The policy differs from the model in eight transactions out of 134,545: six false
warnings and two true ones. For the scam type that was never in training
(`investment_scam`), scams caught at warn go from 70.4% to 71.7%.

v2 adds thirteen more warnings, about one every two days, and none of them is a
fraud: every fraud R05 fires on (20 of its 34 hits) is an account takeover the
model already holds. On this data the behaviour rules cost a few warnings shown
to customers who are travelling and catch nothing new. They are there for what
the data cannot show: a takeover the model does not recognise, and rules-only
mode, where R05 is 15 of the 261 warnings. R06 and R07 never fire offline because
no recorded transaction carries a network.

## 5. Rules-only fallback

If the model bundle is missing, fails to score, or returns NaN, the engine logs
the failure and decides on rules alone (`mode: "rules_only"`). It counts seven
simple signals:

first payment to this receiver · receiving wallet under 30 days old · half the
balance or more · first use of the handset · ৳5,000 or more · the receiver moves
money on fast · the handset has been on the wallet under an hour.

Four signals warn, five step up. Points were chosen on validation (three signals:
2.4% of traffic alerted at 13% precision; four: 0.29% at 61%; five: 0.07% at 95%).
**The fallback never holds on points**: without a model there is not enough
evidence to pause someone's money, so the ceiling is step-up. Only the hard
confirmed-fraud rules can still hold.

On the test period the fallback alerts 12.5 transactions a day at 58.8% precision
and catches 36.7% of scams and 37.2% of the money (step-up: 2.7 a day at 97.0%
precision, 17.2% of scams). It catches none of the unseen scam type. It is a
degraded mode that keeps the service answering, not a substitute for the model.

## 6. Explanations

- **Reasons.** For each alert the exact SHAP contributions of the served model are
  grouped into 14 topics (for example "how fast the receiving wallet moves money
  on"). The top topics that raise the risk and the top ones that lower it are
  returned with the actual values behind them, in English and Bangla:
  "21% of the transfers it received were followed by money leaving within 30
  minutes". A fired rule is listed first, as a policy reason. Allowed transactions
  carry no reasons.
- **Rule trace.** Every decision lists every rule with its status (`fired`,
  `not_fired`, `not_evaluated`, `not_applicable`) and the input values it saw.
- **Similar past cases.** Confirmed victim transfers from the training and
  validation periods (1,181 transfers, 697 cases; never the test period) are
  indexed by their SHAP vector, so "similar" means *risky for the same reasons*.
  For test-period victim transfers of a known scam type, the nearest past case is
  the same type 76.5% of the time. The unseen type has no true match by
  construction; its nearest neighbours are other scam types, and the case note
  says "closest confirmed past case", not "same scam".
- **Recommended actions** come from the policy file, selected by tier, transaction
  type and conditions (for example "check the other wallets that paid this
  receiver" when it received from three or more wallets in a week).
- **Customer message.** Chosen by tier and scenario (scam, account takeover,
  cash-out). The takeover wording is used when the handset has been on the wallet
  for under an hour, so a takeover victim is told to secure the account, not
  asked who told them to pay.

Checked on 600 single decisions from the test period (400 alerts, 200 allowed):
0 differ from the batch result, 0 alerts lack a raising reason, and `decide()`
takes 3.1 ms at the median and 3.4 ms at p95 on a laptop, including scoring and
SHAP (2.4 and 2.7 ms on the previous model, which had fewer trees and four fewer
features).

## 7. Case notes and the language model

The case note has three paragraphs: what happened, why it is risky, what happens
next. By default it is a deterministic template in English or Bangla.

A language model (Claude, through the Anthropic API) may write the note instead.
It is fenced in:

1. **It never decides.** It is called after the decision exists. Thresholds,
   rules and scores are not in any prompt; the prompt contains instructions on
   style only, and the evidence goes in as a JSON document.
2. **It sees only the evidence.** Wallet and agent numbers are masked
   (`W***6128`) before the evidence is built, so no full identifier leaves the
   service.
3. **Its output is checked.** `check_grounding` rejects a note if any number or
   any identifier in it does not appear in the evidence (numbers may be
   reformatted or rounded to at most two decimals; Bangla digits are understood),
   or if it is empty or too long. A rejected note is replaced by the template and
   the reasons for rejection are kept.
4. **It fails closed.** An API error, a refusal or a truncated answer returns
   nothing, and the template is used.
5. **Injected text is treated as data.** The system prompt says so, and since the
   note cannot change the decision and cannot introduce a number or an identifier,
   the damage an injection could do is limited to wording.

All 800 template notes checked (400 alerts, both languages) pass the grounding
check.

## 8. Limits, stated plainly

- **The language-model path has not been run against the live API.** No API key
  was available in the build environment. It is tested with a stand-in client
  (request shape, masking, refusal, truncation, connection error), and every
  result in this document uses the template.
- **The grounding check covers digits and identifiers only.** It does not catch a
  number written in words ("fourteen"), a wrong relation between two correct
  numbers, or an unsupported claim with no number in it. It is a guard against
  invented figures, not a proof of faithfulness.
- **R01 and R02 never fired**, so their effect is shown by unit tests, not by data.
  The same holds for the network rules R06 and R07.
- **A place is a district**, the finest location the data has. A wallet used from
  the other side of its own district looks no different from one used at home.
- **A network is an address prefix** (/24 for IPv4, /48 for IPv6). Mobile carriers
  move customers between prefixes, so on real traffic "never seen" will be more
  common than a change of connection. Grouping by carrier (ASN) would be steadier
  and needs a lookup database this prototype does not ship. The 3-payment minimum
  and the amount test are the only guards, and neither was fitted on real data.
- **Thresholds and fallback points were fitted on synthetic validation data** and
  must be re-fitted on real traffic.
- **Bangla text** was written by the developers and has not been reviewed by a
  professional translator or by upay's customer-communications team.
- **Customer messages are fixed texts** from the policy file. No generated text is
  ever shown to a customer.

## 9. Changing the policy

1. Copy the newest file in `policies/` (now `v3.yaml`) to the next version, set `version:`, edit.
2. `uv run python -m fraudlens.decision.evaluate --policy v4` reports what the
   change does to every tier before anything is served.
3. A file that is malformed, has an unknown field, removes human review from
   holds, or declares a different version than its file name is refused at load.

## 10. Reproduce

```
make policy     # writes policy_report.json and similar_cases.npz next to the model
make mitigation # fits and measures the v3 segments; writes fairness_mitigation.json
make test       # 246 tests (52 need Postgres and skip without it); 19 cover the segments
```

## 11. Young wallets: segment thresholds (policy v3)

v2 interrupts honest payments involving a wallet under 30 days old far more than
others ([MODEL_CARD §13](MODEL_CARD.md)). v3 is v2 plus a `segments:` block; v1 and
v2 are unchanged, v2 stays the default, and `FRAUDLENS_POLICY_VERSION=v3` serves v3.

**What a segment is.** A set of conditions (`r_age_days < 30` on a transfer;
`s_age_days < 30` on any payment) and a `scale` per tier. Its cut-offs are the
model's times the scale, so they follow whichever model version is resolved,
including a shadow model. The first matching segment decides the model's tier;
the rules then run exactly as for any payment, so R01/R02 still hold confirmed
fraud and the mule rules still raise. Each decision records the segment and the
cut-offs used (`segment` in the decision detail), and the 0–100 display score is
anchored to those cut-offs.

**Guards, enforced when the thresholds are resolved.**

- A segment's warn cut-off is capped at the policy's hold cut-off: a payment the
  model would hold for anyone is at least warned in every segment.
- A segment's hold cut-off stays below 1 (it keeps at least 1/scale of the room
  between the policy's hold cut-off and 1), so every segment can still be held for
  a person; the hold tier keeps `human_review: true` as before.
- Cut-offs out of order are refused at load, as for the policy's own.

**The softer tier.** A hold scale above 1 means a payment between the policy's
hold cut-off and the segment's gets step-up (re-authenticate and wait 30 minutes)
instead of money held for an analyst. Step-up rather than a warning, because a
young sender may be a takeover victim and step-up still stops someone who does
not hold the credentials.

**How the scales were chosen** (`fraudlens.decision.mitigation`, on val_a + val_b
only, 110,090 payments). Warn scales 1–250 and hold scales 1–4 per segment, every
combination (6,400). Objective: minimise the larger of the two young-wallet
false-alert ratios, each against honest payments with no young wallet (a rate the
segments cannot move; against the overall rate, helping one group raises the
other's ratio). Subject to: at most 0.5 pp of victim transfers alerted lost, 0.5
pp of victim money alerted, 1 pp of victim transfers held, and the guard. Ratios
within 0.1 count as equal; ties go to the smaller sum of the two, the smaller
false-hold ratio, then the smaller change. 2,420 combinations met the
constraints. Chosen:

| Segment | Warn | Step-up | Hold | Cut-offs on model v4 (warn / step-up / hold) |
| --- | --- | --- | --- | --- |
| S01 receiving wallet under 30 days | ×25 | ×6.03 | ×3 | 0.0496 / 0.0496 / 0.1494 |
| S02 sender under 30 days | ×16 | ×3.86 | ×1 | 0.0318 / 0.0318 / 0.0498 |

For both segments the warn and step-up cut-offs coincide, so below the hold a
young wallet's payment gets step-up or nothing. On validation the choice costs no
victim recall at warn (97.6% before and after; held 96.4% to 95.8%).

**Measured on test** (`fairness_mitigation.json`; the full table with 95%
bootstrap intervals is in MODEL_CARD §13):

| | v2 | v3 |
| --- | --- | --- |
| Young senders, times the overall false-alert rate | 5.90 | 1.14 |
| Young receiving wallets, times the overall rate | 16.17 | 7.03 |
| Young receiving wallets, honest payments held | 1.50% | 0.70% |
| Victim transfers alerted / held | 81.73% / 66.47% | 81.58% / 66.19% |
| Victims' money alerted | 85.19% | 84.76% |
| Alerts a day, precision at warn | 81.0, 64.7% | 71.7, 73.1% |

The cost on test is one victim transfer no longer alerted and two moved from hold
to step-up (৳15,300 of victims' money no longer alerted), against 232 honest
payments no longer interrupted and 18 honest holds softened. The report also
checks that the engine's decisions equal the fit's arithmetic for every test row
and that the policy file carries the chosen scales.

**What it does not do.** The gap for young receiving wallets is narrowed, not
closed: even with no guard (warn ×250) it stays about 6× the no-young-wallet rate,
because the rest comes from rules and from honest payments the model scores at
hold level. Young receiving wallets on USSD remain the worst group (16× the
overall rate in rural areas). Recommendation: serve v3 once a person has accepted
the cost of one victim transfer in 695; watch young-wallet USSD transfers.
