# Plan 06 — Horizontal scaling of the scorer

## Goal

Run the stream scorer as N worker processes in one Redis Streams consumer group,
with the same decisions a single process makes (0 differing feature rows against
the offline evaluation), and prove the gain with a load test on the repository's
own replay data. Ship the deployment that goes with it: Kubernetes manifests with
an autoscaler on stream lag, and a compose override that scales workers.

## Judge criterion it moves

**Scalability & integration (6.67/10).** The comments were "operating on a
single-process scorer" and "scaling and security hardening still pending";
ARCHITECTURE §7 lists "horizontal scaling of the scorer" as not built. This plan
answers the scaling half with code, numbers measured on this laptop and
manifests that validate. (Security hardening is another agent's plan.)

## What has to be shared, and why it is hard

The feature state (`FeatureEngine`: per-wallet counters, the transfer graph,
devices, agents, flags) is in the scorer's memory, and a feature vector is only
right if every earlier state change was applied exactly once and in order. It
cannot be partitioned by wallet: a send touches two wallets, a flag walks the
graph three hops. Moving it to Redis would put a network round trip inside every
feature.

Measured on the test period (20,000 events): computing features and applying the
event costs **17 µs** an event; the decision (three models, policy, explanation,
similar cases, case note) costs **1.3 ms** an event. Over 98% of the work does not
need the order.

## Design

1. **The database stays the log; every process is a replica.** Each process keeps
   its own `FeatureEngine` and catches up from the log (`applied_seq` on
   transactions and flags) before it changes anything, the way a restart already
   rebuilds it. A party first seen by another process is registered from its
   database row, so every replica holds the same state.
2. **One writer at a time, in stream order.** The ordered step (dedupe, frozen and
   stale checks, registration, features, apply, insert the transaction rows)
   runs under a Postgres advisory lock and only after every earlier stream entry
   has been through it (a cursor in Redis). It is microseconds per event.
3. **Decisions in parallel.** For recorded events and unscored types the state
   moves whatever the decision, so their feature vectors are stored in a new
   `pending_decisions` table in the same commit and the decision is made after the
   lock is released, by whichever worker sequenced them. Live scored events
   change the state only if allowed, so they are still decided inside the lock.
4. **Exactly-once effects.** Dedupe on `txn_id` under the lock; the decision
   commit claims its pending row with `DELETE … RETURNING`, so two workers that
   both hold an entry write one decision. Case attachment takes a per-wallet
   advisory lock (one open case per wallet already has a unique index).
5. **Reclaim.** Each worker has its own consumer name. Entries idle longer than
   `claim_idle_ms` (a dead worker's) are taken with `XAUTOCLAIM` and finished;
   a worker waiting on a dead predecessor reclaims it itself.
6. **Separate process.** `python -m fraudlens.platform.worker` runs one worker
   with the model and policy loaded once, a small health/metrics HTTP port, and
   graceful SIGTERM. The API keeps its in-process worker by default
   (`FRAUDLENS_RUN_WORKER`), so `make demo` is unchanged; the API's replica
   follows the log in the background.

## Steps

1. Plan (this file).
2. Migration `0004`: `pending_decisions`.
3. `Scorer`: incremental catch-up shared with `recover`, global/case/wallet
   advisory locks, `sequence()` + `decide_pending()` split, `process()` built on
   them; the workflow's `transaction()` takes the same locks.
4. `stream.Worker`: unique consumer, ordering barrier, `XAUTOCLAIM`, two-phase
   batch, latency metrics. `platform/worker.py` command.
5. Tests: two workers in one group give the offline-identical decisions; a
   redelivered entry and a duplicate decision commit write one decision; a dead
   consumer's entries are reclaimed; a replica sees another process's changes.
6. Load test `fraudlens.platform.loadtest`: 1, 2, 4 worker processes on the same
   slice of the test period, fresh database each run; throughput, p50/p95/p99
   end-to-end latency, and a parity check. Results JSON in `docs/scaling/`, table
   in `docs/SCALING.md` with the hardware.
7. `deploy/k8s`: API and worker Deployments, Services, probes, limits, PDBs, KEDA
   `ScaledObject` on stream length, a documented HPA alternative; validated with
   `kubectl --dry-run=client`. `docker-compose.scale.yml` for `--scale worker=4`.
8. Docs: SCALING.md, PLATFORM §8/§10, ARCHITECTURE §3/§7, README pointer.
9. `uv run pytest -q` and `make lint` green; commit.

## Definition of done

- N workers consume one group; the replay through 2+ workers passes `verify`
  with 0 tier mismatches and 0 differing feature rows (test and load test).
- Tests for consumer group, idempotency, reclaim and replica catch-up pass with
  the rest of the suite against the isolated containers.
- `docs/SCALING.md` has a table of measured throughput and latency for 1, 2 and 4
  workers, the raw JSON beside it, and the hardware stated.
- `deploy/` validates with `kubectl apply --dry-run=client` (the KEDA object
  with its CRD stated as a prerequisite); the compose override starts.
- Limits stated plainly: the ordered step is still one at a time, live scored
  events are decided inside it, a replica's reads may lag by a poll interval.
