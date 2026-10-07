# Security — threat model, personal data and retention

What FraudLens protects, from whom, and how far each protection goes. Each
threat is marked **done** (built and tested), **partial** (built, with a gap
named) or **not done**. The controls themselves are described in
[PLATFORM.md](PLATFORM.md) §7 and [INGEST.md](INGEST.md).

## 1. Assets

| Asset | Where | Why it matters |
| --- | --- | --- |
| Transactions and decisions | Postgres `transactions`, `decisions` | Customers' payments; a changed decision moves or holds money |
| Wallet state (frozen, flagged) | Postgres `wallets`, `freeze_requests`, `wallet_flags` | A freeze stops a customer's money; an unfreeze releases a mule's |
| Cases, notes and verdicts | Postgres `cases`, `case_events` | Evidence, and the labels the next model is trained on |
| Customer scam reports | Postgres `customer_reports` | Free text a customer wrote, possibly with names and numbers |
| Audit log | Postgres `audit_log` (append-only) | The record of who saw and did what |
| Token signing keys | `FRAUDLENS_JWT_SECRET` or the JWT keyring | Whoever has one can be any user |
| Partner HMAC keys | the ingest keyring | Whoever has one can inject transactions as that bank |
| Database and Redis credentials | environment or secret files | Direct access to everything above |
| Models and the decision policy | `backend/artifacts/`, `decision/policies/` | Changing them changes every decision |
| Feature state | the scorer's memory, snapshots in Postgres | Wrong state, wrong scores |

## 2. Trust boundaries

```
 customer ──► payment app/upay ──(bearer token: service)──────────┐
                                                                  │
 core banking ──(HMAC-signed pacs.008, §INGEST)───────────────────┤
                                                                  ▼
 analyst's browser ──(HTTPS, Caddy)──► console ──(bearer token)──► API ──► Postgres
                                                                  │  └───► Redis (stream, revocations, nonces)
                                                                  │
                                    partner callback URL ◄──(HMAC-signed POST)
                                    language model (optional) ◄──(masked evidence)
```

1. **Internet to the edge.** Browsers and partners reach Caddy over TLS
   (`make tls`). Caddy replaces any `X-Forwarded-For` a client sends.
2. **Edge to the API.** A private Docker network; the API believes forwarded
   addresses only from `FRAUDLENS_TRUSTED_PROXIES`.
3. **Caller to the API.** Every request is authenticated: a bearer token with a
   role, or a partner's HMAC signature on the ingest endpoints. Nothing else.
4. **API to its stores.** Postgres and Redis on the private network, with
   passwords; their ports are bound to 127.0.0.1 on the host.
5. **API outwards.** Decision callbacks to partner URLs set by an operator, and
   (off by default) case-note wording from a language model.

## 3. Threats (STRIDE)

### Spoofing

| Threat | Mitigation | Status |
| --- | --- | --- |
| Guessing an analyst's password | Argon2id; five failures in five minutes lock the username from that address; a per-address limit | done |
| A forged or altered token | HS256 with a key of at least 32 characters (production guard); `kid` must name a known, unretired key | done |
| A stolen token | 8-hour expiry, sign-out revokes it in Redis (fails closed), the user is re-read on every request | partial: no binding to a device, no refresh-token rotation |
| A forged ingest request | HMAC-SHA256 over timestamp, nonce, method, path and body; constant-time comparison | done (`test_ingest.py`) |
| Someone posing as FraudLens to a partner's callback endpoint | Callbacks are signed with the partner's own key, verified by the SDK | done |
| Spoofing the client address to dodge the lockout | `X-Forwarded-For` is read only from configured proxies, right to left; `0.0.0.0/0` is refused | done (`test_security.py`) |
| The demo sign-in left on | Off by default, refused in production, every use audited | done |

### Tampering

| Threat | Mitigation | Status |
| --- | --- | --- |
| A request changed in transit | TLS at the edge; ingest bodies are signed whatever the transport | partial: TLS ends at the proxy, the private hops are plain |
| Rewriting the audit trail | A trigger refuses `UPDATE`, `DELETE`, `TRUNCATE` on `audit_log` | partial: a database superuser can drop the trigger; no external copy |
| Changing a decision or a freeze | Roles; freezes need a second person (§5 of PLATFORM.md); every step audited | done |
| Injection through input | Every body validated, unknown fields refused, identifiers match a pattern, SQL parameterised, 1 MB body cap (2 MB at the proxy) | done |
| Poisoning the models through analyst verdicts | Retraining registers a challenger only; promotion is a separate, manual step with a shadow comparison | partial: no automatic check for a run of suspicious verdicts |
| Swapping the model or policy files | Loaded once at start-up from the artifacts directory and the image | not done: no signature on artifacts |

### Repudiation

| Threat | Mitigation | Status |
| --- | --- | --- |
| "I never froze that wallet" | Every case action, freeze step, flag, login and profile view is in the append-only audit log with the user and request id | done |
| "I never looked at that number" | A full identifier is shown only after `POST /audit/reveals` | partial: the API returns full numbers to reviewers (PLATFORM.md §10) |
| "The bank never sent that payment" | Only a holder of the partner's key could have signed it | partial: the transaction is stored, but not the key id, `MsgId` or signature it came with |

