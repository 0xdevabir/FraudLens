# Customer survey: scam experience and reactions to FraudLens screens

**Purpose.** Every FraudLens number so far comes from synthetic data. This survey
asks real MFS users two things: what scams they have actually met, and whether
they would accept the three interventions the decision policy uses (warn,
step-up, hold; docs/DECISION_POLICY.md). It takes about 8 minutes.

**Who.** Adult (18+) personal MFS account holders. Aim for at least 100
respondents, with both urban and rural areas and at least 30% women; TIB's 2025
survey (1,784 respondents in 32 districts) is the benchmark to compare against.

**How.** Face to face or by phone, by an enumerator who reads the questions in
Bangla. Record answers on the CSV template at the end; the column names are
the IDs `fraudlens.validation.survey_analysis` reads.

**What we never ask or record.** Name, phone number, wallet number, PIN, OTP,
NID, or the exact amount in the respondent's account. If a respondent starts to
read out a PIN or OTP, stop them.

---

## Consent (read aloud before any question)

**বাংলা**

> আসসালামু আলাইকুম। আমরা FraudLens নামে একটি ছাত্র প্রকল্পের পক্ষ থেকে এসেছি।
> আমরা মোবাইল ব্যাংকিং (যেমন বিকাশ, নগদ, রকেট) প্রতারণা কমানোর একটি পদ্ধতি নিয়ে
> কাজ করছি, এবং জানতে চাই প্রতারণার বিষয়ে আপনার অভিজ্ঞতা কী এবং কিছু সতর্কবার্তা
> আপনার কাছে কেমন লাগে। এতে প্রায় ৮ মিনিট লাগবে।
>
> আমরা আপনার নাম, ফোন নম্বর, অ্যাকাউন্ট নম্বর, পিন বা ওটিপি জানতে চাইব না — দয়া
> করে এগুলো বলবেন না। আপনার উত্তর বেনামে রাখা হবে এবং শুধু গবেষণার জন্য সামগ্রিক
> হিসাবে ব্যবহার করা হবে। আমরা কোনো এমএফএস কোম্পানি বা বাংলাদেশ ব্যাংকের প্রতিনিধি
> নই, এবং এই জরিপ কোনো হারানো টাকা ফেরত আনতে পারবে না।
>
> অংশ নেওয়া সম্পূর্ণ আপনার ইচ্ছা। যে কোনো প্রশ্ন বাদ দিতে পারেন, যে কোনো সময় থামতে
> পারেন। আপনি কি অংশ নিতে রাজি আছেন?

**English**

> Hello. We are from FraudLens, a student project working on a way to reduce
> mobile money (bKash, Nagad, Rocket and others) fraud. We would like to hear
> about your experience of scams and how some warning screens feel to you. It
> takes about 8 minutes.
>
> We will not ask for your name, phone number, account number, PIN or OTP —
> please do not tell us these. Your answers are anonymous and are used only in
> aggregate for research. We do not represent any MFS provider or Bangladesh
> Bank, and this survey cannot recover lost money.
>
> Taking part is entirely voluntary. You can skip any question or stop at any
> time. Do you agree to take part?

Record `consent` = `yes` or `no`. If `no`, thank them and stop; the analysis
drops the row anyway.

If the respondent has lost money: tell them they can complain to their
provider's helpline (providers must resolve disputes within 10 working days,
MFS Regulations 2022 §17.3) and to Bangladesh Bank's Customer Interests
Protection Centre (CIPC).

---

## Questions

Profile (enumerator fills in): `respondent_id` (e.g. `DHK-014`; never a phone
number), `area` = `urban` / `rural`.

### Part A — scam experience (last 12 months)

| ID | Question (English; read in Bangla) | Answers |
| --- | --- | --- |
| `q01_contacted` | Has anyone called, texted or messaged you trying to get money or a PIN/OTP from your mobile wallet? | `yes` / `no` |
| `q02_lost_money` | Did you lose money to a scam of this kind? | `yes` / `no` |
| `q03_scam_type` | *If yes:* Which is closest to what happened? (read options) | `impersonation` (posed as company, official, relative) / `lottery_fee` (prize, job or loan with a fee) / `wrong_send` ("sent by mistake, please return") / `investment` (high-return scheme) / `account_takeover` (someone got into your account) / `other` |
| `q04_loss_bdt` | *If yes:* Roughly how much did you lose, in taka? | number |
| `q05_reported_to` | *If yes:* Who did you tell? | `nobody` / `provider` / `police` / `cipc` / `several` |
| `q06_recovered` | *If yes:* Did you get any of it back? | `none` / `partial` / `full` |

### Part B — reactions to the three screens

Show each mock-up (screenshots from the FraudLens console, docs/screenshots) or
read the script below. Answers 1–5: 1 = strongly disagree, 5 = strongly agree.

- **Warn.** *"Before you send Tk 8,000: this number was opened 2 days ago and
  has received money from many people today. People are often told to send
  money to 'return a mistake' or 'unlock a prize'. Do you know this person?"*
  [Send anyway] [Cancel]
- **Step-up.** *"This payment looks like a common scam, so it is paused for 30
  minutes. Enter your PIN again to confirm it is really you; after the pause it
  goes through unless you cancel."*
- **Hold.** *"We are holding this Tk 15,000 transfer for up to 30 minutes
  so a person can check it. You can cancel now, or call your provider's
  helpline. The money is still in your wallet."*

| ID | Statement | Answers |
| --- | --- | --- |
| `q07_warn_would_stop` | The warning would make me stop and check before sending. | 1–5 |
| `q08_step_up_ok` | Confirming with my PIN again and waiting 30 minutes would be acceptable. | 1–5 |
| `q09_hold_ok` | Having a payment held for a while would be acceptable if it protects me. | 1–5 |
| `q10_max_hold_minutes` | What is the longest you would accept a payment being held? (minutes; 0 = never) | number |
| `q11_false_alarms_per_year` | How many times a year could a *normal* payment of yours be warned or held before you would stop trusting it? | number |
| `q12_trust` | If your wallet showed screens like these, would you trust it more, the same, or less? | `more` / `same` / `less` |
| `q13_language` | Which language should these screens be in? | `bangla` / `english` / `both` |
| `q14_comments` | Anything else you want to tell us? (free text; not analysed by the script) | text |

---

## CSV template

```
respondent_id,consent,area,q01_contacted,q02_lost_money,q03_scam_type,q04_loss_bdt,q05_reported_to,q06_recovered,q07_warn_would_stop,q08_step_up_ok,q09_hold_ok,q10_max_hold_minutes,q11_false_alarms_per_year,q12_trust,q13_language,q14_comments
```

Leave a cell empty when a question was skipped or does not apply. Then:

```
cd backend
uv run python -m fraudlens.validation.survey_analysis responses.csv --by area
```

The output is JSON: shares for each categorical answer, loss median/mean/p90
for victims only, and for each screen the mean score and the share who agree
(4–5) or disagree (1–2).

## What the answers would change

| Finding | Change in FraudLens |
| --- | --- |
| `q07` agree < 50% | Rewrite the warn text; test the scam-script wording against the message-check copy. |
| `q09` agree < 50% or `q10` median < 30 | Shorten the hold window in DECISION_POLICY.md or reserve hold for the top risk band only. |
| `q11` median low (≤ 2) | Tighten the warn threshold; the policy's false-positive budget is set too loose. |
| `q13` mostly `bangla` | Bangla-first copy for every customer-facing screen. |
| `q03` mix differs from the simulator's typology mix | Re-weight typologies in the calibrated simulator profile (DATA_ASSUMPTIONS.md §11). |
