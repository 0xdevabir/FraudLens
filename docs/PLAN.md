# FraudLens build plan

Scam-to-cash-out interception for mobile financial services: score the sender's
behaviour **and** the recipient wallet and its network, explain every alert, and
route it to a tiered action with a human in the loop.

Each phase ends with a **Verified by** check. A box is ticked only after that
check has actually been run and passed.

## Stack

| Layer | Choice |
| --- | --- |
| Data / ML | Python 3.12 (uv), NumPy, pandas, LightGBM, scikit-learn, NetworkX |
| API | FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16 |
| Streaming | Redis Streams (scored-event bus, live alert feed, async worker) |
| GenAI | Provider-agnostic narrative layer; deterministic template fallback |
| Frontend | Next.js (App Router), TypeScript, Tailwind |
| Infra | Docker Compose, Makefile, pytest, ruff, GitHub Actions |

## Phase 0 — Scaffolding
- [x] Repo layout, `backend/` uv project, pinned dependencies
- [x] `docker-compose.yml` with Postgres and Redis
- [x] Makefile targets, ruff + pytest config
- **Verified by:** `uv sync` succeeds, `pytest` runs, containers report healthy.

## Phase 1 — Synthetic data simulator
- [x] World: customers, agents, merchants, devices, districts
- [x] Normal behaviour: salary cycle, Eid spike, remittance, time-of-day, balances
- [x] Legitimate hard negatives: F-commerce sellers (high fan-in), new phones, travel, emergencies
- [x] Fraud typologies: impersonation, wrong-send refund, lottery/fee, account takeover, mule chains, agent collusion
- [x] Held-out typology (investment scam) that appears only in the test period
- [x] Time-based split, ground-truth labels at transaction, wallet, agent and case level
- [x] `docs/DATA_ASSUMPTIONS.md` documenting every assumption
- **Verified by:** unit tests on invariants (no negative balances, chronological order,
  held-out typology absent before the test period) and a printed data profile.

## Phase 2 — Feature layer
- [x] Stateful feature engine: one code path for training and live scoring
- [x] Sender behaviour features (rolling windows, deviation from own history)
- [x] Recipient features (fan-in, dwell time, pass-through, account age)
- [x] Pair and graph features (prior relationship, shared devices, link to flagged wallets)
- [x] Device/location features, agent peer features
- [x] State snapshot and restore
- **Verified by:** leakage test (features use only past events), offline/online parity
  test (identical values), snapshot round-trip test.

## Phase 3 — Models
- [x] Transaction risk model (LightGBM) with calibration
- [x] Mule wallet model (recipient-side features only)
- [x] Behavioural anomaly model (Isolation Forest)
- [x] Score fusion with ablation (does fusion beat the single model?)
- [x] Agent risk score (peer deviation)
- [x] Ring detection (graph communities over suspicious wallets)
- [x] Evaluation report: PR-AUC, recall at alert budget, precision@k, taka saved,
      per-typology recall including the held-out typology
- [x] Model registry with versioned artifacts and metadata
- **Verified by:** evaluation on the untouched test period, written to
  `artifacts/` and summarised in `docs/MODEL_CARD.md` with real numbers.

## Phase 4 — Decision engine and explanation
- [x] Versioned policy file: rules kept separate from ML scores
- [x] Tiers: allow, warn, step-up, hold for review; thresholds from alert budget
- [x] Rules-only fallback when a model is unavailable
- [x] Per-alert reasons from feature attributions, in English and Bangla
- [x] Rule trace
- [x] Case narrative from structured evidence, with a grounding check
- [x] Similar past cases, recommended next action
- **Verified by:** unit tests per rule and tier, fallback test, grounding test
  (a narrative containing a number not in the evidence is rejected).

## Phase 5 — Platform
- [x] Database schema and migrations
- [x] Scoring API with latency measurement
- [x] Auth, role-based access, audit log
- [x] Case workflow endpoints (assign, verdict, notes, escalate)
- [x] Two-person approval for wallet freezes
- [x] Wallet profile, network, rings, agent risk endpoints
- [x] Customer endpoints: warning response, report a scam, recipient check
- [x] Redis stream, worker, live alert feed
- [x] Historical load and accelerated replay of the test period
- **Verified by:** API tests against real Postgres and Redis, an end-to-end replay
  run, and measured p95 scoring latency.

## Phase 6 — Analyst console and customer demo
- [x] Login, layout, live alert queue
- [x] Case page: timeline, evidence, reasons, narrative, network graph, actions
- [x] Network explorer, rings, agent risk, wallet profile
- [x] Customer phone demo with the Bangla warning and cooling-off flow
- [x] Impact simulator and executive dashboard
- [x] Model monitoring: performance, drift, fairness
- [x] Policy view and audit log
- **Verified by:** production build passes, and each page is exercised in a browser
  against the running backend.

## Phase 7 — MLOps and responsible AI
- [x] Analyst verdicts become labels; retraining job
- [x] Shadow mode for a challenger model
- [x] Drift monitoring on features and scores
- [x] Fairness report across region, account age, balance tier and channel
- [x] PII masking in UI payloads and narrative prompts
- [x] Prompt-injection defence for the narrative layer
- **Verified by:** tests for masking, retraining on feedback, drift detection on a
  shifted sample, and a generated fairness report.

## Phase 8 — Documentation and delivery
- [ ] README with one-command start
- [ ] Architecture, data assumptions, model card
- [ ] CI workflow
- [ ] Demo script following the judging criteria
- **Verified by:** a clean-clone start following only the README.
