# Architecture

How the parts of FraudLens fit together and why they are split the way they are.
The detail of each part is in its own document: data in
[DATA_ASSUMPTIONS.md](DATA_ASSUMPTIONS.md), models in
[MODEL_CARD.md](MODEL_CARD.md), decisions in
[DECISION_POLICY.md](DECISION_POLICY.md), the service in
[PLATFORM.md](PLATFORM.md).

## 1. The problem shapes the design

In an authorised-push-payment scam the victim is the one who presses "send".
Their device, PIN and location are their own, so sender-side fraud checks see a
normal payment. Three consequences run through the whole system:

1. **Score the recipient.** A mule wallet is young, receives from strangers and
   empties itself quickly. The mule model uses recipient features only, so it
   works even when nothing is known about the sender.
2. **Talk to the customer before the money moves.** The cheapest interception is
   the victim changing their mind, so the first two tiers are a warning and a
   cooling-off wait, in Bangla, with fixed texts.
3. **Take the second chance.** If the transfer goes through, the money still has
   to leave through a cash-out. Cash-outs are scored too, and agents are compared
   with their peers.

## 2. Layers

```
                 offline (make pipeline)                      online (make api)
┌────────────┐   ┌───────────┐   ┌──────────┐   ┌─────────┐   ┌──────────────────────────┐
│ simulator  │──▶│ features  │──▶│ models   │──▶│ policy  │──▶│ Scorer                   │
│ 120 days   │   │ engine    │   │ registry │   │ report  │   │ same feature engine,     │
│ 1.45M txns │   │ 61 cols   │   │ v1, v2…  │   │ insights│   │ same bundle, same policy │
└────────────┘   └───────────┘   └──────────┘   └─────────┘   └────────────┬─────────────┘
                                      ▲                                    │
                                      │ verdicts as labels                 ▼
                                 ┌────┴─────┐      ┌───────────┐   ┌──────────────┐
                                 │ retrain  │◀─────│ cases,    │◀──│ decisions,   │
                                 │ shadow   │      │ verdicts, │   │ alerts,      │
                                 │ drift    │      │ freezes   │   │ audit log    │
                                 └──────────┘      └───────────┘   └──────────────┘
                                                         ▲                 │
                                                         └── console ◀─────┘
```

| Package (`backend/src/fraudlens/`) | Responsibility |
| --- | --- |
| `simulator/` | Discrete-event world: customers, agents, merchants, five fraud typologies, reports that arrive late and only for some cases |
| `features/` | `FeatureEngine`: point-in-time state per wallet, agent and device; the same class builds the training table and serves live |
| `models/` | Training, evaluation, the model bundle, the registry, agent peer comparison, ring detection |
| `decision/` | Policy file, rules, tiers, reasons, similar cases, case notes, the offline policy evaluation and the dashboard tables |
| `platform/` | Scoring service, cases and freezes, stream worker, storage, audit, security, PII masking, replay and verification |
| `mlops/` | Verdicts as labels, retraining, shadow scoring, drift |
| `api/` | HTTP: routes, schemas, response views, middleware |

The console (`frontend/`) is a separate Next.js application that talks to the API
over HTTP with a bearer token. It holds no business logic: every number it shows
is computed by the backend.

## 3. Decisions that matter

**One feature engine, offline and online.** The class that replays the dataset to
build the training table is the class the API holds in memory. There is no second
implementation to drift. `make verify` replays the test period through the
running service and compares every served decision with the offline evaluation:
0 tier differences and 0 differing feature rows on 136,180 scored transactions
(PLATFORM §11).

**Features only look backwards.** A transaction's features come from state as it
was before that transaction, and a fraud flag is visible only from its own
timestamp. A test replays a truncated history and checks the features are
unchanged. Splits are by time; one typology exists only in the test period.

**Rules sit beside the model, not inside it.** The policy is a versioned YAML
file: tier thresholds, hard rules (a confirmed-fraud recipient, for example),
customer texts, the review deadline. A decision records which rule or which
threshold decided it. The model can be retrained without touching the rules and
the rules can change without retraining.

