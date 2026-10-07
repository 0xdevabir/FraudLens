"""The signed ingest webhook, decision callbacks and token-key rotation, on Postgres and Redis.

Needs the containers from docker-compose (`make up`). Uses its own database
(`fraudlens_test_ingest`) and Redis database 14.
"""

from __future__ import annotations

import itertools
import json
import shutil
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from test_security import load_sdk

from fraudlens.api import create_app
from fraudlens.config import Settings
from fraudlens.decision import evaluate, insights
from fraudlens.platform import callbacks
from fraudlens.platform.db import make_engine, make_sessions, migrate
from fraudlens.platform.events import moment
from fraudlens.platform.keys import load_keyring, main, rotate_jwt, rotate_partner
from fraudlens.platform.load import load
from fraudlens.platform.models import Transaction
from fraudlens.platform.seed import seed_users

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-staple"
TEST_DB = "fraudlens_test_ingest"
PATH = "/v1/ingest/transactions"
CALLBACK = "http://partner.test/fraudlens/decisions?v=1"


@pytest.fixture(scope="module")
def keyrings(tmp_path_factory):
    root = tmp_path_factory.mktemp("keys")
    rotate_jwt(root / "jwt.json", 480)
    rotate_partner(root / "ingest.json", "upay", timedelta(hours=24), CALLBACK)
    rotate_partner(root / "ingest.json", "quietbank", timedelta(hours=24))  # no callback
    return root


@pytest.fixture(scope="module")
def settings(trained, tmp_path_factory, keyrings):
    data_dir, shared_root, _ = trained
    base = Settings()
    admin_url = make_url(base.database_url)
    redis_url = base.redis_url.rsplit("/", 1)[0] + "/14"
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
            connection.execute(text(f"CREATE DATABASE {TEST_DB}"))
        admin.dispose()
        Redis.from_url(redis_url).flushdb()
    except Exception as exc:  # the containers are not running
        pytest.skip(f"Postgres/Redis not reachable: {type(exc).__name__}")
    models_root = tmp_path_factory.mktemp("ingest") / "models"
    shutil.copytree(shared_root, models_root)
    evaluate.run(data_dir, models_root=models_root)
    insights.run(data_dir, models_root=models_root)
    return Settings(
        data_dir=data_dir.parent,
        dataset=data_dir.name,
        models_root=models_root,
        database_url=admin_url.set(database=TEST_DB).render_as_string(hide_password=False),
        redis_url=redis_url,
        events_stream="fraudlens-ingest-test:events",
        alerts_channel="fraudlens-ingest-test:alerts",
        run_worker=False,
        seed_password=PASSWORD,
        llm_notes=False,
        jwt_keyring=keyrings / "jwt.json",
        ingest_keyring=keyrings / "ingest.json",
    )


@pytest.fixture(scope="module")
def client(settings):
    migrate(settings.database_url)
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    with make_sessions(engine)() as session:
        seed_users(session, settings)
    load(engine, redis, settings, reset=True)
    engine.dispose()
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def p(client):
    return client.app.state.platform


@pytest.fixture(scope="module")
def sdk():
    return load_sdk()


@pytest.fixture(scope="module")
def wallets(p):
    engine = p.scorer.engine
    pool = iter(
        w for w, state in sorted(engine.wallets.items()) if w not in engine.flagged and state.home
    )
    return lambda: next(pool)


_ids = itertools.count(9_100_000_000)


def transfer(sdk, p, sender: str, receiver: str, amount: float = 900.0, **over) -> dict:
    txn_id = next(_ids)
    state = p.scorer.engine.wallets[sender]
    args = {
        "txn_id": txn_id,
        "end_to_end_id": f"E2E-{txn_id}",
        "accepted_at": moment(p.scorer.clock.now()),
        "purpose": "SEND_MONEY",
        "amount": amount,
        "debtor": (sender, "wallet"),
        "creditor": (receiver, "wallet"),
        "channel": "app",
        "district": state.home,
        "debtor_balance_before": 20_000.0,
        "device_id": next(iter(state.devices), ""),
    }
    return sdk.credit_transfer(**(args | over))


def key_of(settings, partner: str = "upay"):
    return load_keyring(settings.ingest_keyring).signing(partner)


def post(client, sdk, settings, message, path=PATH, partner="upay", idem=None, **header_over):
    body = json.dumps(message).encode()
    key = key_of(settings, partner)
    headers = sdk.signed_headers(key.kid, key.secret, "POST", path, body)
    if idem:
        headers["Idempotency-Key"] = idem
    headers.update(header_over)
    return client.post(path, content=body, headers=headers), headers, body


def code(response) -> str:
    return response.json()["error"]["code"]


# ---------------------------------------------------------------- authentication


