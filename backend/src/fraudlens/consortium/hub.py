"""The two roles: the consortium hub and a member provider.

The **hub** is run by the consortium (in Bangladesh, naturally under the central
bank's oversight). It holds the OPRF key, a register of members' signing keys, the
latest bundle from each member, and the dispute docket. It never sees a wallet
number: what reaches it is blinded group elements and already-tokenised bundles.

A **member** is a provider. It keeps its raw mule list to itself, makes tokens by
asking the hub (blinded, rate-limited), publishes signed bundles, imports its
partners' bundles and looks up its own receivers locally.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from . import crypto
from .protocol import (
    BUNDLE_TTL,
    CONFIRMED_TTL,
    SUSPECTED_TTL,
    WATCH_FP_RATE,
    AuditChain,
    Bundle,
    Ledger,
    Listing,
    Match,
    ProtocolError,
    group_element,
    iso,
    normalise,
    parse_iso,
    signal,
    watch_key,
)

DEFAULT_QUOTA = 250_000  # OPRF evaluations per member per day: onboarding plus daily traffic
DISPUTE_SLA = timedelta(days=5)  # an unanswered dispute is decided for the customer
OUTCOMES = ("upheld", "withdrawn")

Mapper = Callable[[Callable, Iterable], Iterable]


@dataclass
class MemberRecord:
    name: str
    display: str
    public: int
    joined_at: str
    quota_per_day: int = DEFAULT_QUOTA
    used: dict[str, int] = field(default_factory=dict)  # day -> evaluations
    disputes_against: int = 0
    withdrawn_after_dispute: int = 0

    def summary(self) -> dict:
        return {
            "name": self.name,
            "display": self.display,
            "key_fingerprint": crypto.fingerprint(self.public),
            "joined_at": self.joined_at,
            "quota_per_day": self.quota_per_day,
            "disputes_against": self.disputes_against,
            "withdrawn_after_dispute": self.withdrawn_after_dispute,
        }


@dataclass
class Dispute:
    dispute_id: str
    listing_id: str
    owner: str
    raised_by: str
    reason: str
    opened_at: str
    status: str = "open"  # open, upheld, withdrawn
    resolved_at: str | None = None
    resolved_by: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class Hub:
    def __init__(self, epoch: str, now: datetime, key: crypto.OprfKey | None = None) -> None:
        self.oprf = key or crypto.OprfKey.generate(epoch)
        self.epoch = self.oprf.epoch
        self.public = self.oprf.public
        self.members: dict[str, MemberRecord] = {}
        self.bundles: dict[str, Bundle] = {}
        self.disputes: dict[str, Dispute] = {}
        self.audit = AuditChain("hub")
        self.audit.append(now, "hub.started", key_epoch=self.epoch,
                          oprf_key=crypto.fingerprint(self.public))  # fmt: skip

    # -------------------------------------------------------------- members

    def register(self, name: str, display: str, public: int, now: datetime, **kw) -> None:
        if name in self.members:
            raise ProtocolError(f"{name} is already a member")
        self.members[name] = MemberRecord(name, display, public, iso(now), **kw)
        self.audit.append(now, "member.joined", member=name,
                          signing_key=crypto.fingerprint(public))  # fmt: skip

    def _member(self, name: str) -> MemberRecord:
        record = self.members.get(name)
        if record is None:
            raise ProtocolError(f"{name} is not a member")
        return record

    # ----------------------------------------------------------------- OPRF

    def evaluate(
        self, member: str, blinded: list[int], now: datetime, mapper: Mapper = map
    ) -> list[int]:
        """Raise blinded elements to the key, within the member's daily quota.

        The quota is what stops a member from tokenising the whole numbering plan
        to reverse partners' tokens: 10^9 numbers at this quota is 10,000 years.
        """
        record = self._member(member)
        day = now.strftime("%Y-%m-%d")
        used = record.used.get(day, 0)
        if used + len(blinded) > record.quota_per_day:
            self.audit.append(now, "oprf.refused", member=member, requested=len(blinded),
                              used_today=used)  # fmt: skip
            raise ProtocolError("daily token quota exceeded")
        record.used[day] = used + len(blinded)
        out = list(mapper(self.oprf.evaluate, blinded))
        self.audit.append(now, "oprf.evaluated", member=member, count=len(blinded))
        return out

    # -------------------------------------------------------------- bundles

    def submit(self, bundle: Bundle, now: datetime) -> dict:
        record = self._member(bundle.provider)
        if not bundle.verify(record.public):
            self.audit.append(now, "share.rejected", member=bundle.provider, why="signature")
            raise ProtocolError("bundle signature does not verify")
        if bundle.key_epoch != self.epoch:
            raise ProtocolError("bundle tokens are from another key epoch")
        held = self.bundles.get(bundle.provider)
        if held is not None and bundle.seq <= held.seq:
            raise ProtocolError("bundle is not newer than the one held")
        self.bundles[bundle.provider] = bundle
        receipt = {
            "member": bundle.provider,
            "seq": bundle.seq,
            "listings": len(bundle.listings),
            "watch": bundle.watch["bloom"]["n"],
            "withdrawn": len(bundle.withdrawn),
            "bytes": bundle.size_bytes(),
        }
        self.audit.append(now, "share.accepted", **receipt)
        return receipt

    def feed(self, member: str, now: datetime) -> dict:
        """Everything a member needs to refresh its ledger."""
        self._member(member)
        others = [b for name, b in sorted(self.bundles.items()) if name != member]
        self.audit.append(now, "feed.served", member=member,
                          bundles=[f"{b.provider}#{b.seq}" for b in others])  # fmt: skip
        return {
            "key_epoch": self.epoch,
            "bundles": others,
            "keys": {name: r.public for name, r in self.members.items()},
            "suspended": sorted(d.listing_id for d in self.disputes.values() if d.status == "open"),
            "removed": sorted(
                d.listing_id for d in self.disputes.values() if d.status == "withdrawn"
            ),
        }

    # ------------------------------------------------------------- disputes

    def open_dispute(self, raised_by: str, listing_id: str, reason: str, now: datetime) -> Dispute:
        """Contest a listing. It stops counting at once and stays out until decided.

        Anyone with standing can raise one: a partner whose customer was matched,
        or the listing member itself on behalf of its customer's appeal.
        """
        self._member(raised_by)
        owner = listing_id.split(":", 1)[0]
        bundle = self.bundles.get(owner)
        if bundle is None or not any(x.listing_id == listing_id for x in bundle.listings):
            raise ProtocolError(f"no current listing {listing_id}")
        if any(d.listing_id == listing_id and d.status == "open" for d in self.disputes.values()):
            raise ProtocolError(f"{listing_id} is already under dispute")
        if not 3 <= len(reason.strip()) <= 500:
            raise ProtocolError("a dispute needs a reason of 3 to 500 characters")
        dispute = Dispute(f"D{len(self.disputes) + 1:04d}", listing_id, owner, raised_by,
                          reason.strip(), iso(now))  # fmt: skip
        self.disputes[dispute.dispute_id] = dispute
        self._member(owner).disputes_against += 1
        self.audit.append(now, "dispute.opened", dispute=dispute.dispute_id, listing=listing_id,
                          owner=owner, raised_by=raised_by)  # fmt: skip
        return dispute

    def resolve_dispute(self, dispute_id: str, by: str, outcome: str, now: datetime) -> Dispute:
        """Only the listing member decides, because only it holds the evidence."""
        dispute = self.disputes.get(dispute_id)
        if dispute is None:
            raise ProtocolError(f"no dispute {dispute_id}")
        if dispute.status != "open":
            raise ProtocolError(f"{dispute_id} is already {dispute.status}")
        if by not in (dispute.owner, "hub"):
            raise ProtocolError("only the listing member (or the hub, on expiry) can decide")
        if outcome not in OUTCOMES:
            raise ProtocolError(f"outcome must be one of {OUTCOMES}")
        dispute.status, dispute.resolved_at, dispute.resolved_by = outcome, iso(now), by
        if outcome == "withdrawn":
            self._member(dispute.owner).withdrawn_after_dispute += 1
        self.audit.append(now, "dispute.resolved", dispute=dispute_id,
                          listing=dispute.listing_id, outcome=outcome, by=by)  # fmt: skip
        return dispute

    def expire_disputes(self, now: datetime) -> list[Dispute]:
        """Disputes the listing member did not answer in time are decided for the customer."""
        late = [
            d
            for d in self.disputes.values()
            if d.status == "open" and parse_iso(d.opened_at) + DISPUTE_SLA <= now
        ]
        return [self.resolve_dispute(d.dispute_id, "hub", "withdrawn", now) for d in late]


@dataclass
class _Local:
    """A member's own record of a listed identifier. The raw value never leaves it."""

    listing_id: str
    kind: str
    value: str
    status: str  # confirmed or suspected
    confidence: float
    typology: str
    first_seen: datetime
    listed_at: datetime
    expires_at: datetime


