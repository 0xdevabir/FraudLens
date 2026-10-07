# Business case — what the policy is worth in taka

The model card and the decision policy report recall, precision and alerts a day.
This document turns those into a monthly profit and loss for a provider: money
kept from scammers, what the friction and the review team cost, and what is left.
It answers the three questions a CFO and a CRO ask first: is it worth running,
how many people does it need, and which of our own numbers would change the answer?

**These are estimates on synthetic data.** The model rates come from the
back-test (model v4, policy v1 thresholds, 134,545 scored payments over 25 test
days the model never trained on). Everything that turns a rate into taka is an
assumption, listed in §2 with its source or reasoning. The simulator's fraud is
far denser than real traffic, so the scam money is rescaled to an assumed real
rate rather than taken from the simulation. A provider would replace the
assumptions with its own figures before relying on any number here.

Code: `backend/src/fraudlens/decision/business.py`. API:
`GET /v1/model/business` (defaults) and `POST /v1/model/business` (any assumption
overridden). Console: the impact simulator, under "Business case".

```
cd backend && uv run python -m fraudlens.decision.business            # the table below
uv run python -m fraudlens.decision.business --set monthly_payments=300000000
```

## 1. How a threshold becomes taka

The impact simulator's slider moves the warn threshold. Step-up and hold keep the
thresholds the policy gives them, or follow the slider once it passes them. For
each of the 40 thresholds in the sweep, and exactly at the policy in force:

| Line | How it is computed |
| --- | --- |
| Scam money at risk | volume × average payment × scam rate |
| Kept from scammers | scam money alerted in each tier's band × the share that tier actually stops (warn, step-up, hold), plus mule cash-outs held after the victim's payment × the hold share. A warning shown to a mule stops nothing, so only holds count there |
| Friction | honest payments interrupted × abandonment rate × cost of an abandoned payment, plus every interruption × support contacts per tier × cost of a contact |
| Review | every held payment × analyst minutes; analysts = the larger of that workload and the round-the-clock floor, rounded up to whole people |
| Operating cost | friction + analysts + running the platform |
| Net benefit | kept × (1 + reputational cost per taka) − operating cost. Customers and provider together: every taka a customer keeps counts |
| Net benefit to the provider | kept × (reimbursement share + reputational cost per taka) − operating cost. Only the provider's own money |

Rates per payment come from the back-test and are multiplied by the provider's
volume. The simulator's scams take ৳26.38 per scored payment (about 93 basis
points of payment value). At the assumed real rate of 2 basis points that is
scaled by 0.0212: scam money and true alerts shrink, false alerts do not, because
honest customers look the same whatever the scam rate.

## 2. Assumptions

| Assumption | Value | Range tried | Source or reasoning |
| --- | --- | --- | --- |
| Send-money and cash-out payments a month | 20 million | 10–40 million | Order of magnitude for a mid-sized provider (upay scale). Bangladesh Bank's monthly MFS statistics put the industry in the hundreds of millions of transactions a month; bKash scale is roughly 300 million scored payments. Replace with the provider's MIS figure |
| Average payment | ৳2,800 | ৳1,500–4,000 | The simulator's scored payments average ৳2,830 (send-money ৳2,247, cash-out ৳3,990) |
| Scam losses, share of payment value | 2 bp | 0.5–8 bp | Assumed. UK push-payment scam losses are about 1–2 bp of Faster Payments value; MFS in Bangladesh is assumed more exposed. No public Bangladeshi figure exists |
| Scam money stopped by a warning | 25% | 10–50% | Assumed: a coached victim often clicks through |
| … by a step-up check | 50% | 30–75% | Assumed: re-authentication stops most takeovers; the 30-minute cooling-off breaks a scam's urgency |
| … by a hold | 90% | 75–98% | Assumed: the money stays while an analyst calls; some victims insist |
| Honest payments abandoned after an interruption | 5% | 1–15% | Assumed |
| Cost of one abandoned payment | ৳25 | ৳10–60 | Lost fee and goodwill: cash-out pays about 1.85%, app send-money ৳0–5; many are retried |
| Support contacts per warning / step-up / hold | 0.02 / 0.10 / 0.25 | 0–0.05 / 0.05–0.25 / 0.10–0.50 | Assumed |
| Cost of one support contact | ৳50 | ৳25–100 | About six minutes of a contact-centre agent at ৳35,000 a month loaded, plus telephony |
| Analyst minutes per held payment | 8 | 4–20 | The impact page's default |
| Analyst cost a month, loaded | ৳60,000 | ৳40,000–100,000 | About ৳45,000 salary in Dhaka plus a third for benefits, seat, supervision |
| Hours of case work per analyst a month | 132 | 110–150 | Six hours of case work a shift, 22 shifts |
| Minimum analysts | 5 | 3–10 | Holds have a 30-minute target at any hour: one seat staffed 24x7 |
| Running the platform a month | ৳5 lakh | ৳2.5–15 lakh | Assumed: API and stream nodes, Postgres, Redis, part of an engineer |
| Share of scam losses the provider reimburses | 25% | 0–100% | Assumed. No mandatory rule in Bangladesh; the UK has required 100% since October 2024 |
| Reputational cost per taka a customer loses | ৳0.25 | ৳0–1 | Assumed, and the hardest to defend; shown separately in §4 |

