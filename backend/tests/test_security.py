"""Keyrings and rotation, secrets from files, client addresses behind a proxy, ingest signatures.

No database or Redis: the endpoint behaviour is in test_ingest.py.
"""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fraudlens.config import BACKEND_DIR, DEV_JWT_SECRET, Settings
from fraudlens.platform import ingest
from fraudlens.platform.audit import WorkflowError
from fraudlens.platform.keys import Keyring, load_keyring, main, rotate_jwt, rotate_partner
from fraudlens.platform.security import (
    TokenError,
    check_production,
    client_address,
    issue_token,
    read_token,
)
from fraudlens.secret_sources import SecretFilesSource

SDK_PATH = BACKEND_DIR.parent / "sdk" / "python" / "fraudlens_partner.py"


def load_sdk():
    spec = importlib.util.spec_from_file_location("fraudlens_partner", SDK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- keyrings


def test_a_rotated_jwt_key_keeps_verifying_until_its_tokens_expire(tmp_path):
    path = tmp_path / "jwt.json"
    t0 = datetime(2026, 10, 7, 9, tzinfo=UTC)
    first = rotate_jwt(path, ttl_minutes=480, now=t0)
    second = rotate_jwt(path, ttl_minutes=480, now=t0 + timedelta(hours=1))
    ring = Keyring.from_json(path.read_text())
    later = t0 + timedelta(hours=2)
    assert ring.signing(now=later).kid == second.kid
    assert ring.verifying(first.kid, now=later) is not None
    # 480 minutes after the rotation (and a minute's grace), the old key is done.
    gone = t0 + timedelta(hours=1, minutes=482)
    assert ring.verifying(first.kid, now=gone) is None
    assert ring.verifying(second.kid, now=gone) is not None
    # The next rotation prunes it; the file is for the owner only.
    rotate_jwt(path, ttl_minutes=480, now=gone)
    assert first.kid not in Keyring.from_json(path.read_text()).keys
    if os.name != "nt":  # Windows has no group or other permission bits to check
        assert path.stat().st_mode & 0o077 == 0


def test_tokens_carry_the_key_id_and_survive_a_rotation(tmp_path):
    path = tmp_path / "jwt.json"
    settings = Settings(jwt_keyring=path)
    rotate_jwt(path, settings.jwt_ttl_minutes)
    old = issue_token(7, "analyst", settings)
    rotate_jwt(path, settings.jwt_ttl_minutes)
    new = issue_token(7, "analyst", settings)
    import jwt

    assert jwt.get_unverified_header(old)["kid"] != jwt.get_unverified_header(new)["kid"]
    assert read_token(old, settings)["sub"] == read_token(new, settings)["sub"] == "7"
    # Retiring the old key (a leak) refuses its tokens at once, without a restart.
    main(["retire", str(path), jwt.get_unverified_header(old)["kid"]])
    with pytest.raises(TokenError):
        read_token(old, settings)
    assert read_token(new, settings)["sub"] == "7"


def test_without_a_keyring_the_single_secret_still_works():
    settings = Settings(jwt_secret="x" * 40)
    token = issue_token(1, "admin", settings)
    assert read_token(token, settings)["role"] == "admin"
    with pytest.raises(TokenError):
        read_token(token, Settings(jwt_secret="y" * 40))


def test_partner_rotation_has_a_grace_period(tmp_path):
    path = tmp_path / "ingest.json"
    t0 = datetime(2026, 10, 7, tzinfo=UTC)
    old = rotate_partner(path, "upay", timedelta(hours=24), "https://core.example/cb", now=t0)
    new = rotate_partner(path, "upay", timedelta(hours=24), now=t0 + timedelta(days=30))
    ring = load_keyring(path)
    assert ring.signing("upay", now=t0 + timedelta(days=30, hours=1)).kid == new.kid
    assert ring.verifying(old.kid, now=t0 + timedelta(days=30, hours=23)) is not None
    assert ring.verifying(old.kid, now=t0 + timedelta(days=31, hours=1)) is None
    assert ring.callback_url("upay") == "https://core.example/cb"  # kept across rotations
    assert ring.signing(None) is None  # partner keys never sign tokens
    with pytest.raises(ValueError):
        rotate_partner(path, "Not A Name", timedelta(hours=1))


def test_the_production_guard_covers_the_keyrings(tmp_path):
    prod = {
        "environment": "production",
        "database_url": "postgresql+psycopg://fraudlens:s3cret-long@db:5432/fraudlens",
        "redis_url": "redis://:pw@redis:6379/0",
        "cors_origins": ["https://console.example"],
    }
    jwt_path = tmp_path / "jwt.json"
    rotate_jwt(jwt_path, 480)
    check_production(Settings(jwt_keyring=jwt_path, **prod))  # the dev secret is not used
    ring = Keyring.from_json(jwt_path.read_text())
    ring.add("jwt")
    weak = next(iter(ring.keys.values()))
    ring.keys[weak.kid] = type(weak)(weak.kid, DEV_JWT_SECRET[:20], weak.created)
    ring.save(jwt_path)
    with pytest.raises(RuntimeError, match="32"):
        check_production(Settings(jwt_keyring=jwt_path, **prod))
    ingest_path = tmp_path / "ingest.json"
    rotate_partner(ingest_path, "upay", timedelta(hours=1), "http://core.example/cb")
    with pytest.raises(RuntimeError, match="https"):
        check_production(Settings(jwt_secret="k" * 40, ingest_keyring=ingest_path, **prod))
    with pytest.raises(ValueError, match="every address"):
        check_production(Settings(trusted_proxies=["0.0.0.0/0"]))


# ---------------------------------------------------------------- secrets from files


def test_settings_read_secrets_from_files(tmp_path, monkeypatch):
    (tmp_path / "jwt").write_text("from-a-file-" + "z" * 30 + "\n")
    (tmp_path / "origins").write_text('["https://console.example"]')
    monkeypatch.setenv("FRAUDLENS_JWT_SECRET_FILE", str(tmp_path / "jwt"))
    monkeypatch.setenv("FRAUDLENS_CORS_ORIGINS_FILE", str(tmp_path / "origins"))
    settings = Settings()
    assert settings.jwt_secret.get_secret_value() == "from-a-file-" + "z" * 30  # newline gone
    assert settings.cors_origins == ["https://console.example"]
    monkeypatch.setenv("FRAUDLENS_JWT_SECRET", "both")
    with pytest.raises(ValueError, match="not both"):
        Settings()


def provide(names: list[str]) -> dict[str, str]:
    """A secrets provider for the test: knows one secret."""
    return {"seed_password": "from-the-vault-123"} if "seed_password" in names else {}


def test_a_secrets_provider_fills_what_is_unset(monkeypatch):
    monkeypatch.setenv("FRAUDLENS_SECRETS_PROVIDER", f"{__name__}:provide")
    assert Settings().seed_password.get_secret_value() == "from-the-vault-123"
    monkeypatch.setenv("FRAUDLENS_SEED_PASSWORD", "the-environment-wins")
    assert Settings().seed_password.get_secret_value() == "the-environment-wins"
    source = SecretFilesSource(Settings, environ={"FRAUDLENS_SECRETS_PROVIDER": "nonsense"})
    with pytest.raises(ValueError, match="package.module:function"):
        source()


# ---------------------------------------------------------------- client address


@pytest.mark.parametrize(
    ("peer", "forwarded", "expected"),
    [
        ("203.0.113.9", [], "203.0.113.9"),  # no proxy in the way
        ("203.0.113.9", ["1.2.3.4"], "203.0.113.9"),  # not our proxy: the header is ignored
        ("10.0.0.2", ["198.51.100.7"], "198.51.100.7"),  # our proxy says who it was
        # A client that writes its own header gets its own address, not the one it chose.
        ("10.0.0.2", ["6.6.6.6, 198.51.100.7"], "198.51.100.7"),
        ("10.0.0.2", ["6.6.6.6", "198.51.100.7, 10.0.0.3"], "198.51.100.7"),  # two of ours
        ("10.0.0.2", ["garbage, 198.51.100.7"], "198.51.100.7"),
        ("10.0.0.2", ["198.51.100.7, garbage"], "10.0.0.2"),  # broken hop: stop at ours
        ("10.0.0.2", [], "10.0.0.2"),
        ("::ffff:10.0.0.2", ["2001:db8::1"], "2001:db8::1"),
    ],
)
def test_forwarded_for_is_believed_only_from_trusted_proxies(peer, forwarded, expected):
    assert client_address(peer, forwarded, ["10.0.0.0/24"]) == expected
    assert client_address(peer, forwarded, []) == peer  # nothing trusted: the peer


# ---------------------------------------------------------------- signatures


def _ring(secret: str = "s" * 64) -> Keyring:
    ring = Keyring()
    key = ring.add("upay", "upay")
    ring.keys[key.kid] = type(key)(key.kid, secret, key.created, "upay")
    return ring


def test_sdk_and_server_agree_on_the_signature():
    sdk, ring = load_sdk(), _ring()
    key = ring.signing("upay")
    body = b'{"GrpHdr":{}}'
    headers = sdk.signed_headers(key.kid, key.secret, "POST", "/v1/ingest/transactions", body)
    assert ingest.verify(ring, headers, "POST", "/v1/ingest/transactions", body, 300) == key
    # And the other way: a callback signed here is accepted by the partner's SDK.
    back = ingest.signed_headers(key, "POST", "/cb?x=1", body, "n" * 32)
    assert sdk.verify({key.kid: key.secret}, back, "POST", "/cb?x=1", body)
    assert not sdk.verify({key.kid: key.secret}, back, "POST", "/cb?x=2", body)
    assert not sdk.verify({key.kid: "other"}, back, "POST", "/cb?x=1", body)
    seen = set()
    assert sdk.verify({key.kid: key.secret}, back, "POST", "/cb?x=1", body, seen_nonce(seen))
    assert not sdk.verify({key.kid: key.secret}, back, "POST", "/cb?x=1", body, seen_nonce(seen))


def seen_nonce(seen: set):
    def check(nonce: str) -> bool:
        if nonce in seen:
            return True
        seen.add(nonce)
        return False

    return check


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"X-FraudLens-Signature": None}, "unsigned"),
        ({"X-FraudLens-Key-Id": "nobody-20261007-0000"}, "unknown_key"),
        ({"X-FraudLens-Timestamp": "1000"}, "stale_timestamp"),
        ({"X-FraudLens-Timestamp": "+1000"}, "stale_timestamp"),
        ({"X-FraudLens-Nonce": "short"}, "bad_nonce"),
        ({"X-FraudLens-Signature": "v1=" + "0" * 64}, "bad_signature"),
        ({"X-FraudLens-Signature": "md5=abc"}, "bad_signature"),
    ],
)
def test_bad_requests_are_refused_with_the_check_that_failed(change, code):
    sdk, ring = load_sdk(), _ring()
    key = ring.signing("upay")
    body = b"{}"
    headers = sdk.signed_headers(key.kid, key.secret, "POST", "/v1/ingest/transactions", body)
    for name, value in change.items():
        if value is None:
            headers.pop(name)
        else:
            headers[name] = value
    with pytest.raises(WorkflowError) as caught:
        ingest.verify(ring, headers, "POST", "/v1/ingest/transactions", body, 300)
    assert (caught.value.status, caught.value.code) == (401, code)


