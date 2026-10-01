# Platform — API, workflow, stream and storage

The models rank risk ([MODEL_CARD.md](MODEL_CARD.md)) and the policy turns a score
into a tier ([DECISION_POLICY.md](DECISION_POLICY.md)). This document covers the
service around them: how a transaction gets in, what is stored, who may do what,
how a held payment reaches a person, and what happens on a restart.

Code: `backend/src/fraudlens/platform/` (scoring, cases, stream, storage) and
`backend/src/fraudlens/api/` (HTTP). Everything here runs on synthetic data
([DATA_ASSUMPTIONS.md](DATA_ASSUMPTIONS.md)).

## 1. Shape

```
upay core ──POST /v1/score──────────────┐            ┌── PostgreSQL: transactions, decisions,
           (answer needed now)          ▼            │   cases, freeze requests, audit log
upay core ──POST /v1/events──▶ Redis stream ──▶ Scorer ──┤
           (bulk, asynchronous)   worker thread      └── Redis pub/sub ──▶ GET /v1/stream/alerts
                                                                            (analyst console)
analysts, supervisors ──▶ cases, verdicts, freeze approvals ──▶ Scorer (release / block / flag)
```

One `Scorer` per process owns the feature state (the same `FeatureEngine` class
that built the training features) behind a lock. HTTP scoring and the stream
worker both go through it, so there is one order of events and one answer per
transaction.

## 2. What happens to a transaction

`POST /v1/score` takes one event and returns the decision. The event says where
it comes from:

| Tier | `source: live` (upay asks before moving money) | `source: replay` (a recorded event) |
| --- | --- | --- |
| allow | `completed`, applied to the feature state | same |
| warn, step-up | `pending_customer`: not applied until the customer proceeds | `completed`, applied |
| hold | `held`: not applied until an analyst releases it | `held`, already applied |

"Applied" means the transaction has entered the state the models read. A live
payment that is waiting has not happened yet, so it does not count as the
sender's history, and a cancelled or blocked one never does. Recorded events are
applied on arrival because the models were trained on features computed that way.

Other outcomes:

- **Idempotent.** A `txn_id` seen before returns the stored decision with
  `duplicate: true` and changes nothing. A retry after a timeout is safe.
- **Frozen party.** If the sender or receiver is frozen the payment is `rejected`
  with reason `wallet_frozen` and is not scored. Two people decided that
  (§5); the model is not consulted.
- **Stale.** An event more than 300 seconds behind the newest one is refused
  (`409 stale_event`): features are only correct in time order.
- **Unknown wallet.** A wallet seen for the first time is registered (segment
  `unknown`) and scored with what is known, which is nothing: young-wallet
  signals apply.
- **No model.** If the model bundle cannot be loaded the service still answers,
  in `rules_only` mode (DECISION_POLICY §5), and `/ready` reports the mode.

`POST /v1/score/what-if` runs the same decision without storing or applying
anything. The customer app uses it before the customer confirms; reviewers get
the full explanation, the service account only the summary.

## 3. Endpoints

All under `/v1`, JSON, `Authorization: Bearer <token>`. The OpenAPI document is
served at `/docs` outside production.

| Area | Endpoint | Role |
| --- | --- | --- |
| Auth | `POST /auth/login`, `GET /auth/me` | anyone / signed in |
| Scoring | `POST /score`, `POST /events` (up to 500 per call, queued), `POST /wallet-flags` | service |
| | `POST /score/what-if` | service, analyst, supervisor |
| Alerts | `GET /alerts`, `GET /decisions/{txn_id}`, `GET /decisions/{txn_id}/narrative?lang=en\|bn`, `GET /stream/alerts` (server-sent events) | analyst, supervisor |
| Cases | `GET /cases`, `POST /cases`, `GET /cases/{id}`, `POST /cases/{id}/assign\|notes\|escalate\|verdict`, `GET /users/reviewers` | analyst, supervisor |
| Freezes | `POST /wallets/{id}/freeze-requests`, `GET /freeze-requests` | analyst, supervisor |
| | `POST /freeze-requests/{id}/approve\|reject`, `POST /wallets/{id}/unfreeze` | supervisor |
| Network | `GET /wallets/{id}`, `GET /wallets/{id}/network`, `GET /rings`, `GET /rings/{ring_id}`, `GET /agents/risk`, `GET /agents/{id}`, `GET /past-cases/{id}` | analyst, supervisor |
| Customer | `POST /customer/recipient-check`, `POST /customer/transactions/{txn_id}/respond`, `POST /customer/reports` | service |
| Operations | `GET /metrics/summary`, `GET /model` | analyst, supervisor, admin |
| | `GET /audit` | supervisor, admin |
| | `GET /health`, `GET /ready` | none |

