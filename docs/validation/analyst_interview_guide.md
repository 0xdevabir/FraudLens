# Analyst interview guide

**Purpose.** Check FraudLens's assumptions against the people who would run it:
fraud, risk and AML analysts at an MFS provider or bank, and customer-care
staff who take scam calls. The simulator's typologies, report rates and
response times are assumptions (docs/DATA_ASSUMPTIONS.md); this interview is how
we find out which are wrong.

**Format.** 45 minutes, one interviewee, two interviewers (one asks, one
notes). Remote or in person. No recording unless the interviewee agrees in
writing.

**Ground rules (say these at the start).**

- We want your professional view, not your employer's data. Please do not share
  customer details, transaction records, internal thresholds or anything under
  NDA. Ranges and "roughly" are fine.
- We will not name you or your organisation. Notes are kept by the team only
  and summarised across interviews.
- You can skip any question or stop at any time.

Note the interviewee's role, years in the job and type of organisation (MFS,
bank, PSP, regulator, other) only — no names.

---

## 1. Today's process (10 min)

1. Walk us through what happens from the moment a customer reports a scam to
   the case being closed. Who touches it, and how long does each step take?
2. How do you find out about a scam other than a customer report? (Rules,
   agent tips, other providers, police, BFIU.)
3. What rules or models run on transactions today, and do they act before the
   money moves or after?
4. Roughly what share of scam reports end with any money recovered? What stops
   recovery most often?

*Compare with:* BB reported 8.7% recovery of MFS fraud value in 2025; TIB found
58.8% of personal holders who had a problem did not complain
(docs/BANGLADESH_CONTEXT.md).

## 2. Typologies and mules (10 min)

5. Which scam types do you see most? Put these in order: impersonation (company
   or official), prize/lottery/job fee, "sent by mistake", investment scheme,
   account takeover (PIN/OTP stolen). What is missing?
6. How quickly does a receiving wallet empty out after a scam payment? Cash-out
   at an agent, onward transfers, or both?
7. How do mule wallets usually look: new accounts, bought accounts, agents'
   own wallets? How long do they stay active before being blocked?
8. How often does the same mule or the same handset show up across providers?
   Would a shared blacklist help, and what stops it today?

*Compare with:* simulator typology mix, mule dwell time and cash-out delay
(DATA_ASSUMPTIONS.md §5) — all currently unsourced.

## 3. The FraudLens console (15 min)

Show the queue, a case page with its reasons, and the network view (make demo,
docs/DEMO_SCRIPT.md). Then ask:

9. Would the reasons shown on a case let you decide faster? What is missing
   that you would look up elsewhere?
10. How many alerts can one analyst clear in a shift? At what false-alarm rate
    does a queue stop being worked?
11. Which of warn / step-up / hold could your organisation actually deploy
    today? What would legal, compliance or customer care object to?
12. A hold delays a payment by up to 30 minutes. Who would have to approve
    that, and what would make them say yes?

*Compare with:* DECISION_POLICY.md bands and the analyst capacity assumed in
the review simulator.

## 4. Data and deployment (5 min)

13. Which of these signals could you get in real time: device ID, SIM-change
    date, wallet age, receiver's inflow in the last hour, agent location?
14. Where would a system like this have to run (on-premise, in-country cloud),
    and who signs off on a new model?

## Close (5 min)

15. If you could change one thing about how scams are handled, what would it
    be?
16. Is there someone else we should talk to?

Thank them. Within 24 hours, write up notes under the headings above, with
quotes marked as quotes and no identifying details.

## After 5+ interviews

Tally the answers to Q5, Q6, Q7 and Q10 in a table and compare with the
simulator's assumptions. Where most analysts disagree with an assumption,
change it in the calibrated profile (`SimConfig.calibrated`) and record the
source as "analyst interviews, n = X, <month year>" in DATA_ASSUMPTIONS.md §11.