### Information disclosure

| Threat | Mitigation | Status |
| --- | --- | --- |
| Reading data with too broad a role | `admin` sees metrics and audit, not customer data; `service` scores but cannot read alerts or cases | done |
| Customer IP addresses | Reduced to the /24 or /48 at the edge; the address is never stored, logged or returned | done |
| Numbers and e-mails in scam reports | Masked on the way out | partial: pattern-based, names in words get through |
| Scam messages a customer checks | Classified and discarded; not stored | done |
| Evidence sent to a language model | Off by default; identifiers masked before it leaves | done |
| Error messages revealing internals | One error shape; validation errors never echo the value; signature refusals never show the expected value | done |
| Secrets in the repository or the image | `.env` and `backend/secrets/` ignored by git; `*_FILE` variables read Docker/Kubernetes secrets; keyrings written with mode 0600 | done |
| Secrets in the environment of the process | `*_FILE` and a provider hook avoid it | partial: only a directory provider ships, not a vault client |
| Reasons for a decision leaking to a fraudster | `/v1/score` and callbacks return the action, not the reasons | done |
| A captured backup | — | not done: no encryption at rest beyond the disk's own |

### Denial of service

| Threat | Mitigation | Status |
| --- | --- | --- |
| Flooding the login or customer endpoints | Per-address and per-wallet rate limits in Redis | done |
| Flooding the stream | `503 backlog_full` above the backlog limit; batches capped at 500 | done |
| Replaying a signed ingest request | Nonce kept in Redis for twice the ±5 minute window; `TxId` deduplicated for 7 days | done |
| Large bodies | 2 MB at the proxy, 1 MB in the API | done |
| A partner's slow or dead callback endpoint | 5 s timeout, backoff, 8 attempts, then a dead list; delivery is off the request path | done |
| Redis down | Token checks fail closed (`503`); scoring needs it for the stream | partial: no Redis replica |
| One scorer process | — | see PLATFORM.md §10 |

### Elevation of privilege

| Threat | Mitigation | Status |
| --- | --- | --- |
| A service account reading cases | A role check on every route | done |
| An analyst approving their own freeze | Two-person rule in the database workflow | done |
| A partner key used for other partners or the console | Partner keys are accepted only on `/v1/ingest`, and JWT keys only for tokens | done |
| A leaked key staying valid | `keys retire` cuts one off at once; rotation keeps old keys only for a grace period | partial: rotation is manual, not scheduled |
| Server-side request forgery via callbacks | Callback URLs are set by operators in the keyring, `https` only in production, redirects not followed | partial: no check of where the name resolves |

## 4. Personal data

| Data | Kept | Shown to |
| --- | --- | --- |
| Wallet, agent and device numbers | In full, in transactions and cases | Analysts and supervisors (masked on screen until a recorded reveal) |
| Amounts, times, districts, channels | In full | Analysts and supervisors |
| Customer IP address | Only its network (/24, /48) | Nobody: used by the policy only |
| Scam-report text | As written | Reviewers, with numbers and e-mails masked |
| Scam messages checked by a customer | Not kept | — |
| Names | Not collected | — |
| Analysts' accounts | Username, role, Argon2id hash | Admins |

Nothing personal goes to the language model unmasked, and nothing goes to it at
all unless it is switched on.

## 5. Retention

The policy below is what FraudLens is designed for. **Enforcing it is not built**:
there is no scheduled job that deletes or anonymises rows. The Redis entries are
the exception: they expire by themselves.

| Data | Kept for | Then |
| --- | --- | --- |
| Transactions and decisions | 5 years (the Money Laundering Prevention Act 2012 asks for at least 5 years of transaction records) | Wallet and device numbers replaced by a keyed hash; amounts and times kept for models |
| Cases, case events, verdicts, freeze requests | 5 years after the case is closed | As above |
| Scam reports | 2 years | Text deleted, category kept |
| Audit log | 5 years | Exported and archived, then deleted |
| Redis: ingest nonces | 10 minutes | Expire |
| Redis: idempotency answers | 24 hours | Expire |
| Redis: seen `TxId`s | 7 days | Expire |
| Redis: revoked tokens | Until the token would have expired | Expire |
| Redis: callback dead list | Last 10,000 | Trimmed |
| Model training data | The synthetic dataset; no real customer data is used | — |

A customer who asks for their data gets what is held under their wallet number;
deleting it before the period ends is refused where the law requires keeping it.

## 6. Not done, in order of what to build next

1. TLS on the private hops (API to Postgres and Redis), or a service mesh.
2. A secrets-manager client behind the provider hook, and scheduled rotation.
3. A retention job implementing §5.
4. Signed model and policy artifacts, checked at start-up.
5. The audit log shipped to write-once storage outside the database.
6. Encrypted backups.
