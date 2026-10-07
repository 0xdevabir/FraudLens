# Ingest — the signed webhook for a core-banking system

`/v1/score` and `/v1/events` need a service account's bearer token. A core-banking
or switch system usually works the other way round: it pushes each payment to a
fraud engine as it is accepted, signs the request with a shared key, retries
until it gets an answer, and wants the verdict back on its own endpoint. That is
this webhook. It takes ISO 20022 pacs.008 credit transfers (in JSON), so a bank
can send what its payment rail already produces.

Code: `backend/src/fraudlens/platform/ingest.py` (signatures, replay,
idempotency, the pacs.008 model), `api/routes/ingest.py`, `platform/callbacks.py`,
`platform/keys.py`. Partner SDK (Python standard library only):
`sdk/python/fraudlens_partner.py`, with `sdk/python/example.py`.

## 1. Endpoints

| Endpoint | Body | Answer |
| --- | --- | --- |
| `POST /v1/ingest/transactions` | pacs.008 with exactly one `CdtTrfTxInf` | `202` queued for the stream |
| `POST /v1/ingest/transactions?wait=decision` | the same | `200` the decision now, the same body as `/v1/score` plus `end_to_end_id` |
| `POST /v1/ingest/transactions/batch` | pacs.008 with 1 to 500 `CdtTrfTxInf` | `202` all queued, in order |

The queued answer:

```json
{"msg_id": "MSG-20260415-0001", "accepted": 2, "duplicates": ["E2E-881"], "callback": true}
```

`duplicates` lists the `EndToEndId`s of transactions whose `TxId` was already
received in the last 7 days. They are not queued again; the rest of the batch is.

The endpoints exist only when `FRAUDLENS_INGEST_KEYRING` names a keyring file;
otherwise they answer `404`.

## 2. Signing a request

Every request carries four headers:

| Header | Value |
| --- | --- |
| `X-FraudLens-Key-Id` | the partner's key id, e.g. `upay-20261007-9ef5` |
| `X-FraudLens-Timestamp` | Unix seconds when the request was signed |
| `X-FraudLens-Nonce` | 16 to 64 of `A-Z a-z 0-9 - _`, new for every request |
| `X-FraudLens-Signature` | `v1=` + hex HMAC-SHA256 of the string below |

What is signed is the timestamp, the nonce, the method, the path with its query,
each followed by a newline, and then the raw body bytes:

```
1776234000\n
4f1c0d6a9b2e47c8a1f3\n
POST\n
/v1/ingest/transactions?wait=decision\n
{"GrpHdr":{...},"CdtTrfTxInf":[...]}
```

The method and path are signed so that a request made for the queue cannot be
replayed as a request for a decision, or at another endpoint. The body is signed
as sent: sign the exact bytes you put on the wire, not a re-serialisation.

The server checks, in this order, and stops at the first failure:

| Check | Refusal |
| --- | --- |
| all four headers present | `401 unsigned` |
| the key id exists, belongs to a partner and is not retired | `401 unknown_key` |
| the timestamp is within ±5 minutes of the server's clock (`FRAUDLENS_INGEST_WINDOW_SECONDS`) | `401 stale_timestamp` |
| the nonce has the right form | `401 bad_nonce` |
| the signature matches (constant-time comparison) | `401 bad_signature` |
| the nonce has not been seen with this key in the last 10 minutes | `409 replayed` |

A refusal names the check that failed, never the value that was expected. The
nonce is remembered in Redis for twice the window; after that the timestamp check
refuses the same request anyway, so a captured request cannot be sent again.

## 3. Retries and idempotency

A retry is a new request: new timestamp, new nonce, new signature. To make it
safe, send `Idempotency-Key` (1 to 128 of `A-Z a-z 0-9 _ . : -`; the SDK uses
the `MsgId`). For 24 hours, per partner:

- the same key with the same body gets the first answer again, with
  `Idempotent-Replayed: true`; refusals such as `409 stale_event` are replayed too;
- the same key with a different body is refused, `422 idempotency_key_reused`;
- while the first request is still being processed, `409 in_progress` with
  `retry_after_seconds`;
- a `5xx` or a malformed body is not remembered, so the retry runs.

Without an idempotency key, the `TxId` check still stops a transaction from being
queued twice; a retried `?wait=decision` for a transaction already decided
answers from the scorer's own duplicate handling.

## 4. The message: pacs.008 to FraudLens

JSON with the ISO 20022 element names, the elements FraudLens uses and nothing
else (unknown elements are refused). One `CdtTrfTxInf` is one transaction.