## 3. Results at upay scale (20 million payments a month)

At the policy in force (warn at 1.49% of payments, measured exactly, not from the
nearest sweep point):

| | A month |
| --- | --- |
| Scam money at risk | ৳1.12 crore |
| Kept from scammers | **৳88.6 lakh (79.1%)** — hold ৳73.4 lakh, mule cash-outs held ৳10.3 lakh, step-up ৳2.9 lakh, warn ৳2.0 lakh |
| Still lost | ৳23.4 lakh |
| Friction | ৳4.6 lakh — 5,164 abandoned honest payments (৳1.3 lakh), 6,664 support contacts (৳3.3 lakh) |
| Holds to review | 11,544 → 1,539 hours → 11.7 FTE of work → **12 analysts** (৳7.2 lakh) |
| Platform | ৳5.0 lakh |
| Operating cost | ৳16.8 lakh |
| **Net benefit** | **৳94.0 lakh a month (about ৳11.3 crore a year)** |
| Net benefit to the provider alone | ৳27.5 lakh a month (about ৳3.3 crore a year) |
| Kept per ৳1 of operating cost | **৳5.27** |
| Honest customers interrupted | **51.6 per 10,000 payments** (103,277 a month) |
| Break-even scam rate | 0.27 bp (0.69 bp for the provider alone) |

Across the sweep (selected points):

| Payments interrupted | Kept from scammers | Operating cost | Net benefit | Provider net | Analysts | Honest per 10,000 | Kept per ৳1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.05% | ৳9.3 lakh | ৳8.0 lakh | ৳3.5 lakh | −৳3.4 lakh | 5 | 0.0 | 1.15 |
| 0.53% | ৳58.1 lakh | ৳8.3 lakh | ৳64.3 lakh | ৳20.7 lakh | 5 | 0.1 | 7.00 |
| 0.96% | ৳86.1 lakh | ৳14.9 lakh | ৳92.8 lakh | ৳28.2 lakh | 12 | 13.2 | 5.79 |
| **1.21% (best)** | ৳88.0 lakh | ৳15.8 lakh | **৳94.2 lakh** | ৳28.2 lakh | 12 | 30.0 | 5.56 |
| **1.49% (policy)** | ৳88.6 lakh | ৳16.8 lakh | **৳94.0 lakh** | ৳27.5 lakh | 12 | 51.6 | 5.27 |
| 2.46% | ৳90.3 lakh | ৳20.8 lakh | ৳92.0 lakh | ৳24.3 lakh | 12 | 140.5 | 4.33 |
| 5.00% | ৳91.4 lakh | ৳31.9 lakh | ৳82.3 lakh | ৳13.7 lakh | 12 | 387.6 | 2.86 |

What this says:

- **The thresholds in force sit at the top of the curve.** The best point in the
  sweep nets ৳22,600 a month more (0.2%) by interrupting 1.21% of payments instead
  of 1.49%. Loosening further buys little: going from 1.49% to 5% keeps another
  ৳2.8 lakh and costs ৳15 lakh more in friction.
- **Holds do the work.** 94% of the money kept comes from the hold tier and the
  mule cash-outs it catches; warnings add 2%. That is why the assumed hold
  effectiveness matters more than the warning's.
- **At real scam rates most interruptions are honest customers.** In the
  simulator 65% of warn-level alerts are fraud; rescaled to 2 bp, 96% of the
  107,406 interruptions a month are honest payments, and about 8,500 of the
  11,544 holds are. The friction cost and the 12 analysts are sized for that.
  The cost per honest customer is low (most see one warning), but a CRO should
  see the count: about 1 in 200 payments.

### At bKash scale (300 million payments a month)

Same assumptions, volume ×15: ৳16.8 crore at risk, **৳13.3 crore kept**, operating
cost ৳1.79 crore (175 analysts, ৳1.05 crore; friction ৳69 lakh), **net benefit
৳14.8 crore a month**, ৳4.85 crore to the provider alone, ৳7.41 kept per ৳1.
The 175 analysts are a real constraint: 173,000 holds a month need a review
operation of that size, or a stricter hold threshold.

## 4. Sensitivity

Net benefit of the policy in force at upay scale (৳94.0 lakh a month; ৳27.5 lakh
to the provider), with one assumption at a time moved to the ends of its range.
Largest swing first.

