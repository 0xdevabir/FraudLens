# Plan 07 — Core-banking ingest and security hardening

## Goal

A core-banking system or payment switch can push transactions to FraudLens over
a signed webhook, the way a bank partner would, and get a decision back (in the
response, or later by a signed callback). The service's secrets can come from
files or a secrets store, its signing keys can be rotated without logging
everyone out, it can sit behind TLS, and rate limits see the real client behind
a proxy. A STRIDE threat model says what is protected and what is not.

## Which judge criterion it moves, and why

- **Scalability & integration (6.67/10)**: "without live core banking webhooks".
  The ingest endpoint is that webhook: HMAC-signed, replay-protected,
  idempotent, ISO 20022 pacs.008-shaped, with a partner SDK and a decision
  callback. It feeds the existing Redis stream, so it adds an integration path,
  not a second scoring path.
- **Responsible AI & security (4/5)** and "security hardening still pending":
  key rotation with `kid`, secrets from files, TLS with HSTS, a trusted-proxy
  setting, and docs/SECURITY.md with every threat's mitigation and an honest
  status.

## Steps

1. `platform/keys.py`: keyrings (JSON files) for JWT signing keys and partner
   HMAC keys, re-read when the file changes; a rotation command
   (`python -m fraudlens.platform.keys rotate-jwt|rotate-partner|list|retire`).
2. `platform/security.py`: tokens carry `kid`; the current key signs, any
   unretired key verifies. Without a keyring the single `FRAUDLENS_JWT_SECRET`
   still works as before. Production guard extended.
3. `secrets.py` + a settings source in `config.py`: `FRAUDLENS_<NAME>_FILE`
   reads a setting from a file (Docker/Kubernetes secrets); an optional
   `FRAUDLENS_SECRETS_PROVIDER=module:function` fills what is still unset.
4. `platform/ingest.py` + `api/routes/ingest.py`:
   `POST /v1/ingest/transactions` (one pacs.008-style message, queued, or
   decided synchronously with `?wait=decision`) and
   `POST /v1/ingest/transactions/batch` (up to 500, queued). Checks, in order:
   key id, timestamp within ±5 minutes, HMAC-SHA256 over
   timestamp, nonce, method, path and body; nonce single use in Redis;
   `Idempotency-Key` replays the stored answer; schema; then the stream.
5. `platform/callbacks.py`: optional signed decision callback per partner, from
   a Redis schedule with exponential backoff and a dead list; any API process
   can deliver, a claim makes each delivery happen once.
6. `api/deps.py`: `client_ip` honours `X-Forwarded-For` only from
   `FRAUDLENS_TRUSTED_PROXIES`.
7. `deploy/Caddyfile` + a `tls` profile in docker-compose: HTTPS, HSTS, the API
   and console behind one origin, the proxy on a fixed address the API trusts.
8. `sdk/python/fraudlens_partner.py` (standard library only) and an example.
9. Tests: bad signature, unknown key, stale timestamp, replayed nonce,
   idempotent retry, conflicting idempotency key, duplicate transaction, schema,
   sync decision, batch, callback with retry; keyrings, JWT rotation, secret
   files, trusted proxies.
10. Docs: docs/INGEST.md (protocol, pacs.008 mapping), docs/SECURITY.md (STRIDE,
    PII and retention), PLATFORM.md "not built" list updated honestly, README.

## Definition of done

- The ingest tests and the existing suite pass against real Postgres and Redis;
  `make lint` is clean.
- A request signed by the SDK is accepted; tampered, stale, replayed and
  unknown-key requests are refused with distinct error codes.
- Rotating the JWT key leaves existing sessions valid until their tokens expire;
  rotating a partner key leaves the old one valid for a stated grace period.
- `docker compose --profile tls config` is valid and the Caddyfile validates.
- SECURITY.md lists every threat with done / partial / not done, and
  PLATFORM.md no longer claims anything that is not built.