Errors always have the same shape, with the request id that is also in the
`X-Request-ID` header and the logs:

```json
{"error": {"code": "two_person_rule", "message": "...", "request_id": "..."}}
```

Validation errors (`422 invalid_request`) list the field and the problem and never
repeat the value that was sent.

### Integrating with a payment backend

1. Before the customer confirms: `POST /v1/customer/recipient-check` (cheap, by
   receiver) or `POST /v1/score/what-if` (the full decision).
2. At confirmation: `POST /v1/score` with `source: live`. Act on `decision.action`
   and show `decision.customer_message` (fixed texts, English and Bangla).
3. When the customer answers a warning or completes step-up:
   `POST /v1/customer/transactions/{txn_id}/respond`.
4. Everything that needs no answer (cash-ins, bill payments, back-office flags):
   `POST /v1/events`. They keep the feature state current.
5. When your own investigation confirms a wallet as fraud: `POST /v1/wallet-flags`.

## 4. Cases

A hold opens a case on the wallet that would receive the money (the sender, for a
cash-out), or joins the open one: there is at most one open case per wallet, so
ten victims paying the same mule are one piece of work, with one deadline
(`sla_due_at`, 30 minutes from the policy). Customer scam reports open or join a
case the same way.

- `assign`: an analyst takes a case, or a supervisor assigns it.
- `notes`: free text, kept in the timeline.
- `escalate`: after that only a supervisor can close it.
- `verdict`: `legitimate` releases every held payment in the case (they are
  applied to the feature state at that moment); `confirmed_fraud` blocks them and
  flags the wallet, which from then on triggers the hard rules R01 and R02. A
  verdict needs a written reason; an analyst cannot give one on a case assigned
  to someone else or on an escalated case, a supervisor can. It is what the feedback loop will train on.

The system never gives a verdict. A held payment stays held until a person
decides.

## 5. Freezing a wallet takes two people

Anyone reviewing can request a freeze, with a reason. A **different** supervisor
approves or rejects it. The rule is checked in the API (`403 two_person_rule`)
and again by a `CHECK` constraint in the database, so a bug or a direct write
cannot produce a freeze that one person both asked for and approved. Unfreezing
is a supervisor action with a reason. All of it is in the audit log.

## 6. Customer side

- **Recipient check.** Returns `none`, `caution` or `high` for a receiver, with
  the policy's warning text. An unknown wallet and an ordinary one give the same
  answer, so the endpoint cannot be used to find out who is a customer. Limited to
  30 checks a minute per sender.
- **Responding to a warning.** `cancel` or `proceed`. For step-up, `proceed` needs
  `step_up_passed: true` from upay's own authentication **and** the cooling-off
  period to be over (`409 cooling_off` with `Retry-After` until then).
- **Reporting a scam.** Stored and attached to a case for a person to read. A
  report on its own flags nobody and releases nothing; its text is never used as
  an instruction to anything. Limited to 5 an hour per reporter.

## 7. Security

- **Passwords** are hashed with Argon2id. A failed login gives the same answer
  for a wrong password and an unknown user. Five failures in five minutes lock
  the username from that address (`429`, `Retry-After`), and each address is
  limited overall.
- **Tokens** are signed JWTs (HS256, 8 hours). Every request re-reads the user:
  disabling an account or changing its role takes effect at once.
- **Roles**: `analyst`, `supervisor`, `admin`, `service`. The admin role can read
  the audit log and the metrics but not customer data; the service account can
  score and cannot read alerts or cases.
- **Audit log.** Logins, every case action, every freeze step, every flag and
  every view of a wallet profile. The table is append-only: a database trigger
  refuses `UPDATE`, `DELETE` and `TRUNCATE`.
- **Input.** Every body is validated with unknown fields refused; identifiers
  match a fixed pattern; bodies over 1 MB are refused; all SQL is parameterised.
- **Headers.** `nosniff`, `X-Frame-Options: DENY`, `no-store`, `no-referrer`,
  HSTS in production. CORS allows only the configured origins and no cookies.
- **Production guard.** With `FRAUDLENS_ENVIRONMENT=production` the API refuses to
  start with the development signing secret, and the interactive docs are off.
- **Language model.** Optional, off by default, and only for the wording of a case
  note after the decision exists (DECISION_POLICY §7).

## 8. Stream and live feed

`POST /v1/events` appends to a Redis stream; a worker thread in the API process
reads it through a consumer group, in order, in batches, and acknowledges after
the batch is committed. An entry that cannot be parsed or fails validation goes
to a dead-letter stream with the reason and does not block the ones behind it.
Entries delivered before a crash are handled first after a restart, and since
scoring is idempotent a redelivery is harmless. If the backlog passes 200,000 the
endpoint answers `503 backlog_full` rather than queue without limit.