def test_the_signature_covers_the_body_path_and_time():
    sdk, ring = load_sdk(), _ring()
    key = ring.signing("upay")
    headers = sdk.signed_headers(key.kid, key.secret, "POST", "/v1/ingest/transactions", b"{}")
    for path, body in [
        ("/v1/ingest/transactions", b'{"a":1}'),  # body changed
        ("/v1/ingest/transactions?wait=decision", b"{}"),  # asks for something else
        ("/v1/ingest/transactions/batch", b"{}"),  # another endpoint
    ]:
        with pytest.raises(WorkflowError, match="does not match"):
            ingest.verify(ring, headers, "POST", path, body, 300)
    then = int(headers["X-FraudLens-Timestamp"])
    with pytest.raises(WorkflowError, match="within 300"):
        ingest.verify(ring, headers, "POST", "/v1/ingest/transactions", b"{}", 300, then + 301)
    ingest.verify(ring, headers, "POST", "/v1/ingest/transactions", b"{}", 300, then - 299)


# ---------------------------------------------------------------- pacs.008


def _transfer(**over) -> dict:
    sdk = load_sdk()
    args = {
        "txn_id": 42,
        "end_to_end_id": "E2E-42",
        "accepted_at": datetime(2026, 10, 7, 9, tzinfo=UTC),
        "purpose": "SEND_MONEY",
        "amount": 900.0,
        "debtor": ("W1", "wallet"),
        "creditor": ("W2", "wallet"),
        "channel": "app",
        "district": "Dhaka",
        "debtor_balance_before": 20_000.0,
        "ip": "203.0.113.9",
    }
    return sdk.credit_transfer(**(args | over))