def test_unsigned_and_badly_signed_requests_are_refused(client, sdk, settings, p, wallets):
    message = sdk.pacs008("MSG-A", [transfer(sdk, p, wallets(), wallets())])
    body = json.dumps(message).encode()
    assert code(client.post(PATH, content=body)) == "unsigned"
    response, headers, _ = post(client, sdk, settings, message)
    assert response.status_code == 202, response.text
    # The same signature on a body with one figure changed.
    tampered = body.replace(b'"Amt": 900.0', b'"Amt": 9000.0')
    assert tampered != body
    forged = client.post(PATH, content=tampered, headers=headers | {"X-FraudLens-Nonce": "n" * 20})
    assert (forged.status_code, code(forged)) == (401, "bad_signature")
    # Signed for the queue, sent asking for a decision.
    wrong_path = client.post(PATH + "?wait=decision", content=body, headers=headers)
    assert code(wrong_path) == "bad_signature"


def test_a_stale_timestamp_is_refused(client, sdk, settings, p, wallets):
    message = sdk.pacs008("MSG-B", [transfer(sdk, p, wallets(), wallets())])
    body = json.dumps(message).encode()
    key = key_of(settings)
    import time

    for skew in (-301, 301):
        headers = sdk.signed_headers(key.kid, key.secret, "POST", PATH, body, time.time() + skew)
        response = client.post(PATH, content=body, headers=headers)
        assert (response.status_code, code(response)) == (401, "stale_timestamp")


def test_a_replayed_request_is_refused(client, sdk, settings, p, wallets):
    message = sdk.pacs008("MSG-C", [transfer(sdk, p, wallets(), wallets())])
    first, headers, body = post(client, sdk, settings, message)
    assert first.status_code == 202
    again = client.post(PATH, content=body, headers=headers)
    assert (again.status_code, code(again)) == (409, "replayed")


def test_a_retired_or_unknown_key_is_refused(client, sdk, settings, p, wallets):
    old = key_of(settings, "quietbank")
    rotate_partner(settings.ingest_keyring, "quietbank", timedelta(hours=1))
    message = sdk.pacs008("MSG-D", [transfer(sdk, p, wallets(), wallets())])
    body = json.dumps(message).encode()
    # During the grace period the old key still works.
    headers = sdk.signed_headers(old.kid, old.secret, "POST", PATH, body)
    assert client.post(PATH, content=body, headers=headers).status_code == 202
    main(["retire", str(settings.ingest_keyring), old.kid])
    headers = sdk.signed_headers(old.kid, old.secret, "POST", PATH, body)
    response = client.post(PATH, content=body, headers=headers)
    assert (response.status_code, code(response)) == (401, "unknown_key")


# ---------------------------------------------------------------- what comes in


def test_a_decision_can_be_had_synchronously(client, sdk, settings, p, wallets):
    message = sdk.pacs008("MSG-E", [transfer(sdk, p, wallets(), wallets())])
    response, _, _ = post(client, sdk, settings, message, path=PATH + "?wait=decision")
    assert response.status_code == 200, response.text
    answer = response.json()
    txn = message["CdtTrfTxInf"][0]
    assert answer["end_to_end_id"] == txn["PmtId"]["EndToEndId"]
    assert answer["txn_id"] == int(txn["PmtId"]["TxId"]) and answer["scored"]
    assert answer["decision"]["tier"] in {"allow", "warn", "step_up", "hold"}
    # Queued again under another message: recognised, not scored twice.
    again = sdk.pacs008("MSG-E2", message["CdtTrfTxInf"])
    response, _, _ = post(client, sdk, settings, again)
    assert response.json()["accepted"] == 0
    assert response.json()["duplicates"] == [txn["PmtId"]["EndToEndId"]]


def test_an_idempotency_key_returns_the_first_answer(client, sdk, settings, p, wallets):
    message = sdk.pacs008("MSG-F", [transfer(sdk, p, wallets(), wallets())])
    path = PATH + "?wait=decision"
    first, _, _ = post(client, sdk, settings, message, path=path, idem="retry-F")
    retry, _, _ = post(client, sdk, settings, message, path=path, idem="retry-F")  # new nonce
    assert first.status_code == retry.status_code == 200
    assert retry.json() == first.json() and not retry.json()["duplicate"]
    assert retry.headers["Idempotent-Replayed"] == "true"
    other = sdk.pacs008("MSG-F2", [transfer(sdk, p, wallets(), wallets())])
    reused, _, _ = post(client, sdk, settings, other, path=path, idem="retry-F")
    assert (reused.status_code, code(reused)) == (422, "idempotency_key_reused")


def test_a_batch_is_queued_in_order_and_scored_by_the_stream(client, sdk, settings, p, wallets):
    transfers = [transfer(sdk, p, wallets(), wallets()) for _ in range(3)]
    transfers[1]["PmtTpInf"]["CtgyPurp"]["Prtry"] = "CASH_IN"  # an agent pays a wallet
    transfers[1]["DbtrAcct"]["Id"]["Othr"] = {"Id": "AG_NOBODY", "SchmeNm": {"Prtry": "agent"}}
    transfers[1]["SplmtryData"]["Envlp"]["Chanl"] = "agent"
    message = sdk.pacs008("MSG-G", transfers)
    single, _, _ = post(client, sdk, settings, message)
    assert code(single) == "invalid_request"  # one endpoint, one transaction
    response, _, _ = post(client, sdk, settings, message, path=PATH + "/batch")
    assert response.status_code == 202, response.text
    assert response.json()["accepted"] == 3 and response.json()["callback"]
    p.worker.drain()
    with p.sessions() as s:
        stored = [s.get(Transaction, int(t["PmtId"]["TxId"])) for t in transfers]
    assert all(t is not None and t.source == "live" for t in stored)


