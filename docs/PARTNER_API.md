# Partner integration guide

For the team connecting a wallet app or payment switch to FraudLens. The platform
itself is described in [PLATFORM.md](PLATFORM.md); this is the part a partner needs.
All data served by the demo is synthetic.

## 1. Get a key

A supervisor or admin makes a key in the console (**Partner API keys**) or with
`POST /v1/api-keys`:

```json
{"name": "Acme Wallet", "scopes": ["score", "events"], "sandbox": true,
 "rate_per_minute": 600, "daily_quota": 100000}
```

The response has the key once, as `flk_<8 hex>_<secret>`. Only a hash is kept. Send it
on every call:

```
X-API-Key: flk_3fa91c20_...
```

| Scope | Allows |
| --- | --- |
| `score` | `POST /v1/score` |
| `events` | `POST /v1/events` |

A key cannot call anything else. Over the rate: `429 rate_limited` with
`Retry-After`. Over the day's quota: `429 quota_exceeded` with `Retry-After` set to
the time until midnight UTC. A refused request is counted as an error, not as usage.
A revoked or wrong key is `401 unauthenticated`, always the same answer.

## 2. Sandbox

A sandbox key never touches real state: no transaction is stored, no case is opened,
no webhook or SMS is sent. `POST /v1/score` answers by the cents of the amount:

| Amount ends in | Tier | `status` |
| --- | --- | --- |
| `.01` | allow | `completed` |
| `.02` | warn | `pending_customer` |
| `.03` | step_up | `pending_customer` |
| `.04` | hold | `held` |
| anything else | allow | `completed` |

The response has the same shape as a real one, with `"sandbox": true` and
`decision.mode: "sandbox"`, and the policy's fixed Bangla and English texts in
`decision.customer_message`. `POST /v1/events` returns `{"accepted": n, "sandbox": true}`
and queues nothing.

```bash
curl -s https://fraudlens.example/v1/score \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"txn_id": 1, "ts": "2026-10-04T10:00:00Z", "type": "SEND_MONEY",
       "sender_id": "W1", "sender_type": "wallet", "receiver_id": "W2",
       "receiver_type": "wallet", "amount": 5000.04, "channel": "app",
       "district": "Dhaka"}'
```

## 3. Webhooks

Register an endpoint (**Webhooks**, or `POST /v1/webhooks`). The URL must be public
https; private, loopback and link-local addresses are refused, when registering and
again at every send. The signing secret (`whsec_…`) is in the response once.

| Event | When |
| --- | --- |
| `decision.warn`, `decision.step_up`, `decision.hold` | a live payment was interrupted |
| `case.verdict` | a reviewer closed a case |
| `freeze.approved` | a second person approved a wallet freeze |

Replays and history loads never notify. A delivery looks like:

```
POST /your/endpoint
X-FraudLens-Event: decision.hold
X-FraudLens-Delivery: 6f1c0d...        # the event id, the same on every retry
X-FraudLens-Signature: t=1791118531,v1=9b3c...

{"id":"6f1c0d...","type":"decision.hold","created_at":"...",
 "data":{"txn_id":123,"tier":"hold","status":"held","case_id":45,"sender_id":"W1"}}
```

Payloads carry identifiers only. Fetch anything else from the API.

**Verify the signature** (constant-time, reject old timestamps):

```python
import hashlib, hmac, time

def verify(secret: str, header: str, body: bytes, tolerance: int = 300) -> bool:
    parts = dict(item.split("=", 1) for item in header.split(","))
    t = int(parts["t"])
    if abs(time.time() - t) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"t={t},v1={expected}", header)
```

**Answer with any 2xx.** Anything else is retried after 30 s, 60 s, 2 min, … (capped
at an hour), six attempts in all, then the delivery is *dead* and appears in the
delivery log, where a supervisor can send it again. Because retries and replays reuse
`X-FraudLens-Delivery`, **de-duplicate on that id**. An endpoint that fails 30
deliveries in a row is switched off. Rotating the secret ends the old one at once.

## 4. Customer SMS

With `FRAUDLENS_NOTIFY_ADAPTER=http`, a step-up or hold on a live payment sends the
sender the policy's fixed warning text, in Bangla and English, through your SMS
gateway: `POST` of `{"wallet_id", "text_bn", "text_en", "event"}` with
`Authorization: Bearer <FRAUDLENS_SMS_GATEWAY_TOKEN>`. `console` logs instead of
sending. A warning is shown in the app, not texted. Nothing else is in the message.

## 5. Scam reports and tracking

`POST /v1/customer/reports` (service account) opens a case and returns a `reference`
such as `FL-7K3Q-9XAM`. Show it to the customer. They can follow it, with no account,
at the console's `/track` page, or with:

```
POST /v1/public/reports/code    {"reference": "FL-7K3Q-9XAM"}     -> 202 {"sent": true}
POST /v1/public/reports/status  {"reference": "...", "code": "123456"}
```

A six-digit code is sent to the wallet that made the report (through the SMS
adapter). Status is one of `received` (nobody has picked it up), `investigating`,
`action_taken` (confirmed fraud, and the wallet was acted on) or `closed` (could not
confirm), each with fixed Bangla and English wording. The first call answers the same
whether or not the reference exists; a code lasts ten minutes and five wrong tries
burn it; both calls are limited per address, and the first per reference. Nothing
about the wallet, the analysts' notes or the model is returned.