**Thresholds come from budgets.** A tier's threshold is chosen on validation data
to meet a precision target within a cap on the share of traffic interrupted. The
score is treated as a ranking, not as a literal probability (MODEL_CARD §9).

**The service still answers without a model.** If the bundle cannot be loaded,
decisions come from the rules and a small set of fallback conditions
(DECISION_POLICY §5), and `/ready` says so.

**The fused score was built and not served.** A logistic fusion of the three
scores was fitted and compared; it did not beat the transaction model, so the
simpler thing is served and the comparison is kept in the model card (§4).

**People decide what is irreversible.** A hold opens a case and waits for a
verdict. A freeze needs a request and a different supervisor's approval, enforced
in the API and by a database constraint. A ring freeze is a batch of such
requests, never a shortcut around them.

**The language model only words things.** It can write the case summary from a
JSON document of masked, structured evidence after the decision exists. Its
output is rejected if it contains a number or identifier that is not in the
evidence, and the deterministic template is used instead. It is off by default.

**State in memory, log in the database.** The feature state lives in the scorer
process for speed (about 3 ms for features, models, policy and reasons when
measured in process; served figures are in PLATFORM §11). Every
event that entered the state is stored with its position, so a restart rebuilds
the same state exactly. The same log lets several processes hold the state: each
catches up from it before it changes anything, so stream workers scale out
behind one ordered step (SCALING.md).

**Two ways in.** `POST /v1/score` answers now, for a payment waiting on a
decision. `POST /v1/events` queues to a Redis stream for everything that needs no
answer. Both end in the same scorer, in one order.

**A challenger never decides.** `make retrain` registers a new version and
promotes nothing. Set as the shadow model, it scores every transaction next to
the served one and its scores are stored separately; the console compares the two
on the decisions both have seen. Promotion is a deliberate command and takes
effect on restart.

## 4. Data stores

| Store | Holds | Why |
| --- | --- | --- |
| PostgreSQL | wallets, transactions, decisions, cases, freeze requests, shadow scores, users, the append-only audit log | The record. Recovery, the console and retraining all read it |
| Redis stream | events waiting to be scored, and a dead-letter stream | Ordered, acknowledged ingestion |
| Redis keys and pub/sub | rate limits, login lockout, the live alert feed | Short-lived, shared across requests |
| Files (`backend/data`, `backend/artifacts`) | the dataset, model versions with their reports, the end-of-history state snapshot | Reproducible from code, never committed |

## 5. Time

The platform's clock is the time of the newest event it has seen, not the wall
clock, because the data is a recorded period (January to April 2026). Review
deadlines and cooling-off periods are measured on that clock; the phone demo can
move it forward to show a wait ending. The audit log is the exception: it records
the real time at which a person acted.

## 6. Identifiers and privacy

The data has no names, phone numbers or national IDs. Wallet, agent and device
numbers are still treated as personal: the console shows them masked
(`W***6128`), and showing one in full is a click that writes the viewer's name to
the audit log. Free text typed by a customer is stored as written and has long
numbers and e-mail addresses masked whenever it leaves the database. Evidence
sent to the language model is built from masked identifiers.

## 7. Deployment in this repository

`docker-compose.yml` has two layers. `make up` starts Postgres and Redis for
development on the host. `make demo` adds the API and console containers; the API
container's entry point (`fraudlens.platform.demo`) builds whatever is missing,
in order (dataset, features, models, policy, database, replay, review, retrain,
shadow scoring), and then serves. Each step is skipped when its output exists, so
a restart goes straight to serving.

`docker-compose.scale.yml` runs the stream workers as their own containers
(`--scale worker=4`), and `deploy/k8s` has the Kubernetes manifests, with the
workers scaled on stream lag (SCALING.md).

Not built: TLS, a secrets manager, periodic
state snapshots. These are listed with the other limits in PLATFORM §10.