| Assumption | Low → net benefit (provider) | High → net benefit (provider) | Swing |
| --- | --- | --- | --- |
| Scam losses, share of payment value | 0.5 bp → ৳12.4 lakh (−৳4.2 lakh) | 8 bp → ৳4.20 crore (৳1.54 crore) | ৳4.08 crore |
| Payments a month | 10 M → ৳44.5 lakh (৳11.2 lakh) | 40 M → ৳1.93 crore (৳60.0 lakh) | ৳1.48 crore |
| Average payment | ৳1,500 → ৳43.3 lakh (৳7.7 lakh) | ৳4,000 → ৳1.41 crore (৳45.7 lakh) | ৳97.3 lakh |
| Reputational cost per taka lost | 0 → ৳71.8 lakh (৳5.3 lakh) | 1 → ৳1.60 crore (৳94.0 lakh) | ৳88.6 lakh |
| Scam money stopped by a hold | 75% → ৳76.5 lakh (৳20.5 lakh) | 98% → ৳1.03 crore (৳31.2 lakh) | ৳26.7 lakh |
| Analyst minutes per hold | 4 → ৳97.6 lakh (৳31.1 lakh) | 20 → ৳83.2 lakh (৳16.7 lakh) | ৳14.4 lakh |
| Running the platform | ৳2.5 lakh → ৳96.5 lakh | ৳15 lakh → ৳84.0 lakh | ৳12.5 lakh |
| Analyst cost a month | ৳40,000 → ৳96.4 lakh | ৳100,000 → ৳89.2 lakh | ৳7.2 lakh |
| Cost of one support contact | ৳25 → ৳95.6 lakh | ৳100 → ৳90.6 lakh | ৳5.0 lakh |
| Scam money stopped by a warning | 10% → ৳92.5 lakh | 50% → ৳96.4 lakh | ৳3.9 lakh |
| Honest payments abandoned | 1% → ৳95.0 lakh | 15% → ৳91.4 lakh | ৳3.6 lakh |
| Scam money stopped by a step-up | 30% → ৳92.5 lakh | 75% → ৳95.8 lakh | ৳3.3 lakh |
| Cost of an abandoned payment | ৳10 → ৳94.7 lakh | ৳60 → ৳92.1 lakh | ৳2.6 lakh |
| Support contacts per step-up | 0.05 → ৳94.5 lakh | 0.25 → ৳92.2 lakh | ৳2.3 lakh |
| Support contacts per hold | 0.10 → ৳94.8 lakh | 0.50 → ৳92.5 lakh | ৳2.3 lakh |
| Support contacts per warning | 0 → ৳94.7 lakh | 0.05 → ৳92.9 lakh | ৳1.8 lakh |
| Hours of case work per analyst | 110 → ৳92.8 lakh | 150 → ৳94.6 lakh | ৳1.8 lakh |
| Share reimbursed by the provider | 0% → ৳94.0 lakh (৳5.3 lakh) | 100% → ৳94.0 lakh (৳94.0 lakh) | 0 (provider ৳88.6 lakh) |
| Minimum analysts | 3 → ৳94.0 lakh | 10 → ৳94.0 lakh | 0 (workload needs 12 anyway) |

Reading it:

- **The answer depends on the scam rate and the volume, not on the cost lines.**
  Every friction and staffing assumption together moves the net benefit by less
  than the plausible range of the scam rate alone. The case stays positive for
  customers and provider together down to 0.27 bp of scam losses, and for the
  provider's own money down to 0.69 bp.
- **For the provider's own P&L, reimbursement and reputation decide it.** With no
  reimbursement and no reputational cost the provider still nets ৳5.3 lakh at the
  default scam rate; at 0.5 bp it would lose ৳4.2 lakh a month while its
  customers kept ৳22.2 lakh (`--set scam_loss_bps=0.5`). Whether that is worth
  paying for is a policy choice, and the table makes it visible.
- **The first number to replace is the scam rate.** It is the largest swing and
  the least known; a provider's complaint and reimbursement records give it
  directly.

## 5. What this does not show

- **The model rates are synthetic.** Recall and false-alert rate come from a
  simulator ([DATA_ASSUMPTIONS.md](DATA_ASSUMPTIONS.md)). Real scams the model has
  not seen will be caught less often; the held-out scam type is caught at 70.4%
  against 86.9% overall (MODEL_CARD).
- **Rescaling assumes the scam mix and the alert ranking stay as they are** when
  scams are rarer. Real precision would come from the provider's own back-test.
- **Tier effectiveness is assumed.** Nothing here measures how many warned victims
  stop; a live A/B of warning texts would.
- **Warnings and step-ups cost no analyst time**, as the policy specifies; only
  holds go to a person. A provider that reviewed step-ups would need more people.
- **The platform cost is flat.** At bKash scale it would be higher, though still
  small next to the review team.
- **No churn model.** Abandonment is priced per payment; customers who leave
  after a false hold are not counted beyond the reputational line.
