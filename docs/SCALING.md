# Scaling the scorer

What exists, what was measured, and what is deliberately not built.

## What exists: bounded recovery

The scorer holds its feature state in one process. After a restart it rebuilds that
state from the database. Without help it replays every transaction since the
dataset's baseline snapshot, so a restart gets slower as the platform runs.

Live snapshots bound that ([PLATFORM.md](PLATFORM.md) §9). After every
`FRAUDLENS_SNAPSHOT_EVERY_EVENTS` applied transactions and flags (default 50,000),
at graceful shutdown, and on `POST /v1/snapshot`, the scorer writes its state
atomically to `engine_live.pkl` with the log position (`seq`) it covers. The next
start loads it and replays only what came after:

| | Replays |
| --- | --- |
| No snapshot | every applied transaction and flag since the baseline |
| Snapshot at `seq` S | only those with `applied_seq > S` |

It is used only when the baseline file is unchanged and the database holds the same
number of applied transactions and flags up to S. After a reset or a restore it is
discarded and the slow path runs, so the worst case is slow, never wrong. The tests
compare features, flags, frozen wallets, the blocklist and the log position after a
snapshot restart against a full replay, and require them to be identical. `/ready`
reports how the last start went.

The cost is a pause: writing takes the scorer's lock for as long as the write takes
(seconds on the full dataset), so scoring waits. The default interval makes that
rare. A deployment that cannot pause should snapshot at shutdown and on a schedule it
chooses (`POST /v1/snapshot`), not on a count.

## What is not built: more than one scorer

Adding scorer processes is the other half of scaling, and it is not done. The obvious
design, partitioning by wallet so each process owns some wallets, does not work on its
own, and this is why.

A payment's features are not about one wallet. They read the **sender's** history
(rolling windows, habits, devices), the **receiver's** (fan-in, dwell time,
pass-through, age), the **pair** (prior relationship) and the **graph** (links to
flagged wallets, shared handsets). A partition holding the sender knows nothing about
the receiver, so it cannot compute the receiver features, which are the ones that
catch mule wallets, the point of the system. Two more things are global: a flag on a
wallet must reach every partition at once, and the one-open-case-per-wallet rule
spans the victims of one mule.

Two designs could work. Both change how the system is built, which is why they were
left for a decision rather than started:

1. **Shared state store.** Move the per-wallet state to a store every scorer reads
   (Redis, or a purpose-built feature store), keeping scoring itself stateless. Cost:
   the feature engine becomes a set of reads and writes with ordering guarantees, and
   the guarantee that serving and training compute identical features (one engine
   class, no skew) has to be re-proved.
2. **Partition by transaction with state hand-off.** Route by *receiver*, since the
   receiver features are the expensive and decisive ones, and replicate the sender
   summaries each payment needs along with it. Cost: the sender state must be kept
   consistent across partitions, which is the same problem as option 1 for half of
   the features.

Either way, the invariant to protect is the one `make verify` checks: decisions
served equal decisions evaluated offline, with zero differences. A change that cannot
keep that at zero should not ship.

Until then the platform scales vertically, and the single-process limit is stated in
the README's "what this is not".
