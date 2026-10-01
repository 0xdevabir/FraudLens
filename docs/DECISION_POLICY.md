# Decision policy — FraudLens policy v1 on model v2

The models rank transactions by risk ([MODEL_CARD.md](MODEL_CARD.md)). This
document covers the layer that turns a score into an action: what happens at each
level of risk, which business rules apply whatever the score is, what the system
does when the model is unavailable, and how each alert explains itself.

Every number below is read from `backend/artifacts/models/v2/policy_report.json`,
which `make policy` writes. The data is synthetic
([DATA_ASSUMPTIONS.md](DATA_ASSUMPTIONS.md)).

Code: `backend/src/fraudlens/decision/`. Policy file:
`backend/src/fraudlens/decision/policies/v1.yaml`.

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

Conclusion, stated as it is: **on this data the model already contains everything
the hand-written rules know.** The rules are in the policy as guarantees and as
defence in depth, not because they improve the numbers.

## 4. Results on the test period (25 days, 134,545 scored transactions)

Tier counts:

| | allow | warn | step-up | hold |
| --- | --- | --- | --- | --- |
| Model only | 132,474 | 777 | 273 | 1,021 |
| Model + policy v1 | 132,469 | 782 | 273 | 1,021 |
| Rules-only fallback | 134,232 | 246 | 67 | 0 |

What each rule did:

| Rule | Fired | Precision when fired | Changed the tier | Of those, fraud |
| --- | --- | --- | --- | --- |
| R01 | 0 | — | 0 | — |
| R02 | 0 | — | 0 | — |
| R03 | 21 | 100% | 0 | — |
| R04 | 471 | 90.5% | 5 | 1 (one extra victim transfer warned) |

Outcomes, everything at or above each tier:

| | Alerts/day | Precision | Scams caught | Taka stopped | Taka incl. exit holds |
| --- | --- | --- | --- | --- | --- |
| Policy, warn | 83.0 | 62.4% | 88.1% | 84.9% | 96.7% |
| Policy, step-up | 51.8 | 83.6% | 80.3% | 77.5% | 88.8% |
| Policy, hold | 40.8 | 93.1% | 74.2% | 73.4% | 80.8% |
| Model only, warn | 82.8 | 62.5% | 87.8% | 84.9% | 96.7% |

The policy differs from the model in five transactions out of 134,545: four false
warnings and one true one. For the scam type that was never in training
(`investment_scam`), scams caught at warn go from 72.4% to 73.0%.

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
  the same type 77.9% of the time. The unseen type has no true match by
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
takes 2.4 ms at the median and 2.7 ms at p95 on a laptop, including scoring and
SHAP.

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
- **Thresholds and fallback points were fitted on synthetic validation data** and
  must be re-fitted on real traffic.
- **Bangla text** was written by the developers and has not been reviewed by a
  professional translator or by upay's customer-communications team.
- **Customer messages are fixed texts** from the policy file. No generated text is
  ever shown to a customer.

## 9. Changing the policy

1. Copy `policies/v1.yaml` to `v2.yaml`, set `version: v2`, edit.
2. `uv run python -m fraudlens.decision.evaluate --policy v2` reports what the
   change does to every tier before anything is served.
3. A file that is malformed, has an unknown field, removes human review from
   holds, or declares a different version than its file name is refused at load.

## 10. Reproduce

```
make policy     # writes policy_report.json and similar_cases.npz next to the model
make test       # 147 tests; 68 cover this layer
```
