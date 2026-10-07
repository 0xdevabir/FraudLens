# Demo script

A walk through the working system in about ten minutes, in the order of the
feature list: data and models first, then one payment followed end to end, then
the people, the feedback loop and the business view.

**Before you start:** `make demo` has finished and http://localhost:3100 shows the
sign-in page. Keep two browser windows: one signed in as `analyst1`, one (a
private window) as `supervisor1`. The password is in `backend/.env`.

Everything shown is computed from the running service. The traffic is the
simulator's test period (25 days) replayed through the scoring API and the event
stream, so the alerts, cases and numbers were produced by the same code path a
live payment takes.

## 1. What the system saw and decided (1 min) — analyst1

**Executive summary** (`/`).

- Money at risk, money stopped, customers interrupted, reviewer workload: one
  line each.
- Say: "This is 25 days of synthetic traffic, about 300,000 transactions, replayed
  through the live API. One scam type in it was never shown to the models."

## 2. One payment, end to end (3 min) — analyst1

**Customer phone demo** (`/phone`). This plays upay's app against the real
scoring endpoint.

1. Pick **An ordinary payment** and send. It goes through: most payments are
   never interrupted.
2. Pick **Looks like a scam**. The phone shows a scam warning in Bangla before the
   money moves. Cancel it.
3. Pick **Riskier**. The customer must verify again and wait out a cooling-off
   period. Press "Skip ahead" to move the platform clock and show the wait ending.
4. Pick **Very likely fraud**. The phone says the payment is paused for review.
   Nothing has been blocked: it is waiting for a person.
5. Pick **To a confirmed-fraud wallet**. A hard rule decides this one, not the
   model, and the explanation says which rule.
6. On an interrupted payment, report the scam with one tap: the customer picks
   what happened and types nothing. Also show the **recipient check**: the
   warning a customer gets as soon as they enter the number, before typing an
   amount.

Then follow the held payment into the console:

7. **Alert queue** (`/alerts`): the held payment is at the top, arriving over the
   live feed. Open it.
8. **Payment page**: the reasons, ranked, each with the facts behind it (SHAP
   contributions of the served model, in English or Bangla); the rule trace; the
   case summary; similar past cases; the recommended next action. Point out that
   wallet numbers are masked, and click one to reveal it.

## 3. The reviewer's work (3 min) — analyst1, then supervisor1

9. **Cases** (`/cases`): one case per wallet, with a review deadline. Open the
   case for the held payment. Take it, add a note, and look at the customer's report
   attached to it.
10. **Network** on the wallet page: who paid in, where the money went, which
    handsets are shared. Follow a link to a neighbouring wallet.
11. Press **Request freeze** and give a reason. As the analyst there is no way to
    approve it.
12. Switch to the **supervisor1** window, **Freeze approvals** (`/approvals`):
    approve the request. (Sign in as the person who asked and the API refuses:
    the two-person rule is also a database constraint.)
13. Back on the case, give the verdict **confirmed fraud** with a reason. The held
    payments are blocked and the wallet is flagged; from now on the hard rule
    stops any payment to it.
13a. **Getting a victim's money back.** On the phone, pick *Looks like a scam*
    and press *send anyway*: the money goes. On the done screen press *Scammed?
    Report it to claim your money back* and pick a reason. The phone shows the
    refund tracker (reported → receiver frozen → scam confirmed → money back) and
    reminds the customer that a refund never needs a PIN or a fee. Open the case:
    the **Victim refunds** card shows the claim and how much is still in the
    wallet. Give **confirmed fraud**: a freeze request is filed for you. As
    supervisor1, approve it in **Freeze approvals**; the refund is paid in the
    same step. Back on the phone, *Check my refund* shows the amount returned.
    `/refunds` lists every claim.
14. **Mule rings** (`/rings`): open a ring. Wallets tied by shared handsets and
    transfers; taken-over victims are listed apart from members. **Propose
    freezing the ring** sends one request per member wallet to the same approval
    queue.
15. **Agent risk** (`/agents`): agents ranked against peers of the same size. Open
    the top one: each reason is a plain sentence with how far from normal it is.

## 4. After deployment (2 min) — analyst1 or admin

16. **Model dashboard** (`/model`):
    - *Performance*: precision and recall at each tier, by scam type, including
      the type the model never saw.
    - *Drift*: which inputs have moved since training, and whether the score has.
    - *Versions and shadow mode*: the registry; the challenger trained on analyst
      verdicts scoring next to the served model without deciding anything.
    - *Feedback loop*: how many verdicts have come back as labels and which
      version learnt from them.
17. **Fairness report** (`/fairness`): who pays for false alarms, by region,
    account age, balance and channel. Say the uncomfortable part: wallets under 30
    days old are alerted far more often than average, and why (the same signal
    finds mules).
18. **Decision policy** (`/policy`): the thresholds, the rules, the rules-only
    fallback and the exact words customers see.

## 5. The business view (1 min) — admin or supervisor1

19. **Impact simulator** (`/impact`): move the threshold. Money saved goes up,
    and so do interrupted customers and reviewer hours. "Back to today's
    threshold" returns to the operating point.
20. **Audit log** (`/audit`): every sign-in, wallet view, identifier reveal,
    verdict and freeze step from this demo, with who did it. Filter by
    `pii.reveal` to find the click from step 8. The table cannot be edited: the
    database refuses updates and deletes.

## What to say if asked

- **"Is the model deciding to block people?"** No. A hold waits for an analyst; a
  freeze needs two people; the model never gives a verdict.
- **"What does the language model do?"** It can word the case summary from masked,
  structured evidence. It is off by default, never sees thresholds, never
  decides, and its text is rejected if it contains a number that is not in the
  evidence. See DECISION_POLICY §7.
- **"How do you know the dashboards are honest?"** `make verify` compares what the
  service served with the offline evaluation, row by row. See PLATFORM §11.
- **"What would not survive real traffic?"** The absolute numbers. The data is
  synthetic; thresholds must be re-fitted. Each document ends with its limits.

## Reset

`make demo-reset` deletes the demo's database, dataset and models; the next
`make demo` rebuilds them. Payments and verdicts made during a demo stay until
then, which is harmless: they are marked as demo actions in the audit log.