| FraudLens field | pacs.008 element | Notes |
| --- | --- | --- |
| — | `GrpHdr.MsgId` | 1–35 characters; returned as `msg_id`, the SDK's idempotency key |
| — | `GrpHdr.CreDtTm` | when the message was created |
| — | `GrpHdr.NbOfTxs` | optional; must equal the number of `CdtTrfTxInf` |
| — | `CdtTrfTxInf[].PmtId.EndToEndId` | the bank's reference; echoed in answers and callbacks |
| `txn_id` | `PmtId.TxId` | digits only, below 2^62 |
| `ts` | `AccptncDtTm` | when the payment was accepted, with a time zone |
| `type` | `PmtTpInf.CtgyPurp.Prtry` | `SEND_MONEY`, `CASH_OUT`, `PAYMENT`, … as in `/v1/score` |
| `amount` | `IntrBkSttlmAmt.Amt` | with `IntrBkSttlmAmt.Ccy` = `BDT`; other currencies are refused |
| `sender_id` | `DbtrAcct.Id.Othr.Id` | the wallet or agent number |
| `sender_type` | `DbtrAcct.Id.Othr.SchmeNm.Prtry` | `wallet`, `agent` or `bank` |
| `receiver_id` | `CdtrAcct.Id.Othr.Id` | |
| `receiver_type` | `CdtrAcct.Id.Othr.SchmeNm.Prtry` | `wallet`, `agent`, `merchant`, `telco` or `biller` |
| `sender_balance_before` | `SplmtryData.Envlp.DbtrBalBefore` | |
| `device_id` | `SplmtryData.Envlp.DvcId` | optional |
| `channel` | `SplmtryData.Envlp.Chanl` | `app`, `ussd`, `agent` or `bank` |
| `district` | `SplmtryData.Envlp.Dstrct` | |
| `ip` | `SplmtryData.Envlp.IPAddr` | optional; reduced to its network at once and never stored ([PLATFORM.md](PLATFORM.md) §3) |

pacs.008 has no place for a wallet's balance, the customer's device or the
channel, so those travel in `SplmtryData`, the standard's extension envelope.
Everything ingested is `source: live`.

Validation errors are `422 invalid_request`, and each one names the place in the
message, not the FraudLens field, for example
`["CdtTrfTxInf", 0, "IntrBkSttlmAmt", "Ccy"]`. Every transaction in a batch is
checked before any is queued: a batch is accepted whole or not at all.

A `202` means the transactions are on the stream (`backlog_full`, `503`, when it
is too long). They are decided by the stream worker like `/v1/events`, with the
same ordering and dead-letter rules.

## 5. Decision callback

When the partner has a `callback_url` in the keyring, each transaction it queued
is followed by one `POST` to that URL once it has been decided:

```json
{"txn_id": 8812345678, "end_to_end_id": "E2E-881", "status": "held", "status_reason": null,
 "scored": true,
 "decision": {"tier": "hold", "action": "hold_for_review", "requires_review": true,
              "risk_score": 0.91, "risk_band": "very high", "mode": "...",
              "model_version": "...", "policy_version": "...", "decided_at": "..."}}
```

It is signed exactly like an ingest request, with the partner's newest key, so
the partner verifies it with the same code (`fraudlens_partner.verify`). The
decision's reasons are not sent: like `/v1/score`, the partner learns what to do,
and the reasons stay with the analysts.

- **When.** The job waits for the decision, checking every 2 seconds. A
  transaction the stream could not process (dead-lettered, stale) is reported
  after 10 minutes as `"status": "not_processed"`.
- **Retries.** Any answer other than `2xx`, a timeout (5 s) or a connection error
  is retried after 2, 4, 8 … seconds (±20%, at most 10 minutes), 8 attempts in
  all. Then the job goes to the Redis list `fraudlens:callbacks:dead` for an
  operator.
- **At least once.** The jobs are in a Redis sorted set; whichever API process
  removes a due job delivers it, so there is one attempt at a time. A crash
  between the `POST` and the bookkeeping can repeat a delivery: treat `txn_id` as
  the idempotency key.
- Redirects are not followed. In production the URL must be `https`.

## 6. Keys

The keyring is a JSON file, mode `0600`, outside the repository
(`backend/secrets/` is ignored by git):

```json
{"keys": {"upay-20261007-9ef5": {"secret": "…", "created": "2026-10-07T03:21:34Z", "partner": "upay"}},
 "partners": {"upay": {"callback_url": "https://upay.example/fraudlens/decisions"}}}
```

```bash
make partner-key PARTNER=upay CALLBACK=https://upay.example/fraudlens/decisions
# key id: upay-20261007-9ef5
# secret: 0706…   (shown once; hand it over out of band)
make keys          # ids, owners and dates, never the secrets
cd backend && uv run python -m fraudlens.platform.keys retire <path> upay-20260901-1a2b
```

A new key does not cut the old ones off: they keep verifying for a grace period
(24 hours by default, `--grace-hours`), so the partner can switch over without a
failed request. The newest key signs callbacks. `retire` stops a leaked key at
once. The API re-reads the file when it changes; no restart.

## 7. Try it

```bash
make partner-key PARTNER=upay CALLBACK=http://127.0.0.1:9099/fraudlens
FRAUDLENS_INGEST_KEYRING=secrets/ingest.json make api      # in another terminal
export FRAUDLENS_PARTNER_KEY_ID=upay-… FRAUDLENS_PARTNER_SECRET=…
python sdk/python/example.py --sender W000123 --receiver W000456 --amount 900 \
    --at 2026-04-15T10:00:00+06:00                          # decided now
python sdk/python/example.py ... --queue --listen 9099      # queued; prints the callback
```

`--at` sets `AccptncDtTm`. The demo's clock follows the transactions it is sent
([PLATFORM.md](PLATFORM.md) §14), so use a time inside the replayed period.
Outside production a callback URL may be plain `http`.

Tests: `backend/tests/test_security.py` (signatures, the SDK against the server,
the message mapping) and `backend/tests/test_ingest.py` (bad signature, stale
timestamp, replay, retired key, duplicates, idempotency, batches, callbacks with
retries and the dead list, against Postgres and Redis).
