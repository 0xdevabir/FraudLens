# Mule-intelligence consortium

A mule that is reported at one provider usually still has a wallet at another one.
Today each provider learns this on its own, one victim at a time. The consortium lets
the providers warn each other about mule wallets **without any of them handing over a
customer's number, handset or identity**.

Everything here is a simulation on the FraudLens dataset. The four providers, their
market shares and the share of people with a second wallet on the same SIM are
*assumptions*, labelled "(simulated)" in the console. The protocol is real and
running code: `backend/src/fraudlens/consortium/`, Python standard library only.

| Piece | Where |
|---|---|
| Crypto: OPRF, Schnorr signatures, Bloom filter | `consortium/crypto.py` |
| Bundle, listing, ledger, lookup, audit chain | `consortium/protocol.py` |
| Hub (token service, relay, disputes) and Member | `consortium/hub.py` |
| Four-provider market and the experiment | `consortium/simulate.py` → `artifacts/consortium/report.json` |
| Demo state for the console | `consortium/demo.py` |
| API | `GET /v1/consortium`, `/matches`, `/audit`; `POST /lookup`, `/disputes`, `/disputes/{id}/resolve` |
| Console | **Network → Mule consortium** (also linked from the rings page) |
| Tests | `backend/tests/test_consortium.py` |

```
uv run python -m fraudlens.consortium.simulate --quick   # one seed, ~10 min
uv run python -m fraudlens.consortium.simulate           # 5 seeds x 3 SIM-sharing rates
```

Nothing else changes: the dataset, features, served model and headline numbers are
untouched; the routes answer 404 (`consortium_not_built`) until the simulation has run.

## Protocol

**Identifiers.** Two kinds: the wallet number (MSISDN, normalised to `8801XXXXXXXXX`)
and the handset id. Names, NIDs and balances are never part of the protocol.

**Tokens (oblivious PRF).** A plain hash or HMAC of a phone number is reversible: the
Bangladeshi numbering plan has about 10⁹ numbers, an afternoon of hashing. So tokens
come from an OPRF held by the hub, in the 2048-bit MODP group of RFC 3526:

1. The provider maps the identifier into the group, `h = H(kind, id)`, and blinds it
   with a fresh random `r`: it sends `h · g^r`.
2. The hub raises it to its secret key `k` and returns it. It sees a random group element.
3. The provider removes the blind with the hub's public key, getting `h^k`, and hashes
   that (with the key epoch and kind) to a 128-bit token.

Same identifier, same token at every provider; the hub never learns the identifier;
no provider can make tokens offline, so it cannot brute-force partners' lists. Token
requests are per-member rate-limited (`DEFAULT_QUOTA`, 250,000 a day) and audited.
Providers cache their own customers' tokens for the key epoch, so a payment-time
lookup normally needs no hub round trip.

**Bundles.** Once a day each provider publishes a signed bundle:

- *confirmed* listings (victim-reported mules): token, kind, confidence, typology,
  first-seen, listed-at, **expiry (180 days)** and a listing id;
- *suspected* wallets (its own mule-model alerts, **30-day expiry**) only inside a Bloom
  filter at 10⁻⁶ false hits per lookup: partners can test a token, not list the set;
- the ids it has withdrawn.

Bundles are Schnorr-signed by the issuing provider and themselves expire after two days,
so a feed that stops being refreshed stops counting. Importers check the signature, key
epoch, sequence number (no replay) and expiry, and reject their own bundle.

**Lookup.** At each transfer the receiving provider tokenises the receiver's number and
handsets and checks them against partner bundles. The signal is
`1 − Π(1 − c_p)` over partners `p`, each counted once at its best confidence `c_p`.
A lookup is audited with counts and listing ids, never values.

**Dispute and removal.** Any member can dispute a listing (for instance on its own
customer's appeal). The listing stops counting **for everyone at once**. Only the
listing member decides (it holds the evidence): *upheld* restores it, *withdrawn* removes
it for good. An unanswered dispute is decided **for the customer** after 5 days.

**Audit.** The hub and every member keep a hash-chained log (each entry commits to the
previous hash): joins, token requests and refusals, shares, imports, feeds, lookups,
disputes. Tampering with any entry breaks the chain (`AuditChain.verify`). Console
lookups and disputes are also written to the platform's Postgres audit log.

## Threat model

| Threat | What stops it | Residual risk |
|---|---|---|
| **Reversing tokens** (dictionary over 01XXXXXXXXX) | OPRF: tokens need the hub key, one rate-limited, audited request at a time | A member can still test *its own* candidates at quota rate; quota and audit make bulk probing visible |
| **Hub learns identifiers** | Blinding: the hub sees `h·g^r`, uniformly random | The hub sees request *volumes* per member |
| **Linkage across epochs** | Tokens bind the key epoch; rotating `k` makes old tokens useless | Within an epoch a token is a stable pseudonym among members |
| **Listing enumeration of suspected wallets** | Suspected wallets are only in a Bloom filter | A member can test tokens it already holds |
| **Poisoning by a malicious member** (listing innocents) | Per-listing attribution via signatures; dispute → instant suspension; disputes-against counted per member; rule-based use of *confirmed* listings only | Measured below: it does hurt until disputes catch up. Governance must be able to suspend a member |
| **Forged or altered listings by the hub** | Schnorr signature per provider; hub only relays | — |
| **Stale or replayed feeds** | Sequence numbers, 2-day bundle expiry | — |
| **Hub key compromise** | Keep `k` in an HSM; rotate per quarter (`key_epoch`); members re-tokenise their own lists | Past bundles become linkable to whoever holds the old key and a candidate list |
| **Member signing-key compromise** | Hub re-registers the member's key; bundles under the old key rejected | Listings forged until rotation |
| **False listing harms a customer** | Expiry, dispute, default-for-customer after 5 days; a match is a signal to a reviewer, never an automatic freeze | — |

The simulated keys in `artifacts/consortium/demo_keys.json` are all held by one process
for the demo. In a deployment nobody holds more than its own.

## Governance and Bangladesh alignment

- **Who runs the hub.** A neutral operator under Bangladesh Bank oversight (the Payment
  Systems Department, alongside the NPSB switch), not one of the providers.
- **Legal basis.** Sharing is for fraud prevention under the MFS regulations and the
  BFIU's anti-money-laundering framework, which already oblige providers to act on mule
  accounts and report suspicious transactions. Tokens and Bloom filters keep it to the
  minimum needed, in line with the data-minimisation and purpose-limitation principles
  of Bangladesh's personal-data-protection framework. (Legal review is still needed;
  this is the design intent, not legal advice.)
