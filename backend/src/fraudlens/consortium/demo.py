"""The simulated consortium as the console sees it.

`save_demo` writes the end state of the headline simulation run to
`artifacts/consortium/`; `ConsortiumService` loads it in the API process, rebuilds
the hub and the four members from it (re-verifying every bundle signature) and
answers lookups and disputes against it. Disputes and lookups made through the API
live in memory and are lost on restart; every one also goes to the platform's audit
log.

The keys in `demo_keys.json` belong to simulated parties. A real hub keeps its OPRF
key in an HSM and each member keeps its own signing key; nobody holds all of them.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import crypto
from .hub import Hub, Member
from .protocol import Bundle, ProtocolError, iso, parse_iso

GUARANTEES = (
    "No wallet number, handset id, name or NID leaves a provider: bundles carry only "
    "128-bit tokens.",
    "Tokens come from an oblivious PRF: the hub never sees the number it is tokenising, "
    "and members cannot make tokens offline, so they cannot reverse partners' lists by "
    "brute force over the 01XXXXXXXXX numbering plan.",
    "Token requests are rate-limited per member and audited, which bounds enumeration.",
    "Suspected (unconfirmed) wallets are shared only inside a Bloom filter: testable, "
    "not listable, and a hit is deniable.",
    "Every bundle is signed by the provider that issued it: the hub relays but cannot "
    "forge or alter a listing.",
    "Every listing expires, can be disputed (it stops counting at once) and withdrawn; an "
    "unanswered dispute is decided for the customer.",
    "Every share, token request, feed and dispute is in a hash-chained audit log.",
)


def save_demo(out: Path, inp, market, run: dict, hub_key: crypto.OprfKey, report: dict) -> None:
    hub: Hub = run["hub"]
    members: dict[str, Member] = run["members"]
    keys = {
        "note": "simulated parties only; see consortium/demo.py",
        "oprf": {"epoch": hub_key.epoch, "secret": f"{hub_key.secret:x}"},
        "members": {n: f"{m.key.secret:x}" for n, m in members.items()},
    }
    (out / "demo_keys.json").write_text(json.dumps(keys))
    bundles = {n: b.to_dict() for n, b in hub.bundles.items()}
    (out / "bundles.json").write_text(json.dumps(bundles))
    pd.DataFrame(
        {
            "wallet_id": list(market.provider),
            "provider": list(market.provider.values()),
            "msisdn": [market.msisdn[w] for w in market.provider],
            "devices": [[d for _, d in inp.devices.get(w, ())] for w in market.provider],
        }
    ).to_parquet(out / "market.parquet", index=False)
    (out / "hub_audit.json").write_text(json.dumps(hub.audit.entries))

    # Test-period matches, one row per receiving wallet: its first partner match.
    send, matches = run["send"], run["matches"]
    is_t = (send["fold"] == "test").to_numpy()
    truth = send[is_t].groupby("receiver_id")["y_mule"].max()
    cell = inp.wallets.set_index("wallet_id")["cell_id"]
    seen: dict[str, dict] = {}
    for i in np.flatnonzero(is_t):
        found = matches[i]
        if not found:
            continue
        row = send.iloc[i]
        wid = row["receiver_id"]
        if wid in seen:
            seen[wid]["transfers_matched"] += 1
            continue
        seen[wid] = {
            "wallet_id": wid,
            "at": iso(datetime.fromtimestamp(float(row["t"]), tz=UTC)),
            "home": market.provider[wid],
            "partners": sorted({m.provider for m in found}),
            "kinds": sorted({m.kind for m in found}),
            "status": "confirmed" if any(m.status == "confirmed" for m in found) else "suspected",
            "confidence": round(max(m.confidence for m in found), 3),
            "typology": next((m.typology for m in found if m.status == "confirmed"), "unknown"),
            "listing_ids": sorted({m.listing_id for m in found if m.listing_id}),
            "mule_score": round(float(row["mule"]), 4),
            "mule_model_alone_would_alert": bool(row["mule"] >= inp.threshold),
            "transfers_matched": 1,
            "simulation_truth": (
                "mule"
                if truth.get(wid, 0) == 1
                else "ring wallet"
                if cell.get(wid, -1) >= 0
                else "not a mule"
            ),
        }
    (out / "matches.json").write_text(json.dumps(list(seen.values())))


class ConsortiumService:
    """The demo consortium, rebuilt from its saved state, as one process holds it."""

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.report = json.loads((directory / "report.json").read_text())
        keys = json.loads((directory / "demo_keys.json").read_text())
        bundles = {
            n: Bundle.from_dict(b)
            for n, b in json.loads((directory / "bundles.json").read_text()).items()
        }
        last = max(parse_iso(b.issued_at) for b in bundles.values())
        # The demo clock: an hour after the last bundles went out.
        self.clock = last + timedelta(hours=1)
        hub_key = crypto.OprfKey(keys["oprf"]["epoch"], int(keys["oprf"]["secret"], 16))
        self.hub = Hub(hub_key.epoch, last, key=hub_key)
        self.hub.audit.entries = json.loads((directory / "hub_audit.json").read_text())
        self.chain_ok = self.hub.audit.verify()
        displays = {p["name"]: p["display"] for p in self.report["providers"]}
        self.members: dict[str, Member] = {}
        for name, secret in keys["members"].items():
            key = crypto.SigningKey(int(secret, 16))
            self.hub.members[name] = _record(name, displays[name], key.public, last)
            self.members[name] = Member(name, self.hub, key)
        for name, bundle in bundles.items():
            if not bundle.verify(self.hub.members[name].public):
                raise ProtocolError(f"saved bundle from {name} does not verify")
            self.hub.bundles[name] = bundle
            self.members[name].seq = bundle.seq
        for m in self.members.values():
            m.sync(last)
        market = pd.read_parquet(directory / "market.parquet")
        self.provider = dict(zip(market["wallet_id"], market["provider"], strict=True))
        self.msisdn = dict(zip(market["wallet_id"], market["msisdn"], strict=True))
        self.devices = {w: list(d) for w, d in zip(market["wallet_id"], market["devices"],
                                                   strict=True)}  # fmt: skip
        self._matches = json.loads((directory / "matches.json").read_text())

    @staticmethod
    def load(artifacts_dir: Path) -> ConsortiumService | None:
        directory = artifacts_dir / "consortium"
        if not (directory / "demo_keys.json").is_file():
            return None
        return ConsortiumService(directory)

    # -------------------------------------------------------------- views

    def overview(self) -> dict:
        feeds = []
        for name, b in sorted(self.hub.bundles.items()):
            feeds.append(
                {
                    "provider": name,
                    "display": self.hub.members[name].display,
                    "seq": b.seq,
                    "issued_at": b.issued_at,
                    "valid_until": b.valid_until,
                    "confirmed_listings": len(b.listings),
                    "by_kind": dict(Counter(x.kind for x in b.listings)),
                    "by_typology": dict(Counter(x.typology for x in b.listings).most_common(6)),
                    "suspected_in_filter": b.watch["bloom"]["n"],
                    "filter_bits": b.watch["bloom"]["m"],
                    "filter_hashes": b.watch["bloom"]["k"],
                    "withdrawn": len(b.withdrawn),
                    "bytes": b.size_bytes(),
                    "signature_ok": b.verify(self.hub.members[name].public),
                    "key_fingerprint": crypto.fingerprint(self.hub.members[name].public),
                }
            )
        return {
            "clock": iso(self.clock),
            "key_epoch": self.hub.epoch,
            "oprf_key_fingerprint": crypto.fingerprint(self.hub.public),
            "members": [r.summary() for r in self.hub.members.values()],
            "feeds": feeds,
            "guarantees": list(GUARANTEES),
            "audit": {
                "entries": len(self.hub.audit.entries),
                "chain_ok": self.hub.audit.verify(),
                "head": self.hub.audit.head(),
            },  # fmt: skip
            "disputes": [d.to_dict() for d in self.hub.disputes.values()],
            "results": self.report,
        }

    def matches(self, limit: int = 200) -> list[dict]:
        return self._matches[:limit]

    def audit(self, limit: int = 100) -> list[dict]:
        return self.hub.audit.entries[-limit:][::-1]

    # ------------------------------------------------------------- actions

    def lookup(self, wallet_id: str) -> dict:
        """What the wallet's own provider learns from partners about it, right now."""
        devices = self.devices.get(wallet_id, [])
        home = self.provider.get(wallet_id)
        if home is None:
            raise ProtocolError("this wallet is not in the simulated market")
        member = self.members[home]
        ids = [("msisdn", self.msisdn[wallet_id])] + [("device", d) for d in devices]
        score, found = member.lookup(ids, self.clock, purpose="analyst")
        return {
            "wallet_id": wallet_id,
            "home": home,
            "identifiers_checked": {"msisdn": 1, "device": len(devices)},
            "signal": round(score, 4),
            "matches": [m.to_dict() for m in found],
            "audit_head": member.audit.head(),
        }

    def open_dispute(self, raised_by: str, listing_id: str, reason: str) -> dict:
        dispute = self.hub.open_dispute(raised_by, listing_id, reason, self.clock)
        for m in self.members.values():
            m.ledger.suspended.add(listing_id)
        return dispute.to_dict()

    def resolve_dispute(self, dispute_id: str, outcome: str) -> dict:
        dispute = self.hub.disputes.get(dispute_id)
        if dispute is None:
            raise ProtocolError(f"no dispute {dispute_id}")
        # In the demo the console acts for the listing member.
        self.hub.resolve_dispute(dispute_id, dispute.owner, outcome, self.clock)
        if outcome == "upheld":
            for m in self.members.values():
                m.ledger.suspended.discard(dispute.listing_id)
        return dispute.to_dict()


def _record(name: str, display: str, public: int, at: datetime):
    from .hub import MemberRecord

    return MemberRecord(name, display, public, iso(at))