Every alert is published on a Redis channel; `GET /v1/stream/alerts` relays it as
server-sent events with a keep-alive every 15 seconds.

## 9. Restart recovery is exact

The feature state lives in memory. The database is its log: every transaction and
flag that entered the state carries the position at which it did
(`applied_seq`). On start the scorer loads the snapshot taken at the end of the
historical period and re-applies everything after it in that order, each
transaction with the time at which it was applied (a released hold is applied at
its release, not at its arrival). The integration tests check that, after holds,
releases, customer answers, late flags and freezes, the rebuilt state gives
bit-identical features to the one that lived through it.

## 10. Limits, stated plainly

- **One scorer process.** The feature state is in one process's memory, so the
  API cannot be scaled by adding copies. Scaling out means partitioning by wallet
  or moving the state to a shared store; neither is built.
- **Recovery time grows** with the number of transactions since the snapshot. A
  production system would write snapshots periodically; this one has only the
  end-of-history snapshot.
- **Rate limits and the login lockout use the address the API sees.** Behind a
  proxy, run uvicorn with `--proxy-headers` and a trusted proxy list, or every
  client shares one address.
- **The live feed authenticates with the Bearer header**, which the browser's
  `EventSource` cannot send; the console reads it with `fetch`.
- **Step-up is asserted by the caller** (`step_up_passed`). The platform trusts
  upay's authentication; it does not perform one.
- **Wallet numbers in API responses to analysts are not masked** (they need them
  to act). Masking applies to case notes and anything sent to the language model.
- **No TLS, secrets manager or key rotation**: deployment concerns outside this
  prototype.

## 11. Measured

One run on a laptop (Apple silicon), API with one process, Postgres and Redis in
Docker on the same machine. Reports are written to `backend/artifacts/reports/`.

**The served decisions are the evaluated ones.** The whole test period (25 days,
304,734 transactions and 133 fraud flags) was replayed into the running service:
days 95–118 through the event stream, day 119 one HTTP request at a time.
`make verify` then compared what was served with the offline evaluation:

| | |
| --- | --- |
| Scored transactions compared | 136,180 of 136,180 |
| Tier differs | 0 |
| Feature rows that differ | 0 |
| Largest difference in risk | 0.0 |
| Served in rules-only mode | 0 |
| Dead-lettered or failed events | 0 |

So the numbers in MODEL_CARD and DECISION_POLICY describe what this service
actually does, not a separate offline code path. (136,180 includes the 1,635
transactions with an ambiguous role that the model report leaves out of its
metrics.) The replay produced 1,094 holds grouped into 265 cases.

**Latency**, day 119 over HTTP, sequential, one connection (12,297 transactions,
5,152 of them scored):

| Milliseconds | p50 | p95 | p99 |
| --- | --- | --- | --- |
| Features + models + policy + reasons, scored transactions | 3.8 | 4.9 | 7.1 |
| Full round trip, scored transactions (incl. database commit) | 10.6 | 15.1 | 23.3 |
| Full round trip, all transactions | 6.8 | 13.1 | 19.2 |

**Throughput.** The stream worker handled about 380 events a second (roughly 190
scored decisions a second), and the full replay of 292,567 events took about
thirteen minutes. Eight concurrent what-if callers got 194 answers a second with
a p95 of 51 ms: scoring is serialised in one process, so concurrency adds waiting,
not capacity (§10). For scale: the simulated system averages 0.14 transactions a
second, and 190 scored decisions a second is about 16 million a day.

**Restart.** With all 304,867 events since the snapshot to re-apply, the service
is ready 8–10 seconds after start. The rebuilt state was compared with the
offline feature engine at the end of the test period on 20,000 probe
transactions: 0 feature rows differ.

**Slowest read endpoints** on this data: agent risk ranking about 400 ms and the
metrics summary about 270 ms (both aggregate over all rows on each call and are
not cached); the rest answer in under 50 ms.

## 12. Run it

```
make up          # Postgres and Redis
make pipeline    # data, features, models, policy (once)
make platform    # migrate, create the demo accounts, load the history
make api         # API and worker on http://127.0.0.1:8010
make replay      # test days 95-118 through the stream   (other terminal)
make replay-live # day 119 over HTTP, writes the latency report
make verify      # served decisions against the offline evaluation
make test        # 147 tests; 32 of them run against real Postgres and Redis
```

The demo accounts (`analyst1`, `analyst2`, `supervisor1`, `supervisor2`, `admin`,
`upay-core`) get the password in `FRAUDLENS_SEED_PASSWORD` (`backend/.env`, see
`.env.example`).