- **Membership.** Signed membership agreement; per-member quotas; a member whose
  listings are repeatedly withdrawn after dispute is reviewed and can be suspended (the
  hub counts `disputes_against` and `withdrawn_after_dispute`).
- **Customers.** A listing only ever raises a review or a step-up at the receiving
  provider. Customers can appeal through their own provider, which raises a dispute.
- **Retention.** 180 days for confirmed, 30 for suspected, 2 for a bundle; audit logs
  kept for the regulator.

## Measured results

From `artifacts/consortium/report.json` (full run: 5 seeds × 3 SIM-sharing rates, 552 s).
Test period 2026-04-06 → 2026-05-01. It contains 145 mule wallets and about 14,800
innocent wallets. The served mule model and its threshold (0.1485) are unchanged in
every arm. Listing confidences are measured, not assumed:

- *confirmed* = 0.893, the precision of the 261 wallets reported before the test period;
- *suspected* = 0.586, the precision of the 58 wallets the mule model alerted on val_b.

The seed changes only which wallet shares a SIM with which. "Shared SIM" is the
simulated share of people who have a second wallet on the same SIM at another provider.

**Main result, 25 % shared SIM.** Mean over 5 seeds, with the [min, max] range.

| Arm | Recall | False alerts (FPR) | Mules found only via consortium | Detected before any victim paid | Victim transfers before detection | ৳ paid to mules never detected | Median hours ahead of report |
|---|---|---|---|---|---|---|---|
| Mule model only (served) | 0.648 | 40 (0.27 %) | — | 70 | 72 | 706,840 | 40.2 |
| + confirmed MSISDN listings (rule) | **0.693** [0.690, 0.697] | 40.8 (0.28 %) | **6.4** [6, 7] | **77.2** [76, 78] | 69.6 | **656,844** | 41.8 |
| + MSISDN, threshold tuned on val_b | 0.684 [0.648, 0.697] | 40.6 (0.27 %) | 5.2 [0, 7] | 76.0 | 69.6 | 662,156 | 41.7 |
| + handset listings too (rule) | 0.693 | 113.8 (0.77 %) | 6.4 | 77.2 | 69.6 | 656,844 | 41.8 |
| handset only / MSISDN+handset, tuned | 0.648 | 40 | 0 | 70 | 72 | 706,840 | 40.2 |

**Effect of the shared-SIM rate.** Confirmed-MSISDN rule arm; the baseline is the same at every rate.

| Shared SIM | Recall | False alerts | Mules found only via consortium | ৳ to mules never detected |
|---|---|---|---|---|
| 0 % (control) | 0.648 | 40 | 0 | 706,840 |
| 25 % | 0.693 | 40.8 | 6.4 | 656,844 |
| 50 % | **0.728** [0.710, 0.759] | 42.0 | **11.6** [9, 16] | **561,170** |

The 0 % row is a control. When nobody has a second wallet on a shared SIM, MSISDN
listings match nothing, and the measured gain is exactly zero.

How to read this:

- **Keep fraud-team precision; raise recall.** Confirmed MSISDN listings add about 6
  mules at 25 % shared SIM (+4.4 recall points) and about 12 at 50 % (+8 points). That
  costs at most 2 extra false alerts on roughly 14,800 innocent wallets. 7 more mules
  (77 vs 70) are caught before any victim pays them. Money sent to mules that are never
  caught falls by ৳50k at 25 % shared SIM and ৳146k at 50 % (−7 % and −21 %).
- **Handset listings are not worth it.** In every run they add 63–80 false alerts and
  find no extra mules: innocents share handsets with mules. Keep them as a review hint.
  They must never trigger an alert on their own.
- **Tuning on val_b is too timid.** val_b holds only 39 mules, so a threshold tuned
  there often falls back to the model alone. Using confirmed listings as a rule, at
  their measured precision, works better.
- **Poisoning is real.** In one test, one member (upay) lists 500 innocent numbers as
  "confirmed". The rule arm's false alerts rise from 41 to 462 (FPR 3.1 %). Disputes were not simulated; in practice the damage lasts until
  disputes suspend those listings. That is why the dispute route suspends a listing for
  all members at once, and why the hub counts `disputes_against` for each member.
- **Cost.** One OPRF token takes 6.3 ms on the client and 2.8 ms at the hub. The run
  makes 116,087 tokens, one per wallet identifier, cached for the key epoch.

## Not done

- Real PSI with cardinality hiding, and threshold OPRF across several hubs.
- Per-member weighting learned from dispute outcomes.
- Production key management (HSM) and network transport; the demo is one process.
- A hosted multi-party deployment: the API serves a saved simulated state.
