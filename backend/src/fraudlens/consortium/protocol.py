"""The consortium protocol: what a provider shares, how a partner checks it, how it is undone.

A provider (a *member*) keeps its mule list locally, with real wallet numbers and
handset fingerprints. What leaves it is a **bundle**, signed by the member:

- `listings`: confirmed mules, one entry per identifier token, each with a
  confidence, a typology, when the provider first saw it and when it expires;
- `watch`: suspected (not yet confirmed) wallets as a Bloom filter of tokens,
  which a partner can test a token against but cannot enumerate;
- `withdrawn`: listings taken back since the last bundle (disputes upheld for the
  customer, or the member's own correction).

Tokens are made through the hub's oblivious PRF (see `crypto`), so a bundle holds no
wallet number, no handset id and nothing a partner can reverse. A partner imports
bundles into its **ledger** and looks up its own receivers at payment time: a local
dictionary and Bloom probe, no network call on the payment path.

Every share, import, lookup and dispute step is appended to a hash-chained
**audit log**; changing or removing any past line breaks the chain.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from . import crypto

FORMAT = "fraudlens-consortium-bundle/1"
KINDS = ("msisdn", "device")
TYPOLOGIES_UNKNOWN = "unknown"
CONFIRMED_TTL = timedelta(days=180)
SUSPECTED_TTL = timedelta(days=30)
BUNDLE_TTL = timedelta(days=2)  # a partner stops trusting a feed it has not refreshed
WATCH_FP_RATE = 1e-6  # per partner, per lookup: lookups run at every transfer
MAX_LISTINGS = 200_000

_MSISDN = re.compile(r"^(?:\+?88)?(01[3-9]\d{8})$")


class ProtocolError(ValueError):
    """A bundle, lookup or dispute that the protocol refuses."""


def iso(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def normalise(kind: str, value: str) -> str:
    """The one spelling of an identifier that every member hashes.

    A Bangladeshi wallet number is the owner's mobile number: 01XXXXXXXXX, with or
    without +88. It is normalised to 8801XXXXXXXXX so that every provider derives
    the same token for the same SIM.
    """
    if kind == "msisdn":
        match = _MSISDN.match(re.sub(r"[\s-]", "", value))
        if not match:
            raise ProtocolError("not a Bangladeshi mobile number")
        return "88" + match.group(1)
    if kind == "device":
        value = value.strip().lower()
        if not 4 <= len(value) <= 128:
            raise ProtocolError("device fingerprint must be 4 to 128 characters")
        return value
    raise ProtocolError(f"unknown identifier kind {kind!r}")


def group_element(kind: str, value: str) -> int:
    return crypto.hash_to_group(f"{kind}:{normalise(kind, value)}".encode())


def canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


# ------------------------------------------------------------------ listings


@dataclass(frozen=True)
class Listing:
    listing_id: str  # "<member>:<n>", stable for the life of the listing
    token: str
    kind: str
    confidence: float
    typology: str
    first_seen: str
    listed_at: str
    expires_at: str

    def active(self, at: datetime) -> bool:
        return parse_iso(self.listed_at) <= at < parse_iso(self.expires_at)


@dataclass
class Bundle:
    provider: str
    seq: int
    key_epoch: str
    issued_at: str
    valid_until: str
    listings: list[Listing]
    watch: dict  # {"confidence": float, "bloom": BloomFilter.to_dict()}
    withdrawn: list[str]
    signature: str = ""
    format: str = FORMAT

    def body(self) -> dict:
        d = asdict(self)
        d.pop("signature")
        return d

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> Bundle:
        if d.get("format") != FORMAT:
            raise ProtocolError("unknown bundle format")
        if len(d["listings"]) > MAX_LISTINGS:
            raise ProtocolError("bundle is larger than the protocol allows")
        listings = [Listing(**item) for item in d["listings"]]
        for item in listings:
            if item.kind not in KINDS or not 0.0 <= item.confidence <= 1.0:
                raise ProtocolError(f"malformed listing {item.listing_id}")
            if not re.fullmatch(r"[0-9a-f]{32}", item.token):
                raise ProtocolError(f"listing {item.listing_id} does not carry a token")
        return Bundle(
            provider=d["provider"],
            seq=int(d["seq"]),
            key_epoch=d["key_epoch"],
            issued_at=d["issued_at"],
            valid_until=d["valid_until"],
            listings=listings,
            watch=d["watch"],
            withdrawn=list(d["withdrawn"]),
            signature=d["signature"],
        )

    def sign(self, key: crypto.SigningKey) -> Bundle:
        self.signature = key.sign(canonical(self.body()))
        return self

    def verify(self, public: int) -> bool:
        return crypto.verify(public, canonical(self.body()), self.signature)

    def size_bytes(self) -> int:
        return len(canonical(self.to_dict()))


# --------------------------------------------------------------------- audit


@dataclass
class AuditChain:
    """Append-only log where each line commits to the one before it."""

    owner: str
    entries: list[dict] = field(default_factory=list)

    GENESIS = "0" * 64

    def append(self, at: datetime, event: str, **detail) -> dict:
        prev = self.entries[-1]["hash"] if self.entries else self.GENESIS
        line = {
            "n": len(self.entries) + 1,
            "at": iso(at),
            "by": self.owner,
            "event": event,
            "detail": detail,
            "prev": prev,
        }
        line["hash"] = hashlib.sha256(canonical(line)).hexdigest()
        self.entries.append(line)
        return line

    def verify(self) -> bool:
        prev = self.GENESIS
        for i, line in enumerate(self.entries, start=1):
            body = {k: v for k, v in line.items() if k != "hash"}
            if line["n"] != i or line["prev"] != prev:
                return False
            if hashlib.sha256(canonical(body)).hexdigest() != line["hash"]:
                return False
            prev = line["hash"]
        return True

    def head(self) -> str:
        return self.entries[-1]["hash"] if self.entries else self.GENESIS


# -------------------------------------------------------------------- ledger


@dataclass(frozen=True)
class Match:
    provider: str
    kind: str
    status: str  # "confirmed" or "suspected"
    confidence: float
    typology: str
    listing_id: str | None  # None for a watch-list (Bloom) hit
    first_seen: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def signal(matches: list[Match]) -> float:
    """One receiver-side number from any number of partner matches.

    Each listing is treated as independent evidence of its stated confidence; a
    provider listing the same wallet under two identifiers counts once, at its
    strongest. 0 means no partner knows anything about this wallet.
    """
    best: dict[str, float] = {}
    for m in matches:
        best[m.provider] = max(best.get(m.provider, 0.0), m.confidence)
    miss = 1.0
    for c in best.values():
        miss *= 1.0 - c
    return 1.0 - miss


class Ledger:
    """One member's view of every partner's latest bundle."""

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.bundles: dict[str, Bundle] = {}
        self._by_token: dict[str, list[Listing]] = {}
        self._watch: dict[str, tuple[crypto.BloomFilter, float]] = {}
        self.suspended: set[str] = set()  # listings under dispute: not used while it runs

    def import_bundle(self, bundle: Bundle, public: int, key_epoch: str, now: datetime) -> dict:
        """Check a partner bundle and make it the partner's current one."""
        if bundle.provider == self.owner:
            raise ProtocolError("a member does not import its own bundle")
        if not bundle.verify(public):
            raise ProtocolError(f"bad signature on the bundle from {bundle.provider}")
        if bundle.key_epoch != key_epoch:
            raise ProtocolError("bundle tokens are from another key epoch")
        current = self.bundles.get(bundle.provider)
        if current is not None and bundle.seq <= current.seq:
            raise ProtocolError("bundle is not newer than the one held (replay)")
        if parse_iso(bundle.valid_until) <= now:
            raise ProtocolError("bundle has expired")
        self.bundles[bundle.provider] = bundle
        self._reindex()
        return {
            "provider": bundle.provider,
            "seq": bundle.seq,
            "listings": len(bundle.listings),
            "watch": bundle.watch["bloom"]["n"],
            "withdrawn": len(bundle.withdrawn),
        }

    def _reindex(self) -> None:
        self._by_token = {}
        self._watch = {}
        for provider, bundle in self.bundles.items():
            gone = set(bundle.withdrawn)
            for item in bundle.listings:
                if item.listing_id not in gone:
                    self._by_token.setdefault(item.token, []).append(item)
            bloom = crypto.BloomFilter.from_dict(bundle.watch["bloom"])
            self._watch[provider] = (bloom, float(bundle.watch["confidence"]))

    def lookup(self, tokens: list[tuple[str, str]], at: datetime) -> list[Match]:
        """Partner knowledge about these (kind, token) pairs at time `at`."""
        out: list[Match] = []
        for kind, token in tokens:
            for item in self._by_token.get(token, ()):
                provider = item.listing_id.split(":", 1)[0]
                if (
                    item.kind == kind
                    and item.active(at)
                    and item.listing_id not in self.suspended
                    and parse_iso(self.bundles[provider].valid_until) > at
                ):
                    out.append(
                        Match(
                            provider,
                            kind,
                            "confirmed",
                            item.confidence,
                            item.typology,
                            item.listing_id,
                            item.first_seen,
                        )  # fmt: skip
                    )
            for provider, (bloom, confidence) in self._watch.items():
                if (
                    parse_iso(self.bundles[provider].valid_until) > at
                    and f"{kind}:{token}" in bloom
                ):
                    out.append(
                        Match(
                            provider, kind, "suspected", confidence, TYPOLOGIES_UNKNOWN, None, None
                        )  # fmt: skip
                    )
        return out

    def listing(self, listing_id: str) -> Listing | None:
        provider = listing_id.split(":", 1)[0]
        bundle = self.bundles.get(provider)
        if bundle is None:
            return None
        return next((x for x in bundle.listings if x.listing_id == listing_id), None)


def watch_key(kind: str, token: str) -> str:
    """What goes into a watch filter: the token bound to its kind."""
    return f"{kind}:{token}"