class Member:
    def __init__(self, name: str, hub: Hub, key: crypto.SigningKey | None = None) -> None:
        self.name = name
        self.hub = hub
        self.key = key or crypto.SigningKey.generate()
        self.ledger = Ledger(name)
        self.audit = AuditChain(name)
        self.seq = 0
        self._next_id = 0
        self._local: dict[tuple[str, str], _Local] = {}
        self._tokens: dict[tuple[str, str], str] = {}  # own identifiers, this key epoch
        self._withdrawn: list[str] = []

    # ------------------------------------------------------------ tokens

    def tokenize(
        self, pairs: list[tuple[str, str]], now: datetime, mapper: Mapper = map
    ) -> list[str]:
        """Tokens for (kind, value) pairs, through the hub's blinded OPRF.

        Tokens of the member's own customers are cached for the key epoch, so a
        payment-time lookup normally needs no hub round trip at all.
        """
        keys = [(kind, normalise(kind, value)) for kind, value in pairs]
        missing = sorted({k for k in keys if k not in self._tokens})
        if missing:
            elements = [group_element(kind, value) for kind, value in missing]
            blinded = list(mapper(crypto.blind, elements))
            evaluated = self.hub.evaluate(self.name, [b for b, _ in blinded], now, mapper)
            public = self.hub.public
            unblinded = mapper(_unblind, [(e, r, public) for e, (_, r) in
                                          zip(evaluated, blinded, strict=True)])  # fmt: skip
            for (kind, value), element_k in zip(missing, unblinded, strict=True):
                self._tokens[kind, value] = crypto.token_from_element(
                    element_k, self.hub.epoch, kind
                )
        return [self._tokens[k] for k in keys]

    # ----------------------------------------------------------- listings

    def list_wallet(
        self,
        identifiers: list[tuple[str, str]],
        status: str,
        confidence: float,
        now: datetime,
        typology: str = "unknown",
        first_seen: datetime | None = None,
    ) -> list[str]:
        """Put a wallet's identifiers on the local list as confirmed or suspected."""
        if status not in ("confirmed", "suspected"):
            raise ProtocolError("status must be confirmed or suspected")
        ttl = CONFIRMED_TTL if status == "confirmed" else SUSPECTED_TTL
        ids = []
        for kind, value in identifiers:
            key = (kind, normalise(kind, value))
            held = self._local.get(key)
            if held is not None and held.status == "confirmed" and status == "suspected":
                ids.append(held.listing_id)  # never downgrade a confirmed listing
                continue
            if held is not None and held.status == status:
                held.expires_at = now + ttl
                held.confidence = max(held.confidence, confidence)
                ids.append(held.listing_id)
                continue
            self._next_id += 1
            listing_id = f"{self.name}:{self._next_id}"
            self._local[key] = _Local(listing_id, kind, key[1], status, confidence, typology,
                                      first_seen or now, now, now + ttl)  # fmt: skip
            ids.append(listing_id)
        return ids

    def withdraw(self, listing_id: str, now: datetime, why: str) -> None:
        """Take a listing back: it is removed locally and named in the next bundle."""
        for key, item in list(self._local.items()):
            if item.listing_id == listing_id:
                del self._local[key]
                self._withdrawn.append(listing_id)
                self.audit.append(now, "listing.withdrawn", listing=listing_id, why=why)
                return
        raise ProtocolError(f"{listing_id} is not one of this member's listings")

    def export_bundle(self, now: datetime, watch_confidence: float, mapper: Mapper = map) -> Bundle:
        """A signed snapshot of the active list, tokenised. Nothing raw goes in it."""
        active = [x for x in self._local.values() if x.listed_at <= now < x.expires_at]
        tokens = self.tokenize([(x.kind, x.value) for x in active], now, mapper)
        listings, watch = [], []
        for item, token in zip(active, tokens, strict=True):
            if item.status == "confirmed":
                listings.append(
                    Listing(
                        item.listing_id,
                        token,
                        item.kind,
                        round(item.confidence, 3),
                        item.typology,
                        iso(item.first_seen),
                        iso(item.listed_at),
                        iso(item.expires_at),
                    )  # fmt: skip
                )
            else:
                watch.append(watch_key(item.kind, token))
        bloom = crypto.BloomFilter.sized(len(watch), WATCH_FP_RATE)
        for key in watch:
            bloom.add(key)
        self.seq += 1
        bundle = Bundle(
            provider=self.name,
            seq=self.seq,
            key_epoch=self.hub.epoch,
            issued_at=iso(now),
            valid_until=iso(now + BUNDLE_TTL),
            listings=sorted(listings, key=lambda x: x.listing_id),
            watch={"confidence": round(watch_confidence, 3), "bloom": bloom.to_dict()},
            withdrawn=list(self._withdrawn),
        ).sign(self.key)
        self.audit.append(now, "share.exported", seq=bundle.seq, listings=len(listings),
                          watch=len(watch), withdrawn=len(self._withdrawn))  # fmt: skip
        return bundle

    def publish(self, now: datetime, watch_confidence: float, mapper: Mapper = map) -> dict:
        return self.hub.submit(self.export_bundle(now, watch_confidence, mapper), now)

    # ----------------------------------------------------------- partners

    def sync(self, now: datetime) -> list[dict]:
        """Import every partner bundle the hub holds that is newer than ours."""
        feed = self.hub.feed(self.name, now)
        imported = []
        for bundle in feed["bundles"]:
            held = self.ledger.bundles.get(bundle.provider)
            if held is not None and held.seq >= bundle.seq:
                continue
            receipt = self.ledger.import_bundle(
                bundle, feed["keys"][bundle.provider], feed["key_epoch"], now
            )
            self.audit.append(now, "share.imported", **receipt)
            imported.append(receipt)
        self.ledger.suspended = set(feed["suspended"]) | set(feed["removed"])
        # A dispute decided against one of our listings: take it off our own list too.
        own = {x.listing_id for x in self._local.values()}
        for listing_id in feed["removed"]:
            if listing_id in own:
                self.withdraw(listing_id, now, "dispute decided for the customer")
        return imported

    def lookup(
        self, identifiers: list[tuple[str, str]], at: datetime, purpose: str = "payment"
    ) -> tuple[float, list[Match]]:
        """Receiver-side check: what partners know about these identifiers.

        Audited with the count of identifiers and of matches, never the values.
        """
        tokens = self.tokenize(identifiers, at)
        matches = self.ledger.lookup(
            [(kind, t) for (kind, _), t in zip(identifiers, tokens, strict=True)], at
        )
        self.audit.append(at, "lookup", purpose=purpose, identifiers=len(identifiers),
                          matches=len(matches),
                          listings=[m.listing_id for m in matches if m.listing_id])  # fmt: skip
        return signal(matches), matches


def _unblind(args: tuple[int, int, int]) -> int:
    return crypto.unblind(*args)
