# Scaling the scorer

How the event stream is consumed by several worker processes at once, what that
gains, and where it stops. The code is in `backend/src/fraudlens/platform/`
(`stream.py`, `worker.py`, `scoring.py`); the deployment files are in `deploy/`
and `docker-compose.scale.yml`.

## 1. The constraint

A transaction's features depend on every transaction and flag before it: the
sender's velocity, the recipient's fan-in, the agent's cash-out pattern. The
state that holds them lives in memory, and the database is its log (PLATFORM §9).
Two workers that each applied half the stream would each hold the wrong state.
So the design keeps one order for changing the state, and moves everything else
out of it.

## 2. Every process is a replica

The API and every worker load the model and the policy once, at start, and
rebuild the whole feature state from the snapshot plus the log. Before a process
changes the state it takes a Postgres advisory lock and replays whatever other
processes have applied since it last looked (rows with a higher `applied_seq`),
so it always starts from the latest state. An approved freeze or release made
through the API reaches the workers the same way, and the API follows the log
in the background (`FRAUDLENS_FOLLOW_INTERVAL_S`), so its `/v1/score` answers
see what the workers did.

## 3. Two steps per batch

Workers share one Redis consumer group, `scorer`, on `fraudlens:events`.

1. **Ordered: into the state.** A worker may put a batch into the state only once
   the entry just before it has been put in by someone. The Redis key
   `<stream>:sequenced` holds the last entry that has; a Lua script moves it
   forward and never back. Under the advisory lock the worker catches up, drops
   duplicates, computes features, applies the events and writes the transaction
   rows, plus a `pending_decisions` row holding each transaction's features. It
   then commits and releases the lock. This step is the short one.
2. **Parallel: the decision.** Outside the lock the worker runs the models, the
   policy and the explanations, which is most of the cost. It then claims its
   rows by deleting them from `pending_decisions` (`DELETE … RETURNING`) and,
   in the same transaction, writes decisions, cases and shadow scores. Cases are
   per subject, so it takes a lock per subject, in sorted order. After the
   commit it publishes alerts, then acknowledges and deletes the stream entries.

## 4. Exactly once

Delivery is at least once and the effects happen once:

- **Duplicates never reach the state.** A transaction id already in the
  database is a duplicate, and is checked under the lock.
- **A decision is written only by whoever deletes the pending row.** If two
  workers end up with the same entry, after a reclaim racing a slow worker, the
  loser deletes nothing and writes nothing. The test
  `test_two_workers_holding_one_entry_record_one_decision` drives this race.
- **Crashing between the steps loses nothing.** The pending row survives with
  the features exactly as computed, so whoever takes the entry over decides from
  them without touching the state again.

## 5. A worker that dies

Every worker runs `XAUTOCLAIM` every `claim_idle_ms / 2` (30 s by default),
including while it waits for an earlier entry to be sequenced. Entries idle in
a dead consumer's pending list move to a live one and are handled one by one, in
order. A pod name is a consumer name, so a restarted pod is a new consumer and its
predecessor's entries are claimed like any other's. The test
`test_a_stopped_workers_entries_are_taken_over_in_order` stops a worker holding
entries and checks that the others finish them, in order. The stream-replay
tests drain the test period with two workers and require the served features
and tiers to match the offline evaluation exactly.

## 6. Measured

`make loadtest` (`fraudlens.platform.loadtest`) resets the database to the
historical period, starts N worker processes, and:

- **Saturation.** Writes the first 20,000 test-period events at once and times
  how long it takes until the last one is decided.
- **Open loop.** Writes the next 9,000 at a fixed 200 a second. It records each
  entry's end-to-end latency, from the entry being added to its decision being
  committed.
- **Parity.** Runs `verify`, comparing every served decision with the offline
  evaluation.

Machine: Apple M5, 10 cores, 16 GB, macOS 27.0, Postgres 16 and Redis 7 in
Docker. Other jobs were running on it: the load average was 21 to 28 throughout,
so absolute numbers are pessimistic and noisy. Raw results:
[`scaling/loadtest-20261007-094657.json`](scaling/loadtest-20261007-094657.json).

| Workers | Throughput (events/s) | Speed-up | p50 (ms) | p95 (ms) | p99 (ms) | Tier mismatches | Differing feature rows |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 334 | 1.00x | 54.1 | 439.3 | 1014.4 | 0 | 0 |
| 2 | 579 | 1.73x | 40.5 | 194.1 | 291.7 | 0 | 0 |
| 4 | 931 | 2.78x | 105.6 | 192.2 | 287.7 | 0 | 0 |

Each run compared 14,419 decisions, and `verify` passed every time. The work
split evenly: 14,540 and 14,460 entries with two workers, and 6,951 to 7,530
each with four.

What the numbers say:

- **Throughput grows with workers, sub-linearly.** The parallel step is most
  of the work, so two workers nearly double it. At four, the ordered step and
  the per-subject case locks start to show.
- **Tail latency falls sharply.** One worker at 200 a second runs at about 60%
  of its capacity, so bursts queue: p99 is one second. With two or more workers
  the backlog clears and p99 drops to about 0.3 s.
- **The p50 rises at four workers.** Each batch now waits its turn for the
  ordered step, on a machine with more runnable jobs than cores.
- **For scale:** the simulated provider averages 0.14 transactions a second, and
  a large real one peaks at a few thousand.

## 7. Deployment

- **Compose.** `docker compose -f docker-compose.yml -f docker-compose.scale.yml
  --profile demo up -d --scale worker=4` runs the demo with four worker
  containers. The API keeps scoring requests and follows the log.
- **Kubernetes.** `deploy/k8s` (`kubectl apply -k deploy/k8s`) contains:
  - Deployments for the API and the workers, with startup, liveness and
    readiness probes (the worker serves `/health`, `/ready` and `/metrics` on
    8081);
  - resource requests and limits;
  - a PodDisruptionBudget for each;
  - a KEDA `ScaledObject` that scales the workers on the stream's length, from 1
    to 4.

  Workers delete entries once decided, so the length is exactly the work still
  to do. `hpa-external.yaml` is the same policy as a plain HPA on an external
  metric, for clusters without KEDA. The manifests pass `kubeconform -strict`
  against Kubernetes 1.30 and the KEDA CRD schemas.

## 8. Limits, stated plainly

- **The ordered step is a ceiling.** Events enter the state one batch at a time,
  so throughput stops growing at a few workers. This is why the scaler stops at
  4. Going further means partitioning the state by wallet, so that unrelated
  wallets sequence independently. That is not built, and it is hard because a
  transaction touches two wallets and an agent.
- **Every process holds the whole state.** Memory grows with the number of
  replicas, and so does start-up replay time.
- **Measured on one busy laptop,** with Postgres and Redis on the same machine.
  The manifests have not been run on a real cluster.
- **The live feed is at most once.** A worker that crashes after its commit but before
  publishing loses that alert on the live feed. The decision and the case are in
  the database, and the console shows them.
