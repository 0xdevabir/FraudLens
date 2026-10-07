# Plan 09: a privacy-preserving cross-provider mule-intelligence consortium

## Goal

Mule rings in Bangladesh spread their wallets over bKash, Nagad, Rocket and upay, so
no single provider sees the whole ring. Give FraudLens a protocol through which
providers share *who the mules are* without sharing *who their customers are*:

- each provider publishes a signed bundle of blinded tokens for the wallet numbers
  (MSISDNs) and handset fingerprints of its confirmed and suspected mules;
- tokens come from an oblivious PRF held by the consortium hub, so raw identifiers
  never leave a provider, and a member cannot compute tokens offline to enumerate
  the 01XXXXXXXXX number space;
- partners receive a Bloom filter of the tokens (local, offline check at payment
  time) and fetch a listing's details only on a hit;
- every listing carries confidence, typology, first-seen, expiry, and can be
  disputed and withdrawn; every share and lookup goes into a hash-chained audit log;
- the lookup becomes a new receiver-side signal.

## Judge criteria it moves

- **Innovation (7.67).** Cross-provider, privacy-preserving mule intelligence is not
  something any one provider can build; it is the project's most distinctive idea.
- **Scalability (6.67).** The design scales across institutions, not just across
  machines: per-payment checks are a local Bloom-filter probe (no network call),
  bundles are deltas, and the hub only does O(listings) work.

## Steps

1. `backend/src/fraudlens/consortium/`:
   `crypto.py` (2048-bit MODP group, OPRF with multiplicative blinding, Schnorr
   signatures, Bloom filter: stdlib only), `protocol.py` (identifiers, listings,
   signed bundles, partner ledger, disputes, hash-chained audit), `hub.py` (member
   registry, rate-limited OPRF evaluation, bundle relay, dispute resolution),
   `simulate.py` (opt-in multi-provider overlay and the experiment).
2. Simulation, opt-in, default dataset untouched: assign every wallet of the existing
   dataset to one of four providers; give each wallet a synthetic MSISDN; let a
   configurable share of mule recruits hold wallets at two providers on one SIM
   (and the same share of ordinary customers, so false matches are measured too).
   Handset sharing comes from the dataset itself.
3. Experiment on the test period: receiver-side mule-wallet recall, wallets alerted
   before any victim paid, victim transfers and hours before detection, for the
   mule model alone vs mule model + consortium (handset tokens, MSISDN tokens,
   both), with the consortium arm's threshold set on `val_b` to the baseline's
   wallet-level FPR and FPR reported on test. Write
   `backend/artifacts/consortium/report.json` and the demo state.
4. API (additive): `GET /v1/consortium` (members, feeds, privacy guarantee,
   measured results), `GET /v1/consortium/matches`, `GET /v1/consortium/audit`,
   `POST /v1/consortium/lookup` (wallet id -> blinded lookup, audited),
   `POST /v1/consortium/disputes`.
5. Console: `/consortium` page (partner feeds, matches, privacy guarantee, results),
   linked from the nav and from the rings page.
6. `docs/CONSORTIUM.md`: protocol, threat model, governance, Bangladesh Bank and
   data-protection alignment, measured results.
7. Tests for the crypto, protocol, disputes, audit chain, and the API route shape.

## Definition of done

- `uv run pytest -q` and `make lint` pass; new tests cover blinding correctness,
  signature forgery rejection, Bloom lookups, TTL, disputes and audit tamper detection.
- `python -m fraudlens.consortium.simulate` writes a report with measured numbers;
  the doc quotes only those numbers.
- Default artifacts, headline numbers and the served decision path are unchanged.
- Console page renders from the API; committed on the worktree branch.