def test_a_pacs008_message_maps_onto_the_platform_event():
    sdk = load_sdk()
    body = json.dumps(sdk.pacs008("MSG-1", [_transfer()])).encode()
    message, [txn] = ingest.parse(body, single=True)
    assert message.GrpHdr.MsgId == "MSG-1"
    assert (txn.txn_id, txn.type, txn.sender_id, txn.receiver_id) == (42, "SEND_MONEY", "W1", "W2")
    assert (txn.amount, txn.sender_balance_before, txn.channel) == (900.0, 20_000.0, "app")
    assert txn.event().txn.network == "203.0.113.0/24"  # the address itself is not kept


def test_pacs008_problems_are_reported_where_the_partner_put_them():
    sdk = load_sdk()
    wrong = _transfer(purpose="CASH_OUT", debtor_balance_before=None)  # a wallet to a wallet
    body = json.dumps(sdk.pacs008("MSG-2", [_transfer(), wrong])).encode()
    with pytest.raises(ingest.IngestInvalid) as caught:
        ingest.parse(body, single=False)
    assert all(e["loc"][:2] == ("CdtTrfTxInf", 1) for e in caught.value.errors)
    with pytest.raises(ingest.IngestInvalid) as caught:
        ingest.parse(body, single=True)
    assert caught.value.errors[0]["loc"] == ("CdtTrfTxInf",)
    unknown = json.loads(body)
    unknown["CdtTrfTxInf"][0]["Surprise"] = 1
    with pytest.raises(ValueError):
        ingest.parse(json.dumps(unknown).encode(), single=False)


def test_the_example_and_the_sdk_have_no_third_party_imports():
    for path in SDK_PATH.parent.glob("*.py"):
        text = Path(path).read_text()
        assert "import httpx" not in text and "import requests" not in text
