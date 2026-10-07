# 08 · Customer experience and customer appeals

## Goal

Make the sender's side of FraudLens the best part of the demo, and give the
customer a way to say "this is a genuine payment" that a person answers.

1. The phone mock (`frontend/app/(console)/phone/page.tsx`) shows every
   intervention tier (allow, warn, step-up, hold) in Bangla or English, with
   natural Bangla copy, a warning that names the scam pattern the decision
   points to, a visible cooling-off countdown, a "what happens next" timeline,
   and proper accessibility at phone width.
2. A customer can appeal a hold or a warning from the phone. An analyst or
   supervisor answers it from an appeals queue in the console: approving
   releases a held payment, rejecting keeps it held for the case. Every step is
   audited, every appeal has a deadline, and an approved appeal becomes a
   training label through the existing feedback path.

## Judge criteria it moves

- **Business / customer impact (13.67/20, "low customer interruption").** The
  cost of APP-fraud controls is borne by honest customers who get interrupted.
  This work makes each interruption shorter and clearer (the customer is told
  *which* scam it looks like, how long the wait is, and what happens next) and
  gives a wrongly-stopped customer recourse with a stated deadline, instead of
  waiting on a helpline. Approved appeals feed back as "legitimate" labels, so
  the false positives that interrupt customers are what the next model learns from.
- **Prototype quality (11.67/15).** An end-to-end customer journey in both
  languages, a new reviewed workflow that reuses the case/approval/audit
  patterns, a migration, tests and a browser test.
- **Responsible AI.** Contestability: an automated hold can be challenged and is
  decided by a named person within an SLA; the decision and the reasoning are in
  the audit log.

## Design

### Customer copy keyed by scam pattern

`POST /v1/demo/pay` and `/v1/score` already return a decision summary. It gains
two additive fields: `scenario` (scam / takeover / unusual_access / cash_out)
and `cue`, one customer-safe pattern name derived from what the decision
already recorded (the rule that fired, the scenario, and the typology of the
most similar past cases): `reported_recipient`, `impersonation`, `prize`,
`investment`, `wrong_send`, `not_you`, or `generic`. No score, no reason
detail and no identifier is added. The phone turns the cue into one plain
question per language ("Did someone claiming to be from upay, bKash or your
bank ask you to send this?"). The policy's own fixed `customer_message` is
still shown unchanged.

### Appeals

- Table `appeals` (migration `0004_customer_appeals`, additive): one per
  transaction; wallet, case, tier at the time, relation to the recipient, the
  customer's reason, status (`pending` / `approved` / `rejected`), `filed_at`,
  `sla_due_at`, decider, note, decided_at.
- Who can appeal: the sender of a payment whose decision was warn, step-up or
  hold. A hold can be appealed only while it is still held.
- SLA: a hold's appeal is due within the hold review time from the policy
  (30 minutes); a warning or step-up appeal (feedback only, the money is the
  customer's to send) within 24 hours.
- Decide: analyst or supervisor. An escalated case needs a supervisor (same
  rule as verdicts). Approve releases a still-held payment through the same
  `Scorer.complete` the verdict uses; it refuses to release money into a wallet
  that is confirmed fraud or frozen. Reject keeps the hold; the case still
  decides. Approving a warning appeal changes no money: it records that the
  warning was wrong. Neither shortens a step-up's cooling-off (that would be
  exactly what a scammer coaching a victim would ask for).
- Audit: `appeal.file`, `appeal.approve`, `appeal.reject`; case timeline events
  `appeal_filed` / `appeal_approved` / `appeal_rejected`.
- Feedback: an approved appeal labels its decision `legitimate` (y = 0) in
  `mlops.feedback.labels`, unless the case it belongs to has its own verdict,
  which wins. A rejected appeal is not a label: "keep it held" is not "fraud".
- Endpoints: `POST /v1/customer/transactions/{txn_id}/appeal` and
  `GET .../appeal` (service); `POST /v1/demo/appeal`, `GET /v1/demo/appeal`
  (staff playing the phone); `GET /v1/appeals`, `POST /v1/appeals/{id}/approve|reject`
  (analyst, supervisor).

### Console

New page `/appeals` (nav: Investigations), modelled on Freeze approvals: status
chips, a table with SLA countdown/overdue, the customer's relation and reason
(masked), the tier, the case link, and Approve / Reject through the existing
`ReasonDialog`.

### Phone

- Language toggle (Bangla default, English), stored per viewer; every phone
  string from one copy table; `lang` attributes set.
- Warn: pattern question, policy message, "Cancel" as the primary action,
  "Send anyway" (dismissible: the policy lets the customer decide), "This is a
  genuine payment" (appeal), report.
- Step-up: live mm:ss cooling-off countdown (`role=timer`), progress bar, PIN,
  "Verify and send" enabled once the wait is over; cancel; appeal.
- Hold: "Your money has not left your wallet", review deadline countdown,
  timeline (paused → reviewer → decision), appeal and report.
- Appeal screen: relation (radio group), reason (textarea with counter),
  submit; then appeal status with its deadline and a refresh.
- Accessibility: focus moves to each new screen's heading, `aria-live` status,
  labelled controls, 44px touch targets, contrast checked, phone frame fits a
  360px viewport without horizontal scroll.

## Steps

1. Plan (this file).
2. Backend: cue in the decision summary; `Appeal` model + migration 0004;
   `platform/appeals.py` workflow; routes (customer, demo, reviewer queue);
   feedback labels; tests in `test_platform.py`.
3. Console: types, `/appeals` page, nav entry.
4. Phone rewrite: language toggle, per-tier screens, countdowns, timeline,
   appeal flow, accessibility.
5. Playwright: `frontend/scripts/phone-e2e.cjs` (`make e2e-phone`): pay a warn
   and a hold in both languages, file an appeal, approve it in `/appeals`, see
   the payment released on the phone. Add `/appeals` to the smoke pages.
6. Docs: PLATFORM.md customer section and endpoint table.
7. `uv run pytest -q`, `make lint`, e2e against a private stack
   (`docker compose -p fl-cx`, ports 55434/56381, API 8028, console 3208).

## Definition of done

- Every phone screen reads correctly in both languages; no mixed-language
  strings except brand names.
- Warn and step-up stay dismissible exactly as the policy says; hold is not.
- Appeal round trip works: file from the phone → appears in `/appeals` with an
  SLA → approve releases the held payment → phone shows it sent; reject keeps
  it held. All audited.
- Approved appeals appear in `/v1/feedback` and `feedback.labels()`.
- Migration matches the models (`test_the_schema_matches_the_models`).
- `pytest`, `make lint` pass; the e2e passes locally if the stack runs.
