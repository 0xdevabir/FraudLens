"""The consortium protocol: privacy, integrity, reversibility and the audit trail."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fraudlens.consortium import crypto
from fraudlens.consortium.hub import DISPUTE_SLA, Hub, Member
from fraudlens.consortium.protocol import (
    Bundle,
    ProtocolError,
    canonical,
    group_element,
    normalise,
    signal,
)

T0 = datetime(2026, 4, 1, tzinfo=UTC)
MULE = "01712345678"
MULE_PHONE = "8801712345678"
CUSTOMER = "01898765432"


@pytest.fixture(scope="module")
def keys():
    # Key generation is cheap but signing keys are reused to keep the module quick.
    return {name: crypto.SigningKey.generate() for name in ("bkash", "nagad", "rocket")}


@pytest.fixture
def world(keys):
    hub = Hub("2026Q2", T0)
    members = {}
    for name, key in keys.items():
        members[name] = Member(name, hub, key)
        hub.register(name, name.title(), key.public, T0)
    return hub, members


def test_group_prime_is_the_rfc_3526_group():
    assert crypto.P.bit_length() == 2048
    assert pow(crypto.G, crypto.Q, crypto.P) == 1  # g generates the order-q subgroup


def test_identifiers_are_normalised_before_hashing():
    assert normalise("msisdn", "+880 1712-345678") == MULE_PHONE
    assert normalise("msisdn", MULE) == normalise("msisdn", MULE_PHONE)
    with pytest.raises(ProtocolError):
        normalise("msisdn", "0123")
    with pytest.raises(ProtocolError):
        normalise("email", "a@b.c")


def test_blinded_oprf_equals_the_keyed_function_and_hides_the_input():
    key = crypto.OprfKey.generate("e1")
    element = group_element("msisdn", MULE)
    blinded, r = crypto.blind(element)
    assert blinded != element  # the hub sees a random group element, not the number's hash
    expected = pow(element, key.secret, crypto.P)
    assert crypto.unblind(key.evaluate(blinded), r, key.public) == expected
    # Blinding is fresh every time: two requests for one number are unlinkable.
    assert crypto.blind(element)[0] != crypto.blind(element)[0]


def test_tokens_depend_on_the_hub_key(world):
    hub, m = world
    a = m["bkash"].tokenize([("msisdn", MULE)], T0)[0]
    b = m["nagad"].tokenize([("msisdn", "+8801712345678")], T0)[0]
    assert a == b  # two providers, one SIM, one token
    other = Member("x", Hub("2026Q2", T0), crypto.SigningKey.generate())
    other.hub.register("x", "X", other.key.public, T0)
    assert other.tokenize([("msisdn", MULE)], T0)[0] != a  # no key, no matching token
    assert MULE_PHONE not in a and len(a) == 32


def test_quota_stops_enumeration(world):
    hub, m = world
    hub.members["rocket"].quota_per_day = 3
    m["rocket"].tokenize([("msisdn", f"0171000000{i}") for i in range(3)], T0)
    with pytest.raises(ProtocolError, match="quota"):
        m["rocket"].tokenize([("msisdn", "01710000009")], T0)
    assert hub.audit.entries[-1]["event"] == "oprf.refused"


def test_signatures_reject_forgery_and_tampering(keys):
    msg = canonical({"a": 1})
    sig = keys["bkash"].sign(msg)
    assert crypto.verify(keys["bkash"].public, msg, sig)
    assert not crypto.verify(keys["nagad"].public, msg, sig)
    assert not crypto.verify(keys["bkash"].public, canonical({"a": 2}), sig)
    assert not crypto.verify(keys["bkash"].public, msg, "zz.zz")


def test_bloom_filter_has_no_false_negatives():
    bloom = crypto.BloomFilter.sized(500, 1e-4)
    for i in range(500):
        bloom.add(f"t{i}")
    assert all(f"t{i}" in bloom for i in range(500))
    false = sum(f"u{i}" in bloom for i in range(20_000))
    assert false <= 20  # expected about 2
    again = crypto.BloomFilter.from_dict(bloom.to_dict())
    assert "t7" in again and again.n == 500


def test_share_import_lookup_end_to_end(world):
    hub, m = world
    bkash, nagad = m["bkash"], m["nagad"]
    bkash.list_wallet([("msisdn", MULE), ("device", "handset-77")], "confirmed", 0.9, T0,
                      typology="impersonation")  # fmt: skip
    bkash.list_wallet([("msisdn", "01911111111")], "suspected", 0.6, T0)
    receipt = bkash.publish(T0, watch_confidence=0.6)
    assert receipt["listings"] == 2 and receipt["watch"] == 1

    bundle = hub.bundles["bkash"].to_dict()
    text = str(bundle)
    assert MULE[1:] not in text and "handset-77" not in text  # nothing raw leaves bkash

    nagad.sync(T0 + timedelta(hours=1))
    score, matches = nagad.lookup([("msisdn", MULE_PHONE)], T0 + timedelta(hours=2))
    assert [x.status for x in matches] == ["confirmed"]
    assert matches[0].typology == "impersonation" and score == pytest.approx(0.9)
    score, matches = nagad.lookup([("msisdn", "01911111111")], T0 + timedelta(hours=2))
    assert [x.status for x in matches] == ["suspected"] and matches[0].listing_id is None
    score, matches = nagad.lookup([("msisdn", CUSTOMER)], T0 + timedelta(hours=2))
    assert score == 0.0 and matches == []
    # The lookup is audited without the number.
    assert MULE_PHONE not in str(nagad.audit.entries) and nagad.audit.verify()


def test_listings_expire(world):
    hub, m = world
    m["bkash"].list_wallet([("msisdn", MULE)], "confirmed", 0.9, T0)
    m["bkash"].publish(T0, 0.6)
    m["nagad"].sync(T0)
    late = T0 + timedelta(days=181)
    # The bundle itself is stale by then, and the listing's own TTL has passed.
    assert m["nagad"].lookup([("msisdn", MULE)], late)[1] == []


def test_import_rejects_bad_signature_and_replay(world):
    hub, m = world
    m["bkash"].list_wallet([("msisdn", MULE)], "confirmed", 0.9, T0)
    bundle = m["bkash"].export_bundle(T0, 0.6)
    forged = Bundle.from_dict({**bundle.to_dict(), "seq": 99})
    with pytest.raises(ProtocolError, match="signature"):
        hub.submit(forged, T0)
    hub.submit(bundle, T0)
    with pytest.raises(ProtocolError, match="newer"):
        hub.submit(bundle, T0)
    # A partner checks the provider's own signature, so the hub cannot alter a listing.
    tampered = Bundle.from_dict(bundle.to_dict())
    tampered.listings[0] = type(tampered.listings[0])(
        **{**tampered.listings[0].__dict__, "confidence": 1.0}
    )
    with pytest.raises(ProtocolError, match="signature"):
        m["nagad"].ledger.import_bundle(tampered, m["bkash"].key.public, hub.epoch, T0)


def test_dispute_suspends_then_withdraws(world):
    hub, m = world
    bkash, nagad = m["bkash"], m["nagad"]
    [listing_id] = bkash.list_wallet([("msisdn", CUSTOMER)], "confirmed", 0.9, T0)
    bkash.publish(T0, 0.6)
    nagad.sync(T0)
    assert nagad.lookup([("msisdn", CUSTOMER)], T0)[0] > 0

    dispute = hub.open_dispute("nagad", listing_id, "customer proved ownership", T0)
    nagad.sync(T0 + timedelta(minutes=5))
    assert nagad.lookup([("msisdn", CUSTOMER)], T0 + timedelta(minutes=5))[0] == 0.0
    with pytest.raises(ProtocolError):
        hub.resolve_dispute(dispute.dispute_id, "nagad", "withdrawn", T0)  # not the owner

    hub.resolve_dispute(dispute.dispute_id, "bkash", "withdrawn", T0 + timedelta(hours=1))
    bkash.sync(T0 + timedelta(hours=1))  # the owner drops it from its own list
    receipt = bkash.publish(T0 + timedelta(hours=2), 0.6)
    assert receipt["listings"] == 0 and receipt["withdrawn"] == 1
    assert hub.members["bkash"].withdrawn_after_dispute == 1


def test_unanswered_dispute_is_decided_for_the_customer(world):
    hub, m = world
    [listing_id] = m["bkash"].list_wallet([("msisdn", CUSTOMER)], "confirmed", 0.9, T0)
    m["bkash"].publish(T0, 0.6)
    hub.open_dispute("nagad", listing_id, "no evidence offered", T0)
    assert hub.expire_disputes(T0 + DISPUTE_SLA - timedelta(minutes=1)) == []
    [late] = hub.expire_disputes(T0 + DISPUTE_SLA)
    assert late.status == "withdrawn" and late.resolved_by == "hub"


def test_audit_chain_detects_tampering(world):
    hub, m = world
    m["bkash"].list_wallet([("msisdn", MULE)], "confirmed", 0.9, T0)
    m["bkash"].publish(T0, 0.6)
    assert hub.audit.verify()
    hub.audit.entries[1]["detail"]["member"] = "someone-else"
    assert not hub.audit.verify()


def test_signal_counts_each_provider_once():
    from fraudlens.consortium.protocol import Match

    a = Match("bkash", "msisdn", "confirmed", 0.9, "x", "bkash:1", None)
    b = Match("bkash", "device", "confirmed", 0.5, "x", "bkash:2", None)
    c = Match("nagad", "msisdn", "suspected", 0.5, "unknown", None, None)
    assert signal([a, b]) == pytest.approx(0.9)
    assert signal([a, b, c]) == pytest.approx(1 - 0.1 * 0.5)
    assert signal([]) == 0.0


def test_demo_state_round_trips_and_serves_lookups_and_disputes(world, tmp_path):
    import json
    from types import SimpleNamespace

    import pandas as pd

    from fraudlens.consortium.demo import ConsortiumService, save_demo

    hub, m = world
    m["bkash"].list_wallet([("msisdn", MULE)], "confirmed", 0.9, T0, typology="impersonation")
    for member in m.values():
        member.publish(T0, watch_confidence=0.6)
    for member in m.values():
        member.sync(T0)
    # nagad's customer W2 is on the SIM bkash listed: a cross-provider match.
    t = (T0 + timedelta(hours=3)).timestamp()
    send = pd.DataFrame({"t": [t], "fold": ["test"], "receiver_id": ["W2"], "mule": [0.05],
                         "y_mule": [1]})  # fmt: skip
    token = m["nagad"].tokenize([("msisdn", MULE)], T0)[0]
    found = m["nagad"].ledger.lookup([("msisdn", token)], T0)
    inp = SimpleNamespace(
        wallets=pd.DataFrame({"wallet_id": ["W1", "W2", "W3"], "cell_id": [3, 3, -1]}),
        devices={"W2": [(t, "handset-9")]},
        threshold=0.15,
    )
    market = SimpleNamespace(provider={"W1": "bkash", "W2": "nagad", "W3": "rocket"},
                             msisdn={"W1": MULE, "W2": MULE, "W3": CUSTOMER})  # fmt: skip
    report = {"providers": [{"name": n, "display": n.title(), "share": 0.3} for n in m]}
    (tmp_path / "report.json").write_text(json.dumps(report))
    run = {"hub": hub, "members": m, "send": send, "matches": [found]}
    save_demo(tmp_path, inp, market, run, hub.oprf, report)

    shared = ("bundles.json", "hub_audit.json", "matches.json")
    assert not any(MULE[1:] in (tmp_path / f).read_text() for f in shared)  # tokens only

    svc = ConsortiumService(tmp_path)
    view = svc.overview()
    assert all(f["signature_ok"] for f in view["feeds"]) and view["audit"]["chain_ok"]
    [row] = svc.matches()
    assert row["status"] == "confirmed" and not row["mule_model_alone_would_alert"]
    assert row["simulation_truth"] == "mule"

    hit = svc.lookup("W2")
    assert hit["home"] == "nagad" and hit["signal"] == pytest.approx(0.9)
    assert svc.lookup("W3")["matches"] == []
    with pytest.raises(ProtocolError):
        svc.lookup("W404")

    listing = hit["matches"][0]["listing_id"]
    dispute = svc.open_dispute("nagad", listing, "customer appeal with evidence")
    assert svc.lookup("W2")["matches"] == []  # stops counting at once
    svc.resolve_dispute(dispute["dispute_id"], "upheld")
    assert svc.lookup("W2")["signal"] == pytest.approx(0.9)
    assert any(e["event"] == "dispute.resolved" for e in svc.audit(5))


def test_dispute_route_accepts_the_listing_ids_the_hub_issues() -> None:
    from pydantic import ValidationError

    from fraudlens.api.routes.consortium import DisputeIn

    hub = Hub("2026Q2", T0)
    bkash = Member("bkash", hub, crypto.SigningKey.generate())
    [issued] = bkash.list_wallet([("msisdn", MULE)], "confirmed", 0.9, T0)
    DisputeIn(listing_id=issued, raised_by="nagad", reason="customer appeal")
    with pytest.raises(ValidationError):
        DisputeIn(listing_id="bkash:x; drop", raised_by="nagad", reason="customer appeal")