def test_schema_problems_name_the_iso_element(client, sdk, settings, p, wallets):
    bad = transfer(sdk, p, wallets(), wallets())
    bad["IntrBkSttlmAmt"] = {"Ccy": "USD", "Amt": -5}
    wrong_kind = transfer(sdk, p, wallets(), wallets())
    wrong_kind["CdtrAcct"]["Id"]["Othr"]["SchmeNm"]["Prtry"] = "agent"  # SEND_MONEY to an agent
    for message, where in [
        (sdk.pacs008("MSG-H", [bad]), "CdtTrfTxInf.0.IntrBkSttlmAmt"),
        (sdk.pacs008("MSG-I", [wrong_kind]), "CdtTrfTxInf.0"),
    ]:
        response, _, _ = post(client, sdk, settings, message)
        assert response.status_code == 422
        fields = [f["field"] for f in response.json()["error"]["fields"]]
        assert all(f.startswith(where) for f in fields), fields
        assert "-5" not in response.text  # the value sent is not repeated


# ---------------------------------------------------------------- callbacks


def test_the_decision_is_called_back_signed_with_retries(client, sdk, settings, p, wallets):
    received, statuses = [], iter([503, 204])

    def fake_post(url: str, headers: dict, body: bytes) -> int:
        received.append((url, headers, body))
        return next(statuses)

    dispatcher = callbacks.Dispatcher(settings, p.sessions, p.redis, post=fake_post)
    p.redis.delete(callbacks.SCHEDULE)
    message = sdk.pacs008("MSG-J", [transfer(sdk, p, wallets(), wallets())])
    response, _, _ = post(client, sdk, settings, message)
    assert response.status_code == 202
    import time

    now = time.time()
    # Not scored yet: the job waits and nothing is sent.
    assert dispatcher.run_once(now + 3) == 1 and received == []
    p.worker.drain()
    assert dispatcher.run_once(now + 6) == 1 and len(received) == 1  # 503: retried later
    [(member, due)] = p.redis.zrange(callbacks.SCHEDULE, 0, -1, withscores=True)
    assert json.loads(member)["try"] == 1 and now + 6 + 1.5 <= due <= now + 6 + 5
    assert dispatcher.run_once(now + 6) == 0  # not due yet
    assert dispatcher.run_once(due) == 1 and len(received) == 2
    assert p.redis.zcard(callbacks.SCHEDULE) == 0 and dispatcher.delivered == 1

    url, headers, body = received[-1]
    assert url == CALLBACK
    key = key_of(settings)
    assert sdk.verify({key.kid: key.secret}, headers, "POST", "/fraudlens/decisions?v=1", body)
    payload = json.loads(body)
    txn = message["CdtTrfTxInf"][0]
    assert payload["end_to_end_id"] == txn["PmtId"]["EndToEndId"]
    assert payload["decision"]["tier"] in {"allow", "warn", "step_up", "hold"}
    assert "reasons" not in payload["decision"]  # what to do, not why


def test_a_callback_that_keeps_failing_ends_in_the_dead_list(client, sdk, settings, p):
    dispatcher = callbacks.Dispatcher(settings, p.sessions, p.redis, post=lambda *a: 500)
    p.redis.delete(callbacks.SCHEDULE, callbacks.DEAD)
    callbacks.schedule(p.redis, "upay", 2**61, "E2E-NEVER", now=0.0)  # never arrives
    now = callbacks.DECISION_WAIT + 10  # long enough: reported as not processed
    for _ in range(callbacks.MAX_ATTEMPTS):
        now += callbacks.MAX_DELAY * 1.3
        assert dispatcher.run_once(now) == 1
    assert p.redis.zcard(callbacks.SCHEDULE) == 0
    [dead] = p.redis.lrange(callbacks.DEAD, 0, -1)
    assert json.loads(dead)["payload"]["status"] == "not_processed"


# ---------------------------------------------------------------- token keys


def test_sessions_survive_a_signing_key_rotation(client, settings):
    login = client.post("/v1/auth/login", json={"username": "analyst1", "password": PASSWORD})
    assert login.status_code == 200, login.text
    old = {"Authorization": f"Bearer {login.json()['access_token']}"}
    import jwt

    old_kid = jwt.get_unverified_header(login.json()["access_token"])["kid"]
    rotate_jwt(settings.jwt_keyring, settings.jwt_ttl_minutes)  # no restart
    again = client.post("/v1/auth/login", json={"username": "analyst1", "password": PASSWORD})
    assert jwt.get_unverified_header(again.json()["access_token"])["kid"] != old_kid
    assert client.get("/v1/auth/me", headers=old).status_code == 200
    main(["retire", str(settings.jwt_keyring), old_kid])
    assert client.get("/v1/auth/me", headers=old).status_code == 401
